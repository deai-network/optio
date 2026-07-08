"""Contract tests for distinguishing child failure from external cancel
in a parent's lifecycle signals (cancellation_flag, should_continue,
terminal state).

Spec: docs/2026-05-28-child-failure-cancel-distinction-design.md
"""
import asyncio
import time as _time

import pytest

from optio_core.exceptions import ChildProcessFailed
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import get_process_by_process_id, upsert_process


async def _wait_terminal(mongo_db, prefix, process_id, timeout=60.0):
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await get_process_by_process_id(mongo_db, prefix, process_id)
        if proc is not None and proc["status"]["state"] in {"done", "failed", "cancelled"}:
            return proc
        await asyncio.sleep(0.02)
    return await get_process_by_process_id(mongo_db, prefix, process_id)


# ---------- In-flight signal contract ----------

async def test_should_continue_true_inside_except_when_child_fails_in_group(mongo_db):
    """Parent runs parallel_group(survive_failure=False) with a failing child.
    Inside parent's `except ExceptionGroup` handler, ctx.should_continue() == True."""
    prefix = "cfcd_isct1"
    observed = {}

    async def fail_child(ctx):
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

    async def slow_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        try:
            async with ctx.parallel_group(survive_failure=False) as g:
                await g.spawn(execute=fail_child, process_id="fc1", name="FC1")
                await g.spawn(execute=slow_child, process_id="sc1", name="SC1")
        except* ChildProcessFailed:
            observed["should_continue"] = ctx.should_continue()
            observed["flag_set"] = ctx.cancellation_flag.is_set()
            raise

    parent_inst = TaskInstance(execute=parent, process_id="p_isct1", name="P_ISCT1")
    fc_inst = TaskInstance(execute=fail_child, process_id="fc1", name="FC1")
    sc_inst = TaskInstance(execute=slow_child, process_id="sc1", name="SC1")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, fc_inst, sc_inst])

    try:
        await asyncio.wait_for(optio.launch_and_wait("p_isct1", session_id=None), timeout=60.0)
    finally:
        await optio.shutdown(grace_seconds=0.5)

    assert observed["should_continue"] is True
    assert observed["flag_set"] is False


async def test_should_continue_false_when_parent_externally_cancelled(mongo_db):
    """Parent is externally cancelled while running a group. Inside parent's
    handling code, ctx.should_continue() == False."""
    prefix = "cfcd_isct2"
    observed = {}
    parent_started = asyncio.Event()

    async def loop_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        parent_started.set()
        try:
            async with ctx.parallel_group(survive_failure=False) as g:
                await g.spawn(execute=loop_child, process_id="lc2a", name="LC2A")
                await g.spawn(execute=loop_child, process_id="lc2b", name="LC2B")
        except BaseException:
            observed["should_continue"] = ctx.should_continue()
            observed["flag_set"] = ctx.cancellation_flag.is_set()
            raise

    parent_inst = TaskInstance(execute=parent, process_id="p_isct2", name="P_ISCT2")
    a_inst = TaskInstance(execute=loop_child, process_id="lc2a", name="LC2A")
    b_inst = TaskInstance(execute=loop_child, process_id="lc2b", name="LC2B")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, a_inst, b_inst])

    runner = asyncio.create_task(optio.launch_and_wait("p_isct2", session_id=None))
    await parent_started.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("p_isct2")
    await asyncio.wait_for(runner, timeout=60.0)
    await optio.shutdown(grace_seconds=0.5)

    assert observed["should_continue"] is False
    assert observed["flag_set"] is True


