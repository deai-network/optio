# Cancel Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `optio_core.cancel(process_id)` propagate to active descendants by default, with a per-task opt-out, an unconditional force-cancel cascade, and α-style upward channeling through the same code path.

**Architecture:** Add `auto_cancel_children` to `TaskInstance`. Make `lifecycle.cancel` recursive over direct active children, passing a shared deadline. Make `executor.force_cancel` recursive unconditionally. Wire executor → lifecycle via a `notify_parent_abnormal` callback so child-cancel / child-fail upward signals re-enter the cancel pipeline. Refuse `run_child` after parent cancellation under auto-propagate. Add an orphan safety net in `_execute_process` finally that issues cooperative cancel on still-active direct children when the parent ends non-`done`.

**Tech Stack:** Python 3.11+, motor (async MongoDB), asyncio, pytest-asyncio. Reference spec: `docs/2026-05-11-cancel-propagation-design.md`.

**Repo layout:** all changes are in `packages/optio-core/`. Tests in `packages/optio-core/tests/`. Doc updates in `AGENTS.md` (root) and `packages/optio-core/AGENTS.md`. Working directory: `/home/csillag/deai/optio`.

**Test execution:** all tests run via `pytest`. MongoDB must be reachable via `MONGO_URL` (defaults to `mongodb://localhost:27017`). Use the project's Docker compose stack as documented in root `AGENTS.md`.

---

## Phase 1 — Foundation

### Task 1.1: Add `auto_cancel_children` field to `TaskInstance`

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py:31-46`
- Test: `packages/optio-core/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_models.py`:

```python
def test_task_instance_auto_cancel_children_default_true():
    """TaskInstance.auto_cancel_children defaults to True."""
    from optio_core.models import TaskInstance

    async def _noop(ctx):
        pass

    task = TaskInstance(execute=_noop, process_id="p1", name="P1")
    assert task.auto_cancel_children is True


def test_task_instance_auto_cancel_children_can_be_false():
    """TaskInstance.auto_cancel_children can be explicitly set to False."""
    from optio_core.models import TaskInstance

    async def _noop(ctx):
        pass

    task = TaskInstance(
        execute=_noop, process_id="p1", name="P1", auto_cancel_children=False,
    )
    assert task.auto_cancel_children is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-core/tests/test_models.py::test_task_instance_auto_cancel_children_default_true -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'auto_cancel_children'` or `AttributeError`.

- [ ] **Step 3: Add the field to `TaskInstance`**

Edit `packages/optio-core/src/optio_core/models.py` — add the field at the end of the `TaskInstance` dataclass (after `ttl_seconds`):

```python
@dataclass
class TaskInstance:
    """A unit of work provided by the application's task generator."""
    execute: Callable[..., Awaitable[None]]
    process_id: str
    name: str
    description: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None
    special: bool = False
    warning: str | None = None
    cancellable: bool = True
    ui_widget: str | None = None
    supports_resume: bool = False
    ttl_seconds: int | None = None
    auto_cancel_children: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest packages/optio-core/tests/test_models.py -v`
Expected: both new tests PASS. All existing model tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py packages/optio-core/tests/test_models.py
git commit -m "feat(optio-core): add TaskInstance.auto_cancel_children flag"
```

---

### Task 1.2: Add `list_direct_children` helper to `store.py`

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py:300-321`
- Test: `packages/optio-core/tests/test_store.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_store.py`:

```python
import pytest
from bson import ObjectId

pytestmark = pytest.mark.asyncio


async def test_list_direct_children_returns_only_direct(mongo_db):
    """list_direct_children excludes grandchildren."""
    from optio_core.store import list_direct_children, _collection

    prefix = "ldc"
    coll = _collection(mongo_db, prefix)
    parent_oid = ObjectId()
    direct_oid = ObjectId()
    grandchild_oid = ObjectId()

    await coll.insert_one({
        "_id": parent_oid, "processId": "parent", "name": "Parent",
        "status": {"state": "running"}, "depth": 0, "order": 0,
    })
    await coll.insert_one({
        "_id": direct_oid, "processId": "direct", "name": "Direct",
        "parentId": parent_oid,
        "status": {"state": "running"}, "depth": 1, "order": 0,
    })
    await coll.insert_one({
        "_id": grandchild_oid, "processId": "gc", "name": "Grandchild",
        "parentId": direct_oid,
        "status": {"state": "running"}, "depth": 2, "order": 0,
    })

    rows = await list_direct_children(mongo_db, prefix, parent_oid)
    ids = [r["_id"] for r in rows]
    assert ids == [direct_oid]


async def test_list_direct_children_filters_by_states(mongo_db):
    """list_direct_children honors the states filter."""
    from optio_core.store import list_direct_children, _collection

    prefix = "ldc2"
    coll = _collection(mongo_db, prefix)
    parent_oid = ObjectId()
    running_oid = ObjectId()
    done_oid = ObjectId()

    await coll.insert_one({
        "_id": parent_oid, "processId": "p", "name": "P",
        "status": {"state": "running"}, "depth": 0, "order": 0,
    })
    await coll.insert_one({
        "_id": running_oid, "processId": "r", "name": "R",
        "parentId": parent_oid,
        "status": {"state": "running"}, "depth": 1, "order": 0,
    })
    await coll.insert_one({
        "_id": done_oid, "processId": "d", "name": "D",
        "parentId": parent_oid,
        "status": {"state": "done"}, "depth": 1, "order": 1,
    })

    rows = await list_direct_children(
        mongo_db, prefix, parent_oid, states={"running", "scheduled"},
    )
    ids = [r["_id"] for r in rows]
    assert ids == [running_oid]


