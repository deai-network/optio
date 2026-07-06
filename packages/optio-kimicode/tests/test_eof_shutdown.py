"""Unit tests for session._eof_shutdown — the EOF-based graceful shutdown of the
conversation (ACP-over-stdio) subprocess.

kimi acp exits cleanly (rc=0, ~20ms) on stdin EOF, whereas it ignores SIGTERM
for the full 5s graceful grace before SIGKILL. So the teardown closes stdin
first; terminate_subprocess is only the backstop.
"""

from __future__ import annotations

import asyncio

import pytest

from optio_kimicode import session as kc


class _Stdin:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Handle:
    def __init__(self, stdin):
        self.stdin = stdin


@pytest.mark.asyncio
async def test_closes_stdin_and_returns_true_when_process_exits(monkeypatch):
    async def _fast_wait(handle):
        return 0

    monkeypatch.setattr(kc, "proc_wait", _fast_wait)
    stdin = _Stdin()
    ok = await kc._eof_shutdown(_Handle(stdin), timeout=1.0)

    assert ok is True
    assert stdin.closed is True  # EOF was signalled


@pytest.mark.asyncio
async def test_returns_false_when_process_does_not_exit(monkeypatch):
    async def _hang(handle):
        await asyncio.sleep(10)

    monkeypatch.setattr(kc, "proc_wait", _hang)
    stdin = _Stdin()
    ok = await kc._eof_shutdown(_Handle(stdin), timeout=0.05)

    # Timed out waiting for exit → caller falls back to signals.
    assert ok is False
    assert stdin.closed is True  # stdin still closed (best effort)


@pytest.mark.asyncio
async def test_no_stdin_still_waits(monkeypatch):
    async def _fast_wait(handle):
        return 0

    monkeypatch.setattr(kc, "proc_wait", _fast_wait)
    ok = await kc._eof_shutdown(_Handle(None), timeout=1.0)
    assert ok is True
