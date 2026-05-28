# Distinguish child-failure from external cancel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the conflation where a child-process failure ends up setting the parent's `cancellation_flag` and Mongo row state to a `cancelled`-shaped value, so `ctx.should_continue()` and the parent's terminal state reliably distinguish "child failed" from "external cancel."

**Architecture:** Split the alpha-cascade callback into two variants — a failure variant that cancels only the parent's siblings (does not touch the parent's flag or row) and a cancel-cascade variant that calls today's `Optio.cancel(parent)` (sets parent flag, transitions parent row, cascades). `ParallelGroup` records the breach reason (failure dominates) and dispatches to the correct variant. Non-group `run_child` is split symmetrically.

**Tech Stack:** Python 3.11+, asyncio, motor (async MongoDB), pytest-asyncio.

**Spec:** `docs/2026-05-28-child-failure-cancel-distinction-design.md`

**Prerequisites:**
- MongoDB running locally on `mongodb://localhost:27017` (Docker per project convention).
- Repo venv at `/home/csillag/deai/optio/.venv` already has `optio-core` installed editable.

---

## File Map

**Modified:**
- `packages/optio-core/src/optio_core/executor.py` — add `notify_parent_failure` callback param; split `execute_child` abnormal-handling into failure vs cancel branches.
- `packages/optio-core/src/optio_core/context.py` — `ParallelGroup`: replace `self._failed: bool` with `self._breach_reason: Literal["failure", "cancel", None]`; per-child `_run` updates breach reason (failure dominates); `__aexit__` dispatches per reason; per-breach also fires sibling-only descent.
- `packages/optio-core/src/optio_core/lifecycle.py` — add `_cancel_active_children` helper (sibling-only descent extracted from `cancel()`); `cancel()` calls the helper; wire both callbacks into `Executor`.
- `packages/optio-core/tests/test_cancel_propagation.py` — tighten `{failed, cancelled}` assertions to exact values.

**Created:**
- `packages/optio-core/tests/test_child_failure_cancel_distinction.py` — new contract tests (in-flight signal + terminal-state contract).

**Unchanged (verified to still pass):**
- `packages/optio-core/tests/test_child_failure_structured.py`
- `packages/optio-core/tests/test_cancel_race_parent_overwrite.py`
- `packages/optio-core/tests/test_group_cancel.py`
- `packages/optio-core/tests/test_executor.py`

---

## Task 0: Feature branch and environment check

**Files:** none (git state only).

- [ ] **Step 1: Create feature branch in-place**

```bash
cd /home/csillag/deai/optio
git checkout -b fix-child-failure-cancel-distinction
git status
```

Expected: branch switched; `git status` clean.

- [ ] **Step 2: Verify MongoDB is reachable**

```bash
docker ps | grep -i mongo
```

Expected: a running MongoDB container. If none, start one:

```bash
docker run -d --name mongo-test -p 27017:27017 mongo:7
```

- [ ] **Step 3: Verify baseline test suite passes before changes**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_cancel_propagation.py tests/test_child_failure_structured.py tests/test_cancel_race_parent_overwrite.py -x -v 2>&1 | tail -30
```

Expected: all pass. Baseline established.

- [ ] **Step 4: No commit yet**

This task only sets up state. Code commits begin in Task 2.

---

## Task 1: Add new contract tests as RED baseline

These tests pin the contract the fix must implement. They are written **before** any implementation and must initially fail.

**Files:**
- Create: `packages/optio-core/tests/test_child_failure_cancel_distinction.py`

- [ ] **Step 1: Create the new test file with the full contract suite**

Write the file at `packages/optio-core/tests/test_child_failure_cancel_distinction.py`:

```python
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