async def test_list_direct_children_no_states_returns_all(mongo_db):
    """When states is None, list_direct_children returns all direct children."""
    from optio_core.store import list_direct_children, _collection

    prefix = "ldc3"
    coll = _collection(mongo_db, prefix)
    parent_oid = ObjectId()

    await coll.insert_one({
        "_id": parent_oid, "processId": "p", "name": "P",
        "status": {"state": "running"}, "depth": 0, "order": 0,
    })
    for i, state in enumerate(["running", "done", "failed"]):
        await coll.insert_one({
            "_id": ObjectId(), "processId": f"c{i}", "name": f"C{i}",
            "parentId": parent_oid,
            "status": {"state": state}, "depth": 1, "order": i,
        })

    rows = await list_direct_children(mongo_db, prefix, parent_oid)
    assert len(rows) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest packages/optio-core/tests/test_store.py::test_list_direct_children_returns_only_direct -v`
Expected: FAIL with `ImportError: cannot import name 'list_direct_children'`.

- [ ] **Step 3: Implement `list_direct_children`**

Edit `packages/optio-core/src/optio_core/store.py` — add immediately after `get_children` (around line 307):

```python
async def list_direct_children(
    db: AsyncIOMotorDatabase,
    prefix: str,
    parent_oid: ObjectId,
    *,
    states: set[str] | None = None,
) -> list[dict]:
    """Return direct children of `parent_oid`, optionally filtered by status.state.

    Sorted by `order` ascending, then `_id` ascending for stable ordering.
    `states=None` returns all direct children regardless of state.
    """
    filt: dict = {"parentId": parent_oid}
    if states is not None:
        filt["status.state"] = {"$in": list(states)}
    return await _collection(db, prefix).find(filt).sort(
        [("order", 1), ("_id", 1)]
    ).to_list(None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest packages/optio-core/tests/test_store.py::test_list_direct_children_returns_only_direct packages/optio-core/tests/test_store.py::test_list_direct_children_filters_by_states packages/optio-core/tests/test_store.py::test_list_direct_children_no_states_returns_all -v`
Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/store.py packages/optio-core/tests/test_store.py
git commit -m "feat(optio-core): add store.list_direct_children helper"
```

---

## Phase 2 — Downward propagation in `lifecycle.cancel`

This phase makes `cancel(parent)` recursive when `auto_cancel_children=True` and passes a shared deadline. New test file: `packages/optio-core/tests/test_cancel_propagation.py`.

### Task 2.1: Create test file with basic downward-propagation scenario

**Files:**
- Create: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Create the test file with header and basic scenario**

Create `packages/optio-core/tests/test_cancel_propagation.py`:

```python
"""Tests for cancel propagation across the process tree.

Spec: docs/2026-05-11-cancel-propagation-design.md
"""
import asyncio
import time as _time

import pytest
from bson import ObjectId

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import (
    get_process_by_process_id, list_direct_children, upsert_process,
)

pytestmark = pytest.mark.asyncio


async def _wait_terminal(mongo_db, prefix: str, process_id: str, timeout: float = 5.0):
    """Poll until process_id reaches a terminal state or timeout."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await get_process_by_process_id(mongo_db, prefix, process_id)
        if proc is not None and proc["status"]["state"] in {"done", "failed", "cancelled"}:
            return proc
        await asyncio.sleep(0.02)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def test_cancel_parent_propagates_to_running_children(mongo_db):
    """cancel(parent) cancels running direct children when auto_cancel_children=True."""
    prefix = "p2t1"
    child_started = asyncio.Event()

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        child_started.set()
        await ctx.run_child(
            execute=long_child,
            process_id="child",
            name="Child",
        )

    parent_task = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_task = TaskInstance(execute=long_child, process_id="child", name="Child")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_task)
    optio._executor.register_tasks([parent_task, child_task])

    runner = asyncio.create_task(optio.launch("parent"))
    await child_started.wait()
    await asyncio.sleep(0.05)  # let child enter `running` in DB

    await optio.cancel("parent")
    await runner

    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    child_proc = await _wait_terminal(mongo_db, prefix, "child")
    assert parent_proc["status"]["state"] == "cancelled"
    assert child_proc["status"]["state"] == "cancelled"

    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_cancel_parent_propagates_to_running_children -v`
Expected: FAIL — the child stays running because today's `cancel()` does not propagate. The test will time out in `_wait_terminal(child)` (eventually reaches assertion error) OR the parent's `cancel_grace_seconds=2.0` kicks in and force-cancels the parent without touching the child.

Note: depending on test runner timing the failure may surface as the child remaining `running` past `_wait_terminal`. Confirm the failure mode is one of these before continuing — it proves the propagation gap.

---

### Task 2.2: Implement recursive cancel with shared deadline

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:381-415`

- [ ] **Step 1: Modify `Optio.cancel` to recurse on active direct children**

Edit `packages/optio-core/src/optio_core/lifecycle.py`. Replace the existing `cancel` method (around line 381) with the recursive version:

```python
async def cancel(
    self,
    process_id: str,
    *,
    inherit_deadline: float | None = None,
) -> CancelOutcome:
    """Cancel a running or scheduled process and propagate to direct active
    children when the process's TaskInstance has `auto_cancel_children=True`.

    `inherit_deadline` is for internal recursion. External callers omit it,
    in which case a fresh deadline is computed from `cancel_grace_seconds`.
    The same monotonic deadline is passed through the entire subtree so
    every entry expires at the same instant.
    """
    proc = await self._resolve(process_id)
    if proc is None:
        return CancelOutcome(ok=False, reason="not-found")
    if not proc.get("cancellable", True) or proc["status"]["state"] not in CANCELLABLE_STATES:
        return CancelOutcome(ok=False, reason="not-cancellable")

    current_state = proc["status"]["state"]
    if current_state == "scheduled":
        now = datetime.now(timezone.utc)
        expire_at = compute_expire_at(proc.get("ttlSeconds"), now=now)
        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="cancelled", stopped_at=now),
            expire_at=expire_at,
        )
    else:
        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="cancel_requested"),
        )
        deadline = (
            inherit_deadline
            if inherit_deadline is not None
            else time.monotonic() + self._config.cancel_grace_seconds
        )
        found = self._executor.request_cancel_with_deadline(
            proc["_id"], deadline=deadline,
        )
        if found:
            await update_status(
                self._config.mongo_db, self._config.prefix, proc["_id"],
                ProcessStatus(state="cancelling"),
            )

    # Downward propagation: recurse into active direct children unless
    # the TaskInstance opts out. Unknown task → assume True (fail safe
    # toward propagation, not orphan).
    task = self._executor._task_registry.get(proc["processId"])
    auto = task.auto_cancel_children if task is not None else True
    if auto:
        from optio_core.store import list_direct_children
        # Use the recursion's effective deadline so descendants share it.
        effective_deadline = (
            inherit_deadline
            if inherit_deadline is not None
            else time.monotonic() + self._config.cancel_grace_seconds
        )
        children = await list_direct_children(
            self._config.mongo_db, self._config.prefix,
            proc["_id"], states=ACTIVE_STATES,
        )
        if children:
            await asyncio.gather(
                *(
                    self.cancel(c["processId"], inherit_deadline=effective_deadline)
                    for c in children
                ),
                return_exceptions=True,
            )

    return CancelOutcome(ok=True)
```

Note: the `effective_deadline` is computed once at the top level by the entry-point caller and threaded through recursion. The duplicate computation above guards against the (impossible-in-practice but defensive) case where the top-level scheduled-state shortcut skipped the deadline arming.

- [ ] **Step 2: Run the basic downward test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_cancel_parent_propagates_to_running_children -v`
Expected: PASS. Both parent and child reach `cancelled`.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_cancel_propagation.py
git commit -m "feat(optio-core): cancel() propagates to active direct children"
```

---

### Task 2.3: Test opt-out shields direct children

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_cancel_optout_does_not_auto_cancel_children(mongo_db):
    """Parent with auto_cancel_children=False keeps children running until
    its own execute fn cancels them or completes."""
    prefix = "p2t3"
    child_started = asyncio.Event()
    parent_started = asyncio.Event()
    parent_observed_cancel = asyncio.Event()

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        parent_started.set()
        # Spawn child concurrently with this parent — must use parallel_group
        # so parent can observe its own cancellation_flag while child runs.
        async with ctx.parallel_group(survive_cancel=True) as group:
            await group.spawn(
                execute=long_child, process_id="child", name="Child",
            )
            child_started.set()
            # Wait for own cancel; child keeps running.
            while ctx.should_continue():
                await asyncio.sleep(0.01)
            parent_observed_cancel.set()
            # Parent does NOT cancel child explicitly — for this test we
            # let force-cancel handle it after grace expires.

    parent_task = TaskInstance(
        execute=parent, process_id="parent", name="Parent",
        auto_cancel_children=False,
    )
    child_task = TaskInstance(execute=long_child, process_id="child", name="Child")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=1.0)
    await upsert_process(mongo_db, prefix, parent_task)
    optio._executor.register_tasks([parent_task, child_task])

    runner = asyncio.create_task(optio.launch("parent"))
    await child_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")

    # Immediately after cancel: child should still be active because
    # parent opted out. Verify the child has NOT been moved to
    # cancel_requested by the cancel call itself.
    child_proc = await get_process_by_process_id(mongo_db, prefix, "child")
    assert child_proc["status"]["state"] in {"running", "scheduled"}, (
        f"opt-out parent should not auto-cancel children, "
        f"got child state={child_proc['status']['state']}"
    )

    # Run-to-completion fallback: wait for force-cancel cascade to clean
    # up everything within grace + buffer.
    await asyncio.wait_for(runner, timeout=5.0)
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    child_proc = await _wait_terminal(mongo_db, prefix, "child")
    # Parent and child both reach terminal — parent via cancel-then-force,
    # child via force-cancel cascade (Phase 5). Until Phase 5 lands, this
    # assertion verifies the IMMEDIATE post-cancel state only.
    assert parent_proc["status"]["state"] in {"cancelled", "failed"}
    assert child_proc["status"]["state"] in {"cancelled", "failed"}

    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test to verify post-cancel immediate state assertion passes**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_cancel_optout_does_not_auto_cancel_children -v`
