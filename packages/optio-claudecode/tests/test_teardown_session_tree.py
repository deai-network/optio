import pytest

import optio_claudecode.host_actions as H


@pytest.fixture
def calls(monkeypatch):
    rec = []

    async def _kill_ttyd(host, socket):
        rec.append(("ttyd_socket", socket))

    async def _kill_session(host, tmux_path, socket, session):
        rec.append(("kill_session", session))

    async def _kill_claude(host, claude_path, **kw):
        rec.append(("kill_claude", claude_path))

    async def _await_gone(host, claude_path, **kw):
        rec.append(("await_gone", claude_path))
        return True

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _kill_ttyd)
    monkeypatch.setattr(H, "_kill_tmux_session", _kill_session)
    monkeypatch.setattr(H, "kill_claude_processes", _kill_claude)
    monkeypatch.setattr(H, "await_claude_gone", _await_gone)
    return rec


class _FakeHandle:
    pass


class _Host:
    def __init__(self):
        self.terminated = []

    async def terminate_subprocess(self, handle, *, aggressive):
        self.terminated.append((handle, aggressive))


@pytest.mark.asyncio
async def test_orphan_branch_uses_kill_ttyd_by_socket(calls):
    host = _Host()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", claude_path="/w/home/.local/bin/claude",
        ttyd_handle=None, aggressive=True,
    )
    # All four steps, in order; orphan ttyd path; no terminate_subprocess.
    assert [c[0] for c in calls] == [
        "ttyd_socket", "kill_session", "kill_claude", "await_gone",
    ]
    assert host.terminated == []


@pytest.mark.asyncio
async def test_handle_branch_uses_terminate_subprocess(calls):
    host = _Host()
    handle = _FakeHandle()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", claude_path="/w/home/.local/bin/claude",
        ttyd_handle=handle, aggressive=False,
    )
    assert host.terminated == [(handle, False)]
    # ttyd-by-socket NOT used when a handle is present.
    assert [c[0] for c in calls] == ["kill_session", "kill_claude", "await_gone"]


@pytest.mark.asyncio
async def test_steps_are_best_effort(calls, monkeypatch):
    # A failure in one step does not abort the rest.
    async def _boom(host, socket):
        raise RuntimeError("ttyd kill blew up")

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _boom)
    host = _Host()
    # Should not raise.
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", claude_path="/w/home/.local/bin/claude",
        ttyd_handle=None, aggressive=True,
    )
    # The remaining three steps still ran.
    assert [c[0] for c in calls] == ["kill_session", "kill_claude", "await_gone"]