async def _wait_terminal(mongo_db, prefix, process_id, timeout=5.0):
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
        await asyncio.wait_for(optio.launch_and_wait("p_isct1"), timeout=10.0)
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

    runner = asyncio.create_task(optio.launch_and_wait("p_isct2"))
    await parent_started.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("p_isct2")
    await asyncio.wait_for(runner, timeout=5.0)
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

    runner = asyncio.create_task(optio.launch_and_wait("p_isct3"))
    await a_running.wait()
    await b_running.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("lc3a")
    await asyncio.wait_for(runner, timeout=5.0)
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
        await asyncio.wait_for(optio.launch_and_wait("p_t1"), timeout=10.0)
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
        await asyncio.wait_for(optio.launch_and_wait("p_t2"), timeout=10.0)
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

    runner = asyncio.create_task(optio.launch_and_wait("p_t3"))
    await a_running.wait()
    await b_running.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("lc_t3a")
    await asyncio.wait_for(runner, timeout=5.0)
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
        await asyncio.wait_for(optio.launch_and_wait("p_ng1"), timeout=10.0)
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

    runner = asyncio.create_task(optio.launch_and_wait("p_ng2"))
    await child_started.wait()
    await asyncio.sleep(0.05)
    await optio.cancel("nglc1")
    await asyncio.wait_for(runner, timeout=5.0)
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
        # Sleeps long enough that the test can externally cancel mixA
        # before this raises; then raises to introduce a failure breach.
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

    runner = asyncio.create_task(optio.launch_and_wait("p_mix"))
    await a_running.wait()
    await b_running.wait()
    await asyncio.sleep(0.05)
    # Cancel mixA externally; mixF will fail ~0.15s later.
    await optio.cancel("mixA")
    await asyncio.wait_for(runner, timeout=5.0)
    await optio.shutdown(grace_seconds=0.5)

    # Failure dominates → parent's flag NOT set during the breach,
    # should_continue stays True.
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
            # Excavator's classifier: if should_continue() is True, the
            # group breached due to a child failure, not external cancel.
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
        await asyncio.wait_for(optio.launch_and_wait("exc_parent"), timeout=10.0)
    finally:
        await optio.shutdown(grace_seconds=0.5)

    # Classifier observed: should_continue=True → app classifies as failed.
    assert classification["should_continue"] is True
    assert classification["app_state"] == "failed"

    # And optio's own row also reflects truth (not 'cancelled').
    parent_proc = await _wait_terminal(mongo_db, prefix, "exc_parent")
    cons_proc = await _wait_terminal(mongo_db, prefix, "exc_cons")
    prod_proc = await _wait_terminal(mongo_db, prefix, "exc_prod")
    assert parent_proc["status"]["state"] == "done", parent_proc["status"]
    assert cons_proc["status"]["state"] == "failed"
    assert prod_proc["status"]["state"] == "cancelled"
```

- [ ] **Step 2: Run the new tests — most should FAIL**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_child_failure_cancel_distinction.py -v 2>&1 | tail -40
```

Expected (RED baseline):
- `test_should_continue_true_inside_except_when_child_fails_in_group` — FAIL (flag_set is True today)
- `test_excavator_reproducer_optio_row_correct` — FAIL (parent ends 'cancelled' today)
- `test_parent_terminal_done_when_child_fails_and_parent_catches_returns` — FAIL (parent ends 'cancelled')
- `test_nongroup_run_child_failure_does_not_set_parent_flag` — FAIL (flag is set today)
- `test_mixed_breach_failure_dominates_cancel` — FAIL (flag is set today)
- The "external-cancel" tests (cases 2, 3, 6, 9) may already PASS today (those cases work correctly today).

The exact failure list isn't critical — what matters is most of the failure-case tests fail and most of the cancel-cascade tests pass. Record the count.

- [ ] **Step 3: Commit the failing tests**

```bash
cd /home/csillag/deai/optio
git add packages/optio-core/tests/test_child_failure_cancel_distinction.py
git commit -m "test(optio-core): contract tests for child-failure / external-cancel distinction (RED)

Tests pin the in-flight signal contract (should_continue inside except)
and the terminal-state contract (parent never 'cancelled' due to child
failure). Most failure-case tests fail today; cancel-cascade tests
already pass. Implementation follows in subsequent commits."
```

---

## Task 2: Refactor `Optio.cancel`'s downward propagation into a shared helper

