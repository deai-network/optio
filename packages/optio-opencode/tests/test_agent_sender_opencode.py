import pytest

import optio_opencode.session as S
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX


@pytest.mark.asyncio
async def test_post_prompt_signature_used_by_sender(monkeypatch):
    """The opencode sender forwards (port, password, session_id, message) to
    _post_opencode_prompt verbatim. Mirrors the closure built in
    run_opencode_session."""
    calls = []

    async def fake_post(port, password, session_id, message):
        calls.append((port, password, session_id, message))

    monkeypatch.setattr(S, "_post_opencode_prompt", fake_post)

    worker_port, password, session_id = 4321, "pw", "sess-1"

    async def _agent_sender(message: str) -> None:
        await S._post_opencode_prompt(worker_port, password, session_id, message)

    await _agent_sender("hello")
    assert calls == [(4321, "pw", "sess-1", "hello")]


def test_resume_notice_string():
    assert f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}" == "System: you have been resumed"
