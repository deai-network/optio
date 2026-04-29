# Deadline-Driven Cancel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `Optio.cancel()` deadline-enforced. Cooperative cancel first, then global grace period, then force-cancel via `asyncio.Task.cancel()` and a conditional Mongo update.

**Architecture:** `Executor` tracks each running task's `asyncio.Task` and a per-process `_CancelEntry` (cooperative flag + monotonic deadline). A single supervisor coroutine in `Optio` scans entries every 500 ms; past-deadline entries get force-cancelled via `Task.cancel()` plus a conditional Mongo update that flips the row to `failed` with a canonical error string. `shutdown()` reuses the same mechanism instead of having its own force-finalize logic.

**Tech Stack:** Python 3.11+, `asyncio`, `motor` (async MongoDB), `pytest-asyncio`. Spec: `docs/2026-04-29-deadline-driven-cancel-design.md`.

**Base revision:** `98021d41a55d4179bcbedf9e167a13d564648dfb` on branch `csillag/project-removal` (as of 2026-04-29T18:23:53Z).

---

## File Structure

**Modified files (all under `packages/optio-core/`):**

- `src/optio_core/models.py` — add `cancel_grace_seconds` to `OptioConfig`.
- `src/optio_core/executor.py` — `_CancelEntry` dataclass, `_running_tasks` registry, `try/finally` cleanup, replace `request_cancel` with `request_cancel_with_deadline`, add `force_cancel`.
- `src/optio_core/lifecycle.py` — accept `cancel_grace_seconds` in `init()`, add `_supervisor_loop` + start/stop, update `_handle_cancel` to record deadline, add `cancel_and_wait`, rewrite `shutdown` to use unified mechanism, remove `_force_finalize_stuck_processes`, factor out `_write_force_cancelled_state`.
- `tests/test_executor.py` — update two `request_cancel` callsites to use the new method.
- `tests/test_widget_primitives.py` — update one `request_cancel` callsite.
- `tests/test_lifecycle_reconciliation.py` — update the shutdown-force-finalize test to point at the new code path.

**New files:**

- `src/optio_core/_force_cancel.py` — small private module exposing `_write_force_cancelled_state` (kept separate so `executor.py` and `lifecycle.py` can both import without a circular dep).
- `tests/test_deadline_cancel.py` — all 10 spec-required scenarios.

---

## Task 1: Add `cancel_grace_seconds` to `OptioConfig`

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py:76-83`

- [ ] **Step 1: Add the field**

In `packages/optio-core/src/optio_core/models.py`, replace the `OptioConfig` dataclass (lines 76-83) with:

```python
@dataclass
class OptioConfig:
    """Configuration for optio initialization."""
    mongo_db: Any  # motor AsyncIOMotorDatabase
    prefix: str = "optio"
    redis_url: str | None = None
    services: dict[str, Any] = field(default_factory=dict)
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None
    cancel_grace_seconds: float = 5.0
```

- [ ] **Step 2: Run existing model tests to confirm nothing else breaks**

Run: `cd packages/optio-core && ./node_modules/.bin/pytest tests/test_models.py -v` *(or `python -m pytest tests/test_models.py -v` if no node_modules — use whichever the repo already runs.)*

Expected: PASS. Field has a default, so older code constructing `OptioConfig()` is unaffected.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py
git commit -m "feat(optio-core): add cancel_grace_seconds to OptioConfig"
```

---

## Task 2: Plumb `cancel_grace_seconds` through `Optio.init`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:43-80`
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-core/tests/test_deadline_cancel.py`:

```python
"""Tests for deadline-driven cooperative cancel.

Spec: docs/2026-04-29-deadline-driven-cancel-design.md
"""
import asyncio

import pytest

from optio_core.lifecycle import Optio


pytestmark = pytest.mark.asyncio


async def test_init_accepts_cancel_grace_seconds(mongo_db):
    """Optio.init forwards cancel_grace_seconds onto OptioConfig."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsinit", cancel_grace_seconds=2.5)
    assert optio._config.cancel_grace_seconds == 2.5


async def test_init_default_cancel_grace_seconds(mongo_db):
    """Default cancel_grace_seconds is 5.0."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsdefault")
    assert optio._config.cancel_grace_seconds == 5.0
```

- [ ] **Step 2: Run test to confirm failure**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py -v`

Expected: FAIL — `init()` does not accept the `cancel_grace_seconds` keyword.

- [ ] **Step 3: Add the kwarg**

In `packages/optio-core/src/optio_core/lifecycle.py`, replace the `init` method signature and `OptioConfig` construction:

```python
    async def init(
        self,
        mongo_db: AsyncIOMotorDatabase,
        prefix: str = "optio",
        redis_url: str | None = None,
        services: dict[str, Any] | None = None,
        get_task_definitions: Callable[
            [dict[str, Any], ProcessMetadataFilter | None],
            Awaitable[list[TaskInstance]],
        ] | None = None,
        cancel_grace_seconds: float = 5.0,
    ) -> None:
        """Initialize optio.

        Args:
            mongo_db: Motor async MongoDB database.
            prefix: Namespace for collections and streams.
            redis_url: Redis connection URL. If None, Redis features (command
                consumer, custom commands) are disabled and processes are
                managed via direct method calls.
            services: Custom services dict passed to task execute functions.
            get_task_definitions: Async function (services, metadata_filter)
                returning task definitions.
            cancel_grace_seconds: Cooperative-cancel deadline (seconds). After
                this elapses without the task unwinding, the supervisor
                force-cancels via asyncio.Task.cancel() and writes a terminal
                'failed' state. Default 5.0. Same value applies to every
                cancel during this Optio lifetime.
        """
        services = services or {}
        self._config = OptioConfig(
            mongo_db=mongo_db,
            prefix=prefix,
            redis_url=redis_url,
            services=services,
            get_task_definitions=get_task_definitions,
            cancel_grace_seconds=cancel_grace_seconds,
        )
        # ... existing body unchanged
```

