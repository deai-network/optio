"""_tail_and_dispatch + _caller_message_loop route client-directed events."""

import asyncio

import pytest

from optio_agents.protocol.session import (
    _caller_message_loop,
    _tail_and_dispatch,
    run_log_protocol_session,
)
from optio_agents import get_protocol
from optio_agents.context import SYSTEM_MESSAGE_PREFIX, HookContext


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
        self.client = []
        self.progress = []

    async def request_browser_open(self, url):
        self.browser.append(url)
        return "rid-b"

    async def need_attention(self, reason):
        self.attention.append(reason)
        return "rid-a"

    async def client_message(self, keyword, data):
        self.client.append((keyword, data))
        return "rid-c"

    def report_progress(self, percent, message):
        self.progress.append((percent, message))


@pytest.mark.asyncio
async def test_dispatch_routes_browser_attention_client_caller():
    host = _FakeHost([
        "BROWSER: https://x\n",
        "ATTENTION: help me\n",
        'CLIENT_MESSAGE: ev {"n": 1}\n',
        'CALLER_MESSAGE: ask {"q": 2}\n',
        "DONE\n",
    ])
    ctx = _FakeCtx()
    done = asyncio.Event()
    caller_queue = asyncio.Queue()
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), caller_queue, done, [],
        get_protocol(
            browser="redirect", client_messages=True, caller_messages=True,
        ).parse_log_line,
    )
    assert ctx.browser == ["https://x"]
    assert ctx.attention == ["help me"]
    assert ctx.client == [("ev", {"n": 1})]
    assert caller_queue.get_nowait() == ("ask", {"q": 2})
    assert done.is_set()


@pytest.mark.asyncio
async def test_dispatch_ignores_browser_under_suppress():
    host = _FakeHost([
        "BROWSER: https://x\n",
        "ATTENTION: help me\n",
        "DONE\n",
    ])
    ctx = _FakeCtx()
    done = asyncio.Event()
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), asyncio.Queue(), done, [],
        get_protocol(browser="suppress").parse_log_line,
    )
    assert ctx.browser == []                 # BROWSER line was inert (UnknownLine)
    assert ctx.attention == ["help me"]      # ATTENTION still routed
    assert done.is_set()


@pytest.mark.asyncio
async def test_dispatch_messages_inert_when_disabled():
    host = _FakeHost([
        'CLIENT_MESSAGE: ev {"n": 1}\n',
        'CALLER_MESSAGE: ask {"q": 2}\n',
        "DONE\n",
    ])
    ctx = _FakeCtx()
    done = asyncio.Event()
    caller_queue = asyncio.Queue()
    await _tail_and_dispatch(
        host, ctx, asyncio.Queue(), caller_queue, done, [],
        get_protocol().parse_log_line,
    )
    assert ctx.client == []
    assert caller_queue.empty()
    # Disabled keywords surface verbatim as progress text.
    texts = [m for (_p, m) in ctx.progress]
    assert 'CLIENT_MESSAGE: ev {"n": 1}' in texts
    assert 'CALLER_MESSAGE: ask {"q": 2}' in texts


def _hook_ctx_with_sender(ctx, sent):
    hook_ctx = HookContext(ctx, _FakeHost([]))
    async def _sender(message):
        sent.append(message)
    hook_ctx._agent_sender = _sender
    return hook_ctx


@pytest.mark.asyncio
async def test_caller_loop_invokes_callback_and_sends_feedback():
    ctx = _FakeCtx()
    sent: list[str] = []
    received: list[tuple[str, object]] = []

    async def on_caller(hook_ctx, keyword, data):
        received.append((keyword, data))
        return "the answer is 42"

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(("ask", {"q": 2}))
    loop_task = asyncio.create_task(
        _caller_message_loop(on_caller, queue, ctx, _hook_ctx_with_sender(ctx, sent)),
    )
    await queue.join()
    loop_task.cancel()

    assert received == [("ask", {"q": 2})]
    # send_to_agent prefixes harness messages so the agent can tell them apart.
    assert sent == [f"{SYSTEM_MESSAGE_PREFIX}the answer is 42"]


@pytest.mark.asyncio
async def test_caller_loop_none_feedback_sends_nothing():
    ctx = _FakeCtx()
    sent: list[str] = []

    async def on_caller(hook_ctx, keyword, data):
        return None

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(("ask", 1))
    loop_task = asyncio.create_task(
        _caller_message_loop(on_caller, queue, ctx, _hook_ctx_with_sender(ctx, sent)),
    )
    await queue.join()
    loop_task.cancel()

    assert sent == []


@pytest.mark.asyncio
async def test_caller_loop_survives_callback_exception():
    ctx = _FakeCtx()
    sent: list[str] = []
    calls: list[str] = []

    async def on_caller(hook_ctx, keyword, data):
        calls.append(keyword)
        if keyword == "boom":
            raise RuntimeError("handler exploded")
        return "ok-2"

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put(("boom", 1))
    await queue.put(("fine", 2))
    loop_task = asyncio.create_task(
        _caller_message_loop(on_caller, queue, ctx, _hook_ctx_with_sender(ctx, sent)),
    )
    await queue.join()
    loop_task.cancel()

    assert calls == ["boom", "fine"]         # second message still processed
    assert sent == [f"{SYSTEM_MESSAGE_PREFIX}ok-2"]
    assert any("on_caller_message callback raised" in (m or "")
               for (_p, m) in ctx.progress)


class _GuardHost(_FakeHost):
    """Host stub for guard tests: run_log_protocol_session must raise the
    ValueError before touching the host, so every method explodes."""

    def __init__(self):
        super().__init__([])

    async def setup_workdir(self):
        raise AssertionError("guard must fire before workdir setup")


@pytest.mark.asyncio
async def test_guard_feature_on_without_callback():
    async def body(host, hook_ctx):
        pass

    with pytest.raises(ValueError, match="no on_caller_message"):
        await run_log_protocol_session(
            _GuardHost(), _FakeCtx(), body=body,
            protocol=get_protocol(caller_messages=True),
        )


@pytest.mark.asyncio
async def test_guard_callback_without_feature():
    async def body(host, hook_ctx):
        pass

    async def on_caller(hook_ctx, keyword, data):
        return None

    with pytest.raises(ValueError, match="does not enable it"):
        await run_log_protocol_session(
            _GuardHost(), _FakeCtx(), body=body,
            protocol=get_protocol(),
            on_caller_message=on_caller,
        )
