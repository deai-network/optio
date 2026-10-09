"""Full-cycle resume test for optio-claudecode against fake_claude.py."""

import asyncio
import dataclasses
import json
import os
import pathlib

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.session import run_claudecode_session
from optio_claudecode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_test_resume_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_ctx(mongo_db, process_id: str, *, resume: bool):
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        resume=resume,
    )


def _cfg(shim_install_dir, claude_cache_dir, scenario: str) -> ClaudeCodeTaskConfig:
    return ClaudeCodeTaskConfig(
        consumer_instructions=f"(scenario: {scenario})",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=True,
        # A real configured (logged-in) session has credentials on disk;
        # plant_home_files writes this to home/.claude/.credentials.json.
        # Required by the credentials-present snapshot guard.
        credentials_json={"token": "test"},
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )


async def _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, scenario, resume, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
    ctx = await _make_ctx(mongo_db, pid, resume=resume)
    await run_claudecode_session(ctx, _cfg(shim_install_dir, claude_cache_dir, scenario))


async def test_terminal_flow_captures_snapshot(mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch):
    pid = "cc_terminal_1"
    await _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, "happy", False, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None
    assert snap["endState"] == "done"
    assert "sessionBlobId" in snap and "workdirBlobId" in snap

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True


async def test_session_blob_excludes_home_claude_from_workdir_blob(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """The plaintext workdir blob must NOT contain home/.claude (defensive
    rm -rf at capture). The session blob is where home/.claude lives."""
    import io, tarfile
    pid = "cc_split_1"
    await _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, "happy", False, monkeypatch)
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)

    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    wstream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await wstream.read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        names = tar.getnames()
    assert not any("home/.claude" in n for n in names), names

    sstream = await bucket.open_download_stream(snap["sessionBlobId"])
    session_bytes = await sstream.read()
    with tarfile.open(fileobj=io.BytesIO(session_bytes), mode="r:gz") as tar:
        snames = tar.getnames()
    assert any("home/.claude" in n for n in snames), snames


async def test_workdir_blob_excludes_heavy_regenerable_home_dirs(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """The claude binary install, mozilla cache, and mozilla profile under
    the isolated HOME are regenerable junk (the binary is reinstalled on
    resume) and must NOT bloat the workdir snapshot — they are rm -rf'd
    before the workdir tar. Without this, a real session's 230MB+ binary
    makes the in-memory gzip blow the cancellation grace period."""
    import io, tarfile
    pid = "cc_heavy_excl"
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")

    async def _plant_junk(hook_ctx):
        wd = hook_ctx._host.workdir
        await hook_ctx.run_on_host(
            f"mkdir -p {wd}/home/.local/share/claude/versions/v1 "
            f"{wd}/home/.cache/mozilla/firefox {wd}/home/.mozilla/firefox && "
            f"echo BIN > {wd}/home/.local/share/claude/versions/v1/claude && "
            f"echo C > {wd}/home/.cache/mozilla/firefox/cache && "
            f"echo M > {wd}/home/.mozilla/firefox/prof"
        )

    ctx = await _make_ctx(mongo_db, pid, resume=False)
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="(heavy)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=True,
        credentials_json={"token": "test"},
        before_execute=_plant_junk,
    )
    await run_claudecode_session(ctx, cfg)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    wstream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await wstream.read()
    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        names = tar.getnames()
    for needle in ("home/.cache/mozilla", "home/.mozilla"):
        assert not any(needle in n for n in names), (needle, names)


async def test_resume_creates_second_snapshot_and_passes_continue(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    import io, tarfile
    pid = "cc_resume_1"
    await _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, "idempotent_done", False, monkeypatch)
    await _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, "idempotent_done", True, monkeypatch)

    count = await mongo_db[f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].count_documents(
        {"processId": pid}
    )
    assert count == 2

    # The latest session blob carries home/.claude/fake_claude_argv.json
    # written by fake_claude. The resumed launch line must contain --continue.
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    sstream = await bucket.open_download_stream(snap["sessionBlobId"])
    session_bytes = await sstream.read()
    with tarfile.open(fileobj=io.BytesIO(session_bytes), mode="r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("fake_claude_argv.json"))
        argv_lines = tar.extractfile(member).read().decode("utf-8").splitlines()
    launches = [json.loads(line) for line in argv_lines if line]
    # First launch: no --continue. Second (resume): --continue present.
    assert "--continue" not in launches[0]
    assert any("--continue" in launch for launch in launches[1:])
    # Nothing configured: the fresh launch leaves the model to claude; the
    # continued one passes the default alias, so it runs the newest default
    # model rather than the full id its transcript ended on.
    assert "--model" not in launches[0]
    resumed = next(launch for launch in launches[1:] if "--continue" in launch)
    assert resumed[resumed.index("--model") + 1] == "default"


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    pid = "cc_resume_no_prior"
    await _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, "happy", True, monkeypatch)
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None  # fresh-start cycle still captures a terminal snapshot