Pure refactor. Extract the recursive-descent step out of `cancel()` so it can be reused by the new failure-breach callback. No behavior change.

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:413-536` and surrounding area.

- [ ] **Step 1: Add `_cancel_active_children` helper to `Optio`**

Insert this method on the `Optio` class in `lifecycle.py`, **above** `cancel`:

```python
async def _cancel_active_children(
    self,
    parent_process_id: str,
    *,
    inherit_deadline: float | None = None,
) -> None:
    """Cancel the active direct children of `parent_process_id` cooperatively,
    using a shared deadline budget. Does NOT cancel the parent itself.

    Honors the parent task's `auto_cancel_children` setting (assumes True
    if the task is not in the registry — fail-safe toward propagation).

    Used by:
      - `cancel()` for its downward-propagation step.
      - The alpha-cascade failure-breach callback (parallel_group failure
        breach, non-group run_child with survive_failure=False).

    Safe to invoke multiple times concurrently: `cancel()` on an
    already-cancelled / not-cancellable child is a no-op.
    """
    proc = await self._resolve(parent_process_id)
    if proc is None:
        return
    task = self._executor._task_registry.get(proc["processId"])
    auto = task.auto_cancel_children if task is not None else True
    if not auto:
        return
    effective_deadline = (
        inherit_deadline
        if inherit_deadline is not None
        else time.monotonic() + self._config.cancel_grace_seconds
    )
    from optio_core.store import list_direct_children
    children = await list_direct_children(
        self._config.mongo_db, self._config.prefix,
        proc["_id"], states=ACTIVE_STATES,
    )
    if not children:
        return
    _trace(
        "CANCEL-TRACE %s: propagating to children=%s",
        proc["processId"], [c["processId"] for c in children],
    )
    await asyncio.gather(
        *(
            self.cancel(str(c["_id"]), inherit_deadline=effective_deadline)
            for c in children
        ),
        return_exceptions=True,
    )
```

- [ ] **Step 2: Replace the inline descent in `cancel()` with a call to the helper**

In `lifecycle.py`, find the block currently at lines 508-530:

```python
# Downward propagation: recurse over active direct children unless
# this process's TaskInstance opts out. Unknown task → assume True
# (fail safe toward propagation, not orphan).
task = self._executor._task_registry.get(proc["processId"])
auto = task.auto_cancel_children if task is not None else True
if auto:
    from optio_core.store import list_direct_children
    children = await list_direct_children(
        self._config.mongo_db, self._config.prefix,
        proc["_id"], states=ACTIVE_STATES,
    )
    if children:
        _trace(
            "CANCEL-TRACE %s: propagating to children=%s",
            process_id, [c["processId"] for c in children],
        )
        await asyncio.gather(
            *(
                self.cancel(str(c["_id"]), inherit_deadline=effective_deadline)
                for c in children
            ),
            return_exceptions=True,
        )
```

Replace with:

```python
# Downward propagation: delegate to the shared helper. The helper
# honors auto_cancel_children and shares the deadline budget.
await self._cancel_active_children(
    str(proc["_id"]),
    inherit_deadline=effective_deadline,
)
```

- [ ] **Step 3: Run the full test suite — baseline must still pass**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_cancel_propagation.py tests/test_child_failure_structured.py tests/test_cancel_race_parent_overwrite.py tests/test_group_cancel.py -x -v 2>&1 | tail -30
```

Expected: identical results to Task 0 baseline. No regressions.

The new `test_child_failure_cancel_distinction.py` tests still fail at the same rate as Task 1.

- [ ] **Step 4: Commit the refactor**

```bash
cd /home/csillag/deai/optio
git add packages/optio-core/src/optio_core/lifecycle.py
git commit -m "refactor(optio-core): extract _cancel_active_children helper from Optio.cancel

Pure refactor. Pulls the downward-propagation block of cancel() into a
reusable method so the alpha-cascade callback can perform sibling-only
cancellation without also cancelling the parent. No behavior change yet."
```

---

## Task 3: Add the failure-variant callback to `Executor` and wire it

Plumbs the new callback without changing behavior yet. Both callbacks are wired; only existing `notify_parent_abnormal` call sites use them.

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:52-66`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:172-176`

- [ ] **Step 1: Add the `notify_parent_failure` parameter to `Executor.__init__`**

In `executor.py`, change the `__init__` signature and body:

```python
def __init__(
    self,
    db: AsyncIOMotorDatabase,
    prefix: str,
    services: dict[str, Any],
    optio: "Optio | None" = None,
    notify_parent_abnormal: Callable[..., Awaitable[Any]] | None = None,
    notify_parent_failure: Callable[..., Awaitable[Any]] | None = None,
):
    self._db = db
    self._prefix = prefix
    self._services = services
    self._optio = optio
    self._notify_parent_abnormal = notify_parent_abnormal
    self._notify_parent_failure = notify_parent_failure
    self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
    self._running_tasks: dict[ObjectId, asyncio.Task] = {}
    self._task_registry: dict[str, TaskInstance] = {}
```

