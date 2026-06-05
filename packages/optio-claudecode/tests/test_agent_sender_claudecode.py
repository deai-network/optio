import pytest

import optio_claudecode.host_actions as H


@pytest.mark.asyncio
async def test_sender_delegates_to_send_text_to_claude(monkeypatch):
    """The claudecode sender forwards (host, tmux_path, tmux_socket,
    tmux_session, message) to send_text_to_claude. Mirrors the closure built
    in run_claudecode_session."""
    calls = []

    async def fake_send(host, tmux_path, tmux_socket, tmux_session, message):
        calls.append((host, tmux_path, tmux_socket, tmux_session, message))

    monkeypatch.setattr(H, "send_text_to_claude", fake_send)

    host, tmux_path, tmux_socket, tmux_session = object(), "tmux", "/tmp/s", "optio"

    async def _agent_sender(message: str) -> None:
        await H.send_text_to_claude(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    await _agent_sender("ping")
    assert calls == [(host, "tmux", "/tmp/s", "optio", "ping")]
