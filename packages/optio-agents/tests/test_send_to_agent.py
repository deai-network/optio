import pytest

from optio_agents.context import HookContext, SYSTEM_MESSAGE_PREFIX


class _Ctx:
    def __init__(self):
        self.msgs = []

    def report_progress(self, percent, message=None):
        self.msgs.append(message)


@pytest.mark.asyncio
async def test_no_sender_returns_false_and_logs():
    hc = HookContext(_Ctx(), object())
    assert await hc.send_to_agent("hi") is False
    assert any("no channel" in (m or "") for m in hc._ctx.msgs)


@pytest.mark.asyncio
async def test_sender_success_prefixes_and_returns_true():
    sent = []

    async def sender(m):
        sent.append(m)

    hc = HookContext(_Ctx(), object())
    hc._agent_sender = sender
    assert await hc.send_to_agent("hi") is True
    assert sent == [f"{SYSTEM_MESSAGE_PREFIX}hi"]


@pytest.mark.asyncio
async def test_sender_raise_returns_false_and_logs():
    async def sender(m):
        raise RuntimeError("dead")

    hc = HookContext(_Ctx(), object())
    hc._agent_sender = sender
    assert await hc.send_to_agent("hi") is False
    assert any("send_to_agent failed" in (m or "") for m in hc._ctx.msgs)