(Leave the rest of `init` untouched.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py -v`

Expected: PASS for both `test_init_accepts_cancel_grace_seconds` and `test_init_default_cancel_grace_seconds`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "feat(optio-core): expose cancel_grace_seconds in Optio.init"
```

---

## Task 3: Introduce `_CancelEntry` and `_running_tasks` registry in `Executor`

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:23-32, 90-193`
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_executor_tracks_running_task_and_cancel_entry(mongo_db):
    """While a process is running, _running_tasks and _cancellation_flags both
    have an entry; the flag value is a _CancelEntry, not a bare Event."""
    from optio_core.executor import Executor, _CancelEntry
    from optio_core.models import TaskInstance
    from optio_core.store import upsert_process

    prefix = "ctxtrack"
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold_until_release(ctx):  # noqa: ARG001
        started.set()
        await release.wait()

    task_inst = TaskInstance(
        process_id="p.hold", name="Hold", params={}, execute=hold_until_release,
    )
    await upsert_process(mongo_db, prefix, task_inst)
    executor = Executor(mongo_db, prefix, services={})
    executor.register_tasks([task_inst])

    runner = asyncio.create_task(executor.launch_process("p.hold"))
    await started.wait()

    # Find the oid via the registry's only entry.
    assert len(executor._running_tasks) == 1
    oid = next(iter(executor._running_tasks))
    assert isinstance(executor._running_tasks[oid], asyncio.Task)
    entry = executor._cancellation_flags[oid]
    assert isinstance(entry, _CancelEntry)
    assert entry.deadline is None
    assert isinstance(entry.flag, asyncio.Event)

    release.set()
    await runner

    # After completion both registries are empty.
    assert executor._running_tasks == {}
    assert executor._cancellation_flags == {}
```

- [ ] **Step 2: Run test to confirm failure**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_executor_tracks_running_task_and_cancel_entry -v`

Expected: FAIL — `_CancelEntry` does not exist; `_running_tasks` does not exist.

- [ ] **Step 3: Add `_CancelEntry` and registry**

In `packages/optio-core/src/optio_core/executor.py`, add imports and the dataclass at the top, alongside existing imports:

```python
"""Task executor — runs task functions with state management."""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import (
    TaskInstance, ProcessStatus, Progress, ProcessMetadataFilter, matches_filter,
)
from optio_core.state_machine import LAUNCHABLE_STATES
from optio_core.store import (
    get_process_by_process_id,
    update_status, clear_result_fields,
    create_child_process, append_log,
    clear_widget_upstream,
)
from optio_core.context import ProcessContext


@dataclass
class _CancelEntry:
    """Tracks cooperative-cancel state for one running process.

    `flag` is the cooperative cancellation Event consumed by ProcessContext.
    `deadline` is a monotonic timestamp; None until cancel() is called. Once
    set, it is not refreshed by subsequent calls (first wins).
    """
    flag: asyncio.Event
    deadline: float | None = None
```

Then update the constructor and `_execute_process`:

```python
class Executor:
    """Executes task functions with lifecycle management."""

    def __init__(self, db: AsyncIOMotorDatabase, prefix: str, services: dict[str, Any]):
        self._db = db
        self._prefix = prefix
        self._services = services
        self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
        self._running_tasks: dict[ObjectId, asyncio.Task] = {}
        self._task_registry: dict[str, TaskInstance] = {}
```

Replace the body of `_execute_process` so it uses `_CancelEntry`, captures the running task, and uses a `try/finally` for registry cleanup:

```python
    async def _execute_process(
        self, proc: dict, execute_fn: Callable | None,
        parent_ctx: ProcessContext | None = None,
        resume: bool = False,
    ) -> str:
        """Execute a process."""
        oid = proc["_id"]
        root_oid = proc.get("rootId", oid)

        cancel_flag = asyncio.Event()
        self._cancellation_flags[oid] = _CancelEntry(flag=cancel_flag, deadline=None)
        current = asyncio.current_task()
        if current is not None:
            self._running_tasks[oid] = current

        try:
            now = datetime.now(timezone.utc)
            await update_status(
                self._db, self._prefix, oid,
                ProcessStatus(state="running", running_since=now),
            )
            await append_log(self._db, self._prefix, oid, "event", "State changed to running")

            ctx = ProcessContext(
                process_oid=oid,
                process_id=proc["processId"],
                root_oid=root_oid,
                depth=proc.get("depth", 0),
                params=proc.get("params", {}),
                metadata=proc.get("metadata", {}),
                services=self._services,
                db=self._db,
                prefix=self._prefix,
                cancellation_flag=cancel_flag,
                child_counter={"next": 0},
                resume=resume,
            )
            ctx._executor = self

            if parent_ctx is not None and parent_ctx._on_child_progress is not None:
                child_process_id = proc["processId"]
                child_name = proc["name"]
                def _listener(percent, message, _pid=child_process_id, _name=child_name):
                    parent_ctx._notify_child_progress(_pid, _name, "running", percent, message)
                ctx._parent_listener = _listener

            if execute_fn is None:
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="failed", error="No execute function found",
                        failed_at=datetime.now(timezone.utc),
                    ),
                )
                return "failed"

            start_time = time.monotonic()
            end_state = "done"

            try:
                await execute_fn(ctx)
                if cancel_flag.is_set():
                    end_state = "cancelled"
            except Exception as e:
                await ctx.flush_final_progress()
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="failed", error=str(e),
                        failed_at=datetime.now(timezone.utc),
                    ),
                )
                await append_log(self._db, self._prefix, oid, "error", str(e))
                await clear_widget_upstream(self._db, self._prefix, oid)
                await self._cleanup_ephemeral(proc["processId"])
                return "failed"

            await ctx.flush_final_progress()
            elapsed = time.monotonic() - start_time

            if end_state == "done":
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="done",
                        done_at=datetime.now(timezone.utc),
                        duration=round(elapsed, 2),
                    ),
                )
                await append_log(self._db, self._prefix, oid, "event", "State changed to done")
            elif end_state == "cancelled":
                await update_status(
                    self._db, self._prefix, oid,
                    ProcessStatus(
                        state="cancelled",
                        stopped_at=datetime.now(timezone.utc),
                    ),
                )
                await append_log(self._db, self._prefix, oid, "event", "State changed to cancelled")

            await clear_widget_upstream(self._db, self._prefix, oid)
            await self._cleanup_ephemeral(proc["processId"])
            return end_state
        finally:
            self._cancellation_flags.pop(oid, None)
            self._running_tasks.pop(oid, None)
