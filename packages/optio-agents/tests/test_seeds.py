"""Tests for the generic optio-agents seed engine."""

import io
import tarfile

import pytest
from bson import ObjectId

from optio_agents import seeds
from optio_host.host import LocalHost


SUFFIX = "_fake_seeds"


async def test_insert_load_delete_list_roundtrip(mongo_db):
    blob_id = ObjectId()
    seed_id = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=blob_id, manifest_version=1,
    )
    assert isinstance(seed_id, str)
    assert ObjectId(seed_id)  # hex parses

    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc is not None
    assert doc["blobId"] == blob_id
    assert doc["manifestVersion"] == 1
    assert "createdAt" in doc

    listed = await seeds.list_seeds(mongo_db, prefix="t", suffix=SUFFIX)
    assert listed == [{"seedId": seed_id, "createdAt": doc["createdAt"]}]

    removed_blob = await seeds.delete_seed(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id,
    )
    assert removed_blob == blob_id
    assert await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id) is None


async def test_load_and_delete_tolerate_bad_id(mongo_db):
    assert await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id="not-hex") is None
    assert await seeds.delete_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id="not-hex") is None
    missing = str(ObjectId())
    assert await seeds.delete_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=missing) is None


async def test_purge_removes_doc_and_blob(mongo_db):
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    blob_id = await bucket.upload_from_stream("seed", b"x")
    seed_id = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=blob_id, manifest_version=1,
    )

    await seeds.purge_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)

    assert await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id) is None

    import gridfs

    with pytest.raises(gridfs.errors.NoFile):
        await bucket.open_download_stream(blob_id)


async def test_purge_unknown_seed_raises(mongo_db):
    with pytest.raises(KeyError):
        await seeds.purge_seed(
            mongo_db, prefix="t", suffix=SUFFIX, seed_id=str(ObjectId()),
        )


async def _local_ctx(mongo_db, taskdir):
    """A minimal real ProcessContext for GridFS blob I/O."""
    import asyncio

    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["t_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid,
        process_id="p",
        root_oid=oid,
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="t",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


def _plant_env(host_workdir: str) -> None:
    """Plant INCLUDE + EXCLUDE files under <workdir>/home."""
    import os

    home = os.path.join(host_workdir, "home")
    claude = os.path.join(home, ".claude")
    os.makedirs(os.path.join(claude, "plugins", "marketplace"), exist_ok=True)
    os.makedirs(os.path.join(claude, "projects", "old-cwd"), exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "x"}')
    with open(os.path.join(claude, "plugins", "marketplace", "p.json"), "w") as fh:
        fh.write("{}")
    with open(os.path.join(claude, "projects", "old-cwd", "transcript.jsonl"), "w") as fh:
        fh.write('{"msg": "secret"}')
    with open(os.path.join(home, ".claude.json"), "w") as fh:
        fh.write('{"userID": "u1"}')


FAKE_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".claude/.credentials.json", ".claude/plugins", ".claude.json"],
    version=7,
)


async def test_capture_then_merge_roundtrip(mongo_db, tmp_workdir):
    import os

    # capture host
    src = LocalHost(taskdir=os.path.join(tmp_workdir, "src"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)

    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )

    # seed doc records the manifest version
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["manifestVersion"] == 7

    # the stored tar contains ONLY include paths — never the transcript
    payload = bytearray()
    async with ctx.load_blob(doc["blobId"]) as reader:
        while chunk := await reader.read(1 << 20):
            payload.extend(chunk)
    with tarfile.open(fileobj=io.BytesIO(bytes(payload)), mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith(".credentials.json") for n in names)
    assert any("plugins" in n for n in names)
    assert any(n == ".claude.json" for n in names)
    assert not any("projects" in n for n in names), names
    assert not any("transcript.jsonl" in n for n in names), names

    # merge into a fresh host
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "dst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude", ".credentials.json"))
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude.json"))
    assert not os.path.exists(os.path.join(dst.workdir, "home", ".claude", "projects"))


async def test_capture_encrypt_decrypt_roundtrip(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "esrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)

    def enc(b: bytes) -> bytes:
        return bytes((x + 1) & 0xFF for x in b)

    def dec(b: bytes) -> bytes:
        return bytes((x - 1) & 0xFF for x in b)

    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=enc,
    )
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "edst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=dec,
    )
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude.json"))


async def test_merge_unknown_seed_raises(mongo_db, tmp_workdir):
    import os

    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "u"))
    await dst.setup_workdir()
    ctx = await _local_ctx(mongo_db, dst.taskdir)
    with pytest.raises(KeyError):
        await seeds.merge_seed(
            ctx, dst, seed_id=str(ObjectId()), manifest=FAKE_MANIFEST,
            suffix=SUFFIX, decrypt=None,
        )


async def test_consume_transform_runs_after_extract(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "tsrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)

    async def _stamp(host) -> None:
        await host.run_command(f"touch {host.workdir}/home/.transform-ran")

    manifest = seeds.SeedManifest(
        home_subdir="home", include=[".claude.json"], version=1, consume_transform=_stamp,
    )
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=manifest, suffix=SUFFIX, encrypt=None,
    )
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "tdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=manifest, suffix=SUFFIX, decrypt=None,
    )
    assert os.path.exists(os.path.join(dst.workdir, "home", ".transform-ran"))


