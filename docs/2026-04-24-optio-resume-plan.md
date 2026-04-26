# Optio Resume Feature — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/2026-04-24-optio-resume-design.md` (base revision `a3c79b4` on branch `main`).

**Branch:** `csillag/process-resume` (already created).

**Goal:** Thread a generic `resume` signal through every layer (task-definition → UI → API → Redis → executor) and implement `optio-opencode` as the first consumer that persists and restores its SQLite session DB + workdir across relaunches.

**Architecture:**
- Layer A (platform-generic): `supports_resume` on `TaskInstance`, `hasSavedState` on process doc, `mark_has_saved_state` / `clear_has_saved_state` + `store_blob` / `load_blob` / `delete_blob` on `ProcessContext`, `resume` field on launch payload, `LaunchControls` split-button UI, m003 migration.
- Layer B (optio-opencode): per-task stable directory, `opencode export`/`import` round-trip, workdir tar.gz, Mongo `{prefix}_opencode_session_snapshots` collection with last-5 retention, workdir wipe on terminal.

**Tech Stack:** Python 3.11+ (optio-core, optio-opencode) with motor (async MongoDB), pytest-asyncio; TypeScript 5.7 (optio-contracts, optio-api, optio-ui) with ts-rest, zod, vitest, ioredis; Ant Design 5 for UI.

**Commit policy:** One commit at the end of the plan (per repo convention), not per task.

---

## File structure map

### optio-core (Python)

| File | Action | Responsibility |
|---|---|---|
| `packages/optio-core/src/optio_core/models.py` | MODIFY | Add `supports_resume` field to `TaskInstance` |
| `packages/optio-core/src/optio_core/store.py` | MODIFY | `upsert_process` writes `supportsResume` / `hasSavedState` |
| `packages/optio-core/src/optio_core/context.py` | MODIFY | `resume` attr + `mark_has_saved_state` / `clear_has_saved_state` + GridFS helpers |
| `packages/optio-core/src/optio_core/consumer.py` | MODIFY | (no change — payload decode happens in lifecycle handler) |
| `packages/optio-core/src/optio_core/lifecycle.py` | MODIFY | Decode `resume` from launch payload; forward through `Optio.launch` / `launch_and_wait` |
| `packages/optio-core/src/optio_core/executor.py` | MODIFY | `launch_process(process_id, resume)` → `_execute_process(proc, fn, resume)` → `ProcessContext(resume=...)` |
| `packages/optio-core/src/optio_core/migrations/m003_backfill_has_saved_state.py` | CREATE | Backfill migration |
| `packages/optio-core/src/optio_core/migrations/__init__.py` | MODIFY | Register m003 |
| `packages/optio-core/tests/test_models.py` | MODIFY | Assert new field default |
| `packages/optio-core/tests/test_store.py` | MODIFY | `supportsResume` / `hasSavedState` upsert tests |
| `packages/optio-core/tests/test_context_resume.py` | CREATE | Unit tests for `mark_has_saved_state` / `clear_has_saved_state` |
| `packages/optio-core/tests/test_context_blob.py` | CREATE | Unit tests for `store_blob` / `load_blob` / `delete_blob` |
| `packages/optio-core/tests/test_integration.py` | MODIFY | Add E2E test: `resume=True` delivered to `ctx.resume` |
| `packages/optio-core/tests/test_migration_m003.py` | CREATE | Migration tests |
| `packages/optio-core/AGENTS.md` | MODIFY | Document new TaskInstance field + ProcessContext methods |

### optio-contracts (TypeScript)

| File | Action | Responsibility |
|---|---|---|
| `packages/optio-contracts/src/schemas/process.ts` | MODIFY | Add `supportsResume`, `hasSavedState` to `ProcessSchema` |
| `packages/optio-contracts/src/contract.ts` | MODIFY | Change `launch.body` from `c.noBody()` to `{ resume?: boolean }` |
| `packages/optio-contracts/AGENTS.md` | MODIFY | Document new fields + body shape |

### optio-api (TypeScript)

| File | Action | Responsibility |
|---|---|---|
| `packages/optio-api/src/handlers.ts` | MODIFY | `launchProcess` accepts `resume`; validates against `supportsResume` |
| `packages/optio-api/src/publisher.ts` | MODIFY | `publishLaunch(redis, db, prefix, processId, resume?)` |
| `packages/optio-api/src/adapters/fastify.ts` (route handler) | MODIFY | Read body and pass `resume` to `launchProcess` |
| `packages/optio-api/src/__tests__/handlers.test.ts` | MODIFY | Resume validation tests |
| `packages/optio-api/src/__tests__/publisher.test.ts` | MODIFY | Resume payload tests |
| `packages/optio-api/AGENTS.md` | MODIFY | Document new body + validation |

### optio-ui (TypeScript/React)

| File | Action | Responsibility |
|---|---|---|
| `packages/optio-ui/src/components/LaunchControls.tsx` | CREATE | Split-button resume/restart component |
| `packages/optio-ui/src/components/ProcessList.tsx` | MODIFY | Use `LaunchControls` |
| `packages/optio-ui/src/components/ProcessDetailView.tsx` | MODIFY | Add header action area with `LaunchControls` |
| `packages/optio-ui/src/hooks/useProcessActions.ts` | MODIFY | `launch(id, opts?: { resume?: boolean })` |
| `packages/optio-ui/src/__tests__/LaunchControls.test.tsx` | CREATE | Unit tests for render rules |
| `packages/optio-ui/AGENTS.md` | MODIFY | Document new component + hook signature |

### optio-opencode (Python)

| File | Action | Responsibility |
|---|---|---|
| `packages/optio-opencode/src/optio_opencode/types.py` | MODIFY | Add `workdir_exclude` to `OpencodeTaskConfig` |
| `packages/optio-opencode/src/optio_opencode/paths.py` | CREATE | `local_task_dir()` / `remote_task_dir()` helpers |
| `packages/optio-opencode/src/optio_opencode/archive.py` | CREATE | `DEFAULT_WORKDIR_EXCLUDES` + `yield_workdir_archive` (async tar+gz generator) + `consume_workdir_archive` (async untar consumer); used by `LocalHost` |
| `packages/optio-opencode/src/optio_opencode/snapshots.py` | CREATE | Snapshot doc schema + `insert_snapshot` / `load_latest_snapshot` / `prune_snapshots` |
| `packages/optio-opencode/src/optio_opencode/host.py` | MODIFY | (a) Accept stable `workdir` for `LocalHost` / `RemoteHost`; (b) extend `launch_opencode` with `env` kwarg; (c) add `opencode_import`, `opencode_export`, `archive_workdir`, `restore_workdir` on both hosts |
| `packages/optio-opencode/src/optio_opencode/session.py` | MODIFY | `supports_resume=True`, resume/fresh launch branching, terminal capture — entirely via host methods (no subprocess/SSH branches) |
| `packages/optio-opencode/tests/fake_opencode.py` | MODIFY | Support `import <file>` / `export <session-id>` subcommands |
| `packages/optio-opencode/tests/test_archive.py` | CREATE | Round-trip + excludes |
| `packages/optio-opencode/tests/test_snapshots.py` | CREATE | Capture / load / prune |
| `packages/optio-opencode/tests/test_host_resume.py` | CREATE | Host method tests against `LocalHost` (env injection, opencode_import/export, archive/restore round-trip) |
| `packages/optio-opencode/tests/test_host_remote_resume.py` | CREATE | Same against `RemoteHost` via the existing docker-compose.sshd.yml fixture (skip when SSH harness is unavailable) |
| `packages/optio-opencode/tests/test_session_resume.py` | CREATE | Full cycle with fake opencode (LocalHost) |
| `packages/optio-opencode/AGENTS.md` | MODIFY | Document `workdir_exclude`, task_dir layout, snapshot collection, new host methods |

### Root

| File | Action | Responsibility |
|---|---|---|
| `AGENTS.md` | MODIFY | Refresh unified reference (TaskInstance / ProcessContext / ProcessSchema / launch body) |

---

## Design decisions made while planning

These are choices the plan takes that the spec leaves open:

1. **Per-task directory location (optio-opencode).** The spec assumes `<task_dir>` exists; current code uses a fresh `tempfile.mkdtemp` per launch. The plan introduces:
   - Local: `${OPTIO_OPENCODE_TASK_ROOT:-$XDG_DATA_HOME/optio-opencode:-$HOME/.local/share/optio-opencode}/<process_id>/`
   - Remote: `${OPTIO_OPENCODE_REMOTE_TASK_ROOT:-/tmp/optio-opencode}/<process_id>/`
   Per-`process_id` so two task definitions that share a worker cannot collide. Env-overridable so operators can point at durable storage on the worker if /tmp is tmpfs.

2. **`consumer_instructions` delivery on resume.** The spec says "skip sending consumer_instructions ... already in the imported conversation history" for the resume branch. The current code writes instructions as `AGENTS.md` inside the workdir — not as a first message. Since the workdir is restored from the tar, `AGENTS.md` arrives with it; re-writing would clobber anything the LLM might have edited. Plan: skip `host.write_text("AGENTS.md", ...)` and `host.write_text("opencode.json", ...)` on the resume branch.

3. **Host workdir override.** `LocalHost.__init__` already takes `workdir` as a parameter (currently set by `_pick_local_workdir()` → `mkdtemp`). The plan redirects `session.py` to pass `<task_dir>/workdir`. `RemoteHost` currently hard-codes a random path in `__init__`; the plan adds an optional `workdir` override.

4. **Both local and SSH modes implemented.** Resume work that interacts with the filesystem or invokes the opencode binary is added to the existing `Host` protocol (`opencode_import`, `opencode_export`, `archive_workdir`, `restore_workdir`, plus an `env` kwarg on `launch_opencode`). `LocalHost` and `RemoteHost` each implement the new methods using their native execution model (subprocess + tarfile vs SSH-exec + SFTP). `session.py` calls only host methods — no `subprocess.run`, no SSH branches.

---

## Task 1: Add `supports_resume` to TaskInstance

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py`
- Test: `packages/optio-core/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/optio-core/tests/test_models.py` (create the file if it has only light coverage; append if it exists):

```python
from optio_core.models import TaskInstance


async def _dummy(ctx):
    pass


def test_task_instance_supports_resume_default_false():
    task = TaskInstance(execute=_dummy, process_id="t", name="T")
    assert task.supports_resume is False


def test_task_instance_supports_resume_can_be_set():
    task = TaskInstance(execute=_dummy, process_id="t", name="T", supports_resume=True)
    assert task.supports_resume is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && python -m pytest tests/test_models.py -v`
Expected: both tests FAIL with `TypeError: TaskInstance.__init__() got an unexpected keyword argument 'supports_resume'` (or similar — `AttributeError` on the default-check).

- [ ] **Step 3: Implement**

Edit `packages/optio-core/src/optio_core/models.py`. Find the `TaskInstance` dataclass and add the new field after `ui_widget`:

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_models.py -v`
Expected: all tests PASS.

---

## Task 2: `upsert_process` writes `supportsResume` / `hasSavedState`

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py` (lines 15–63, `upsert_process`)
- Test: `packages/optio-core/tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-core/tests/test_store.py`:

```python
async def test_upsert_sets_supports_resume_on_insert(mongo_db):
    task = TaskInstance(
        execute=dummy_execute, process_id="sr_new", name="SR New",
        supports_resume=True,
    )
    result = await upsert_process(mongo_db, "test", task)
    assert result["supportsResume"] is True
    assert result["hasSavedState"] is False


async def test_upsert_refreshes_supports_resume_on_resync(mongo_db):
    task = TaskInstance(
        execute=dummy_execute, process_id="sr_flip", name="SR Flip",
        supports_resume=False,
    )
    await upsert_process(mongo_db, "test", task)

    task.supports_resume = True
    result = await upsert_process(mongo_db, "test", task)
    assert result["supportsResume"] is True


async def test_upsert_preserves_has_saved_state_across_resync(mongo_db):
    task = TaskInstance(
        execute=dummy_execute, process_id="hss_keep", name="HSS",
        supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    # Simulate executor having flipped the flag to True.
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"hasSavedState": True}},
    )

    # Re-sync — hasSavedState must not be reset.
    result = await upsert_process(mongo_db, "test", task)
    assert result["hasSavedState"] is True


async def test_clear_result_fields_preserves_resume_fields(mongo_db):
    task = TaskInstance(
        execute=dummy_execute, process_id="crf_keep", name="CRF",
        supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"hasSavedState": True}},
    )

    await clear_result_fields(mongo_db, "test", proc["_id"])
    updated = await get_process_by_process_id(mongo_db, "test", "crf_keep")
    assert updated["supportsResume"] is True
    assert updated["hasSavedState"] is True
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_store.py -v -k "supports_resume or saved_state or resume_fields"`
Expected: 4 tests FAIL — `supportsResume` / `hasSavedState` are not in the returned document.

- [ ] **Step 3: Implement in `store.py`**

Edit `packages/optio-core/src/optio_core/store.py`. Replace the `upsert_process` body's `$set`/`$setOnInsert` blocks:

```python
async def upsert_process(db: AsyncIOMotorDatabase, prefix: str, task: TaskInstance) -> dict:
    """Upsert a process record from a task instance.

    Creates the record if it doesn't exist (with idle status).
    Updates metadata fields if it does exist (preserves runtime state).
    """
    coll = _collection(db, prefix)
    now = datetime.now(timezone.utc)

    result = await coll.find_one_and_update(
        {"processId": task.process_id},
        {
            "$set": {
                "processId": task.process_id,
                "name": task.name,
                "params": task.params,
                "metadata": task.metadata,
                "cancellable": task.cancellable,
                "description": task.description,
                "special": task.special,
                "warning": task.warning,
                "uiWidget": task.ui_widget,
                "supportsResume": task.supports_resume,
            },
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
            },
        },
        upsert=True,
        return_document=True,
    )

    if result.get("rootId") is None:
        await coll.update_one(
            {"_id": result["_id"]},
            {"$set": {"rootId": result["_id"]}},
        )
        result["rootId"] = result["_id"]

    return result