```

Note: the explicit `pop(oid, None)` calls inside the prior bodies (`failed` branch, `done` branch) are removed — the `finally` clause handles them. The `try/finally` is mandatory: the upcoming `force_cancel` injects `CancelledError`, which `except Exception` does not catch.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_executor_tracks_running_task_and_cancel_entry -v`

Expected: PASS.

- [ ] **Step 5: Run all executor tests, expect FAILURES on the old `request_cancel` API**

Run: `cd packages/optio-core && python -m pytest tests/test_executor.py tests/test_widget_primitives.py -v`

Expected: PASS — `request_cancel` still exists for now (it sits on a different code path). If they fail, the failure will be a `flag.set()` call where `flag` is now a `_CancelEntry`. Track the failures; they're handled in Task 4.

- [ ] **Step 6: Patch the still-existing `request_cancel` to read through `_CancelEntry`**

In `packages/optio-core/src/optio_core/executor.py`, replace `request_cancel`:

```python
    def request_cancel(self, process_oid: ObjectId) -> bool:
        """Request cancellation of a running process. (Legacy — Task 4 replaces.)"""
        entry = self._cancellation_flags.get(process_oid)
        if entry is not None:
            entry.flag.set()
            return True
        return False
```

Also fix the child cancel propagation at `executor.py:232`. The line `parent_ctx._cancellation_flag.set()` reads `ProcessContext._cancellation_flag` (an Event held in `ctx`). This is unchanged — `ctx` still receives the bare Event — so no edit needed at that line. But verify by reading it.

- [ ] **Step 7: Run all executor tests again**

Run: `cd packages/optio-core && python -m pytest tests/test_executor.py tests/test_widget_primitives.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "feat(optio-core): track running asyncio tasks and per-process cancel entries"
```

---

## Task 4: Replace `request_cancel` with `request_cancel_with_deadline`

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:236-242`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:486`
- Modify: `packages/optio-core/tests/test_executor.py:65, 164`
- Modify: `packages/optio-core/tests/test_widget_primitives.py:247`
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_request_cancel_with_deadline_records_first_deadline(mongo_db):
    """First call records a deadline; second call is a no-op on the deadline."""
    from optio_core.executor import Executor, _CancelEntry
    import time as _time

    executor = Executor(mongo_db, "rcwd", services={})
    fake_oid = __import__("bson").ObjectId()
    flag = asyncio.Event()
    executor._cancellation_flags[fake_oid] = _CancelEntry(flag=flag, deadline=None)

    first = _time.monotonic() + 1.0
    found = executor.request_cancel_with_deadline(fake_oid, deadline=first)
    assert found is True
    assert flag.is_set()
    assert executor._cancellation_flags[fake_oid].deadline == first

    second = _time.monotonic() + 99.0
    found2 = executor.request_cancel_with_deadline(fake_oid, deadline=second)
    assert found2 is True
    assert executor._cancellation_flags[fake_oid].deadline == first  # not refreshed


async def test_request_cancel_with_deadline_returns_false_when_unknown(mongo_db):
    from optio_core.executor import Executor

    executor = Executor(mongo_db, "rcwd2", services={})
    fake_oid = __import__("bson").ObjectId()
    found = executor.request_cancel_with_deadline(fake_oid, deadline=1.0)
    assert found is False
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_request_cancel_with_deadline_records_first_deadline tests/test_deadline_cancel.py::test_request_cancel_with_deadline_returns_false_when_unknown -v`

Expected: FAIL — `request_cancel_with_deadline` does not exist.

- [ ] **Step 3: Add the new method, remove the old**

In `packages/optio-core/src/optio_core/executor.py`, replace the existing `request_cancel` with:

```python
    def request_cancel_with_deadline(
        self, process_oid: ObjectId, deadline: float
    ) -> bool:
        """Request cooperative cancel and record a force-cancel deadline.

        Sets the cooperative cancel flag. Records `deadline` (a monotonic
        timestamp) in the entry only if no deadline is set yet — first wins.
        Returns True if an entry was found, False otherwise.
        """
        entry = self._cancellation_flags.get(process_oid)
        if entry is None:
            return False
        entry.flag.set()
        if entry.deadline is None:
            entry.deadline = deadline
        return True
```

- [ ] **Step 4: Update the lifecycle call site**

In `packages/optio-core/src/optio_core/lifecycle.py`, replace line 486:

```python
        import time
        found = self._executor.request_cancel_with_deadline(
            proc["_id"],
            deadline=time.monotonic() + self._config.cancel_grace_seconds,
        )
```

(Add `import time` at the top of `lifecycle.py` if it is not already imported. Check the existing imports first.)

- [ ] **Step 5: Update existing tests that called `request_cancel`**

In `packages/optio-core/tests/test_executor.py:65` — replace:

```python
        executor.request_cancel(proc["_id"])
