# Auto-Resume Tasks on Engine Restart — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After an engine restart, automatically resume top-level tasks that opted into `auto_resume` and gracefully saved their state, deferred by a configurable delay so dev-mode restart bursts don't thrash.

**Architecture:** A new `auto_resume` flag on `TaskInstance`. At shutdown, a top-level process of an `auto_resume` task is stamped `autoResumeScheduled=true` on its process document. On the next engine start, a one-shot timer (default 300s) re-launches every stamped process that is `cancelled` with `hasSavedState`, then clears the stamp. Any transition to `failed` clears the stamp (force-killed processes are never resumed). A stopwatch indicator surfaces the pending state in the UI via the shared `ProcessStatusBadge`.

**Tech Stack:** Python (optio-core, motor/MongoDB, pytest), TypeScript (optio-contracts zod, optio-ui React + Ant Design + vitest), excavator engine (Python) + frontend (React i18n).

**Spec:** `docs/superpowers/specs/2026-06-06-auto-resume-on-restart-design.md`

**Repositories & worktrees:**
- optio: this worktree — `/home/csillag/deai/optio/.worktrees/autoresume` (branch `csillag/autoresume`).
- excavator: `/home/csillag/deai/excavator` (Tasks 12–13). Coordinate with optio: excavator depends on `optio_core` carrying the new `auto_resume` field, and on `optio-contracts`/`optio-ui` carrying `autoResumeScheduled`. Build/link optio packages before running excavator tests.

**Prerequisites for running tests:**
- A MongoDB reachable at `MONGO_URL` (default `mongodb://localhost:27017`) for all optio-core / engine pytest.
- Python venv per the optio Makefile (`make` targets use `$(VENV)/bin/pytest`). Commands below use `python -m pytest` — substitute the venv interpreter if not activated.

**Commit convention:** This is an optio / excavator repo — **omit any `Co-Authored-By` trailer** (per AGENTS.md). Conventional Commits style.

---

## File Structure

**optio-core (Python)** — `packages/optio-core/src/optio_core/`
- `models.py` — `TaskInstance.auto_resume` field; `OptioConfig.auto_resume_delay_seconds` field.
- `store.py` — `autoResumeScheduled` default on insert; new `set_auto_resume_scheduled()` helper.
- `lifecycle.py` — `init()` config param; sync validation; shutdown stamping; reconcile stamp-clear; launch stamp-clear; startup timer + sweep + shutdown teardown.
- `_force_cancel.py` — clear stamp on the force-cancelled→failed write.
- `tests/test_auto_resume.py` — new test module covering all of the above.

**optio-contracts (TS)** — `packages/optio-contracts/src/schemas/process.ts`
- `ProcessSchema.autoResumeScheduled`.

**optio-ui (TS)** — `packages/optio-ui/src/`
- `process-state.ts` — `ProcessStateLike.autoResumeScheduled`.
- `components/ProcessStatusBadge.tsx` — new optional prop + stopwatch indicator.
- `components/ProcessItem.tsx`, `components/ProcessTreeView.tsx` — pass the field into the badge.
- `__tests__/ProcessStatusBadge.test.tsx` — new test.

**excavator engine (Python)** — `packages/engine/src/engine/free_style/task.py`
- `_finalize_ti()` — set `ti.auto_resume = True`.
- `packages/engine/tests/test_free_style_task.py` — assert the flag.

**excavator frontend (TS)** — `packages/frontend/src/`
- i18n `en.json`, `hu.json`, `de.json` — `status.autoResumeScheduled` label.
- `features/processes/ProcessDetailPage.tsx` — pass `autoResumeScheduled` to the header badge.

---

## Task 1: `auto_resume` flag + delay config on models

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py:63-74` (TaskInstance), `:144-152` (OptioConfig)
- Test: `packages/optio-core/tests/test_auto_resume.py` (create)

- [ ] **Step 1: Write the failing test**

Create `packages/optio-core/tests/test_auto_resume.py`:

```python
"""Tests for auto-resume-on-restart.

Spec: docs/superpowers/specs/2026-06-06-auto-resume-on-restart-design.md
"""
import asyncio
from datetime import datetime, timezone

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, OptioConfig
from optio_core.store import get_process_by_process_id, set_auto_resume_scheduled


async def _noop(ctx):  # noqa: ARG001
    pass


def test_task_instance_auto_resume_defaults_false():
    ti = TaskInstance(execute=_noop, process_id="t", name="T")
    assert ti.auto_resume is False
    ti2 = TaskInstance(
        execute=_noop, process_id="t2", name="T2",
        supports_resume=True, auto_resume=True,
    )
    assert ti2.auto_resume is True


def test_optio_config_auto_resume_delay_default():
    cfg = OptioConfig(mongo_db=None)
    assert cfg.auto_resume_delay_seconds == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_task_instance_auto_resume_defaults_false tests/test_auto_resume.py::test_optio_config_auto_resume_delay_default -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'auto_resume'` and `AttributeError`/`assert` on `auto_resume_delay_seconds` (also an ImportError on `set_auto_resume_scheduled`, which Task 3 adds — temporarily remove that import to see Step-1 failures, or accept the collection error; the import lands green in Task 3).

NOTE: To keep this task self-contained, you may stub the import by removing `set_auto_resume_scheduled` from the import line until Task 3. Re-add it in Task 3.

- [ ] **Step 3: Add the fields**

In `models.py`, `TaskInstance` (after `auto_cancel_children`):

```python
@dataclass
class TaskInstance(TaskInstanceCore):
    """A unit of work provided by the application's task generator."""
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None
    special: bool = False
    warning: str | None = None
    cancellable: bool = True
    ui_widget: str | None = None
    supports_resume: bool = False
    ttl_seconds: int | None = None
    auto_cancel_children: bool = True
    # When True, a *top-level* process of this task that is interrupted by an
    # engine shutdown and that gracefully saved its state is re-launched
    # (resume=True) automatically after the next engine start, post-delay.
    # Requires supports_resume=True (validated at task-sync time).
    auto_resume: bool = False
