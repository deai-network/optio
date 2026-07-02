import pytest

import optio_codex.host_actions as H


@pytest.fixture
def calls(monkeypatch):
    rec = []

    async def _kill_ttyd(host, socket):
        rec.append(("ttyd_socket", socket))

    async def _kill_session(host, tmux_path, socket, session):
        rec.append(("kill_session", session))

    async def _kill_codex(host, codex_path, **kw):
        rec.append(("kill_codex", codex_path))

    async def _await_gone(host, codex_path, **kw):
        rec.append(("await_gone", codex_path))
        return True

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _kill_ttyd)
    monkeypatch.setattr(H, "_kill_tmux_session", _kill_session)
    monkeypatch.setattr(H, "kill_codex_processes", _kill_codex)
    monkeypatch.setattr(H, "await_codex_gone", _await_gone)
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
        tmux_session="optio", codex_path="/w/home/.local/bin/codex",
        ttyd_handle=None, aggressive=True,
    )
    assert [c[0] for c in calls] == [
        "ttyd_socket", "kill_session", "kill_codex", "await_gone",
    ]
    assert host.terminated == []


@pytest.mark.asyncio
async def test_handle_branch_uses_terminate_subprocess(calls):
    host = _Host()
    handle = _FakeHandle()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", codex_path="/w/home/.local/bin/codex",
        ttyd_handle=handle, aggressive=False,
    )
    assert host.terminated == [(handle, False)]
    assert [c[0] for c in calls] == ["kill_session", "kill_codex", "await_gone"]


@pytest.mark.asyncio
async def test_steps_are_best_effort(calls, monkeypatch):
    async def _boom(host, socket):
        raise RuntimeError("ttyd kill blew up")

    monkeypatch.setattr(H, "_kill_ttyd_by_socket", _boom)
    host = _Host()
    await H.teardown_session_tree(
        host, tmux_path="tmux", tmux_socket="/tmp/s.sock",
        tmux_session="optio", codex_path="/w/home/.local/bin/codex",
        ttyd_handle=None, aggressive=True,
    )
    assert [c[0] for c in calls] == ["kill_session", "kill_codex", "await_gone"]
