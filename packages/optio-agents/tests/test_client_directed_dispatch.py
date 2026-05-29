"""_tail_and_dispatch routes the three client-directed events to ctx."""

import pytest

from optio_agents.protocol.session import _tail_and_dispatch


class _FakeHost:
    def __init__(self, lines, workdir="/wd"):
        self._lines = lines
        self.workdir = workdir

    async def tail_file(self, _path):
        for line in self._lines:
            yield line


class _FakeCtx:
    def __init__(self):
        self.browser = []
        self.attention = []
        self.domain = []
        self.progress = []

    async def request_browser_open(self, url):
        self.browser.append(url)
        return "rid-b"

    async def need_attention(self, reason):
        self.attention.append(reason)
        return "rid-a"

    async def domain_message(self, keyword, data):
        self.domain.append((keyword, data))
        return "rid-d"

    def report_progress(self, percent, message):
        self.progress.append((percent, message))


@pytest.mark.asyncio
async def test_dispatch_routes_browser_attention_domain():
    host = _FakeHost([
        "BROWSER: https://x\n",
        "ATTENTION: help me\n",
        'DOMAIN_MESSAGE: ev {"n": 1}\n',
        "DONE\n",
    ])
    ctx = _FakeCtx()
    import asyncio
    done = asyncio.Event()
    await _tail_and_dispatch(host, ctx, asyncio.Queue(), done, [])
    assert ctx.browser == ["https://x"]
    assert ctx.attention == ["help me"]
    assert ctx.domain == [("ev", {"n": 1})]
    assert done.is_set()
