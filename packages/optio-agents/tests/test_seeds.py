"""Tests for the generic optio-agents seed engine."""

import datetime as _dt
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


# --- pool / lease layer ----------------------------------------------------


async def _pooled_seed(mongo_db, pool_key):
    """Insert a seed and assign it to `pool_key`; return its seed_id."""
    sid = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=ObjectId(), manifest_version=1,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, poolKey=pool_key,
    )
    return sid


def _coll(mongo_db):
    return mongo_db[f"t{SUFFIX}"]


async def test_acquire_leases_a_free_seed(mongo_db):
    sid = await _pooled_seed(mongo_db, "p1")
    got = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p1", holder="h1")
    assert got == sid
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid)
    assert doc["lease"]["holder"] == "h1"
    # Mongo's $$NOW (and the round-tripped expiresAt) is UTC; compare to a UTC
    # now made naive to match the document_class tz-awareness of the client.
    assert doc["lease"]["expiresAt"] > _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    assert "lastLeasedAt" in doc


async def test_acquire_is_exclusive_and_returns_none_when_exhausted(mongo_db):
    a = await _pooled_seed(mongo_db, "p2")
    b = await _pooled_seed(mongo_db, "p2")
    first = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p2", holder="h1")
    second = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p2", holder="h2")
    assert {first, second} == {a, b}
    third = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p2", holder="h3")
    assert third is None


async def test_acquire_skips_dead_seeds(mongo_db):
    sid = await _pooled_seed(mongo_db, "p3")
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="dead")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p3", holder="h1") is None


async def test_acquire_ignores_other_pools(mongo_db):
    await _pooled_seed(mongo_db, "pA")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="pB", holder="h1") is None


async def test_acquire_reclaims_expired_lease(mongo_db):
    sid = await _pooled_seed(mongo_db, "p4")
    past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=120)
    await _coll(mongo_db).update_one(
        {"_id": ObjectId(sid)}, {"$set": {"lease": {"holder": "stale", "expiresAt": past}}},
    )
    got = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p4", holder="h2")
    assert got == sid
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid)
    assert doc["lease"]["holder"] == "h2"


async def test_acquire_does_not_reclaim_live_lease(mongo_db):
    await _pooled_seed(mongo_db, "p5")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p5", holder="h1")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p5", holder="h2") is None


async def test_acquire_round_robin(mongo_db):
    a = await _pooled_seed(mongo_db, "p6")
    b = await _pooled_seed(mongo_db, "p6")
    first = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p6", holder="h1")
    await seeds.release(mongo_db, prefix="t", suffix=SUFFIX, seed_id=first, holder="h1")
    # the never-leased seed (no lastLeasedAt) sorts first -> the OTHER seed wins
    second = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p6", holder="h2")
    assert second != first
    assert {first, second} == {a, b}


async def test_renew_extends_for_holder_and_cas_rejects_others(mongo_db):
    sid = await _pooled_seed(mongo_db, "p7")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p7", holder="h1")
    before = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid))["lease"]["expiresAt"]
    ok = await seeds.renew_lease(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="h1")
    assert ok is True
    after = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid))["lease"]["expiresAt"]
    assert after >= before
    # a non-holder cannot renew
    assert await seeds.renew_lease(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="other") is False


async def test_renew_false_after_steal(mongo_db):
    sid = await _pooled_seed(mongo_db, "p8")
    # h1 holds, lease expires, h2 steals it
    past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=120)
    await _coll(mongo_db).update_one(
        {"_id": ObjectId(sid)}, {"$set": {"lease": {"holder": "h1", "expiresAt": past}}},
    )
    stolen = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p8", holder="h2")
    assert stolen == sid
    # the original holder's renewal now fails
    assert await seeds.renew_lease(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="h1") is False


async def test_release_frees_and_is_holder_guarded(mongo_db):
    sid = await _pooled_seed(mongo_db, "p9")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p9", holder="h1")
    # wrong holder cannot release
    await seeds.release(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="other")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p9", holder="h2") is None
    # right holder releases
    await seeds.release(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="h1")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p9", holder="h2") == sid


