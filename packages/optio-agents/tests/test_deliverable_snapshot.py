"""The driver snapshots the deliverable metadata when it accepts a DELIVERABLE: line.

``snapshot_metadata`` puts the driver's ``origin``, ``delivered_at`` and ``resumed`` first,
adds the provider's entries and clamps. A provider that raises, hangs or answers something
that is not a list costs the delivery nothing (RF2) and never puts a value in the log (RF1).
``_tail_and_dispatch`` takes the snapshot when it accepts the line, so a live model switch
before the callback runs does not change it (RF4); a three-argument callback still gets
exactly three arguments (RF3).
"""

import asyncio
import logging
from datetime import datetime, timezone

import pytest

from optio_agents import get_protocol
from optio_agents.context import SYSTEM_MESSAGE_PREFIX, HookContext
from optio_agents.deliverable_meta import (
    KEY_MODEL,
    KEY_ORIGIN,
    KEY_RESUMED,
    ORIGIN_AGENT,
    SOURCE_AGENT,
    entry,
    meta_get,
    snapshot_metadata,
)
from optio_agents.protocol import session as S
from optio_host.host import LocalHost

DRIVER_KEYS = ["origin", "delivered_at", "resumed"]
ACCEPT = f"{SYSTEM_MESSAGE_PREFIX}deliverable x.md: accepted. thanks for the good work."
LOGGER = "optio_agents.deliverable_meta"


class _Host:
    def __init__(self, lines=(), workdir="/wd"):
        self._lines = list(lines)
        self.workdir = workdir

    async def tail_file(self, _path):
        for line in self._lines:
            yield line

    async def fetch_bytes_from_host(self, path):
        return b"body"


class _Ctx:
    def __init__(self, resume=False):
        self.resume = resume
        self.progress = []

    def report_progress(self, percent, message=None):
        self.progress.append((percent, message))

    def should_continue(self) -> bool:
        return True


def _hook_ctx(sent=None):
    hook_ctx = HookContext(_Ctx(), _Host())

    async def sender(message):
        if sent is not None:
            sent.append(message)

    hook_ctx._agent_sender = sender
    return hook_ctx


def _keys(md):
    return [e["key"] for e in md]


def _warnings(caplog):
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]


# --- _tail_and_dispatch: the snapshot time ------------------------------------------


async def test_a_switch_after_the_line_does_not_change_the_snapshot():          # RF4
    state = {"model": "lyceum/kimi-k3.1-pro"}
    provider = lambda: [entry(KEY_MODEL, state["model"], SOURCE_AGENT)]
    q = asyncio.Queue()
    host = _Host(["DELIVERABLE: ./deliverables/x.md\n", "DONE\n"])
    await S._tail_and_dispatch(host, _Ctx(resume=False), q, asyncio.Queue(), asyncio.Event(), [],
                               get_protocol().parse_log_line, _hook_ctx(), None, deliverable_metadata=provider)
    state["model"] = "claude-opus-5"                         # the operator switches before the callback runs
    got = []

    async def cb(h, p, t, metadata=None):
        got.append(metadata)

    loop = asyncio.create_task(S._deliverable_fetch_loop(host, cb, q, _Ctx(resume=False), _hook_ctx()))
    await asyncio.wait_for(q.join(), 60)
    loop.cancel()
    assert meta_get(got[0], KEY_MODEL) == "lyceum/kimi-k3.1-pro"
    assert [e["key"] for e in got[0]][:3] == ["origin", "delivered_at", "resumed"]


async def test_a_rejected_line_takes_no_snapshot():
    calls = []

    def provider():
        calls.append(1)
        return []

    q = asyncio.Queue()
    sent = []
    host = _Host(["DELIVERABLE: ../outside.md\n", "DELIVERABLE: ./not-deliverables.md\n", "DONE\n"])
    await S._tail_and_dispatch(host, _Ctx(), q, asyncio.Queue(), asyncio.Event(), [],
                               get_protocol().parse_log_line, _hook_ctx(sent), None, deliverable_metadata=provider)
    assert calls == []
    assert q.empty()
    assert len(sent) == 2 and all("rejected" in m for m in sent)


# --- snapshot_metadata --------------------------------------------------------------


async def test_a_raising_provider_still_delivers_and_logs_no_value(caplog):     # RF1, RF2
    def provider(): raise RuntimeError("rq-sentinel-token-0000")
    md = await snapshot_metadata(provider, resumed=False)
    assert [e["key"] for e in md] == ["origin", "delivered_at", "resumed"]
    assert "rq-sentinel-token-0000" not in caplog.text and "RuntimeError" in caplog.text
    [warning] = _warnings(caplog)
    assert all(key in warning.getMessage() for key in DRIVER_KEYS)


async def test_the_drivers_entries_come_first():
    now = datetime(2026, 11, 18, 9, 30, 5, 123000, tzinfo=timezone.utc)
    md = await snapshot_metadata(lambda: [entry("model", "m", "optio")], resumed=True, now=now)
    assert _keys(md) == ["origin", "delivered_at", "resumed", "model"]
    assert meta_get(md, KEY_ORIGIN) == "agent"
    assert meta_get(md, KEY_RESUMED) is True
    assert md[1] == entry("delivered_at", "2026-11-18T09:30:05.123+00:00", "optio")
    assert all(e["source"] == "optio" for e in md[:3])


