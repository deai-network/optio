# `group_cancel` / `group_cancel_and_wait` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new public methods on `Optio` — `group_cancel(metadata_filter, block_new_launches=False)` (fire-and-forget) and `group_cancel_and_wait(metadata_filter, block_new_launches=False)` (waits for terminal state) — that cancel every active process matching a metadata filter, with an optional launch guard for the duration of the call.

**Architecture:** Both public methods compose on a private `_group_cancel_issue` helper that does snapshot + parallel cancel + optional leak sweep. The waiter additionally runs a forward-walking pointer wait loop. Guard lifecycle is owned by each public method via `AsyncExitStack`. Pure orchestration over existing primitives — `list_processes`, `cancel`, `get_process`, `block_launches`, the supervisor, `OptioConfig.cancel_grace_seconds`. No new internal state, no new wire-protocol shapes.

**Tech Stack:** Python 3.11+, asyncio, motor (MongoDB), pytest + pytest-asyncio. All edits live in `packages/optio-core/`.

**Spec:** `docs/2026-04-30-group-cancel-design.md`. Read it first.

**Branch:** `csillag/group-task-termination`. Already created and checked out.

---

## File Structure

**Modify:**
- `packages/optio-core/src/optio_core/lifecycle.py` — add `_group_cancel_issue`, `group_cancel`, `group_cancel_and_wait` methods on the `Optio` class, alongside the existing `cancel` / `cancel_and_wait`.
- `packages/optio-core/src/optio_core/__init__.py` — export `group_cancel` and `group_cancel_and_wait` from the module-level singleton.

**Create:**
- `packages/optio-core/tests/test_group_cancel.py` — all tests for the new pair.

No file splits; everything lives next to the existing single-pid cancel methods.

---

## Standard Patterns Used Throughout

These patterns appear in many tests below — included once here so each task can reference them without repeating:

**Pytest async marker (top of every test file):**
```python
pytestmark = pytest.mark.asyncio
```

**Boilerplate for spinning up an Optio with running tasks:**
```python
import asyncio
import pytest
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio

async def _start_optio(mongo_db, prefix, tasks, cancel_grace_seconds=2.0):
    """Helper: init an Optio with the given tasks, return (optio, run_task)."""
    async def gen(_s, _f):
        return list(tasks)
    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen,
        cancel_grace_seconds=cancel_grace_seconds,
    )
    run_task = asyncio.create_task(optio.run())
    return optio, run_task

async def _stop_optio(optio, run_task):
    await optio.shutdown()
    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass
```

**Tests should `await asyncio.sleep(0.05)` after launch** before asserting state, to give the executor a chance to flip the process from `scheduled` → `running`.

---

## Task 1: Stubs + filter validation

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` — add new methods alongside `cancel_and_wait` (around line 290).
- Modify: `packages/optio-core/src/optio_core/__init__.py` — export the two new methods.
- Create: `packages/optio-core/tests/test_group_cancel.py` — initial test file with one test.

- [ ] **Step 1.1: Write the failing test for filter validation**

Create `packages/optio-core/tests/test_group_cancel.py` with:

```python
"""Tests for group_cancel / group_cancel_and_wait.

Spec: docs/2026-04-30-group-cancel-design.md
"""
import asyncio
import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


# ---------- Filter validation (no Mongo needed) ----------

@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel(bad_filter, block_new_launches=block_new_launches)


@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_and_wait_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel_and_wait raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel_and_wait(bad_filter, block_new_launches=block_new_launches)
```

- [ ] **Step 1.2: Run the test — should fail with AttributeError**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`
Expected: FAIL with `AttributeError: 'Optio' object has no attribute 'group_cancel'`.

- [ ] **Step 1.3: Add the stubs**

In `packages/optio-core/src/optio_core/lifecycle.py`, add three methods on the `Optio` class right after `cancel_and_wait` (around line 290, before `dismiss`):