Expected: PASS for the immediate-after-cancel assertion (child still active). The terminal-state assertion may PASS or take the full grace window. If it times out, the assertion `_wait_terminal` will trigger — until force-cancel cascade (Phase 5) lands, the child may stay running. Verify the IMMEDIATE assertion holds; if the terminal assertion fails, that's expected pre-Phase-5.

If the test fails on the terminal-state assertion only, that is acceptable until Phase 5 lands. Mark this test with `@pytest.mark.skip(reason="completes after Phase 5 force-cancel cascade")` if needed, removing the skip in Task 5.3.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): opt-out parent shields direct children from auto-cancel"
```

---

### Task 2.4: Test per-level flag honored (recursion through opt-out)

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_cancel_recursion_honors_per_level_optout(mongo_db):
    """A→B→C where B opts out. cancel(A) cancels A and B; C remains active."""
    prefix = "p2t4"
    a_running = asyncio.Event()
    b_running = asyncio.Event()
    c_running = asyncio.Event()

    async def c_task(ctx):
        c_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def b_task(ctx):
        b_running.set()
        async with ctx.parallel_group(survive_cancel=True) as group:
            await group.spawn(execute=c_task, process_id="c", name="C")
            while ctx.should_continue():
                await asyncio.sleep(0.01)

    async def a_task(ctx):
        a_running.set()
        await ctx.run_child(execute=b_task, process_id="b", name="B")

    a_inst = TaskInstance(execute=a_task, process_id="a", name="A")
    b_inst = TaskInstance(
        execute=b_task, process_id="b", name="B",
        auto_cancel_children=False,
    )
    c_inst = TaskInstance(execute=c_task, process_id="c", name="C")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=1.0)
    await upsert_process(mongo_db, prefix, a_inst)
    optio._executor.register_tasks([a_inst, b_inst, c_inst])

    runner = asyncio.create_task(optio.launch("a"))
    await a_running.wait()
    await b_running.wait()
    await c_running.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("a")

    # Immediately: A and B should be in cancel_requested/cancelling; C
    # remains active because B opted out.
    a_proc = await get_process_by_process_id(mongo_db, prefix, "a")
    b_proc = await get_process_by_process_id(mongo_db, prefix, "b")
    c_proc = await get_process_by_process_id(mongo_db, prefix, "c")
    assert a_proc["status"]["state"] in {"cancel_requested", "cancelling"}
    assert b_proc["status"]["state"] in {"cancel_requested", "cancelling"}
    assert c_proc["status"]["state"] in {"running", "scheduled"}

    # Run-to-completion via force-cancel after grace (Phase 5 dependency).
    await asyncio.wait_for(runner, timeout=5.0)
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_cancel_recursion_honors_per_level_optout -v`
Expected: the immediate state assertions PASS. The `await asyncio.wait_for(runner, timeout=5.0)` may time out until Phase 5; if so, only check immediate assertions until Phase 5 lands. Mark `@pytest.mark.skip` temporarily if needed and remove in Task 5.3.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): cancel recursion honors per-level auto_cancel_children"
```

---

### Task 2.5: Test shared deadline across subtree

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_cancel_shared_deadline_across_subtree(mongo_db):
    """All entries created under one cancel sweep share the same monotonic deadline."""
    prefix = "p2t5"
    parent_running = asyncio.Event()
    child1_running = asyncio.Event()
    child2_running = asyncio.Event()

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        parent_running.set()
        async with ctx.parallel_group(survive_cancel=True) as group:
            await group.spawn(execute=long_child, process_id="c1", name="C1")
            child1_running.set()
            await group.spawn(execute=long_child, process_id="c2", name="C2")
            child2_running.set()

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    c1_inst = TaskInstance(execute=long_child, process_id="c1", name="C1")
    c2_inst = TaskInstance(execute=long_child, process_id="c2", name="C2")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=3.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, c1_inst, c2_inst])

    runner = asyncio.create_task(optio.launch("parent"))
    await parent_running.wait()
    await child1_running.wait()
    await child2_running.wait()
    await asyncio.sleep(0.1)

    await optio.cancel("parent")

    # Inspect deadlines in the executor's cancellation_flags map.
    entries = optio._executor._cancellation_flags
    deadlines = [entry.deadline for entry in entries.values() if entry.deadline is not None]
    assert len(deadlines) >= 3, f"expected ≥3 entries, got {len(deadlines)}"
    # All deadlines should be identical (shared deadline).
    assert all(d == deadlines[0] for d in deadlines), (
        f"deadlines diverge: {deadlines}"
    )

    await asyncio.wait_for(runner, timeout=10.0)
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_cancel_shared_deadline_across_subtree -v`
Expected: PASS. (The recursion already threads `inherit_deadline` after Task 2.2.) If FAIL with diverging deadlines, recheck `effective_deadline` computation in `cancel()`.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): cancel sweep shares a single monotonic deadline"
```

---

### Task 2.6: Test concurrent-cancel idempotency

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_cancel_concurrent_calls_are_idempotent(mongo_db):
    """Concurrent cancel(parent) calls do not corrupt state. One wins, others
    return not-cancellable."""
    prefix = "p2t6"
    parent_running = asyncio.Event()

    async def long_parent(ctx):
        parent_running.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    parent_inst = TaskInstance(execute=long_parent, process_id="parent", name="Parent")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst])

    runner = asyncio.create_task(optio.launch("parent"))
    await parent_running.wait()
    await asyncio.sleep(0.05)

    results = await asyncio.gather(
        optio.cancel("parent"),
        optio.cancel("parent"),
        optio.cancel("parent"),
    )
    ok_count = sum(1 for r in results if r.ok)
    not_cancellable_count = sum(
        1 for r in results if not r.ok and r.reason == "not-cancellable"
    )
    assert ok_count == 1, f"expected exactly one ok, got {ok_count}"
    assert ok_count + not_cancellable_count == 3

    await asyncio.wait_for(runner, timeout=3.0)
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    assert parent_proc["status"]["state"] == "cancelled"

    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_cancel_concurrent_calls_are_idempotent -v`
