import pytest

import optio_claudecode.session as S


class _Result:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0


class _Host:
    """Stub host. ``existing`` is a set of marker paths reported present."""

    def __init__(self, workdir, existing=None):
        self.workdir = workdir
        self.existing = set(existing or ())
        self.written = []
        self.removed = []
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        # Emulate `test -e <path> && echo YES || true` marker probes.
        if cmd.startswith("test -e "):
            path = cmd.split("test -e ", 1)[1].split(" ", 1)[0].strip("'\"")
            return _Result("YES\n" if path in self.existing else "")
        if cmd.startswith("rm -f "):
            path = cmd.split("rm -f ", 1)[1].strip().strip("'\"")
            self.removed.append(path)
            self.existing.discard(path)
        return _Result()

    async def write_text(self, rel, text):
        self.written.append(rel)
        self.existing.add(f"{self.workdir.rstrip('/')}/{rel}")


class _Config:
    workdir_exclude = None
    session_blob_encrypt = None


class _Ctx:
    process_id = "pid-1"
    resume = True


@pytest.fixture
def patched(monkeypatch):
    rec = {"alive": False, "teardown": [], "capture": [], "fail_capture": False}

    async def _alive(host, tmux_path, socket, session):
        return rec["alive"]

    async def _require_tmux(host):
        return "tmux"

    def _socket(host):
        return "/tmp/optio-cc-deadbeef.sock"

    async def _teardown(host, **kw):
        rec["teardown"].append(kw)

    async def _capture(ctx, host, **kw):
        rec["capture"].append(kw)
        if rec["fail_capture"]:
            raise RuntimeError("capture failed")

    monkeypatch.setattr(S.host_actions, "tmux_session_alive", _alive)
    monkeypatch.setattr(S.host_actions, "_require_tmux", _require_tmux)
    monkeypatch.setattr(S.host_actions, "_tmux_socket_path", _socket)
    monkeypatch.setattr(S.host_actions, "teardown_session_tree", _teardown)
    monkeypatch.setattr(S, "_capture_snapshot", _capture)
    return rec


@pytest.mark.asyncio
async def test_noop_when_no_session_and_no_marker(patched, tmp_path):
    patched["alive"] = False
    host = _Host(str(tmp_path))
    await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    assert patched["teardown"] == []
    assert patched["capture"] == []
    assert host.written == []


@pytest.mark.asyncio
async def test_triggers_on_live_session_kill_before_capture(patched, tmp_path):
    patched["alive"] = True
    host = _Host(str(tmp_path))
    await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    # Marker written, orphan killed, then captured — kill strictly before capture.
    assert host.written == [".optio-rescue-pending"]
    assert len(patched["teardown"]) == 1
    assert patched["teardown"][0]["ttyd_handle"] is None
    assert len(patched["capture"]) == 1
    # end_state marks the rescue for forensics.
    assert patched["capture"][0]["end_state"] == "rescued"
    # Marker excluded from the snapshot so a restored workdir does not loop.
    assert ".optio-rescue-pending" in (patched["capture"][0]["workdir_exclude"] or [])
    # Marker cleared after capture success.
    marker = f"{str(tmp_path).rstrip('/')}/.optio-rescue-pending"
    assert marker in host.removed


@pytest.mark.asyncio
async def test_triggers_on_marker_even_without_session(patched, tmp_path):
    patched["alive"] = False
    marker = f"{str(tmp_path).rstrip('/')}/.optio-rescue-pending"
    host = _Host(str(tmp_path), existing={marker})
    await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    # Detect-by-marker path still rescues (mid-rescue retry).
    assert len(patched["teardown"]) == 1
    assert len(patched["capture"]) == 1


@pytest.mark.asyncio
async def test_capture_failure_persists_marker_and_reraises(patched, tmp_path):
    patched["alive"] = True
    patched["fail_capture"] = True
    host = _Host(str(tmp_path))
    with pytest.raises(RuntimeError):
        await S._rescue_orphan_if_present(_Ctx(), host, _Config())
    marker = f"{str(tmp_path).rstrip('/')}/.optio-rescue-pending"
    # Marker NOT removed — a retried resume re-enters rescue.
    assert marker not in host.removed


@pytest.mark.asyncio
async def test_skipped_when_not_resuming(patched, tmp_path):
    patched["alive"] = True

    class _FreshCtx:
        process_id = "pid-1"
        resume = False

    host = _Host(str(tmp_path))
    await S._rescue_orphan_if_present(_FreshCtx(), host, _Config())
    assert patched["teardown"] == []
    assert patched["capture"] == []