async def test_should_continue_false_when_child_cancelled_externally_no_survive(mongo_db):
    """Parent runs parallel_group(survive_cancel=False); external cancel of a
    child. Inside parent's `except ExceptionGroup`, should_continue() == False
    (cancellation cascades up)."""
    prefix = "cfcd_isct3"
    observed = {}
    a_running = asyncio.Event()
    b_running = asyncio.Event()

    async def loop_child(ctx):
        if ctx.process_id == "lc3a":
            a_running.set()
        elif ctx.process_id == "lc3b":
            b_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        try:
            async with ctx.parallel_group(survive_cancel=False) as g:
                await g.spawn(execute=loop_child, process_id="lc3a", name="LC3A")
                await g.spawn(execute=loop_child, process_id="lc3b", name="LC3B")
        except* BaseException:
            observed["should_continue"] = ctx.should_continue()
            observed["flag_set"] = ctx.cancellation_flag.is_set()
            raise

    parent_inst = TaskInstance(execute=parent, process_id="p_isct3", name="P_ISCT3")
    a_inst = TaskInstance(execute=loop_child, process_id="lc3a", name="LC3A")
    b_inst = TaskInstance(execute=loop_child, process_id="lc3b", name="LC3B")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, a_inst, b_inst])

    runner = asyncio.create_task(optio.launch_and_wait("p_isct3", session_id=None))
    await a_running.wait()
    await b_running.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("lc3a")
    await asyncio.wait_for(runner, timeout=60.0)
    await optio.shutdown(grace_seconds=0.5)

    assert observed["should_continue"] is False
    assert observed["flag_set"] is True


# ---------- Terminal-state contract ----------

async def test_parent_terminal_done_when_child_fails_and_parent_catches_returns(mongo_db):
    """Parent catches ExceptionGroup and returns normally → parent ends 'done'
    (parent took explicit responsibility)."""
    prefix = "cfcd_term1"

    async def fail_child(ctx):
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

    async def slow_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        try:
            async with ctx.parallel_group(survive_failure=False) as g:
                await g.spawn(execute=fail_child, process_id="fc_t1", name="FC")
                await g.spawn(execute=slow_child, process_id="sc_t1", name="SC")
        except* ChildProcessFailed:
            pass  # swallow; parent returns normally

    parent_inst = TaskInstance(execute=parent, process_id="p_t1", name="P_T1")
    fc_inst = TaskInstance(execute=fail_child, process_id="fc_t1", name="FC")
    sc_inst = TaskInstance(execute=slow_child, process_id="sc_t1", name="SC")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, fc_inst, sc_inst])

    try:
        await asyncio.wait_for(optio.launch_and_wait("p_t1", session_id=None), timeout=60.0)
    finally:
        await optio.shutdown(grace_seconds=0.5)

    parent_proc = await _wait_terminal(mongo_db, prefix, "p_t1")
    fc_proc = await _wait_terminal(mongo_db, prefix, "fc_t1")
    sc_proc = await _wait_terminal(mongo_db, prefix, "sc_t1")
    assert parent_proc["status"]["state"] == "done", parent_proc["status"]
    assert fc_proc["status"]["state"] == "failed"
    assert sc_proc["status"]["state"] == "cancelled"


async def test_parent_terminal_failed_when_child_fails_and_parent_reraises(mongo_db):
    """Parent does not catch (or catches and re-raises) → parent ends 'failed'."""
    prefix = "cfcd_term2"

    async def fail_child(ctx):
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

    async def slow_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        async with ctx.parallel_group(survive_failure=False) as g:
            await g.spawn(execute=fail_child, process_id="fc_t2", name="FC")
            await g.spawn(execute=slow_child, process_id="sc_t2", name="SC")

    parent_inst = TaskInstance(execute=parent, process_id="p_t2", name="P_T2")
    fc_inst = TaskInstance(execute=fail_child, process_id="fc_t2", name="FC")
    sc_inst = TaskInstance(execute=slow_child, process_id="sc_t2", name="SC")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, fc_inst, sc_inst])

    try:
        await asyncio.wait_for(optio.launch_and_wait("p_t2", session_id=None), timeout=60.0)
    finally:
        await optio.shutdown(grace_seconds=0.5)

    parent_proc = await _wait_terminal(mongo_db, prefix, "p_t2")
    fc_proc = await _wait_terminal(mongo_db, prefix, "fc_t2")
    sc_proc = await _wait_terminal(mongo_db, prefix, "sc_t2")
    assert parent_proc["status"]["state"] == "failed", parent_proc["status"]
    assert fc_proc["status"]["state"] == "failed"
    assert sc_proc["status"]["state"] == "cancelled"


