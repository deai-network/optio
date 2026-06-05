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


@pytest.mark.asyncio
async def test_returned_string_is_routed_to_send():
    async def cb(hook_ctx, display, text):
        return "reject: missing header"

    assert await _drive(cb) == ["reject: missing header"]


@pytest.mark.asyncio
async def test_none_return_sends_nothing():
    async def cb(hook_ctx, display, text):
        return None

    assert await _drive(cb) == []


@pytest.mark.asyncio
async def test_empty_return_sends_nothing():
    async def cb(hook_ctx, display, text):
        return "   "

    assert await _drive(cb) == []