```

In `models.py`, `OptioConfig` (after `cancel_grace_seconds`):

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
    # One-shot post-boot delay before auto-resume re-launches stamped
    # processes. The wait lets the environment settle (dev-mode code edits
    # cause rapid restart bursts) so we don't thrash re-launches.
    auto_resume_delay_seconds: float = 300.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_task_instance_auto_resume_defaults_false tests/test_auto_resume.py::test_optio_config_auto_resume_delay_default -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): auto_resume flag on TaskInstance + auto_resume_delay_seconds config"
```

---

## Task 2: Thread `auto_resume_delay_seconds` through `init()`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:105-153` (`Optio.init`)
- Test: `packages/optio-core/tests/test_auto_resume.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_resume.py`:

```python
async def test_init_threads_auto_resume_delay(mongo_db):
    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(execute=_noop, process_id="p", name="P")]

    fw = Optio()
    await fw.init(
        mongo_db=mongo_db, prefix="ardelay",
        get_task_definitions=get_tasks, auto_resume_delay_seconds=0.05,
    )
    try:
        assert fw._config.auto_resume_delay_seconds == 0.05
    finally:
        await fw.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_init_threads_auto_resume_delay -v`
Expected: FAIL — `TypeError: init() got an unexpected keyword argument 'auto_resume_delay_seconds'`

- [ ] **Step 3: Add the parameter**

In `lifecycle.py`, `Optio.init` signature — add the parameter after `cancel_grace_seconds`:

```python
    async def init(
        self,
        mongo_db: AsyncIOMotorDatabase,
        prefix: str = "optio",
        redis_url: str | None = None,
        rpc_server: "RpcServerCore | None" = None,
        services: dict[str, Any] | None = None,
        get_task_definitions: Callable[
            [dict[str, Any], ProcessMetadataFilter | None],
            Awaitable[list[TaskInstance]],
        ] | None = None,
        cancel_grace_seconds: float = 5.0,
        auto_resume_delay_seconds: float = 300.0,
    ) -> None:
```

And pass it into `OptioConfig(...)` (the `self._config = OptioConfig(...)` block):

```python
        self._config = OptioConfig(
            mongo_db=mongo_db,
            prefix=prefix,
            redis_url=redis_url,
            services=services,
            get_task_definitions=get_task_definitions,
            cancel_grace_seconds=cancel_grace_seconds,
            auto_resume_delay_seconds=auto_resume_delay_seconds,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_init_threads_auto_resume_delay -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): thread auto_resume_delay_seconds through init()"
```

---

## Task 3: Process-doc `autoResumeScheduled` default + store helper

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py:66-78` (`upsert_process` `$setOnInsert`); add helper near `update_status`
- Test: `packages/optio-core/tests/test_auto_resume.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_resume.py` (and ensure the top-of-file import line reads `from optio_core.store import get_process_by_process_id, set_auto_resume_scheduled`):

```python
async def test_upsert_sets_auto_resume_scheduled_false(mongo_db):
    from optio_core.store import upsert_process
    prefix = "arstore"
    ti = TaskInstance(execute=_noop, process_id="p", name="P")
    proc = await upsert_process(mongo_db, prefix, ti)
    assert proc["autoResumeScheduled"] is False


async def test_set_auto_resume_scheduled_flips_flag(mongo_db):
    from optio_core.store import upsert_process
    prefix = "arstore2"
    ti = TaskInstance(execute=_noop, process_id="p", name="P")
    proc = await upsert_process(mongo_db, prefix, ti)

    await set_auto_resume_scheduled(mongo_db, prefix, proc["_id"], True)
    again = await get_process_by_process_id(mongo_db, prefix, "p")
    assert again["autoResumeScheduled"] is True

    await set_auto_resume_scheduled(mongo_db, prefix, proc["_id"], False)
    again2 = await get_process_by_process_id(mongo_db, prefix, "p")
    assert again2["autoResumeScheduled"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_upsert_sets_auto_resume_scheduled_false tests/test_auto_resume.py::test_set_auto_resume_scheduled_flips_flag -v`
Expected: FAIL — `KeyError: 'autoResumeScheduled'` and `ImportError: cannot import name 'set_auto_resume_scheduled'`

- [ ] **Step 3: Add the default + helper**

In `store.py`, `upsert_process`, add to the `$setOnInsert` dict (after `"hasSavedState": False,`):

```python
            "$setOnInsert": {
                "parentId": None,
                "rootId": None,
                "depth": 0,
                "order": 0,
                "adhoc": False,
                "ephemeral": False,
                "status": ProcessStatus().to_dict(),
                "progress": Progress().to_dict(),
                "log": [],
                "createdAt": now,
                "hasSavedState": False,
                "autoResumeScheduled": False,
            },
```

Add this helper right after `update_status` (around `store.py:191`):

```python
async def set_auto_resume_scheduled(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, value: bool,
) -> None:
    """Set the `autoResumeScheduled` stamp on a process.

    The stamp marks a cancelled, state-saved top-level process for automatic
    resume after the next engine start. Set True at shutdown (for eligible
    processes), cleared on resume / manual launch / any transition to failed.
    """
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"autoResumeScheduled": value}},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_upsert_sets_auto_resume_scheduled_false tests/test_auto_resume.py::test_set_auto_resume_scheduled_flips_flag -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/store.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): autoResumeScheduled process-doc field + set helper"
```

---

## Task 4: Sync-time validation — `auto_resume` requires `supports_resume`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:1134-1161` (`_sync_definitions`)
- Test: `packages/optio-core/tests/test_auto_resume.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_resume.py`:

