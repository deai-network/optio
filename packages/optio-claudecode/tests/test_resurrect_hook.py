"""resurrect_claudecode_session against a real LocalHost workdir + GridFS."""

import asyncio
import io
import os
import signal
import tarfile

import pytest
from bson import ObjectId

import optio_claudecode.session as S
from optio_claudecode import pending_captures as PC
from optio_claudecode import resurrect as R
from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.snapshots import insert_snapshot, load_latest_snapshot
from optio_core import NothingToResurrect
from optio_host.host import LocalHost


def _config():
    return ClaudeCodeTaskConfig(
        consumer_instructions="(resurrect test)",
        fs_isolation=False,
        supports_resume=True,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )


@pytest.fixture
def host(tmp_path, monkeypatch):
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    h = LocalHost(taskdir=taskdir)
    os.makedirs(h.workdir, exist_ok=True)
    monkeypatch.setattr(S, "_build_host", lambda config, process_id: h)

    async def _no_orphan(host_, tmux_path, socket, session):
        return False
    monkeypatch.setattr(S.host_actions, "tmux_session_alive", _no_orphan)
    return h


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


async def _prepare(mongo_db, ctx):
    await mongo_db["test_processes"].update_one({"_id": ctx._process_oid}, {"$set": {
        "supportsResume": True, "supportsResurrect": True, "hasUnsavedWork": True,
        "status.state": "failed",
    }})


async def _blob(ctx, name, payload=b"tar"):
    async with ctx.store_blob(name) as w:
        await w.write(payload)
    return w.file_id


async def _archive_names(ctx, blob_id):
    async with ctx.load_blob(blob_id) as r:
        data = await r.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        return {os.path.normpath(n) for n in t.getnames()}


def _under(names, top):
    return [n for n in names if n == top or n.startswith(top + "/")]


async def _snapshot_before_now(mongo_db, ctx, *, session_blob_id, workdir_blob_id, end_state="cancelled"):
    """Insert a snapshot and move its capturedAt one second back, so blobs
    stored after it are strictly newer (BSON dates have millisecond
    resolution; in production minutes or hours separate the two)."""
    from datetime import timedelta
    doc = await insert_snapshot(mongo_db, prefix="test", process_id=ctx.process_id, end_state=end_state,
                                session_blob_id=session_blob_id, workdir_blob_id=workdir_blob_id,
                                deliverables_emitted=[])
    await mongo_db["test_claudecode_session_snapshots"].update_one(
        {"_id": doc["_id"]}, {"$set": {"capturedAt": doc["capturedAt"] - timedelta(seconds=1)}},
    )