async def test_resume_with_no_transcript_launches_without_continue(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """D3: a restored snapshot whose home/.claude/projects has no *.jsonl
    must launch WITHOUT --continue (passing it makes claude exit at
    startup). The `happy` scenario never writes a transcript, so its
    snapshot has none — the resumed launch must omit --continue."""
    import io, tarfile

    pid = "cc_d3_no_transcript"
    # First cycle: fresh `happy` run captures a snapshot with NO transcript.
    await _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, "happy", False, monkeypatch)
    # Second cycle: resume. D3 must suppress --continue.
    await _run_cycle(mongo_db, pid, shim_install_dir, claude_cache_dir, "happy", True, monkeypatch)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    sstream = await bucket.open_download_stream(snap["sessionBlobId"])
    session_bytes = await sstream.read()
    with tarfile.open(fileobj=io.BytesIO(session_bytes), mode="r:gz") as tar:
        member = next(
            m for m in tar.getmembers() if m.name.endswith("fake_claude_argv.json")
        )
        argv_lines = tar.extractfile(member).read().decode("utf-8").splitlines()
    launches = [json.loads(line) for line in argv_lines if line]
    # Neither the fresh launch nor the resumed launch may carry --continue,
    # because no transcript ever existed.
    assert all("--continue" not in launch for launch in launches), launches


async def test_interrupt_before_launch_captures_no_snapshot(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """CHANGE #1 (reached-live gate): if the session is interrupted before
    claude is launched (launch_ttyd_with_claude raises), launched_handle stays
    None, so the finally block must NOT capture a snapshot and must NOT mark
    the process resumable — even though credentials were planted."""
    from optio_claudecode import host_actions

    pid = "cc_interrupt_pre_launch"

    async def _boom(*a, **kw):
        raise RuntimeError("simulated interrupt before launch")

    monkeypatch.setattr(host_actions, "launch_ttyd_with_claude", _boom)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx = await _make_ctx(mongo_db, pid, resume=False)
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="(interrupt)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=True,
        credentials_json={"token": "test"},
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )

    with pytest.raises(Exception):
        await run_claudecode_session(ctx, cfg)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is None

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc.get("hasSavedState") is not True