```python
async def test_auto_resume_without_supports_resume_hard_fails(mongo_db):
    async def get_tasks(_services, metadata_filter=None):
        return [
            TaskInstance(
                execute=_noop, process_id="bad", name="Bad",
                auto_resume=True, supports_resume=False,
            )
        ]

    fw = Optio()
    with pytest.raises(ValueError, match="auto_resume"):
        await fw.init(mongo_db=mongo_db, prefix="arvalid", get_task_definitions=get_tasks)


async def test_auto_resume_with_supports_resume_is_accepted(mongo_db):
    async def get_tasks(_services, metadata_filter=None):
        return [
            TaskInstance(
                execute=_noop, process_id="good", name="Good",
                auto_resume=True, supports_resume=True,
            )
        ]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="arvalid_ok", get_task_definitions=get_tasks)
    try:
        proc = await get_process_by_process_id(mongo_db, "arvalid_ok", "good")
        assert proc is not None
    finally:
        await fw.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_auto_resume_without_supports_resume_hard_fails -v`
Expected: FAIL — no `ValueError` raised (`DID NOT RAISE`).

- [ ] **Step 3: Add the validation**

In `lifecycle.py`, `_sync_definitions`, insert the validation loop immediately after the `metadata_filter` filtering block and before the `pid_to_oid` loop (around `:1156`):

```python
        # Framework guarantees only in-scope tasks reach downstream layers,
        # so callback authors may ignore `metadata_filter` if they prefer.
        if metadata_filter:
            tasks = [t for t in tasks if matches_filter(t.metadata, metadata_filter)]

        # Validate auto_resume coherence: you cannot resume a task that does
        # not support resume. Catch the misconfiguration loudly at sync time
        # (engine startup) rather than silently no-op'ing at restart.
        for task in tasks:
            if task.auto_resume and not task.supports_resume:
                raise ValueError(
                    f"Task '{task.process_id}': auto_resume=True requires "
                    f"supports_resume=True"
                )

        pid_to_oid: dict[str, str] = {}
        for task in tasks:
            proc = await upsert_process(self._config.mongo_db, self._config.prefix, task)
            pid_to_oid[task.process_id] = str(proc["_id"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_auto_resume_without_supports_resume_hard_fails tests/test_auto_resume.py::test_auto_resume_with_supports_resume_is_accepted -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): hard-fail engine on auto_resume without supports_resume"
```

---

## Task 5: Shutdown stamping (top-level + auto_resume only)

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` — import `set_auto_resume_scheduled`; new `_stamp_auto_resume_if_eligible`; wire into `shutdown` step-3 loop (`:976`)
- Test: `packages/optio-core/tests/test_auto_resume.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_resume.py`:

```python
async def test_shutdown_stamps_eligible_top_level_process(mongo_db):
    """A root process of an auto_resume task that saves state and cancels
    gracefully ends 'cancelled' + hasSavedState + autoResumeScheduled."""
    prefix = "arstamp"
    started = asyncio.Event()

    async def cooperative(ctx):
        await ctx.mark_has_saved_state()
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.05)

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=cooperative, process_id="ana", name="Ana",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("ana", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=2.0)

    await fw.shutdown(grace_seconds=1.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "ana")
    assert proc["status"]["state"] == "cancelled", proc["status"]
    assert proc["hasSavedState"] is True
    assert proc["autoResumeScheduled"] is True


async def test_shutdown_does_not_stamp_non_auto_resume(mongo_db):
    prefix = "arstamp_neg"
    started = asyncio.Event()

    async def cooperative(ctx):
        await ctx.mark_has_saved_state()
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.05)

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=cooperative, process_id="plain", name="Plain",
            supports_resume=True, auto_resume=False,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("plain", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=2.0)

    await fw.shutdown(grace_seconds=1.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "plain")
    assert proc["status"]["state"] == "cancelled"
    assert proc.get("autoResumeScheduled") is False


async def test_stamp_eligibility_is_top_level_only(mongo_db):
    """_stamp_auto_resume_if_eligible stamps depth-0 but not depth-1 docs."""
    prefix = "arstamp_depth"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return []

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        task = TaskInstance(
            execute=_noop, process_id="dep", name="Dep",
            supports_resume=True, auto_resume=True,
        )
        fw._executor._task_registry["dep"] = task

        root = await coll.insert_one({
            "processId": "dep", "depth": 0, "status": {"state": "cancelled"},
            "autoResumeScheduled": False, "log": [],
        })
        child = await coll.insert_one({
            "processId": "dep", "depth": 1, "status": {"state": "cancelled"},
            "autoResumeScheduled": False, "log": [],
        })

        await fw._stamp_auto_resume_if_eligible(root.inserted_id)
        await fw._stamp_auto_resume_if_eligible(child.inserted_id)

        root_doc = await coll.find_one({"_id": root.inserted_id})
        child_doc = await coll.find_one({"_id": child.inserted_id})
        assert root_doc["autoResumeScheduled"] is True
        assert child_doc["autoResumeScheduled"] is False
    finally:
        await fw.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py -k "stamp" -v`
Expected: FAIL — `AttributeError: 'Optio' object has no attribute '_stamp_auto_resume_if_eligible'`, and the cooperative tests assert `autoResumeScheduled is True` but find it `False`.

- [ ] **Step 3: Add the import, the helper, and the wiring**

In `lifecycle.py`, extend the `from optio_core.store import (...)` block (around `:33-37`) to include `set_auto_resume_scheduled`:

```python
from optio_core.store import (
    upsert_process, remove_stale_processes, find_stale_process_ids,
    get_process_by_process_id, update_status, clear_result_fields,
    append_log, compute_expire_at, purge_processes, set_auto_resume_scheduled,
)
```

Add the helper method to the `Optio` class (place it just above `shutdown`, around `:935`):

```python
    async def _stamp_auto_resume_if_eligible(self, oid: ObjectId) -> None:
        """Stamp `autoResumeScheduled=True` on a process iff it is a top-level
        (depth 0) process whose task opted into auto_resume.

        Called during shutdown for every process being cancelled. The depth
        check enforces the top-level-only restriction at the point where the
        concrete process depth is known (a task definition does not know the
        depth of its future instances).
        """
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        doc = await coll.find_one({"_id": oid}, {"processId": 1, "depth": 1})
        if doc is None or doc.get("depth", 0) != 0:
            return
        task = self._executor._task_registry.get(doc["processId"])
        if task is None or not getattr(task, "auto_resume", False):
            return
        await set_auto_resume_scheduled(
            self._config.mongo_db, self._config.prefix, oid, True,
        )
        await append_log(
            self._config.mongo_db, self._config.prefix, oid,
            "event", "Scheduled for auto-resume after restart",
        )