Expected: PASS. Mongo conditional state-transition is the serialization point.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): concurrent cancel calls are idempotent"
```

---

## Phase 3 — Refusal of `run_child` after parent cancel

### Task 3.1: Test refusal of `run_child` when auto-propagate parent is cancelled

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_run_child_refuses_after_parent_cancel_when_auto(mongo_db):
    """When parent has auto_cancel_children=True and its cancellation_flag
    is set, ctx.run_child returns 'cancelled' immediately without creating
    a child doc."""
    prefix = "p3t1"
    spawn_after_cancel = asyncio.Event()
    parent_observed_cancel = asyncio.Event()
    refusal_result: dict = {}

    async def short_child(ctx):
        ctx.report_progress(50, "doing")

    async def parent(ctx):
        # Wait for cancel.
        while ctx.should_continue():
            await asyncio.sleep(0.01)
        parent_observed_cancel.set()
        # Now flag is set; try to spawn another child.
        state = await ctx.run_child(
            execute=short_child, process_id="late_child", name="Late",
        )
        refusal_result["state"] = state
        spawn_after_cancel.set()

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_inst = TaskInstance(execute=short_child, process_id="late_child", name="Late")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, child_inst])

    runner = asyncio.create_task(optio.launch("parent"))
    await asyncio.sleep(0.1)

    await optio.cancel("parent")
    await spawn_after_cancel.wait()

    assert refusal_result["state"] == "cancelled"
    # No child doc should have been created for late_child.
    late_proc = await get_process_by_process_id(mongo_db, prefix, "late_child")
    assert late_proc is None, "no child doc should exist for refused run_child"

    await runner
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_run_child_refuses_after_parent_cancel_when_auto -v`
Expected: FAIL — `run_child` today does not check the flag at entry; a child doc is created.

---

### Task 3.2: Implement refusal in `ctx.run_child`

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py:295-336`

- [ ] **Step 1: Add the refusal logic at entry of `run_child`**

Edit `packages/optio-core/src/optio_core/context.py`. Replace the body of `run_child` (around line 295):

```python
async def run_child(
    self,
    execute: Callable[..., Awaitable[None]],
    process_id: str,
    name: str,
    params: dict[str, Any] | None = None,
    survive_failure: bool = False,
    survive_cancel: bool = False,
    on_child_progress: Callable | None = None,
    description: str | None = None,
) -> str:
    """Launch a sequential child process. Blocks until child completes.

    If the parent's cancellation_flag is set and the parent's TaskInstance
    has `auto_cancel_children=True` (the default), refuse: return
    `"cancelled"` without inserting a child doc. Tasks that opt out of
    auto-propagation may still spawn during their own cancel window.
    """
    if self._executor is None:
        raise RuntimeError("Executor not set on context")
    if self._cancellation_flag.is_set():
        # Refuse only when this task auto-propagates.
        task = self._executor._task_registry.get(self.process_id)
        auto = task.auto_cancel_children if task is not None else True
        if auto:
            from optio_core.store import append_log
            await append_log(
                self._db, self._prefix, self._process_oid,
                "event",
                f"Refused to spawn child '{name}' (process_id={process_id}): "
                f"parent already cancelled",
            )
            return "cancelled"
    if on_child_progress is not None:
        self._set_child_callback(on_child_progress)
    return await self._executor.execute_child(
        parent_ctx=self,
        execute=execute,
        process_id=process_id,
        name=name,
        params=params or {},
        survive_failure=survive_failure,
        survive_cancel=survive_cancel,
        description=description,
    )
```

Also extend `ParallelGroup.spawn` (around line 495) to apply the same refusal. Replace the body:

```python
async def spawn(
    self,
    execute: Callable[..., Awaitable[None]],
    process_id: str,
    name: str,
    params: dict[str, Any] | None = None,
    description: str | None = None,
) -> None:
    """Add a child to the group. Blocks if max_concurrency reached.

    Refuses (records 'cancelled' result, no DB doc) if the parent's
    cancellation_flag is set and the parent's TaskInstance has
    `auto_cancel_children=True`.
    """
    if self._ctx._cancellation_flag.is_set():
        task = self._ctx._executor._task_registry.get(self._ctx.process_id)
        auto = task.auto_cancel_children if task is not None else True
        if auto:
            from optio_core.store import append_log
            await append_log(
                self._ctx._db, self._ctx._prefix, self._ctx._process_oid,
                "event",
                f"Refused to spawn child '{name}' (process_id={process_id}): "
                f"parent already cancelled",
            )
            self._results.append(ChildResult(
                process_id=process_id, state="cancelled", error="parent cancelled",
            ))
            return
    await self._semaphore.acquire()

    async def _run():
        try:
            state = await self._ctx.run_child(
                execute=execute,
                process_id=process_id,
                name=name,
                params=params,
                survive_failure=True,
                survive_cancel=True,
                description=description,
            )
            self._results.append(ChildResult(
                process_id=process_id,
                state=state,
                error=None if state == "done" else f"Child {state}",
            ))
            if state == "failed" and not self._survive_failure:
                self._failed = True
            if state == "cancelled" and not self._survive_cancel:
                self._failed = True
        finally:
            self._semaphore.release()

    task = asyncio.create_task(_run())
    self._tasks.append(task)
```

- [ ] **Step 2: Run the new test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_run_child_refuses_after_parent_cancel_when_auto -v`
Expected: PASS.

- [ ] **Step 3: Run existing context tests for regressions**

Run: `pytest packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_parallel.py packages/optio-core/tests/test_child_progress.py -v`
Expected: all PASS. If any test fails with "refused to spawn", it is likely a pre-existing test that was triggering the new refusal path inadvertently — debug case by case.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/src/optio_core/context.py packages/optio-core/tests/test_cancel_propagation.py
git commit -m "feat(optio-core): run_child / parallel_group.spawn refuse after parent cancel under auto_cancel_children"
```

---

## Phase 4 — α: upward propagation channeled through `cancel`

### Task 4.1: Add `notify_parent_abnormal` callback wiring to Executor

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:36-52`

- [ ] **Step 1: Modify `Executor.__init__` to accept the callback**

Edit `packages/optio-core/src/optio_core/executor.py`. Replace the constructor:

```python
class Executor:
    """Executes task functions with lifecycle management."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        prefix: str,
        services: dict[str, Any],
        optio: "Optio | None" = None,
        notify_parent_abnormal: Callable[[str], Awaitable[None]] | None = None,
    ):
        self._db = db
        self._prefix = prefix
        self._services = services
        self._optio = optio
        self._notify_parent_abnormal = notify_parent_abnormal
        self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
        self._running_tasks: dict[ObjectId, asyncio.Task] = {}
        self._task_registry: dict[str, TaskInstance] = {}
```

- [ ] **Step 2: Wire the callback from `Optio.init`**

Edit `packages/optio-core/src/optio_core/lifecycle.py`. Find the `Executor(...)` construction (search `self._executor = Executor(`) and add the callback:

```python
self._executor = Executor(
    db=mongo_db,
    prefix=prefix,
    services=services,
    optio=self,
    notify_parent_abnormal=self.cancel,
)
```

(Adapt to the exact existing argument order; preserve all existing keyword args.)

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `pytest packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_lifecycle_reconciliation.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/src/optio_core/lifecycle.py
git commit -m "feat(optio-core): plumb notify_parent_abnormal callback executor->lifecycle"
```

---

