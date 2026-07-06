"""Graceful-flush teardown gating for seeded antigravity sessions (Task 4.1).

agy authenticates with a rotating Google OAuth refresh token. If agy rotated
the token this session, the new token store must reach the seed via the
teardown save-back backstop — but an aggressive SIGKILL can beat agy's flush,
stranding the rotation (the seed keeps the now-spent token → the next launch
demands re-auth). So a SEEDED session is torn down GRACEFULLY (SIGTERM + wait)
even on cancel, giving agy time to persist its token store before the final
save-back reads it. A non-seeded session keeps the fast aggressive kill.

Two layers are proven here:
  * ``session._teardown_aggressive`` — the gating decision (truth table).
  * ``AntigravityConversation.close(aggressive=…)`` — the conversation-mode turn
    reaper honours the gate (graceful vs fast kill of an in-flight ``-p`` turn).
"""

from __future__ import annotations

import asyncio

import pytest

from optio_antigravity.session import _teardown_aggressive


# --- gating decision -----------------------------------------------------

def test_teardown_aggressive_grace_for_seeded_sessions():
    # Seeded + cancelled → graceful (False): let agy flush the rotated token.
    assert _teardown_aggressive(cancelled=True, seeded=True) is False
    # Non-seeded + cancelled → fast aggressive kill (True).
    assert _teardown_aggressive(cancelled=True, seeded=False) is True
    # A clean (non-cancelled) stop is never aggressive, seeded or not.
    assert _teardown_aggressive(cancelled=False, seeded=True) is False
    assert _teardown_aggressive(cancelled=False, seeded=False) is False


# --- conversation-mode turn reaper honours the gate ----------------------

async def _spy_and_run_inflight(conv):
    """Install a spy on the host's terminate_subprocess, start a slow (parked)
    turn, and wait for its process handle to appear. Returns (recorded, task)."""
    recorded: list[bool] = []
    orig = conv._host.terminate_subprocess

    async def _spy(handle, *, aggressive):
        recorded.append(aggressive)
        return await orig(handle, aggressive=aggressive)

    conv._host.terminate_subprocess = _spy  # type: ignore[method-assign]
    task = asyncio.ensure_future(conv.send("slow"))
    await asyncio.wait_for(conv._handle_ready.wait(), timeout=5.0)
    return recorded, task


async def test_close_seeded_uses_graceful_terminate(fake_agy_slow):
    conv = fake_agy_slow
    recorded, task = await _spy_and_run_inflight(conv)
    # Seeded teardown: SIGTERM-and-wait so agy flushes its token store.
    await conv.close(aggressive=False)
    with pytest.raises(Exception):
        await task
    assert recorded and recorded[-1] is False
    assert conv.closed is True


async def test_close_default_is_aggressive(fake_agy_slow):
    conv = fake_agy_slow
    recorded, task = await _spy_and_run_inflight(conv)
    # Default (non-seeded) teardown: fast aggressive kill.
    await conv.close()
    with pytest.raises(Exception):
        await task
    assert recorded and recorded[-1] is True