async def test_the_provider_cannot_push_out_the_drivers_entries():
    flood = [entry(f"k{i}", "v", SOURCE_AGENT) for i in range(40)] + [{"key": "x", "value": {"a": 1}, "source": "agent"}]
    md = await snapshot_metadata(lambda: flood, resumed=False)
    assert len(md) == 32
    assert _keys(md)[:3] == DRIVER_KEYS and _keys(md)[3:] == [f"k{i}" for i in range(29)]


async def test_an_async_provider_is_awaited():
    async def provider():
        await asyncio.sleep(0)
        return [entry(KEY_MODEL, "m", SOURCE_AGENT)]

    md = await snapshot_metadata(provider, resumed=False)
    assert _keys(md) == DRIVER_KEYS + ["model"]
    assert meta_get(md, KEY_MODEL) == "m"


async def test_a_hanging_provider_times_out(caplog):                            # RF2
    async def provider():
        await asyncio.Event().wait()

    md = await snapshot_metadata(provider, resumed=False, timeout=0.05)
    assert _keys(md) == DRIVER_KEYS
    [warning] = _warnings(caplog)
    assert "TimeoutError" in warning.getMessage()


@pytest.mark.parametrize("answer", [
    {"model": "x"},
    (entry(KEY_MODEL, "rq-sentinel-value-0001", SOURCE_AGENT),),
    None,
])
async def test_a_non_list_answer_counts_as_a_failure(caplog, answer):           # RF2
    md = await snapshot_metadata(lambda: answer, resumed=False)
    assert _keys(md) == DRIVER_KEYS
    assert len(_warnings(caplog)) == 1
    assert "rq-sentinel-value-0001" not in caplog.text


async def test_without_a_provider_only_the_drivers_entries(caplog):
    md = await snapshot_metadata(None, resumed=False)
    assert _keys(md) == DRIVER_KEYS
    assert meta_get(md, KEY_RESUMED) is False
    assert _warnings(caplog) == []


async def test_cancellation_propagates():
    def provider(): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await snapshot_metadata(provider, resumed=False)


# --- run_log_protocol_session on a LocalHost ----------------------------------------


def _body(acked):
    """An agent that writes deliverables/x.md, announces it, waits for the harness's
    reply (as the protocol tells it to), then reports DONE."""
    async def body(host, hook_ctx):
        await host.write_text("deliverables/x.md", "the recipe")
        await host.run_command(f"echo 'DELIVERABLE: ./deliverables/x.md' >> {host.workdir}/optio.log")
        await asyncio.wait_for(acked.wait(), 60)
        await host.run_command(f"echo DONE >> {host.workdir}/optio.log")
    return body


async def _run_session(tmp_workdir, on_deliverable, provider):
    """Run one resumed session that delivers x.md; return what was sent to the agent."""
    host = LocalHost(taskdir=tmp_workdir)
    await host.connect()
    sent, acked = [], asyncio.Event()

    async def sender(message):
        sent.append(message)
        acked.set()

    try:
        await asyncio.wait_for(S.run_log_protocol_session(
            host, _Ctx(resume=True), body=_body(acked), on_deliverable=on_deliverable,
            protocol=get_protocol(), agent_sender=sender, deliverable_metadata=provider,
        ), 60)
    finally:
        await host.disconnect()
    return sent


@pytest.mark.parametrize("arity", [3, 4])
async def test_a_session_delivers_the_snapshot(tmp_workdir, arity):            # RF3
    calls = []

    async def three(h, p, t):
        calls.append((h, p, t))

    async def four(h, p, t, md):
        calls.append((h, p, t, md))

    provider = lambda: [entry(KEY_MODEL, "m", SOURCE_AGENT)]
    sent = await _run_session(tmp_workdir, three if arity == 3 else four, provider)
    assert len(calls) == 1 and len(calls[0]) == arity
    assert calls[0][1:3] == ("x.md", "the recipe")
    assert sent == [ACCEPT]
    if arity == 4:
        md = calls[0][3]
        assert meta_get(md, KEY_RESUMED) is True
        assert meta_get(md, KEY_ORIGIN) == ORIGIN_AGENT
        assert entry(KEY_MODEL, "m", SOURCE_AGENT) in md


async def test_a_session_whose_provider_hangs_still_delivers(tmp_workdir, caplog):   # RF2
    calls = []

    async def four(h, p, t, md):
        calls.append(md)

    async def provider():
        await asyncio.Event().wait()

    sent = await _run_session(tmp_workdir, four, provider)
    assert len(calls) == 1
    assert _keys(calls[0]) == DRIVER_KEYS
    assert meta_get(calls[0], KEY_RESUMED) is True
    assert sent == [ACCEPT]
    assert "TimeoutError" in caplog.text