```

In `shutdown`, step 3 loop (`:976-982`), stamp before setting each cancel flag:

```python
            if self._executor:
                now_mono = time.monotonic()
                for oid in list(self._executor._cancellation_flags.keys()):
                    entry = self._executor._cancellation_flags.get(oid)
                    if entry is None:
                        continue
                    await self._stamp_auto_resume_if_eligible(oid)
                    entry.flag.set()
                    if entry.deadline is None:
                        entry.deadline = now_mono + grace
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py -k "stamp" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): stamp top-level auto_resume processes at shutdown"
```

---

## Task 6: Any transition to `failed` clears the stamp

**Files:**
- Modify: `packages/optio-core/src/optio_core/_force_cancel.py:56` (`set_doc`); `lifecycle.py:1050` (`_reconcile_interrupted_processes` `$set`)
- Test: `packages/optio-core/tests/test_auto_resume.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_resume.py`:

```python
async def test_force_cancel_clears_stamp(mongo_db):
    """An uncooperative auto_resume root is stamped at shutdown, then
    force-cancelled to failed — the stamp must be cleared."""
    prefix = "arclear_force"
    started = asyncio.Event()

    async def uncooperative(ctx):
        started.set()
        await asyncio.sleep(30)  # ignore cancellation

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=uncooperative, process_id="stuck", name="Stuck",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    await fw.launch("stuck", session_id=None)
    await asyncio.wait_for(started.wait(), timeout=2.0)

    await fw.shutdown(grace_seconds=0.2)

    proc = await get_process_by_process_id(mongo_db, prefix, "stuck")
    assert proc["status"]["state"] == "failed"
    assert proc.get("autoResumeScheduled") is False


async def test_reconcile_clears_stamp(mongo_db):
    """A stamped, still-running process from a previous session is reconciled
    to failed on init — the stamp must be cleared."""
    prefix = "arclear_recon"
    coll = mongo_db[f"{prefix}_processes"]
    await coll.insert_one({
        "processId": "ghost", "name": "Ghost", "params": {}, "metadata": {},
        "parentId": None, "rootId": None, "depth": 0, "order": 0,
        "adhoc": False, "ephemeral": False,
        "status": {"state": "running", "runningSince": datetime.now(timezone.utc)},
        "progress": {"percent": None, "message": None}, "log": [],
        "createdAt": datetime.now(timezone.utc),
        "autoResumeScheduled": True,
    })

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="ghost", name="Ghost",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        proc = await get_process_by_process_id(mongo_db, prefix, "ghost")
        assert proc["status"]["state"] == "failed"
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py -k "clears_stamp" -v`
Expected: FAIL — both assert `autoResumeScheduled is False` but find it `True`.

- [ ] **Step 3: Clear the stamp on both failed-write paths**

In `_force_cancel.py`, `_write_force_cancelled_state`, add `autoResumeScheduled` to `set_doc` (`:56`):

```python
    set_doc: dict = {
        "status": status_doc,
        "widgetUpstream": None,
        "autoResumeScheduled": False,
    }
```

In `lifecycle.py`, `_reconcile_interrupted_processes`, extend the per-row `$set` (`:1050`):

```python
            await coll.update_one(
                {"_id": oid},
                {"$set": {"widgetUpstream": None, "autoResumeScheduled": False}},
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py -k "clears_stamp" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/_force_cancel.py packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): clear autoResumeScheduled on any transition to failed"
```

---

## Task 7: Manual launch clears the stamp

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:413-422` (`launch`)
- Test: `packages/optio-core/tests/test_auto_resume.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_resume.py`:

```python
async def test_launch_clears_stamp(mongo_db):
    """Launching a stamped process clears the stamp (human beat the timer)."""
    prefix = "arlaunch_clear"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        # Put the synced doc into a stamped, resumable, cancelled state.
        await coll.update_one(
            {"processId": "r"},
            {"$set": {
                "status": {"state": "cancelled"},
                "hasSavedState": True,
                "autoResumeScheduled": True,
            }},
        )
        outcome = await fw.launch("r", resume=True, session_id=None)
        assert outcome.ok, outcome.reason

        proc = await get_process_by_process_id(mongo_db, prefix, "r")
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_launch_clears_stamp -v`
Expected: FAIL — asserts `autoResumeScheduled is False` but finds it `True`.

- [ ] **Step 3: Clear the stamp in `launch`**

In `lifecycle.py`, `launch`, after scheduling the executor task and before the re-read (`:414-421`):

```python
        oid_str = str(proc["_id"])
        asyncio.create_task(
            self._executor.launch_process(oid_str, resume=resume, session_id=session_id),
        )
        # A launch (manual or timer-driven) supersedes any pending auto-resume
        # stamp on this process — clear it so the post-boot sweep won't
        # double-launch it.
        if proc.get("autoResumeScheduled"):
            await set_auto_resume_scheduled(
                self._config.mongo_db, self._config.prefix, proc["_id"], False,
            )
        # Yield once so the executor's first state-write (idle→scheduled)
        # lands before we re-read; then return the post-launch snapshot
        # via OID (unambiguous).
        await asyncio.sleep(0)
        post = await self._resolve(oid_str)
        return LaunchOutcome(ok=True, proc=post)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py::test_launch_clears_stamp -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): clear autoResumeScheduled on launch"
```

---

