# Partial Task Regeneration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional metadata filter that scopes `Optio.resync()` (and the optio-api resync endpoint) to a subset of tasks, so callers can refresh one logical group without churning unrelated tasks, schedules, or DB records.

**Architecture:** A `ProcessMetadataFilter` type (flat AND-equality dict) is threaded end-to-end. The executor's task registry and the scheduler's job map are upgraded to store full `TaskInstance` objects so each layer can self-scope cleanup using `task.metadata`. The `optio-api` resync endpoint accepts an optional `metadataFilter` body field and forwards it through the publisher to the Redis stream payload that the optio-core consumer dispatches.

**Tech Stack:** Python 3 (optio-core: pytest, motor, MongoDB, APScheduler), TypeScript (optio-api: vitest, ioredis-mock, fastify/express/Next.js adapters), Redis Streams.

**Spec:** [`docs/2026-04-27-partial-task-regen-design.md`](2026-04-27-partial-task-regen-design.md)

---

## File Structure

### optio-core (Python)

- **Modify** `packages/optio-core/src/optio_core/models.py` — add `ProcessMetadataFilter` type alias and `matches_filter` helper.
- **Modify** `packages/optio-core/src/optio_core/store.py` — `remove_stale_processes` gains optional `metadata_filter` parameter.
- **Modify** `packages/optio-core/src/optio_core/executor.py` — `_task_registry` stores `dict[str, TaskInstance]` (was `dict[str, Callable]`); `register_tasks` accepts optional `metadata_filter`; `launch_process` reads `.execute` from the stored task.
- **Modify** `packages/optio-core/src/optio_core/scheduler.py` — `_job_ids: set[str]` becomes `_jobs: dict[str, TaskInstance]`; `sync_schedules` accepts optional `metadata_filter`.
- **Modify** `packages/optio-core/src/optio_core/lifecycle.py` — drop `self._tasks`; `_sync_definitions`, `_handle_resync`, and `Optio.resync` accept `metadata_filter`; `_handle_resync` honors `clean=True` + filter; `adhoc_define` stores full `TaskInstance` in the registry; `get_task_definitions` callback signature gains a second parameter.
- **Modify** `packages/optio-core/tests/test_models.py` — extend with `matches_filter` cases.
- **Modify** `packages/optio-core/tests/test_store.py` — extend with filter cases for `remove_stale_processes`.
- **Modify** `packages/optio-core/tests/test_executor.py` — extend with registry-shape and partial-register cases.
- **Create** `packages/optio-core/tests/test_scheduler.py` — new test file for scheduler filter semantics.
- **Create** `packages/optio-core/tests/test_resync.py` — new end-to-end test file for partial resync via `Optio.resync()`.

### Downstream callback callers (Python)

- **Modify** `packages/optio-demo/src/optio_demo/tasks/__init__.py` — `get_task_definitions` accepts `metadata_filter` (ignored).
- **Modify** `examples/test-app/tasks/__init__.py` — same.

### optio-api (TypeScript)

- **Create** `packages/optio-api/src/types.ts` — `ProcessMetadataFilter` type alias (or extend an existing types file if present — see Task 7).
- **Modify** `packages/optio-api/src/publisher.ts` — `publishResync` accepts optional `metadataFilter`.
- **Modify** `packages/optio-api/src/handlers.ts` — `resyncProcesses` forwards `metadataFilter`.
- **Modify** `packages/optio-api/src/adapters/fastify.ts` — adapter parses `body.metadataFilter`.
- **Modify** `packages/optio-api/src/adapters/express.ts` — same.
- **Modify** `packages/optio-api/src/adapters/nextjs-app.ts` — same.
- **Modify** `packages/optio-api/src/adapters/nextjs-pages.ts` — same.
- **Modify** `packages/optio-api/src/__tests__/publisher.test.ts` — extend with `metadataFilter` cases.
- **Modify** `packages/optio-api/src/adapters/__tests__/fastify.test.ts` — extend resync test with `metadataFilter`.
- **Modify** `packages/optio-api/src/adapters/__tests__/express.test.ts` — same.
- **Modify** `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts` — same.
- **Modify** `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts` — same.

---

## Tooling notes