`notify_parent_abnormal` is kept (called by parallel_group cancel-cascade and non-group cancel-cascade). `notify_parent_failure` is new (called by failure-breach paths).

- [ ] **Step 2: Wire both callbacks in `Optio.init`**

In `lifecycle.py:172-176`, change:

```python
self._executor = Executor(
    mongo_db, prefix, services, optio=self,
    notify_parent_abnormal=self.cancel,
    notify_parent_failure=self._cancel_active_children,
)
```

- [ ] **Step 3: Run the full test suite — no behavior change expected**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_cancel_propagation.py tests/test_child_failure_structured.py tests/test_cancel_race_parent_overwrite.py tests/test_group_cancel.py tests/test_executor.py -x -v 2>&1 | tail -30
```

Expected: same baseline results. Wiring exists but the failure callback has no call site yet.

- [ ] **Step 4: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/src/optio_core/lifecycle.py
git commit -m "feat(optio-core): plumb notify_parent_failure callback through Executor

Wires Optio._cancel_active_children as a second alpha-cascade callback
alongside the existing notify_parent_abnormal. No call sites use it yet;
subsequent commits split the parallel_group and non-group run_child
abnormal-handling paths to dispatch by breach reason."
```

---

## Task 4: `ParallelGroup` — track breach reason, dispatch by reason

Behavior change for groups. After this task, the group-failure tests in `test_child_failure_cancel_distinction.py` should start passing.

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py:577-698`

- [ ] **Step 1: Replace `self._failed: bool` with `self._breach_reason`**

In `context.py:580-597`, change `ParallelGroup.__init__`:

```python
def __init__(
    self,
    ctx: ProcessContext,
    max_concurrency: int,
    survive_failure: bool,
    survive_cancel: bool,
    on_child_progress: Callable | None = None,
):
    self._ctx = ctx
    self._max_concurrency = max_concurrency
    self._survive_failure = survive_failure
    self._survive_cancel = survive_cancel
    self._semaphore = asyncio.Semaphore(max_concurrency)
    self._tasks: list[asyncio.Task] = []
    self._results: list[ChildResult] = []
    # Breach reason: None until a child's outcome breaches the group's
    # survive_* settings. "failure" once any non-survived failed outcome
    # is seen; "cancel" only if no failure has occurred. Failure dominates.
    self._breach_reason: Literal["failure", "cancel", None] = None
    if on_child_progress is not None:
        self._ctx._set_child_callback(on_child_progress)
```

Add the `Literal` import at the top of `context.py` if not already present:

```python
from typing import Literal
```

(Check whether `Literal` is already imported — if so, skip this addition.)

- [ ] **Step 2: Update per-child breach detection in `_run`**

Find the block currently at `context.py:654-672` inside the `_run` closure (the existing block reads):

```python
breached = False
if outcome.state == "failed" and not self._survive_failure:
    self._failed = True
    breached = True
if outcome.state == "cancelled" and not self._survive_cancel:
    self._failed = True
    breached = True
# alpha at group level: when group's own survive_*
# aggregate is breached, invoke notify_parent_abnormal so
# the parent's cancel propagation reaches sibling spawns.
# spawn's run_child uses survive_*=True per-child so the
# execute_child alpha path does not fire for individual
# children of a parallel_group.
if breached:
    executor = self._ctx._executor
    if executor is not None and executor._notify_parent_abnormal is not None:
        asyncio.create_task(
            executor._notify_parent_abnormal(self._ctx.process_id)
        )
```

Replace with:

```python
breached = False
if outcome.state == "failed" and not self._survive_failure:
    # Failure dominates: any non-survived failure pins the breach
    # reason to "failure", even if a prior cancellation breach has
    # already set it to "cancel".
    self._breach_reason = "failure"
    breached = True
if outcome.state == "cancelled" and not self._survive_cancel:
    # Only escalate from None to "cancel". Do not downgrade from
    # "failure".
    if self._breach_reason != "failure":
        self._breach_reason = "cancel"
    breached = True