## Task 8: Startup timer + auto-resume sweep + shutdown teardown

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:76-89` (`__init__`), `:918` (`run` arming), `:951-959` (`shutdown` teardown); add `_auto_resume_timer` + `_auto_resume_scheduled_processes`
- Test: `packages/optio-core/tests/test_auto_resume.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auto_resume.py`:

```python
async def test_sweep_resumes_eligible_and_clears_stamp(mongo_db):
    """_auto_resume_scheduled_processes launches cancelled+saved+stamped roots."""
    prefix = "arsweep"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        await coll.update_one(
            {"processId": "r"},
            {"$set": {
                "status": {"state": "cancelled"},
                "hasSavedState": True,
                "autoResumeScheduled": True,
            }},
        )
        await fw._auto_resume_scheduled_processes()
        await asyncio.sleep(0.1)  # let the fire-and-forget executor advance

        proc = await get_process_by_process_id(mongo_db, prefix, "r")
        assert proc["status"]["state"] != "cancelled"  # got (re)launched
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()


async def test_sweep_ignores_failed_and_unsaved(mongo_db):
    prefix = "arsweep_neg"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [
            TaskInstance(execute=_noop, process_id="f", name="F",
                         supports_resume=True, auto_resume=True),
            TaskInstance(execute=_noop, process_id="u", name="U",
                         supports_resume=True, auto_resume=True),
        ]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        # 'f' is stamped but failed (force-killed) — must not resume.
        await coll.update_one({"processId": "f"}, {"$set": {
            "status": {"state": "failed"}, "hasSavedState": False,
            "autoResumeScheduled": True}})
        # 'u' is stamped + cancelled but has no saved state — must not resume.
        await coll.update_one({"processId": "u"}, {"$set": {
            "status": {"state": "cancelled"}, "hasSavedState": False,
            "autoResumeScheduled": True}})

        await fw._auto_resume_scheduled_processes()
        await asyncio.sleep(0.1)

        f = await get_process_by_process_id(mongo_db, prefix, "f")
        u = await get_process_by_process_id(mongo_db, prefix, "u")
        assert f["status"]["state"] == "failed"
        assert u["status"]["state"] == "cancelled"
    finally:
        await fw.shutdown()


async def test_sweep_skips_blocked_and_clears_stamp(mongo_db):
    """A blocked launch is logged, skipped, and un-stamped (no retry)."""
    prefix = "arsweep_block"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="b", name="B",
            metadata={"banned": "yes"},
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks)
    try:
        await coll.update_one({"processId": "b"}, {"$set": {
            "status": {"state": "cancelled"}, "hasSavedState": True,
            "autoResumeScheduled": True}})
        # Register a persistent-style in-memory block matching the task metadata.
        async with fw.block_launches({"banned": "yes"}):
            await fw._auto_resume_scheduled_processes()

        proc = await get_process_by_process_id(mongo_db, prefix, "b")
        assert proc["status"]["state"] == "cancelled"  # not launched
        assert proc.get("autoResumeScheduled") is False  # un-stamped
    finally:
        await fw.shutdown()


async def test_timer_fires_after_delay_via_run(mongo_db):
    """End-to-end: run() arms the one-shot timer; after the (tiny) delay the
    eligible process is resumed."""
    prefix = "artimer"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(
        mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks,
        auto_resume_delay_seconds=0.2,
    )
    # Seed eligible state AFTER init (init's reconcile leaves 'cancelled' alone).
    await coll.update_one({"processId": "r"}, {"$set": {
        "status": {"state": "cancelled"}, "hasSavedState": True,
        "autoResumeScheduled": True}})

    run_task = asyncio.create_task(fw.run())
    try:
        await asyncio.sleep(0.6)  # delay (0.2) + executor advance margin
        proc = await get_process_by_process_id(mongo_db, prefix, "r")
        assert proc["status"]["state"] != "cancelled"
        assert proc.get("autoResumeScheduled") is False
    finally:
        await fw.shutdown()
        await asyncio.wait_for(run_task, timeout=5.0)


async def test_timer_does_not_fire_if_shutdown_first(mongo_db):
    """Shutdown before the delay elapses cancels the one-shot timer; the
    stamped process is NOT resumed and the stamp persists for next boot."""
    prefix = "artimer_cancel"
    coll = mongo_db[f"{prefix}_processes"]

    async def get_tasks(_services, metadata_filter=None):
        return [TaskInstance(
            execute=_noop, process_id="r", name="R",
            supports_resume=True, auto_resume=True,
        )]

    fw = Optio()
    await fw.init(
        mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_tasks,
        auto_resume_delay_seconds=10.0,
    )
    await coll.update_one({"processId": "r"}, {"$set": {
        "status": {"state": "cancelled"}, "hasSavedState": True,
        "autoResumeScheduled": True}})

    run_task = asyncio.create_task(fw.run())
    await asyncio.sleep(0.2)  # let run() arm the timer
    await fw.shutdown()
    await asyncio.wait_for(run_task, timeout=5.0)

    proc = await get_process_by_process_id(mongo_db, prefix, "r")
    assert proc["status"]["state"] == "cancelled"  # not resumed
    assert proc.get("autoResumeScheduled") is True  # stamp survives
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py -k "sweep or timer" -v`
Expected: FAIL — `AttributeError: 'Optio' object has no attribute '_auto_resume_scheduled_processes'` (and `_auto_resume_timer` / arming not present).

- [ ] **Step 3: Add the field, arming, teardown, and methods**

In `lifecycle.py` `__init__`, add the task handle (after `self._supervisor_task = None`, `:88`):

```python
        self._supervisor_task: asyncio.Task | None = None
        self._auto_resume_task: asyncio.Task | None = None
```

In `run`, arm the one-shot timer right after the supervisor task is created (`:918`):

```python
        self._supervisor_task = asyncio.create_task(self._supervisor_loop())
        # One-shot post-boot auto-resume timer. Fires once after
        # auto_resume_delay_seconds, re-launching stamped+saved processes.
        self._auto_resume_task = asyncio.create_task(self._auto_resume_timer())