async def test_no_credentials_captures_no_snapshot(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """CHANGE #2 (credentials-present guard): a session that launches normally
    but has no home/.claude/.credentials.json (unconfigured environment) must
    NOT capture a snapshot and must NOT mark the process resumable. Restoring
    such a snapshot would drop the agent to /login."""
    pid = "cc_no_creds"
    # `happy` launches normally but never writes .credentials.json, and this
    # config plants no credentials_json either — so home/.claude/.credentials.json
    # is absent at capture time. (Cannot use _cfg/_run_cycle here: _cfg now
    # plants creds to model a logged-in session.)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    ctx = await _make_ctx(mongo_db, pid, resume=False)
    cfg = ClaudeCodeTaskConfig(
        consumer_instructions="(no creds)",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        permission_mode="bypassPermissions",
        supports_resume=True,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )
    await run_claudecode_session(ctx, cfg)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is None

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc.get("hasSavedState") is not True


async def test_resume_relocates_old_root_claude_json(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """Regression: an OLD-layout `.claude.json` (HOME root) carried in a restored
    snapshot must be relocated into `.claude/` on resume, mirroring the seed
    consume path's `_rekey_claude_json_projects`. Without it, claude (driven by
    CLAUDE_CONFIG_DIR=<home>/.claude) cannot find `.claude.json` -> folder-trust
    prompt -> which bypassPermissions does NOT suppress -> the session hangs.
    """
    pid = "cc_resume_reloc"

    # 1) Fresh run: plant an old-root home/.claude.json so it lands in the workdir
    #    blob (it sits OUTSIDE home/.claude, so the capture-time wipe spares it).
    async def _plant_old_root(hook_ctx):
        wd = hook_ctx._host.workdir
        await hook_ctx.run_on_host(
            "printf %s "
            "'{\"projects\":{\"/old/cwd\":{\"hasTrustDialogAccepted\":true}}}' "
            f"> {wd}/home/.claude.json"
        )

    ctx = await _make_ctx(mongo_db, pid, resume=False)
    cfg = dataclasses.replace(
        _cfg(shim_install_dir, claude_cache_dir, "idempotent_done"),
        before_execute=_plant_old_root,
    )
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "idempotent_done")
    await run_claudecode_session(ctx, cfg)

    # 2) Resume: assert the old-root file was relocated into .claude/ and removed.
    observed: dict[str, bool] = {}

    async def _assert_relocated(hook_ctx):
        wd = hook_ctx._host.workdir
        observed["new"] = os.path.exists(f"{wd}/home/.claude/.claude.json")
        observed["old"] = os.path.exists(f"{wd}/home/.claude.json")

    ctx2 = await _make_ctx(mongo_db, pid, resume=True)
    cfg2 = dataclasses.replace(
        _cfg(shim_install_dir, claude_cache_dir, "idempotent_done"),
        before_execute=_assert_relocated,
    )
    await run_claudecode_session(ctx2, cfg2)

    assert observed.get("new") is True, "resume must relocate .claude.json into .claude/"
    assert observed.get("old") is False, "old-root .claude.json must be removed after relocation"


# --------------------------------------------------------------------------
# Resurrect: a capture cut off after its session step (the 2026-10-06 shape),
# then the Resurrect hook, then a real resume from the resurrected snapshot.
# Spec: docs/2026-10-07-resurrect-failed-session-design.md, Testing.
# --------------------------------------------------------------------------

_WORK_MARKER = "UNSAVED-WORK.txt"


async def _resurrectable_ctx(mongo_db, process_id: str, *, resume: bool, state: str = "running"):
    """Like _make_ctx, for a task that has a resurrect hook (supportsResurrect),
    so mark_unsaved_work is honoured as in production."""
    async def _hook(ctx):  # never called: the test calls the real hook
        raise AssertionError("not used")
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
        resurrect=_hook,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": state}}},
    )
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        resume=resume,
    )


def _local_taskdir(process_id: str) -> tuple[str, str]:
    from optio_host.paths import task_dir
    taskdir = task_dir(ssh=None, process_id=process_id, consumer_name="optio-claudecode")
    return taskdir, os.path.join(taskdir, "workdir")