# Per-breach: cancel still-running siblings cooperatively
# (sibling-only descent, no parent flag set). This is fired on
# every breach event regardless of reason — both reasons need
# siblings cancelled, and the helper is idempotent. The decision
# of whether to ALSO cascade cancel(parent) upward is deferred to
# __aexit__ based on the final breach reason.
if breached:
    executor = self._ctx._executor
    if executor is not None and executor._notify_parent_failure is not None:
        asyncio.create_task(
            executor._notify_parent_failure(self._ctx.process_id)
        )
```

- [ ] **Step 3: Update `__aexit__` to dispatch by breach reason**

Find the existing `__aexit__` at `context.py:682-698`:

```python
async def __aexit__(self, exc_type, exc_val, exc_tb):
    if self._tasks:
        await asyncio.gather(*self._tasks, return_exceptions=True)
    if self._failed:
        failed = [r for r in self._results if r.state != "done"]
        failures = [
            ChildProcessFailed(
                r.name,
                r.process_id,
                r.original_exception
                if r.original_exception is not None
                else RuntimeError(f"child {r.state}"),
            )
            for r in failed
        ]
        raise ExceptionGroup("Parallel group failed", failures)
    return False
```

Replace with:

```python
async def __aexit__(self, exc_type, exc_val, exc_tb):
    if self._tasks:
        await asyncio.gather(*self._tasks, return_exceptions=True)
    if self._breach_reason == "cancel":
        # Cancellation cascade upward. Set parent's flag synchronously
        # so the user code observes should_continue() == False
        # immediately upon re-entry from the `async with`. Also schedule
        # Optio.cancel(parent) so the parent's Mongo row transitions
        # through cancel_requested/cancelling correctly.
        self._ctx._cancellation_flag.set()
        executor = self._ctx._executor
        if executor is not None and executor._notify_parent_abnormal is not None:
            asyncio.create_task(
                executor._notify_parent_abnormal(self._ctx.process_id)
            )
    if self._breach_reason is not None:
        failed = [r for r in self._results if r.state != "done"]
        failures = [
            ChildProcessFailed(
                r.name,
                r.process_id,
                r.original_exception
                if r.original_exception is not None
                else RuntimeError(f"child {r.state}"),
            )
            for r in failed
        ]
        raise ExceptionGroup("Parallel group failed", failures)
    return False
```

- [ ] **Step 4: Run the contract tests — group-failure cases should pass**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_child_failure_cancel_distinction.py -v 2>&1 | tail -40
```

Expected:
- `test_should_continue_true_inside_except_when_child_fails_in_group` — PASS
- `test_should_continue_false_when_parent_externally_cancelled` — PASS
- `test_should_continue_false_when_child_cancelled_externally_no_survive` — PASS
- `test_parent_terminal_done_when_child_fails_and_parent_catches_returns` — PASS
- `test_parent_terminal_failed_when_child_fails_and_parent_reraises` — PASS
- `test_parent_terminal_cancelled_when_child_cancel_cascades_and_parent_catches_returns` — PASS
- `test_mixed_breach_failure_dominates_cancel` — PASS
- `test_excavator_reproducer_optio_row_correct` — PASS
- The two non-group tests (`test_nongroup_run_child_failure_does_not_set_parent_flag`, `test_nongroup_run_child_cancel_sets_parent_flag`) — STILL FAIL (Task 5 handles non-group path).

- [ ] **Step 5: Run the existing test suite — check for regressions**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_cancel_propagation.py tests/test_child_failure_structured.py tests/test_cancel_race_parent_overwrite.py tests/test_group_cancel.py tests/test_parallel.py -v 2>&1 | tail -50
```

Expected: still passes. The `{failed, cancelled}` permissive assertions still allow whichever value the parent ends up at; Task 6 will tighten them.

If any test fails, halt and diagnose before committing. Likely culprits: a test that relied on the parent's row state being `cancelling` mid-flight (unlikely; not observed in inventory), or a deterministic-timing assumption broken by the change in callback dispatch.

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-core/src/optio_core/context.py
git commit -m "fix(optio-core): parallel_group dispatches alpha-cascade by breach reason

ParallelGroup tracks _breach_reason ('failure' | 'cancel' | None) instead
of a collapsed _failed bool. Failure dominates: any non-survived failed
outcome pins the breach to 'failure', regardless of any concurrent
cancellation.

On per-child breach, fires notify_parent_failure (sibling-only descent),
which is safe to invoke for both reasons. In __aexit__, if the final
breach reason is 'cancel', additionally sets the parent's
cancellation_flag synchronously and fires notify_parent_abnormal
(Optio.cancel(parent)) to propagate the cancellation upward through the
parent's row state.

Result: ctx.should_continue() inside the parent's `except ExceptionGroup`
is now a reliable discriminator. It returns True when a child failed
and False when an external cancel (on self, ancestor, or non-surviving
descendant) is the cause."
```