async def test_merge_narrow_overlay_extracts_only_listed_members(mongo_db, tmp_workdir):
    import os

    # capture a FULL seed (creds + plugins + .claude.json)
    src = LocalHost(taskdir=os.path.join(tmp_workdir, "nsrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )

    # destination already has an OLD creds file + an unrelated file that a
    # narrow overlay must NOT delete
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "ndst"))
    await dst.setup_workdir()
    claude = os.path.join(dst.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "OLD"}')
    with open(os.path.join(claude, "keep.txt"), "w") as fh:
        fh.write("keep me")

    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=narrow, suffix=SUFFIX, decrypt=None,
    )

    # creds overwritten from the seed; plugins NOT injected; unrelated file kept
    with open(os.path.join(claude, ".credentials.json")) as fh:
        assert fh.read() == '{"token": "x"}'  # the seed value from _plant_env
    assert not os.path.exists(os.path.join(claude, "plugins"))
    assert os.path.exists(os.path.join(claude, "keep.txt"))


async def test_merge_tolerates_include_member_absent_from_archive(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "asrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "adst"))
    await dst.setup_workdir()
    # ask for a member the archive does not contain -> no error, no extraction
    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/settings.json"], version=7,
    )
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=narrow, suffix=SUFFIX, decrypt=None,
    )
    assert not os.path.exists(os.path.join(dst.workdir, "home", ".claude", "settings.json"))


async def test_update_seed_blob_swaps_blobid_and_stamps_updatedat(mongo_db):
    old = ObjectId()
    seed_id = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=old, manifest_version=1,
    )
    new = ObjectId()
    await seeds.update_seed_blob(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id, new_blob_id=new,
    )
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] == new
    assert "updatedAt" in doc


def _mk_targz(members: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_merge_tar_members_overrides_and_preserves():
    base = _mk_targz({"a.txt": b"OLD-A", "b.txt": b"B"})
    overlay = _mk_targz({"a.txt": b"NEW-A"})
    merged = seeds._merge_tar_members(base, overlay)
    with tarfile.open(fileobj=io.BytesIO(merged), mode="r:gz") as tar:
        got = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    assert got == {"a.txt": b"NEW-A", "b.txt": b"B"}


async def test_refresh_seed_replaces_credentials_in_place(mongo_db, tmp_workdir):
    import os

    # capture a full seed with creds={"token":"x"}
    src = LocalHost(taskdir=os.path.join(tmp_workdir, "rsrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    old_blob = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id))["blobId"]

    # a NEW live home whose creds have rotated
    live = LocalHost(taskdir=os.path.join(tmp_workdir, "rlive"))
    await live.setup_workdir()
    claude = os.path.join(live.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "ROTATED"}')

    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    await seeds.refresh_seed(
        ctx, live, seed_id=seed_id, manifest=narrow, suffix=SUFFIX,
        encrypt=None, decrypt=None,
    )

    # seed id unchanged; blob swapped; updatedAt stamped
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] != old_blob
    assert "updatedAt" in doc

    # merging the refreshed seed yields the rotated creds, and the rest of the
    # full environment is still intact
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "rdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert fh.read() == '{"token": "ROTATED"}'
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude", "plugins"))

    # old blob removed
    import gridfs
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    with pytest.raises(gridfs.errors.NoFile):
        await AsyncIOMotorGridFSBucket(mongo_db).open_download_stream(old_blob)


async def test_refresh_seed_unknown_id_raises(mongo_db, tmp_workdir):
    import os

    live = LocalHost(taskdir=os.path.join(tmp_workdir, "rk"))
    await live.setup_workdir()
    ctx = await _local_ctx(mongo_db, live.taskdir)
    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    with pytest.raises(KeyError):
        await seeds.refresh_seed(
            ctx, live, seed_id=str(ObjectId()), manifest=narrow, suffix=SUFFIX,
            encrypt=None, decrypt=None,
        )


async def test_refresh_seed_crash_before_doc_update_keeps_old_blob(
    mongo_db, tmp_workdir, monkeypatch,
):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "csrc"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    old_blob = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id))["blobId"]

    live = LocalHost(taskdir=os.path.join(tmp_workdir, "clive"))
    await live.setup_workdir()
    claude = os.path.join(live.workdir, "home", ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, ".credentials.json"), "w") as fh:
        fh.write('{"token": "ROTATED"}')

    async def boom(*a, **k):
        raise RuntimeError("simulated crash before doc update")

    monkeypatch.setattr(seeds, "update_seed_blob", boom)
    narrow = seeds.SeedManifest(
        home_subdir="home", include=[".claude/.credentials.json"], version=7,
    )
    with pytest.raises(RuntimeError):
        await seeds.refresh_seed(
            ctx, live, seed_id=seed_id, manifest=narrow, suffix=SUFFIX,
            encrypt=None, decrypt=None,
        )

    # doc still points at the original blob and still decodes to the old creds
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] == old_blob
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "cdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert fh.read() == '{"token": "x"}'
