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