---

## Task 5: Split `execute_child` abnormal-handling by breach reason

Behavior change for non-group `run_child`. After this task, the non-group contract tests should pass.

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:340-371`

- [ ] **Step 1: Replace the abnormal-handling block in `execute_child`**

Find the existing block at `executor.py:340-371`:

```python
end_state, exc = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)

if parent_ctx._on_child_progress is not None:
    parent_ctx._notify_child_state_change(process_id, end_state)

# alpha: abnormal child terminal -> trigger parent's downward
# propagation via the lifecycle callback. The callback is
# idempotent for an already-cancel_requested parent.
abnormal = (
    (end_state == "cancelled" and not survive_cancel)
    or (end_state == "failed" and not survive_failure)
)
if abnormal and self._notify_parent_abnormal is not None:
    _trace(
        "CANCEL-TRACE %s: abnormal child %s (%s) → scheduling notify_parent_abnormal(parent=%s)",
        process_id, name, end_state, parent_ctx.process_id,
    )
    asyncio.create_task(
        self._notify_parent_abnormal(parent_ctx.process_id)
    )

if end_state == "failed" and not survive_failure:
    if exc is None:
        exc = RuntimeError(f"Child process '{name}' failed")
    raise ChildProcessFailed(name, process_id, exc) from exc
if end_state == "cancelled" and not survive_cancel:
    parent_ctx._cancellation_flag.set()

return ChildOutcome(
    state=end_state,
    original_exception=exc if end_state == "failed" else None,
)
```

Replace with:

```python
end_state, exc = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)

if parent_ctx._on_child_progress is not None:
    parent_ctx._notify_child_state_change(process_id, end_state)

abnormal_failed = end_state == "failed" and not survive_failure
abnormal_cancelled = end_state == "cancelled" and not survive_cancel

# Failure breach: cancel parent's OTHER active concurrent children only.
# Do NOT set parent's flag, do NOT change parent's row state — the
# ChildProcessFailed raise below communicates the failure to parent's
# user code, and the parent's terminal state is then determined by
# whether the user catches+returns or re-raises.
if abnormal_failed:
    if self._notify_parent_failure is not None:
        _trace(
            "CANCEL-TRACE %s: failed child %s → scheduling notify_parent_failure(parent=%s)",
            process_id, name, parent_ctx.process_id,
        )
        asyncio.create_task(
            self._notify_parent_failure(parent_ctx.process_id)
        )

# Cancellation breach: cascade upward. Set parent's flag synchronously
# so subsequent operations in the parent's user code observe
# should_continue() == False, and schedule Optio.cancel(parent) so the
# parent's row transitions through cancel_requested/cancelling.
if abnormal_cancelled:
    parent_ctx._cancellation_flag.set()
    if self._notify_parent_abnormal is not None:
        _trace(
            "CANCEL-TRACE %s: cancelled child %s → scheduling notify_parent_abnormal(parent=%s)",
            process_id, name, parent_ctx.process_id,
        )
        asyncio.create_task(
            self._notify_parent_abnormal(parent_ctx.process_id)
        )

if abnormal_failed:
    if exc is None:
        exc = RuntimeError(f"Child process '{name}' failed")
    raise ChildProcessFailed(name, process_id, exc) from exc

return ChildOutcome(
    state=end_state,
    original_exception=exc if end_state == "failed" else None,
)
```

- [ ] **Step 2: Run contract tests — all should pass now**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_child_failure_cancel_distinction.py -v 2>&1 | tail -40
```

Expected: all tests pass.