```python
    async def _group_cancel_issue(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool,
    ) -> list[str]:
        """Snapshot, cancel, optionally leak-sweep. Returns the list of
        process_ids that were cancelled (snapshot + leaked).

        Caller is responsible for the launch guard's AsyncExitStack —
        this helper assumes the guard is already active when called with
        block_new_launches=True.
        """
        raise NotImplementedError  # filled in later tasks

    async def group_cancel(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter`. Does NOT
        wait for terminal state. See docs/2026-04-30-group-cancel-design.md."""
        if not metadata_filter:
            raise ValueError(
                "group_cancel requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        raise NotImplementedError

    async def group_cancel_and_wait(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter` and wait
        for all of them to reach a terminal state. See
        docs/2026-04-30-group-cancel-design.md.

        Do not call from inside a task whose metadata matches the filter —
        use group_cancel for self-cancel.
        """
        if not metadata_filter:
            raise ValueError(
                "group_cancel_and_wait requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        raise NotImplementedError
```

In `packages/optio-core/src/optio_core/__init__.py`, after the existing `block_launches` line, add:

```python
group_cancel = _instance.group_cancel
group_cancel_and_wait = _instance.group_cancel_and_wait
```

And add the names to `__all__`:

```python
__all__ = [
    "TaskInstance", "ChildResult", "LaunchBlocked",
    "init", "run", "shutdown", "on_command",
    "adhoc_define", "adhoc_delete",
    "launch", "launch_and_wait", "cancel", "dismiss", "resync",
    "get_process", "list_processes",
    "block_launches",
    "group_cancel", "group_cancel_and_wait",
]
```

- [ ] **Step 1.4: Re-run the test — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`
Expected: 8 PASS (2 bad_filter values × 2 block_new_launches values × 2 methods).

- [ ] **Step 1.5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/src/optio_core/__init__.py \
        packages/optio-core/tests/test_group_cancel.py
git commit -m "feat(optio-core): group_cancel pair stubs + filter validation"
```

---

## Task 2: Snapshot + parallel cancel (the issue path)

This task wires up `_group_cancel_issue` and makes `group_cancel` work for the basic happy path. The waiter still raises NotImplementedError; we'll fix it in Task 4.

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` — implement `_group_cancel_issue` (snapshot + parallel cancel; leak sweep stays a no-op for now). Implement `group_cancel` body (no guard yet, no leak sweep).
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the out-of-scope test.

- [ ] **Step 2.1: Write the failing test for out-of-scope tasks untouched**

Append to `packages/optio-core/tests/test_group_cancel.py`:

```python
# ---------- Snapshot + parallel cancel ----------

async def test_group_cancel_only_cancels_in_scope(mongo_db):
    """group_cancel cancels tasks that match the filter and leaves others alone."""
    started_a = asyncio.Event()
    started_b = asyncio.Event()
    release_b = asyncio.Event()

    async def cooperative_a(ctx):
        started_a.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    async def cooperative_b(ctx):  # noqa: ARG001
        started_b.set()
        await release_b.wait()

    task_a = TaskInstance(
        process_id="p.a", name="A", params={}, execute=cooperative_a,
        metadata={"team": "alpha"},
    )
    task_b = TaskInstance(
        process_id="p.b", name="B", params={}, execute=cooperative_b,
        metadata={"team": "beta"},
    )

    optio, run_task = await _start_optio(mongo_db, "gc_scope", [task_a, task_b])
    try:
        await optio.launch("p.a")
        await optio.launch("p.b")
        await started_a.wait()
        await started_b.wait()

        await optio.group_cancel({"team": "alpha"})

        # Wait long enough for cooperative_a to observe and unwind.
        await asyncio.sleep(0.25)

        proc_a = await optio.get_process("p.a")
        proc_b = await optio.get_process("p.b")
        assert proc_a["status"]["state"] == "cancelled"
        assert proc_b["status"]["state"] == "running"

        release_b.set()
        await asyncio.sleep(0.1)
    finally:
        await _stop_optio(optio, run_task)
```

Also paste the `_start_optio` / `_stop_optio` helpers from the **Standard Patterns** section above just below the imports of `test_group_cancel.py`.

- [ ] **Step 2.2: Run — should fail with NotImplementedError**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_group_cancel_only_cancels_in_scope -v`
Expected: FAIL — `NotImplementedError` from the stub.

- [ ] **Step 2.3: Implement `_group_cancel_issue` (snapshot + parallel cancel only)**

Replace the `_group_cancel_issue` stub in `lifecycle.py` with:

```python
    async def _group_cancel_issue(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool,
    ) -> list[str]:
        """Snapshot, cancel, optionally leak-sweep. Returns the list of
        process_ids that were cancelled (snapshot + leaked)."""
        # 1. Snapshot active processes matching the filter.
        procs = await self.list_processes(metadata=metadata_filter)
        active = [p for p in procs if p["status"]["state"] in ACTIVE_STATES]

        # 2. Issue cancellations in parallel. cancel() is non-blocking
        #    and idempotent.
        if active:
            await asyncio.gather(
                *(self.cancel(p["processId"]) for p in active)
            )

        pending_ids = [p["processId"] for p in active]

        # 3. Leak sweep — implemented in a later task.
        return pending_ids
```

Note: `asyncio` is already imported at the top of `lifecycle.py`. `ACTIVE_STATES` is already imported from `state_machine`.

Replace the `group_cancel` stub with a thin wrapper that calls the helper:

```python
    async def group_cancel(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter`. Does NOT
        wait for terminal state. See docs/2026-04-30-group-cancel-design.md."""
        if not metadata_filter:
            raise ValueError(
                "group_cancel requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        # Guard not yet wired up — added in a later task.
        await self._group_cancel_issue(metadata_filter, block_new_launches)
```

- [ ] **Step 2.4: Run the test — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`
Expected: 9 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_group_cancel.py
git commit -m "feat(optio-core): group_cancel snapshot + parallel cancel"
```

---

## Task 3: `group_cancel` returns before terminal (fire-and-forget semantics)

Verifies the contract: `group_cancel` returns once cancels are issued, not once tasks are terminal.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 3.1: Write the test**

Append to `test_group_cancel.py`:

```python
async def test_group_cancel_returns_before_terminal(mongo_db):
    """group_cancel returns once cancels are issued — not once tasks are terminal.

    The cooperative task here observes the flag only after a delay; if
    group_cancel waited for terminal state, the call would block for that
    delay. Since it doesn't, the call returns quickly and the task is
    still in cancel_requested / cancelling / running when we check.
    """
    started = asyncio.Event()

    async def slow_cooperative(ctx):
        started.set()
        # Don't check the flag until well after group_cancel has had a
        # chance to issue and return.
        await asyncio.sleep(0.5)
        if ctx.cancellation_flag.is_set():
            return

    task = TaskInstance(
        process_id="p.slow", name="Slow", params={}, execute=slow_cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, "gc_fire", [task])
    try:
        await optio.launch("p.slow")
        await started.wait()

        # group_cancel should return almost immediately.
        import time
        t0 = time.monotonic()
        await optio.group_cancel({"team": "alpha"})
        elapsed = time.monotonic() - t0
        assert elapsed < 0.2, f"group_cancel took {elapsed:.3f}s — should be fast"

        # Task is not terminal yet.
        proc = await optio.get_process("p.slow")
        assert proc["status"]["state"] in ("running", "cancel_requested", "cancelling")
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 3.2: Run — should pass without code changes**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_group_cancel_returns_before_terminal -v`
Expected: PASS. (No impl change needed; `group_cancel` is already fire-and-forget.)

- [ ] **Step 3.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): group_cancel returns before terminal state"
```

---

## Task 4: `group_cancel_and_wait` wait loop — all cooperative

Implement the pointer-walk wait loop and verify all-cooperative drain.

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` — implement `group_cancel_and_wait` body.
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 4.1: Write the failing test**

Append:

```python
# ---------- group_cancel_and_wait wait loop ----------

async def test_group_cancel_and_wait_all_cooperative(mongo_db):
    """All cooperative tasks reach terminal state 'cancelled' by the time
    group_cancel_and_wait returns; the call blocks until they do."""
    started = [asyncio.Event() for _ in range(3)]

    def make_cooperative(idx):
        async def fn(ctx):
            started[idx].set()
            for _ in range(200):
                if ctx.cancellation_flag.is_set():
                    return
                await asyncio.sleep(0.02)
        return fn

    tasks = [
        TaskInstance(
            process_id=f"p.coop.{i}", name=f"Coop{i}", params={},
            execute=make_cooperative(i), metadata={"team": "alpha"},
        )
        for i in range(3)
    ]

    optio, run_task = await _start_optio(mongo_db, "gcw_coop", tasks)
    try:
        for i in range(3):
            await optio.launch(f"p.coop.{i}")
        for ev in started:
            await ev.wait()

        await optio.group_cancel_and_wait({"team": "alpha"})

        for i in range(3):
            proc = await optio.get_process(f"p.coop.{i}")
            assert proc["status"]["state"] == "cancelled", (
                f"task {i} ended in {proc['status']['state']}"
            )
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 4.2: Run — should fail with NotImplementedError**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_group_cancel_and_wait_all_cooperative -v`
Expected: FAIL — `NotImplementedError`.

- [ ] **Step 4.3: Implement `group_cancel_and_wait`**

Replace the `group_cancel_and_wait` stub in `lifecycle.py` with the full body:

```python
    async def group_cancel_and_wait(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter` and wait
        for all of them to reach a terminal state. See
        docs/2026-04-30-group-cancel-design.md.

        Do not call from inside a task whose metadata matches the filter —
        use group_cancel for self-cancel.
        """
        if not metadata_filter:
            raise ValueError(
                "group_cancel_and_wait requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        # Guard not yet wired up — added in a later task.
        pending = await self._group_cancel_issue(metadata_filter, block_new_launches)
        if not pending:
            return

        # Pointer-walk wait loop. Helper's contract is "wait for ALL
        # terminal" so total wall time = max(t_i) regardless of check
        # order. One Mongo find_one per tick in the steady state.
        ceiling = self._config.cancel_grace_seconds + 25.0
        deadline = time.monotonic() + ceiling
        i = 0
        while i < len(pending):
            proc = await self.get_process(pending[i])
            if proc is None or proc["status"]["state"] not in ACTIVE_STATES:
                i += 1
                continue
            if time.monotonic() >= deadline:
                remaining = len(pending) - i
                raise asyncio.TimeoutError(
                    f"group_cancel_and_wait: {remaining} process(es) "
                    f"did not reach a terminal state within {ceiling}s "
                    f"(filter={metadata_filter})"
                )
            await asyncio.sleep(0.1)
```

Note: `time` is already imported at the top of `lifecycle.py`.

- [ ] **Step 4.4: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_group_cancel_and_wait_all_cooperative -v`
Expected: PASS within `cancel_grace_seconds` (~2.0s in the helper).

- [ ] **Step 4.5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_group_cancel.py
git commit -m "feat(optio-core): group_cancel_and_wait pointer-walk wait loop"
```

---

## Task 5: Mixed cooperative + stubborn

Verifies that stubborn tasks are force-cancelled by the supervisor and end in `failed` with the canonical error string. No new code; this is a regression test for the supervisor + helper interaction.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 5.1: Write the test**

Append:

```python
async def test_group_cancel_and_wait_mixed_cooperative_and_stubborn(mongo_db):
    """Cooperative tasks end 'cancelled'; stubborn tasks (ignore the flag)
    are force-cancelled by the supervisor and end 'failed' with the
    canonical error string."""
    started_coop = asyncio.Event()
    started_stub = asyncio.Event()

    async def cooperative(ctx):
        started_coop.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    async def stubborn(ctx):  # noqa: ARG001
        started_stub.set()
        while True:
            await asyncio.sleep(0.05)  # ignores the flag

    task_coop = TaskInstance(
        process_id="p.coop", name="Coop", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )
    task_stub = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_mixed", [task_coop, task_stub], cancel_grace_seconds=0.5,
    )
    try:
        await optio.launch("p.coop")
        await optio.launch("p.stub")
        await started_coop.wait()
        await started_stub.wait()

        await optio.group_cancel_and_wait({"team": "alpha"})

        proc_coop = await optio.get_process("p.coop")
        proc_stub = await optio.get_process("p.stub")
        assert proc_coop["status"]["state"] == "cancelled"
        assert proc_stub["status"]["state"] == "failed"
        assert (
            "Task did not unwind within cancellation grace period"
            in (proc_stub["status"].get("error") or "")
        )
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 5.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_group_cancel_and_wait_mixed_cooperative_and_stubborn -v`
Expected: PASS within `cancel_grace_seconds + small force-cancel buffer` (~1s).

- [ ] **Step 5.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): group_cancel_and_wait handles mixed cooperative/stubborn"
```

---

## Task 6: Internal ceiling backstop

If the supervisor never finalizes a stubborn task (patched to no-op), `group_cancel_and_wait` raises `asyncio.TimeoutError` once the internal ceiling expires.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 6.1: Write the test**

Append:

```python
async def test_group_cancel_and_wait_raises_on_internal_ceiling(mongo_db, monkeypatch):
    """Patch the executor's force_cancel to a no-op so the supervisor never
    finalizes the stubborn task. group_cancel_and_wait must raise
    asyncio.TimeoutError once the internal ceiling expires."""
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    task = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
        metadata={"team": "alpha"},
    )

    # Use a very small grace + ceiling buffer so the test is fast. The
    # ceiling = cancel_grace_seconds + 25 in production; we patch that
    # constant via a small monkeypatch on the helper at the call site.
    optio, run_task = await _start_optio(
        mongo_db, "gcw_ceil", [task], cancel_grace_seconds=0.2,
    )
    try:
        await optio.launch("p.stub")
        await started.wait()

        # No-op the executor's force_cancel so the supervisor cannot
        # finalize the stubborn task.
        async def noop(*a, **k):
            return None
        monkeypatch.setattr(optio._executor, "force_cancel", noop)

        # Patch the +25.0 buffer to something tiny by monkeypatching the
        # helper to use a smaller ceiling. Cleanest: temporarily mutate
        # the config's cancel_grace_seconds to make the formula evaluate
        # to a small number, e.g. set to a negative value so total ≈ 0.5.
        # But: cancel_grace_seconds also controls when the supervisor
        # would force-cancel — which is patched out. So we can shrink it.
        optio._config.cancel_grace_seconds = -24.5  # ceiling = 0.5

        with pytest.raises(asyncio.TimeoutError, match="did not reach a terminal state"):
            await optio.group_cancel_and_wait({"team": "alpha"})
    finally:
        # Reset before shutdown so shutdown's grace logic doesn't go wild.
        optio._config.cancel_grace_seconds = 0.2
        await _stop_optio(optio, run_task)
```

- [ ] **Step 6.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_group_cancel_and_wait_raises_on_internal_ceiling -v`
Expected: PASS within ~1s.

- [ ] **Step 6.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): group_cancel_and_wait raises on internal ceiling"
```

---

## Task 7: `block_new_launches=True` activates the launch guard

Implement the optional launch-guard scope. Verify it rejects new launches during the call.

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` — wrap each public method body in `AsyncExitStack`.
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 7.1: Write the failing test**

Append:

```python
# ---------- block_new_launches=True ----------

async def test_block_new_launches_rejects_during_call(mongo_db):
    """While group_cancel_and_wait runs with block_new_launches=True,
    a concurrent launch matching the filter raises LaunchBlocked.
    After the helper returns, the guard is gone."""
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    target_task = TaskInstance(
        process_id="p.target", name="Target", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )
    intruder_task = TaskInstance(
        process_id="p.intruder", name="Intruder", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_guard", [target_task, intruder_task],
    )
    try:
        await optio.launch("p.target")
        await started.wait()

        # We need the intruder launch to race with the helper. Spawn a
        # coroutine that waits briefly then attempts to launch.
        intruder_blocked = asyncio.Event()

        async def attempt_intruder():
            # Wait long enough that the helper has registered the guard.
            await asyncio.sleep(0.05)
            with pytest.raises(LaunchBlocked):
                await optio.launch_and_wait("p.intruder")
            intruder_blocked.set()

        intruder_task_handle = asyncio.create_task(attempt_intruder())

        await optio.group_cancel_and_wait(
            {"team": "alpha"}, block_new_launches=True,
        )

        await intruder_task_handle
        assert intruder_blocked.is_set()

        # Guard lifted on return.
        assert optio._launch_blocks == {}

        # And the same launch now succeeds.
        # (We need a fresh started Event since cooperative re-runs.)
        # Skip — already verified rejection-then-guard-lifted; not bothering
        # with a second launch here.
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 7.2: Run — should fail (guard not yet wired up)**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_block_new_launches_rejects_during_call -v`
Expected: FAIL — intruder launch will succeed because the guard isn't set up yet.

- [ ] **Step 7.3: Wire up the guard scope in both public methods**

Replace `group_cancel` in `lifecycle.py` with:

```python
    async def group_cancel(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """Cancel every active process matching `metadata_filter`. Does NOT
        wait for terminal state. See docs/2026-04-30-group-cancel-design.md."""
        if not metadata_filter:
            raise ValueError(
                "group_cancel requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        async with AsyncExitStack() as stack:
            if block_new_launches:
                await stack.enter_async_context(
                    self.block_launches(metadata_filter)
                )
            await self._group_cancel_issue(metadata_filter, block_new_launches)
```

Replace `group_cancel_and_wait` similarly:

```python
    async def group_cancel_and_wait(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
    ) -> None:
        """[unchanged docstring]"""
        if not metadata_filter:
            raise ValueError(
                "group_cancel_and_wait requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        async with AsyncExitStack() as stack:
            if block_new_launches:
                await stack.enter_async_context(
                    self.block_launches(metadata_filter)
                )
            pending = await self._group_cancel_issue(
                metadata_filter, block_new_launches,
            )
            if not pending:
                return

            ceiling = self._config.cancel_grace_seconds + 25.0
            deadline = time.monotonic() + ceiling
            i = 0
            while i < len(pending):
                proc = await self.get_process(pending[i])
                if proc is None or proc["status"]["state"] not in ACTIVE_STATES:
                    i += 1
                    continue
                if time.monotonic() >= deadline:
                    remaining = len(pending) - i
                    raise asyncio.TimeoutError(
                        f"group_cancel_and_wait: {remaining} process(es) "
                        f"did not reach a terminal state within {ceiling}s "
                        f"(filter={metadata_filter})"
                    )
                await asyncio.sleep(0.1)
```

Add `from contextlib import AsyncExitStack` to the top of `lifecycle.py` if not already imported. (Check the existing imports — at the time of writing, `lifecycle.py` imports `from contextlib import asynccontextmanager`; you'll need to add `AsyncExitStack` to that import.)

- [ ] **Step 7.4: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_block_new_launches_rejects_during_call -v`
Expected: PASS.

- [ ] **Step 7.5: Run the whole file — verify nothing regressed**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`
Expected: all previous tests still PASS.

- [ ] **Step 7.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_group_cancel.py
git commit -m "feat(optio-core): block_new_launches guard on group_cancel pair"
```

---

## Task 8: `block_new_launches=False` does NOT register a guard

Capture-and-compare style assertion (per spec — robust against unrelated guards).

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 8.1: Write the test**

Append:

```python
@pytest.mark.parametrize("method_name", ["group_cancel", "group_cancel_and_wait"])
async def test_block_new_launches_false_no_guard_registered(mongo_db, method_name):
    """With block_new_launches=False, _launch_blocks does not gain a token
    during the call. Capture-and-compare so unrelated guards don't break
    the assertion."""
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    task = TaskInstance(
        process_id="p.x", name="X", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, f"gc_noguard_{method_name}", [task])
    try:
        await optio.launch("p.x")
        await started.wait()

        before = set(optio._launch_blocks.keys())
        method = getattr(optio, method_name)
        await method({"team": "alpha"}, block_new_launches=False)
        after = set(optio._launch_blocks.keys())
        assert before == after
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 8.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_block_new_launches_false_no_guard_registered -v`
Expected: PASS for both parametrizations.

- [ ] **Step 8.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): no guard registered when block_new_launches=False"
```

---

## Task 9: Leak sweep catches a bucket-(c) launch

Implement the leak sweep. Verify it catches a launch that passed the check before the guard registered but completed its upsert after the snapshot.

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` — extend `_group_cancel_issue` with the leak-sweep step.
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

The simulation: monkey-patch `_check_launch_blocks` so it doesn't raise even when a guard is active, then directly invoke an `adhoc_define` + `launch` from a separate coroutine timed to land its upsert *after* the helper's snapshot. The leak sweep's 100 ms re-list must then find it.

- [ ] **Step 9.1: Write the failing test**

Append:

```python
# ---------- Leak sweep ----------

async def test_leak_sweep_catches_post_snapshot_launch(mongo_db, monkeypatch):
    """A launch that passed _check_launch_blocks before the guard
    registered but completed its upsert AFTER the helper's initial
    snapshot is caught by the leak sweep and cancelled."""
    started_intruder = asyncio.Event()

    async def cooperative(ctx):
        started_intruder.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    intruder_task = TaskInstance(
        process_id="p.intruder", name="Intruder", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_leak", [intruder_task],
    )
    try:
        # Patch _check_launch_blocks to a no-op for this test — simulates
        # the racing launch that passed the check before the guard arrived.
        monkeypatch.setattr(optio, "_check_launch_blocks", lambda _md: None)

        # Helper that launches the intruder concurrently with the helper.
        async def stage_intruder():
            # Wait until we're confident the helper has snapshotted (which
            # happens almost immediately on entry). The 100 ms leak-sweep
            # delay then gives us plenty of time to land the upsert.
            await asyncio.sleep(0.02)
            await optio.launch("p.intruder")

        intruder_handle = asyncio.create_task(stage_intruder())

        await optio.group_cancel_and_wait(
            {"team": "alpha"}, block_new_launches=True,
        )
        await intruder_handle

        # Intruder must have been cancelled (caught by the leak sweep)
        # and reached a terminal state by the time the call returned.
        proc = await optio.get_process("p.intruder")
        assert proc["status"]["state"] in ("cancelled", "failed"), (
            f"intruder ended in {proc['status']['state']} "
            "(should have been caught by leak sweep)"
        )
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 9.2: Run — should fail**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_leak_sweep_catches_post_snapshot_launch -v`
Expected: FAIL — intruder may still be `running` when the helper returns (or, depending on timing, the helper might raise TimeoutError).

- [ ] **Step 9.3: Implement the leak sweep**

Replace `_group_cancel_issue` in `lifecycle.py` with:

```python
    async def _group_cancel_issue(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool,
    ) -> list[str]:
        """Snapshot, cancel, optionally leak-sweep. Returns the list of
        process_ids that were cancelled (snapshot + leaked).

        Caller is responsible for the launch guard's AsyncExitStack —
        this helper assumes the guard is already active when called with
        block_new_launches=True.
        """
        # 1. Snapshot active processes matching the filter.
        procs = await self.list_processes(metadata=metadata_filter)
        active = [p for p in procs if p["status"]["state"] in ACTIVE_STATES]

        # 2. Issue cancellations in parallel. cancel() is non-blocking
        #    and idempotent.
        if active:
            await asyncio.gather(
                *(self.cancel(p["processId"]) for p in active)
            )

        pending_ids = [p["processId"] for p in active]

        # 3. Leak sweep (only with block_new_launches=True). Catches
        #    launches that passed _check_launch_blocks before the guard
        #    registered but completed their upsert after our snapshot.
        if block_new_launches:
            await asyncio.sleep(0.1)
            latest = await self.list_processes(metadata=metadata_filter)
            known = set(pending_ids)
            leaked = [
                p for p in latest
                if p["status"]["state"] in ACTIVE_STATES
                and p["processId"] not in known
            ]
            if leaked:
                await asyncio.gather(
                    *(self.cancel(p["processId"]) for p in leaked)
                )
                pending_ids.extend(p["processId"] for p in leaked)

        return pending_ids
```

- [ ] **Step 9.4: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_leak_sweep_catches_post_snapshot_launch -v`
Expected: PASS.

- [ ] **Step 9.5: Run the whole file**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`
Expected: all previous tests still PASS.

- [ ] **Step 9.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_group_cancel.py
git commit -m "feat(optio-core): leak sweep for group_cancel block_new_launches=True"
```

---

## Task 10: Leak sweep is a no-op when nothing leaks

Verifies the sweep doesn't add spurious pids when there's no concurrent in-flight launch.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 10.1: Write the test**

Append:

```python
async def test_leak_sweep_noop_when_no_concurrent_launch(mongo_db):
    """With block_new_launches=True and no in-flight launches, the leak
    sweep adds zero pids; helper returns normally."""
    started = asyncio.Event()

    async def cooperative(ctx):
        started.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    task = TaskInstance(
        process_id="p.solo", name="Solo", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, "gcw_noleak", [task])
    try:
        await optio.launch("p.solo")
        await started.wait()

        # No concurrent stage_intruder; just call the helper.
        await optio.group_cancel_and_wait(
            {"team": "alpha"}, block_new_launches=True,
        )

        proc = await optio.get_process("p.solo")
        assert proc["status"]["state"] == "cancelled"
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 10.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_leak_sweep_noop_when_no_concurrent_launch -v`
Expected: PASS.

- [ ] **Step 10.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): leak sweep is a no-op when nothing to leak"
```

---

## Task 11: Self-cancel via `group_cancel`

A task whose own metadata matches the filter calls `group_cancel({"team":"alpha"})` from inside its body. The call must return; the task must then unwind cooperatively at its next cancel-check.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 11.1: Write the test**

Append:

```python
# ---------- Self-cancel ----------

async def test_self_cancel_via_group_cancel(mongo_db):
    """A task that calls group_cancel matching its own metadata returns
    from the call cleanly, then unwinds cooperatively."""
    reached_after_call = asyncio.Event()

    async def self_canceller(ctx):
        # Cancel my own group, including myself.
        await optio_handle["optio"].group_cancel({"team": "alpha"})
        reached_after_call.set()
        # Now keep checking the flag. Should see it set very soon.
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    task = TaskInstance(
        process_id="p.self", name="Self", params={}, execute=self_canceller,
        metadata={"team": "alpha"},
    )

    # Trick: stash the optio reference where the task body can reach it.
    optio_handle = {}

    optio, run_task = await _start_optio(mongo_db, "gc_self", [task])
    optio_handle["optio"] = optio
    try:
        await optio.launch("p.self")

        # Wait for the task to reach the post-call point and then unwind.
        for _ in range(200):
            await asyncio.sleep(0.05)
            proc = await optio.get_process("p.self")
            if proc["status"]["state"] == "cancelled":
                break
        proc = await optio.get_process("p.self")
        assert proc["status"]["state"] == "cancelled"
        assert reached_after_call.is_set()  # the call returned
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 11.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_self_cancel_via_group_cancel -v`
Expected: PASS within ~`cancel_grace_seconds` (~2s).

- [ ] **Step 11.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): self-cancel via group_cancel works"
```

---

## Task 12: Guard lifted on exception (`block_new_launches=True`)

When `group_cancel_and_wait` raises (e.g. `asyncio.TimeoutError`), the AsyncExitStack must lift the guard.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 12.1: Write the test**

Append:

```python
async def test_guard_lifted_on_exception(mongo_db, monkeypatch):
    """When group_cancel_and_wait raises asyncio.TimeoutError with
    block_new_launches=True, the launch guard is lifted on the way out
    (capture-and-compare _launch_blocks)."""
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    task = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(
        mongo_db, "gcw_lift", [task], cancel_grace_seconds=0.2,
    )
    try:
        await optio.launch("p.stub")
        await started.wait()

        # Patch out force_cancel + shrink the ceiling — same trick as
        # Task 6, so the helper raises TimeoutError.
        async def noop(*a, **k):
            return None
        monkeypatch.setattr(optio._executor, "force_cancel", noop)
        optio._config.cancel_grace_seconds = -24.5  # ceiling = 0.5

        before = set(optio._launch_blocks.keys())
        with pytest.raises(asyncio.TimeoutError):
            await optio.group_cancel_and_wait(
                {"team": "alpha"}, block_new_launches=True,
            )
        after = set(optio._launch_blocks.keys())
        assert before == after  # guard lifted on raise
    finally:
        optio._config.cancel_grace_seconds = 0.2
        await _stop_optio(optio, run_task)
```

- [ ] **Step 12.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_guard_lifted_on_exception -v`
Expected: PASS.

- [ ] **Step 12.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): launch guard lifted on group_cancel_and_wait exception"
```

---

## Task 13: New launches during wait NOT included (`block_new_launches=False`)

Verifies snapshot semantics: with the guard off, post-snapshot launches are not part of the wait set.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 13.1: Write the test**

Append:

```python
async def test_no_block_new_launches_post_snapshot_not_cancelled(mongo_db):
    """With block_new_launches=False, a launch that lands during the
    wait phase is NOT cancelled by the helper (snapshot semantics)."""
    started_a = asyncio.Event()
    started_b = asyncio.Event()

    async def cooperative(ctx):
        started_a.set()
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.02)

    async def long_runner(ctx):  # noqa: ARG001
        started_b.set()
        await asyncio.sleep(2.0)

    task_a = TaskInstance(
        process_id="p.a", name="A", params={}, execute=cooperative,
        metadata={"team": "alpha"},
    )
    task_b = TaskInstance(
        process_id="p.b", name="B", params={}, execute=long_runner,
        metadata={"team": "alpha"},
    )

    optio, run_task = await _start_optio(mongo_db, "gcw_noblock_post", [task_a, task_b])
    try:
        await optio.launch("p.a")
        await started_a.wait()

        # Stage task_b to launch after the helper's snapshot. The helper
        # snapshots almost immediately on entry; the wait loop polls every
        # 100 ms. A 50 ms delay reliably lands b's upsert during the wait.
        async def stage_b():
            await asyncio.sleep(0.05)
            await optio.launch("p.b")

        b_handle = asyncio.create_task(stage_b())

        await optio.group_cancel_and_wait({"team": "alpha"})  # default False
        await b_handle

        # b is still running — was not in the snapshot.
        proc_b = await optio.get_process("p.b")
        assert proc_b["status"]["state"] == "running"
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 13.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_no_block_new_launches_post_snapshot_not_cancelled -v`
Expected: PASS.

- [ ] **Step 13.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): default group_cancel_and_wait honours snapshot semantics"
```

---

## Task 14: No active processes match

Both helpers return cleanly when the snapshot is empty; with `block_new_launches=True` the leak sweep still runs.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 14.1: Write the tests**

Append:

```python
@pytest.mark.parametrize("method_name", ["group_cancel", "group_cancel_and_wait"])
async def test_no_active_processes_match(mongo_db, method_name):
    """No matching tasks → both helpers return without error."""
    optio, run_task = await _start_optio(mongo_db, f"gc_empty_{method_name}", [])
    try:
        method = getattr(optio, method_name)
        # Default block_new_launches=False: trivial return.
        await method({"team": "alpha"})
        # block_new_launches=True: leak sweep runs but adds 0 pids.
        await method({"team": "alpha"}, block_new_launches=True)
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 14.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_no_active_processes_match -v`
Expected: PASS for both parametrizations.

- [ ] **Step 14.3: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): both helpers handle empty snapshot cleanly"
```

---

## Task 15: Module-level export verification

Quick sanity test that the two new methods are accessible via `optio_core.group_cancel` / `optio_core.group_cancel_and_wait` — i.e. the singleton wiring in `__init__.py` is correct.

**Files:**
- Modify: `packages/optio-core/tests/test_group_cancel.py` — add the test.

- [ ] **Step 15.1: Write the test**

Append:

```python
# ---------- Public API export ----------

def test_group_cancel_pair_exported_from_package():
    """Both helpers are exported from optio_core, bound to the singleton."""
    import optio_core
    assert optio_core.group_cancel == optio_core._instance.group_cancel
    assert optio_core.group_cancel_and_wait == optio_core._instance.group_cancel_and_wait
    assert "group_cancel" in optio_core.__all__
    assert "group_cancel_and_wait" in optio_core.__all__
```

- [ ] **Step 15.2: Run — should pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py::test_group_cancel_pair_exported_from_package -v`
Expected: PASS.

- [ ] **Step 15.3: Run the full new test file one last time**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`
Expected: all tests PASS.

- [ ] **Step 15.4: Run the full optio-core suite to verify no regressions**

Run: `pytest packages/optio-core/tests/ -v`
Expected: all PASS.

- [ ] **Step 15.5: Commit**

```bash
git add packages/optio-core/tests/test_group_cancel.py
git commit -m "test(optio-core): group_cancel pair exported from package"
```

---

## Wrap-up

After Task 15:
- All tests in `packages/optio-core/tests/test_group_cancel.py` pass.
- Full `pytest packages/optio-core/tests/` passes (no regressions).
- Both methods exported from `optio_core` and bound to the singleton.

Use `superpowers:finishing-a-development-branch` next to merge to `main` (or open a PR). The spec's `**Base revision:**` header enables that skill's drift detection; if drift on `main` is detected, follow its rebase + spec-update flow.
