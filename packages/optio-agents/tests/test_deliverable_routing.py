import asyncio

import pytest

from optio_agents.context import HookContext
from optio_agents.protocol import session as S


class _Host:
    async def fetch_bytes_from_host(self, path):
        return b"deliverable body"


class _Ctx:
    def report_progress(self, percent, message=None):
        pass


async def _drive(callback):
    """Run the deliverable loop once for a single 'x.md' deliverable; return
    the messages handed to send_to_agent (spied, so un-prefixed)."""
    hc = HookContext(_Ctx(), _Host())
    sent = []

    async def spy(msg):
        sent.append(msg)
        return True

    hc.send_to_agent = spy  # shadow the bound method with a spy
    q: asyncio.Queue = asyncio.Queue()
    await q.put(("/abs/x.md", "x.md"))
    task = asyncio.create_task(
        S._deliverable_fetch_loop(_Host(), callback, q, _Ctx(), hc)
    )
    await q.join()
    task.cancel()
    return sent


ACCEPT = "deliverable x.md: accepted. thanks for the good work."


@pytest.mark.asyncio
async def test_revision_string_is_wrapped_and_sent():
    async def cb(hook_ctx, display, text):
        return "missing recipe-dsl-version header"

    assert await _drive(cb) == ["deliverable x.md: missing recipe-dsl-version header"]


@pytest.mark.asyncio
async def test_none_return_acknowledges_accepted():
    async def cb(hook_ctx, display, text):
        return None

    assert await _drive(cb) == [ACCEPT]


@pytest.mark.asyncio
async def test_empty_return_acknowledges_accepted():
    async def cb(hook_ctx, display, text):
        return "   "

    assert await _drive(cb) == [ACCEPT]


@pytest.mark.asyncio
async def test_ok_return_acknowledges_accepted():
    async def cb(hook_ctx, display, text):
        return "ok"

    assert await _drive(cb) == [ACCEPT]


@pytest.mark.asyncio
async def test_no_callback_acknowledges_accepted():
    assert await _drive(None) == [ACCEPT]


@pytest.mark.asyncio
async def test_callback_raise_sends_trouble_note():
    async def cb(hook_ctx, display, text):
        raise RuntimeError("boom")

    sent = await _drive(cb)
    assert len(sent) == 1
    assert sent[0].startswith("deliverable x.md: I have trouble with this one.")
    assert "after you are resumed" in sent[0]
