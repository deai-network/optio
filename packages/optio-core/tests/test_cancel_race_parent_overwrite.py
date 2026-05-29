"""Regression test for a race in lifecycle.cancel where the parent's
W2 ('cancelling') write could overwrite the executor's terminal
'cancelled' write.

Scenario:
  parent (running)
    └─ child (running)

User cancels CHILD directly. Sequence:
  1. lifecycle.cancel(child) flips child state and propagates down (no
     grand-children). cancel(child) returns.
  2. child's execute_fn picks up the cancel flag, returns.
  3. child's _execute_process writes child state → 'cancelled'.
  4. child's execute_child: alpha-cascade — schedules cancel(parent).
  5. parent's _cancellation_flag is set; child returns.
  6. parent's execute_fn (which was awaiting run_child(child)) returns.
  7. parent's _execute_process writes parent state → 'cancelled'.
  8. The scheduled cancel(parent) task from step 4 also runs:
       - W1: state running → cancel_requested (matched while parent was
         still running).
       - W2: state → cancelling.
  9. If W2 is unconditional, it overwrites the terminal 'cancelled'
     state from step 7. Parent stuck at 'cancelling'.

Fix (this test verifies): W2 must be conditional on
'state == cancel_requested'. If the executor has already moved the row
to 'cancelled', W2's filter doesn't match, terminal state preserved.
"""
import asyncio
import time as _time

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import get_process_by_process_id, upsert_process


async def _wait_terminal(mongo_db, prefix, process_id, timeout=5.0):
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await get_process_by_process_id(mongo_db, prefix, process_id)
        if proc is not None and proc["status"]["state"] in {"done", "failed", "cancelled"}:
            return proc
        await asyncio.sleep(0.02)
    return await get_process_by_process_id(mongo_db, prefix, process_id)


async def test_cancel_child_does_not_leave_parent_stuck_at_cancelling(mongo_db):
    """Cancelling a child must allow the parent to reach 'cancelled' via its
    natural finalization. The alpha-cascade cancel(parent) MUST NOT overwrite
    the parent's terminal 'cancelled' state with 'cancelling'."""
    prefix = "race_p"
    child_started = asyncio.Event()

    async def child(ctx):
        child_started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        await ctx.run_child(child, "race-child", "Child")

    parent_inst = TaskInstance(execute=parent, process_id="race-parent", name="Parent")
    child_inst = TaskInstance(execute=child, process_id="race-child", name="Child")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, child_inst])

    runner = asyncio.create_task(optio.launch_and_wait("race-parent", session_id=None))
    await child_started.wait()
    await asyncio.sleep(0.05)

    # Cancel the CHILD (not the parent). The alpha-cascade fires
    # cancel(parent) in parallel with parent's natural finalization.
    await optio.cancel("race-child")
    await runner

    child_proc = await _wait_terminal(mongo_db, prefix, "race-child")
    parent_proc = await _wait_terminal(mongo_db, prefix, "race-parent")

    assert child_proc["status"]["state"] == "cancelled"
    assert parent_proc["status"]["state"] == "cancelled", (
        f"Parent stuck at {parent_proc['status']['state']!r} — "
        f"alpha-cascade cancel(parent) overwrote terminal 'cancelled'."
    )

    await optio.shutdown(grace_seconds=0.5)