### Task 4.2: Write α test (sibling auto-cancel on child cancel) — failing

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_alpha_child_cancel_triggers_parent_cancel_of_siblings(mongo_db):
    """α: when child cancels and parent has survive_cancel=False, parent's
    OTHER active children are also cancelled."""
    prefix = "p4t2"
    a_running = asyncio.Event()
    b_running = asyncio.Event()
    c_running = asyncio.Event()

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        a_running.set()
        async with ctx.parallel_group(survive_cancel=False) as group:
            await group.spawn(execute=long_child, process_id="b", name="B")
            b_running.set()
            await group.spawn(execute=long_child, process_id="c", name="C")
            c_running.set()

    parent_inst = TaskInstance(execute=parent, process_id="a", name="A")
    b_inst = TaskInstance(execute=long_child, process_id="b", name="B")
    c_inst = TaskInstance(execute=long_child, process_id="c", name="C")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, b_inst, c_inst])

    runner = asyncio.create_task(optio.launch("a"))
    await a_running.wait()
    await b_running.wait()
    await c_running.wait()
    await asyncio.sleep(0.1)

    # Cancel B directly — α should trigger cancel(A) which propagates to C.
    await optio.cancel("b")

    a_proc = await _wait_terminal(mongo_db, prefix, "a")
    b_proc = await _wait_terminal(mongo_db, prefix, "b")
    c_proc = await _wait_terminal(mongo_db, prefix, "c")
    assert b_proc["status"]["state"] == "cancelled"
    assert c_proc["status"]["state"] == "cancelled"
    assert a_proc["status"]["state"] == "cancelled"

    await runner
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_alpha_child_cancel_triggers_parent_cancel_of_siblings -v`
Expected: FAIL — α is not yet wired; C remains active or test times out.

---

### Task 4.3: Implement α invocation in `executor.execute_child`

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:228-269`

- [ ] **Step 1: Modify `execute_child` to invoke the callback**

Edit `packages/optio-core/src/optio_core/executor.py`. Replace the tail of `execute_child` (the post-execution conditional block):

```python
async def execute_child(
    self,
    parent_ctx: ProcessContext,
    execute: Callable[..., Awaitable[None]],
    process_id: str,
    name: str,
    params: dict,
    survive_failure: bool = False,
    survive_cancel: bool = False,
    description: str | None = None,
) -> str:
    """Execute a child process (called from ProcessContext.run_child)."""
    if self._optio is not None:
        self._optio._check_launch_blocks(parent_ctx.metadata)
    order = parent_ctx._next_child_order()

    child_doc = await create_child_process(
        self._db, self._prefix,
        parent_oid=parent_ctx._process_oid,
        root_oid=parent_ctx._root_oid,
        process_id=process_id,
        name=name,
        params=params,
        depth=parent_ctx._depth + 1,
        order=order,
        initial_state="scheduled",
        metadata=parent_ctx.metadata,
        description=description,
    )
    await append_log(
        self._db, self._prefix, parent_ctx._process_oid,
        "event", f"Spawned child: {name}",
    )

    end_state = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)

    if parent_ctx._on_child_progress is not None:
        parent_ctx._notify_child_state_change(process_id, end_state)

    # α: abnormal child terminal → trigger parent's downward propagation
    # by re-entering lifecycle.cancel via the callback. The callback is
    # idempotent for an already-cancel_requested parent.
    abnormal = (
        (end_state == "cancelled" and not survive_cancel)
        or (end_state == "failed" and not survive_failure)
    )
    if abnormal and self._notify_parent_abnormal is not None:
        # Fire-and-forget; do not await — the parent's downward sweep
        # cancels the OTHER siblings, but our own caller must continue.
        asyncio.create_task(
            self._notify_parent_abnormal(parent_ctx.process_id)
        )

    if end_state == "failed" and not survive_failure:
        raise RuntimeError(f"Child process '{name}' failed")
    if end_state == "cancelled" and not survive_cancel:
        parent_ctx._cancellation_flag.set()

    return end_state
```

- [ ] **Step 2: Run the α test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_alpha_child_cancel_triggers_parent_cancel_of_siblings -v`
Expected: PASS.

- [ ] **Step 3: Run existing executor tests for regression**

Run: `pytest packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_parallel.py packages/optio-core/tests/test_child_progress.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/tests/test_cancel_propagation.py
git commit -m "feat(optio-core): alpha — abnormal child terminal triggers parent cancel"
```

---

### Task 4.3b: α also fires from `ParallelGroup.spawn._run` on aggregate breach

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py:495-530` (the `ParallelGroup.spawn._run` body)

**Rationale:** `ParallelGroup.spawn` calls `ctx.run_child(survive_failure=True, survive_cancel=True, ...)` internally so each spawn task collects results rather than raising. As a result, the α path inside `executor.execute_child` (which only fires on survive_*=False) does not trigger for individual group children. To make `parallel_group(survive_failure=False)` fail-fast and `parallel_group(survive_cancel=False)` cancel-fast, fire α at the group level when its own survive_* aggregate is breached.

- [ ] **Step 1: Modify `ParallelGroup.spawn._run` to fire α on first aggregate breach**

Edit `packages/optio-core/src/optio_core/context.py`. Replace the `_run` body inside `ParallelGroup.spawn` (preserving the refusal block added in Task 3.2):

```python
async def _run():
    try:
        state = await self._ctx.run_child(
            execute=execute,
            process_id=process_id,
            name=name,
            params=params,
            survive_failure=True,
            survive_cancel=True,
            description=description,
        )
        self._results.append(ChildResult(
            process_id=process_id,
            state=state,
            error=None if state == "done" else f"Child {state}",
        ))
        breached = False
        if state == "failed" and not self._survive_failure:
            self._failed = True
            breached = True
        if state == "cancelled" and not self._survive_cancel:
            self._failed = True
            breached = True
        # α from group level: when group's own survive_* is breached,
        # invoke notify_parent_abnormal so the parent's cancel propagation
        # reaches sibling spawns.
        if breached:
            executor = self._ctx._executor
            if executor is not None and executor._notify_parent_abnormal is not None:
                asyncio.create_task(
                    executor._notify_parent_abnormal(self._ctx.process_id)
                )
    finally:
        self._semaphore.release()
```

- [ ] **Step 2: Run the α test for parallel_group siblings**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_alpha_child_cancel_triggers_parent_cancel_of_siblings -v`
Expected: PASS. Note: the test uses `parallel_group(survive_cancel=False)` — group-level breach triggers α → parent gets cancel_requested → recursion cancels C.

Important: under this combined path the parent's `__aexit__` may still raise `RuntimeError("Parallel group failed")` because `_failed` is True. That means parent's final state is `failed` (not `cancelled`). Update the test assertion accordingly:

Edit the test in `packages/optio-core/tests/test_cancel_propagation.py`:

```python
    assert b_proc["status"]["state"] == "cancelled"
    assert c_proc["status"]["state"] == "cancelled"
    assert a_proc["status"]["state"] in {"failed", "cancelled"}
```

- [ ] **Step 3: Run the parallel_group fail-fast test (anticipating Task 4.4)**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_parallel_group_fail_fast_under_alpha -v` if it exists yet. (May not — that test is added in Task 4.4.)

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/src/optio_core/context.py packages/optio-core/tests/test_cancel_propagation.py
git commit -m "feat(optio-core): ParallelGroup fires alpha on aggregate survive_* breach"
```

---

### Task 4.4: Test parallel_group fail-fast under α

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_parallel_group_fail_fast_under_alpha(mongo_db):
    """parallel_group(survive_failure=False): one child failing auto-cancels
    siblings via α, rather than waiting for them to finish."""
    prefix = "p4t4"
    started = asyncio.Event()

    async def quick_fail(ctx):
        await asyncio.sleep(0.05)
        raise RuntimeError("kaboom")

    async def long_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent(ctx):
        started.set()
        async with ctx.parallel_group(survive_failure=False) as group:
            await group.spawn(execute=quick_fail, process_id="b", name="B")
            await group.spawn(execute=long_child, process_id="c", name="C")

    parent_inst = TaskInstance(execute=parent, process_id="a", name="A")
    b_inst = TaskInstance(execute=quick_fail, process_id="b", name="B")
    c_inst = TaskInstance(execute=long_child, process_id="c", name="C")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=3.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, b_inst, c_inst])

    t0 = _time.monotonic()
    runner = asyncio.create_task(optio.launch("a"))
    await started.wait()
    await runner
    elapsed = _time.monotonic() - t0

    # Under fail-fast α, C is cancelled shortly after B fails. Total
    # runtime should be well under cancel_grace_seconds.
    assert elapsed < 2.0, f"expected fail-fast, took {elapsed:.2f}s"

    b_proc = await get_process_by_process_id(mongo_db, prefix, "b")
    c_proc = await get_process_by_process_id(mongo_db, prefix, "c")
    a_proc = await get_process_by_process_id(mongo_db, prefix, "a")
    assert b_proc["status"]["state"] == "failed"
    assert c_proc["status"]["state"] == "cancelled"
    assert a_proc["status"]["state"] in {"failed", "cancelled"}

    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_parallel_group_fail_fast_under_alpha -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): parallel_group(survive_failure=False) is now fail-fast"
```

