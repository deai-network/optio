# Test Application Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Python test application that exercises every optio-core task/process feature, observable via optio-dashboard.

**Architecture:** A standalone Python application in `examples/test-app/` with task definitions grouped by feature area. Each task is a `TaskInstance` that demonstrates one or more features from the feature catalog. The app connects to MongoDB + Redis and runs alongside optio-dashboard for visual verification.

**Tech Stack:** Python 3.11+, optio-core, motor, redis

**Spec:** `docs/superpowers/specs/2026-03-28-optio-core-feature-catalog.md`

---

## File Structure

```
examples/test-app/
  main.py                  # Entry point: parse env, init optio, run
  tasks/
    __init__.py            # get_task_definitions() — assembles all TaskInstances
    basic.py               # Params, metadata, warning, special, non-cancellable, services
    progress.py            # Progress reporting: variants, indeterminate, all 3 helpers
    children.py            # Sequential, parallel, nested, mixed children
    cancellation.py        # Cooperative, ignored, parent+child cancellation
    errors.py              # Simple failure, cascading, parallel failures, survive_failure
    adhoc_ephemeral.py     # Ad-hoc define/delete, ephemeral, mark_ephemeral
    scheduled.py           # Cron-scheduled task
```

Each `tasks/*.py` file exports a function `get_tasks(services) -> list[TaskInstance]` that returns the tasks for that feature area. `tasks/__init__.py` assembles them all.

---

### Task 1: Scaffold the application

**Files:**
- Create: `examples/test-app/main.py`
- Create: `examples/test-app/tasks/__init__.py`

- [ ] **Step 1: Create main.py**

This is the entry point. It reads env vars, connects to MongoDB and Redis, initializes optio, and runs.

```python
"""Optio test application — exercises all optio-core features."""

import asyncio
import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from optio_core.lifecycle import Optio

from tasks import get_task_definitions

logging.basicConfig(level=logging.INFO)


async def main():
    mongo_url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/optio-test-app")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    prefix = os.environ.get("OPTIO_PREFIX", "optio")

    # Parse DB name from URL (last path segment)
    db_name = mongo_url.rsplit("/", 1)[-1]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    fw = Optio()
    await fw.init(
        mongo_db=db,
        prefix=prefix,
        redis_url=redis_url,
        services={"mongo_db": db, "test_secret": "optio-test-secret-42"},
        get_task_definitions=get_task_definitions,
    )

    await fw.run()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create tasks/__init__.py**

Assembles tasks from all modules. Each module's `get_tasks()` receives the services dict.

```python
"""Task definitions for the optio test application."""

from optio_core.models import TaskInstance

from tasks.basic import get_tasks as basic_tasks
from tasks.progress import get_tasks as progress_tasks
from tasks.children import get_tasks as children_tasks
from tasks.cancellation import get_tasks as cancellation_tasks
from tasks.errors import get_tasks as errors_tasks
from tasks.adhoc_ephemeral import get_tasks as adhoc_ephemeral_tasks
from tasks.scheduled import get_tasks as scheduled_tasks


async def get_task_definitions(services: dict) -> list[TaskInstance]:
    return [
        *basic_tasks(),
        *progress_tasks(),
        *children_tasks(),
        *cancellation_tasks(),
        *errors_tasks(),
        *adhoc_ephemeral_tasks(services),
        *scheduled_tasks(),
    ]