async def test_parent_terminal_cancelled_when_child_cancel_cascades_and_parent_catches_returns(mongo_db):
    """Group survive_cancel=False; external child cancel; parent catches and
    returns → parent ends 'cancelled' (cancellation cascade preserved)."""
    prefix = "cfcd_term3"
    a_running = asyncio.Event()
    b_running = asyncio.Event()

    async def loop_child(ctx):
        if ctx.process_id == "lc_t3a":
            a_running.set()
        elif ctx.process_id == "lc_t3b":
            b_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        try:
            async with ctx.parallel_group(survive_cancel=False) as g:
                await g.spawn(execute=loop_child, process_id="lc_t3a", name="LCA")
                await g.spawn(execute=loop_child, process_id="lc_t3b", name="LCB")
        except* ChildProcessFailed:
            pass

    parent_inst = TaskInstance(execute=parent, process_id="p_t3", name="P_T3")
    a_inst = TaskInstance(execute=loop_child, process_id="lc_t3a", name="LCA")
    b_inst = TaskInstance(execute=loop_child, process_id="lc_t3b", name="LCB")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, a_inst, b_inst])

    runner = asyncio.create_task(optio.launch_and_wait("p_t3", session_id=None))
    await a_running.wait()
    await b_running.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("lc_t3a")
    await asyncio.wait_for(runner, timeout=60.0)
    await optio.shutdown(grace_seconds=0.5)

    parent_proc = await _wait_terminal(mongo_db, prefix, "p_t3")
    a_proc = await _wait_terminal(mongo_db, prefix, "lc_t3a")
    b_proc = await _wait_terminal(mongo_db, prefix, "lc_t3b")
    assert parent_proc["status"]["state"] == "cancelled", parent_proc["status"]
    assert a_proc["status"]["state"] == "cancelled"
    assert b_proc["status"]["state"] == "cancelled"


async def test_nongroup_run_child_failure_does_not_set_parent_flag(mongo_db):
    """Non-group run_child with survive_failure=False. Child fails. Inside
    parent's `except ChildProcessFailed`, ctx.should_continue() == True;
    parent can spawn further work after catching."""
    prefix = "cfcd_ng1"
    observed = {}

    async def fail_child(ctx):
        await asyncio.sleep(0.02)
        raise RuntimeError("ng-boom")

    async def small_child(ctx):
        ctx.report_progress(100)

    async def parent(ctx):
        try:
            await ctx.run_child(execute=fail_child, process_id="ngfc1", name="NGFC1")
        except ChildProcessFailed:
            observed["should_continue_after_fail"] = ctx.should_continue()
            observed["flag_set_after_fail"] = ctx.cancellation_flag.is_set()
            outcome = await ctx.run_child(execute=small_child, process_id="ngsc1", name="NGSC1")
            observed["second_spawn_state"] = outcome.state

    parent_inst = TaskInstance(execute=parent, process_id="p_ng1", name="P_NG1")
    fc_inst = TaskInstance(execute=fail_child, process_id="ngfc1", name="NGFC1")
    sc_inst = TaskInstance(execute=small_child, process_id="ngsc1", name="NGSC1")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, fc_inst, sc_inst])

    try:
        await asyncio.wait_for(optio.launch_and_wait("p_ng1", session_id=None), timeout=60.0)
    finally:
        await optio.shutdown(grace_seconds=0.5)

    assert observed["should_continue_after_fail"] is True
    assert observed["flag_set_after_fail"] is False
    assert observed["second_spawn_state"] == "done"


async def test_nongroup_run_child_cancel_sets_parent_flag(mongo_db):
    """Non-group run_child with survive_cancel=False. Child externally
    cancelled. Parent's flag is set (cancellation cascade preserved)."""
    prefix = "cfcd_ng2"
    observed = {}
    child_started = asyncio.Event()

    async def loop_child(ctx):
        child_started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        outcome = await ctx.run_child(execute=loop_child, process_id="nglc1", name="NGLC1")
        observed["outcome_state"] = outcome.state
        observed["should_continue_after"] = ctx.should_continue()
        observed["flag_set_after"] = ctx.cancellation_flag.is_set()

    parent_inst = TaskInstance(execute=parent, process_id="p_ng2", name="P_NG2")
    lc_inst = TaskInstance(execute=loop_child, process_id="nglc1", name="NGLC1")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, lc_inst])

    runner = asyncio.create_task(optio.launch_and_wait("p_ng2", session_id=None))
    await child_started.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("nglc1")
    await asyncio.wait_for(runner, timeout=60.0)
    await optio.shutdown(grace_seconds=0.5)

    assert observed["outcome_state"] == "cancelled"
    assert observed["should_continue_after"] is False
    assert observed["flag_set_after"] is True