---

## Phase 5 — Force-cancel cascade

### Task 5.1: Test force-cancel cascade on auto-propagate path

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_force_cancel_cascade_auto_propagate(mongo_db):
    """Stubborn child that ignores should_continue gets force-cancelled
    when grace expires; parent also force-cancelled."""
    prefix = "p5t1"
    parent_started = asyncio.Event()
    child_started = asyncio.Event()

    async def stubborn_child(ctx):
        child_started.set()
        # Ignore should_continue — only break on asyncio.CancelledError.
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    async def parent(ctx):
        parent_started.set()
        await ctx.run_child(
            execute=stubborn_child, process_id="stub", name="Stub",
        )

    parent_inst = TaskInstance(execute=parent, process_id="parent", name="Parent")
    child_inst = TaskInstance(execute=stubborn_child, process_id="stub", name="Stub")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=0.5)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, child_inst])

    # Start supervisor loop.
    optio._running = True
    optio._supervisor_task = asyncio.create_task(optio._supervisor_loop())

    runner = asyncio.create_task(optio.launch("parent"))
    await parent_started.wait()
    await child_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")

    # Wait for force-cancel cascade to land (grace + supervisor scan + cascade time).
    await asyncio.wait_for(runner, timeout=5.0)
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    child_proc = await _wait_terminal(mongo_db, prefix, "stub")
    # Stubborn child reaches terminal 'failed' (force-cancel canonical state)
    # OR 'cancelled' if it broke earlier than expected.
    assert parent_proc["status"]["state"] in {"failed", "cancelled"}
    assert child_proc["status"]["state"] in {"failed", "cancelled"}

    optio._running = False
    if optio._supervisor_task:
        optio._supervisor_task.cancel()
        try:
            await optio._supervisor_task
        except asyncio.CancelledError:
            pass
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test to verify it (partially) fails**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_force_cancel_cascade_auto_propagate -v`
Expected outcome depends on timing. Under shared-deadline + supervisor M1, both entries are in `_cancellation_flags` with the same deadline, so the supervisor force-cancels both individually. The test may PASS even before the recursive cascade lands, because supervisor M1 already handles it. If PASS, that's fine — proceed; the recursive cascade is still needed for the opt-out path (Task 5.3).

If FAIL, capture the failure mode in the commit message.

---

### Task 5.2: Implement recursive `executor.force_cancel`

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:288-309`

- [ ] **Step 1: Modify `executor.force_cancel` to recurse over direct active children**

Edit `packages/optio-core/src/optio_core/executor.py`. Replace `force_cancel`:

```python
async def force_cancel(self, oid: ObjectId) -> None:
    """Hard-cancel a process whose cooperative deadline has expired.

    Calls Task.cancel() on the tracked asyncio Task, awaits a bounded
    unwind, then writes the conditional 'failed' terminal state to Mongo
    via _write_force_cancelled_state. After the local terminal write,
    cascade unconditionally to direct active children — captures both
    auto-propagate descendants (already in supervisor map; idempotent)
    and opt-out descendants (only this cascade reaches them).
    """
    from optio_core._force_cancel import _write_force_cancelled_state
    from optio_core.store import list_direct_children

    task = self._running_tasks.get(oid)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
    await _write_force_cancelled_state(self._db, self._prefix, oid)

    # Cascade to direct active children. Unconditional — force is force.
    from optio_core.state_machine import ACTIVE_STATES
    children = await list_direct_children(
        self._db, self._prefix, oid, states=ACTIVE_STATES,
    )
    if children:
        await asyncio.gather(
            *(self.force_cancel(c["_id"]) for c in children),
            return_exceptions=True,
        )
```

- [ ] **Step 2: Re-run the cascade tests**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_force_cancel_cascade_auto_propagate -v`
Expected: PASS.

Also re-run the previously-skipped opt-out tests (remove `@pytest.mark.skip` if it was added):
Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_cancel_optout_does_not_auto_cancel_children packages/optio-core/tests/test_cancel_propagation.py::test_cancel_recursion_honors_per_level_optout -v`
Expected: PASS now (force-cancel cascade rescues C and other opt-out descendants).

- [ ] **Step 3: Run existing force-cancel / deadline-cancel tests**

Run: `pytest packages/optio-core/tests/test_deadline_cancel.py packages/optio-core/tests/test_deadline_cancel_launchguard.py -v`
Expected: all PASS. If any test asserts terminal state of a non-descendant process, no behavior change there. If a test indirectly created a parent-child relationship where the new cascade now reaches additional rows, update the assertions to match.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/tests/test_cancel_propagation.py
git commit -m "feat(optio-core): force_cancel cascades to direct active children"
```

---

### Task 5.3: Test force-cancel cascade on opt-out path

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_force_cancel_cascade_optout_path(mongo_db):
    """Opt-out parent: cancel does not propagate; after grace, force-cancel
    cascade catches the children."""
    prefix = "p5t3"
    parent_started = asyncio.Event()
    child_started = asyncio.Event()

    async def long_child(ctx):
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    async def parent(ctx):
        parent_started.set()
        async with ctx.parallel_group(survive_cancel=True, survive_failure=True) as group:
            await group.spawn(execute=long_child, process_id="b", name="B")
            child_started.set()
            # Parent does not cancel B; let force-cancel catch it.
            try:
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise

    parent_inst = TaskInstance(
        execute=parent, process_id="parent", name="Parent",
        auto_cancel_children=False,
    )
    b_inst = TaskInstance(execute=long_child, process_id="b", name="B")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=0.5)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, b_inst])
    optio._running = True
    optio._supervisor_task = asyncio.create_task(optio._supervisor_loop())

    runner = asyncio.create_task(optio.launch("parent"))
    await parent_started.wait()
    await child_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")

    # Immediately after cancel: B still active.
    b_proc = await get_process_by_process_id(mongo_db, prefix, "b")
    assert b_proc["status"]["state"] in {"running", "scheduled"}

    # After grace + cascade, B should be terminal.
    await asyncio.wait_for(runner, timeout=5.0)
    b_proc = await _wait_terminal(mongo_db, prefix, "b")
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    assert b_proc["status"]["state"] == "failed"  # force-cancel canonical state
    assert parent_proc["status"]["state"] == "failed"

    optio._running = False
    if optio._supervisor_task:
        optio._supervisor_task.cancel()
        try:
            await optio._supervisor_task
        except asyncio.CancelledError:
            pass
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_force_cancel_cascade_optout_path -v`
Expected: PASS — the cascade in `force_cancel` reaches B even though B was never in supervisor's map.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): force-cancel cascade catches opt-out descendants"
```

---

### Task 5.4: Test cascade catches child spawned during opt-out window

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_force_cancel_cascade_catches_late_optout_child(mongo_db):
    """Opt-out parent spawns a NEW child after cancel arrives. Force-cancel
    cascade walks the DB at force time and catches it."""
    prefix = "p5t4"
    parent_started = asyncio.Event()
    late_child_spawned = asyncio.Event()

    async def late_child(ctx):
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    async def parent(ctx):
        parent_started.set()
        # Wait for cancel to arrive.
        while ctx.should_continue():
            await asyncio.sleep(0.01)
        # Opt-out window: still allowed to spawn. Use parallel_group to
        # exercise the spawn API while parent's flag is already set.
        async with ctx.parallel_group(survive_cancel=True, survive_failure=True) as group:
            await group.spawn(
                execute=late_child, process_id="late", name="Late",
            )
            late_child_spawned.set()
            try:
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise

    parent_inst = TaskInstance(
        execute=parent, process_id="parent", name="Parent",
        auto_cancel_children=False,
    )
    late_inst = TaskInstance(execute=late_child, process_id="late", name="Late")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=0.5)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, late_inst])
    optio._running = True
    optio._supervisor_task = asyncio.create_task(optio._supervisor_loop())

    runner = asyncio.create_task(optio.launch("parent"))
    await parent_started.wait()
    await asyncio.sleep(0.05)

    await optio.cancel("parent")
    await late_child_spawned.wait()

    # Within force-cancel window, late child still exists in DB and is active.
    late_proc = await get_process_by_process_id(mongo_db, prefix, "late")
    assert late_proc is not None
    assert late_proc["status"]["state"] in {"running", "scheduled"}

    # After grace + cascade, late child reaches terminal.
    await asyncio.wait_for(runner, timeout=5.0)
    late_proc = await _wait_terminal(mongo_db, prefix, "late")
    assert late_proc["status"]["state"] == "failed"

    optio._running = False
    if optio._supervisor_task:
        optio._supervisor_task.cancel()
        try:
            await optio._supervisor_task
        except asyncio.CancelledError:
            pass
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_force_cancel_cascade_catches_late_optout_child -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_cancel_propagation.py
git commit -m "test(optio-core): force-cancel cascade catches children spawned in opt-out window"
```

