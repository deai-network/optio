"""Tests for the OPTIO_CANCEL_TRACE diagnostic helpers in optio_host.host
that can be exercised without a live SSH connection:

* ``_mib`` / ``_mib_per_s`` — the pure byte-counting/rate-formatting helpers
  used by ``RemoteHost.archive_workdir``'s progress/DONE trace lines.
* ``_traced`` — the async START/DONE/RAISED span helper shared by
  ``fetch_bytes_from_host`` (sibling of the identically-shaped helper in
  optio_claudecode.session).

``RemoteHost.archive_workdir``/``run_command``/``fetch_bytes_from_host``
themselves need a real (or SSH-container) connection and are covered
elsewhere (test_remote_host_connect.py); this file only covers the
connection-free pieces of the new tracing.
"""

import logging

import pytest

from optio_host import host as host_mod
from optio_host.host import (
    _ARCHIVE_TRACE_PROGRESS_INTERVAL_S,
    _mib,
    _mib_per_s,
    _traced,
)


# --- _mib / _mib_per_s -----------------------------------------------------


def test_mib_converts_bytes():
    assert _mib(1024 * 1024) == pytest.approx(1.0)
    assert _mib(0) == 0.0
    assert _mib(5 * 1024 * 1024) == pytest.approx(5.0)


def test_mib_per_s_computes_rate():
    assert _mib_per_s(10 * 1024 * 1024, 2.0) == pytest.approx(5.0)
    assert _mib_per_s(1024 * 1024, 1.0) == pytest.approx(1.0)


def test_mib_per_s_zero_or_negative_elapsed_is_zero_not_a_division_error():
    assert _mib_per_s(1024 * 1024, 0.0) == 0.0
    assert _mib_per_s(1024 * 1024, -1.0) == 0.0


def test_progress_interval_matches_spec():
    assert _ARCHIVE_TRACE_PROGRESS_INTERVAL_S == 10.0


# --- _traced ---------------------------------------------------------------


class _FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


async def test_traced_emits_nothing_when_trace_disabled(monkeypatch, caplog):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", False)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        async with _traced("fetch_bytes_from_host path=/x", clock=clock) as t:
            clock.tick(0.7)
    assert caplog.records == []
    assert t.elapsed == pytest.approx(0.7)


async def test_traced_emits_start_done_with_elapsed_when_enabled(monkeypatch, caplog):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", True)
    clock = _FakeClock()
    with caplog.at_level(logging.WARNING, logger="optio_core.cancel_trace"):
        async with _traced("fetch_bytes_from_host path=/x", clock=clock):
            clock.tick(1.2)
    messages = [r.getMessage() for r in caplog.records]
    assert any("fetch_bytes_from_host path=/x START" in m for m in messages)
    done = [m for m in messages if "fetch_bytes_from_host path=/x DONE" in m]
    assert len(done) == 1
    assert "1.2s" in done[0]
    assert all("optio-host" in m for m in messages)


async def test_traced_raised_propagates(monkeypatch):
    monkeypatch.setattr(host_mod, "_CANCEL_TRACE", False)
    with pytest.raises(ValueError, match="nope"):
        async with _traced("x"):
            raise ValueError("nope")