```

In `shutdown`, tear the timer down. Add a new sub-step right after the heartbeat teardown (after `:959`, the `self._heartbeat_task = None` line):

```python
            # 1b. Auto-resume one-shot timer (cancel if it hasn't fired yet).
            if self._auto_resume_task:
                self._auto_resume_task.cancel()
                try:
                    await self._auto_resume_task
                except asyncio.CancelledError:
                    pass
                self._auto_resume_task = None
```

Add the two methods to the `Optio` class (place them just after `_supervisor_loop`, around `:1076`):

```python
    async def _auto_resume_timer(self) -> None:
        """One-shot: wait auto_resume_delay_seconds, then sweep for stamped
        processes and re-launch them. Cancelled cleanly at shutdown if it has
        not fired yet (stamps persist for the next boot's timer)."""
        delay = self._config.auto_resume_delay_seconds
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if not self._running:
            return
        try:
            await self._auto_resume_scheduled_processes()
        except Exception:
            logger.exception("auto-resume sweep failed")

    async def _auto_resume_scheduled_processes(self) -> None:
        """Re-launch every process stamped for auto-resume that genuinely saved
        its state: autoResumeScheduled=True AND state='cancelled' AND
        hasSavedState=True. Force-killed (failed) and unsaved processes are
        excluded by the query. On a blocked / non-launchable target, log, clear
        the stamp, and skip (no retry). launch() clears the stamp on success."""
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        cursor = coll.find(
            {
                "autoResumeScheduled": True,
                "status.state": "cancelled",
                "hasSavedState": True,
            },
            {"_id": 1, "processId": 1},
        )
        docs = [doc async for doc in cursor]
        if not docs:
            return
        logger.info(f"Auto-resuming {len(docs)} scheduled process(es)")
        for doc in docs:
            outcome = await self.launch(str(doc["_id"]), resume=True, session_id=None)
            if outcome.ok:
                logger.info(f"Auto-resumed {doc['processId']}")
            else:
                logger.warning(
                    f"Auto-resume skipped {doc['processId']}: {outcome.reason}"
                )
                await set_auto_resume_scheduled(
                    self._config.mongo_db, self._config.prefix, doc["_id"], False,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py -k "sweep or timer" -v`
Expected: PASS

- [ ] **Step 5: Run the full auto-resume module + the reconciliation/shutdown suite (regression check)**

Run: `cd packages/optio-core && python -m pytest tests/test_auto_resume.py tests/test_lifecycle_reconciliation.py tests/test_shutdown_drain_completion.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_auto_resume.py
git commit -m "feat(optio-core): one-shot post-boot auto-resume timer + sweep"
```

---

## Task 9: Contract field `autoResumeScheduled`

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts:71-72`

- [ ] **Step 1: Add the field**

In `process.ts`, `ProcessSchema`, after the `supportsResume` / `hasSavedState` block (`:72`):

```typescript
  // Resume feature — default false when absent in stored doc (UI treats
  // missing fields as false defensively)
  supportsResume: z.boolean().optional(),
  hasSavedState: z.boolean().optional(),

  // Auto-resume on restart: set on a cancelled, state-saved top-level process
  // whose task opted into auto_resume; the engine re-launches it (resume=true)
  // after a post-boot delay. Cleared on resume / manual launch / failed.
  autoResumeScheduled: z.boolean().optional(),
```

- [ ] **Step 2: Type-check / build the contract package**

Run: `pnpm --filter optio-contracts build`
Expected: PASS (no TS errors)

- [ ] **Step 3: Commit**

```bash
git add packages/optio-contracts/src/schemas/process.ts
git commit -m "feat(optio-contracts): autoResumeScheduled on ProcessSchema"
```

---

## Task 10: `ProcessStatusBadge` indicator + `ProcessStateLike` field

**Files:**
- Modify: `packages/optio-ui/src/process-state.ts:42-47`; `packages/optio-ui/src/components/ProcessStatusBadge.tsx`
- Test: `packages/optio-ui/src/__tests__/ProcessStatusBadge.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

Create `packages/optio-ui/src/__tests__/ProcessStatusBadge.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

import { ProcessStatusBadge } from '../components/ProcessStatusBadge.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

function renderBadge(props: any) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ProcessStatusBadge {...props} />
    </I18nextProvider>,
  );
}