```

with:

```python
        import time as _time
        executor.request_cancel_with_deadline(proc["_id"], deadline=_time.monotonic() + 60.0)
```

Apply the same replacement at `test_executor.py:164` and `test_widget_primitives.py:247`. (60 s is well past anything the tests measure; deadline never fires.)

- [ ] **Step 6: Run all affected tests**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py tests/test_executor.py tests/test_widget_primitives.py -v`

Expected: PASS.

- [ ] **Step 7: Verify the old method is gone**

Run: `grep -n "request_cancel\b" packages/optio-core/src packages/optio-core/tests`

Expected: only `request_cancel_with_deadline` matches; no bare `request_cancel(` references remain.

- [ ] **Step 8: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_widget_primitives.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "feat(optio-core): replace request_cancel with deadline-recording variant"
```

---

## Task 5: Extract `_write_force_cancelled_state` shared helper

**Files:**
- Create: `packages/optio-core/src/optio_core/_force_cancel.py`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:365-400` (will be removed in Task 8)
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_write_force_cancelled_state_updates_active_process(mongo_db):
    """Conditional update flips active->failed and writes canonical error."""
    from optio_core._force_cancel import _write_force_cancelled_state
    from datetime import datetime, timezone
    from bson import ObjectId

    prefix = "wfcs"
    coll = mongo_db[f"{prefix}_processes"]
    oid = ObjectId()
    await coll.insert_one({
        "_id": oid,
        "processId": "p.fc",
        "name": "FC",
        "status": {"state": "running", "runningSince": datetime.now(timezone.utc)},
        "widgetUpstream": {"url": "http://x", "innerAuth": None},
        "log": [],
    })

    updated = await _write_force_cancelled_state(mongo_db, prefix, oid)
    assert updated is True
    doc = await coll.find_one({"_id": oid})
    assert doc["status"]["state"] == "failed"
    assert "Task did not unwind within cancellation grace period" in doc["status"]["error"]
    assert doc["widgetUpstream"] is None


async def test_write_force_cancelled_state_no_op_on_terminal_process(mongo_db):
    """If the process is already terminal, the conditional update is a no-op."""
    from optio_core._force_cancel import _write_force_cancelled_state
    from datetime import datetime, timezone
    from bson import ObjectId

    prefix = "wfcs2"
    coll = mongo_db[f"{prefix}_processes"]
    oid = ObjectId()
    await coll.insert_one({
        "_id": oid,
        "processId": "p.done",
        "name": "Done",
        "status": {"state": "done", "doneAt": datetime.now(timezone.utc)},
        "widgetUpstream": None,
        "log": [],
    })

    updated = await _write_force_cancelled_state(mongo_db, prefix, oid)
    assert updated is False
    doc = await coll.find_one({"_id": oid})
    assert doc["status"]["state"] == "done"
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_write_force_cancelled_state_updates_active_process tests/test_deadline_cancel.py::test_write_force_cancelled_state_no_op_on_terminal_process -v`

Expected: FAIL — module `_force_cancel` does not exist.

- [ ] **Step 3: Create the helper module**

Create `packages/optio-core/src/optio_core/_force_cancel.py`:

```python
"""Shared helper for writing the canonical 'force-cancelled' terminal state.

Imported by both Executor.force_cancel and Optio.shutdown. Kept in its own
module to avoid a circular import between executor.py and lifecycle.py.

Spec: docs/2026-04-29-deadline-driven-cancel-design.md
"""
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import ProcessStatus
from optio_core.state_machine import ACTIVE_STATES
from optio_core.store import append_log


FORCE_CANCEL_ERROR = "Task did not unwind within cancellation grace period"


async def _write_force_cancelled_state(
    db: AsyncIOMotorDatabase, prefix: str, oid: ObjectId,
) -> bool:
    """Conditionally flip an active process to terminal 'failed' state.

    Only updates rows whose current state is in ACTIVE_STATES. A task that
    won the race to a terminal state owns its own transition and is left
    alone. Returns True if the row was updated, False otherwise.
    """
    coll = db[f"{prefix}_processes"]
    now = datetime.now(timezone.utc)
    status_doc = ProcessStatus(
        state="failed", error=FORCE_CANCEL_ERROR, failed_at=now,
    ).to_dict()
    result = await coll.update_one(
        {"_id": oid, "status.state": {"$in": list(ACTIVE_STATES)}},
        {"$set": {"status": status_doc, "widgetUpstream": None}},
    )
    if result.modified_count:
        await append_log(
            db, prefix, oid,
            "event",
            "State forced: running -> failed (cancellation grace period exceeded)",
        )
        return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_write_force_cancelled_state_updates_active_process tests/test_deadline_cancel.py::test_write_force_cancelled_state_no_op_on_terminal_process -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/_force_cancel.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "feat(optio-core): add _write_force_cancelled_state shared helper"
```

---

## Task 6: Add `Executor.force_cancel`

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py` (append method)
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_force_cancel_writes_failed_state_for_stubborn_task(mongo_db):
    """Stubborn task ignores the cooperative flag; force_cancel terminates it."""
    from optio_core.executor import Executor
    from optio_core.models import TaskInstance
    from optio_core.store import upsert_process

    prefix = "fc"
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        # Ignore the flag entirely — busy-await to give cancellation a hook.
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stubborn", params={}, execute=stubborn,
    )
    await upsert_process(mongo_db, prefix, task_inst)
    executor = Executor(mongo_db, prefix, services={})
    executor.register_tasks([task_inst])

    runner = asyncio.create_task(executor.launch_process("p.stub"))
    await started.wait()

    oid = next(iter(executor._running_tasks))
    await executor.force_cancel(oid)

    # Mongo state: failed with canonical error.
    coll = mongo_db[f"{prefix}_processes"]
    doc = await coll.find_one({"_id": oid})
    assert doc["status"]["state"] == "failed"
    assert "Task did not unwind within cancellation grace period" in doc["status"]["error"]

    # The asyncio task is finished one way or another.
    with pytest.raises(asyncio.CancelledError):
        await runner