---

## Phase 6 — Orphan safety net [DROPPED 2026-05-12]

**Status: dropped.** Reason: the scenario the safety net was meant to handle (opt-out parent's execute fn raises mid-cancel-handling while children still active) cannot actually happen with the current `parallel_group` implementation. `parallel_group.__aexit__` blocks on `await asyncio.gather(*tasks, ...)` even when an exception is propagating, which means a parent cannot exit its execute body while children are still alive.

The orphan cleanup is fully covered by Phase 5's force-cancel cascade (Task 5.2): when the parent's grace expires and `force_cancel(parent)` runs, the recursive cascade catches every active descendant in DB regardless of how they got there. Tests 5.3 and 5.4 verify this explicitly for the opt-out path and the late-spawn case.

If `parallel_group.__aexit__` is ever changed to fail-fast on exception, revisit this and add the safety net as a finer-grained backup before the force-cancel cascade.

### Task 6.1 (dropped): Test orphan safety net (opt-out parent fails mid-handling)

**Files:**
- Modify: `packages/optio-core/tests/test_cancel_propagation.py`

- [ ] **Step 1: Append the test**

```python
async def test_orphan_safety_net_when_parent_fails(mongo_db):
    """Opt-out parent spawns a child, then raises before cleaning it up.
    The end-of-execute safety net cancels the still-active direct child."""
    prefix = "p6t1"
    parent_started = asyncio.Event()
    child_started = asyncio.Event()

    async def long_child(ctx):
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    async def parent(ctx):
        parent_started.set()
        async with ctx.parallel_group(survive_cancel=True, survive_failure=True) as group:
            await group.spawn(execute=long_child, process_id="b", name="B")
            child_started.set()
            await asyncio.sleep(0.05)
            raise RuntimeError("parent kaboom")

    parent_inst = TaskInstance(
        execute=parent, process_id="parent", name="Parent",
        auto_cancel_children=False,
    )
    b_inst = TaskInstance(execute=long_child, process_id="b", name="B")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix, cancel_grace_seconds=2.0)
    await upsert_process(mongo_db, prefix, parent_inst)
    optio._executor.register_tasks([parent_inst, b_inst])

    runner = asyncio.create_task(optio.launch("parent"))
    await parent_started.wait()
    await child_started.wait()

    await runner
    parent_proc = await _wait_terminal(mongo_db, prefix, "parent")
    b_proc = await _wait_terminal(mongo_db, prefix, "b")
    assert parent_proc["status"]["state"] == "failed"
    assert b_proc["status"]["state"] in {"cancelled", "failed"}

    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_orphan_safety_net_when_parent_fails -v`
Expected: FAIL — without the safety net, B stays running after parent fails; the test will time out at `_wait_terminal(b)`.

---

### Task 6.2: Implement orphan safety net in `_execute_process`

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:111-226`

- [ ] **Step 1: Add safety net at end of `_execute_process`**

Edit `packages/optio-core/src/optio_core/executor.py`. In `_execute_process`, just before the `finally:` block (after the `clear_widget_upstream` + `cleanup_ephemeral` calls), add the safety net. Concretely, the structure becomes:

```python
async def _execute_process(
    self, proc: dict, execute_fn: Callable | None,
    parent_ctx: ProcessContext | None = None,
    resume: bool = False,
) -> str:
    """Execute a process."""
    oid = proc["_id"]
    root_oid = proc.get("rootId", oid)
    ttl_seconds = proc.get("ttlSeconds")

    cancel_flag = asyncio.Event()
    self._cancellation_flags[oid] = _CancelEntry(flag=cancel_flag, deadline=None)
    current = asyncio.current_task()
    if current is None:
        raise RuntimeError("_execute_process must be called from within an asyncio Task")
    self._running_tasks[oid] = current

    end_state: str = "done"
    try:
        # ... existing body unchanged: state-machine writes, execute_fn,
        #     exception handling, terminal-state writes, clear_widget_upstream,
        #     cleanup_ephemeral. Capture the value being returned into end_state.

        # (existing code from current implementation goes here. Make sure
        #  every `return` is preceded by `end_state = <state>`, so the
        #  safety-net branch in the finally has visibility.)

        # At each return point, assign end_state before returning.
        # e.g. before `return "failed"`: end_state = "failed"
        #      before `return end_state`: pass (already set)
        return end_state
    finally:
        # Orphan safety net: if this process ended non-`done` and has
        # still-active direct children in the DB (typically only the
        # opt-out path), cooperative-cancel them so they do not orphan.
        if end_state != "done":
            try:
                from optio_core.store import list_direct_children
                from optio_core.state_machine import ACTIVE_STATES
                live = await list_direct_children(
                    self._db, self._prefix, oid, states=ACTIVE_STATES,
                )
                if live and self._notify_parent_abnormal is not None:
                    # Reuse the same callback used by α — cooperative cancel
                    # each direct active child via lifecycle.cancel. They
                    # inherit a fresh deadline because the parent's deadline
                    # has already elapsed by definition.
                    await asyncio.gather(
                        *(
                            self._notify_parent_abnormal(c["processId"])
                            for c in live
                        ),
                        return_exceptions=True,
                    )
            except Exception:  # noqa: BLE001
                # Safety net must not mask the original terminal state.
                pass
        self._cancellation_flags.pop(oid, None)
        self._running_tasks.pop(oid, None)
```

When editing, preserve every existing line of `_execute_process` and merely wrap the return paths so `end_state` is set. The minimal changes:

- Initialize `end_state: str = "done"` at the top of the `try`.
- Before every `return <something>`: set `end_state = <something>`.
- Add the safety-net block inside the existing `finally`.

- [ ] **Step 2: Run the safety-net test**

Run: `pytest packages/optio-core/tests/test_cancel_propagation.py::test_orphan_safety_net_when_parent_fails -v`
Expected: PASS.

- [ ] **Step 3: Run executor regression suite**

Run: `pytest packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_parallel.py packages/optio-core/tests/test_child_progress.py packages/optio-core/tests/test_engine_service.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/tests/test_cancel_propagation.py
git commit -m "feat(optio-core): orphan safety net for opt-out parents that fail mid-handling"
```

---

## Phase 7 — Documentation

### Task 7.1: Update root `AGENTS.md`

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update the cancel section and the TaskInstance section**

Open `/home/csillag/deai/optio/AGENTS.md`. Locate two areas:

a) The line documenting `cancel`:

```
await optio_core.cancel(process_id: str) -> None
```

Replace with:

```
await optio_core.cancel(process_id: str) -> None
# Cancels the named process. Recursively cancels active direct descendants
# whose TaskInstance has `auto_cancel_children=True` (default). Opt-out
# parents handle their own children's shutdown. Force-cancel after grace
# always cascades through the subtree.
```

b) The `TaskInstance` field list (around line 153 — the block describing fields like `cancellable: bool = True`). Add `auto_cancel_children`:

```
    cancellable: bool = True                 # whether this process can be cancelled
    auto_cancel_children: bool = True        # cancel of this process auto-cancels active direct descendants
```

c) The state-machine narrative section (the block around line 332 with the state table). After the existing CANCELLABLE_STATES block, add a short paragraph:

```
## Cancel propagation

`cancel(p)` recursively cancels active direct descendants when p's
TaskInstance has `auto_cancel_children=True` (default). Setting it to
False makes p responsible for cleaning up its own subtree within the
cancel grace budget. Force-cancel (the post-grace path) always
cascades through the live subtree regardless of the flag.

Upward propagation: when a child terminates abnormally (cancelled with
`survive_cancel=False`, or failed with `survive_failure=False`), the
executor invokes `cancel(parent)` so the parent's other active children
are cancelled too. This is implemented via a `notify_parent_abnormal`
callback bound to `lifecycle.cancel`.

The cancel grace deadline is shared across the subtree: descendants
inherit the root cancel's monotonic deadline rather than starting a
fresh clock at each level.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): document cancel propagation and auto_cancel_children"
```

---

### Task 7.2: Update `packages/optio-core/AGENTS.md`

**Files:**
- Modify: `packages/optio-core/AGENTS.md`

- [ ] **Step 1: Read the existing file**

Open `packages/optio-core/AGENTS.md`. Find the section describing `cancel` / `force_cancel` semantics (likely near a table of public methods or a state-machine block).

- [ ] **Step 2: Add or update the relevant text**

Wherever the file describes `lifecycle.cancel`, add:

```
`cancel` recurses to active direct children of the cancelled process
when its TaskInstance has `auto_cancel_children=True` (default).
Recursion threads the same monotonic deadline through every level so
descendants share one grace budget. Internal callers pass
`inherit_deadline=...`; external callers omit it.
```

Wherever the file describes `executor.force_cancel`, add:

```
`force_cancel` recursively walks direct active children in DB and
force-cancels each. Unconditional — no flag check. Idempotent at every
touchpoint: `task.cancel()` is idempotent, `_write_force_cancelled_state`
is a conditional Mongo update on `state in ACTIVE_STATES`.
```

Wherever the file describes `TaskInstance` fields, add the row:

```
auto_cancel_children   bool   default True   Cancel of this process auto-cancels its active direct descendants.
```

If the file lacks these sections, append them under appropriately-titled subheadings at the bottom.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/AGENTS.md
git commit -m "docs(optio-core): document cancel/force_cancel recursion and auto_cancel_children"
```