describe('ProcessStatusBadge auto-resume indicator', () => {
  it('shows the stopwatch indicator when autoResumeScheduled is true', () => {
    renderBadge({ state: 'cancelled', autoResumeScheduled: true });
    expect(screen.getByLabelText('Scheduled for auto-restart')).toBeTruthy();
  });

  it('does not show the indicator when autoResumeScheduled is false/absent', () => {
    renderBadge({ state: 'cancelled' });
    expect(screen.queryByLabelText('Scheduled for auto-restart')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter optio-ui test -- ProcessStatusBadge`
Expected: FAIL — `Unable to find a label with the text of: Scheduled for auto-restart`

- [ ] **Step 3: Add the `ProcessStateLike` field**

In `process-state.ts`, `ProcessStateLike` (`:42-47`):

```typescript
export interface ProcessStateLike {
  status?: { state?: string } | null;
  cancellable?: boolean;
  supportsResume?: boolean;
  hasSavedState?: boolean;
  autoResumeScheduled?: boolean;
}
```

- [ ] **Step 4: Add the prop + indicator to `ProcessStatusBadge`**

In `ProcessStatusBadge.tsx`, update the icon import (`:2`):

```tsx
import { ExclamationCircleOutlined, ClockCircleOutlined } from '@ant-design/icons';
```

Extend the props interface (`:54-59`):

```tsx
interface ProcessStatusBadgeProps {
  state: string;
  error?: string;
  runningSince?: string | null;
  size?: ProcessStatusBadgeSize;
  /** When true, render a stopwatch indicator: this process is stamped for
   *  automatic resume after an engine restart. */
  autoResumeScheduled?: boolean;
}
```

Update the component signature + render (`:61-80`):

```tsx
export function ProcessStatusBadge({ state, error, runningSince, size = 'small', autoResumeScheduled }: ProcessStatusBadgeProps) {
  const { t } = useTranslation();
  const color = STATUS_COLORS[state] ?? 'default';
  const label = t(`status.${state}`, state);
  const isActive = isActiveState(state);
  const elapsed = useElapsed(runningSince, isActive);

  const autoResumeLabel = t('status.autoResumeScheduled', 'Scheduled for auto-restart');

  return (
    <span>
      <Tag color={color} style={SIZE_STYLE[size]}>
        {label}
        {elapsed && ` (${elapsed})`}
      </Tag>
      {state === 'failed' && error && (
        <Tooltip title={error}>
          <ExclamationCircleOutlined style={{ color: '#ff4d4f', marginLeft: 4 }} />
        </Tooltip>
      )}
      {autoResumeScheduled && (
        <Tooltip title={autoResumeLabel}>
          <ClockCircleOutlined aria-label={autoResumeLabel} style={{ color: '#722ed1', marginLeft: 4 }} />
        </Tooltip>
      )}
    </span>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pnpm --filter optio-ui test -- ProcessStatusBadge`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/optio-ui/src/process-state.ts packages/optio-ui/src/components/ProcessStatusBadge.tsx packages/optio-ui/src/__tests__/ProcessStatusBadge.test.tsx
git commit -m "feat(optio-ui): auto-resume stopwatch indicator on ProcessStatusBadge"
```

---

## Task 11: Pass the field through `ProcessItem` and `ProcessTreeView`

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessItem.tsx:105-110`; `packages/optio-ui/src/components/ProcessTreeView.tsx:12-23,75`

- [ ] **Step 1: Wire `ProcessItem`**

In `ProcessItem.tsx`, the `ProcessStatusBadge` call (`:105-110`) — add the prop (the full `process` object is in scope):

```tsx
      <ProcessStatusBadge
        state={state}
        error={process.status?.error}
        runningSince={process.status?.runningSince}
        size={size}
        autoResumeScheduled={process.autoResumeScheduled}
      />
```

- [ ] **Step 2: Wire `ProcessTreeView`**

In `ProcessTreeView.tsx`, add the field to the `ProcessNode` interface (`:12-23`):

```tsx
interface ProcessNode {
  _id: string;
  name: string;
  description?: string | null;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  warning?: string;
  supportsResume?: boolean;
  hasSavedState?: boolean;
  autoResumeScheduled?: boolean;
  children?: ProcessNode[];
}
```

And the badge call (`:75`):

```tsx
          <ProcessStatusBadge state={node.status.state} error={node.status.error} runningSince={node.status.runningSince} autoResumeScheduled={node.autoResumeScheduled} />
```

- [ ] **Step 3: Build + run the optio-ui test suite (regression)**

Run: `pnpm --filter optio-ui build && pnpm --filter optio-ui test`
Expected: PASS (build clean, all tests green)

- [ ] **Step 4: Commit**

```bash
git add packages/optio-ui/src/components/ProcessItem.tsx packages/optio-ui/src/components/ProcessTreeView.tsx
git commit -m "feat(optio-ui): forward autoResumeScheduled through ProcessItem and ProcessTreeView"
```

---

## Task 12: Excavator engine — opt the two free-style analysis tasks in

**Repository:** `/home/csillag/deai/excavator`

**Files:**
- Modify: `packages/engine/src/engine/free_style/task.py:122-129` (`_finalize_ti`)
- Test: `packages/engine/tests/test_free_style_task.py`

> **Dependency:** This requires the `optio_core` build carrying `TaskInstance.auto_resume` (Task 1). Ensure excavator resolves the local/linked optio-core from this branch before running the test.

- [ ] **Step 1: Write the failing test**

Append to `packages/engine/tests/test_free_style_task.py` (match the file's existing import style; add imports at the top if absent):

```python
from optio_core.models import TaskInstance
from engine.free_style.task import _finalize_ti


def test_finalize_ti_sets_auto_resume():
    async def _exec(ctx):  # noqa: ARG001
        pass

    ti = TaskInstance(
        execute=_exec, process_id="x/analyze/y/claudecode", name="Analyze",
        supports_resume=True,
    )
    out = _finalize_ti(ti, source_id="y", dataspace_slug="x", session_state={})
    assert out.auto_resume is True
    assert out.supports_resume is True  # precondition for auto_resume
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/csillag/deai/excavator && python -m pytest packages/engine/tests/test_free_style_task.py::test_finalize_ti_sets_auto_resume -v`
Expected: FAIL — `assert out.auto_resume is True` (it is `False`).

- [ ] **Step 3: Set the flag in `_finalize_ti`**

In `packages/engine/src/engine/free_style/task.py`, `_finalize_ti` (`:122-129`):

```python
def _finalize_ti(ti, source_id: str, dataspace_slug: str, session_state: dict):
    """Attach params and wrap execute with terminal-state tracking. Shared by
    both free-style backends (opencode + claudecode)."""
    ti.params = {"source_id": source_id, "dataspace": dataspace_slug}
    # Free-style analyses are long-running, resumable, top-level tasks: opt them
    # into auto-resume so an engine restart re-launches them automatically
    # (post-delay) instead of leaving them cancelled for a human to relaunch.
    ti.auto_resume = True
    ti.execute = _wrap_execute_with_state_tracking(
        ti.execute, source_id, dataspace_slug, session_state,
    )
    return ti
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/csillag/deai/excavator && python -m pytest packages/engine/tests/test_free_style_task.py::test_finalize_ti_sets_auto_resume -v`
Expected: PASS

- [ ] **Step 5: Run the free-style task suite (regression)**

Run: `cd /home/csillag/deai/excavator && python -m pytest packages/engine/tests/test_free_style_task.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/engine/src/engine/free_style/task.py packages/engine/tests/test_free_style_task.py
git commit -m "feat(engine): opt free-style analyses into auto_resume on restart"
```

---

## Task 13: Excavator frontend — i18n label + ProcessDetailPage badge prop

**Repository:** `/home/csillag/deai/excavator`

**Files:**
- Modify: `packages/frontend/src/` i18n files `en.json`, `hu.json`, `de.json` (locate via `git grep -l '"status"' packages/frontend/src`)
- Modify: `packages/frontend/src/features/processes/ProcessDetailPage.tsx:104` (header badge)

> **Note:** Per project convention, every i18n key is added to **en.json, hu.json, AND de.json** in the same change (the implementer authors all three; the user does not translate manually).

> **Dependency:** Requires the `optio-ui` build carrying the new `ProcessStatusBadge` prop (Task 10).

- [ ] **Step 1: Locate the i18n `status.*` block**

Run: `cd /home/csillag/deai/excavator && git grep -n '"cancelled"' packages/frontend/src | grep -i json`
Expected: shows the `status` object in `en.json`, `hu.json`, `de.json` (the keys mirroring optio process states).

- [ ] **Step 2: Add the `autoResumeScheduled` key to all three locale files**

In the `status` object of `en.json`:

```json
    "autoResumeScheduled": "Scheduled for auto-restart"
```

In `hu.json`:

```json
    "autoResumeScheduled": "Automatikus újraindításra ütemezve"
```

In `de.json`:

```json
    "autoResumeScheduled": "Für automatischen Neustart geplant"
```

(Insert as a new member of the existing `status` object in each file; mind the trailing-comma placement so the JSON stays valid.)

- [ ] **Step 3: Pass the prop at the ProcessDetailPage header badge**

In `packages/frontend/src/features/processes/ProcessDetailPage.tsx`, the header `ProcessStatusBadge` usage (around `:104`) — add `autoResumeScheduled={process.autoResumeScheduled}` to the existing element (the `process` doc is already in scope there):

```tsx
<ProcessStatusBadge
  state={process.status.state}
  error={process.status.error}
  runningSince={process.status.runningSince}
  autoResumeScheduled={process.autoResumeScheduled}
/>
```

(If the existing usage is a single-line element, add the one prop inline; keep the other props unchanged.)

- [ ] **Step 4: Type-check / build the frontend**

Run: `cd /home/csillag/deai/excavator && pnpm --filter frontend build`
Expected: PASS (no TS errors; the new prop is optional on the badge so list/tree/table usages compile unchanged)

- [ ] **Step 5: Verify the JSON parses**

Run: `cd /home/csillag/deai/excavator && node -e "for (const f of ['en','hu','de']) { const p=require('child_process').execSync('git grep -l \"autoResumeScheduled\" packages/frontend/src').toString(); } console.log('ok')"`
Simpler check — run the frontend test/lint if present: `cd /home/csillag/deai/excavator && pnpm --filter frontend test` (or `pnpm --filter frontend lint`).
Expected: PASS / JSON valid.

- [ ] **Step 6: Commit**

```bash
git add packages/frontend/src
git commit -m "feat(frontend): surface auto-resume-scheduled badge on process detail"
```

---

## Final Verification

- [ ] **optio-core full suite** (regression across the cancel/shutdown/reconcile machinery this feature touches):

Run: `cd /home/csillag/deai/optio/.worktrees/autoresume/packages/optio-core && python -m pytest tests/ -v`
Expected: PASS (note pre-existing cancel-suite flakiness under full-suite load is a known issue unrelated to this change — re-run any isolated failures individually to confirm).

- [ ] **optio TS packages**:

Run: `cd /home/csillag/deai/optio/.worktrees/autoresume && pnpm --filter optio-contracts build && pnpm --filter optio-ui build && pnpm --filter optio-ui test`
Expected: PASS

- [ ] **excavator engine + frontend**:

Run: `cd /home/csillag/deai/excavator && python -m pytest packages/engine/tests/test_free_style_task.py -v && pnpm --filter frontend build`
Expected: PASS

- [ ] **Manual smoke (optional, requires a running engine):** launch a free-style analysis, kill the engine while it runs, confirm the process lands `cancelled` with the stopwatch badge, restart the engine, and confirm it auto-resumes after `auto_resume_delay_seconds`.

---

## Self-Review Notes

- **Spec §1 (flag)** → Task 1 (optio-core), Task 12 (excavator opt-in via `_finalize_ti`).
- **Spec §2 (validation)** → Task 4.
- **Spec §3 (stamp field)** → Task 3 (Python doc/store), Task 9 (contract), Task 10 (`ProcessStateLike`).
- **Spec §4 (shutdown stamping, top-level only)** → Task 5.
- **Spec §5 (failed clears stamp)** → Task 6.
- **Spec §6 (startup timer + sweep)** → Task 8.
- **Spec §7 (manual launch clears stamp)** → Task 7.
- **Spec §8 (edge cases)** → covered by tests in Task 8 (`test_timer_does_not_fire_if_shutdown_first` = second-shutdown/stamp-survival) and Task 6/7.
- **Spec §9 (UI indicator)** → Tasks 10–11 (optio-ui), Task 13 (excavator i18n + detail-page prop).
- **Spec §10 (excavator opt-in)** → Task 12.

**Naming consistency:** `auto_resume` (Python field), `autoResumeScheduled` (doc/contract/TS field), `auto_resume_delay_seconds` (config), `set_auto_resume_scheduled` (store helper), `_stamp_auto_resume_if_eligible` / `_auto_resume_timer` / `_auto_resume_scheduled_processes` (lifecycle methods) — used identically across all tasks.

**Known scope caveat (UI):** `ProcessStatusBadge` is prop-based, so the indicator reaches displays mediated by `ProcessItem` / `ProcessTreeView` for free (process lists, dashboard recent, detail-page tree), but excavator's *direct* `ProcessStatusBadge` usages (SourcesTable, SourceOverview, RecentSourceActivity) would each need the optional prop added to show it there. This plan wires the prop only at the primary detail view (Task 13); the other direct usages can adopt it later if desired (the optional prop defaults to no indicator, so they compile and behave unchanged).
```