- [ ] **Step 3: Run the existing test suite — check for regressions**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_cancel_propagation.py tests/test_child_failure_structured.py tests/test_cancel_race_parent_overwrite.py tests/test_group_cancel.py tests/test_parallel.py tests/test_executor.py -v 2>&1 | tail -50
```

Expected: still passes.

- [ ] **Step 4: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-core/src/optio_core/executor.py
git commit -m "fix(optio-core): execute_child dispatches alpha-cascade by breach reason

Non-group run_child path is split:
- Failed-not-survive: fires notify_parent_failure (sibling-only descent).
  Does NOT set parent's cancellation_flag. The ChildProcessFailed raise
  carries the failure signal; the parent's terminal state is determined
  by whether user code re-raises (-> failed) or catches+returns (-> done).
- Cancelled-not-survive: keeps existing line-366 flag-set AND fires
  notify_parent_abnormal (Optio.cancel(parent)) so the cancellation
  cascade propagates upward including the parent's row transitions.

User code catching ChildProcessFailed can now continue to spawn further
work — the implicit guard via parent's flag is no longer set on failure.
This is the intended breaking change (documented in the spec)."
```

---

## Task 6: Tighten existing permissive test assertions

Replace `state in {"failed", "cancelled"}` with exact expected values now that the contract is deterministic.

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Tighten `test_parallel_group_fail_fast_under_alpha`**

In `tests/test_cancel_propagation.py`, find lines 405-409:

```python
b_proc = await get_process_by_process_id(mongo_db, prefix, "b")
c_proc = await get_process_by_process_id(mongo_db, prefix, "c")
a_proc = await get_process_by_process_id(mongo_db, prefix, "a")
assert b_proc["status"]["state"] == "failed"
assert c_proc["status"]["state"] == "cancelled"
assert a_proc["status"]["state"] in {"failed", "cancelled"}
```

The parent task `parent(ctx)` does not catch the ExceptionGroup; the failure breaches the group; parent re-raises. After the fix, parent ends `failed` deterministically. Replace the last line:

```python
assert b_proc["status"]["state"] == "failed"
assert c_proc["status"]["state"] == "cancelled"
assert a_proc["status"]["state"] == "failed"
```

- [ ] **Step 2: Tighten `test_parallel_group_cancel_propagates_to_siblings`**

In `tests/test_cancel_propagation.py`, find the assertion around line 358-362:

```python
# Parent ends 'failed' because parallel_group(survive_cancel=False)
# raises ExceptionGroup[ChildProcessFailed] when any child cancels;
# the exception overwrites the prior cancel_requested state. Either
# terminal is acceptable.
assert a_proc["status"]["state"] in {"failed", "cancelled"}
```

The parent task does not catch the ExceptionGroup, so it re-raises. After the fix, the cancellation cascades upward: parent's flag is set, parent's row goes `cancel_requested → cancelling`; then the ExceptionGroup re-raises out and the executor's `except Exception` arm writes `failed`. Net: parent ends `failed` deterministically. Replace the comment + assertion:

```python
# Parent does not catch the ExceptionGroup; it re-raises out and the
# executor's `except Exception` arm writes 'failed'. The cancellation
# cascade still set the parent's flag and transitioned its row through
# cancel_requested/cancelling; the failed write overwrites those.
assert a_proc["status"]["state"] == "failed"
```

- [ ] **Step 3: Run the modified test file**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest tests/test_cancel_propagation.py -v 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 4: Run the full optio-core suite as a final regression check**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest -x -v 2>&1 | tail -50
```

Expected: all pass. If something unrelated fails (e.g., test_no_redis, test_migrations) and the failure pre-existed before this branch, it is out of scope; verify against the baseline from Task 0 Step 3.

- [ ] **Step 5: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): tighten parent terminal-state assertions to exact values

Replaces 'state in {failed, cancelled}' with the deterministic value the
fix guarantees. The two affected tests had the parent re-raise the
ExceptionGroup (no try/except), so the parent now ends exactly 'failed'."
```

---

## Task 7: Update package AGENTS.md if needed

The spec changes lifecycle internals and the executor's callback shape. Public API (`ctx.parallel_group`, `ctx.run_child`, `Optio.cancel`) is unchanged.