async def test_mixed_breach_failure_dominates_cancel(mongo_db):
    """Group with one failing child AND one externally-cancelled child.
    Failure dominates: parent's flag is NOT set; parent ends 'failed'
    (no catch in parent)."""
    prefix = "cfcd_mix"
    observed = {}
    a_running = asyncio.Event()
    b_running = asyncio.Event()

    async def loop_child(ctx):
        if ctx.process_id == "mixA":
            a_running.set()
        elif ctx.process_id == "mixB":
            b_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def slow_fail_child(ctx):
        await asyncio.sleep(0.20)
        raise RuntimeError("mix-fail")

    async def parent(ctx):
        try:
            async with ctx.parallel_group(
                survive_failure=False, survive_cancel=False,
            ) as g:
                await g.spawn(execute=loop_child, process_id="mixA", name="MIXA")
                await g.spawn(execute=loop_child, process_id="mixB", name="MIXB")
                await g.spawn(execute=slow_fail_child, process_id="mixF", name="MIXF")
        except* ChildProcessFailed:
            observed["should_continue"] = ctx.should_continue()
            observed["flag_set"] = ctx.cancellation_flag.is_set()
            raise

    parent_inst = TaskInstance(execute=parent, process_id="p_mix", name="P_MIX")
    a_inst = TaskInstance(execute=loop_child, process_id="mixA", name="MIXA")
    b_inst = TaskInstance(execute=loop_child, process_id="mixB", name="MIXB")
    f_inst = TaskInstance(execute=slow_fail_child, process_id="mixF", name="MIXF")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, a_inst, b_inst, f_inst])

    runner = asyncio.create_task(optio.launch_and_wait("p_mix", session_id=None))
    await a_running.wait()
    await b_running.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("mixA")
    await asyncio.wait_for(runner, timeout=60.0)
    await optio.shutdown(grace_seconds=0.5)

    assert observed["should_continue"] is True, observed
    assert observed["flag_set"] is False, observed

    parent_proc = await _wait_terminal(mongo_db, prefix, "p_mix")
    assert parent_proc["status"]["state"] == "failed"


async def test_excavator_reproducer_optio_row_correct(mongo_db):
    """End-to-end repro of the Excavator-style scenario in optio's own state
    machine: producer + consumer in parallel_group(survive_failure=False);
    consumer fails; parent's drive_sync catches ExceptionGroup, classifies
    using should_continue(), and returns. Parent's optio row must be 'done'
    (not 'cancelled')."""
    prefix = "cfcd_excavator"
    classification = {}

    async def producer(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.02)

    async def consumer(ctx):
        await asyncio.sleep(0.05)
        raise RuntimeError("consumer write failed")

    async def drive_sync(ctx):
        try:
            async with ctx.parallel_group(survive_failure=False) as g:
                await g.spawn(execute=producer, process_id="exc_prod", name="PROD")
                await g.spawn(execute=consumer, process_id="exc_cons", name="CONS")
        except* ChildProcessFailed:
            classification["should_continue"] = ctx.should_continue()
            if ctx.should_continue():
                classification["app_state"] = "failed"
            else:
                classification["app_state"] = "cancelled"

    parent_inst = TaskInstance(execute=drive_sync, process_id="exc_parent", name="ExcParent")
    prod_inst = TaskInstance(execute=producer, process_id="exc_prod", name="PROD")
    cons_inst = TaskInstance(execute=consumer, process_id="exc_cons", name="CONS")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, prod_inst, cons_inst])

    try:
        await asyncio.wait_for(optio.launch_and_wait("exc_parent", session_id=None), timeout=60.0)
    finally:
        await optio.shutdown(grace_seconds=0.5)

    assert classification["should_continue"] is True
    assert classification["app_state"] == "failed"

    parent_proc = await _wait_terminal(mongo_db, prefix, "exc_parent")
    cons_proc = await _wait_terminal(mongo_db, prefix, "exc_cons")
    prod_proc = await _wait_terminal(mongo_db, prefix, "exc_prod")
    assert parent_proc["status"]["state"] == "done", parent_proc["status"]
    assert cons_proc["status"]["state"] == "failed"
    assert prod_proc["status"]["state"] == "cancelled"
