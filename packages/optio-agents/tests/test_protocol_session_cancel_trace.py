"""Tests for the OPTIO_CANCEL_TRACE diagnostic helper in
optio_agents.protocol.session: the ``_traced`` async span helper (sibling of
the identically-shaped helpers in optio_claudecode.session and
optio_host.host), and its use around the driver's ``finally:`` block —
the in-session task cancel/gather, and the ``after_execute`` hook call.

Diagnostic only: no test here asserts anything about capture/snapshot
behavior, only about the trace lines and that exceptions still propagate.
"""

import logging

import pytest

from optio_agents.protocol import session as session_mod
from optio_agents.protocol.session import _traced
from optio_host.host import LocalHost
from optio_agents import get_protocol


class _FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


class _Ctx:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def report_progress(self, percent, message=None):
        self.messages.append(message)

    def should_continue(self) -> bool:
        return True


async def _done_body(host, hook_ctx) -> None:
    await host.run_command(f"echo DONE >> {host.workdir}/optio.log")


# --- _traced ---------------------------------------------------------------


async def test_traced_emits_nothing_when_trace_disabled(monkeypatch, caplog):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", False)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        async with _traced("in-session task cancel/gather", clock=clock) as t:
            clock.tick(0.9)
    assert caplog.records == []
    assert t.elapsed == pytest.approx(0.9)


async def test_traced_emits_start_done_with_elapsed_when_enabled(monkeypatch, caplog):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", True)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        async with _traced("in-session task cancel/gather", clock=clock):
            clock.tick(3.1)
    messages = [r.getMessage() for r in caplog.records]
    assert any("in-session task cancel/gather START" in m for m in messages)
    done = [m for m in messages if "in-session task cancel/gather DONE" in m]
    assert len(done) == 1
    assert "3.1s" in done[0]
    assert all("optio-agents" in m for m in messages)


async def test_traced_raised_with_exception_type_and_propagates(monkeypatch, caplog):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", True)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        with pytest.raises(RuntimeError, match="boom"):
            async with _traced("after_execute hook", clock=clock):
                clock.tick(0.2)
                raise RuntimeError("boom")
    messages = [r.getMessage() for r in caplog.records]
    raised = [m for m in messages if "after_execute hook RAISED" in m]
    assert len(raised) == 1
    assert "RuntimeError" in raised[0]
    assert "0.2s" in raised[0]


# --- integration: the driver's finally block --------------------------------


async def test_finally_block_traces_task_gather_and_after_execute(
    monkeypatch, tmp_workdir, caplog,
):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", True)
    host = LocalHost(taskdir=tmp_workdir)
    await host.connect()
    await host.setup_workdir()

    after_calls: list[str] = []

    async def after_execute(hook_ctx) -> None:
        after_calls.append("called")

    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        await session_mod.run_log_protocol_session(
            host, _Ctx(), body=_done_body, after_execute=after_execute,
            protocol=get_protocol(),
        )

    assert after_calls == ["called"]
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "finally: in-session task cancel/gather START" in m for m in messages
    )
    assert any(
        "finally: in-session task cancel/gather DONE" in m for m in messages
    )
    assert any("finally: after_execute hook START" in m for m in messages)
    assert any("finally: after_execute hook DONE" in m for m in messages)