---

## Phase 8 — Regression sweep

### Task 8.1: Run the full optio-core test suite

**Files:**
- (No edits — verification step.)

- [ ] **Step 1: Run the entire test suite**

Run: `cd packages/optio-core && pytest -v`
Expected: ALL tests pass. Particular attention to:

- `test_executor.py` — child cancellation paths now affected by α.
- `test_parallel.py` — fail-fast under α visible here.
- `test_deadline_cancel.py` — force-cancel cascade extends behavior.
- `test_group_cancel.py` — `group_cancel` calls `cancel` per match; each now propagates downward, but `group_cancel`'s tests use flat lists, so behavior should match.
- `test_lifecycle_reconciliation.py` — reconciliation does not involve the new path; should be unaffected.
- `test_resync_cancel_stale.py` — uses `_cancel_stale_processes`, which calls `cancel`; downward propagation applies but stale processes have no live children, so behavior should match.

If anything fails:

a) Inspect the failure. If it is a behavior change explicitly anticipated by this spec (e.g., a test that relied on `parallel_group(survive_failure=False)` waiting for all siblings), update the test to match the new semantics and note it in the commit message.

b) If it is an unexpected failure, debug — do not blanket-skip.

- [ ] **Step 2: Run excavator integration tests where applicable**

Run from `/home/csillag/deai/excavator`:

```bash
pytest packages/engine -v -x -k "not slow"
```

Expected: PASS, with the caveat that excavator integration tests may not be runnable locally without additional services. If they are not runnable, document that and proceed.

- [ ] **Step 3: Commit any test updates**

If tests were updated to match new semantics:

```bash
git add <updated test files>
git commit -m "test(optio-core): update tests for new cancel-propagation semantics"
```

If no updates were needed, no commit.

---

## Done

All phases complete. The branch should now contain:

1. `TaskInstance.auto_cancel_children: bool = True` (Phase 1).
2. `store.list_direct_children` helper (Phase 1).
3. `lifecycle.cancel` recursive over active children, shared deadline (Phase 2).
4. `ctx.run_child` / `parallel_group.spawn` refuse after parent cancel under auto (Phase 3).
5. `executor.execute_child` invokes `notify_parent_abnormal` callback for abnormal child terminal (Phase 4).
6. `executor.force_cancel` recurses to direct active children unconditionally (Phase 5).
7. `executor._execute_process` orphan safety net for non-`done` end states (Phase 6).
8. Documentation updates in root and package `AGENTS.md` (Phase 7).
9. Full suite green (Phase 8).

Spec coverage check (against `docs/2026-05-11-cancel-propagation-design.md`):

- Direction 1 (downward) — Phase 2.
- Direction 2 (upward α) — Phase 4.
- Direction 3 (opt-out) — Phase 1 (flag) + Phase 3 (allowance) + Phase 5 (cascade rescues).
- Direction 4 (force cascade) — Phase 5.
- Shared deadline — Task 2.2 + Task 2.5.
- API surface table — fully covered across phases.
- Race & edge cases R1–R9 — covered by tests 2.6 (R1, R2), 5.1+5.3 (R3, R4), 3.1 (R5), 5.4 (R6), default-True path in `cancel()` (R7), 2.4 (R8), 6.1 (R9).
- Behavior changes — Task 4.4 (fail-fast), shared deadline tested in 2.5.

If a spec section is not covered by any task, add the task in this plan before starting execution.