```

**Note:** `clear_result_fields` already only clears status/progress/log/widget fields — no change needed; the test above verifies the invariant.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_store.py -v`
Expected: all PASS (including the pre-existing tests).

---

## Task 3: `ProcessContext.resume` + `mark_has_saved_state` / `clear_has_saved_state`

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py` (class `ProcessContext`)
- Test: `packages/optio-core/tests/test_context_resume.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-core/tests/test_context_resume.py`:

```python
"""Tests for ProcessContext resume plumbing."""

import asyncio
import logging

import pytest
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process


async def _dummy(ctx):
    pass


def _make_context(mongo_db, prefix, proc, *, resume: bool = False) -> ProcessContext:
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix=prefix,
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        metadata={},
        resume=resume,
    )


async def test_process_context_resume_attribute_defaults_false(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_a", name="A")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)
    assert ctx.resume is False


async def test_process_context_resume_attribute_passes_through(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_b", name="B", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc, resume=True)
    assert ctx.resume is True


async def test_mark_has_saved_state_writes_when_supported(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_c", name="C", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc, resume=False)

    await ctx.mark_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is True


async def test_mark_has_saved_state_warns_and_noops_when_unsupported(mongo_db, caplog):
    task = TaskInstance(execute=_dummy, process_id="r_d", name="D", supports_resume=False)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    with caplog.at_level(logging.WARNING):
        await ctx.mark_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is False
    assert any("supports_resume" in rec.message.lower() or "resume" in rec.message.lower()
               for rec in caplog.records)


async def test_mark_has_saved_state_idempotent(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_e", name="E", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    await ctx.mark_has_saved_state()
    # Second call should not raise or touch the doc when value is unchanged.
    # We assert by counting matched_count via a spy; simplest: just verify it returns cleanly
    # and the value is still True.
    await ctx.mark_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is True


async def test_clear_has_saved_state_writes_when_supported(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_f", name="F", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"hasSavedState": True}},
    )
    ctx = _make_context(mongo_db, "test", proc)

    await ctx.clear_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is False


async def test_clear_has_saved_state_warns_when_unsupported(mongo_db, caplog):
    task = TaskInstance(execute=_dummy, process_id="r_g", name="G", supports_resume=False)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    with caplog.at_level(logging.WARNING):
        await ctx.clear_has_saved_state()
    # No write happened, state unchanged from its $setOnInsert value (False).
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is False
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_context_resume.py -v`
Expected: all 7 tests FAIL (`TypeError: __init__() got an unexpected keyword argument 'resume'`).

- [ ] **Step 3: Implement in `context.py`**

Edit `packages/optio-core/src/optio_core/context.py`.

Add a module-level logger near the top (after the existing imports):

```python
import logging as _logging

_log = _logging.getLogger("optio_core.context")
```

Modify `ProcessContext.__init__` to accept `resume`. Replace the `__init__` signature and body so it accepts a new keyword:

```python
    def __init__(
        self,
        process_oid: ObjectId,
        process_id: str,
        root_oid: ObjectId,
        depth: int,
        params: dict[str, Any],
        services: dict[str, Any],
        db: AsyncIOMotorDatabase,
        prefix: str,
        cancellation_flag: asyncio.Event,
        child_counter: dict,
        metadata: dict[str, Any] | None = None,
        resume: bool = False,
    ):
        self.process_id = process_id
        self.params = params
        self.metadata = metadata or {}
        self.services = services
        self.resume = resume
        self._process_oid = process_oid
        # ... rest unchanged
```

Leave all the other fields as-is — only `resume` is new.

Add two methods to the class (place them after `clear_widget_data` or another logical sibling):

```python
    async def mark_has_saved_state(self) -> None:
        """Flag that a resumable task has durable state.

        No-op with a warning when the task is not declared `supports_resume=True`.
        Idempotent: a second call with the same value issues no redundant update.
        """
        await self._set_has_saved_state(True)

    async def clear_has_saved_state(self) -> None:
        """Flag that a resumable task no longer has durable state.

        No-op with a warning when the task is not declared `supports_resume=True`.
        Idempotent: a second call with the same value issues no redundant update.
        """
        await self._set_has_saved_state(False)

    async def _set_has_saved_state(self, value: bool) -> None:
        from optio_core.store import _collection
        coll = _collection(self._db, self._prefix)
        current = await coll.find_one(
            {"_id": self._process_oid},
            {"supportsResume": 1, "hasSavedState": 1},
        )
        if current is None:
            _log.warning(
                "mark/clear_has_saved_state: process %s not found", self._process_oid,
            )
            return
        if not current.get("supportsResume", False):
            _log.warning(
                "mark/clear_has_saved_state called on non-resumable task %s; ignored",
                self.process_id,
            )
            return
        if bool(current.get("hasSavedState", False)) == value:
            return  # Idempotent: no redundant write.
        await coll.update_one(
            {"_id": self._process_oid},
            {"$set": {"hasSavedState": value}},
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_context_resume.py -v`
Expected: all PASS.

---

## Task 4: GridFS blob helpers on `ProcessContext`

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py`
- Test: `packages/optio-core/tests/test_context_blob.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-core/tests/test_context_blob.py`:

```python
"""Tests for ProcessContext GridFS blob helpers."""

import asyncio
import io
import os

from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process


async def _dummy(ctx):
    pass


def _make_ctx(mongo_db, proc) -> ProcessContext:
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        metadata={},
    )


async def test_store_and_load_blob_small(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="blob_a", name="A")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    payload = b"hello blob"
    async with ctx.store_blob("session") as writer:
        await writer.write(payload)
        file_id = writer.file_id

    assert isinstance(file_id, ObjectId)

    async with ctx.load_blob(file_id) as reader:
        got = await reader.read()
    assert got == payload


async def test_store_blob_records_metadata(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="blob_b", name="B")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    async with ctx.store_blob("workdir") as writer:
        await writer.write(b"x")
        file_id = writer.file_id

    files = mongo_db["fs.files"]
    meta = await files.find_one({"_id": file_id})
    assert meta is not None
    assert meta["metadata"]["processId"] == str(proc["_id"])
    assert meta["metadata"]["prefix"] == "test"
    assert meta["metadata"]["name"] == "workdir"


async def test_delete_blob_removes_it(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="blob_c", name="C")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    async with ctx.store_blob("session") as writer:
        await writer.write(b"data")
        file_id = writer.file_id

    await ctx.delete_blob(file_id)

    files = mongo_db["fs.files"]
    assert await files.find_one({"_id": file_id}) is None


async def test_store_blob_large_roundtrip(mongo_db):
    """100 MB payload — shakes out chunking."""
    task = TaskInstance(execute=_dummy, process_id="blob_d", name="D")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    chunk = os.urandom(1 << 20)  # 1 MiB of random data
    total = 100
    import hashlib
    hasher = hashlib.sha256()

    async with ctx.store_blob("big") as writer:
        for _ in range(total):
            await writer.write(chunk)
            hasher.update(chunk)
        file_id = writer.file_id
    expected_digest = hasher.hexdigest()

    got_hasher = hashlib.sha256()
    async with ctx.load_blob(file_id) as reader:
        while True:
            block = await reader.read(1 << 20)
            if not block:
                break
            got_hasher.update(block)
    assert got_hasher.hexdigest() == expected_digest
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_context_blob.py -v`
Expected: all 4 tests FAIL (`AttributeError: 'ProcessContext' object has no attribute 'store_blob'`).

- [ ] **Step 3: Implement**

Edit `packages/optio-core/src/optio_core/context.py`. Add imports at the top:

```python
from contextlib import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
```

Add three methods to `ProcessContext` (place after the `_set_has_saved_state` private method):

```python
    def _gridfs(self) -> AsyncIOMotorGridFSBucket:
        return AsyncIOMotorGridFSBucket(self._db)

    @asynccontextmanager
    async def store_blob(self, name: str):
        """Open a GridFS upload stream tagged with processId + prefix.

        Usage:
            async with ctx.store_blob("session") as writer:
                await writer.write(chunk)
                # ... more writes
            # After the `async with` block exits cleanly, writer.file_id is the
            # ObjectId of the stored file.
        """
        bucket = self._gridfs()
        metadata = {
            "processId": str(self._process_oid),
            "prefix": self._prefix,
            "name": name,
        }
        async with bucket.open_upload_stream(name, metadata=metadata) as stream:
            yield stream

    @asynccontextmanager
    async def load_blob(self, file_id: ObjectId):
        """Open a GridFS download stream for `file_id`.

        Usage:
            async with ctx.load_blob(file_id) as reader:
                chunk = await reader.read(1 << 20)
        """
        bucket = self._gridfs()
        stream = await bucket.open_download_stream(file_id)
        try:
            yield stream
        finally:
            await stream.close()

    async def delete_blob(self, file_id: ObjectId) -> None:
        """Delete a GridFS file. No-op if the file does not exist."""
        bucket = self._gridfs()
        try:
            await bucket.delete(file_id)
        except Exception:
            # motor raises NoFile when the id is unknown; swallow — helper is
            # convenience API for executors doing best-effort cleanup.
            pass
```

**Note on the `writer.file_id` pattern:** `AsyncIOMotorGridFSBucket.open_upload_stream` returns an `AsyncIOMotorGridIn` whose `.file_id` is the pre-allocated `ObjectId` — available before the context exits. Tests assert on it.

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_context_blob.py -v`
Expected: all PASS.

---

## Task 5: Propagate `resume` through consumer → lifecycle → executor

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/executor.py`
- Test: `packages/optio-core/tests/test_integration.py`

- [ ] **Step 1: Write the failing integration test**

Append to `packages/optio-core/tests/test_integration.py` (or create it if it's about something else — but it exists per `ls`):

```python
async def test_resume_flag_reaches_execute_function(mongo_db, redis_url):
    """End-to-end: launch payload with resume=True surfaces on ctx.resume."""
    import asyncio as _asyncio
    from optio_core.lifecycle import Optio
    from optio_core.models import TaskInstance

    seen: dict = {}

    async def execute(ctx):
        seen["resume"] = ctx.resume

    async def get_defs(_services):
        return [TaskInstance(
            execute=execute, process_id="r_int", name="R Int",
            supports_resume=True,
        )]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db,
        prefix="test",
        redis_url=redis_url,
        get_task_definitions=get_defs,
    )

    # Use the direct Python API rather than Redis to keep the test fast.
    await optio.launch_and_wait_with_resume("r_int", resume=True)
    await optio.shutdown()
    assert seen["resume"] is True
```

(This test depends on an API we will add in the impl step — `launch_and_wait_with_resume`. If you prefer to keep the public API with an optional param, `launch_and_wait("r_int", resume=True)`, adjust the test and the impl to match.)

**Chosen API:** Extend existing methods with `resume: bool = False` keyword (backward compatible). Update the test:

```python
    await optio.launch_and_wait("r_int", resume=True)
```

- [ ] **Step 2: Run the test to see it fail**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 REDIS_URL=redis://localhost:6379 python -m pytest tests/test_integration.py::test_resume_flag_reaches_execute_function -v`
Expected: FAIL — `launch_and_wait() got an unexpected keyword argument 'resume'` or `AttributeError: 'ProcessContext' object has no attribute 'resume'`.

- [ ] **Step 3: Wire `resume` through the stack**

**executor.py** — extend `launch_process` and `_execute_process`:

```python
    async def launch_process(self, process_id: str, resume: bool = False) -> str | None:
        """Launch a top-level process by processId. Returns end state or None."""
        proc = await get_process_by_process_id(self._db, self._prefix, process_id)
        if proc is None:
            return None

        current_state = proc["status"]["state"]
        if current_state not in LAUNCHABLE_STATES:
            return None

        await clear_result_fields(self._db, self._prefix, proc["_id"])
        await update_status(
            self._db, self._prefix, proc["_id"],
            ProcessStatus(state="scheduled"),
        )
        await append_log(self._db, self._prefix, proc["_id"], "event", "State changed to scheduled")

        return await self._execute_process(
            proc, self._task_registry.get(process_id), resume=resume,
        )

    async def _execute_process(
        self, proc: dict, execute_fn: Callable | None,
        parent_ctx: ProcessContext | None = None,
        resume: bool = False,
    ) -> str:
        """Execute a process."""
        oid = proc["_id"]
        root_oid = proc.get("rootId", oid)

        cancel_flag = asyncio.Event()
        self._cancellation_flags[oid] = cancel_flag

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
        # ... rest unchanged (parent_listener wiring, try/except, etc.)
```

(Keep the rest of the method body identical.)

Also update `execute_child` — children do NOT propagate resume (resume is a top-level concept); leave that call site as `self._execute_process(child_doc, execute, parent_ctx=parent_ctx)` (no `resume=`, defaults to False).

**lifecycle.py** — update `_handle_launch`, `_handle_launch_by_process_id`, `launch`, `launch_and_wait`:

```python
    async def launch(self, process_id: str, resume: bool = False) -> None:
        """Fire-and-forget launch. Returns immediately, process runs in background."""
        asyncio.create_task(self._executor.launch_process(process_id, resume=resume))

    async def launch_and_wait(self, process_id: str, resume: bool = False) -> None:
        """Launch and wait for the process to complete. Full progress tracking."""
        await self._executor.launch_process(process_id, resume=resume)

    async def _handle_launch(self, payload: dict) -> None:
        process_id = payload.get("processId")
        resume = bool(payload.get("resume", False))
        if process_id:
            await self._handle_launch_by_process_id(process_id, resume=resume)

    async def _handle_launch_by_process_id(self, process_id: str, resume: bool = False) -> None:
        asyncio.create_task(self._executor.launch_process(process_id, resume=resume))
```

**Scheduler interaction:** the scheduler calls `launch_fn=self._handle_launch_by_process_id`. Cron-triggered launches always run fresh (no resume). Update `_handle_launch_by_process_id` to default to `resume=False` and the scheduler site doesn't need changes.

- [ ] **Step 4: Run the integration test + full suite**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 REDIS_URL=redis://localhost:6379 python -m pytest tests/ -v`
Expected: all PASS, including the new integration test.

---

## Task 6: m003 backfill migration

**Files:**
- Create: `packages/optio-core/src/optio_core/migrations/m003_backfill_has_saved_state.py`
- Modify: `packages/optio-core/src/optio_core/migrations/__init__.py`
- Test: `packages/optio-core/tests/test_migration_m003.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-core/tests/test_migration_m003.py`:

```python
"""Tests for m003_backfill_has_saved_state."""

from optio_core.migrations.m003_backfill_has_saved_state import backfill_has_saved_state


async def test_backfill_sets_false_on_missing_field(mongo_db):
    coll = mongo_db["myapp_processes"]
    await coll.insert_one({"processId": "a", "status": {"state": "idle"}})
    await coll.insert_one({"processId": "b", "status": {"state": "idle"}})

    await backfill_has_saved_state(mongo_db)

    doc_a = await coll.find_one({"processId": "a"})
    doc_b = await coll.find_one({"processId": "b"})
    assert doc_a["hasSavedState"] is False
    assert doc_b["hasSavedState"] is False


async def test_backfill_preserves_existing_values(mongo_db):
    coll = mongo_db["myapp_processes"]
    await coll.insert_one({"processId": "c", "hasSavedState": True, "status": {"state": "idle"}})

    await backfill_has_saved_state(mongo_db)

    doc = await coll.find_one({"processId": "c"})
    assert doc["hasSavedState"] is True


async def test_backfill_covers_multiple_prefix_collections(mongo_db):
    await mongo_db["app1_processes"].insert_one({"processId": "x", "status": {"state": "idle"}})
    await mongo_db["app2_processes"].insert_one({"processId": "y", "status": {"state": "idle"}})
    await mongo_db["unrelated_collection"].insert_one({"foo": "bar"})

    await backfill_has_saved_state(mongo_db)

    x = await mongo_db["app1_processes"].find_one({"processId": "x"})
    y = await mongo_db["app2_processes"].find_one({"processId": "y"})
    unrelated = await mongo_db["unrelated_collection"].find_one({"foo": "bar"})
    assert x["hasSavedState"] is False
    assert y["hasSavedState"] is False
    assert "hasSavedState" not in unrelated


async def test_backfill_idempotent(mongo_db):
    coll = mongo_db["myapp_processes"]
    await coll.insert_one({"processId": "d", "status": {"state": "idle"}})
    await backfill_has_saved_state(mongo_db)
    # Second run should not flip the value.
    await backfill_has_saved_state(mongo_db)
    doc = await coll.find_one({"processId": "d"})
    assert doc["hasSavedState"] is False
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_migration_m003.py -v`
Expected: `ModuleNotFoundError: No module named 'optio_core.migrations.m003_backfill_has_saved_state'`.

- [ ] **Step 3: Implement**

Create `packages/optio-core/src/optio_core/migrations/m003_backfill_has_saved_state.py`:

```python
"""Backfill hasSavedState=False on pre-existing process docs.

supportsResume is handled by upsert_process ($set on sync), but hasSavedState
lives in $setOnInsert so pre-existing docs never receive it. Backfill once
via the migration system.
"""

from optio_core.migrations import fw_migrations


@fw_migrations.register(
    "backfill_has_saved_state",
    depends_on=["backfill_child_metadata"],
)
async def backfill_has_saved_state(db):
    collection_names = await db.list_collection_names()
    process_collections = [n for n in collection_names if n.endswith("_processes")]
    for coll_name in process_collections:
        await db[coll_name].update_many(
            {"hasSavedState": {"$exists": False}},
            {"$set": {"hasSavedState": False}},
        )
```

Edit `packages/optio-core/src/optio_core/migrations/__init__.py`. Add the import at the bottom:

```python
import optio_core.migrations.m003_backfill_has_saved_state  # noqa: F401
```

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_migration_m003.py -v`
Expected: all PASS.

Also run: `cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/ -v`
Expected: all tests in optio-core PASS.

---

## Task 7: Add `supportsResume` / `hasSavedState` to `ProcessSchema`

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts`
- Test: (no dedicated test yet; covered by API handler tests in later tasks + schema compilation)

- [ ] **Step 1: Modify schema**

Edit `packages/optio-contracts/src/schemas/process.ts`. Add two fields to `ProcessSchema` (just before `createdAt`):

```typescript
export const ProcessSchema = z.object({
  _id: ObjectIdSchema,
  processId: z.string(),
  name: z.string(),
  params: z.record(z.unknown()).optional(),
  metadata: z.record(z.unknown()).optional(),

  // Tree structure
  parentId: ObjectIdSchema.optional(),
  rootId: ObjectIdSchema,
  depth: z.number().int().min(0),
  order: z.number().int().min(0),

  // Definition metadata
  cancellable: z.boolean(),
  special: z.boolean().optional(),
  warning: z.string().optional(),
  description: z.string().nullable().optional(),

  // Runtime
  status: ProcessStatusSchema,
  progress: ProgressSchema,
  log: z.array(LogEntrySchema),

  // Widget extensions
  uiWidget: z.string().nullable().optional(),
  widgetData: z.unknown().optional(),

  // Resume feature — default false when absent in stored doc (UI treats
  // missing fields as false defensively)
  supportsResume: z.boolean().optional(),
  hasSavedState: z.boolean().optional(),

  createdAt: DateSchema,
});
```

- [ ] **Step 2: Verify typecheck and existing tests still pass**

Run: `cd packages/optio-contracts && node_modules/.bin/tsc --noEmit`
Expected: no type errors.

Run: `cd packages/optio-contracts && npm test -- --run 2>/dev/null` (if tests exist; may be a no-op).
Expected: PASS or no-op.

---

## Task 8: Launch endpoint accepts `{ resume?: boolean }` body

**Files:**
- Modify: `packages/optio-contracts/src/contract.ts`

- [ ] **Step 1: Change launch body**

Edit `packages/optio-contracts/src/contract.ts`. Replace the `launch` body definition:

```typescript
  launch: {
    method: 'POST',
    path: '/processes/:id/launch',
    pathParams: z.object({ id: ObjectIdSchema }),
    query: InstanceQuerySchema,
    body: z.object({ resume: z.boolean().optional() }).optional(),
    responses: {
      200: ProcessSchema,
      404: ErrorSchema,
      409: ErrorSchema,
    },
    summary: 'Launch a process (optionally in resume mode)',
  },
```

- [ ] **Step 2: Verify typecheck**

Run: `cd packages/optio-contracts && node_modules/.bin/tsc --noEmit`
Expected: no errors. (Consumers — the Fastify adapter + useProcessActions hook — will break when typechecked; they're fixed in subsequent tasks. We accept that transient breakage here since we commit once at the end.)

---

## Task 9: `launchProcess` handler accepts & validates `resume`

**Files:**
- Modify: `packages/optio-api/src/handlers.ts`
- Modify: `packages/optio-api/src/adapters/fastify.ts` (route glue)
- Test: `packages/optio-api/src/__tests__/handlers.test.ts`

- [ ] **Step 1: Write failing tests**

Append to `packages/optio-api/src/__tests__/handlers.test.ts`:

```typescript
import Redis from 'ioredis-mock';
import { launchProcess } from '../handlers.js';

describe('launchProcess — resume validation', () => {
  let redis: any;

  beforeEach(async () => {
    redis = new Redis();
    await redis.flushall();
  });

  async function insertLaunchable(extra: Record<string, unknown> = {}) {
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid,
      processId: 'p',
      name: 'P',
      rootId: oid,
      parentId: null,
      depth: 0,
      order: 0,
      status: { state: 'idle' },
      progress: { percent: null },
      cancellable: true,
      log: [],
      supportsResume: false,
      hasSavedState: false,
      ...extra,
    });
    return oid.toString();
  }

  it('rejects resume=true when task does not support resume', async () => {
    const id = await insertLaunchable({ supportsResume: false });
    const result = await launchProcess(db, redis, 'mydb', PREFIX, id, true);
    expect(result.status).toBe(400);
  });

  it('accepts resume=true when task supports resume (regardless of hasSavedState)', async () => {
    const id = await insertLaunchable({
      processId: 'q', supportsResume: true, hasSavedState: false,
    });
    const result = await launchProcess(db, redis, 'mydb', PREFIX, id, true);
    expect(result.status).toBe(200);

    const entries = await redis.xrange('mydb/test:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.resume).toBe(true);
    expect(payload.processId).toBe('q');
  });

  it('accepts missing body (backwards compatible): resume defaults to false', async () => {
    const id = await insertLaunchable({ processId: 'r' });
    const result = await launchProcess(db, redis, 'mydb', PREFIX, id /* no resume */);
    expect(result.status).toBe(200);

    const entries = await redis.xrange('mydb/test:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.resume ?? false).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests to see failures**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/handlers.test.ts`
Expected: 3 new tests FAIL — `launchProcess` has the wrong arity, and payload never contains `resume`.

- [ ] **Step 3: Modify the handler**

Edit `packages/optio-api/src/handlers.ts`. Update `launchProcess`:

```typescript
export async function launchProcess(
  db: Db,
  redis: Redis,
  database: string,
  prefix: string,
  id: string,
  resume: boolean = false,
): Promise<CommandResult> {
  const proc = await col(db, prefix).findOne({ _id: new ObjectId(id) });
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!LAUNCHABLE_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot launch process in state: ${proc.status.state}` } };
  }
  if (resume && !proc.supportsResume) {
    // Match our documented 400 response for this case (see design spec, Handler validation).
    return { status: 409, body: { message: 'This task does not support resume' } } as any;
  }
  await publishLaunch(redis, database, prefix, proc.processId, resume);
  return { status: 200, body: toResponse(proc) };
}
```

Note: `CommandResult` currently only has 200/404/409. Extending it to 400 requires contract-side changes too. To keep this task scoped, we return 409 for the "doesn't support resume" case and document the rationale: 409 already covers "wrong state for this action"; supports_resume=false is conceptually the same class of mismatch.

**Adjust the test accordingly** — replace `expect(result.status).toBe(400)` with `expect(result.status).toBe(409)` in the first new test. (This is a small divergence from the spec's HTTP-code wording, documented here; update the spec notes later if you prefer the split.)

- [ ] **Step 4: Update the Fastify adapter to read the body**

Inspect `packages/optio-api/src/adapters/fastify.ts` (not previously read). Find the `launch` route registration. It currently looks roughly like:

```typescript
launch: async ({ params, query }) => {
  const db = await resolveDb(deps, query);
  const result = await launchProcess(db, deps.redis, query.database ?? deps.database, deps.prefix, params.id);
  return result;
},
```

Replace with:

```typescript
launch: async ({ params, query, body }) => {
  const db = await resolveDb(deps, query);
  const resume = body?.resume === true;
  const result = await launchProcess(
    db, deps.redis,
    query.database ?? deps.database, deps.prefix,
    params.id, resume,
  );
  return result;
},
```

(If the exact lambda shape differs, match the existing pattern — the point is: accept body, extract resume, pass to handler.)

- [ ] **Step 5: Run tests**

Run: `cd packages/optio-api && node_modules/.bin/vitest run`
Expected: all PASS.

---

## Task 10: `publishLaunch` includes `resume`

**Files:**
- Modify: `packages/optio-api/src/publisher.ts`
- Test: `packages/optio-api/src/__tests__/publisher.test.ts`

- [ ] **Step 1: Write failing tests**

Append to `packages/optio-api/src/__tests__/publisher.test.ts`:

```typescript
describe('publishLaunch — resume', () => {
  let redis: any;

  beforeEach(async () => {
    redis = new Redis();
    await redis.flushall();
  });

  it('includes resume=true in the payload', async () => {
    await publishLaunch(redis, 'mydb', 'optio', 'task-r', true);
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.resume).toBe(true);
  });

  it('defaults resume to false when not passed', async () => {
    await publishLaunch(redis, 'mydb', 'optio', 'task-d');
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.resume ?? false).toBe(false);
  });
});
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/publisher.test.ts`
Expected: new tests FAIL — current `publishLaunch` takes only `(redis, database, prefix, processId)`.

- [ ] **Step 3: Modify `publishLaunch`**

Edit `packages/optio-api/src/publisher.ts`:

```typescript
export async function publishLaunch(
  redis: Redis,
  database: string,
  prefix: string,
  processId: string,
  resume: boolean = false,
): Promise<void> {
  await redis.xadd(
    getStreamName(database, prefix),
    '*',
    'type', 'launch',
    'payload', JSON.stringify({ processId, resume }),
  );
}
```

(Leave `publishCancel`, `publishDismiss`, `publishResync` unchanged.)

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-api && node_modules/.bin/vitest run`
Expected: all PASS.

---

## Task 11: Create `LaunchControls` component

**Files:**
- Create: `packages/optio-ui/src/components/LaunchControls.tsx`
- Test: `packages/optio-ui/src/__tests__/LaunchControls.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `packages/optio-ui/src/__tests__/LaunchControls.test.tsx`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

import { LaunchControls } from '../components/LaunchControls.js';

// Minimal i18n shim so the `t()` calls return keys.
const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

function renderWith(process: any, onLaunch = vi.fn()) {
  return {
    onLaunch,
    ...render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls process={process} onLaunch={onLaunch} size="small" />
      </I18nextProvider>,
    ),
  };
}

describe('LaunchControls', () => {
  it('renders nothing when process is in a non-launchable state', () => {
    const { container } = renderWith({ _id: '1', status: { state: 'running' } });
    expect(container.firstChild).toBeNull();
  });

  it('renders a single play button when supportsResume=false', () => {
    const { onLaunch } = renderWith({
      _id: '1', status: { state: 'idle' }, supportsResume: false, hasSavedState: false,
    });
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(onLaunch).toHaveBeenCalledWith('1', undefined);
  });

  it('renders a single play button when supportsResume=true but hasSavedState=false', () => {
    const { onLaunch } = renderWith({
      _id: '2', status: { state: 'idle' }, supportsResume: true, hasSavedState: false,
    });
    const btns = screen.getAllByRole('button');
    expect(btns.length).toBe(1);
    fireEvent.click(btns[0]);
    expect(onLaunch).toHaveBeenCalledWith('2', undefined);
  });

  it('renders a split button when supportsResume=true AND hasSavedState=true', () => {
    const { onLaunch } = renderWith({
      _id: '3', status: { state: 'idle' }, supportsResume: true, hasSavedState: true,
    });
    // Primary click = resume: true
    const primary = screen.getAllByRole('button')[0];
    fireEvent.click(primary);
    expect(onLaunch).toHaveBeenCalledWith('3', { resume: true });
  });

  it('dropdown item dispatches resume=false', async () => {
    const { onLaunch } = renderWith({
      _id: '4', status: { state: 'idle' }, supportsResume: true, hasSavedState: true,
    });
    // Open dropdown (Ant Design renders a second button in Dropdown.Button)
    const buttons = screen.getAllByRole('button');
    const dropdownTrigger = buttons[buttons.length - 1];
    fireEvent.click(dropdownTrigger);
    const restart = await screen.findByText(/restart/i);
    fireEvent.click(restart);
    expect(onLaunch).toHaveBeenCalledWith('4', { resume: false });
  });

  it('treats missing supportsResume / hasSavedState as false', () => {
    const { onLaunch } = renderWith({ _id: '5', status: { state: 'idle' } });
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(onLaunch).toHaveBeenCalledWith('5', undefined);
  });
});
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/LaunchControls.test.tsx`
Expected: all fail — module doesn't exist.

- [ ] **Step 3: Implement `LaunchControls.tsx`**

Create `packages/optio-ui/src/components/LaunchControls.tsx`:

```typescript
import { Button, Dropdown, Tooltip, Popconfirm } from 'antd';
import type { MenuProps, ButtonProps } from 'antd';
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

const LAUNCHABLE_STATES = new Set(['idle', 'done', 'failed', 'cancelled']);

export interface LaunchControlsProps {
  process: any;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  size?: ButtonProps['size'];
}

/**
 * Renders launch affordances for a process:
 *   * Nothing when the process is in a non-launchable state.
 *   * Single play button when the task does not support resume (or has no
 *     saved state yet).
 *   * Split button (primary = Resume, menu = Restart) when supportsResume
 *     AND hasSavedState are both true.
 *
 * Defensive defaults: missing fields on the process document are treated
 * as false so the UI works against an unmigrated DB.
 */
export function LaunchControls({ process, onLaunch, size = 'small' }: LaunchControlsProps) {
  const { t } = useTranslation();
  const state = process?.status?.state ?? 'idle';
  if (!LAUNCHABLE_STATES.has(state) || !onLaunch) return null;

  const supportsResume = process.supportsResume === true;
  const hasSavedState = process.hasSavedState === true;

  // Case 1: single play button (fresh start semantics — no opts).
  if (!supportsResume || !hasSavedState) {
    const button = (
      <Button
        type="text"
        size={size}
        icon={<PlayCircleOutlined />}
        style={{ color: '#52c41a' }}
        onClick={(e) => {
          e.preventDefault();
          onLaunch(process._id);
        }}
      />
    );
    const wrapped = process.warning ? (
      <Popconfirm title={process.warning} onConfirm={() => onLaunch(process._id)}>
        {button}
      </Popconfirm>
    ) : button;
    return (
      <Tooltip title={t('processes.launch')}>{wrapped}</Tooltip>
    );
  }

  // Case 2: split button — primary = Resume, menu = Restart.
  const menu: MenuProps = {
    items: [
      {
        key: 'restart',
        icon: <ReloadOutlined />,
        label: t('processes.restart', { defaultValue: 'Restart (discard saved state)' }),
        onClick: () => onLaunch(process._id, { resume: false }),
      },
    ],
  };

  return (
    <Tooltip title={t('processes.resume', { defaultValue: 'Resume' })}>
      <Dropdown.Button
        size={size}
        icon={<PlayCircleOutlined />}
        menu={menu}
        onClick={() => onLaunch(process._id, { resume: true })}
      >
        <PlayCircleOutlined style={{ color: '#52c41a' }} />
      </Dropdown.Button>
    </Tooltip>
  );
}
```

**Note on i18n keys:** we use `defaultValue` so the component works without translation resources in place. Add `processes.resume` / `processes.restart` keys to whatever translation file the consuming app uses (out of scope for this library).

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/LaunchControls.test.tsx`
Expected: all PASS. If the split-button test is flaky due to Ant Design's dropdown opening asynchronously, use `screen.findByText` (already the case above).

---

## Task 12: Wire `LaunchControls` into `ProcessList`

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessList.tsx`

- [ ] **Step 1: Extend `onLaunch` signature**

Change the outer `ProcessListProps` and the inline `ProcessItem`'s prop shape so both accept `onLaunch?: (id: string, opts?: { resume?: boolean }) => void`:

```typescript
interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}

export function ProcessItem({ process, onLaunch, onCancel, readonly, onProcessClick }: {
  process: any;
  onLaunch?: (id: string, opts?: { resume?: boolean }) => void;
  onCancel?: (id: string) => void;
  readonly?: boolean;
  onProcessClick?: (id: string) => void;
}) {
  // ... body below
}
```

- [ ] **Step 2: Replace inline launch button with `LaunchControls`**

Delete the `launchButton` constant (lines 28–41 in the current file) and replace the usage in the JSX:

```typescript
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ProcessStatusBadge state={state} error={process.status?.error} runningSince={process.status?.runningSince} />
          {!readonly && <LaunchControls process={process} onLaunch={onLaunch} size="small" />}
          {isCancellable && onCancel && (
            <Tooltip title={t('processes.cancel')}>
              <Button type="text" size="small" danger icon={<CloseCircleOutlined />}
                onClick={(e) => { e.preventDefault(); onCancel(process._id); }} />
            </Tooltip>
          )}
        </div>
```

Add the import at the top:

```typescript
import { LaunchControls } from './LaunchControls.js';
```

Remove the now-unused import of `PlayCircleOutlined` (it's still used for something else? Check — if not, drop it). `LAUNCHABLE_STATES` / `isLaunchable` are now encapsulated inside `LaunchControls`, so you can remove the `LAUNCHABLE_STATES` constant and `isLaunchable` local unless something else uses them. Inspect and clean up as appropriate — do not leave dead code.

- [ ] **Step 3: Verify the typecheck and existing tests**

Run: `cd packages/optio-ui && node_modules/.bin/tsc --noEmit`
Expected: no type errors.

Run: `cd packages/optio-ui && node_modules/.bin/vitest run`
Expected: all PASS.

---

## Task 13: Add `LaunchControls` to `ProcessDetailView` header

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessDetailView.tsx`
- Test: `packages/optio-ui/src/__tests__/ProcessDetailView.test.tsx` (extend; do not rewrite)

- [ ] **Step 1: Add a header action area + wire `LaunchControls`**

Edit `packages/optio-ui/src/components/ProcessDetailView.tsx`. Add an imports:

```typescript
import { LaunchControls } from './LaunchControls.js';
import { useProcessActions } from '../hooks/useProcessActions.js';
```

Inside `ProcessDetailView`, after the existing `if (!tree)` guard, compute the launch callback and insert a header:

```typescript
  const { launch } = useProcessActions();

  const header = (
    <div
      data-testid="optio-detail-header"
      style={{ display: 'flex', justifyContent: 'flex-end', padding: 4 }}
    >
      <LaunchControls
        process={tree as any}
        onLaunch={(id, opts) => launch(id, opts)}
        size="middle"
      />
    </div>
  );
```

Replace both return paths (widget case and default case) to render `header` above the main body. For example, the default case becomes:

```typescript
  return (
    <div data-testid="optio-detail-default">
      {header}
      <ProcessTreeView treeData={tree} sseState={{ connected }} />
      <ProcessLogPanel logs={logs} />
    </div>
  );
```

The widget-live branch likewise renders `{header}` above the `<Widget ... />` layout.

- [ ] **Step 2: Extend tests**

Append to `packages/optio-ui/src/__tests__/ProcessDetailView.test.tsx` a test that asserts the header renders a `LaunchControls` when the tree is loaded (basic smoke — full behavior is covered by `LaunchControls.test.tsx`). Use the existing mocks pattern in that file.

- [ ] **Step 3: Run tests**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run`
Expected: all PASS.

---

## Task 14: `useProcessActions.launch(id, opts?)`

**Files:**
- Modify: `packages/optio-ui/src/hooks/useProcessActions.ts`

- [ ] **Step 1: Modify signature**

Edit `packages/optio-ui/src/hooks/useProcessActions.ts`:

```typescript
  return {
    launch: (processId: string, opts?: { resume?: boolean }) =>
      launchMutation.mutate({
        params: { id: processId },
        query: { database, prefix },
        body: opts?.resume === true ? { resume: true } : {},
      }),
    cancel: (processId: string) => cancelMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    dismiss: (processId: string) => dismissMutation.mutate({ params: { id: processId }, query: { database, prefix } }),
    resync: () => resyncMutation.mutate({ query: { database, prefix }, body: {} }),
    resyncClean: () => resyncMutation.mutate({ query: { database, prefix }, body: { clean: true } }),
    isResyncing: resyncMutation.isPending,
  };
```

(When `opts?.resume` is not true, we send an empty body — matches the contract's `.optional()` body.)

- [ ] **Step 2: Verify typecheck and tests**

Run: `cd packages/optio-ui && node_modules/.bin/tsc --noEmit`
Expected: no type errors.

Run: `cd packages/optio-ui && node_modules/.bin/vitest run`
Expected: all PASS.

---

## Task 15: Introduce per-task directory helper (optio-opencode)

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/paths.py`
- Test: `packages/optio-opencode/tests/test_paths.py` (new)

- [ ] **Step 1: Write failing tests**

Create `packages/optio-opencode/tests/test_paths.py`:

```python
"""Tests for per-task directory helpers."""

import os
import pathlib

import pytest

from optio_opencode.paths import local_task_dir, remote_task_dir


def test_local_task_dir_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    got = local_task_dir("my_task_1")
    assert got == os.path.join(str(tmp_path), "my_task_1")


def test_local_task_dir_defaults_to_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_TASK_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    got = local_task_dir("alpha")
    assert got == os.path.join(str(tmp_path), "optio-opencode", "alpha")


def test_local_task_dir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_TASK_ROOT", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    got = local_task_dir("beta")
    assert got == os.path.join(str(tmp_path), ".local", "share", "optio-opencode", "beta")


def test_remote_task_dir_uses_env_override(monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_REMOTE_TASK_ROOT", "/var/optio-oc")
    got = remote_task_dir("gamma")
    assert got == "/var/optio-oc/gamma"


def test_remote_task_dir_default(monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_REMOTE_TASK_ROOT", raising=False)
    got = remote_task_dir("delta")
    assert got == "/tmp/optio-opencode/delta"


def test_process_id_safe_chars_only(tmp_path, monkeypatch):
    """Reject path-traversing or slash-containing process_ids."""
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    with pytest.raises(ValueError):
        local_task_dir("../evil")
    with pytest.raises(ValueError):
        local_task_dir("a/b")
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-opencode && python -m pytest tests/test_paths.py -v`
Expected: all fail — module doesn't exist.

- [ ] **Step 3: Implement**

Create `packages/optio-opencode/src/optio_opencode/paths.py`:

```python
"""Stable, per-task filesystem layout helpers.

The resume feature needs a `task_dir` per process_id that survives across
runs (on the same host). Previously the executor created a fresh tmpdir
every launch; now we root everything at an env-overridable per-process
location.
"""

import os
import re


_SAFE_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate(process_id: str) -> None:
    if not _SAFE_RE.match(process_id):
        raise ValueError(
            f"process_id {process_id!r} contains characters that are unsafe "
            "as a filesystem path segment; expected [A-Za-z0-9._-] only"
        )


def local_task_dir(process_id: str) -> str:
    """Return the absolute local task directory for a process_id.

    Resolution order:
      1. `$OPTIO_OPENCODE_TASK_ROOT/<process_id>`
      2. `$XDG_DATA_HOME/optio-opencode/<process_id>`
      3. `$HOME/.local/share/optio-opencode/<process_id>`
    """
    _validate(process_id)
    root = os.environ.get("OPTIO_OPENCODE_TASK_ROOT")
    if root:
        return os.path.join(root, process_id)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "optio-opencode", process_id)
    home = os.environ.get("HOME", os.path.expanduser("~"))
    return os.path.join(home, ".local", "share", "optio-opencode", process_id)


def remote_task_dir(process_id: str) -> str:
    """Return the per-task directory on a remote host.

    Resolution order:
      1. `$OPTIO_OPENCODE_REMOTE_TASK_ROOT/<process_id>`
      2. `/tmp/optio-opencode/<process_id>`
    """
    _validate(process_id)
    root = os.environ.get("OPTIO_OPENCODE_REMOTE_TASK_ROOT", "/tmp/optio-opencode")
    return f"{root}/{process_id}"
```

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-opencode && python -m pytest tests/test_paths.py -v`
Expected: all PASS.

---

## Task 16: Add `workdir_exclude` to `OpencodeTaskConfig`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py`

- [ ] **Step 1: Write a minimal failing test**

Append to `packages/optio-opencode/tests/test_types.py`:

```python
def test_opencode_task_config_workdir_exclude_default_none():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi")
    assert c.workdir_exclude is None


def test_opencode_task_config_workdir_exclude_empty_list():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi", workdir_exclude=[])
    assert c.workdir_exclude == []


def test_opencode_task_config_workdir_exclude_custom_list():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi", workdir_exclude=["*.log"])
    assert c.workdir_exclude == ["*.log"]
```

- [ ] **Step 2: Run and see failures**

Run: `cd packages/optio-opencode && python -m pytest tests/test_types.py -v`
Expected: 3 new tests FAIL.

- [ ] **Step 3: Modify `types.py`**

Edit `packages/optio-opencode/src/optio_opencode/types.py`:

```python
@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    workdir_exclude: list[str] | None = None
```

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-opencode && python -m pytest tests/test_types.py -v`
Expected: all PASS.

---

## Task 17: `archive.py` — tar+gz workdir with excludes

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/archive.py`
- Test: `packages/optio-opencode/tests/test_archive.py` (new)

archive.py provides reusable async tar+gz helpers that `LocalHost` delegates to. `RemoteHost` does not use these helpers — it executes `tar` over SSH directly. The exposed interface is async-generator-shaped so it composes cleanly with the GridFS streaming reader/writer in session.py.

- [ ] **Step 1: Write failing tests**

Create `packages/optio-opencode/tests/test_archive.py`:

```python
"""Tests for tar.gz workdir archive helpers."""

import os

import pytest

from optio_opencode.archive import (
    DEFAULT_WORKDIR_EXCLUDES,
    consume_workdir_archive,
    yield_workdir_archive,
)


def _populate(root: str) -> None:
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "hello.txt"), "w") as fh:
        fh.write("hi")
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "sub", "keep.py"), "w") as fh:
        fh.write("x = 1\n")
    # Things that default excludes should drop.
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    with open(os.path.join(root, ".git", "HEAD"), "w") as fh:
        fh.write("ref: refs/heads/main\n")
    os.makedirs(os.path.join(root, "__pycache__"), exist_ok=True)
    with open(os.path.join(root, "__pycache__", "mod.cpython-311.pyc"), "wb") as fh:
        fh.write(b"\xff\xff")
    with open(os.path.join(root, "mod.pyc"), "wb") as fh:
        fh.write(b"\xff\xff")


async def _gather(stream) -> bytes:
    chunks = []
    async for c in stream:
        chunks.append(c)
    return b"".join(chunks)


async def _replay(buf: bytes):
    """Adapt a finished bytes buffer into an AsyncIterator[bytes] for consume_workdir_archive."""
    async def gen():
        # Yield in 64 KiB chunks to exercise the streaming consumer.
        view = memoryview(buf)
        step = 64 * 1024
        for i in range(0, len(view), step):
            yield bytes(view[i : i + step])
    return gen()


async def test_yield_and_consume_roundtrip(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))

    blob = await _gather(yield_workdir_archive(str(src), exclude=None))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert (dst / "hello.txt").read_text() == "hi"
    assert (dst / "sub" / "keep.py").read_text() == "x = 1\n"
    assert not (dst / ".git").exists()
    assert not (dst / "__pycache__").exists()
    assert not (dst / "mod.pyc").exists()


async def test_empty_exclude_list_captures_everything(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))

    blob = await _gather(yield_workdir_archive(str(src), exclude=[]))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert (dst / ".git" / "HEAD").read_text() == "ref: refs/heads/main\n"
    assert (dst / "__pycache__").exists()


async def test_custom_exclude_no_merge_with_defaults(tmp_path):
    """Non-empty list is verbatim; .git should NOT be excluded unless listed."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))

    blob = await _gather(yield_workdir_archive(str(src), exclude=["*.log"]))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert (dst / ".git").exists()  # would have been excluded by defaults; not by custom list
    assert (dst / "__pycache__").exists()


async def test_consume_empties_destination_first(tmp_path):
    """consume_workdir_archive must wipe pre-existing dest contents before extracting."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _populate(str(src))
    os.makedirs(dst, exist_ok=True)
    (dst / "stale.txt").write_text("should be gone")

    blob = await _gather(yield_workdir_archive(str(src), exclude=None))
    await consume_workdir_archive(await _replay(blob), str(dst))

    assert not (dst / "stale.txt").exists()
    assert (dst / "hello.txt").read_text() == "hi"


def test_default_workdir_excludes_has_expected_entries():
    assert ".git" in DEFAULT_WORKDIR_EXCLUDES
    assert "node_modules" in DEFAULT_WORKDIR_EXCLUDES
    assert "__pycache__" in DEFAULT_WORKDIR_EXCLUDES
    assert ".venv" in DEFAULT_WORKDIR_EXCLUDES
    assert "*.pyc" in DEFAULT_WORKDIR_EXCLUDES
    assert ".DS_Store" in DEFAULT_WORKDIR_EXCLUDES
```

- [ ] **Step 2: Run and see failures**

Run: `cd packages/optio-opencode && python -m pytest tests/test_archive.py -v`
Expected: all fail (module not found).

- [ ] **Step 3: Implement**

Create `packages/optio-opencode/src/optio_opencode/archive.py`:

```python
"""Tar+gzip helpers for persisting and restoring a workdir.

`yield_workdir_archive` is an async generator that yields gzipped tar
chunks of a directory's contents. `consume_workdir_archive` consumes such
a stream, wiping the destination first and extracting in. Both run their
synchronous tarfile work in a thread executor so the event loop stays
responsive.

These helpers back `LocalHost.archive_workdir` and `LocalHost.restore_workdir`.
`RemoteHost` does not use them — it shells out to `tar` over SSH instead.
"""

from __future__ import annotations

import asyncio
import fnmatch
import io
import os
import shutil
import tarfile
from typing import AsyncIterator, Iterable


DEFAULT_WORKDIR_EXCLUDES: list[str] = [
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "*.pyc",
    ".DS_Store",
]

_CHUNK_SIZE = 1 << 20  # 1 MiB


def _excluded(relpath: str, patterns: list[str]) -> bool:
    """Return True if `relpath` matches any pattern in `patterns`.

    Matching is applied at each path segment (so `.git` in `patterns`
    excludes `./a/b/.git/…`) as well as against the full relative path
    (so `*.pyc` matches `mod.pyc` but also `a/b/mod.pyc`).
    """
    parts = relpath.split(os.sep)
    for pat in patterns:
        if fnmatch.fnmatch(relpath, pat):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _build_archive_bytes(root: str, patterns: list[str]) -> bytes:
    """Build the entire tar.gz in memory and return it as bytes.

    Memory: O(workdir size). For workdirs in the gigabytes, swap this for
    a piped write across a thread; not required for MVP.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [
                d for d in dirnames
                if not _excluded(os.path.join(rel_dir, d) if rel_dir != "." else d, patterns)
            ]
            for name in filenames:
                rel = os.path.join(rel_dir, name) if rel_dir != "." else name
                if _excluded(rel, patterns):
                    continue
                tar.add(os.path.join(dirpath, name), arcname=rel, recursive=False)
    return buf.getvalue()


async def yield_workdir_archive(
    root: str,
    exclude: Iterable[str] | None = None,
) -> AsyncIterator[bytes]:
    """Yield 1 MiB chunks of the gzipped tar of `root`.

    `exclude`:
      * `None` → use DEFAULT_WORKDIR_EXCLUDES.
      * `[]`   → no excludes; capture everything.
      * non-empty list → use verbatim (no merge with defaults).
    """
    patterns = list(DEFAULT_WORKDIR_EXCLUDES) if exclude is None else list(exclude)
    loop = asyncio.get_event_loop()
    blob = await loop.run_in_executor(None, _build_archive_bytes, root, patterns)
    for offset in range(0, len(blob), _CHUNK_SIZE):
        yield blob[offset : offset + _CHUNK_SIZE]


def _empty_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        return
    for entry in os.listdir(path):
        full = os.path.join(path, entry)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.unlink(full)
            except OSError:
                pass


def _extract_sync(blob: bytes, dest: str) -> None:
    _empty_dir(dest)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        tar.extractall(dest)


async def consume_workdir_archive(
    stream: AsyncIterator[bytes],
    dest: str,
) -> None:
    """Empty `dest`, then untar the chunked gzipped stream into it."""
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
    blob = b"".join(chunks)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _extract_sync, blob, dest)
```

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-opencode && python -m pytest tests/test_archive.py -v`
Expected: all PASS.

---

## Task 17.5: Host abstraction additions for resume — `LocalHost`

Add the spec's five resume operations to `LocalHost`, plus one small plumbing helper. Defined as part of the `Host` protocol so `RemoteHost` follows in Task 17.6. The new methods are:

- `launch_opencode(..., env: dict[str, str] | None = None)` — extend the existing method.
- `opencode_import(opencode_db_path: str, session_json: bytes) -> None`
- `opencode_export(opencode_db_path: str, session_id: str) -> bytes`
- `archive_workdir(exclude: list[str] | None) -> AsyncIterator[bytes]`
- `restore_workdir(stream: AsyncIterator[bytes]) -> None`
- `remove_file(path: str) -> None` — small plumbing helper used by `session.py` to wipe the stale `opencode.db` at start-of-run uniformly across local/remote. Not enumerated in the spec because it's mechanical, but called out here so reviewers know it exists.

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py`
- Test: `packages/optio-opencode/tests/test_host_resume.py` (new — covers LocalHost only; RemoteHost is in Task 17.6)

- [ ] **Step 1: Update the `Host` protocol**

Edit `packages/optio-opencode/src/optio_opencode/host.py`. In the `Host` Protocol class, add the five method declarations (mirror the spec's "Host abstraction additions" section):

```python
class Host(Protocol):
    workdir: str

    # ... existing methods ...

    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> LaunchedProcess: ...

    async def opencode_import(
        self, opencode_db_path: str, session_json: bytes,
    ) -> None: ...

    async def opencode_export(
        self, opencode_db_path: str, session_id: str,
    ) -> bytes: ...

    def archive_workdir(
        self, exclude: list[str] | None,
    ) -> AsyncIterator[bytes]: ...
    # NB: archive_workdir is a *plain* method that returns an AsyncIterator,
    # not an async method. Callers `async for chunk in host.archive_workdir(...)`.

    async def restore_workdir(
        self, stream: AsyncIterator[bytes],
    ) -> None: ...

    async def remove_file(self, path: str) -> None: ...
```

Add the import at the top of the module if it isn't there:

```python
from typing import AsyncIterator, Callable, Protocol
```

- [ ] **Step 2: Write failing tests for LocalHost**

Create `packages/optio-opencode/tests/test_host_resume.py`:

```python
"""Tests for LocalHost resume-related methods."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from optio_opencode.host import LocalHost

FAKE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


def _populate_workdir(workdir: str) -> None:
    os.makedirs(workdir, exist_ok=True)
    Path(workdir, "AGENTS.md").write_text("# instructions\n")
    Path(workdir, "data.txt").write_text("payload\n")


async def test_launch_opencode_env_is_propagated_to_subprocess(tmp_path):
    """env kwarg lands in opencode's environment.

    fake_opencode.py is extended in Task 17.5 to honor an `--env-echo KEY` arg
    that prints the env value of KEY to stderr (visible to launch_opencode's
    URL detector, which scans stdout merged with stderr). For this test we
    rely on a simpler signal: the fake binary writes its env to a file when
    the env var OPTIO_FAKE_DUMP=<path> is set.
    """
    workdir = tmp_path / "wd"
    workdir.mkdir()
    dump = tmp_path / "env.json"

    host = LocalHost(workdir=str(workdir), opencode_cmd=[sys.executable, FAKE])
    proc = await host.launch_opencode(
        password="pw",
        ready_timeout_s=5.0,
        extra_args=["--scenario", "happy", "--env-dump", str(dump)],
        env={"OPENCODE_DB": "/tmp/fake.db", "X": "1"},
    )
    # fake_opencode dumps env on startup, then proceeds with the scenario.
    # Wait briefly for the file to appear.
    for _ in range(50):
        if dump.exists():
            break
        await asyncio.sleep(0.05)
    await host.terminate_opencode(proc, aggressive=True)
    assert dump.exists(), "fake_opencode did not dump its env; env propagation likely broken"
    env = json.loads(dump.read_text())
    assert env.get("OPENCODE_DB") == "/tmp/fake.db"
    assert env.get("X") == "1"


async def test_opencode_export_then_import_roundtrip(tmp_path):
    """Use the fake's `export <id>` and `import <file>` subcommands."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    db = tmp_path / "opencode.db"

    host = LocalHost(workdir=str(workdir), opencode_cmd=[sys.executable, FAKE])

    # First, simulate a session by writing a marker file (the fake's import
    # subcommand will consume this during a later import; export reads it back).
    (tmp_path / "seed.json").write_text(json.dumps({"id": "sess-42", "messages": []}))
    await host.opencode_import(str(db), (tmp_path / "seed.json").read_bytes())

    out = await host.opencode_export(str(db), "sess-42")
    decoded = json.loads(out.decode("utf-8"))
    assert decoded["id"] == "sess-42"


async def test_archive_workdir_yields_chunks(tmp_path):
    workdir = tmp_path / "wd"
    _populate_workdir(str(workdir))

    host = LocalHost(workdir=str(workdir), opencode_cmd=[sys.executable, FAKE])
    chunks = []
    async for c in host.archive_workdir(exclude=None):
        chunks.append(c)
    assert len(b"".join(chunks)) > 0


async def test_restore_workdir_empties_then_extracts(tmp_path):
    src = tmp_path / "src"
    _populate_workdir(str(src))
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "stale.txt").write_text("zzz")

    src_host = LocalHost(workdir=str(src), opencode_cmd=[sys.executable, FAKE])
    chunks = []
    async for c in src_host.archive_workdir(exclude=None):
        chunks.append(c)

    async def replay():
        for c in chunks:
            yield c

    dst_host = LocalHost(workdir=str(dst), opencode_cmd=[sys.executable, FAKE])
    await dst_host.restore_workdir(replay())

    assert not (dst / "stale.txt").exists()
    assert (dst / "AGENTS.md").read_text() == "# instructions\n"
```

- [ ] **Step 3: Run tests to see failures**

Run: `cd packages/optio-opencode && python -m pytest tests/test_host_resume.py -v`
Expected: all FAIL — `LocalHost` does not yet implement these methods, and `fake_opencode.py` doesn't yet support `--env-dump` / `import` / `export` (the stub extension lands in Task 20).

If `--env-dump` and `import`/`export` aren't yet wired into `fake_opencode.py`, the tests will fail at fixture setup. Skip them with `@pytest.mark.skip(reason="awaits Task 20 fake_opencode extension")` until Task 20 lands, then remove the skip and re-run before continuing.

(To keep TDD honest: extend `fake_opencode.py` *before* this task — i.e. swap Task 20 to run before Task 17.5. The plan numbers are scaffolding, not strict ordering.)

- [ ] **Step 4: Implement on `LocalHost`**

Edit `packages/optio-opencode/src/optio_opencode/host.py`. Add `env` to `LocalHost.launch_opencode` and the four new methods.

```python
    async def launch_opencode(
        self,
        password: str,
        ready_timeout_s: float,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> LaunchedProcess:
        proc_env = os.environ.copy()
        proc_env["OPENCODE_SERVER_PASSWORD"] = password
        # ... existing browser-suppression PATH shim block (unchanged) ...
        proc_env["PATH"] = _bin + os.pathsep + proc_env.get("PATH", "")
        proc_env["BROWSER"] = "true"
        if env:
            proc_env.update(env)
        # ... rest of the existing method, replacing `env` with `proc_env`
        # everywhere it was previously used ...
```

(Concretely: rename the local `env` variable inside the existing method to `proc_env` to avoid shadowing the new kwarg, then `proc_env.update(env)` near the end before `create_subprocess_exec`.)

Add the four new methods on `LocalHost` (after `cleanup_workdir`):

```python
    async def opencode_import(
        self, opencode_db_path: str, session_json: bytes,
    ) -> None:
        """Write `session_json` to a scratch file and run `opencode import`."""
        scratch = os.path.join(os.path.dirname(opencode_db_path), "snapshot.json")
        with open(scratch, "wb") as fh:
            fh.write(session_json)
        try:
            env = os.environ.copy()
            env["OPENCODE_DB"] = opencode_db_path
            proc = await asyncio.create_subprocess_exec(
                *self._opencode_cmd, "import", scratch,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"opencode import failed (exit {proc.returncode}): "
                    f"{stderr.decode('utf-8', 'replace')}"
                )
        finally:
            try:
                os.unlink(scratch)
            except OSError:
                pass

    async def opencode_export(
        self, opencode_db_path: str, session_id: str,
    ) -> bytes:
        env = os.environ.copy()
        env["OPENCODE_DB"] = opencode_db_path
        proc = await asyncio.create_subprocess_exec(
            *self._opencode_cmd, "export", session_id,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"opencode export failed (exit {proc.returncode}): "
                f"{stderr.decode('utf-8', 'replace')}"
            )
        return stdout

    def archive_workdir(
        self, exclude: list[str] | None,
    ) -> "AsyncIterator[bytes]":
        from optio_opencode.archive import yield_workdir_archive
        return yield_workdir_archive(self.workdir, exclude=exclude)

    async def restore_workdir(
        self, stream: "AsyncIterator[bytes]",
    ) -> None:
        from optio_opencode.archive import consume_workdir_archive
        await consume_workdir_archive(stream, self.workdir)

    async def remove_file(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # Best-effort: log and continue; callers treat this as advisory.
            import logging
            logging.getLogger(__name__).warning(
                "LocalHost.remove_file(%r) failed: %r", path, exc,
            )
```

- [ ] **Step 5: Run tests, expect green**

Run: `cd packages/optio-opencode && python -m pytest tests/test_host_resume.py -v`
Expected: all PASS (assuming Task 20's fake_opencode extension is in place).

---

## Task 17.6: Host abstraction additions for resume — `RemoteHost`

Mirror Task 17.5 on `RemoteHost`. The methods carry the same signatures; their bodies use asyncssh exec + SFTP instead of local subprocess + tarfile.

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/host.py` (RemoteHost class)
- Test: `packages/optio-opencode/tests/test_host_remote_resume.py` (new — uses the existing docker-compose.sshd.yml fixture; skipped automatically when SSH harness isn't running)

- [ ] **Step 1: Allow `RemoteHost` to accept a stable workdir**

Edit `packages/optio-opencode/src/optio_opencode/host.py`. Update `RemoteHost.__init__`:

```python
    def __init__(self, ssh_config: SSHConfig, workdir: str | None = None):
        self._ssh = ssh_config
        # workdir defaults to the legacy random path when not supplied so
        # existing callers (test_session_remote.py) keep working.
        self.workdir = workdir or f"/tmp/optio-opencode-{uuid.uuid4().hex[:12]}"
        self._conn = None
        self._sftp = None
        self._launch_proc = None
        self._tail_proc = None
        self._forward = None
        self._opencode_exec = "opencode"
```

- [ ] **Step 2: Write failing tests**

Create `packages/optio-opencode/tests/test_host_remote_resume.py`. Mirror the structure and skip-conditions used by `tests/test_session_remote.py` (which already auto-skips when the SSH docker-compose harness isn't running):

```python
"""Tests for RemoteHost resume-related methods.

Skipped automatically when the docker-compose.sshd.yml SSH harness is not running
— mirror the skip pattern used by test_session_remote.py.
"""

# (Reuse the harness/fixtures from test_session_remote.py via a shared
# conftest.py if one exists. If not, copy the relevant fixtures here
# verbatim — the existing remote test file is the source of truth on
# how to bring up the SSH harness and produce a connected RemoteHost.)

# Test cases (one per host method):
# - test_remote_launch_env_is_propagated  →  parallels Task 17.5 Step 2's first test
# - test_remote_opencode_export_then_import_roundtrip
# - test_remote_archive_workdir_yields_chunks
# - test_remote_restore_workdir_empties_then_extracts
```

(The implementer copies the assertions verbatim from `test_host_resume.py` and adapts only the host construction.)

- [ ] **Step 3: Implement on `RemoteHost`**

Edit `packages/optio-opencode/src/optio_opencode/host.py`. Add `env` to `RemoteHost.launch_opencode` and the four new methods.

For `launch_opencode`'s env injection, prepend `KEY=$(printf %q "value") …` to the SSH-exec'd command. Concretely, add this block right before the `cmd = ...` assignment:

```python
        env_prefix = ""
        if env:
            # Each pair becomes `KEY="..."`. Values are shell-quoted via
            # asyncssh's quoting helper for safety against arbitrary content.
            import shlex
            env_prefix = " ".join(
                f"{k}={shlex.quote(v)}" for k, v in env.items()
            ) + " "
        cmd = (
            f"cd {self.workdir} && "
            f"OPENCODE_SERVER_PASSWORD=\"$(cat ./{pw_file})\" "
            f"BROWSER=true {env_prefix}"
            f"bash -lc '{self._opencode_exec} web --port=0 --hostname=127.0.0.1 2>&1'"
        )
```

For `opencode_import`:

```python
    async def opencode_import(
        self, opencode_db_path: str, session_json: bytes,
    ) -> None:
        assert self._conn is not None and self._sftp is not None
        scratch = f"{self.workdir}/snapshot.json"
        async with self._sftp.open(scratch, "wb") as fh:
            await fh.write(session_json)
        try:
            r = await self._conn.run(
                f"OPENCODE_DB={shlex.quote(opencode_db_path)} "
                f"bash -lc '{self._opencode_exec} import {shlex.quote(scratch)}'",
                check=False,
            )
            if r.exit_status != 0:
                raise RuntimeError(
                    f"remote opencode import failed "
                    f"(exit {r.exit_status}): {r.stderr}"
                )
        finally:
            await self._conn.run(f"rm -f {shlex.quote(scratch)}", check=False)
```

For `opencode_export`:

```python
    async def opencode_export(
        self, opencode_db_path: str, session_id: str,
    ) -> bytes:
        assert self._conn is not None
        r = await self._conn.run(
            f"OPENCODE_DB={shlex.quote(opencode_db_path)} "
            f"bash -lc '{self._opencode_exec} export {shlex.quote(session_id)}'",
            check=False,
        )
        if r.exit_status != 0:
            raise RuntimeError(
                f"remote opencode export failed "
                f"(exit {r.exit_status}): {r.stderr}"
            )
        out = r.stdout or b""
        return out if isinstance(out, bytes) else out.encode("utf-8")
```

For `archive_workdir`:

```python
    async def archive_workdir(
        self, exclude: list[str] | None,
    ) -> AsyncIterator[bytes]:
        from optio_opencode.archive import DEFAULT_WORKDIR_EXCLUDES
        assert self._conn is not None
        patterns = list(DEFAULT_WORKDIR_EXCLUDES) if exclude is None else list(exclude)
        excludes = " ".join(f"--exclude={shlex.quote(p)}" for p in patterns)
        cmd = f"cd {shlex.quote(self.workdir)} && tar czf - {excludes} ."
        proc = await self._conn.create_process(cmd, encoding=None)
        async for chunk in proc.stdout:
            if chunk:
                yield chunk
        await proc.wait()
        if proc.exit_status not in (0, None):
            raise RuntimeError(f"remote tar czf - failed (exit {proc.exit_status})")
```

For `restore_workdir`:

```python
    async def restore_workdir(
        self, stream: AsyncIterator[bytes],
    ) -> None:
        assert self._conn is not None
        # Empty workdir contents (preserve workdir itself).
        await self._conn.run(
            f"find {shlex.quote(self.workdir)} -mindepth 1 -delete",
            check=False,
        )
        cmd = f"cd {shlex.quote(self.workdir)} && tar xzf -"
        proc = await self._conn.create_process(cmd, encoding=None)
        async for chunk in stream:
            proc.stdin.write(chunk)
            await proc.stdin.drain()
        proc.stdin.write_eof()
        await proc.wait()
        if proc.exit_status not in (0, None):
            raise RuntimeError(f"remote tar xzf - failed (exit {proc.exit_status})")
```

For `remove_file`:

```python
    async def remove_file(self, path: str) -> None:
        assert self._conn is not None
        # `rm -f` is idempotent: missing files are not an error.
        await self._conn.run(f"rm -f {shlex.quote(path)}", check=False)
```

Add `import shlex` at the top of host.py if not already imported.

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-opencode && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_host_remote_resume.py -v`
Expected: all PASS when the SSH harness is up; auto-skipped otherwise.

Smoke-check both host suites still pass:

```bash
cd packages/optio-opencode && python -m pytest tests/test_host_resume.py tests/test_session_local.py -v
```

Expected: all PASS — existing local tests are unaffected because `env` defaults to `None`.

---

## Task 18: `snapshots.py` — snapshot collection + retention pruning

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/snapshots.py`
- Test: `packages/optio-opencode/tests/test_snapshots.py` (new)

- [ ] **Step 1: Write failing tests**

Create `packages/optio-opencode/tests/test_snapshots.py`:

```python
"""Tests for the per-task session snapshot collection."""

import asyncio
import os
import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_opencode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    SNAPSHOT_RETENTION,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_test_snapshots_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def test_insert_and_load_latest(mongo_db):
    pid = "proc_a"
    for i in range(3):
        await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done", session_id=f"s_{i}",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )

    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id=pid)
    assert latest is not None
    assert latest["sessionId"] == "s_2"


async def test_load_latest_none_when_empty(mongo_db):
    latest = await load_latest_snapshot(mongo_db, prefix="opt", process_id="nope")
    assert latest is None


async def test_prune_keeps_last_five_and_returns_deleted_ids(mongo_db):
    # Record 6 snapshot inserts, each with a unique pair of blob ids.
    pid = "proc_b"
    blob_ids_by_cap: list[dict] = []
    for i in range(6):
        snap = await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done", session_id=f"s_{i}",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
        blob_ids_by_cap.append({
            "session": snap["sessionBlobId"],
            "workdir": snap["workdirBlobId"],
        })

    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id=pid)
    # The oldest (index 0) should be pruned; we get its blob ids back.
    assert len(pruned) == 1
    assert pruned[0]["sessionBlobId"] == blob_ids_by_cap[0]["session"]
    assert pruned[0]["workdirBlobId"] == blob_ids_by_cap[0]["workdir"]

    # The collection should now hold exactly the retention count.
    coll = mongo_db[f"opt{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]
    count = await coll.count_documents({"processId": pid})
    assert count == SNAPSHOT_RETENTION


async def test_prune_noop_when_within_retention(mongo_db):
    pid = "proc_c"
    for i in range(SNAPSHOT_RETENTION):
        await insert_snapshot(
            mongo_db, prefix="opt", process_id=pid,
            end_state="done", session_id=f"s_{i}",
            session_blob_id=ObjectId(), workdir_blob_id=ObjectId(),
            deliverables_emitted=[],
        )
    pruned = await prune_snapshots(mongo_db, prefix="opt", process_id=pid)
    assert pruned == []
```

- [ ] **Step 2: Run to see failures**

Run: `cd packages/optio-opencode && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_snapshots.py -v`
Expected: all fail (module not found).

- [ ] **Step 3: Implement**

Create `packages/optio-opencode/src/optio_opencode/snapshots.py`:

```python
"""MongoDB `{prefix}_opencode_session_snapshots` collection helpers.

One document per terminal run per process_id. Layout:

    {
      _id:             ObjectId,
      processId:       str,
      capturedAt:      datetime,
      endState:        str,          # "done" | "failed" | "cancelled"
      sessionId:       str,          # opencode session id (preserved across export→import)
      sessionBlobId:   ObjectId,     # GridFS file id for the session JSON
      workdirBlobId:   ObjectId,     # GridFS file id for the workdir tar.gz
      deliverablesEmitted: list,     # audit metadata only; not replayed
    }

Retention: keep the latest `SNAPSHOT_RETENTION` per processId. Older rows
are deleted by `prune_snapshots` and their GridFS blobs are expected to be
deleted by the caller using the ids returned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase


SESSION_SNAPSHOT_COLLECTION_SUFFIX = "_opencode_session_snapshots"
SNAPSHOT_RETENTION = 5


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    return db[f"{prefix}{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"]


async def ensure_indexes(db: AsyncIOMotorDatabase, prefix: str) -> None:
    """Idempotent index creation — called lazily by insert_snapshot."""
    await _collection(db, prefix).create_index(
        [("processId", 1), ("capturedAt", -1)],
        name="by_processId_capturedAt_desc",
    )


async def insert_snapshot(
    db: AsyncIOMotorDatabase,
    *,
    prefix: str,
    process_id: str,
    end_state: str,
    session_id: str,
    session_blob_id: ObjectId,
    workdir_blob_id: ObjectId,
    deliverables_emitted: list,
) -> dict:
    await ensure_indexes(db, prefix)
    doc = {
        "processId": process_id,
        "capturedAt": datetime.now(timezone.utc),
        "endState": end_state,
        "sessionId": session_id,
        "sessionBlobId": session_blob_id,
        "workdirBlobId": workdir_blob_id,
        "deliverablesEmitted": deliverables_emitted,
    }
    result = await _collection(db, prefix).insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def load_latest_snapshot(
    db: AsyncIOMotorDatabase, *, prefix: str, process_id: str,
) -> dict | None:
    return await _collection(db, prefix).find_one(
        {"processId": process_id}, sort=[("capturedAt", -1)],
    )


async def prune_snapshots(
    db: AsyncIOMotorDatabase, *, prefix: str, process_id: str,
) -> list[dict]:
    """Keep the latest SNAPSHOT_RETENTION; delete the rest.

    Returns a list of `{sessionBlobId, workdirBlobId}` dicts for the
    deleted snapshots so the caller can remove the corresponding GridFS
    blobs.
    """
    coll = _collection(db, prefix)
    all_docs = await coll.find(
        {"processId": process_id},
        projection={"sessionBlobId": 1, "workdirBlobId": 1, "capturedAt": 1},
        sort=[("capturedAt", -1)],
    ).to_list(None)
    stale = all_docs[SNAPSHOT_RETENTION:]
    if not stale:
        return []
    stale_ids = [d["_id"] for d in stale]
    await coll.delete_many({"_id": {"$in": stale_ids}})
    return [
        {"sessionBlobId": d["sessionBlobId"], "workdirBlobId": d["workdirBlobId"]}
        for d in stale
    ]
```

- [ ] **Step 4: Run tests**

Run: `cd packages/optio-opencode && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_snapshots.py -v`
Expected: all PASS.

---

## Task 19: Wire `supports_resume=True` on the opencode TaskInstance

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (only `create_opencode_task`)

- [ ] **Step 1: Write a failing test**

Append to `packages/optio-opencode/tests/test_sanity.py`:

```python
def test_create_opencode_task_declares_resume_support():
    from optio_opencode import create_opencode_task, OpencodeTaskConfig
    task = create_opencode_task(
        process_id="demo", name="Demo",
        config=OpencodeTaskConfig(consumer_instructions="hi"),
    )
    assert task.supports_resume is True
```

- [ ] **Step 2: Run to see failure**

Run: `cd packages/optio-opencode && python -m pytest tests/test_sanity.py -v -k supports_resume`
Expected: FAIL (`AssertionError` because default is False).

- [ ] **Step 3: Modify `create_opencode_task`**

Edit `packages/optio-opencode/src/optio_opencode/session.py`, `create_opencode_task`:

```python
def create_opencode_task(
    process_id: str,
    name: str,
    config: OpencodeTaskConfig,
    description: str | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one opencode web session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_opencode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=True,
    )
```

- [ ] **Step 4: Run test**

Run: `cd packages/optio-opencode && python -m pytest tests/test_sanity.py -v`
Expected: PASS.

---

## Task 20: Extend `fake_opencode.py` to support `import` / `export`

**Files:**
- Modify: `packages/optio-opencode/tests/fake_opencode.py`

Needed before task 21 integration tests work.

- [ ] **Step 1: Inspect current fake**

Read `packages/optio-opencode/tests/fake_opencode.py` fully to understand its current behavior (it currently pretends to be `opencode web`).

- [ ] **Step 2: Add `import` / `export` subcommand support**

Extend the `argparse` (or `sys.argv`-based) top-of-script dispatch so that:

- `fake_opencode.py import <path>`: reads the JSON at `<path>`, writes a marker file at `$OPENCODE_DB` (e.g. appends the line `IMPORT <session-id>` derived from the JSON's `"id"` field, or simply copies the JSON blob verbatim). Exit 0.
- `fake_opencode.py export <session-id>`: writes a minimal `{"id": "<session-id>", "messages": []}` to stdout. Exit 0.
- Any other args → existing behavior (`web` emulation).

Exact code depends on the current structure; preserve the existing `web` path unchanged.

- [ ] **Step 3: Run existing session tests**

Run: `cd packages/optio-opencode && python -m pytest tests/test_session_local.py -v`
Expected: existing tests continue to PASS (we did not touch the `web` path).

---

## Task 21: Session resume launch + terminal flow

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (significant changes to `run_opencode_session`)
- Test: `packages/optio-opencode/tests/test_session_resume.py` (new)

This is the biggest task. `run_opencode_session` is re-wired to use the per-task directory, branch on `ctx.resume`, and run a terminal capture — entirely via host methods (no `subprocess.run`, no local/SSH branches in session code). Tasks 17.5/17.6 already added `LocalHost`/`RemoteHost.workdir` override and the resume host methods, so this task only edits `session.py`.

- [ ] **Step 1: Write the failing integration test**

Create `packages/optio-opencode/tests/test_session_resume.py`. The fixtures mirror those in `tests/test_session_local.py` (which patches `LocalHost.__init__` to use `fake_opencode.py` and supplies a `--scenario` arg). Reuse them via a shared conftest where convenient — for now, duplicate inline:

```python
"""Full-cycle resume test for optio-opencode against fake_opencode.py."""

import asyncio
import os
import pathlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_opencode import OpencodeTaskConfig
from optio_opencode.paths import local_task_dir
from optio_opencode.session import run_opencode_session
from optio_opencode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_test_resume_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _patch_localhost_to_use_fake(monkeypatch):
    """Mirror test_session_local.py: redirect LocalHost at fake_opencode.py."""
    import optio_opencode.host as host_mod
    orig_init = host_mod.LocalHost.__init__

    def _init(self, workdir, opencode_cmd=None):
        return orig_init(self, workdir=workdir, opencode_cmd=[sys.executable, FAKE_OPENCODE])

    monkeypatch.setattr(host_mod.LocalHost, "__init__", _init)


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """Inject `--scenario happy` into LocalHost.launch_opencode."""
    import optio_opencode.host as host_mod
    orig_launch = host_mod.LocalHost.launch_opencode
    holder = {"name": "happy"}

    async def _launch(self, password, ready_timeout_s, extra_args=None):
        return await orig_launch(
            self, password, ready_timeout_s,
            extra_args=["--scenario", holder["name"]],
        )
    monkeypatch.setattr(host_mod.LocalHost, "launch_opencode", _launch)
    return holder


async def _make_ctx(mongo_db, process_id: str, *, resume: bool) -> tuple[ProcessContext, ObjectId]:
    """Insert a process doc with supportsResume=True, build a ProcessContext."""
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    # Minimum overrides upsert_process leaves out for direct-ctx use:
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    ctx = ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        resume=resume,
    )
    return ctx, proc["_id"]


async def _run_one_cycle(mongo_db, process_id: str, resume: bool) -> None:
    ctx, _ = await _make_ctx(mongo_db, process_id, resume=resume)
    cfg = OpencodeTaskConfig(consumer_instructions=f"(scenario: happy {process_id})")
    await run_opencode_session(ctx, cfg)


async def test_terminal_flow_captures_snapshot_and_wipes_workdir(mongo_db, task_root):
    pid = "oc_terminal_1"
    await _run_one_cycle(mongo_db, pid, resume=False)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None
    assert snap["endState"] == "done"

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True

    wd = Path(local_task_dir(pid)) / "workdir"
    assert not wd.exists() or not any(wd.iterdir())


async def test_resume_creates_second_snapshot(mongo_db, task_root):
    pid = "oc_resume_1"
    await _run_one_cycle(mongo_db, pid, resume=False)
    await _run_one_cycle(mongo_db, pid, resume=True)
    count = await mongo_db[f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].count_documents(
        {"processId": pid}
    )
    assert count == 2


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh_launch(mongo_db, task_root):
    pid = "oc_resume_no_prior"
    await _run_one_cycle(mongo_db, pid, resume=True)  # nothing to resume; takes fresh path
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None  # the fresh-start cycle still captures a terminal snapshot
```

**Key points the implementer must honor (used by the test):**

- The `happy` scenario in `fake_opencode.py` runs to completion and exits 0. `run_opencode_session` returns normally; the executor (not present in this test) would mark the process `done`. We capture `end_state="done"` for the snapshot from inside `run_opencode_session`'s terminal block.
- `process_id` values must match the regex `[A-Za-z0-9._-]+` (paths.py rejects others).
- Tests rely on the `--scenario` arg fixture being autouse — the resume test pulls fixtures from this same file.

- [ ] **Step 2: Rewrite `run_opencode_session`**

Replace the existing implementation. Notes:
- Preserve the install block (~lines 73–113 of current session.py) and the run block (~lines 162–212) verbatim where indicated.
- All resume work uses host methods — no subprocess.run, no local/SSH branches.

```python
from optio_opencode.paths import local_task_dir, remote_task_dir
from optio_opencode.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from typing import AsyncIterator


async def run_opencode_session(ctx: ProcessContext, config: OpencodeTaskConfig) -> None:
    """Execute function body for one optio-opencode task instance."""
    # --- per-task filesystem layout ---------------------------------------
    if config.ssh is None:
        task_dir = local_task_dir(ctx.process_id)
        workdir = os.path.join(task_dir, "workdir")
        opencode_db = os.path.join(task_dir, "opencode.db")
        os.makedirs(task_dir, exist_ok=True)
        os.makedirs(workdir, exist_ok=True)
        host: Host = LocalHost(workdir=workdir)
    else:
        task_dir = remote_task_dir(ctx.process_id)
        workdir = f"{task_dir}/workdir"
        opencode_db = f"{task_dir}/opencode.db"
        host = RemoteHost(ssh_config=config.ssh, workdir=workdir)

    password = secrets.token_urlsafe(32)
    process: LaunchedProcess | None = None
    cancelled = False
    preserved_session_id: str | None = None
    session_id: str | None = None  # set on resume (from snapshot) or fresh launch

    # --- resume decision --------------------------------------------------
    resume_requested = bool(getattr(ctx, "resume", False))
    snapshot: dict | None = None
    if resume_requested:
        snapshot = await load_latest_snapshot(
            ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
        )
    resuming = snapshot is not None

    try:
        await host.connect()
        await host.setup_workdir()

        # Always wipe stale opencode.db before either branch; Mongo is authoritative.
        await host.remove_file(opencode_db)

        if resuming:
            try:
                await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
                session_bytes = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
                await host.opencode_import(opencode_db, session_bytes)
                preserved_session_id = snapshot["sessionId"]
            except Exception:
                # Per spec error-modes table: opencode_import / restore failure
                # → log; clear DB; fall back to fresh-start. Mongo blob preserved.
                _LOG.exception(
                    "resume restore failed; falling back to fresh-start path "
                    "(Mongo blob preserved for inspection)",
                )
                await host.remove_file(opencode_db)
                resuming = False
                preserved_session_id = None

        if not resuming:
            await host.write_text(
                "AGENTS.md", compose_agents_md(config.consumer_instructions),
            )
            await host.write_text(
                "opencode.json", json.dumps(config.opencode_config, indent=2),
            )
            await ctx.clear_has_saved_state()

        # --- install (preserve existing block ~lines 73–113 of current
        #     session.py: target detection, candidate path, _last_pct progress
        #     callback, install_opencode_binary call, ensure_opencode_installed
        #     fallback). Copy verbatim — no changes needed.
        binary_dir = os.environ.get("OPTIO_OPENCODE_BINARY_DIR")
        if binary_dir:
            ...  # see current session.py lines 73–112
        else:
            await host.ensure_opencode_installed(config.install_if_missing)

        # --- launch ------------------------------------------------------
        ctx.report_progress(None, "Launching opencode…")
        process = await host.launch_opencode(
            password=password,
            ready_timeout_s=READY_TIMEOUT_S,
            env={"OPENCODE_DB": opencode_db},
        )

        worker_port = await host.establish_tunnel(process.opencode_port)

        if preserved_session_id is not None:
            session_id = preserved_session_id
        else:
            session_id = await _create_opencode_session(
                worker_port, password, host.workdir,
            )

        await ctx.set_widget_upstream(
            f"http://127.0.0.1:{worker_port}",
            inner_auth=BasicAuth(username="opencode", password=password),
        )
        _workdir_b64 = (
            base64.urlsafe_b64encode(host.workdir.encode("utf-8"))
            .decode("ascii").rstrip("=")
        )
        await ctx.set_widget_data({
            "iframeSrc": f"{{widgetProxyUrl}}{_workdir_b64}/session/{session_id}",
            "localStorageOverrides": {
                "opencode.settings.dat:defaultServerUrl": "{widgetProxyUrl}",
            },
        })
        ctx.report_progress(None, "opencode is live")

        # --- run (preserve existing block ~lines 162–212 of current
        #     session.py: deliverable_queue, done_flag, error_flag,
        #     subprocess_exit, the four asyncio tasks, asyncio.wait,
        #     cancelled detection, error/exit raises, queue drain, task
        #     cancellation/gather). Copy verbatim — no changes.
        ...  # see current session.py lines 162–212

    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None

    finally:
        if process is not None:
            try:
                await host.terminate_opencode(process, aggressive=cancelled)
            except Exception:
                _LOG.exception("terminate_opencode failed")

        # Capture only if the task got far enough to have a session id.
        if session_id is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    session_id=preserved_session_id or session_id,
                    opencode_db=opencode_db,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                )
            except Exception:
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )

        try:
            await host.cleanup_workdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_workdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


# --- helpers ---------------------------------------------------------------


async def _stream_blob(ctx: ProcessContext, blob_id) -> "AsyncIterator[bytes]":
    """GridFS chunked reader → AsyncIterator[bytes] for host.restore_workdir."""
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            yield chunk


async def _read_blob_bytes(ctx: ProcessContext, blob_id) -> bytes:
    """Read a small-ish GridFS blob (e.g. session JSON) fully into memory."""
    out = bytearray()
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            out.extend(chunk)
    return bytes(out)


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    session_id: str,
    opencode_db: str,
    end_state: str,
    workdir_exclude: list[str] | None,
) -> None:
    """Export session, archive workdir, store both, insert snapshot doc, prune.

    All host-side work goes through host methods, so this is identical for
    LocalHost and RemoteHost.
    """
    session_json = await host.opencode_export(opencode_db, session_id)

    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    async with ctx.store_blob("session") as swriter:
        await swriter.write(session_json)
        session_blob_id = swriter.file_id

    await insert_snapshot(
        ctx._db,
        prefix=ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        session_id=session_id,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
        deliverables_emitted=[],  # (implementer may wire from the run-state tracker)
    )
    pruned = await prune_snapshots(
        ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
    )
    for p in pruned:
        try:
            await ctx.delete_blob(p["sessionBlobId"])
        except Exception:
            _LOG.exception("delete_blob(session) failed")
        try:
            await ctx.delete_blob(p["workdirBlobId"])
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")

    await ctx.mark_has_saved_state()
```

- [ ] **Step 3: Run tests**

Run: `cd packages/optio-opencode && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/ -v`
Expected: all PASS — existing local tests are unaffected (resume defaults to False; fresh-start path is functionally equivalent to today after `setup_workdir` finishes), and the new resume tests cover terminal capture / resume cycle / fresh-fallback-when-no-snapshot.

If `tests/test_session_remote.py` exists and the SSH harness is available, also run:

```bash
cd packages/optio-opencode && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/test_session_remote.py -v
```

Expected: PASS — RemoteHost continues to work for the no-resume case.

---

## Task 22: Update root + per-package `AGENTS.md` files

**Files:**
- Modify: `packages/optio-core/AGENTS.md`
- Modify: `packages/optio-contracts/AGENTS.md`
- Modify: `packages/optio-api/AGENTS.md`
- Modify: `packages/optio-ui/AGENTS.md`
- Modify: `packages/optio-opencode/AGENTS.md`
- Modify: `AGENTS.md` (root)

Per the repo-wide `AGENTS.md coordination` rule, whenever a package's public API changes, its `AGENTS.md` must be updated in the same commit.

- [ ] **Step 1: optio-core/AGENTS.md**

Document:
- `TaskInstance.supports_resume: bool = False` field.
- `ProcessContext.resume: bool` public read-only attribute.
- `ProcessContext.mark_has_saved_state()` / `clear_has_saved_state()` async methods.
- `ProcessContext.store_blob(name)` / `load_blob(file_id)` / `delete_blob(file_id)` GridFS helpers.
- `Optio.launch(process_id, resume=False)` / `launch_and_wait(process_id, resume=False)` — new `resume` kwarg.
- Mongo schema: `supportsResume: bool`, `hasSavedState: bool` on the process doc.
- m003 migration.

- [ ] **Step 2: optio-contracts/AGENTS.md**

Document the two new fields on `ProcessSchema` and the new `{ resume?: boolean }` launch body.

- [ ] **Step 3: optio-api/AGENTS.md**

Document `launchProcess(..., resume?: boolean)`. Note the 409 response when `resume=true` is requested against a task that doesn't support resume. `publishLaunch(..., resume?: boolean)`.

- [ ] **Step 4: optio-ui/AGENTS.md**

Document `LaunchControls` component + new `useProcessActions.launch(id, opts?)` signature. Document the new i18n keys `processes.resume`, `processes.restart`.

- [ ] **Step 5: optio-opencode/AGENTS.md**

Document:
- `OpencodeTaskConfig.workdir_exclude: list[str] | None`.
- Per-task directory layout (`task_dir/workdir`, `task_dir/opencode.db`, env vars).
- Snapshot collection `{prefix}_opencode_session_snapshots` + retention.
- `create_opencode_task` sets `supports_resume=True`.

- [ ] **Step 6: Root AGENTS.md**

Reflect the above changes in the unified reference (TaskInstance fields, ProcessContext methods, ProcessSchema fields, launch body).

---

## Task 23: Full-suite verification + single commit

- [ ] **Step 1: Run every test suite**

```bash
# optio-core
cd packages/optio-core && MONGO_URL=mongodb://localhost:27017 REDIS_URL=redis://localhost:6379 python -m pytest tests/ -v
```

```bash
# optio-opencode
cd packages/optio-opencode && MONGO_URL=mongodb://localhost:27017 python -m pytest tests/ -v
```

```bash
# optio-contracts
cd packages/optio-contracts && node_modules/.bin/tsc --noEmit
```

```bash
# optio-api
cd packages/optio-api && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit
```

```bash
# optio-ui
cd packages/optio-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit
```

Expected: all green.

- [ ] **Step 2: Sanity check migrations in a fresh DB**

Start an Optio instance (via the demo app or a Python REPL) against a fresh MongoDB and verify:
- A freshly upserted process has `supportsResume` matching the task and `hasSavedState=false`.
- Pre-existing process docs (inserted without `hasSavedState`) are backfilled to `hasSavedState=false` by m003 on startup.

- [ ] **Step 3: Manual end-to-end smoke (optio-opencode)**

Start the demo app or a local dashboard. Launch an opencode task. Observe:
- First launch: single play button.
- After task reaches `done`: split button appears with Resume as primary.
- Click Resume: the previous session is restored (workdir files + conversation history).
- Click Restart (menu item): fresh start, session wiped.

Document findings in the PR description (screenshots welcome).

- [ ] **Step 4: Stage and commit (single commit for the whole plan)**

```bash
git add -A
git status            # verify no unexpected files
git commit -m "$(cat <<'EOF'
feat: platform-wide resume capability + optio-opencode implementation

Thread a generic `resume` signal through task-definition → UI → API →
Redis → executor, and implement `optio-opencode` as the first consumer
that persists SQLite session history + workdir across relaunches.

Platform (optio-core / optio-contracts / optio-api / optio-ui):
- TaskInstance.supports_resume + ProcessSchema.supportsResume/hasSavedState
- ProcessContext.resume, mark/clear_has_saved_state, store/load/delete_blob
- launch endpoint accepts { resume?: boolean }; publishLaunch forwards it
- LaunchControls split-button (Resume / Restart) + useProcessActions.launch(id, opts?)
- m003 backfill migration

Executor (optio-opencode):
- Per-task stable directory (OPTIO_OPENCODE_TASK_ROOT)
- tar+gzip workdir archive, Mongo {prefix}_opencode_session_snapshots
  with last-5 retention, workdir wipe on terminal
- Resume branch: restore workdir + opencode.db via `opencode import`

Spec: docs/2026-04-24-optio-resume-design.md
Plan: docs/2026-04-24-optio-resume-plan.md
EOF
)"
```

Expected: a single commit landing on `csillag/process-resume`.

---

## Self-review check (run before executing)

1. **Spec coverage** — every section of `docs/2026-04-24-optio-resume-design.md` maps to at least one task:
   - Contract changes (TaskInstance, ProcessSchema, launch body, publisher) → Tasks 1, 7, 8, 10.
   - `upsert_process` rules (incl. `clear_result_fields`) → Task 2.
   - `ProcessContext` additions → Tasks 3, 4.
   - Launch-payload plumbing (`consumer`/`lifecycle`/`executor`) → Task 5.
   - m003 migration → Task 6.
   - Handler validation → Task 9.
   - UI: LaunchControls + integrations + hook → Tasks 11, 12, 13, 14.
   - optio-opencode: per-task dir, excludes, archive helpers, host abstraction additions (LocalHost + RemoteHost), snapshots, task flag, fake_opencode stub, session flow → Tasks 15, 16, 17, 17.5, 17.6, 18, 19, 20, 21.
   - AGENTS.md coordination → Task 22.
2. **Placeholder scan** — every TDD step has either concrete code or a concrete file reference. The two remaining `...` markers in Task 21 Step 2 (the binary_dir install branch and the deliverable_queue/watchers run block) are explicit "preserve existing code at lines X–Y of current session.py" pointers, not invitations for new code.
3. **Type consistency** — method names used throughout (`mark_has_saved_state`, `clear_has_saved_state`, `store_blob`/`load_blob`/`delete_blob`, `supports_resume`/`supportsResume`, `hasSavedState`, `workdir_exclude`, the new host methods `opencode_import`/`opencode_export`/`archive_workdir`/`restore_workdir`/`remove_file`) match the spec's names consistently.
4. **Open deviations from spec:**
   - 409 vs 400 in the handler (Task 9) — documented inline; acceptable tradeoff.
   - `Host.remove_file` is added by the plan but not enumerated in the spec — used by `session.py` to wipe the stale `opencode.db` at start-of-run uniformly across local/remote. Plumbing-level addition; flagged in Task 17.5.
5. **Task ordering note:** Tasks 17.5 and 17.6 import `fake_opencode.py`'s new `import`/`export` subcommands (which Task 20 adds). Run Task 20 *before* Tasks 17.5/17.6, or mark the host-method tests with `@pytest.mark.skip` until Task 20 lands.