- Working directory for all Python work: `packages/optio-core/`. Run tests with `pytest tests/<file>::<test> -v` (the package's pytest config picks up the `tests/` dir; conftest provides a `mongo_db` fixture against `MONGO_URL`).
- Working directory for all TS work: `packages/optio-api/`. Run tests with `pnpm test -- <pattern>` from the package directory; vitest is the runner.
- MongoDB: must be running. The repo's standard is to run MongoDB via Docker (no local mongod). `MONGO_URL` env var defaults to `mongodb://localhost:27017`.
- Do not use `npx` for `tsc`. Use `node_modules/.bin/tsc` directly when type-checking.
- Frequent commits: each task ends with a single commit on the current branch (`feat/partial-task-regen`).

---

## Task 1: `ProcessMetadataFilter` type and `matches_filter` helper

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py`
- Modify: `packages/optio-core/tests/test_models.py`

- [ ] **Step 1: Write failing tests for `matches_filter`**

Append to `packages/optio-core/tests/test_models.py`:

```python
from optio_core.models import matches_filter


def test_matches_filter_none_filter_matches_anything():
    assert matches_filter({}, None) is True
    assert matches_filter({"group": "ingest"}, None) is True


def test_matches_filter_empty_filter_matches_anything():
    assert matches_filter({}, {}) is True
    assert matches_filter({"group": "ingest"}, {}) is True


def test_matches_filter_equality_match():
    assert matches_filter({"group": "ingest"}, {"group": "ingest"}) is True


def test_matches_filter_equality_mismatch():
    assert matches_filter({"group": "ingest"}, {"group": "etl"}) is False


def test_matches_filter_missing_key_is_mismatch():
    assert matches_filter({}, {"group": "ingest"}) is False


def test_matches_filter_and_semantics():
    metadata = {"group": "ingest", "tier": "fast"}
    assert matches_filter(metadata, {"group": "ingest", "tier": "fast"}) is True
    assert matches_filter(metadata, {"group": "ingest", "tier": "slow"}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_models.py -v -k matches_filter`
Expected: FAIL with `ImportError: cannot import name 'matches_filter' from 'optio_core.models'`.

- [ ] **Step 3: Implement type alias and helper in `models.py`**

In `packages/optio-core/src/optio_core/models.py`, change the `from typing` import line and add the type alias + helper at the bottom of the file (after the existing dataclasses):

```python
from typing import Any, Callable, Awaitable, Union, TypeAlias
```

```python
ProcessMetadataFilter: TypeAlias = dict[str, Any]


def matches_filter(
    metadata: dict[str, Any],
    filter: ProcessMetadataFilter | None,
) -> bool:
    """Return True iff every key in `filter` is present and equal in `metadata`.

    A `None` or empty `filter` matches anything (used to mean "no filter").
    """
    if not filter:
        return True
    return all(metadata.get(k) == v for k, v in filter.items())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_models.py -v -k matches_filter`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py packages/optio-core/tests/test_models.py
git commit -m "feat(optio-core): ProcessMetadataFilter type and matches_filter helper"
```

---

## Task 2: `remove_stale_processes` accepts a metadata filter

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py`
- Modify: `packages/optio-core/tests/test_store.py`

- [ ] **Step 1: Write failing test in `test_store.py`**

Append to `packages/optio-core/tests/test_store.py`:

```python
async def test_remove_stale_processes_with_filter(mongo_db):
    t_in1 = TaskInstance(
        execute=dummy_execute, process_id="in_keep", name="In Keep",
        metadata={"group": "ingest"},
    )
    t_in2 = TaskInstance(
        execute=dummy_execute, process_id="in_drop", name="In Drop",
        metadata={"group": "ingest"},
    )
    t_out = TaskInstance(
        execute=dummy_execute, process_id="other", name="Other",
        metadata={"group": "etl"},
    )
    await upsert_process(mongo_db, "test", t_in1)
    await upsert_process(mongo_db, "test", t_in2)
    await upsert_process(mongo_db, "test", t_out)

    count = await remove_stale_processes(
        mongo_db, "test", {"in_keep"}, metadata_filter={"group": "ingest"},
    )

    assert count == 1
    assert await get_process_by_process_id(mongo_db, "test", "in_keep") is not None
    assert await get_process_by_process_id(mongo_db, "test", "in_drop") is None
    assert await get_process_by_process_id(mongo_db, "test", "other") is not None


async def test_remove_stale_processes_filter_none_is_full_sweep(mongo_db):
    t1 = TaskInstance(
        execute=dummy_execute, process_id="alpha", name="A",
        metadata={"group": "ingest"},
    )
    t2 = TaskInstance(
        execute=dummy_execute, process_id="beta", name="B",
        metadata={"group": "etl"},
    )
    await upsert_process(mongo_db, "test", t1)
    await upsert_process(mongo_db, "test", t2)

    count = await remove_stale_processes(mongo_db, "test", {"alpha"})

    assert count == 1
    assert await get_process_by_process_id(mongo_db, "test", "alpha") is not None
    assert await get_process_by_process_id(mongo_db, "test", "beta") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_store.py -v -k remove_stale_processes`
Expected: `test_remove_stale_processes` (existing) PASSES; the two new tests FAIL — the new one with `unexpected keyword argument 'metadata_filter'`, the second with `count == 0` (because the existing test sweeps unconditionally; this new test only adds another assertion path but verifies `None` keeps the old behavior).

(If the second test happens to pass on the unmodified code, that's fine — it just means the `None` case already works; keep it as a regression guard.)

- [ ] **Step 3: Implement filter param in `remove_stale_processes`**

Replace the existing `remove_stale_processes` in `packages/optio-core/src/optio_core/store.py` with:

```python
from optio_core.models import (
    TaskInstance, ProcessStatus, Progress, InnerAuth, ProcessMetadataFilter,
)
```

(Update the existing `from optio_core.models import ...` line to also import `ProcessMetadataFilter`.)

```python
async def remove_stale_processes(
    db: AsyncIOMotorDatabase,
    prefix: str,
    valid_process_ids: set[str],
    metadata_filter: ProcessMetadataFilter | None = None,
) -> int:
    """Remove process records whose processId is not in the valid set.

    Only removes root processes (parentId is None). When `metadata_filter`
    is provided, the deletion is scoped to records whose stored `metadata`
    matches every key/value in the filter (flat AND-equality).
    """
    coll = _collection(db, prefix)
    query: dict[str, Any] = {
        "processId": {"$nin": list(valid_process_ids)},
        "parentId": None,
    }
    if metadata_filter:
        for k, v in metadata_filter.items():
            query[f"metadata.{k}"] = v
    result = await coll.delete_many(query)
    return result.deleted_count
```

(Add `from typing import Any` at the top of `store.py` if not already imported.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_store.py -v -k remove_stale_processes`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/store.py packages/optio-core/tests/test_store.py
git commit -m "feat(optio-core): scope remove_stale_processes by metadata filter"
```

---

## Task 3: Executor stores full `TaskInstance` and accepts metadata filter

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (one line in `adhoc_define`)
- Modify: `packages/optio-core/tests/test_executor.py`

- [ ] **Step 1: Write failing tests in `test_executor.py`**

Append to `packages/optio-core/tests/test_executor.py`:

```python
from optio_core.models import TaskInstance


async def test_register_tasks_stores_full_taskinstance(mongo_db):
    async def my_task(ctx):
        pass

    task = TaskInstance(
        execute=my_task, process_id="reg1", name="Reg1",
        metadata={"group": "ingest"},
    )

    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    stored = executor._task_registry["reg1"]
    assert stored is task                       # full TaskInstance, not just the callable
    assert stored.metadata == {"group": "ingest"}
    assert stored.execute is my_task            # lookup helper for launch path


async def test_register_tasks_partial_filter_keeps_out_of_scope(mongo_db):
    async def t(ctx):
        pass

    in_old = TaskInstance(execute=t, process_id="in_old", name="X", metadata={"group": "ingest"})
    out_old = TaskInstance(execute=t, process_id="out_old", name="Y", metadata={"group": "etl"})
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([in_old, out_old])

    in_new = TaskInstance(execute=t, process_id="in_new", name="Z", metadata={"group": "ingest"})
    executor.register_tasks([in_new], metadata_filter={"group": "ingest"})

    assert "in_old" not in executor._task_registry  # in-scope, dropped
    assert "out_old" in executor._task_registry     # out-of-scope, kept
    assert "in_new" in executor._task_registry      # added


async def test_register_tasks_partial_filter_with_overlap(mongo_db):
    async def t(ctx):
        pass

    keep = TaskInstance(execute=t, process_id="keep", name="K", metadata={"group": "ingest"})
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([keep])

    keep_v2 = TaskInstance(execute=t, process_id="keep", name="K2", metadata={"group": "ingest"})
    executor.register_tasks([keep_v2], metadata_filter={"group": "ingest"})

    assert executor._task_registry["keep"].name == "K2"


async def test_register_tasks_no_filter_full_replace(mongo_db):
    async def t(ctx):
        pass

    a = TaskInstance(execute=t, process_id="a", name="A", metadata={"group": "ingest"})
    b = TaskInstance(execute=t, process_id="b", name="B", metadata={"group": "etl"})
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([a, b])

    c = TaskInstance(execute=t, process_id="c", name="C", metadata={"group": "ingest"})
    executor.register_tasks([c])

    assert set(executor._task_registry) == {"c"}
```

Update `test_launch_basic_process` and `test_launch_failing_process` (they assert nothing about the registry shape and continue to pass), but verify that the launch path still works after the refactor.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_executor.py -v -k "register_tasks or test_launch_basic_process"`
Expected: registry tests FAIL — `executor._task_registry["reg1"]` is the `Callable`, not a `TaskInstance`. Existing launch tests still PASS.

- [ ] **Step 3: Refactor `Executor`**

In `packages/optio-core/src/optio_core/executor.py`:

Replace the registry attribute and `register_tasks`:

```python
from optio_core.models import (
    TaskInstance, ProcessStatus, Progress, ProcessMetadataFilter, matches_filter,
)
```

(Adjust the existing models import to include the new symbols.)

```python
class Executor:
    def __init__(self, db, prefix, services):
        # ... existing fields unchanged ...
        self._task_registry: dict[str, TaskInstance] = {}

    def register_tasks(
        self,
        tasks: list[TaskInstance],
        metadata_filter: ProcessMetadataFilter | None = None,
    ) -> None:
        """Register task definitions by processId.

        With no `metadata_filter`, the registry is fully replaced (current
        behaviour). With a filter, only entries whose existing `metadata`
        matches the filter are eligible for removal; everything outside the
        scope is preserved. Tasks in the new list are then upserted.
        """
        if not metadata_filter:
            self._task_registry = {t.process_id: t for t in tasks}
            return
        new_ids = {t.process_id for t in tasks}
        for pid in list(self._task_registry):
            existing = self._task_registry[pid]
            if matches_filter(existing.metadata, metadata_filter) and pid not in new_ids:
                del self._task_registry[pid]
        for t in tasks:
            self._task_registry[t.process_id] = t
```

Update the launch lookup. Find the existing line in `launch_process`:

```python
return await self._execute_process(proc, self._task_registry.get(process_id), resume=resume)
```

Replace with:

```python
task = self._task_registry.get(process_id)
return await self._execute_process(
    proc, task.execute if task else None, resume=resume,
)
```

In `_cleanup_ephemeral` the registry is mutated via `self._task_registry.pop(process_id, None)` — that still works (key removal is shape-agnostic). No change required there.

- [ ] **Step 4: Update `adhoc_define` in `lifecycle.py`**

Find the existing line in `Optio.adhoc_define` (currently `lifecycle.py:165`):

```python
self._executor._task_registry[task.process_id] = task.execute
```

Replace with:

```python
self._executor._task_registry[task.process_id] = task
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_executor.py -v`
Expected: all tests pass (the four new ones + existing ones, including launch and cancellation flows).

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_executor.py
git commit -m "refactor(optio-core): executor registry stores TaskInstance + filter-aware register_tasks"
```

---

## Task 4: Scheduler stores `TaskInstance` per job and accepts metadata filter

**Files:**
- Modify: `packages/optio-core/src/optio_core/scheduler.py`
- Create: `packages/optio-core/tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests in a new file**

Create `packages/optio-core/tests/test_scheduler.py`:

```python
"""Tests for ProcessScheduler — partial sync semantics."""

import pytest
from optio_core.models import TaskInstance
from optio_core.scheduler import ProcessScheduler


class FakeAPScheduler:
    """Minimal stand-in for apscheduler.AsyncScheduler used by ProcessScheduler."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}

    async def add_job(self, fn, trigger=None, id=None, args=None):
        self.jobs[id] = {"fn": fn, "trigger": trigger, "args": args}

    async def remove_job(self, job_id: str):
        if job_id in self.jobs:
            del self.jobs[job_id]
        else:
            raise KeyError(job_id)


async def _noop(_pid: str):
    pass


def _ps_with_fake() -> tuple[ProcessScheduler, FakeAPScheduler]:
    ps = ProcessScheduler(launch_fn=_noop)
    fake = FakeAPScheduler()
    ps._scheduler = fake  # bypass start()
    return ps, fake


def _t(pid: str, *, group: str, schedule: str = "0 * * * *") -> TaskInstance:
    async def fn(ctx):  # pragma: no cover
        pass
    return TaskInstance(
        execute=fn, process_id=pid, name=pid,
        metadata={"group": group}, schedule=schedule,
    )


@pytest.mark.asyncio
async def test_sync_schedules_full_replace_no_filter():
    ps, fake = _ps_with_fake()
    a = _t("a", group="ingest")
    b = _t("b", group="etl")
    await ps.sync_schedules([a, b])
    assert set(fake.jobs) == {"sched_a", "sched_b"}
    assert set(ps._jobs) == {"sched_a", "sched_b"}

    # full replace: only [a] remains
    await ps.sync_schedules([a])
    assert set(fake.jobs) == {"sched_a"}
    assert set(ps._jobs) == {"sched_a"}


@pytest.mark.asyncio
async def test_sync_schedules_partial_keeps_out_of_scope():
    ps, fake = _ps_with_fake()
    in1 = _t("in1", group="ingest")
    in2 = _t("in2", group="ingest")
    out1 = _t("out1", group="etl")
    await ps.sync_schedules([in1, in2, out1])
    assert set(fake.jobs) == {"sched_in1", "sched_in2", "sched_out1"}

    # Partial sync: callback returned only [in1]; in2 should be dropped, out1 preserved.
    await ps.sync_schedules([in1], metadata_filter={"group": "ingest"})
    assert set(fake.jobs) == {"sched_in1", "sched_out1"}
    assert set(ps._jobs) == {"sched_in1", "sched_out1"}


@pytest.mark.asyncio
async def test_sync_schedules_partial_replaces_existing_inscope():
    ps, fake = _ps_with_fake()
    v1 = _t("a", group="ingest", schedule="0 * * * *")
    await ps.sync_schedules([v1])

    v2 = _t("a", group="ingest", schedule="*/5 * * * *")
    await ps.sync_schedules([v2], metadata_filter={"group": "ingest"})

    assert set(fake.jobs) == {"sched_a"}
    assert ps._jobs["sched_a"].schedule == "*/5 * * * *"


@pytest.mark.asyncio
async def test_sync_schedules_skips_tasks_with_no_schedule():
    ps, fake = _ps_with_fake()
    scheduled = _t("a", group="ingest")
    unscheduled = _t("b", group="ingest", schedule=None)  # type: ignore[arg-type]
    unscheduled.schedule = None
    await ps.sync_schedules([scheduled, unscheduled])
    assert set(fake.jobs) == {"sched_a"}
    assert "sched_b" not in ps._jobs


@pytest.mark.asyncio
async def test_sync_schedules_no_apscheduler_is_noop():
    ps = ProcessScheduler(launch_fn=_noop)
    # _scheduler stays None
    await ps.sync_schedules([_t("a", group="ingest")])
    # No exception, no jobs tracked.
    assert ps._jobs == {} or not hasattr(ps, "_jobs")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_scheduler.py -v`
Expected: FAIL — `ps._jobs` does not exist (the current attribute is `_job_ids: set[str]`); some tests also fail because `sync_schedules` does not accept `metadata_filter`.

- [ ] **Step 3: Implement filter-aware `sync_schedules`**

Replace the body of `packages/optio-core/src/optio_core/scheduler.py` with:

```python
"""APScheduler integration for cron-based process triggering."""

import logging
from typing import Callable, Awaitable

from optio_core.models import TaskInstance, ProcessMetadataFilter, matches_filter

logger = logging.getLogger("optio_core_core.scheduler")


class ProcessScheduler:
    """Manages cron schedules for process execution.

    Uses APScheduler 4.x AsyncScheduler for async cron triggers.
    Falls back to a no-op if APScheduler is not available or fails.
    """

    def __init__(self, launch_fn: Callable[[str], Awaitable]):
        self._launch_fn = launch_fn
        self._scheduler = None
        self._jobs: dict[str, TaskInstance] = {}

    async def start(self) -> None:
        try:
            from apscheduler import AsyncScheduler
            self._scheduler = AsyncScheduler()
            await self._scheduler.__aenter__()
            logger.info("Scheduler started")
        except Exception as e:
            logger.warning(f"Could not start scheduler: {e}")
            self._scheduler = None

    async def stop(self) -> None:
        if self._scheduler:
            try:
                await self._scheduler.__aexit__(None, None, None)
            except Exception:
                pass
            logger.info("Scheduler stopped")

    async def sync_schedules(
        self,
        tasks: list,
        metadata_filter: ProcessMetadataFilter | None = None,
    ) -> None:
        """Sync APScheduler jobs against `tasks`.

        With no `metadata_filter`, every existing job is removed and the
        full task list is re-registered (current behaviour). With a filter,
        only jobs whose stored `TaskInstance.metadata` matches the filter
        are eligible for removal; out-of-scope jobs are preserved.
        """
        if not self._scheduler:
            return

        new_ids = {f"sched_{t.process_id}" for t in tasks if t.schedule}

        for job_id in list(self._jobs):
            existing = self._jobs[job_id]
            if metadata_filter is None:
                should_remove = True
            else:
                should_remove = (
                    matches_filter(existing.metadata, metadata_filter)
                    and job_id not in new_ids
                )
            if should_remove:
                try:
                    await self._scheduler.remove_job(job_id)
                except Exception:
                    pass
                del self._jobs[job_id]

        for task in tasks:
            if not task.schedule:
                continue
            job_id = f"sched_{task.process_id}"
            if job_id in self._jobs:
                try:
                    await self._scheduler.remove_job(job_id)
                except Exception:
                    pass
            try:
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger.from_crontab(task.schedule)
                await self._scheduler.add_job(
                    self._launch_fn,
                    trigger=trigger,
                    id=job_id,
                    args=[task.process_id],
                )
                self._jobs[job_id] = task
                logger.info(f"Scheduled {task.process_id}: {task.schedule}")
            except Exception as e:
                logger.error(f"Failed to schedule {task.process_id}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_scheduler.py -v`
Expected: 5 passed.

The fake scheduler stand-in lets us avoid building real `CronTrigger` objects — but the `sync_schedules` body imports `CronTrigger.from_crontab(task.schedule)`. The fake's `add_job` ignores `trigger`, so the import must still succeed. APScheduler is in the optio-core test dependencies; if `from apscheduler.triggers.cron import CronTrigger` raises, the production code logs and continues. The tests are designed to run regardless: they assert against `ps._jobs` and `fake.jobs`, both of which are populated only when `add_job` succeeds. Verify that the test environment has apscheduler installed; if not, install before running.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/scheduler.py packages/optio-core/tests/test_scheduler.py
git commit -m "refactor(optio-core): scheduler tracks TaskInstance per job + filter-aware sync_schedules"
```

---

## Task 5: `_sync_definitions` and callback signature accept the filter

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Create: `packages/optio-core/tests/test_resync.py`

This task changes the `get_task_definitions` callback signature (breaking) and threads the filter through `_sync_definitions`. Public `Optio.resync` and `_handle_resync` are updated in Task 6.

- [ ] **Step 1: Write failing tests in a new file**

Create `packages/optio-core/tests/test_resync.py`:

```python
"""End-to-end tests for partial task regeneration via Optio.resync()."""

import pytest
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import get_process_by_process_id


async def _noop(ctx):
    pass


def _t(pid: str, *, group: str, schedule: str | None = None) -> TaskInstance:
    return TaskInstance(
        execute=_noop, process_id=pid, name=pid,
        metadata={"group": group}, schedule=schedule,
    )


@pytest.mark.asyncio
async def test_full_resync_unchanged(mongo_db):
    tasks = [_t("a", group="ingest"), _t("b", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        return tasks

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    # Drop one task and resync — full sync removes the missing one.
    tasks.pop(1)
    await optio.resync()

    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "a") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "b") is None


@pytest.mark.asyncio
async def test_partial_resync_only_touches_in_scope(mongo_db):
    initial = [_t("ing1", group="ingest"), _t("ing2", group="ingest"), _t("etl1", group="etl")]
    state = {"tasks": list(initial), "filter_seen": None}

    async def get_tasks(services, metadata_filter=None):
        state["filter_seen"] = metadata_filter
        if not metadata_filter:
            return state["tasks"]
        return [t for t in state["tasks"] if all(t.metadata.get(k) == v for k, v in metadata_filter.items())]

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    # Caller updates only the ingest group: drop ing2, add ing3.
    state["tasks"] = [
        _t("ing1", group="ingest"),
        _t("ing3", group="ingest"),
        _t("etl1", group="etl"),
    ]
    await optio.resync(metadata_filter={"group": "ingest"})

    assert state["filter_seen"] == {"group": "ingest"}
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing1") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing3") is not None
    # ing2 was in the in-scope DB set but absent from the partial result -> deleted.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing2") is None
    # etl1 is out of scope -> preserved.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "etl1") is not None


@pytest.mark.asyncio
async def test_partial_resync_clean_scopes_delete(mongo_db):
    tasks = [_t("ing1", group="ingest"), _t("etl1", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        if not metadata_filter:
            return tasks
        return [t for t in tasks if all(t.metadata.get(k) == v for k, v in metadata_filter.items())]

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    await optio.resync(clean=True, metadata_filter={"group": "ingest"})

    # Re-imported in-scope row + preserved out-of-scope row.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing1") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "etl1") is not None


@pytest.mark.asyncio
async def test_partial_resync_drops_out_of_scope_returned_by_callback(mongo_db):
    """Callback ignores the filter and returns its full list. Framework
    must drop out-of-scope tasks before upsert/register/schedule.
    """
    full_set = [_t("ing1", group="ingest"), _t("etl1", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        # Deliberately ignore metadata_filter — return everything.
        return full_set

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    # Drop ing1 from the source list, then partial-resync the ingest group.
    full_set.pop(0)
    await optio.resync(metadata_filter={"group": "ingest"})

    # ing1 is in-scope and was absent from the (over-returned) list -> deleted.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing1") is None
    # etl1 is out of scope; even though the callback returned it, framework
    # must NOT have re-upserted, re-registered, or re-scheduled it. Existing
    # row simply survives untouched.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "etl1") is not None
    # And it should still be in the executor registry (not dropped, not
    # re-added: register_tasks was called with [] under the ingest filter).
    assert "etl1" in optio._executor._task_registry


@pytest.mark.asyncio
async def test_empty_filter_treated_as_full_sync(mongo_db):
    tasks = [_t("a", group="ingest"), _t("b", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        return tasks

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    tasks.pop(1)
    await optio.resync(metadata_filter={})

    # `{}` collapses to None -> full sweep removes b.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "a") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "b") is None
```

(All four tests will fail at this step because Task 6 hasn't run yet — `Optio.resync` and `_handle_resync` still have the old signatures. We accept this because Tasks 5 and 6 are tightly coupled and combining them in two commits is the right granularity.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_resync.py -v`
Expected: tests fail because `get_task_definitions` is called with one argument by `_sync_definitions`. (`get_tasks` accepts `metadata_filter=None` so works; the current `_sync_definitions` calls `self._config.get_task_definitions(self._config.services)` — single-arg — so tests still raise `TypeError: get_tasks() missing 1 required positional argument` only if we make the second arg required, which we won't. Acceptance: the test failures can come from the resync-method signature mismatch in Task 6; this is fine.)

- [ ] **Step 3: Update `_sync_definitions` and the config callable type**

In `packages/optio-core/src/optio_core/lifecycle.py`:

Update the import block:

```python
from optio_core.models import (
    TaskInstance, OptioConfig, ProcessStatus, ProcessMetadataFilter, matches_filter,
)
```

Update the type hint of the `get_task_definitions` parameter on `Optio.init`:

```python
get_task_definitions: Callable[
    [dict[str, Any], ProcessMetadataFilter | None],
    Awaitable[list[TaskInstance]],
] | None = None,
```

(Doc string: clarify the callback receives `(services, metadata_filter)` — `metadata_filter` is `None` for full sync.)

Replace the body of `_sync_definitions`:

```python
async def _sync_definitions(
    self,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> None:
    """Run the task generator and sync with database, optionally scoped."""
    if self._config.get_task_definitions is None:
        return

    tasks = await self._config.get_task_definitions(
        self._config.services, metadata_filter,
    )

    # Framework guarantees only in-scope tasks reach downstream layers,
    # so callback authors may ignore `metadata_filter` if they prefer.
    if metadata_filter:
        tasks = [t for t in tasks if matches_filter(t.metadata, metadata_filter)]

    for task in tasks:
        await upsert_process(self._config.mongo_db, self._config.prefix, task)

    valid_ids = {t.process_id for t in tasks}
    removed = await remove_stale_processes(
        self._config.mongo_db, self._config.prefix, valid_ids, metadata_filter,
    )
    if removed:
        logger.info(f"Removed {removed} stale process records")

    self._executor.register_tasks(tasks, metadata_filter)
    await self._scheduler.sync_schedules(tasks, metadata_filter)

    scope = "(all)" if not metadata_filter else f"(filter={metadata_filter})"
    logger.info(f"Synced {len(tasks)} task definitions {scope}")
```

Remove the `self._tasks: list[TaskInstance] = []` field from `Optio.__init__` (it is no longer the source of truth; the executor's registry and the scheduler's `_jobs` dict are). Search for any other reference to `self._tasks` in the file and remove or rewrite it. As of the spec snapshot, no other reference exists.

Also update `OptioConfig` in `models.py` if it carries a typed `get_task_definitions` field (it does — see lines around 75–85 of `models.py`). Update its type:

```python
get_task_definitions: Callable[
    [dict[str, Any], "ProcessMetadataFilter | None"],
    Awaitable[list["TaskInstance"]],
] | None = None
```

(Use forward references / put the type alias above the `OptioConfig` dataclass if needed to avoid ordering issues.)

- [ ] **Step 4: Update `Optio.init` callsite**

The startup call at the end of `Optio.init` (currently `lifecycle.py:106`) is:

```python
await self._sync_definitions()
```

Leave this unchanged — initial sync is always a full sync (`metadata_filter=None`).

- [ ] **Step 5: Update optio-core test fixtures to the two-arg callback signature**

The signature change in this task means every in-tree callback that defines `get_task_definitions(services)` (single-arg) will fail at runtime with `TypeError: ... takes 1 positional argument but 2 were given`. Update the optio-core test fixtures now so the test suite stays green for this commit. (Sibling packages `optio-demo` and `examples/test-app` are updated in Task 6b — they have their own test suites and are not run by Task 5's pytest invocation.)

Files to edit (3 files, ~20 callsites):

- `packages/optio-core/tests/test_lifecycle_reconciliation.py`
- `packages/optio-core/tests/test_no_redis.py`
- `packages/optio-core/tests/test_integration.py`

For each callback definition (look for `async def get_tasks(services)`, `async def _get_tasks(services)`, `async def get_defs(services)`, `async def _tasks(services)`, etc.), append a second parameter that defaults to `None` and is unused:

```python
# Before
async def get_tasks(services):
    return [...]

# After
async def get_tasks(services, metadata_filter=None):
    return [...]
```

Run: `cd packages/optio-core && grep -rn "async def.*services).*:" tests/test_lifecycle_reconciliation.py tests/test_no_redis.py tests/test_integration.py`
to enumerate every site, then edit each to the two-arg form. Body unchanged.

If `grep` reveals additional definitions that aren't called as `get_task_definitions` (e.g. internal helpers with the same shape), inspect carefully — only the callbacks passed to `Optio.init(..., get_task_definitions=...)` need updating.

- [ ] **Step 6: Run the full optio-core suite to surface any drift**

Run: `cd packages/optio-core && pytest tests -x -v`
Expected: pre-existing tests pass; `tests/test_resync.py` may still fail until Task 6 (the public `Optio.resync` accepts `metadata_filter`).

If `test_resync.py` tests pass at this point because `Optio.resync()` already takes `**kwargs` or passes through, that's a bonus — proceed. If they fail with `TypeError: resync() got an unexpected keyword argument 'metadata_filter'`, that is expected and addressed in Task 6.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/src/optio_core/models.py packages/optio-core/tests
git commit -m "feat(optio-core): _sync_definitions accepts metadata_filter; callback signature now (services, filter)"
```

---

## Task 6: `Optio.resync` and `_handle_resync` thread the filter end-to-end

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

- [ ] **Step 1: Verify the test file from Task 5 already covers this**

The four tests in `tests/test_resync.py` exercise `optio.resync(metadata_filter=...)` and `optio.resync(clean=True, metadata_filter=...)`. They are the failing tests for this task.

Run: `cd packages/optio-core && pytest tests/test_resync.py -v`
Expected: tests still fail with `TypeError: resync() got an unexpected keyword argument 'metadata_filter'`.

- [ ] **Step 2: Update `Optio.resync`**

Replace the existing `resync` method in `lifecycle.py`:

```python
async def resync(
    self,
    clean: bool = False,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> None:
    """Re-sync task definitions from the generator.

    With no `metadata_filter`, the full task set is regenerated and stale
    records / schedules / registry entries are pruned. With a filter,
    regeneration is scoped to tasks whose `metadata` matches; out-of-scope
    state is preserved.

    `clean=True` deletes process records before re-importing. When combined
    with a filter, only in-scope records are deleted.
    """
    await self._handle_resync({"clean": clean, "metadataFilter": metadata_filter})
```

- [ ] **Step 3: Update `_handle_resync`**

Replace the existing `_handle_resync`:

```python
async def _handle_resync(self, payload: dict) -> None:
    clean = payload.get("clean", False)
    metadata_filter = payload.get("metadataFilter") or None  # treat {} as None

    if clean:
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        if metadata_filter:
            mongo_query: dict[str, Any] = {"parentId": None}
            for k, v in metadata_filter.items():
                mongo_query[f"metadata.{k}"] = v
            deleted = await coll.delete_many(mongo_query)
        else:
            deleted = await coll.delete_many({})
        logger.info(f"Nuked {deleted.deleted_count} process records")

    await self._sync_definitions(metadata_filter)
```

(Add `from typing import Any` to `lifecycle.py` if not already imported.)

The `parentId: None` constraint on the scoped clean mirrors `remove_stale_processes` so we never accidentally delete child rows.

- [ ] **Step 4: Run the resync test suite**

Run: `cd packages/optio-core && pytest tests/test_resync.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full optio-core suite for regressions**

Run: `cd packages/optio-core && pytest tests -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py
git commit -m "feat(optio-core): Optio.resync accepts metadata_filter; clean+filter scoped delete

BREAKING CHANGE: get_task_definitions callback signature is now
(services, metadata_filter) — callbacks must accept a second
parameter, even if they ignore it."
```

---

## Task 6b: Update downstream `get_task_definitions` callbacks

The `get_task_definitions` callback signature now takes `(services, metadata_filter)`. Tasks 5 and 6 updated optio-core itself plus its test fixtures. Two downstream call sites also define single-arg callbacks and must be updated to keep the demo / examples runnable.

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/__init__.py`
- Modify: `examples/test-app/tasks/__init__.py`

These callbacks aggregate task lists and have no per-group logic. The simplest correct change is to accept the second parameter and ignore it — the framework-side input filter in `_sync_definitions` (added in Task 5) drops out-of-scope tasks before any downstream processing, so over-returning is safe.

- [ ] **Step 1: Update optio-demo callback**

In `packages/optio-demo/src/optio_demo/tasks/__init__.py`, replace the existing definition:

```python
"""Task definitions for the optio demo application."""

from optio_core.models import TaskInstance, ProcessMetadataFilter

from optio_demo.tasks.terraforming import get_tasks as terraforming_tasks
from optio_demo.tasks.home import get_tasks as home_tasks
from optio_demo.tasks.heist import get_tasks as heist_tasks
from optio_demo.tasks.festival import get_tasks as festival_tasks
from optio_demo.tasks.wakeup import get_tasks as wakeup_tasks
from optio_demo.tasks.marimo import get_tasks as marimo_tasks
from optio_demo.tasks.opencode import get_tasks as opencode_tasks


async def get_task_definitions(
    services: dict,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> list[TaskInstance]:
    return [
        *terraforming_tasks(),
        *home_tasks(),
        *heist_tasks(),
        *festival_tasks(),
        *wakeup_tasks(),
        *marimo_tasks(),
        *opencode_tasks(),
    ]
```

- [ ] **Step 2: Update examples/test-app callback**

In `examples/test-app/tasks/__init__.py`, locate the existing `get_task_definitions(services: dict)` definition and add the second parameter the same way:

```python
from optio_core.models import TaskInstance, ProcessMetadataFilter


async def get_task_definitions(
    services: dict,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> list[TaskInstance]:
    # Body unchanged — return the same task list as before.
    ...
```

(Read the file first to preserve any imports and the existing return body verbatim; only the signature changes.)

- [ ] **Step 3: Run tests where each callback is exercised, if any**

For optio-demo, the package may have its own test suite. Run: `cd packages/optio-demo && pytest -v 2>/dev/null` if a test directory exists; otherwise verify import succeeds: `cd packages/optio-demo && python -c "from optio_demo.tasks import get_task_definitions; print(get_task_definitions)"`.

For examples/test-app, verify the module still imports: `cd examples/test-app && python -c "from tasks import get_task_definitions; print(get_task_definitions)"`.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-demo/src/optio_demo/tasks/__init__.py examples/test-app/tasks/__init__.py
git commit -m "refactor(optio-demo,examples): accept metadata_filter param in get_task_definitions"
```

---

## Task 7: optio-api `ProcessMetadataFilter` type

**Files:**
- Create or modify: `packages/optio-api/src/types.ts`

- [ ] **Step 1: Locate or create the types module**

Run: `cd packages/optio-api && ls src/types.ts 2>/dev/null && echo EXISTS || echo MISSING`

If `types.ts` exists, append the new alias. If not, create it.

- [ ] **Step 2: Add the type alias**

Either append to existing `packages/optio-api/src/types.ts`:

```typescript
export type ProcessMetadataFilter = Record<string, unknown>;
```

Or create the file with that one export.

- [ ] **Step 3: Type-check**

Run: `cd packages/optio-api && node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-api/src/types.ts
git commit -m "feat(optio-api): ProcessMetadataFilter type alias"
```

---

## Task 8: `publishResync` forwards the metadata filter

**Files:**
- Modify: `packages/optio-api/src/publisher.ts`
- Modify: `packages/optio-api/src/__tests__/publisher.test.ts`

- [ ] **Step 1: Write failing tests**

Append to `packages/optio-api/src/__tests__/publisher.test.ts`:

```typescript
describe('publishResync — metadataFilter', () => {
  it('omits metadataFilter from the payload when not provided', async () => {
    await publishResync(redis, 'mydb', 'optio');
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.metadataFilter).toBeUndefined();
  });

  it('omits metadataFilter when filter is an empty object', async () => {
    await publishResync(redis, 'mydb', 'optio', false, {});
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.metadataFilter).toBeUndefined();
  });

  it('includes metadataFilter when provided and non-empty', async () => {
    await publishResync(redis, 'mydb', 'optio', false, { group: 'ingest' });
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.metadataFilter).toEqual({ group: 'ingest' });
    expect(payload.clean).toBe(false);
  });

  it('combines metadataFilter with clean=true', async () => {
    await publishResync(redis, 'mydb', 'optio', true, { group: 'ingest' });
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.metadataFilter).toEqual({ group: 'ingest' });
    expect(payload.clean).toBe(true);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-api && pnpm test -- publisher.test.ts`
Expected: the four new tests fail (`publishResync` does not accept the fifth argument or does not forward it).

- [ ] **Step 3: Update `publishResync`**

Replace the existing `publishResync` in `packages/optio-api/src/publisher.ts`:

```typescript
import type { ProcessMetadataFilter } from './types.js';
```

```typescript
export async function publishResync(
  redis: Redis,
  database: string,
  prefix: string,
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<void> {
  const payload: { clean: boolean; metadataFilter?: ProcessMetadataFilter } = { clean };
  if (metadataFilter && Object.keys(metadataFilter).length > 0) {
    payload.metadataFilter = metadataFilter;
  }
  await redis.xadd(
    getStreamName(database, prefix),
    '*',
    'type', 'resync',
    'payload', JSON.stringify(payload),
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-api && pnpm test -- publisher.test.ts`
Expected: all `publishResync` tests pass (including the existing `clean=true/false` tests).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-api/src/publisher.ts packages/optio-api/src/__tests__/publisher.test.ts
git commit -m "feat(optio-api): publishResync forwards metadataFilter"
```

---

## Task 9: `resyncProcesses` handler forwards the filter

**Files:**
- Modify: `packages/optio-api/src/handlers.ts`

- [ ] **Step 1: Update the handler signature**

Replace the existing `resyncProcesses` in `packages/optio-api/src/handlers.ts`:

```typescript
import type { ProcessMetadataFilter } from './types.js';
```

```typescript
export async function resyncProcesses(
  redis: Redis,
  database: string,
  prefix: string,
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<{ message: string }> {
  await publishResync(redis, database, prefix, clean, metadataFilter);
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
```

(There is currently no dedicated test for `resyncProcesses` in `handlers.test.ts`. Task 10 covers behaviour through the adapter tests, which already exercise this code path.)

- [ ] **Step 2: Type-check**

Run: `cd packages/optio-api && node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-api/src/handlers.ts
git commit -m "feat(optio-api): resyncProcesses forwards metadataFilter"
```

---

## Task 10: Adapters parse and forward `body.metadataFilter`

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/express.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`

- [ ] **Step 1: Write failing test in `fastify.test.ts`**

Append to the resync section of `packages/optio-api/src/adapters/__tests__/fastify.test.ts`:

```typescript
it('POST /api/processes/resync — forwards metadataFilter to Redis', async () => {
  const app = createApp();

  const res = await app.inject({
    method: 'POST',
    url: '/api/processes/resync',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ metadataFilter: { group: 'ingest' } }),
  });

  expect(res.statusCode).toBe(200);

  // Inspect the redis mock for the published payload.
  const entries = await (redis as any).xrange(
    `${DB_NAME}/${PREFIX}:commands`, '-', '+',
  );
  const [, fields] = entries[entries.length - 1];
  const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
  expect(payload.metadataFilter).toEqual({ group: 'ingest' });
});
```

(Confirm `DB_NAME` and `PREFIX` constants are in scope at the top of the test file; if they are different identifiers, substitute as appropriate.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-api && pnpm test -- fastify.test.ts -t metadataFilter`
Expected: FAIL — `payload.metadataFilter` is `undefined` because the adapter doesn't read `body.metadataFilter`.

- [ ] **Step 3: Update fastify adapter**

In `packages/optio-api/src/adapters/fastify.ts`, find the `resync` handler (currently around line 416) and replace it:

```typescript
import type { ProcessMetadataFilter } from '../types.js';
```

```typescript
resync: async ({
  query,
  body,
}: {
  query: { database?: string; prefix?: string };
  body: { clean?: boolean; metadataFilter?: ProcessMetadataFilter };
}) => {
  const database = query.database ?? defaultDatabase;
  const prefix = query.prefix ?? defaultPrefix;
  const result = await handlers.resyncProcesses(
    redis, database, prefix,
    body.clean ?? false,
    body.metadataFilter,
  );
  return result;
},
```

(Preserve any helper-binding shape of the existing function — the inner body should produce the same `result` and return path. Mirror what the existing handler does for query/prefix resolution.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-api && pnpm test -- fastify.test.ts`
Expected: all pass.

- [ ] **Step 5: Repeat Steps 1–4 for express, nextjs-app, nextjs-pages**

For each adapter test file (`express.test.ts`, `nextjs-app.test.ts`, `nextjs-pages.test.ts`), append an analogous test that POSTs `{ metadataFilter: { group: 'ingest' } }` to `/api/processes/resync` and asserts that the Redis-mock stream contains a payload with `metadataFilter` equal to that object. Adapt the request shape to each runtime (supertest for express, the makeNextRequest helper for nextjs).

For each adapter source file (`express.ts`, `nextjs-app.ts`, `nextjs-pages.ts`), find the existing `resync:` handler (already located at lines 92, 91, and 78 respectively in the spec snapshot) and apply the same edit: extend the body type to include `metadataFilter?: ProcessMetadataFilter` and pass `body.metadataFilter` as the fifth argument to `handlers.resyncProcesses`.

For each adapter, run the failing test, apply the fix, run again, confirm pass.

- [ ] **Step 6: Run all adapter tests + type-check**

Run: `cd packages/optio-api && pnpm test -- adapters && node_modules/.bin/tsc --noEmit`
Expected: all pass; 0 type errors.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-api/src/adapters
git commit -m "feat(optio-api): adapters parse and forward body.metadataFilter on resync"
```

---

## Task 11: Documentation pass

**Files:**
- Modify: `packages/optio-core/AGENTS.md` (and/or `README.md` if it documents the `get_task_definitions` callback)
- Modify: `packages/optio-api/AGENTS.md` (if it documents the resync endpoint body)

- [ ] **Step 1: Locate documentation that mentions `get_task_definitions` or the resync body**

Run: `grep -rn "get_task_definitions\|/processes/resync" packages/optio-core packages/optio-api --include="*.md"`

- [ ] **Step 2: Update each match**

For Python (optio-core): note the new two-arg signature `(services, metadata_filter)` and that `metadata_filter=None` reproduces full-sync behaviour. Reference the spec at `docs/2026-04-27-partial-task-regen-design.md`.

For TypeScript (optio-api): update the documented body of `POST /api/processes/resync` to include `metadataFilter?: ProcessMetadataFilter` alongside `clean?`.

If neither AGENTS.md mentions these, skip — no change required.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core packages/optio-api
git commit -m "docs: document partial task regen API surface"
```

(Skip if Step 2 produced no edits.)

---

## Verification — final pass

- [ ] **Step 1: Full optio-core test run**

Run: `cd packages/optio-core && pytest tests -v`
Expected: all pass.

- [ ] **Step 2: Full optio-api test run + type-check**

Run: `cd packages/optio-api && pnpm test && node_modules/.bin/tsc --noEmit`
Expected: all pass; 0 type errors.

- [ ] **Step 3: Branch state**

Run: `git log --oneline main..HEAD`
Expected: roughly 8–11 commits, one per task above.

- [ ] **Step 4: Hand off**

Per the project's finishing-a-development-branch flow, present integration options to the user (merge to main, open PR, etc.). Do not push or merge without explicit instruction.

---

## Migration note for callers (delivered in Task 6 commit body)

Existing application code that supplies `get_task_definitions` to `Optio.init` must update its callback signature:

```python
# Before
async def my_get_tasks(services):
    return [...]

# After
async def my_get_tasks(services, metadata_filter=None):
    if metadata_filter:
        # optionally filter — or ignore and return the full list
        return [t for t in all_tasks if matches(t, metadata_filter)]
    return all_tasks
```

A callback that ignores `metadata_filter` and returns its full list still works correctly: the per-layer scoping in store/executor/scheduler narrows the cleanup to the in-scope subset, so out-of-scope state is preserved even when the callback over-returns.