```

- [ ] **Step 3: Create stub files for all task modules**

Create each `tasks/*.py` with an empty `get_tasks()` function so the app can start before we fill them in.

```python
# tasks/basic.py
"""Basic feature tasks: params, metadata, warning, special, non-cancellable, services."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

```python
# tasks/progress.py
"""Progress reporting tasks: variants, indeterminate, sequential/average/mapped helpers."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

```python
# tasks/children.py
"""Child process tasks: sequential, parallel, nested, mixed."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

```python
# tasks/cancellation.py
"""Cancellation tasks: cooperative, ignored, parent+child propagation."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

```python
# tasks/errors.py
"""Error handling tasks: simple failure, cascading, parallel failures, survive_failure."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

```python
# tasks/adhoc_ephemeral.py
"""Ad-hoc and ephemeral process tasks."""
from optio_core.models import TaskInstance

def get_tasks(services: dict) -> list[TaskInstance]:
    return []
```

```python
# tasks/scheduled.py
"""Cron-scheduled tasks."""
from optio_core.models import TaskInstance

def get_tasks() -> list[TaskInstance]:
    return []
```

- [ ] **Step 4: Verify the app starts**

Run: `cd examples/test-app && PYTHONPATH=../../packages/optio-core/src python main.py`

Expected: App starts, logs "Optio initialized", and waits for commands. Press Ctrl+C to stop.

- [ ] **Step 5: Commit**

```bash
git add examples/test-app/
git commit -m "feat: scaffold optio test application"
```

---

### Task 2: Basic feature tasks

**Files:**
- Modify: `examples/test-app/tasks/basic.py`

These tasks exercise: params, metadata inheritance, warning, special flag, non-cancellable, and services access.

- [ ] **Step 1: Write basic.py**

```python
"""Basic feature tasks: params, metadata, warning, special, non-cancellable, services."""

import asyncio
from optio_core.models import TaskInstance, CancellationConfig


async def _params_task(ctx):
    """Reads ctx.params["count"] and loops that many times, reporting progress."""
    count = ctx.params["count"]
    for i in range(count):
        pct = (i + 1) / count * 100
        ctx.report_progress(pct, f"Step {i + 1} of {count}")
        await asyncio.sleep(0.5)


async def _metadata_task(ctx):
    """Has metadata, spawns a child to verify inheritance."""

    async def _child(child_ctx):
        # Child reads its metadata — should match parent's
        meta = child_ctx.metadata
        child_ctx.report_progress(50, f"Child sees metadata: {meta}")
        await asyncio.sleep(0.3)
        child_ctx.report_progress(100, "Child done")

    ctx.report_progress(10, f"Parent metadata: {ctx.metadata}")
    await ctx.run_child(_child, "metadata-demo-child", "Metadata Child")
    ctx.report_progress(100, "Parent done — check child's logs for inherited metadata")


async def _warning_task(ctx):
    """Simple task with a warning string. Run it and verify the UI shows the warning."""
    ctx.report_progress(None, "Working despite the warning...")
    await asyncio.sleep(2)
    ctx.report_progress(100, "Done")


async def _special_task(ctx):
    """Task with special=True. Verify the flag in the DB."""
    ctx.report_progress(100, "Special task ran")


async def _non_cancellable_task(ctx):
    """Non-cancellable task. Dashboard should hide the cancel button."""
    for i in range(10):
        ctx.report_progress((i + 1) * 10, f"Step {i + 1}")
        await asyncio.sleep(0.5)


async def _services_task(ctx):
    """Reads ctx.services and logs values."""
    secret = ctx.services.get("test_secret", "NOT FOUND")
    has_db = "mongo_db" in ctx.services
    ctx.report_progress(50, f"test_secret = {secret}")
    ctx.report_progress(100, f"mongo_db present = {has_db}")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_params_task,
            process_id="basic-params",
            name="Basic: Params Demo",
            params={"count": 5},
        ),
        TaskInstance(
            execute=_metadata_task,
            process_id="basic-metadata",
            name="Basic: Metadata Inheritance",
            metadata={"category": "import", "priority": "high"},
        ),
        TaskInstance(
            execute=_warning_task,
            process_id="basic-warning",
            name="Basic: Warning Demo",
            warning="This process takes a long time",
        ),
        TaskInstance(
            execute=_special_task,
            process_id="basic-special",
            name="Basic: Special Flag",
            special=True,
        ),
        TaskInstance(
            execute=_non_cancellable_task,
            process_id="basic-non-cancellable",
            name="Basic: Non-Cancellable",
            cancellation=CancellationConfig(cancellable=False),
        ),
        TaskInstance(
            execute=_services_task,
            process_id="basic-services",
            name="Basic: Services Access",
        ),
    ]
```

- [ ] **Step 2: Verify**

Start the app, launch each task from the dashboard. Verify:
- `basic-params`: runs 5 iterations with progress messages
- `basic-metadata`: child logs show inherited metadata `{"category": "import", "priority": "high"}`
- `basic-warning`: UI shows warning string
- `basic-special`: process record has `special: true`
- `basic-non-cancellable`: no cancel button in dashboard
- `basic-services`: logs show `test_secret = optio-test-secret-42` and `mongo_db present = True`

- [ ] **Step 3: Commit**

```bash
git add examples/test-app/tasks/basic.py
git commit -m "feat(test-app): add basic feature tasks"
```

---

### Task 3: Progress reporting tasks

**Files:**
- Modify: `examples/test-app/tasks/progress.py`

These tasks exercise: progress with/without message, indeterminate progress, and all three progress helpers (sequential, average, mapped).

- [ ] **Step 1: Write progress.py**

```python
"""Progress reporting tasks: variants, indeterminate, sequential/average/mapped helpers."""

import asyncio
from optio_core.models import TaskInstance
from optio_core.progress_helpers import sequential_progress, average_progress, mapped_progress


async def _progress_variants(ctx):
    """Demonstrates all four progress reporting variants."""
    ctx.report_progress(25, "With message at 25%")      # percent + log entry
    await asyncio.sleep(0.5)
    ctx.report_progress(50)                               # percent only, no log
    await asyncio.sleep(0.5)
    ctx.report_progress(None, "Indeterminate + message")  # indeterminate + log
    await asyncio.sleep(0.5)
    ctx.report_progress(None)                             # indeterminate, no log
    await asyncio.sleep(0.5)
    ctx.report_progress(100, "Done — check logs: should only have messages at 25% and indeterminate")


async def _child_work(ctx):
    """Generic child that reports progress 0 to 100 in steps."""
    steps = ctx.params.get("steps", 5)
    delay = ctx.params.get("delay", 0.3)
    for i in range(steps):
        pct = (i + 1) / steps * 100
        ctx.report_progress(pct, f"Step {i + 1}/{steps}")
        await asyncio.sleep(delay)


async def _sequential_progress_demo(ctx):
    """3 sequential children with sequential_progress helper.
    Parent progress should advance in 3 equal segments."""
    cb = sequential_progress(ctx, 3)
    await ctx.run_child(_child_work, "seq-prog-c1", "Step 1 of 3",
                        params={"steps": 4, "delay": 0.3}, on_child_progress=cb)
    await ctx.run_child(_child_work, "seq-prog-c2", "Step 2 of 3",
                        params={"steps": 4, "delay": 0.3}, on_child_progress=cb)
    await ctx.run_child(_child_work, "seq-prog-c3", "Step 3 of 3",
                        params={"steps": 4, "delay": 0.3}, on_child_progress=cb)


async def _average_progress_demo(ctx):
    """4 parallel children with average_progress helper.
    Parent progress = average of all children."""
    cb = average_progress(ctx)
    async with ctx.parallel_group(max_concurrency=4, on_child_progress=cb) as group:
        await group.spawn(_child_work, "avg-prog-c1", "Fast Worker",
                          params={"steps": 3, "delay": 0.2})
        await group.spawn(_child_work, "avg-prog-c2", "Medium Worker",
                          params={"steps": 5, "delay": 0.3})
        await group.spawn(_child_work, "avg-prog-c3", "Slow Worker",
                          params={"steps": 8, "delay": 0.4})
        await group.spawn(_child_work, "avg-prog-c4", "Very Slow Worker",
                          params={"steps": 10, "delay": 0.3})


async def _mapped_progress_demo(ctx):
    """Phased work: child A (0-30%), manual (30-70%), child B (70-100%)."""
    cb1 = mapped_progress(ctx, 0.0, 0.3)
    await ctx.run_child(_child_work, "mapped-prog-c1", "Download Phase",
                        params={"steps": 5, "delay": 0.3}, on_child_progress=cb1)

    # Manual progress for the middle phase
    for i in range(5):
        pct = 30 + (i + 1) * 8  # 38, 46, 54, 62, 70
        ctx.report_progress(pct, f"Processing step {i + 1}/5")
        await asyncio.sleep(0.3)

    cb2 = mapped_progress(ctx, 0.7, 1.0)
    await ctx.run_child(_child_work, "mapped-prog-c2", "Upload Phase",
                        params={"steps": 5, "delay": 0.3}, on_child_progress=cb2)


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_progress_variants,
            process_id="progress-variants",
            name="Progress: All Variants",
        ),
        TaskInstance(
            execute=_sequential_progress_demo,
            process_id="progress-sequential",
            name="Progress: Sequential Helper",
        ),
        TaskInstance(
            execute=_average_progress_demo,
            process_id="progress-average",
            name="Progress: Average Helper",
        ),
        TaskInstance(
            execute=_mapped_progress_demo,
            process_id="progress-mapped",
            name="Progress: Mapped Helper",
        ),
    ]
```

- [ ] **Step 2: Verify**

Start the app, launch each task from the dashboard:
- `progress-variants`: logs contain only messages at 25% and indeterminate; progress goes through 25 -> 50 -> None -> None -> 100
- `progress-sequential`: parent progress advances in 3 equal segments (0-33, 33-66, 66-100)
- `progress-average`: parent progress = average of 4 children running at different speeds
- `progress-mapped`: parent progress: 0-30% from child A, 30-70% manual, 70-100% from child B

- [ ] **Step 3: Commit**

```bash
git add examples/test-app/tasks/progress.py
git commit -m "feat(test-app): add progress reporting tasks"
```

---

### Task 4: Child process tasks

**Files:**
- Modify: `examples/test-app/tasks/children.py`

These tasks exercise: sequential children, parallel children with concurrency limit, 3-level nesting, and mixed sequential+parallel.

- [ ] **Step 1: Write children.py**

```python
"""Child process tasks: sequential, parallel, nested, mixed."""

import asyncio
from optio_core.models import TaskInstance


async def _simple_child(ctx):
    """A child that does work and reports progress."""
    steps = ctx.params.get("steps", 3)
    delay = ctx.params.get("delay", 0.5)
    for i in range(steps):
        ctx.report_progress((i + 1) / steps * 100, f"Working {i + 1}/{steps}")
        await asyncio.sleep(delay)


async def _sequential_children_demo(ctx):
    """Runs 3 children sequentially. Verify order, depth, parentId, rootId."""
    ctx.report_progress(0, "Starting 3 sequential children")
    await ctx.run_child(_simple_child, "seq-child-1", "Sequential Child 1",
                        params={"steps": 3, "delay": 0.3})
    await ctx.run_child(_simple_child, "seq-child-2", "Sequential Child 2",
                        params={"steps": 3, "delay": 0.3})
    await ctx.run_child(_simple_child, "seq-child-3", "Sequential Child 3",
                        params={"steps": 3, "delay": 0.3})
    ctx.report_progress(100, "All 3 children completed sequentially")


async def _parallel_children_demo(ctx):
    """Spawns 5 children in a parallel group with max_concurrency=2.
    Verify at most 2 run concurrently (check runningSince timestamps)."""
    ctx.report_progress(0, "Starting 5 parallel children (max 2 concurrent)")
    async with ctx.parallel_group(max_concurrency=2) as group:
        for i in range(5):
            await group.spawn(
                _simple_child, f"par-child-{i+1}", f"Parallel Child {i+1}",
                params={"steps": 3, "delay": 0.5},
            )
    ctx.report_progress(100, f"All 5 done. Results: {[(r.process_id, r.state) for r in group.results]}")


async def _nested_children_demo(ctx):
    """3-level nesting: grandparent -> child -> grandchild.
    Verify depth=0,1,2 and rootId consistent."""

    async def _grandchild(gc_ctx):
        gc_ctx.report_progress(50, f"Grandchild depth={gc_ctx._depth}")
        await asyncio.sleep(0.3)
        gc_ctx.report_progress(100, "Grandchild done")

    async def _child(c_ctx):
        c_ctx.report_progress(30, f"Child depth={c_ctx._depth}, spawning grandchild")
        await c_ctx.run_child(_grandchild, "nested-grandchild", "Grandchild")
        c_ctx.report_progress(100, "Child done")

    ctx.report_progress(10, "Grandparent spawning child")
    await ctx.run_child(_child, "nested-child", "Child")
    ctx.report_progress(100, "Grandparent done — check tree for 3 levels")


async def _mixed_children_demo(ctx):
    """2 sequential, then parallel group of 3, then 1 more sequential.
    Verify order values and tree structure."""
    ctx.report_progress(0, "Starting mixed sequence")

    await ctx.run_child(_simple_child, "mixed-seq-1", "Sequential 1",
                        params={"steps": 2, "delay": 0.2})
    await ctx.run_child(_simple_child, "mixed-seq-2", "Sequential 2",
                        params={"steps": 2, "delay": 0.2})

    ctx.report_progress(33, "Sequential part done, starting parallel group")

    async with ctx.parallel_group(max_concurrency=3) as group:
        for i in range(3):
            await group.spawn(
                _simple_child, f"mixed-par-{i+1}", f"Parallel {i+1}",
                params={"steps": 2, "delay": 0.3},
            )

    ctx.report_progress(83, "Parallel group done, one more sequential")

    await ctx.run_child(_simple_child, "mixed-seq-3", "Sequential 3",
                        params={"steps": 2, "delay": 0.2})

    ctx.report_progress(100, "All done — check tree: order 0,1 (seq), 2,3,4 (par), 5 (seq)")


async def _params_not_inherited_demo(ctx):
    """Parent has params, child has different params.
    Verify child sees its own params, not parent's."""

    async def _child_check_params(c_ctx):
        c_ctx.report_progress(100, f"Child params: {c_ctx.params}")

    ctx.report_progress(50, f"Parent params: {ctx.params}")
    await ctx.run_child(_child_check_params, "params-check-child", "Params Check Child",
                        params={"child_key": "child_value"})
    ctx.report_progress(100, "Check child logs — should show child_key, not parent_key")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_sequential_children_demo,
            process_id="children-sequential",
            name="Children: Sequential",
        ),
        TaskInstance(
            execute=_parallel_children_demo,
            process_id="children-parallel",
            name="Children: Parallel (max 2)",
        ),
        TaskInstance(
            execute=_nested_children_demo,
            process_id="children-nested",
            name="Children: 3-Level Nesting",
        ),
        TaskInstance(
            execute=_mixed_children_demo,
            process_id="children-mixed",
            name="Children: Mixed Seq + Parallel",
        ),
        TaskInstance(
            execute=_params_not_inherited_demo,
            process_id="children-params-not-inherited",
            name="Children: Params Not Inherited",
            params={"parent_key": "parent_value"},
        ),
    ]
```

- [ ] **Step 2: Verify**

Launch each task from the dashboard and check process trees:
- `children-sequential`: 3 children with order 0,1,2, depth=1, executed in order
- `children-parallel`: 5 children, observe via timestamps that at most 2 ran concurrently
- `children-nested`: tree with depth 0,1,2; all share same rootId
- `children-mixed`: 6 children with order 0-5, correct tree shape
- `children-params-not-inherited`: child logs show `{"child_key": "child_value"}`, not parent's params

- [ ] **Step 3: Commit**

```bash
git add examples/test-app/tasks/children.py
git commit -m "feat(test-app): add child process tasks"
```

---

### Task 5: Cancellation tasks

**Files:**
- Modify: `examples/test-app/tasks/cancellation.py`

These tasks exercise: cooperative cancellation, cancellation ignored, parent-child propagation, survive_cancel.

- [ ] **Step 1: Write cancellation.py**

```python
"""Cancellation tasks: cooperative, ignored, parent+child propagation."""

import asyncio
from optio_core.models import TaskInstance


async def _cooperative_cancel(ctx):
    """Long-running task that checks should_continue() each iteration.
    Cancel it from the dashboard to see it stop promptly."""
    for i in range(60):
        if not ctx.should_continue():
            ctx.report_progress(None, "Cancellation detected, stopping")
            return
        ctx.report_progress(i / 60 * 100, f"Iteration {i + 1}/60")
        await asyncio.sleep(0.5)
    ctx.report_progress(100, "Completed all 60 iterations (was not cancelled)")


async def _ignore_cancel(ctx):
    """Task that never checks should_continue().
    Cancel it — it finishes its work but ends in 'cancelled' state, not 'done'."""
    for i in range(10):
        ctx.report_progress((i + 1) * 10, f"Step {i + 1}/10 (ignoring cancel)")
        await asyncio.sleep(0.5)


async def _cancel_with_children(ctx):
    """Parent running a long child. Cancel the parent from dashboard.
    Both parent and child should end up cancelled."""

    async def _long_child(c_ctx):
        for i in range(60):
            if not c_ctx.should_continue():
                c_ctx.report_progress(None, "Child: cancellation detected")
                return
            c_ctx.report_progress(i / 60 * 100, f"Child step {i + 1}/60")
            await asyncio.sleep(0.5)

    ctx.report_progress(10, "Spawning long child — cancel me to cancel both")
    await ctx.run_child(_long_child, "cancel-propagation-child", "Long Child")
    ctx.report_progress(100, "Parent done (if you see this, cancellation didn't happen)")


async def _cancel_parallel_group(ctx):
    """Parent with 3 long parallel children. Cancel parent — all should cancel."""

    async def _long_worker(w_ctx):
        for i in range(60):
            if not w_ctx.should_continue():
                w_ctx.report_progress(None, f"Worker {w_ctx.process_id}: cancelled")
                return
            w_ctx.report_progress(i / 60 * 100)
            await asyncio.sleep(0.5)

    ctx.report_progress(5, "Spawning 3 parallel workers — cancel me to cancel all")
    async with ctx.parallel_group(max_concurrency=3) as group:
        for i in range(3):
            await group.spawn(_long_worker, f"cancel-par-{i+1}", f"Worker {i+1}")
    ctx.report_progress(100, "All workers done")


async def _survive_cancel_demo(ctx):
    """Parent spawns a child that gets cancelled (via its own should_continue check).
    Parent uses survive_cancel=True and continues."""

    async def _self_cancelling_child(c_ctx):
        """Simulates cancellation by simply returning early."""
        c_ctx.report_progress(50, "Child doing some work")
        await asyncio.sleep(0.3)
        # The child ends normally — to test survive_cancel,
        # cancel the *parent* from the dashboard while the child is running.
        # The child sees the flag and exits, and parent survives.
        for i in range(30):
            if not c_ctx.should_continue():
                c_ctx.report_progress(None, "Child saw cancellation, exiting")
                return
            await asyncio.sleep(0.1)
        c_ctx.report_progress(100, "Child done (wasn't cancelled)")

    ctx.report_progress(10, "Running child with survive_cancel=True")
    state = await ctx.run_child(
        _self_cancelling_child, "survive-cancel-child", "Cancellable Child",
        survive_cancel=True,
    )
    ctx.report_progress(60, f"Child ended with state: {state}")
    # Parent continues despite child cancellation
    await asyncio.sleep(0.5)
    ctx.report_progress(100, f"Parent survived child state={state} and completed")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_cooperative_cancel,
            process_id="cancel-cooperative",
            name="Cancel: Cooperative",
        ),
        TaskInstance(
            execute=_ignore_cancel,
            process_id="cancel-ignored",
            name="Cancel: Ignored (still ends cancelled)",
        ),
        TaskInstance(
            execute=_cancel_with_children,
            process_id="cancel-with-child",
            name="Cancel: Parent + Child Propagation",
        ),
        TaskInstance(
            execute=_cancel_parallel_group,
            process_id="cancel-parallel-group",
            name="Cancel: Parallel Group",
        ),
        TaskInstance(
            execute=_survive_cancel_demo,
            process_id="cancel-survive",
            name="Cancel: Survive Child Cancellation",
        ),
    ]
```

- [ ] **Step 2: Verify**

Launch each task and cancel from dashboard:
- `cancel-cooperative`: cancel mid-run, observe cancel_requested -> cancelling -> cancelled, stops promptly
- `cancel-ignored`: cancel mid-run, task finishes all 10 steps but ends in `cancelled` state
- `cancel-with-child`: cancel parent, both parent and child end in `cancelled`
- `cancel-parallel-group`: cancel parent, all 3 workers cancel
- `cancel-survive`: cancel parent while child runs, child cancels, parent sees `state="cancelled"` from run_child but continues to completion

- [ ] **Step 3: Commit**

```bash
git add examples/test-app/tasks/cancellation.py
git commit -m "feat(test-app): add cancellation tasks"
```

---

### Task 6: Error handling tasks

**Files:**
- Modify: `examples/test-app/tasks/errors.py`

These tasks exercise: simple failure, failure after partial progress, cascading failure, survive_failure, parallel group failure.

- [ ] **Step 1: Write errors.py**

```python
"""Error handling tasks: simple failure, cascading, parallel failures, survive_failure."""

import asyncio
from optio_core.models import TaskInstance


async def _simple_failure(ctx):
    """Raises ValueError. Check: state=failed, status.error, error log, failedAt."""
    ctx.report_progress(30, "About to fail...")
    await asyncio.sleep(0.3)
    raise ValueError("bad input — this is intentional")


async def _failure_after_progress(ctx):
    """Reports progress to 60%, then raises. Verify progress is flushed before failure."""
    for i in range(6):
        ctx.report_progress((i + 1) * 10, f"Step {i + 1}")
        await asyncio.sleep(0.2)
    raise RuntimeError("Failed at 60% — check that progress shows 60")


async def _cascading_failure(ctx):
    """3-level cascade: grandchild raises -> child fails -> parent fails.
    Each level has its own error message."""

    async def _failing_grandchild(gc_ctx):
        gc_ctx.report_progress(50, "Grandchild working...")
        await asyncio.sleep(0.2)
        raise Exception("Grandchild exploded")

    async def _middle_child(c_ctx):
        c_ctx.report_progress(30, "Child spawning grandchild (will fail)")
        await c_ctx.run_child(_failing_grandchild, "cascade-grandchild", "Failing Grandchild")
        c_ctx.report_progress(100, "You should never see this")

    ctx.report_progress(10, "Parent spawning child (whose grandchild will fail)")
    await ctx.run_child(_middle_child, "cascade-child", "Middle Child")
    ctx.report_progress(100, "You should never see this either")


async def _survive_failure_demo(ctx):
    """Grandchild fails, but child uses survive_failure=True.
    Child and parent complete successfully."""

    async def _failing_grandchild(gc_ctx):
        await asyncio.sleep(0.2)
        raise Exception("Grandchild failed")

    async def _resilient_child(c_ctx):
        c_ctx.report_progress(20, "Running grandchild with survive_failure=True")
        state = await c_ctx.run_child(
            _failing_grandchild, "survive-grandchild", "Failing Grandchild",
            survive_failure=True,
        )
        c_ctx.report_progress(60, f"Grandchild returned state={state}, child continues")
        await asyncio.sleep(0.3)
        c_ctx.report_progress(100, "Resilient child completed")

    ctx.report_progress(10, "Spawning resilient child")
    await ctx.run_child(_resilient_child, "survive-child", "Resilient Child")
    ctx.report_progress(100, "Parent done — grandchild failed, child and parent succeeded")


async def _parallel_failure_default(ctx):
    """3 children, one fails. survive_failure=False (default). Parent fails."""

    async def _good_child(c_ctx):
        await asyncio.sleep(0.5)
        c_ctx.report_progress(100, "Good child done")

    async def _bad_child(c_ctx):
        await asyncio.sleep(0.2)
        raise Exception("Bad child exploded")

    ctx.report_progress(5, "Spawning 3 parallel children, one will fail")
    async with ctx.parallel_group(max_concurrency=3) as group:
        await group.spawn(_good_child, "par-fail-good-1", "Good Child 1")
        await group.spawn(_bad_child, "par-fail-bad", "Bad Child")
        await group.spawn(_good_child, "par-fail-good-2", "Good Child 2")
    ctx.report_progress(100, "You should never see this")


async def _parallel_failure_survived(ctx):
    """3 children, one fails. survive_failure=True. Parent inspects results."""

    async def _good_child(c_ctx):
        await asyncio.sleep(0.5)
        c_ctx.report_progress(100, "Good child done")

    async def _bad_child(c_ctx):
        await asyncio.sleep(0.2)
        raise Exception("Bad child exploded")

    ctx.report_progress(5, "Spawning 3 parallel children (survive_failure=True)")
    async with ctx.parallel_group(max_concurrency=3, survive_failure=True) as group:
        await group.spawn(_good_child, "par-surv-good-1", "Good Child 1")
        await group.spawn(_bad_child, "par-surv-bad", "Bad Child")
        await group.spawn(_good_child, "par-surv-good-2", "Good Child 2")

    summary = [(r.process_id, r.state) for r in group.results]
    ctx.report_progress(100, f"Parent survived. Results: {summary}")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_simple_failure,
            process_id="error-simple",
            name="Error: Simple Failure",
        ),
        TaskInstance(
            execute=_failure_after_progress,
            process_id="error-after-progress",
            name="Error: Failure After Progress",
        ),
        TaskInstance(
            execute=_cascading_failure,
            process_id="error-cascade",
            name="Error: 3-Level Cascade",
        ),
        TaskInstance(
            execute=_survive_failure_demo,
            process_id="error-survive",
            name="Error: Survive Failure",
        ),
        TaskInstance(
            execute=_parallel_failure_default,
            process_id="error-parallel-default",
            name="Error: Parallel Failure (default)",
        ),
        TaskInstance(
            execute=_parallel_failure_survived,
            process_id="error-parallel-survived",
            name="Error: Parallel Failure (survived)",
        ),
    ]
```

- [ ] **Step 2: Verify**

Launch each task from dashboard:
- `error-simple`: state=failed, error="bad input — this is intentional", error log entry, failedAt set
- `error-after-progress`: state=failed, progress shows 60%
- `error-cascade`: all 3 levels failed, each with own error message
- `error-survive`: grandchild=failed, child=done, parent=done
- `error-parallel-default`: parent=failed with "Parallel group failed: 1 children..."
- `error-parallel-survived`: parent=done, results show 1 failed + 2 done

- [ ] **Step 3: Commit**

```bash
git add examples/test-app/tasks/errors.py
git commit -m "feat(test-app): add error handling tasks"
```

---

### Task 7: Ad-hoc and ephemeral tasks

**Files:**
- Modify: `examples/test-app/tasks/adhoc_ephemeral.py`

These tasks exercise: ad-hoc process define/delete, ephemeral processes, mark_ephemeral mid-execution. Since ad-hoc processes require access to the `Optio` instance, these tasks use the services dict to access it.

- [ ] **Step 1: Update main.py to pass the Optio instance in services**

In `examples/test-app/main.py`, modify the init call to pass the `fw` instance:

Replace:
```python
    fw = Optio()
    await fw.init(
        mongo_db=db,
        prefix=prefix,
        redis_url=redis_url,
        services={"mongo_db": db, "test_secret": "optio-test-secret-42"},
        get_task_definitions=get_task_definitions,
    )
```

With:
```python
    fw = Optio()
    services = {"mongo_db": db, "test_secret": "optio-test-secret-42", "optio": fw}
    await fw.init(
        mongo_db=db,
        prefix=prefix,
        redis_url=redis_url,
        services=services,
        get_task_definitions=get_task_definitions,
    )
```

- [ ] **Step 2: Write adhoc_ephemeral.py**

```python
"""Ad-hoc and ephemeral process tasks."""

import asyncio
from optio_core.models import TaskInstance


async def _adhoc_demo(ctx):
    """Creates ad-hoc processes at runtime, launches them, then cleans up."""
    optio = ctx.services["optio"]

    async def _adhoc_worker(w_ctx):
        w_ctx.report_progress(50, f"Ad-hoc worker {w_ctx.process_id} running")
        await asyncio.sleep(0.5)
        w_ctx.report_progress(100, "Ad-hoc worker done")

    ctx.report_progress(10, "Defining root ad-hoc process")
    proc = await optio.adhoc_define(
        TaskInstance(execute=_adhoc_worker, process_id="adhoc-root", name="Ad-hoc Root"),
    )
    ctx.report_progress(20, f"Created ad-hoc root (adhoc={proc.get('adhoc')})")

    # Launch the ad-hoc process
    await optio.launch_and_wait("adhoc-root")
    ctx.report_progress(50, "Ad-hoc root completed")

    # Verify it exists, then delete it
    check = await optio.get_process("adhoc-root")
    ctx.report_progress(60, f"Ad-hoc root state after run: {check['status']['state']}")

    await optio.adhoc_delete("adhoc-root")
    check = await optio.get_process("adhoc-root")
    ctx.report_progress(80, f"After adhoc_delete: process exists = {check is not None}")

    ctx.report_progress(100, "Ad-hoc demo complete")


async def _ephemeral_adhoc_demo(ctx):
    """Creates an ephemeral ad-hoc process. After completion, it auto-deletes."""
    optio = ctx.services["optio"]

    async def _ephemeral_worker(w_ctx):
        w_ctx.report_progress(50, "Ephemeral worker running")
        await asyncio.sleep(0.5)
        w_ctx.report_progress(100, "Ephemeral worker done — I'll be deleted soon")

    ctx.report_progress(10, "Defining ephemeral ad-hoc process")
    await optio.adhoc_define(
        TaskInstance(execute=_ephemeral_worker, process_id="ephemeral-adhoc",
                     name="Ephemeral Ad-hoc"),
        ephemeral=True,
    )

    ctx.report_progress(30, "Launching ephemeral process")
    await optio.launch_and_wait("ephemeral-adhoc")

    # Give cleanup a moment
    await asyncio.sleep(0.2)

    check = await optio.get_process("ephemeral-adhoc")
    ctx.report_progress(100, f"After completion: process exists = {check is not None} (should be False)")


async def _mark_ephemeral_demo(ctx):
    """Regular task that calls mark_ephemeral() mid-execution. Auto-deletes after completion.

    NOTE: Because this is a generator-defined task, after it auto-deletes,
    a resync would re-create it. To observe the deletion, check the DB
    immediately after it finishes (before any resync)."""
    ctx.report_progress(20, "I'm a normal task, about to become ephemeral")
    await ctx.mark_ephemeral()
    ctx.report_progress(50, "Now marked ephemeral — I'll be deleted when done")
    await asyncio.sleep(1)
    ctx.report_progress(100, "Finishing — watch me disappear from the DB")


async def _ephemeral_parent_with_children(ctx):
    """Ephemeral parent spawns 3 children.
    After parent completes, parent + all children deleted from DB."""
    optio = ctx.services["optio"]

    async def _child(c_ctx):
        c_ctx.report_progress(100, f"Child {c_ctx.process_id} done")

    async def _ephemeral_parent(p_ctx):
        p_ctx.report_progress(10, "Ephemeral parent spawning 3 children")
        for i in range(3):
            await p_ctx.run_child(_child, f"eph-parent-child-{i+1}", f"Child {i+1}")
        p_ctx.report_progress(100, "All children done — parent + children will be deleted")

    ctx.report_progress(10, "Creating ephemeral parent ad-hoc process")
    await optio.adhoc_define(
        TaskInstance(execute=_ephemeral_parent, process_id="eph-parent",
                     name="Ephemeral Parent"),
        ephemeral=True,
    )

    ctx.report_progress(30, "Launching ephemeral parent")
    await optio.launch_and_wait("eph-parent")
    await asyncio.sleep(0.2)

    parent_check = await optio.get_process("eph-parent")
    ctx.report_progress(100, f"After completion: parent exists = {parent_check is not None} (should be False)")


def get_tasks(services: dict) -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_adhoc_demo,
            process_id="adhoc-demo",
            name="Ad-hoc: Define, Launch, Delete",
        ),
        TaskInstance(
            execute=_ephemeral_adhoc_demo,
            process_id="ephemeral-adhoc-demo",
            name="Ephemeral: Ad-hoc Auto-Delete",
        ),
        TaskInstance(
            execute=_mark_ephemeral_demo,
            process_id="mark-ephemeral-demo",
            name="Ephemeral: mark_ephemeral() Mid-Run",
        ),
        TaskInstance(
            execute=_ephemeral_parent_with_children,
            process_id="ephemeral-parent-demo",
            name="Ephemeral: Parent + Children Deleted",
        ),
    ]
```

- [ ] **Step 3: Verify**

Launch each task from dashboard:
- `adhoc-demo`: logs show ad-hoc created, launched, completed, then deleted
- `ephemeral-adhoc-demo`: logs show process exists=False after completion
- `mark-ephemeral-demo`: process disappears from DB after completion (may reappear on resync since it's generator-defined)
- `ephemeral-parent-demo`: parent and all 3 children deleted after completion

- [ ] **Step 4: Commit**

```bash
git add examples/test-app/main.py examples/test-app/tasks/adhoc_ephemeral.py
git commit -m "feat(test-app): add ad-hoc and ephemeral tasks"
```

---

### Task 8: Scheduled task

**Files:**
- Modify: `examples/test-app/tasks/scheduled.py`

This exercises cron scheduling: a task that runs every minute.

- [ ] **Step 1: Write scheduled.py**

```python
"""Cron-scheduled tasks."""

import asyncio
from datetime import datetime, timezone
from optio_core.models import TaskInstance


async def _scheduled_task(ctx):
    """Runs on a cron schedule (every minute).
    Watch the dashboard: after it completes, it re-launches on the next minute."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    ctx.report_progress(0, f"Scheduled run started at {now}")
    for i in range(5):
        ctx.report_progress((i + 1) * 20, f"Step {i + 1}/5")
        await asyncio.sleep(0.5)
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    ctx.report_progress(100, f"Completed at {now} — next run in ~1 minute")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_scheduled_task,
            process_id="scheduled-every-minute",
            name="Scheduled: Every Minute",
            schedule="* * * * *",
        ),
    ]
```

- [ ] **Step 2: Verify**

Start the app, wait up to 1 minute. The task should auto-launch. After it completes (~2.5s), wait for the next minute — it should launch again. Verify via dashboard that multiple runs happen and each clears the previous logs.

- [ ] **Step 3: Commit**

```bash
git add examples/test-app/tasks/scheduled.py
git commit -m "feat(test-app): add cron-scheduled task"
```

---

## Self-Review Checklist

### Spec coverage

| Spec Section | Tasks Covering It |
|---|---|
| 1. TaskInstance Definition | Task 2 (params, metadata, warning, special, non-cancellable) |
| 2. ProcessContext API | Task 2 (services), Task 3 (progress variants), Task 7 (mark_ephemeral) |
| 3. State Machine | All tasks exercise state transitions; Tasks 5+6 for failure/cancel paths |
| 4. Child Processes | Task 4 (sequential, parallel, nested, mixed, params not inherited) |
| 5. Cancellation | Task 5 (cooperative, ignored, parent-child, parallel, survive_cancel) |
| 6. Progress & Helpers | Task 3 (variants, sequential, average, mapped helpers) |
| 7. Error Handling | Task 6 (simple, after progress, cascade, survive, parallel) |
| 8. Logging | Covered by all tasks (event logs auto-generated); Task 3 (progress message logs) |
| 9. Ephemeral | Task 7 (adhoc ephemeral, mark_ephemeral, parent+children) |
| 10. Ad-hoc | Task 7 (define, launch, delete, child ad-hoc) |
| 11. Cron Scheduling | Task 8 |
| 12. Lifecycle Operations | Re-launch/dismiss testable from dashboard for any task; Task 7 (resync behavior noted) |

### Placeholder scan

No TBDs, TODOs, or "implement later" found.

### Type consistency

- All tasks use `TaskInstance`, `CancellationConfig` from `optio_core.models` consistently.
- All progress helpers imported from `optio_core.progress_helpers`.
- `ctx` parameter naming consistent across all task functions.
- `get_tasks()` return type consistent: `list[TaskInstance]`.
- `adhoc_ephemeral.py` uses `get_tasks(services)` signature matching the call in `__init__.py`.