**Files:**
- Read: `packages/optio-core/AGENTS.md` (verify nothing documents the conflated behavior)
- Modify (if needed): `packages/optio-core/AGENTS.md`
- Read: `AGENTS.md` (root — check for cross-package reference)
- Modify (if needed): `AGENTS.md`

- [ ] **Step 1: Audit `packages/optio-core/AGENTS.md`**

```bash
cat /home/csillag/deai/optio/packages/optio-core/AGENTS.md | grep -nE "should_continue|cancellation_flag|notify_parent_abnormal|parallel_group" || echo "no matches"
```

If `AGENTS.md` documents `should_continue()` semantics, `cancellation_flag` semantics, the alpha-cascade mechanism, or `parallel_group` behavior, update those sections to reflect the new contract:

- `should_continue()` returns `False` if and only if the process has been cancelled (external on self/ancestor, or cancellation cascade from non-surviving descendant). It does **not** return `False` because a child failed.
- `parallel_group(survive_failure=False)` with a failed child causes the group to raise `ExceptionGroup[ChildProcessFailed]` and the parent's user code is responsible for re-raising (parent → `failed`) or catching+returning (parent → `done`).
- `parallel_group(survive_cancel=False)` with an externally cancelled descendant causes the cancellation to cascade upward through the parent's flag and row state.

- [ ] **Step 2: Audit root `AGENTS.md`**

```bash
grep -nE "should_continue|cancellation_flag|parallel_group|alpha.cascade" /home/csillag/deai/optio/AGENTS.md || echo "no matches"
```

If matches exist, update consistently.

- [ ] **Step 3: Commit (only if AGENTS.md actually changed)**

```bash
cd /home/csillag/deai/optio
git status
git diff --stat
# If there are AGENTS.md changes:
git add packages/optio-core/AGENTS.md AGENTS.md
git commit -m "docs(optio-core): clarify should_continue / parallel_group contract in AGENTS.md"
```

If nothing changed, skip the commit.

---

## Task 8: Final regression sweep and changelog note

- [ ] **Step 1: Run the entire optio-core test suite**

```bash
cd /home/csillag/deai/optio/packages/optio-core
/home/csillag/deai/optio/.venv/bin/pytest -v 2>&1 | tail -80
```

Expected: all pass (or only the same flake/skip as baseline at Task 0 Step 3).

- [ ] **Step 2: Verify dependent tests across the workspace are not broken**

The fix is internal to optio-core. Other packages consume it via the public API which is unchanged. Run a broad sweep to confirm:

```bash
cd /home/csillag/deai/optio
OPTIO_SKIP_PREFLIGHT_TESTS=1 pnpm -r test 2>&1 | tail -60 || true
```

Expected: pre-existing flake on `optio-api` WS preflight is suppressed by the env var (per project memory). No new failures introduced by this branch.

If a non-flaky test fails downstream, investigate — most likely a consumer's test was implicitly relying on the buggy parent-flag-set-on-child-failure behavior.

- [ ] **Step 3: Final commit if any small fixups were made**

If everything is green, no extra commit is needed.

- [ ] **Step 4: Branch summary**

```bash
cd /home/csillag/deai/optio
git log --oneline main..HEAD
```

Expected commit chain:
```
<hash> test(optio-core): tighten parent terminal-state assertions to exact values
<hash> fix(optio-core): execute_child dispatches alpha-cascade by breach reason
<hash> fix(optio-core): parallel_group dispatches alpha-cascade by breach reason
<hash> feat(optio-core): plumb notify_parent_failure callback through Executor
<hash> refactor(optio-core): extract _cancel_active_children helper from Optio.cancel
<hash> test(optio-core): contract tests for child-failure / external-cancel distinction (RED)
```

(Plus possibly a docs commit from Task 7.)

The spec commit (`2d7a7bb`) precedes the branch and lives on `main`.

---

## Notes for the executor

- **No worktree.** Per project convention, implementation happens on a feature branch created in-place. The `using-git-worktrees` skill is not invoked.
- **MongoDB is required** for the test suite. Use Docker; do not start a local `mongod`.
- **No `pip install -e` against global Python.** The repo venv at `/home/csillag/deai/optio/.venv` is already configured editable for `optio-core`.
- **No `Co-Authored-By`** in commit messages.
- **Flat `docs/`** layout — already used for the spec.
- **No `npx`** — N/A for Python work in this plan.