@pytest.mark.asyncio
async def test_resurrect_with_home_claude_present(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    _write(f"{host.workdir}/home/.claude/.credentials.json", '{"t": 1}')
    _write(f"{host.workdir}/{S._RESCUE_MARKER}", "")
    _write(f"{host.workdir}/.git/HEAD", "ref")
    await R.resurrect_claudecode_session(ctx, _config())
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"
    assert not os.path.exists(host.taskdir)
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasSavedState"] is True
    names = await _archive_names(ctx, snap["workdirBlobId"])
    assert "CLAUDE.md" in names
    # The rescue marker joins the default excludes (.git), it does not replace them.
    assert not _under(names, S._RESCUE_MARKER) and not _under(names, ".git")
    assert not _under(names, "home/.claude")


@pytest.mark.asyncio
async def test_credentials_gone_during_the_save_keeps_the_taskdir(mongo_db, host, ctx_and_captures, monkeypatch):
    """The hook saw credentials, but the capture's own guard then refused
    (they disappeared in between): no snapshot was inserted, so the host is
    not cleaned up and the hook reports nothing saved."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    creds = f"{host.workdir}/home/.claude/.credentials.json"
    _write(creds, '{"t": 1}')
    real_check = R._credentials_present

    async def _check_then_lose_them(host_):
        present = await real_check(host_)
        os.remove(creds)
        return present
    monkeypatch.setattr(R, "_credentials_present", _check_then_lose_them)

    with pytest.raises(NothingToResurrect, match="credentials disappeared during the save"):
        await R.resurrect_claudecode_session(ctx, _config())

    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None
    assert os.path.exists(f"{host.workdir}/CLAUDE.md")


@pytest.mark.asyncio
async def test_resurrect_old_code_shape(mongo_db, host, ctx_and_captures):
    """2026-10-06: session blob stored, home/.claude removed, workdir archive
    cut off (partial chunks, no fs.files), no pending record (old code)."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    older = await _blob(ctx, "session", b"old")
    old_wd = await _blob(ctx, "workdir", b"oldwd")
    await _snapshot_before_now(mongo_db, ctx, session_blob_id=older, workdir_blob_id=old_wd)
    orphan = await _blob(ctx, "session", b"new-session")
    partial = ObjectId()
    await mongo_db["fs.chunks"].insert_one({"files_id": partial, "n": 0, "data": b"partial"})
    _write(f"{host.workdir}/CLAUDE.md", "six hours of work")

    await R.resurrect_claudecode_session(ctx, _config())

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == orphan
    assert snap["endState"] == "resurrected"
    # Unrecorded partial chunks are not ours to guess at: left alone.
    assert await mongo_db["fs.chunks"].count_documents({"files_id": partial}) == 1


@pytest.mark.asyncio
async def test_pending_record_partial_blob_deleted(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    orphan = await _blob(ctx, "session", b"s")
    partial = ObjectId()
    await mongo_db["fs.chunks"].insert_one({"files_id": partial, "n": 0, "data": b"partial"})
    await PC.record_pending_capture(mongo_db, "test", process_id=ctx.process_id,
                                    session_blob_id=orphan, workdir_blob_id=partial)
    _write(f"{host.workdir}/CLAUDE.md", "work")

    await R.resurrect_claudecode_session(ctx, _config())

    assert await mongo_db["fs.chunks"].count_documents({"files_id": partial}) == 0
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == orphan


@pytest.mark.asyncio
async def test_pending_record_naming_a_snapshot_blob_finishes_that_capture(mongo_db, host, ctx_and_captures):
    """A capture force-killed between insert_snapshot and
    delete_pending_capture (here: before mark_has_saved_state, on the
    process's first snapshot) leaves a record naming the committed
    snapshot's workdir blob. The work is saved: the blob stays, the
    bookkeeping is finished, the record and the taskdir go."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    s = await _blob(ctx, "session", b"s")
    w = await _blob(ctx, "workdir", b"w")
    await insert_snapshot(mongo_db, prefix="test", process_id=ctx.process_id, end_state="cancelled",
                          session_blob_id=s, workdir_blob_id=w, deliverables_emitted=[])
    await PC.record_pending_capture(mongo_db, "test", process_id=ctx.process_id,
                                    session_blob_id=s, workdir_blob_id=w)
    _write(f"{host.workdir}/CLAUDE.md", "work")  # home/.claude already removed

    await R.resurrect_claudecode_session(ctx, _config())

    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasSavedState"] is True
    assert doc["hasUnsavedWork"] is False
    assert not os.path.exists(host.taskdir)
    assert await mongo_db["fs.files"].count_documents({"_id": w}) == 1
    assert await mongo_db["fs.chunks"].count_documents({"files_id": w}) == 1
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["workdirBlobId"] == w  # no second snapshot


@pytest.mark.asyncio
async def test_committed_record_with_credentials_saves_the_new_work(mongo_db, host, ctx_and_captures):
    """A committed record next to a workdir that still has its credentials
    is not this run's cut-off capture (that removes home/.claude before
    recording): the workdir holds newer work and is saved."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    s = await _blob(ctx, "session", b"s")
    w = await _blob(ctx, "workdir", b"w")
    await _snapshot_before_now(mongo_db, ctx, session_blob_id=s, workdir_blob_id=w)
    await PC.record_pending_capture(mongo_db, "test", process_id=ctx.process_id,
                                    session_blob_id=s, workdir_blob_id=w)
    _write(f"{host.workdir}/CLAUDE.md", "new work")
    _write(f"{host.workdir}/home/.claude/.credentials.json", '{"t": 1}')

    await R.resurrect_claudecode_session(ctx, _config())

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"
    assert snap["workdirBlobId"] != w
    async with ctx.load_blob(snap["workdirBlobId"]) as r:
        data = await r.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        assert t.extractfile("./CLAUDE.md").read() == b"new work"
    assert not os.path.exists(host.taskdir)
    assert await mongo_db["fs.files"].count_documents({"_id": w}) == 1
    assert await mongo_db["fs.chunks"].count_documents({"files_id": w}) == 1
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None


@pytest.mark.asyncio
async def test_no_session_blob_but_a_snapshot_offers_resume(mongo_db, host, ctx_and_captures):
    """Nothing to save, but a snapshot exists that hasSavedState never
    flagged (a capture cut off before mark_has_saved_state, record already
    gone): flag it so Resume is offered; keep the taskdir."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    s = await _blob(ctx, "session", b"s")
    w = await _blob(ctx, "workdir", b"w")
    await insert_snapshot(mongo_db, prefix="test", process_id=ctx.process_id, end_state="cancelled",
                          session_blob_id=s, workdir_blob_id=w, deliverables_emitted=[])
    _write(f"{host.workdir}/CLAUDE.md", "work")

    with pytest.raises(NothingToResurrect, match="session state not found"):
        await R.resurrect_claudecode_session(ctx, _config())

    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasSavedState"] is True
    assert os.path.exists(f"{host.workdir}/CLAUDE.md")


@pytest.mark.asyncio
async def test_fallback_ignores_session_blob_older_than_latest_snapshot(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    # An unreferenced session blob from before the latest snapshot (e.g. an
    # on_session_saved blob of an earlier run) must not be picked. Uploaded
    # before insert_snapshot, so its uploadDate <= capturedAt.
    await _blob(ctx, "session", b"stale")
    s = await _blob(ctx, "session", b"s")
    w = await _blob(ctx, "workdir", b"w")
    await insert_snapshot(mongo_db, prefix="test", process_id=ctx.process_id, end_state="done",
                          session_blob_id=s, workdir_blob_id=w, deliverables_emitted=[])
    _write(f"{host.workdir}/CLAUDE.md", "work")
    with pytest.raises(NothingToResurrect, match="session state not found"):
        await R.resurrect_claudecode_session(ctx, _config())


@pytest.mark.asyncio
async def test_home_claude_without_credentials_uses_session_blob_fallback(mongo_db, host, ctx_and_captures):
    """An interrupted `rm -rf home/.claude` leaves the directory without its
    credentials: the session blob the capture stored is used, and the
    leftover home/.claude stays out of the plaintext workdir blob."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    older = await _blob(ctx, "session", b"old")
    old_wd = await _blob(ctx, "workdir", b"oldwd")
    await _snapshot_before_now(mongo_db, ctx, session_blob_id=older, workdir_blob_id=old_wd)
    orphan = await _blob(ctx, "session", b"new-session")
    _write(f"{host.workdir}/CLAUDE.md", "work")
    _write(f"{host.workdir}/home/.claude/projects/t.jsonl", "{}")  # no .credentials.json
    _write(f"{host.workdir}/{S._RESCUE_MARKER}", "")
    _write(f"{host.workdir}/.git/HEAD", "ref")

    await R.resurrect_claudecode_session(ctx, _config())

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == orphan
    assert snap["endState"] == "resurrected"
    names = await _archive_names(ctx, snap["workdirBlobId"])
    assert "CLAUDE.md" in names
    assert not _under(names, "home/.claude")
    assert not _under(names, S._RESCUE_MARKER) and not _under(names, ".git")
    assert not os.path.exists(host.taskdir)


@pytest.mark.asyncio
async def test_home_claude_without_credentials_and_no_blob_keeps_taskdir(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    _write(f"{host.workdir}/home/.claude/settings.json", "{}")  # no .credentials.json
    with pytest.raises(NothingToResurrect, match="session state not found"):
        await R.resurrect_claudecode_session(ctx, _config())
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None
    # Nothing was saved, so nothing on the host is removed.
    assert os.path.exists(f"{host.workdir}/CLAUDE.md")
    assert os.path.exists(f"{host.workdir}/home/.claude/settings.json")


@pytest.mark.asyncio
async def test_empty_workdir_is_nothing_to_resurrect(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    with pytest.raises(NothingToResurrect, match="workdir"):
        await R.resurrect_claudecode_session(ctx, _config())
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None


@pytest.mark.asyncio
async def test_missing_workdir_is_nothing_to_resurrect(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    os.rmdir(host.workdir)
    with pytest.raises(NothingToResurrect, match="workdir"):
        await R.resurrect_claudecode_session(ctx, _config())


@pytest.mark.asyncio
async def test_resurrect_stops_stray_optio_log_tail(mongo_db, host, ctx_and_captures):
    """The failed run's `tail -F -n +1 <workdir>/optio.log` is killed; the
    anchored pattern leaves a tail of any other path alone."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    _write(f"{host.workdir}/home/.claude/.credentials.json", '{"t": 1}')
    log = f"{host.workdir}/optio.log"
    _write(log, "")
    procs = []
    for path in (log, log + ".1"):
        procs.append(await asyncio.create_subprocess_exec(
            "tail", "-F", "-n", "+1", path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        ))
    stray, other = procs
    try:
        await R.resurrect_claudecode_session(ctx, _config())
        assert await asyncio.wait_for(stray.wait(), 60) == -signal.SIGTERM
    finally:
        for p in procs:
            if p.returncode is None:
                p.kill()
    assert await asyncio.wait_for(other.wait(), 60) == -signal.SIGKILL


@pytest.mark.asyncio
async def test_resurrect_without_tmux_skips_the_tmux_tree(mongo_db, host, ctx_and_captures, monkeypatch):
    """A worker without tmux (conversation mode) has no tmux session to stop;
    the optio.log tail is still killed."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)

    async def _no_tmux(host_):
        return None

    async def _must_not_run(*a, **kw):
        raise AssertionError("tmux probed on a worker without tmux")
    monkeypatch.setattr(S.host_actions, "find_tmux", _no_tmux)
    monkeypatch.setattr(S.host_actions, "tmux_session_alive", _must_not_run)
    monkeypatch.setattr(S.host_actions, "teardown_session_tree", _must_not_run)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    _write(f"{host.workdir}/home/.claude/.credentials.json", '{"t": 1}')
    log = f"{host.workdir}/optio.log"
    _write(log, "")
    stray = await asyncio.create_subprocess_exec(
        "tail", "-F", "-n", "+1", log,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await R.resurrect_claudecode_session(ctx, _config())
        assert await asyncio.wait_for(stray.wait(), 60) == -signal.SIGTERM
    finally:
        if stray.returncode is None:
            stray.kill()
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"


class _ProbeHost:
    """A host whose every command returns the given result."""

    workdir = "/srv/task/workdir"

    def __init__(self, stdout="", stderr="", exit_code=0):
        from optio_host.host import RunResult
        self.result = RunResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    async def run_command(self, cmd, **kw):
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", ["_workdir_has_content", "_credentials_present"])
async def test_a_failed_probe_raises_instead_of_answering_absent(probe):
    """A probe whose command fails (connection lost, shell error) gives no
    answer: taking it for "absent" would end in NothingToResurrect, a cleared
    flag and a Resume that wipes the work."""
    fn = getattr(R, probe)
    with pytest.raises(RuntimeError, match=r"exit 255.*connection lost"):
        await fn(_ProbeHost(stderr="ssh: connection lost", exit_code=255))
    # Clean answers stay answers.
    assert await fn(_ProbeHost(stdout="")) is False
    assert await fn(_ProbeHost(stdout="YES\n")) is True


@pytest.mark.asyncio
async def test_failed_workdir_probe_is_an_error_not_nothing_to_resurrect(mongo_db, host, ctx_and_captures, monkeypatch):
    from optio_host.host import RunResult
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    real_run = host.run_command

    async def _flaky(cmd, **kw):
        if "ls -A" in cmd:
            return RunResult(stdout="", stderr="ssh: connection lost", exit_code=255)
        return await real_run(cmd, **kw)
    monkeypatch.setattr(host, "run_command", _flaky)

    with pytest.raises(RuntimeError, match="exit 255"):
        await R.resurrect_claudecode_session(ctx, _config())
    assert os.path.exists(f"{host.workdir}/CLAUDE.md")


def test_factory_attaches_hook_only_for_resumable_configs():
    cfg = _config()
    ti = S.create_claudecode_task(process_id="p", name="P", config=cfg)
    assert ti.resurrect is not None
    cfg2 = ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False, supports_resume=False)
    assert S.create_claudecode_task(process_id="p2", name="P2", config=cfg2).resurrect is None