# --------------------------------------------------------------------------
# Integration: real (shim) detached session -> orphan -> rescue.
#
# Exercises the REAL teardown_session_tree + _capture_snapshot against a real
# local tmux tree: launch a detached tmux session whose "claude" is a long-lived
# sleep shim at the deterministic <workdir>/home/.local/bin/claude path, abandon
# it (no teardown) so it is the orphan, then run _rescue_orphan_if_present and
# assert the orphan is gone, a fresh "rescued" snapshot exists, and the marker is
# cleared. Mirrors the launch scaffolding in test_tmux_persistence.py.
# --------------------------------------------------------------------------

import os  # noqa: E402
import shutil  # noqa: E402

from optio_claudecode import ClaudeCodeTaskConfig  # noqa: E402
from optio_claudecode import host_actions as H  # noqa: E402


_NO_TMUX = shutil.which("tmux") is None


async def _launch_orphan_session(host):
    """Start a detached tmux session whose 'claude' records its pid then sleeps,
    on the deterministic per-task socket the rescue probes. Returns
    (tmux_path, socket)."""
    workdir = host.workdir
    os.makedirs(f"{workdir}/home/.local/bin", exist_ok=True)
    claude = f"{workdir}/home/.local/bin/claude"
    marker = f"{workdir}/claude.pid"
    with open(claude, "w") as f:
        f.write(f"#!/bin/bash\necho $$ > {marker}\nexec sleep 60\n")
    os.chmod(claude, 0o755)

    # Plant credentials so the capture credentials-present guard passes.
    os.makedirs(f"{workdir}/home/.claude", exist_ok=True)
    with open(f"{workdir}/home/.claude/.credentials.json", "w") as f:
        f.write('{"token": "test"}')

    tmux_path = await H._require_tmux(host)
    socket = H._tmux_socket_path(host)
    argv = H.build_tmux_session_argv(
        tmux_path=tmux_path, claude_path=claude, workdir=workdir,
        socket_path=socket, session_name="optio",
        extra_env=None, claude_flags=[],
    )
    import shlex
    cmd = " ".join(shlex.quote(a) for a in argv)
    r = await host.run_command(cmd)
    assert r.exit_code == 0, r.stderr
    return tmux_path, socket


@pytest.mark.skipif(_NO_TMUX, reason="tmux not installed on the worker")
@pytest.mark.asyncio
async def test_rescue_end_to_end_kills_orphan_and_snapshots(
    mongo_db, tmp_path, ctx_and_captures,
):
    """Launch a real (shim) detached session, abandon it so it becomes an
    orphan, then run _rescue_orphan_if_present and assert: the tmux session is
    gone, no claude shim remains, and a fresh 'rescued' snapshot was inserted.

    Manual run:
        .venv/bin/python -m pytest \\
          packages/optio-claudecode/tests/test_rescue_orphan.py \\
          -k end_to_end -v
    """
    import asyncio

    from optio_claudecode.snapshots import load_latest_snapshot
    from optio_host.host import LocalHost

    ctx, _cap, _flag = ctx_and_captures
    ctx.resume = True

    # (a)+(b) Build the LocalHost and launch the detached (shim) session on the
    #         deterministic per-task socket.
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)

    tmux_path, socket = await _launch_orphan_session(host)
    # Confirm the orphan is actually alive before we rescue it.
    assert await H.tmux_session_alive(host, tmux_path, socket, "optio")
    pid_file = f"{host.workdir}/claude.pid"
    for _ in range(30):
        if os.path.exists(pid_file):
            break
        await asyncio.sleep(0.1)
    assert os.path.exists(pid_file)
    child_pid = int(open(pid_file).read().strip())

    # (c) Crash simulation: we never captured an in-process handle, and we do
    #     not tear the session down. The tmux/claude tree is the orphan.

    config = ClaudeCodeTaskConfig(
        consumer_instructions="(rescue e2e)",
        fs_isolation=False,
        supports_resume=True,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )

    # (d) Rescue.
    await S._rescue_orphan_if_present(ctx, host, config)

    # (e) Assertions.
    # The tmux session is gone (orphan killed before capture).
    assert (await H.tmux_session_alive(host, tmux_path, socket, "optio")) is False
    # The sleep shim child is reaped (kill-session SIGHUPs the pane tree).
    await asyncio.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)

    # A fresh 'rescued' snapshot was inserted.
    snap = await load_latest_snapshot(
        mongo_db, prefix=ctx._prefix, process_id=ctx.process_id,
    )
    assert snap is not None
    assert snap["endState"] == "rescued"

    # Marker cleared on success.
    marker = f"{host.workdir.rstrip('/')}/.optio-rescue-pending"
    r = await host.run_command(
        f"test -e {marker} && echo Y || true"
    )
    assert "Y" not in r.stdout