async def _session_argv_lines(mongo_db, blob_id) -> list[list[str]]:
    import io, tarfile
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(blob_id)
    data = await stream.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("fake_claude_argv.json"))
        lines = tar.extractfile(member).read().decode("utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


async def test_resume_after_resurrect_restores_the_cut_off_runs_work(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    import optio_claudecode.session as S
    from optio_claudecode import pending_captures as PC
    from optio_claudecode.resurrect import resurrect_claudecode_session

    pid = "cc_resurrect_resume"
    scenario = "idempotent_done"  # writes a transcript: a resume passes --continue
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
    cfg = _cfg(shim_install_dir, claude_cache_dir, scenario)
    taskdir, workdir = _local_taskdir(pid)

    # 1) A first run saves snapshot A normally (without the work below).
    await run_claudecode_session(await _resurrectable_ctx(mongo_db, pid, resume=False), cfg)
    snap_a = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap_a is not None
    assert not os.path.exists(taskdir)

    # 2) A resumed run goes live and does work (after_execute runs after the
    #    session, before the teardown capture). Its capture is cut off while
    #    streaming the workdir: session blob stored, home/.claude removed,
    #    pending record written, partial workdir chunks, no snapshot record.
    async def _do_work(hook_ctx):
        await hook_ctx.run_on_host(f"echo 'six hours of work' > {hook_ctx._host.workdir}/{_WORK_MARKER}")

    real_stream = S._stream_archive_to_blob

    async def _cut_off(ctx_, host_, exclude, wwriter, **kw):
        # GridFS buffers up to 32 MB before inserting chunks; a real archive
        # (784 MB on 2026-10-06) cut off mid-stream leaves flushed chunks of
        # the recorded blob and no fs.files document. Model that directly.
        await mongo_db["fs.chunks"].insert_one(
            {"files_id": wwriter.file_id, "n": 0, "data": b"partial"},
        )
        raise RuntimeError("capture cut off (simulated force-cancel)")
    monkeypatch.setattr(S, "_stream_archive_to_blob", _cut_off)
    ctx2 = await _resurrectable_ctx(mongo_db, pid, resume=True)
    await run_claudecode_session(ctx2, dataclasses.replace(cfg, after_execute=_do_work))
    monkeypatch.setattr(S, "_stream_archive_to_blob", real_stream)

    # The 2026-10-06 shape, left by the run itself.
    assert os.path.isfile(os.path.join(workdir, _WORK_MARKER)), "taskdir must be kept"
    assert not os.path.exists(os.path.join(workdir, "home", ".claude"))
    pending = await PC.load_pending_capture(mongo_db, "test", pid)
    assert pending is not None
    assert await mongo_db["fs.chunks"].count_documents({"files_id": pending["workdirBlobId"]}) >= 1
    assert await mongo_db["fs.files"].count_documents({"_id": pending["workdirBlobId"]}) == 0
    assert (await load_latest_snapshot(mongo_db, prefix="test", process_id=pid))["_id"] == snap_a["_id"]
    doc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert doc["hasUnsavedWork"] is True
    # The engine force-fails the run.
    await mongo_db["test_processes"].update_one(
        {"processId": pid}, {"$set": {"status": {"state": "failed"}}},
    )

    # 3) Resurrect (the hook optio-core runs; ctx.resume=False).
    ctx_r = await _resurrectable_ctx(mongo_db, pid, resume=False, state="failed")
    await resurrect_claudecode_session(ctx_r, cfg)
    snap_r = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap_r["endState"] == "resurrected"
    assert snap_r["sessionBlobId"] == pending["sessionBlobId"]
    assert await PC.load_pending_capture(mongo_db, "test", pid) is None
    assert await mongo_db["fs.chunks"].count_documents({"files_id": pending["workdirBlobId"]}) == 0
    assert not os.path.exists(taskdir)

    # 4) The resume optio-core launches next restores that snapshot.
    observed: dict[str, bool] = {}

    async def _observe(hook_ctx):
        wd = hook_ctx._host.workdir
        observed["work"] = os.path.isfile(f"{wd}/{_WORK_MARKER}")
        observed["transcript"] = os.path.isfile(
            f"{wd}/home/.claude/projects/resumed/session.jsonl",
        )
        observed["argv"] = os.path.isfile(f"{wd}/home/.claude/fake_claude_argv.json")

    ctx3 = await _resurrectable_ctx(mongo_db, pid, resume=True)
    await run_claudecode_session(ctx3, dataclasses.replace(cfg, before_execute=_observe))
    assert observed == {"work": True, "transcript": True, "argv": True}, observed

    # home/.claude came from the cut-off run's session blob: its argv log
    # holds runs 1 and 2 (snapshot A's would hold only run 1), and run 3 was
    # launched with --continue.
    snap_c = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap_c["_id"] != snap_r["_id"]
    launches = await _session_argv_lines(mongo_db, snap_c["sessionBlobId"])
    assert len(launches) == 3, launches
    assert "--continue" not in launches[0]
    assert "--continue" in launches[1]
    assert "--continue" in launches[2]


async def test_teardown_keeps_the_taskdir_when_the_capture_raises(
    mongo_db, task_root, shim_install_dir, claude_cache_dir, monkeypatch,
):
    """Wiring: a capture that raises inside the teardown leaves the task
    directory on the host and hasUnsavedWork set (the run went live)."""
    import optio_claudecode.session as S

    pid = "cc_capture_raises"
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "happy")
    calls = []

    async def _boom(*a, **kw):
        calls.append(kw.get("end_state"))
        raise RuntimeError("capture failed")
    monkeypatch.setattr(S, "_capture_snapshot", _boom)
    taskdir, workdir = _local_taskdir(pid)

    await run_claudecode_session(
        await _resurrectable_ctx(mongo_db, pid, resume=False),
        _cfg(shim_install_dir, claude_cache_dir, "happy"),
    )

    assert calls == ["done"]
    assert os.path.isdir(workdir) and os.listdir(workdir)
    doc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert doc["hasUnsavedWork"] is True
    assert doc.get("hasSavedState") is not True