async def test_mark_status_alive_again_revives(mongo_db):
    sid = await _pooled_seed(mongo_db, "p10")
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="dead")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p10", holder="h1") is None
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="alive")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p10", holder="h1") == sid


async def test_assign_to_pool_makes_seed_eligible(mongo_db):
    sid = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=ObjectId(), manifest_version=1,
    )
    # unassigned -> not acquirable
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p11", holder="h1") is None
    await seeds.assign_to_pool(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, poolKey="p11")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p11", holder="h1") == sid


async def test_list_pool_reports_status_leased_and_timestamps(mongo_db):
    a = await _pooled_seed(mongo_db, "p12")
    b = await _pooled_seed(mongo_db, "p12")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p12", holder="h1")  # leases one
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=b, status="dead")
    listed = {d["seedId"]: d for d in await seeds.list_pool(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p12")}
    assert set(listed) == {a, b}
    assert listed[a]["leased"] is True
    assert listed[b]["status"] == "dead"
    assert listed[b]["leased"] is False
    assert "lastVerifiedAt" in listed[a]


async def test_list_pool_returns_metadata(mongo_db):
    sid = await _pooled_seed(mongo_db, "pm")
    await seeds.declare_metadata(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, metadata={"account": {"uuid": "u9"}})
    listed = await seeds.list_pool(mongo_db, prefix="t", suffix=SUFFIX, poolKey="pm")
    assert listed[0]["metadata"]["account"]["uuid"] == "u9"


async def test_reap_expired_leases(mongo_db):
    live = await _pooled_seed(mongo_db, "p13")
    expired = await _pooled_seed(mongo_db, "p13")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p13", holder="h1")  # leases `live`
    past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=120)
    await _coll(mongo_db).update_one(
        {"_id": ObjectId(expired)}, {"$set": {"lease": {"holder": "old", "expiresAt": past}}},
    )
    n = await seeds.reap_expired_leases(mongo_db, prefix="t", suffix=SUFFIX)
    assert n == 1
    assert (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=expired))["lease"] is None
    # the live lease is untouched
    assert (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=live))["lease"] is not None


async def test_declare_metadata_merges_keys(mongo_db):
    sid = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=ObjectId(), manifest_version=1,
    )
    await seeds.declare_metadata(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, metadata={"usage": {"a": 1}},
    )
    await seeds.declare_metadata(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, metadata={"account": {"uuid": "u1"}},
    )
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid)
    assert doc["metadata"]["usage"] == {"a": 1}
    assert doc["metadata"]["account"] == {"uuid": "u1"}


async def test_overwrite_seed_member_replaces_in_place(mongo_db, tmp_workdir):
    import os

    src = LocalHost(taskdir=os.path.join(tmp_workdir, "ov"))
    await src.setup_workdir()
    _plant_env(src.workdir)
    ctx = await _local_ctx(mongo_db, src.taskdir)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=FAKE_MANIFEST, suffix=SUFFIX, encrypt=None,
    )
    old_blob = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id))["blobId"]

    await seeds.overwrite_seed_member(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id,
        member_path=".claude/.credentials.json", content=b'{"token": "NEW"}',
        encrypt=None, decrypt=None,
    )

    # blob swapped; member replaced; other members intact; reflected by merge
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=seed_id)
    assert doc["blobId"] != old_blob
    dst = LocalHost(taskdir=os.path.join(tmp_workdir, "ovdst"))
    await dst.setup_workdir()
    await seeds.merge_seed(
        ctx, dst, seed_id=seed_id, manifest=FAKE_MANIFEST, suffix=SUFFIX, decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".claude", ".credentials.json")) as fh:
        assert fh.read() == '{"token": "NEW"}'
    assert os.path.exists(os.path.join(dst.workdir, "home", ".claude.json"))
    import gridfs
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    with pytest.raises(gridfs.errors.NoFile):
        await AsyncIOMotorGridFSBucket(mongo_db).open_download_stream(old_blob)