```

- [ ] **Step 2: Run test to confirm failure**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_force_cancel_writes_failed_state_for_stubborn_task -v`

Expected: FAIL — `force_cancel` does not exist.

- [ ] **Step 3: Implement `force_cancel`**

Append the method at the end of the `Executor` class in `packages/optio-core/src/optio_core/executor.py`:

```python
    async def force_cancel(self, oid: ObjectId) -> None:
        """Hard-cancel a process whose cooperative deadline has expired.

        Calls Task.cancel() on the tracked asyncio Task, awaits a bounded
        unwind, then writes the conditional 'failed' terminal state to
        Mongo via _write_force_cancelled_state. Used only by the
        Optio-level supervisor and by shutdown.
        """
        from optio_core._force_cancel import _write_force_cancelled_state

        task = self._running_tasks.get(oid)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                # TimeoutError: thread-blocked or stubborn — proceed regardless.
                # CancelledError: task acknowledged cancellation — proceed.
                # Other exceptions are not our concern; the conditional Mongo
                # update below is the source of truth.
                pass
        await _write_force_cancelled_state(self._db, self._prefix, oid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_force_cancel_writes_failed_state_for_stubborn_task -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "feat(optio-core): add Executor.force_cancel"
```

---

## Task 7: Add supervisor loop to `Optio`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:34-41, 250-280`
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_supervisor_force_cancels_past_deadline_entries(mongo_db):
    """A stubborn task whose deadline has passed is force-cancelled by the supervisor."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance
    import time as _time

    prefix = "supv"
    started = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.supv", name="Supv", params={}, execute=stubborn,
    )

    async def gen(_services, _filter):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.supv")
        await started.wait()
        # Cancel — record the deadline.
        await optio.cancel("p.supv")

        # Within ~1.5s the supervisor should have force-cancelled.
        deadline = _time.monotonic() + 3.0
        while _time.monotonic() < deadline:
            proc = await optio.get_process("p.supv")
            if proc and proc["status"]["state"] == "failed":
                break
            await asyncio.sleep(0.1)

        proc = await optio.get_process("p.supv")
        assert proc["status"]["state"] == "failed"
        assert "Task did not unwind within cancellation grace period" in proc["status"]["error"]
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
```

- [ ] **Step 2: Run test to confirm failure**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_supervisor_force_cancels_past_deadline_entries -v`

Expected: FAIL — supervisor does not exist; `_handle_cancel` does not yet record a deadline (Task 8 fixes the latter).

- [ ] **Step 3: Add the supervisor task field, the loop, and start/stop**

In `packages/optio-core/src/optio_core/lifecycle.py`, update the `__init__` to add the supervisor task slot:

```python
    def __init__(self):
        self._config: OptioConfig | None = None
        self._redis: Redis | None = None
        self._executor: Executor | None = None
        self._consumer: CommandConsumer | None = None
        self._scheduler: ProcessScheduler | None = None
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
```

Add the supervisor loop method (place right above `_heartbeat_loop`):

```python
    async def _supervisor_loop(self) -> None:
        """Scan for past-deadline cancellations every 500 ms; force-cancel them."""
        import time as _time
        while self._running:
            try:
                now = _time.monotonic()
                if self._executor is not None:
                    for oid, entry in list(self._executor._cancellation_flags.items()):
                        if entry.deadline is None:
                            continue
                        if now < entry.deadline:
                            continue
                        await self._executor.force_cancel(oid)
            except Exception as e:
                logger.exception(f"Supervisor loop error: {e}")
            await asyncio.sleep(0.5)
```

In `run()`, after the heartbeat-task start, also start the supervisor:

```python
        if self._redis:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        self._supervisor_task = asyncio.create_task(self._supervisor_loop())
```

In `shutdown()`, after the main grace-handling block (current line 322 area, before the Redis aclose), stop the supervisor:

```python
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            self._supervisor_task = None
```

(This is interim placement. Task 9 rewrites `shutdown()` properly; this just gets the supervisor stopping cleanly so Task 7's test doesn't hang on teardown.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_supervisor_force_cancels_past_deadline_entries -v`

Expected: PASS. (If it fails because `_handle_cancel` is not yet recording a deadline — i.e. `entry.deadline` stays `None` — push this test forward into Task 8 and proceed; otherwise it passes because Task 4 already updated `_handle_cancel`. Verify before committing.)

- [ ] **Step 5: Run the existing lifecycle suite**

Run: `cd packages/optio-core && python -m pytest tests/test_lifecycle_reconciliation.py -v`

Expected: PASS — supervisor is harmless on existing flows. (If `test_shutdown_force_finalizes_uncooperative_task` fails here, it is fixed in Task 9.)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "feat(optio-core): add supervisor loop that force-cancels past-deadline tasks"
```

---

## Task 8: Confirm `_handle_cancel` records the deadline correctly

This task is technically already done by Task 4's edit; this task formalizes it with a focused test and a careful re-read.

**Files:**
- Verify: `packages/optio-core/src/optio_core/lifecycle.py:457-491`
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_handle_cancel_records_deadline_on_running_process(mongo_db):
    """Calling cancel() on a running process records monotonic deadline."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance
    import time as _time

    prefix = "hcdl"
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(ctx):  # noqa: ARG001
        started.set()
        await release.wait()

    task_inst = TaskInstance(
        process_id="p.hold", name="Hold", params={}, execute=hold,
    )
    async def gen(_services, _filter):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=10.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.hold")
        await started.wait()

        before = _time.monotonic()
        await optio.cancel("p.hold")
        after = _time.monotonic()

        oid = next(iter(optio._executor._cancellation_flags))
        entry = optio._executor._cancellation_flags[oid]
        assert entry.flag.is_set()
        assert entry.deadline is not None
        # deadline is in the future, within [before+10, after+10] inclusive
        assert before + 10.0 - 0.5 <= entry.deadline <= after + 10.0 + 0.5
    finally:
        release.set()
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
```

- [ ] **Step 2: Run test**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_handle_cancel_records_deadline_on_running_process -v`

