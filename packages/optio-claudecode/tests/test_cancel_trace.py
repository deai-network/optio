"""Tests for the OPTIO_CANCEL_TRACE diagnostic helpers in session.py:

* ``_traced`` — the async context manager that emits START/DONE/RAISED
  spans (with elapsed time) through the ``optio_core.cancel_trace`` logger,
  gated on ``OPTIO_CANCEL_TRACE``.
* ``_stream_archive_to_blob`` — the byte-counting/progress-cadence helper
  pulled out of ``_capture_snapshot`` so the "Snapshot: archiving workdir…"
  milestone logic can be driven with a fake host and an injected clock.

Diagnostic only: nothing here exercises real capture, Mongo, or a real
host — every test is a pure unit test against fakes.
"""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from optio_claudecode import session as session_mod
from optio_claudecode.session import (
    _ARCHIVE_PROGRESS_INTERVAL_S,
    _capture_snapshot,
    _stream_archive_to_blob,
    _traced,
)


class _FakeClock:
    """Monotonically-advancing fake clock, injectable wherever the code
    under test accepts a ``clock`` callable instead of ``time.monotonic``."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


# --- _traced -------------------------------------------------------------


async def test_traced_emits_nothing_when_trace_disabled(monkeypatch, caplog):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", False)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        async with _traced("widget teardown", clock=clock) as t:
            clock.tick(1.5)
    assert caplog.records == []
    # start/elapsed stay populated regardless of the trace gate — an
    # always-on milestone that reuses them must work with OPTIO_CANCEL_TRACE=0.
    assert t.elapsed == pytest.approx(1.5)


async def test_traced_emits_start_and_done_with_elapsed_when_enabled(monkeypatch, caplog):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", True)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        async with _traced("widget teardown", clock=clock):
            clock.tick(2.3)
    messages = [r.getMessage() for r in caplog.records]
    assert any("widget teardown START" in m for m in messages)
    done = [m for m in messages if "widget teardown DONE" in m]
    assert len(done) == 1
    assert "2.3s" in done[0]
    assert all("optio-claudecode" in m for m in messages)


async def test_traced_logs_raised_with_exception_type_and_still_propagates(monkeypatch, caplog):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", True)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        with pytest.raises(RuntimeError, match="boom"):
            async with _traced("widget teardown", clock=clock):
                clock.tick(0.4)
                raise RuntimeError("boom")
    messages = [r.getMessage() for r in caplog.records]
    raised = [m for m in messages if "widget teardown RAISED" in m]
    assert len(raised) == 1
    assert "RuntimeError" in raised[0]
    assert "0.4s" in raised[0]
    assert not any("DONE" in m for m in messages)


async def test_traced_never_swallows_even_when_trace_disabled(monkeypatch):
    monkeypatch.setattr(session_mod, "_CANCEL_TRACE", False)
    with pytest.raises(ValueError, match="nope"):
        async with _traced("x"):
            raise ValueError("nope")


# --- _stream_archive_to_blob ---------------------------------------------


class _FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)


class _FakeHost:
    """``archive_workdir`` is a *plain* method returning an AsyncIterator
    (see the ``Host`` protocol docstring in optio-host) — this fake mirrors
    that exactly, ticking the shared fake clock as each chunk becomes
    "available" so the 30 s progress cadence is exercised deterministically."""

    def __init__(self, chunks_with_delays, clock: _FakeClock) -> None:
        self._chunks_with_delays = chunks_with_delays
        self._clock = clock

    def archive_workdir(self, exclude):
        async def _gen():
            for chunk, delay in self._chunks_with_delays:
                self._clock.tick(delay)
                yield chunk
        return _gen()


class _FakeCtx:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def report_progress(self, pct, message):
        assert pct is None
        self.messages.append(message)


_MB = 1024 * 1024


def test_archive_progress_interval_matches_spec():
    assert _ARCHIVE_PROGRESS_INTERVAL_S == 30.0


async def test_archive_progress_reports_every_30s_with_exact_messages():
    clock = _FakeClock()
    ctx = _FakeCtx()
    chunks = [
        (b"a" * (5 * _MB), 10.0),  # t=10s: under 30s since start -> no report
        (b"b" * (5 * _MB), 25.0),  # t=35s: crosses 30s -> report
        (b"c" * (5 * _MB), 30.0),  # t=65s: crosses next 30s -> report
    ]
    host = _FakeHost(chunks, clock)
    writer = _FakeWriter()

    total = await _stream_archive_to_blob(
        ctx, host, None, writer, start=0.0, clock=clock,
    )

    assert total == 15 * _MB
    assert writer.chunks == [c for c, _ in chunks]
    assert ctx.messages == [
        "Snapshot: archiving workdir, 10 MB so far (35 s)",
        "Snapshot: archiving workdir, 15 MB so far (65 s)",
    ]


async def test_archive_progress_no_report_when_under_interval():
    clock = _FakeClock()
    ctx = _FakeCtx()
    chunks = [(b"x" * _MB, 5.0), (b"y" * _MB, 5.0)]  # total elapsed 10s < 30s
    host = _FakeHost(chunks, clock)
    writer = _FakeWriter()

    total = await _stream_archive_to_blob(
        ctx, host, None, writer, start=0.0, clock=clock,
    )

    assert total == 2 * _MB
    assert ctx.messages == []


async def test_archive_progress_exclude_passed_through_unchanged():
    clock = _FakeClock()
    ctx = _FakeCtx()
    seen_excludes = []

    class _RecordingHost(_FakeHost):
        def archive_workdir(self, exclude):
            seen_excludes.append(exclude)
            return super().archive_workdir(exclude)

    host = _RecordingHost([(b"z", 1.0)], clock)
    writer = _FakeWriter()
    await _stream_archive_to_blob(
        ctx, host, ["node_modules"], writer, start=0.0, clock=clock,
    )
    assert seen_excludes == [["node_modules"]]


# --- _capture_snapshot: the full always-on milestone sequence ------------


async def test_capture_snapshot_emits_the_four_milestones_in_order(monkeypatch):
    """End-to-end (fake ctx/host, no Mongo/SSH — mirrors
    test_session_blob_hooks.py's encrypt-hook test) check that
    _capture_snapshot's ctx.report_progress calls are exactly the four
    milestones from the spec, in order, with the right MB/byte formatting.
    The elapsed-seconds part is asserted by pattern (real wall-clock, not
    controlled here) rather than by exact value.
    """
    fake_session_tar = b"s" * (2 * 1024 * 1024)  # exactly 2.0 MB
    monkeypatch.setattr(
        session_mod, "_archive_home_claude",
        AsyncMock(return_value=fake_session_tar),
    )
    monkeypatch.setattr(session_mod, "insert_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(session_mod, "prune_snapshots", AsyncMock(return_value=[]))

    class _FakeWriter:
        def __init__(self, slot: str):
            self.slot = slot
            self._buf = bytearray()
            self.file_id = "f-" + slot
            self._position = 0

        async def write(self, b: bytes):
            self._buf.extend(b)
            self._position = len(self._buf)

    class _FakeBlobCtx:
        def __init__(self, slot: str):
            self._slot = slot

        async def __aenter__(self):
            self._w = _FakeWriter(self._slot)
            return self._w

        async def __aexit__(self, *exc):
            return False

    fake_ctx = MagicMock()
    fake_ctx.store_blob = lambda slot: _FakeBlobCtx(slot)
    fake_ctx._db = None
    fake_ctx._prefix = "test"
    fake_ctx.process_id = "pid-x"
    fake_ctx.delete_blob = AsyncMock()
    fake_ctx.mark_has_saved_state = AsyncMock()

    async def _fake_archive(_excl):
        yield b"w" * (3 * 1024 * 1024)  # 3 MB of workdir tar, one chunk

    fake_host = MagicMock()
    fake_host.workdir = "/tmp/wd"
    fake_host.archive_workdir = _fake_archive
    fake_host.run_command = AsyncMock(return_value=MagicMock(stdout="OK\n"))

    await _capture_snapshot(
        fake_ctx, fake_host,
        end_state="done",
        workdir_exclude=None,
        session_blob_encrypt=None,
    )

    messages = [call.args[1] for call in fake_ctx.report_progress.call_args_list]
    assert len(messages) == 4
    assert re.fullmatch(
        r"Snapshot: session state saved \(2\.0 MB, \d+ s\)", messages[0],
    )
    assert messages[1] == "Snapshot: archiving workdir…"
    assert re.fullmatch(
        r"Snapshot: workdir archived \(3 MB in \d+ s\)", messages[2],
    )
    assert messages[3] == "Snapshot saved"