Expected: PASS — Task 4 already wired this. If it fails, return to Task 4 step 4 and verify the lifecycle edit landed.

- [ ] **Step 3: Commit (test-only)**

```bash
git add packages/optio-core/tests/test_deadline_cancel.py
git commit -m "test(optio-core): pin deadline-recording behaviour of _handle_cancel"
```

---

## Task 9: Add `Optio.cancel_and_wait`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:202-208`
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_cancel_and_wait_cooperative(mongo_db):
    """Cooperative task ends 'cancelled'; cancel_and_wait returns 'cancelled'."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "caw1"

    async def cooperative(ctx):
        # Honour the flag promptly.
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.coop", name="Coop", params={}, execute=cooperative,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.coop")
        await asyncio.sleep(0.2)  # let it transition to running
        state = await optio.cancel_and_wait("p.coop")
        assert state == "cancelled"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_cancel_and_wait_stubborn_returns_failed(mongo_db):
    """Stubborn task force-cancelled; cancel_and_wait returns 'failed'."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "caw2"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.2)
        state = await optio.cancel_and_wait("p.stub")
        assert state == "failed"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_cancel_and_wait_returns_none_for_missing(mongo_db):
    from optio_core.lifecycle import Optio
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cawnone")
    state = await optio.cancel_and_wait("nope.does.not.exist")
    assert state is None


async def test_cancel_and_wait_short_circuits_for_already_terminal(mongo_db):
    """A done/failed/cancelled process returns its state immediately."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "cawterm"

    async def quick(ctx):  # noqa: ARG001
        return

    task_inst = TaskInstance(
        process_id="p.quick", name="Quick", params={}, execute=quick,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix, get_task_definitions=gen,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch_and_wait("p.quick")
        state = await optio.cancel_and_wait("p.quick")
        assert state == "done"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_cancel_and_wait_raises_timeout_when_force_cancel_neutered(mongo_db):
    """If force_cancel never converges, the internal ceiling fires."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "cawto"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.2,
    )

    # Patch force_cancel to be a no-op so the supervisor never actually
    # transitions the task. The ceiling should fire.
    async def _noop(_oid):
        return
    optio._executor.force_cancel = _noop  # type: ignore[assignment]

    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.2)
        with pytest.raises(asyncio.TimeoutError):
            await optio.cancel_and_wait("p.stub")
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py -k "cancel_and_wait" -v`

Expected: FAIL — `cancel_and_wait` does not exist.

- [ ] **Step 3: Implement `cancel_and_wait`**

In `packages/optio-core/src/optio_core/lifecycle.py`, add the method right after `cancel`:

```python
    async def cancel_and_wait(self, process_id: str) -> str | None:
        """Cancel and wait until the process reaches a terminal state.

        Returns the terminal state ('cancelled', 'failed', 'done', ...) or
        None if the process does not exist. Raises asyncio.TimeoutError if
        the process has not reached a terminal state within
        cancel_grace_seconds + 25s — strictly a backstop against supervisor
        or DB anomalies.
        """
        import time as _time
        proc = await get_process_by_process_id(
            self._config.mongo_db, self._config.prefix, process_id,
        )
        if proc is None:
            return None

        await self.cancel(process_id)

        ceiling = self._config.cancel_grace_seconds + 25.0
        deadline = _time.monotonic() + ceiling
        while True:
            proc = await get_process_by_process_id(
                self._config.mongo_db, self._config.prefix, process_id,
            )
            if proc is None:
                return None
            state = proc["status"]["state"]
            if state not in ACTIVE_STATES:
                return state
            if _time.monotonic() >= deadline:
                raise asyncio.TimeoutError(
                    f"Process {process_id} did not reach terminal state within {ceiling}s"
                )
            await asyncio.sleep(0.1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py -k "cancel_and_wait" -v`

Expected: PASS for all five.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "feat(optio-core): add Optio.cancel_and_wait with hard timeout backstop"
```

---

## Task 10: Unify `shutdown()` on the supervisor mechanism

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:280-327`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:365-400` (delete `_force_finalize_stuck_processes`)
- Modify: `packages/optio-core/tests/test_lifecycle_reconciliation.py:121-225`
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_shutdown_finalizes_mixed_cooperative_and_stubborn_tasks(mongo_db):
    """Mixed tasks: cooperators -> 'cancelled', stubborn -> 'failed'."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "shutmix"

    async def cooperative(ctx):
        for _ in range(200):
            if ctx.cancellation_flag.is_set():
                return
            await asyncio.sleep(0.05)

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    tasks = [
        TaskInstance(process_id="p.coop", name="Coop", params={}, execute=cooperative),
        TaskInstance(process_id="p.stub", name="Stub", params={}, execute=stubborn),
    ]
    async def gen(_s, _f):
        return tasks

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.4,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.coop")
        await optio.launch("p.stub")
        await asyncio.sleep(0.3)

        await optio.shutdown()

        coop = await optio.get_process("p.coop")
        stub = await optio.get_process("p.stub")
        assert coop["status"]["state"] == "cancelled"
        assert stub["status"]["state"] == "failed"
        assert "Task did not unwind within cancellation grace period" in stub["status"]["error"]
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_shutdown_grace_seconds_override_honoured(mongo_db):
    """shutdown(grace_seconds=X) overrides the configured default for that call."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "shutov"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=10.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.3)

        # Even though the config grace is 10s, override to 0.3s.
        import time as _time
        t0 = _time.monotonic()
        await optio.shutdown(grace_seconds=0.3)
        elapsed = _time.monotonic() - t0
        # Should finish well under 10 seconds.
        assert elapsed < 6.0

        proc = await optio.get_process("p.stub")
        assert proc["status"]["state"] == "failed"
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
```

- [ ] **Step 2: Run tests to confirm initial state**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py -k "shutdown" -v`

Expected: likely PASS for `test_shutdown_finalizes_mixed_cooperative_and_stubborn_tasks` (Task 7's interim shutdown is already mostly correct), FAIL or hang for `test_shutdown_grace_seconds_override_honoured` because the current shutdown sets the cooperative flag but does **not** set per-entry deadlines, so the supervisor never force-cancels stubborn tasks via the override path. Confirm the failure mode.

- [ ] **Step 3: Rewrite `shutdown()`**

In `packages/optio-core/src/optio_core/lifecycle.py`, replace the existing `shutdown` method (lines 280-327) with:

```python
    async def shutdown(self, grace_seconds: float | None = None) -> None:
        """Graceful shutdown unified on the deadline-cancel mechanism.

        Args:
            grace_seconds: How long to wait for cooperating tasks to unwind
                after the cooperative flag + deadline are set. Defaults to
                config.cancel_grace_seconds. Tasks past their deadline are
                force-cancelled by the supervisor (or, if the supervisor has
                already stopped, by direct executor.force_cancel calls below).
        """
        import time as _time

        logger.info("Shutdown requested")
        self._running = False

        # 1. Heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # 2. Consumer
        if self._consumer:
            self._consumer.stop()
        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()

        # 3. Cancel everything via the unified mechanism.
        grace = (
            grace_seconds
            if grace_seconds is not None
            else self._config.cancel_grace_seconds
        )
        if self._executor:
            now_mono = _time.monotonic()
            for oid in list(self._executor._cancellation_flags.keys()):
                entry = self._executor._cancellation_flags.get(oid)
                if entry is None:
                    continue
                entry.flag.set()
                if entry.deadline is None:
                    entry.deadline = now_mono + grace

            # Wait for entries to drain. The supervisor handles force-cancel.
            ceiling = _time.monotonic() + grace + 5.0
            while self._executor._cancellation_flags and _time.monotonic() < ceiling:
                await asyncio.sleep(0.1)

            # Belt and braces: anything still left, force-cancel directly.
            # (Handles the case where the supervisor was slow or already stopped.)
            for oid in list(self._executor._cancellation_flags.keys()):
                await self._executor.force_cancel(oid)

        # 4. Stop supervisor (after final force-cancel pass).
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            self._supervisor_task = None

        # 5. Redis
        if self._redis:
            await self._redis.aclose()

        logger.info("Shutdown complete")
```

- [ ] **Step 4: Delete `_force_finalize_stuck_processes`**

In the same file, remove the entire `_force_finalize_stuck_processes` method (current lines 365-400). Verify there are no remaining callers:

```bash
grep -rn "_force_finalize_stuck_processes" packages/optio-core
```

Expected: only matches inside the lifecycle test file we update next.

- [ ] **Step 5: Update `tests/test_lifecycle_reconciliation.py`**

In `packages/optio-core/tests/test_lifecycle_reconciliation.py`, the test `test_shutdown_force_finalizes_uncooperative_task` (line 121) and the test that pokes `_force_finalize_stuck_processes` directly (around lines 190-225) need to be updated.

Read the full file first, then for each test that asserts `_force_finalize_stuck_processes` behaviour, replace direct calls to `_force_finalize_stuck_processes` with the equivalent assertion: drive the same uncooperative-task scenario via `optio.shutdown(grace_seconds=0.2)` and check the resulting Mongo state. The canonical error string changes from `"Task did not exit within shutdown grace period"` to `"Task did not unwind within cancellation grace period"` — update the assertion accordingly.

If a test was specifically pinning the conditional-update no-op behaviour for already-terminal rows, port it to call `_write_force_cancelled_state` directly:

```python
from optio_core._force_cancel import _write_force_cancelled_state
updated = await _write_force_cancelled_state(mongo_db, prefix, oid)
assert updated is False  # row was already terminal
```

(There may also be one or two assertion message tweaks; run the suite and adjust.)

- [ ] **Step 6: Run all tests**

Run: `cd packages/optio-core && python -m pytest tests/ -v`

Expected: PASS across the full suite.

- [ ] **Step 7: Verify no orphaned references**

```bash
grep -rn "_force_finalize_stuck_processes\|Task did not exit within shutdown grace period" packages/optio-core
```

Expected: no matches.

- [ ] **Step 8: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_lifecycle_reconciliation.py packages/optio-core/tests/test_deadline_cancel.py
git commit -m "refactor(optio-core): unify shutdown on deadline-cancel mechanism"
```

---

## Task 11: Add the remaining spec scenarios (re-entry idempotency, asyncio.to_thread)

**Files:**
- Test: `packages/optio-core/tests/test_deadline_cancel.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadline_cancel.py`:

```python
async def test_re_entry_idempotency_does_not_refresh_deadline(mongo_db):
    """Two cancel() calls 1s apart: deadline set by the first stays in force."""
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "reidem"

    async def stubborn(ctx):  # noqa: ARG001
        while True:
            await asyncio.sleep(0.05)

    task_inst = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=1.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.stub")
        await asyncio.sleep(0.2)

        await optio.cancel("p.stub")
        oid = next(iter(optio._executor._cancellation_flags))
        first_deadline = optio._executor._cancellation_flags[oid].deadline

        await asyncio.sleep(1.0)  # past the first deadline; supervisor may fire
        # Refresh the entry pointer; it may already be gone if force-cancelled.
        # Either way, calling cancel again must not raise and must be a no-op
        # on the deadline (if entry still exists).
        await optio.cancel("p.stub")
        if oid in optio._executor._cancellation_flags:
            assert optio._executor._cancellation_flags[oid].deadline == first_deadline

        # Eventually terminal.
        import time as _time
        ceil = _time.monotonic() + 4.0
        while _time.monotonic() < ceil:
            proc = await optio.get_process("p.stub")
            if proc and proc["status"]["state"] in ("failed", "cancelled"):
                break
            await asyncio.sleep(0.1)
        proc = await optio.get_process("p.stub")
        assert proc["status"]["state"] == "failed"
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_to_thread_blocked_task_reaches_failed_state(mongo_db):
    """Task blocked inside asyncio.to_thread: Mongo state still goes to 'failed'.

    The thread is allowed to outlive the test. We use a short sleep so the
    underlying thread terminates before pytest tears down the event loop.
    """
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    prefix = "thrblk"

    def _block_briefly() -> None:
        import time as _t
        _t.sleep(2.0)

    async def thread_blocked(ctx):  # noqa: ARG001
        await asyncio.to_thread(_block_briefly)

    task_inst = TaskInstance(
        process_id="p.thr", name="Thr", params={}, execute=thread_blocked,
    )
    async def gen(_s, _f):
        return [task_inst]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        await optio.launch("p.thr")
        await asyncio.sleep(0.2)

        await optio.cancel("p.thr")

        import time as _time
        ceil = _time.monotonic() + 4.0
        while _time.monotonic() < ceil:
            proc = await optio.get_process("p.thr")
            if proc and proc["status"]["state"] == "failed":
                break
            await asyncio.sleep(0.1)
        proc = await optio.get_process("p.thr")
        assert proc["status"]["state"] == "failed"
        assert "Task did not unwind within cancellation grace period" in proc["status"]["error"]
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
        # Let the orphaned thread finish before the loop tears down.
        await asyncio.sleep(2.5)
```

- [ ] **Step 2: Run the new tests**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py::test_re_entry_idempotency_does_not_refresh_deadline tests/test_deadline_cancel.py::test_to_thread_blocked_task_reaches_failed_state -v`

Expected: PASS — the underlying mechanism is already in place.

- [ ] **Step 3: Run the full deadline-cancel suite end-to-end**

Run: `cd packages/optio-core && python -m pytest tests/test_deadline_cancel.py -v`

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/tests/test_deadline_cancel.py
git commit -m "test(optio-core): cover re-entry idempotency and to_thread limitation"
```

---

## Task 12: Full suite + lint/type checks

**Files:** none new

- [ ] **Step 1: Full optio-core suite**

Run: `cd packages/optio-core && python -m pytest tests/ -v`

Expected: ALL PASS (no skips except those already-skipped before this branch).

- [ ] **Step 2: Repo-wide test sanity check**

Run: `python -m pytest packages/ -q --ignore=packages/optio-core/.venv 2>&1 | tail -40`

Expected: no regressions in adjacent packages.

- [ ] **Step 3: Type check**

If the project has a tsc-style command for python (e.g. `mypy`), run it as the repo configures it. Otherwise skip with a note in the commit body.

- [ ] **Step 4: Verify the spec checklist**

Open `docs/2026-04-29-deadline-driven-cancel-design.md` and walk the "Testing" section. Confirm the test file covers every listed scenario:

1. Cooperative cancel — `test_cancel_and_wait_cooperative` + assertion
2. Stubborn cancel — `test_force_cancel_writes_failed_state_for_stubborn_task`, `test_supervisor_force_cancels_past_deadline_entries`, `test_cancel_and_wait_stubborn_returns_failed`
3. Re-entry idempotency — `test_re_entry_idempotency_does_not_refresh_deadline`
4. `cancel_and_wait` returns terminal state — `test_cancel_and_wait_cooperative` + `test_cancel_and_wait_stubborn_returns_failed`
5. `cancel_and_wait` on missing process_id — `test_cancel_and_wait_returns_none_for_missing`
6. `cancel_and_wait` raises TimeoutError — `test_cancel_and_wait_raises_timeout_when_force_cancel_neutered`
7. Already-terminal short-circuit — `test_cancel_and_wait_short_circuits_for_already_terminal`
8. Shutdown unification — `test_shutdown_finalizes_mixed_cooperative_and_stubborn_tasks`
9. Shutdown override — `test_shutdown_grace_seconds_override_honoured`
10. `asyncio.to_thread`-blocked task — `test_to_thread_blocked_task_reaches_failed_state`

Plus the structural tests added along the way: `test_init_*`, `test_executor_tracks_*`, `test_request_cancel_with_deadline_*`, `test_write_force_cancelled_state_*`, `test_handle_cancel_records_deadline_*`.

- [ ] **Step 5: Commit a tracking entry if anything was deferred**

If any spec scenario was skipped (e.g. environment couldn't run `to_thread` test), document it in a follow-up commit:

```bash
git commit --allow-empty -m "docs(optio-core): note deferred deadline-cancel coverage [details]"
```

Otherwise no commit needed.

---

## Self-Review Checklist

- ✅ Spec coverage: all 10 testing scenarios mapped to tasks.
- ✅ No placeholders: every step has concrete code or commands.
- ✅ Type consistency: `_CancelEntry`, `request_cancel_with_deadline`, `force_cancel`, `_write_force_cancelled_state`, `cancel_and_wait`, `_supervisor_loop` names match across tasks.
- ✅ Base revision header present.
- ✅ TDD discipline: every code task starts with a failing test.
- ✅ Frequent commits: each task ends with one focused commit.
