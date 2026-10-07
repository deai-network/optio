# Resurrect a failed session: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Resurrect action that saves the work a failed claudecode run left on its host and then resumes from it, in one click.

**Architecture:** A separate command (`POST /processes/:id/resurrect` -> engine RPC `resurrect` -> `Optio.resurrect`) runs an optional per-task hook (`TaskInstance.resurrect`) in a background task, then launches a normal resume. Two process fields drive it: `supportsResurrect` (task has a hook) and `hasUnsavedWork` (set by the agent while its workdir holds unsaved work). optio-claudecode implements the hook; optio-ui makes Resurrect the primary launch action when it applies.

**Tech Stack:** Python 3.11+ (motor, pytest, pytest-asyncio), TypeScript (zod, ts-rest, clamator codegen, vitest), React 19 + antd 6 (optio-ui), MongoDB GridFS.

**Spec:** `docs/2026-10-07-resurrect-failed-session-design.md`

## Global Constraints

- Work on branch `csillag/resurrect` in superego `~/resurrect/optio`; base `main` d775a7d5. Nothing is pushed or released by this plan.
- Never commit `pnpm-lock.yaml` (it differs locally only because this clone has no `../unitas` sibling). Always `git add` explicit paths.
- No `Co-Authored-By` or other self-credit lines in commits (optio `AGENTS.md`).
- When a package's public API, exports, props, hooks or contract endpoints change, update that package's `AGENTS.md` in the same commit, and the root `AGENTS.md` where it mirrors it (optio `AGENTS.md`).
- No test may depend on wall-clock time: wait for a condition with a 60 s hang ceiling, never sleep-then-assert (optio `AGENTS.md`, "Tests must survive CPU starvation").
- Test environment on superego: `export MONGO_URL=mongodb://127.0.0.1:27217 REDIS_URL=redis://127.0.0.1:6579` (containers `resurrect-mongo`, `resurrect-redis`; `docker start` them if stopped). Python: `../../.venv/bin/pytest` from the package dir. TS: `npx vitest run <file>` from the package dir. After changing a TS package another package imports, rebuild it: `pnpm --filter <pkg> build`.
- Browser checks use only `/usr/bin/chromium` (no downloaded browsers).
- Field names: `supportsResurrect`, `hasUnsavedWork` (Mongo and wire); Python `TaskInstance.resurrect`, `ProcessContext.mark_unsaved_work()` / `clear_unsaved_work()`, `optio_core.NothingToResurrect`, `optio_core.ResurrectOutcome`.
- Resurrect failure reasons, exactly: `not-found`, `not-resurrectable`, `no-resurrect-support`, `resurrect-in-progress`, `launch-blocked`, `shutting-down`.

## Review Focus

1. A process that failed on today's code: no pending-capture record, no `home/.claude` in the workdir, an orphaned session blob, partial workdir chunks. Expect: Resurrect uses the orphaned session blob, leaves the unrecorded partial chunks alone, saves, resumes. (Task 7, `test_resurrect_old_code_shape`.)
2. A session blob older than the latest snapshot (left by an earlier run) must never be picked by the fallback. (Task 7, `test_fallback_ignores_session_blob_older_than_latest_snapshot`.)
3. The workdir exists but is empty (LocalHost `_build_host` creates it): `NothingToResurrect`, flag cleared, no empty snapshot. (Task 7, `test_empty_workdir_is_nothing_to_resurrect`.)
4. Save succeeds but launches matching the task are blocked meanwhile: flag cleared, snapshot kept, log line, process stays resumable. (Task 2, `test_resurrect_success_but_launch_blocked`.)
5. A second Resurrect, or a Resume, while a resurrect runs: refused (`resurrect-in-progress` / `not-launchable`). (Task 2, `test_second_resurrect_and_launch_refused_while_running`.)

---

### Task 1: optio-core flags, hook field and context methods

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py` (TaskInstance, new `ResurrectOutcome`)
- Modify: `packages/optio-core/src/optio_core/exceptions.py` (new `NothingToResurrect`)
- Modify: `packages/optio-core/src/optio_core/store.py` (`upsert_process`, new `set_has_unsaved_work`)
- Modify: `packages/optio-core/src/optio_core/context.py` (`mark_unsaved_work`, `clear_unsaved_work`, `store_blob(file_id=)`)
- Modify: `packages/optio-core/src/optio_core/executor.py` (`launch_process` clears the flag)
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (sync-time validation)
- Modify: `packages/optio-core/src/optio_core/__init__.py` (exports)
- Modify: `packages/optio-core/AGENTS.md`
- Test: `packages/optio-core/tests/test_resurrect_flags.py`

**Interfaces:**
- Produces: `TaskInstance.resurrect: Callable[[ProcessContext], Awaitable[None]] | None = None`; Mongo fields `supportsResurrect` (set by task sync) and `hasUnsavedWork` (default False); `store.set_has_unsaved_work(db, prefix, process_oid, value: bool) -> None`; `ProcessContext.mark_unsaved_work() -> None`, `ProcessContext.clear_unsaved_work() -> None`; `ProcessContext.store_blob(name: str, file_id: ObjectId | None = None)`; `ResurrectOutcome(ok: bool, reason: Literal[...] | None = None, proc: dict | None = None)`; `NothingToResurrect(Exception)`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-core/tests/test_resurrect_flags.py`:

```python
"""Resurrect: process fields, context methods, launch clears hasUnsavedWork."""

import pytest
from bson import ObjectId

from optio_core import NothingToResurrect, ResurrectOutcome
from optio_core.context import ProcessContext
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def _noop(ctx):
    pass


async def _hook(ctx):
    pass


def _ctx(mongo_db, prefix, oid, process_id="p"):
    import asyncio
    return ProcessContext(
        process_oid=oid, process_id=process_id, root_oid=oid, depth=0,
        params={}, services={}, db=mongo_db, prefix=prefix,
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


def test_exports():
    assert issubclass(NothingToResurrect, Exception)
    out = ResurrectOutcome(ok=False, reason="not-resurrectable")
    assert out.ok is False and out.reason == "not-resurrectable" and out.proc is None


@pytest.mark.asyncio
async def test_task_sync_sets_supports_resurrect(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="rflags1")
    with_hook = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="with", name="With",
        supports_resume=True, resurrect=_hook,
    ))
    without = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="without", name="Without",
    ))
    coll = mongo_db["rflags1_processes"]
    assert (await coll.find_one({"_id": with_hook["_id"]}))["supportsResurrect"] is True
    assert (await coll.find_one({"_id": with_hook["_id"]}))["hasUnsavedWork"] is False
    assert (await coll.find_one({"_id": without["_id"]}))["supportsResurrect"] is False


@pytest.mark.asyncio
async def test_resurrect_requires_supports_resume_at_sync(mongo_db):
    fw = Optio()

    async def tasks(_services, _metadata_filter):
        return [TaskInstance(
            execute=_noop, process_id="bad", name="Bad", resurrect=_hook,
        )]

    with pytest.raises(ValueError, match="resurrect requires supports_resume=True"):
        await fw.init(mongo_db=mongo_db, prefix="rflags2", get_task_definitions=tasks)


@pytest.mark.asyncio
async def test_mark_and_clear_unsaved_work(mongo_db):
    oid = ObjectId()
    coll = mongo_db["rflags3_processes"]
    await coll.insert_one({"_id": oid, "processId": "p", "supportsResurrect": True})
    ctx = _ctx(mongo_db, "rflags3", oid)
    await ctx.mark_unsaved_work()
    assert (await coll.find_one({"_id": oid}))["hasUnsavedWork"] is True
    await ctx.clear_unsaved_work()
    assert (await coll.find_one({"_id": oid}))["hasUnsavedWork"] is False


@pytest.mark.asyncio
async def test_mark_unsaved_work_ignored_without_resurrect_support(mongo_db, caplog):
    oid = ObjectId()
    coll = mongo_db["rflags4_processes"]
    await coll.insert_one({"_id": oid, "processId": "p", "supportsResurrect": False})
    ctx = _ctx(mongo_db, "rflags4", oid)
    await ctx.mark_unsaved_work()
    assert "hasUnsavedWork" not in await coll.find_one({"_id": oid})
    assert "supportsResurrect" in caplog.text or "resurrect" in caplog.text


@pytest.mark.asyncio
async def test_clear_unsaved_work_silent_when_already_clear(mongo_db, caplog):
    oid = ObjectId()
    coll = mongo_db["rflags5_processes"]
    await coll.insert_one({"_id": oid, "processId": "p"})
    ctx = _ctx(mongo_db, "rflags5", oid)
    await ctx.clear_unsaved_work()
    assert "hasUnsavedWork" not in await coll.find_one({"_id": oid})
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_store_blob_with_given_file_id(mongo_db):
    oid = ObjectId()
    await mongo_db["rflags6_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = _ctx(mongo_db, "rflags6", oid)
    wanted = ObjectId()
    async with ctx.store_blob("workdir", file_id=wanted) as w:
        await w.write(b"abc")
    assert w.file_id == wanted
    f = await mongo_db["fs.files"].find_one({"_id": wanted})
    assert f["metadata"] == {"processId": str(oid), "prefix": "rflags6", "name": "workdir"}


@pytest.mark.asyncio
async def test_launch_clears_unsaved_work(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="rflags7")
    proc = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="p7", name="P7",
        supports_resume=True, resurrect=_hook,
    ))
    coll = mongo_db["rflags7_processes"]
    await coll.update_one({"_id": proc["_id"]}, {"$set": {"hasUnsavedWork": True}})
    await fw._executor.launch_process(str(proc["_id"]), session_id=None)
    assert (await coll.find_one({"_id": proc["_id"]}))["hasUnsavedWork"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `packages/optio-core`): `../../.venv/bin/pytest -q tests/test_resurrect_flags.py`
Expected: FAIL at import (`cannot import name 'NothingToResurrect'`).

- [ ] **Step 3: Implement**

`exceptions.py`, append:

```python
class NothingToResurrect(Exception):
    """Raised by a task's resurrect hook when nothing is left to save
    (no workdir on the host, an empty one, or no session state). optio-core
    then clears the process's hasUnsavedWork flag."""
```

`models.py`: add after `DismissOutcome`:

```python
@dataclass(frozen=True)
class ResurrectOutcome:
    """Result of Optio.resurrect. ok=True means the save was started in the
    background; `proc` is the process doc at that moment."""
    ok: bool
    reason: Literal[
        "not-found", "not-resurrectable", "no-resurrect-support",
        "resurrect-in-progress", "launch-blocked", "shutting-down",
    ] | None = None
    proc: dict[str, Any] | None = None
```

and in `TaskInstance`, after `auto_resume: bool = False` and its comment:

```python
    # Optional "resurrect" hook: saves the work a failed run left on its host
    # (snapshot + mark_has_saved_state) and removes the host leftovers. Run by
    # Optio.resurrect outside `execute`, followed by a resume. Raises
    # NothingToResurrect when nothing is left to save. Requires
    # supports_resume=True (validated at task-sync time).
    resurrect: Callable[..., Awaitable[None]] | None = None
```

`store.py` `upsert_process`: in `$set` after `"supportsResume": task.supports_resume,` add `"supportsResurrect": getattr(task, "resurrect", None) is not None,`; in `$setOnInsert` after `"hasSavedState": False,` add `"hasUnsavedWork": False,`. After `set_auto_resume_scheduled` add:

```python
async def set_has_unsaved_work(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, value: bool,
) -> None:
    """Set `hasUnsavedWork`: the host workdir holds work newer than the last
    snapshot. Agents set it through ProcessContext; optio-core clears it when
    a launch starts and when a resurrect saved the work or found nothing."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"hasUnsavedWork": value}},
    )
```

`context.py`: after `_set_has_saved_state` add:

```python
    async def mark_unsaved_work(self) -> None:
        """Flag that the host workdir now holds work newer than the last
        snapshot (Resurrect can save it if the run fails).

        No-op with a warning when the task has no resurrect hook.
        Idempotent: a second call with the same value issues no update.
        """
        await self._set_unsaved_work(True)

    async def clear_unsaved_work(self) -> None:
        """Flag that nothing unsaved is left on the host (a save completed).

        Idempotent and silent when the flag is already clear.
        """
        await self._set_unsaved_work(False)

    async def _set_unsaved_work(self, value: bool) -> None:
        from optio_core.store import _collection
        coll = _collection(self._db, self._prefix)
        current = await coll.find_one(
            {"_id": self._process_oid},
            {"supportsResurrect": 1, "hasUnsavedWork": 1},
        )
        if current is None:
            _log.warning(
                "mark/clear_unsaved_work: process %s not found", self._process_oid,
            )
            return
        if bool(current.get("hasUnsavedWork", False)) == value:
            return  # Idempotent: no redundant write.
        if not current.get("supportsResurrect", False):
            _log.warning(
                "mark_unsaved_work called on task %s which has no resurrect hook "
                "(supportsResurrect=False); ignored",
                self.process_id,
            )
            return
        await coll.update_one(
            {"_id": self._process_oid},
            {"$set": {"hasUnsavedWork": value}},
        )
```

and change `store_blob`:

```python
    @asynccontextmanager
    async def store_blob(self, name: str, file_id: ObjectId | None = None):
        """Open a GridFS upload stream tagged with processId + prefix.

        ``file_id`` pre-selects the blob's id (so a caller can record it
        before writing); default: GridFS generates one.
        ...keep the existing usage lines...
        """
        bucket = self._gridfs()
        metadata = {
            "processId": str(self._process_oid),
            "prefix": self._prefix,
            "name": name,
        }
        if file_id is None:
            stream_cm = bucket.open_upload_stream(name, metadata=metadata)
        else:
            stream_cm = bucket.open_upload_stream_with_id(file_id, name, metadata=metadata)
        async with stream_cm as stream:
            yield _GridInWrapper(stream)
```

`executor.py` `launch_process`: after the `append_log(... "State changed to scheduled")` line add:

```python
        # A launch rebuilds the workdir: whatever unsaved work a failed run
        # left there is gone from now on (Resurrect no longer applies).
        if proc.get("hasUnsavedWork"):
            await set_has_unsaved_work(self._db, self._prefix, proc["_id"], False)
```

and add `set_has_unsaved_work` to executor.py's `from optio_core.store import (...)` list.

`lifecycle.py`, in the task-sync validation loop next to the auto_resume check:

```python
            if task.resurrect is not None and not task.supports_resume:
                raise ValueError(
                    f"Task '{task.process_id}': resurrect requires "
                    f"supports_resume=True"
                )
```

`__init__.py`: import `ResurrectOutcome` from `optio_core.models` and `NothingToResurrect` from `optio_core.exceptions`; add both to `__all__`.

`packages/optio-core/AGENTS.md`: in the TaskInstance field list add `resurrect`; in the ProcessContext method list add `mark_unsaved_work()` / `clear_unsaved_work()` and the `file_id` parameter of `store_blob`; in the process-document fields add `supportsResurrect`, `hasUnsavedWork`; list `NothingToResurrect`, `ResurrectOutcome` among exports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `../../.venv/bin/pytest -q tests/test_resurrect_flags.py tests/test_outcomes.py tests/test_store.py tests/test_context_resume.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/{models,exceptions,store,context,executor,lifecycle,__init__}.py \
        packages/optio-core/tests/test_resurrect_flags.py packages/optio-core/AGENTS.md
git commit -m "feat(optio-core): resurrect hook field, supportsResurrect/hasUnsavedWork flags"
```

---

### Task 2: `Optio.resurrect` command

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/AGENTS.md`
- Test: `packages/optio-core/tests/test_resurrect.py`

**Interfaces:**
- Consumes: Task 1 (`TaskInstance.resurrect`, `ResurrectOutcome`, `NothingToResurrect`, `set_has_unsaved_work`).
- Produces: `Optio.resurrect(process_id: str, *, session_id: str | None) -> ResurrectOutcome`; `Optio._resurrecting: dict[ObjectId, asyncio.Task]`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-core/tests/test_resurrect.py`:

```python
"""Optio.resurrect: preconditions, background save, resume, failure handling."""

import asyncio
import time

import pytest

from optio_core import NothingToResurrect, ResurrectOutcome
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def _noop(ctx):
    pass


async def _wait_until(pred, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached within the hang ceiling")


async def _setup(mongo_db, prefix, hook, *, state="failed", unsaved=True, metadata=None):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix)
    proc = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="r1", name="R1", supports_resume=True,
        resurrect=hook, metadata=metadata or {},
    ))
    coll = mongo_db[f"{prefix}_processes"]
    await coll.update_one({"_id": proc["_id"]}, {"$set": {
        "status.state": state, "hasUnsavedWork": unsaved, "hasSavedState": True,
    }})
    launched = []

    async def _spy(oid, *, resume, session_id):
        launched.append((oid, resume, session_id))
    fw._executor.launch_process = _spy
    return fw, coll, proc, launched


@pytest.mark.asyncio
async def test_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="res0")
    assert await fw.resurrect("missing", session_id=None) == ResurrectOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_no_resurrect_support(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="res1")
    await fw.adhoc_define(TaskInstance(execute=_noop, process_id="plain", name="Plain"))
    out = await fw.resurrect("plain", session_id=None)
    assert out == ResurrectOutcome(ok=False, reason="no-resurrect-support")


@pytest.mark.asyncio
async def test_not_resurrectable_without_flag_or_when_active(mongo_db):
    async def hook(ctx):
        raise AssertionError("must not run")
    fw, coll, proc, _ = await _setup(mongo_db, "res2", hook, unsaved=False)
    assert (await fw.resurrect("r1", session_id=None)).reason == "not-resurrectable"
    await coll.update_one({"_id": proc["_id"]}, {"$set": {"hasUnsavedWork": True, "status.state": "running"}})
    assert (await fw.resurrect("r1", session_id=None)).reason == "not-resurrectable"


@pytest.mark.asyncio
async def test_success_clears_flag_and_resumes(mongo_db):
    calls = []

    async def hook(ctx):
        calls.append(ctx.process_id)
        ctx.report_progress(None, "hook ran")
    fw, coll, proc, launched = await _setup(mongo_db, "res3", hook)
    out = await fw.resurrect("r1", session_id="s1")
    assert out.ok is True and out.proc["_id"] == proc["_id"]

    async def done():
        return bool(launched)
    await _wait_until(done)
    assert calls == ["r1"]
    assert launched == [(str(proc["_id"]), True, "s1")]
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is False
    messages = [e["message"] for e in doc["log"]]
    assert "Resurrect requested" in messages
    assert any(m.startswith("Resurrected") for m in messages)
    assert proc["_id"] not in fw._resurrecting


@pytest.mark.asyncio
async def test_nothing_to_resurrect_clears_flag_without_resume(mongo_db):
    async def hook(ctx):
        raise NothingToResurrect("workdir no longer on the host")
    fw, coll, proc, launched = await _setup(mongo_db, "res4", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True

    async def flag_cleared():
        doc = await coll.find_one({"_id": proc["_id"]})
        return doc["hasUnsavedWork"] is False and proc["_id"] not in fw._resurrecting
    await _wait_until(flag_cleared)
    assert launched == []
    doc = await coll.find_one({"_id": proc["_id"]})
    assert any("workdir no longer on the host" in e["message"] for e in doc["log"])
    assert doc["progress"]["message"] is None


@pytest.mark.asyncio
async def test_hook_error_keeps_flag(mongo_db):
    async def hook(ctx):
        raise RuntimeError("disk full")
    fw, coll, proc, launched = await _setup(mongo_db, "res5", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True

    async def finished():
        return proc["_id"] not in fw._resurrecting
    await _wait_until(finished)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is True
    assert launched == []
    assert any(e["level"] == "error" and "disk full" in e["message"] for e in doc["log"])


@pytest.mark.asyncio
async def test_second_resurrect_and_launch_refused_while_running(mongo_db):
    release = asyncio.Event()

    async def hook(ctx):
        await release.wait()
    fw, coll, proc, launched = await _setup(mongo_db, "res6", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    again = await fw.resurrect("r1", session_id=None)
    assert again.reason == "resurrect-in-progress"
    blocked = await fw.launch("r1", resume=True, session_id=None)
    assert blocked.ok is False and blocked.reason == "not-launchable"
    release.set()

    async def done():
        return bool(launched)
    await _wait_until(done)


@pytest.mark.asyncio
async def test_launch_blocked_refused_up_front(mongo_db):
    async def hook(ctx):
        raise AssertionError("must not run")
    fw, *_ = await _setup(mongo_db, "res7", hook, metadata={"project": "p1"})
    async with fw.block_launches({"project": "p1"}):
        out = await fw.resurrect("r1", session_id=None)
    assert out.reason == "launch-blocked"


@pytest.mark.asyncio
async def test_resurrect_success_but_launch_blocked(mongo_db):
    gate = asyncio.Event()

    async def hook(ctx):
        await gate.wait()
    fw, coll, proc, launched = await _setup(mongo_db, "res8", hook, metadata={"project": "p1"})
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    async with fw.block_launches({"project": "p1"}):
        gate.set()

        async def finished():
            return proc["_id"] not in fw._resurrecting and any(
                "Resume after resurrect not started" in e["message"]
                for e in (await coll.find_one({"_id": proc["_id"]}))["log"]
            )
        await _wait_until(finished)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is False
    assert launched == []


@pytest.mark.asyncio
async def test_shutdown_cancels_running_resurrect_and_keeps_flag(mongo_db):
    started = asyncio.Event()

    async def hook(ctx):
        started.set()
        await asyncio.Event().wait()
    fw, coll, proc, _ = await _setup(mongo_db, "res9", hook)
    assert (await fw.resurrect("r1", session_id=None)).ok is True
    await asyncio.wait_for(started.wait(), 60)
    await fw.shutdown(grace_seconds=0.1)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert doc["hasUnsavedWork"] is True
    assert fw._resurrecting == {}
    assert (await fw.resurrect("r1", session_id=None)).reason == "shutting-down"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `../../.venv/bin/pytest -q tests/test_resurrect.py`
Expected: FAIL (`'Optio' object has no attribute 'resurrect'`).

- [ ] **Step 3: Implement**

In `lifecycle.py`:

1. Imports: add `ResurrectOutcome` to the `optio_core.models` import, `NothingToResurrect` next to `LaunchError` (`from optio_core.exceptions import LaunchError, NothingToResurrect`), and `set_has_unsaved_work`, `append_log`, `update_progress` to the `optio_core.store` import if not already imported; `from optio_core.models import Progress` if not imported; `from optio_core.context import ProcessContext` if not imported.
2. `__init__`: add `self._resurrecting: dict[ObjectId, asyncio.Task] = {}`.
3. `launch`: right after the `not-found` check add:

```python
        if proc["_id"] in self._resurrecting:
            # The resurrect in progress ends in its own resume.
            return LaunchOutcome(ok=False, reason="not-launchable")
```

4. New methods after `launch_and_await_result`:

```python
    async def resurrect(
        self, process_id: str, *, session_id: str | None,
    ) -> ResurrectOutcome:
        """Save the work a failed run left on its host, then resume.

        Fire-and-forget like `launch`: preconditions are answered here with a
        typed reason; the task's resurrect hook and the follow-up resume run
        in a background task, reported through the process's progress and
        log. Spec: docs/2026-10-07-resurrect-failed-session-design.md
        """
        if self._shutting_down:
            return ResurrectOutcome(ok=False, reason="shutting-down")
        proc = await self._resolve(process_id)
        if proc is None:
            return ResurrectOutcome(ok=False, reason="not-found")
        task = self._executor._task_registry.get(proc["processId"])
        if task is None or getattr(task, "resurrect", None) is None:
            return ResurrectOutcome(ok=False, reason="no-resurrect-support")
        oid = proc["_id"]
        if oid in self._resurrecting:
            return ResurrectOutcome(ok=False, reason="resurrect-in-progress")
        if (
            proc["status"]["state"] not in LAUNCHABLE_STATES
            or not proc.get("hasUnsavedWork", False)
        ):
            return ResurrectOutcome(ok=False, reason="not-resurrectable")
        if self._matches_block(task.metadata):
            return ResurrectOutcome(ok=False, reason="launch-blocked")
        self._resurrecting[oid] = asyncio.create_task(
            self._run_resurrect(proc, task, session_id),
        )
        return ResurrectOutcome(ok=True, proc=proc)

    async def _run_resurrect(
        self, proc: dict, task: TaskInstance, session_id: str | None,
    ) -> None:
        oid = proc["_id"]
        db, prefix = self._config.mongo_db, self._config.prefix
        saved = False
        try:
            await append_log(db, prefix, oid, "event", "Resurrect requested")
            await update_progress(db, prefix, oid, Progress(
                percent=None, message="Resurrecting: saving the unsaved work…",
            ))
            ctx = ProcessContext(
                process_oid=oid,
                process_id=proc["processId"],
                root_oid=proc.get("rootId") or oid,
                depth=proc.get("depth", 0),
                params=proc.get("params", {}),
                metadata=proc.get("metadata", {}),
                services=self._executor._services,
                db=db,
                prefix=prefix,
                cancellation_flag=asyncio.Event(),
                child_counter={"next": 0},
                resume=False,
                session_id=session_id,
            )
            ctx._executor = self._executor
            try:
                await task.resurrect(ctx)
            except NothingToResurrect as exc:
                await set_has_unsaved_work(db, prefix, oid, False)
                await append_log(db, prefix, oid, "event", f"Nothing to resurrect: {exc}")
                return
            except asyncio.CancelledError:
                await append_log(
                    db, prefix, oid, "event",
                    "Resurrect interrupted (engine shutting down); unsaved work kept",
                )
                raise
            except Exception as exc:
                logger.exception("resurrect of %s failed", proc["processId"])
                await append_log(db, prefix, oid, "error", f"Resurrect failed: {exc}")
                return
            await set_has_unsaved_work(db, prefix, oid, False)
            await append_log(
                db, prefix, oid, "event", "Resurrected: unsaved work saved; resuming",
            )
            saved = True
        finally:
            try:
                await update_progress(db, prefix, oid, Progress(percent=None, message=None))
            finally:
                self._resurrecting.pop(oid, None)
        if saved:
            outcome = await self.launch(str(oid), resume=True, session_id=session_id)
            if not outcome.ok:
                await append_log(
                    db, prefix, oid, "event",
                    f"Resume after resurrect not started: {outcome.reason}",
                )
```

5. `shutdown`: right after the line `self._shutting_down = True`'s `try:` block step 3 (the executor drain loop and its belt-and-braces force-cancel), add:

```python
            # 3b. Resurrects in progress: cancel; their flag stays set.
            for t in list(self._resurrecting.values()):
                t.cancel()
            if self._resurrecting:
                await asyncio.gather(*self._resurrecting.values(), return_exceptions=True)
            self._resurrecting.clear()
```

`packages/optio-core/AGENTS.md`: document `Optio.resurrect` next to `launch` (reasons, fire-and-forget, resume afterwards, flag handling, lock against launch).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `../../.venv/bin/pytest -q tests/test_resurrect.py tests/test_resurrect_flags.py tests/test_outcomes.py tests/test_auto_resume.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_resurrect.py packages/optio-core/AGENTS.md
git commit -m "feat(optio-core): Optio.resurrect saves a failed run's work, then resumes"
```

---

### Task 3: Wire: contracts, codegen, engine RPC

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts`
- Modify: `packages/optio-contracts/src/engine-failure-reasons.ts`
- Modify: `packages/optio-contracts/src/optio-engine-to-api.ts`
- Modify: `packages/optio-contracts/src/api-to-frontend.ts`
- Modify: `packages/optio-contracts/src/index.ts`
- Regenerate: `packages/optio-api/src/_generated/*`, `packages/optio-core/src/optio_core/_generated/*` (`make codegen`)
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`
- Modify: `packages/optio-contracts/AGENTS.md`, `packages/optio-core/AGENTS.md`
- Test: `packages/optio-contracts/src/__tests__/optio-engine-contract.test.ts`, `packages/optio-contracts/src/__tests__/process-schema.test.ts`, `packages/optio-core/tests/test_engine_service.py`

**Interfaces:**
- Consumes: Task 2 (`Optio.resurrect`).
- Produces: zod `ResurrectFailureReason`; `ProcessSchema.supportsResurrect?`, `ProcessSchema.hasUnsavedWork?`; engine RPC `resurrect({ processId, sessionId }) -> { ok: true, process } | { ok: false, reason }`; frontend route `processes.resurrect` (`POST /processes/:id/resurrect`, body `{ sessionId? }`, 200/404/409); Python `ResurrectParams`, `ResurrectResult` in `optio_core._generated.optio_engine`; `OptioEngineService.resurrect`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-contracts/src/__tests__/optio-engine-contract.test.ts` (inside the `describe`), and add `ResurrectFailureReason` to its import from `../engine-failure-reasons.js`:

```ts
  it('exposes resurrect with the launch-shaped result', () => {
    const resurrect = optioEngineContract.methods.resurrect;
    expect(resurrect).toBeDefined();
    expect(resurrect.params.parse({ processId: 'p1', sessionId: null })).toEqual({ processId: 'p1', sessionId: null });
    const fail = resurrect.result.parse({ ok: false, reason: 'resurrect-in-progress' });
    expect(fail.ok).toBe(false);
  });

  it('knows exactly the resurrect failure reasons', () => {
    expect(ResurrectFailureReason.options).toEqual([
      'not-found', 'not-resurrectable', 'no-resurrect-support',
      'resurrect-in-progress', 'launch-blocked', 'shutting-down',
    ]);
  });
```

Append to `packages/optio-contracts/src/__tests__/process-schema.test.ts` (it already has a `baseProcess()` fixture):

```ts
it('accepts supportsResurrect and hasUnsavedWork', () => {
  const parsed = ProcessSchema.parse({ ...baseProcess(), supportsResurrect: true, hasUnsavedWork: true });
  expect(parsed.supportsResurrect).toBe(true);
  expect(parsed.hasUnsavedWork).toBe(true);
});
```

Append to `packages/optio-core/tests/test_engine_service.py` (add `ResurrectParams, ResurrectResult` to the `_generated` import and `ResurrectOutcome` to the models import):

```python
@pytest.mark.asyncio
async def test_resurrect_ok(fake_optio, sample_idle_proc):
    from optio_core._engine_service import OptioEngineService
    fake_optio.resurrect = AsyncMock(return_value=ResurrectOutcome(ok=True, proc=sample_idle_proc))
    svc = OptioEngineService(fake_optio)
    res = await svc.resurrect(ResurrectParams(process_id="p1", session_id="s"))
    assert isinstance(res, ResurrectResult)
    fake_optio.resurrect.assert_awaited_once_with("p1", session_id="s")
    assert res.root.ok is True


@pytest.mark.asyncio
async def test_resurrect_reason(fake_optio):
    from optio_core._engine_service import OptioEngineService
    fake_optio.resurrect = AsyncMock(return_value=ResurrectOutcome(ok=False, reason="not-resurrectable"))
    svc = OptioEngineService(fake_optio)
    res = await svc.resurrect(ResurrectParams(process_id="p1", session_id=None))
    assert res.root.ok is False and res.root.reason == "not-resurrectable"


def test_wire_keys_carry_resurrect_fields():
    from optio_core._engine_service import _PROCESS_WIRE_KEYS
    assert {"supportsResurrect", "hasUnsavedWork"} <= _PROCESS_WIRE_KEYS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (in `packages/optio-contracts`): `npx vitest run src/__tests__/optio-engine-contract.test.ts src/__tests__/process-schema.test.ts` -> FAIL (`resurrect` undefined).
Run (in `packages/optio-core`): `../../.venv/bin/pytest -q tests/test_engine_service.py` -> FAIL (import error `ResurrectParams`).

- [ ] **Step 3: Implement**

`engine-failure-reasons.ts`, after `DismissFailureReason`:

```ts
export const ResurrectFailureReason = z.enum([
  'not-found',
  'not-resurrectable',
  'no-resurrect-support',
  'resurrect-in-progress',
  'launch-blocked',
  'shutting-down',
]);
```

and `export type ResurrectFailureReason = z.infer<typeof ResurrectFailureReason>;` with the other types.

`schemas/process.ts`, after `hasSavedState: z.boolean().optional(),`:

```ts
  // Resurrect: the task has a resurrect hook / the host workdir holds work
  // newer than the last snapshot. Missing = false.
  supportsResurrect: z.boolean().optional(),
  hasUnsavedWork: z.boolean().optional(),
```

`optio-engine-to-api.ts`: import `ResurrectFailureReason`; add

```ts
const resurrectResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: ResurrectFailureReason }),
]);
```

and in the contract, after `dismiss`:

```ts
  resurrect: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      // Like launch: the initiating session, or explicit null.
      sessionId: z.string().nullable(),
    }),
    result: resurrectResult,
  }),
```

`api-to-frontend.ts`: import `ResurrectFailureReason`; add `const ResurrectErrorBody = z.object({ reason: ResurrectFailureReason, message: z.string() });` next to the others; after the `dismiss` route:

```ts
  resurrect: {
    method: 'POST',
    path: '/processes/:id/resurrect',
    pathParams: z.object({ id: ProcessIdParamSchema }),
    query: InstanceQuerySchema,
    body: z.object({
      sessionId: z.string().nullable().optional(),
    }).optional(),
    responses: {
      200: ProcessSchema,
      404: ResurrectErrorBody,
      409: ResurrectErrorBody,
    },
    summary: 'Save the work a failed run left on its host, then resume',
  },
```

`index.ts`: add `ResurrectFailureReason` to the failure-reason re-exports.

Build and regenerate (repo root):

```bash
pnpm --filter optio-contracts build
make codegen
git status --short packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated
```

Expected: generated files change (new `resurrect` method, `ResurrectParams`/`ResurrectResult`, process model fields).

`_engine_service.py`: add `"supportsResurrect", "hasUnsavedWork"` to `_PROCESS_WIRE_KEYS`; import `ResurrectParams, ResurrectResult`; add after `dismiss`:

```python
    # --------------------------------------------------------------- resurrect
    async def resurrect(self, params: ResurrectParams) -> ResurrectResult:
        outcome = await self._optio.resurrect(
            params.process_id, session_id=params.session_id,
        )
        if not outcome.ok:
            return ResurrectResult.model_validate(
                {"ok": False, "reason": outcome.reason}
            )
        return ResurrectResult.model_validate(
            {"ok": True, "process": _to_process_dict(outcome.proc)}
        )
```

AGENTS.md: optio-contracts (new route, RPC, reason enum, process fields), optio-core (RPC `resurrect`, wire keys).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run` in `packages/optio-contracts`; `../../.venv/bin/pytest -q tests/test_engine_service.py tests/test_resurrect.py` in `packages/optio-core`.
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-contracts/src packages/optio-contracts/AGENTS.md \
        packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated \
        packages/optio-core/src/optio_core/_engine_service.py packages/optio-core/tests/test_engine_service.py \
        packages/optio-core/AGENTS.md
git commit -m "feat(optio-contracts,optio-core): resurrect route, engine RPC and process fields"
```

(If `make codegen` also rewrote `packages/optio-agents-ui` generated files without content changes, do not add them.)

---

### Task 4: optio-api handler, adapters, streams

**Files:**
- Modify: `packages/optio-api/src/auth.ts` (`OptioAction`)
- Modify: `packages/optio-api/src/handlers.ts`
- Modify: `packages/optio-api/src/adapters/{express,fastify,nextjs-app,nextjs-pages}.ts`
- Modify: `packages/optio-api/src/stream-poller.ts`
- Modify: `packages/optio-api/AGENTS.md`, `packages/optio-api/README.md` (action list)
- Test: `packages/optio-api/src/__tests__/handlers-access.test.ts`, `packages/optio-api/src/__tests__/stream-poller.test.ts`

**Interfaces:**
- Consumes: Task 3 (`ResurrectFailureReason`, generated engine client `resurrect`).
- Produces: `resurrectProcess(ctx, query, id, sessionId?: string | null, access?: Access): Promise<ResurrectCommandResult>`; `OptioAction` includes `'resurrect'`; streamed process summaries carry `supportsResurrect`, `hasUnsavedWork`.

- [ ] **Step 1: Write the failing tests**

In `handlers-access.test.ts`: import `resurrectProcess`; add `resurrect: vi.fn(ok),` to `makeEngine()`; add to the `commands` array:

```ts
    ['resurrect', (ctx: OptioContext, id: string, a?: Access) => resurrectProcess(ctx, { prefix: PREFIX }, id, null, a)],
```

(The existing loop then checks 404-out-of-scope and 403-denied for resurrect, with the engine not called.) Add:

```ts
describe('resurrectProcess', () => {
  it('asks authorize with action resurrect and the process', async () => {
    await insertTree(1);
    const engine = makeEngine();
    const access = scoped(1);
    const result: any = await resurrectProcess(makeCtx(engine), { prefix: PREFIX }, 'root-1', 's1', access);
    expect(result.status).toBe(200);
    expect(access.authorize).toHaveBeenCalledWith(expect.objectContaining({
      action: 'resurrect',
      process: expect.objectContaining({ processId: 'root-1' }),
    }));
    expect(engine.resurrect).toHaveBeenCalledWith({ processId: 'root-1', sessionId: 's1' });
  });

  it('maps engine reasons to 404/409', async () => {
    await insertTree(1);
    const engine = makeEngine();
    engine.resurrect = vi.fn(() => ({ ok: false, reason: 'not-resurrectable' })) as any;
    const r1: any = await resurrectProcess(makeCtx(engine), { prefix: PREFIX }, 'root-1', null);
    expect(r1.status).toBe(409);
    expect(r1.body.reason).toBe('not-resurrectable');
    engine.resurrect = vi.fn(() => ({ ok: false, reason: 'not-found' })) as any;
    const r2: any = await resurrectProcess(makeCtx(engine), { prefix: PREFIX }, 'root-1', null);
    expect(r2.status).toBe(404);
  });
});
```

In `stream-poller.test.ts`, append (next to `describe('autoResumeScheduled propagation', ...)`, reusing that file's `db`, `PREFIX` and `waitUntil`):

```ts
describe('resurrect fields propagation', () => {
  async function insertRoot(extra: Record<string, unknown>) {
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId, processId: 'res', name: 'RES', rootId, parentId: null,
      depth: 0, order: 0, status: { state: 'failed' }, progress: { percent: null },
      cancellable: true, log: [], ...extra,
    });
    return rootId;
  }

  it('createTreePoller forwards supportsResurrect and hasUnsavedWork', async () => {
    const rootId = await insertRoot({ supportsResurrect: true, hasUnsavedWork: true });
    const events: any[] = [];
    const poller = createTreePoller({
      db, prefix: PREFIX, sendEvent: (d) => events.push(d), onError: () => {},
      rootId: rootId.toString(), baseDepth: 0,
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();
    const p = events.find((e) => e.type === 'update').processes[0];
    expect(p.supportsResurrect).toBe(true);
    expect(p.hasUnsavedWork).toBe(true);
  });

  it('createListPoller defaults both to false when absent', async () => {
    await insertRoot({});
    const events: any[] = [];
    const poller = createListPoller({
      db, prefix: PREFIX, sendEvent: (e) => events.push(e), onError: () => {},
    });
    poller.start();
    await waitUntil(() => events.some((e) => e.type === 'update'));
    poller.stop();
    const p = events.find((e) => e.type === 'update').processes.find((x: any) => x.processId === 'res');
    expect(p.supportsResurrect).toBe(false);
    expect(p.hasUnsavedWork).toBe(false);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (in `packages/optio-api`): `npx vitest run src/__tests__/handlers-access.test.ts src/__tests__/stream-poller.test.ts`
Expected: FAIL (`resurrectProcess` is not exported; fields missing).

- [ ] **Step 3: Implement**

`auth.ts`: `| 'launch' | 'cancel' | 'dismiss' | 'resync'` becomes `| 'launch' | 'cancel' | 'dismiss' | 'resurrect' | 'resync'`.

`handlers.ts`: import `ResurrectFailureReason as ResurrectFailureReasonType` with the other reason types; add:

```ts
export type ResurrectCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: ResurrectFailureReasonType; message: string } }
  | ForbiddenResult;

const RESURRECT_STATUS: Record<ResurrectFailureReasonType, 404 | 409> = {
  'not-found': 404,
  'not-resurrectable': 409,
  'no-resurrect-support': 409,
  'resurrect-in-progress': 409,
  'launch-blocked': 409,
  'shutting-down': 409,
};

const RESURRECT_MESSAGES: Record<ResurrectFailureReasonType, string> = {
  'not-found': 'Process not found',
  'not-resurrectable': 'Process has no unsaved work to resurrect, or is still active',
  'no-resurrect-support': 'This task does not support resurrect',
  'resurrect-in-progress': 'A resurrect of this process is already running',
  'launch-blocked': 'Launches matching this filter are currently blocked',
  'shutting-down': 'The engine is shutting down',
};

function resurrectFail(reason: ResurrectFailureReasonType): ResurrectCommandResult {
  return { status: RESURRECT_STATUS[reason], body: { reason, message: RESURRECT_MESSAGES[reason] } };
}

export async function resurrectProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  sessionId: string | null = null,
  access: Access = UNRESTRICTED,
): Promise<ResurrectCommandResult> {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const gate = await gateProcess(col(db, prefix), id, access, 'resurrect');
  if (!gate.ok) return gate.status === 404 ? resurrectFail('not-found') : FORBIDDEN;
  const engine = resolveOptioEngine(ctx, query);
  const result = await engine.resurrect({ processId: id, sessionId });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return resurrectFail(result.reason);
}
```

Adapters, next to each `dismiss` entry of the ts-rest router:

- `express.ts`:
```ts
    resurrect: async ({ params, query, body, req }) => {
      const result = await handlers.resurrectProcess(
        ctx, query, params.id, body?.sessionId ?? null, await access(req),
      );
      return result as any;
    },
```
- `fastify.ts`: same with `request` instead of `req`.
- `nextjs-app.ts` and `nextjs-pages.ts`:
```ts
    resurrect: async ({ params, query, body }) => {
      const result = await handlers.resurrectProcess(
        ctx, query, params.id, body?.sessionId ?? null, await currentAccess(),
      );
      return result as any;
    },
```

`stream-poller.ts`: after every `hasSavedState: p.hasSavedState ?? false,` line add

```ts
          supportsResurrect: p.supportsResurrect ?? false,
          hasUnsavedWork: p.hasUnsavedWork ?? false,
```

(keeping each site's indentation; `grep -n "hasSavedState" src/stream-poller.ts` lists them all).

AGENTS.md / README.md of optio-api: add the route, `resurrectProcess`, and `'resurrect'` in the `OptioAction` list ("single-process action like launch").

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/__tests__/handlers-access.test.ts src/__tests__/stream-poller.test.ts src/__tests__/adapters-access.test.ts src/__tests__/fastify-access.test.ts src/__tests__/handlers.test.ts` and `npx tsc --noEmit -p .`
Expected: all PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-api/src/auth.ts packages/optio-api/src/handlers.ts packages/optio-api/src/adapters \
        packages/optio-api/src/stream-poller.ts packages/optio-api/src/__tests__/handlers-access.test.ts \
        packages/optio-api/src/__tests__/stream-poller.test.ts packages/optio-api/AGENTS.md packages/optio-api/README.md
git commit -m "feat(optio-api): POST /processes/:id/resurrect under scope and authorize"
```

---

### Task 5: optio-ui Resurrect action

**Files:**
- Modify: `packages/optio-ui/src/process-state.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessActions.ts`
- Modify: `packages/optio-ui/src/components/LaunchControls.tsx`
- Modify: `packages/optio-ui/src/components/{ProcessItem,ProcessList,ProcessFilter,ProcessTreeView,ProcessDetailView}.tsx`
- Modify: `packages/optio-ui/src/index.ts` (export `isResurrectable` if `isResumable` is exported there)
- Modify: `packages/optio-ui/AGENTS.md`
- Test: `packages/optio-ui/src/__tests__/LaunchControls.test.tsx`, `packages/optio-ui/src/__tests__/process-state.test.ts`

**Interfaces:**
- Consumes: Task 3/4 (`processes.resurrect` route; fields on streamed processes).
- Produces: `isResurrectable(process): boolean`; `useProcessActions().resurrect(processId: string): void`; prop `onResurrect?: (processId: string) => void` on `LaunchControls`, `ProcessItem`, `ProcessList`, `FilteredProcessList`, `ProcessTreeView`.

- [ ] **Step 1: Write the failing tests**

`process-state.test.ts`, add (import `isResurrectable`):

```ts
describe('isResurrectable', () => {
  it('needs a launchable state, supportsResurrect and hasUnsavedWork', () => {
    expect(isResurrectable({ status: { state: 'failed' }, supportsResurrect: true, hasUnsavedWork: true })).toBe(true);
    expect(isResurrectable({ status: { state: 'running' }, supportsResurrect: true, hasUnsavedWork: true })).toBe(false);
    expect(isResurrectable({ status: { state: 'failed' }, supportsResurrect: false, hasUnsavedWork: true })).toBe(false);
    expect(isResurrectable({ status: { state: 'failed' }, supportsResurrect: true })).toBe(false);
    expect(isResurrectable(null)).toBe(false);
  });
});
```

`LaunchControls.test.tsx`: extend `renderWith` with an optional `onResurrect` and add:

```tsx
function renderResurrect(process: any) {
  const onLaunch = vi.fn();
  const onResurrect = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <LaunchControls process={process} onLaunch={onLaunch} onResurrect={onResurrect} size="small" />
    </I18nextProvider>,
  );
  return { onLaunch, onResurrect };
}

const failedWithWork = {
  _id: '9', status: { state: 'failed' },
  supportsResume: true, hasSavedState: true, supportsResurrect: true, hasUnsavedWork: true,
};

describe('LaunchControls resurrect', () => {
  it('primary button resurrects', () => {
    const { onResurrect, onLaunch } = renderResurrect(failedWithWork);
    fireEvent.click(screen.getByRole('button', { name: /resurrect/i }));
    expect(onResurrect).toHaveBeenCalledWith('9');
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('resume from last snapshot asks for confirmation first', async () => {
    const { onLaunch } = renderResurrect(failedWithWork);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    fireEvent.click(await screen.findByText(/resume from last snapshot/i));
    expect(onLaunch).not.toHaveBeenCalled();
    expect(await screen.findByText(/discards the unsaved work/i)).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: /discard and continue/i }));
    await vi.waitFor(() => expect(onLaunch).toHaveBeenCalledWith('9', { resume: true }));
  });

  it('restart asks for confirmation first', async () => {
    const { onLaunch } = renderResurrect(failedWithWork);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    fireEvent.click(await screen.findByText(/^restart$/i));
    fireEvent.click(await screen.findByRole('button', { name: /discard and continue/i }));
    await vi.waitFor(() => expect(onLaunch).toHaveBeenCalledWith('9', { resume: false }));
  });

  it('no resume item without saved state', async () => {
    renderResurrect({ ...failedWithWork, hasSavedState: false });
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    await screen.findByText(/^restart$/i);
    expect(screen.queryByText(/resume from last snapshot/i)).toBeNull();
  });

  it('falls back to the resume split button without onResurrect or without the flag', () => {
    const { onLaunch } = renderWith({ ...failedWithWork });
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(onLaunch).toHaveBeenCalledWith('9', { resume: true });
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (in `packages/optio-ui`): `npx vitest run src/__tests__/LaunchControls.test.tsx src/__tests__/process-state.test.ts`
Expected: FAIL (`isResurrectable` not exported; no resurrect button).

- [ ] **Step 3: Implement**

`process-state.ts`: add `supportsResurrect?: boolean; hasUnsavedWork?: boolean;` to `ProcessStateLike`, and

```ts
/** The host still holds a failed run's unsaved work and the task can save it. */
export function isResurrectable(process: ProcessStateLike | null | undefined): boolean {
  return isLaunchable(process)
    && process?.supportsResurrect === true
    && process?.hasUnsavedWork === true;
}
```

`useProcessActions.ts`: `const resurrectMutation = api.processes.resurrect.useMutation({ onSuccess: invalidate });` and in the returned object:

```ts
    resurrect: (processId: string) =>
      resurrectMutation.mutate({
        params: { id: processId },
        query: { database, prefix },
        body: { sessionId: getSessionId() },
      }),
```

`LaunchControls.tsx`: imports become

```tsx
import { Button, Dropdown, Modal, Space, Tooltip, Popconfirm } from 'antd';
import { DownOutlined, MedicineBoxOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { isLaunchable, isResumable, isResurrectable } from '../process-state.js';
```

add to `LaunchControlsProps`:

```tsx
  /** Save the work a failed run left on its host, then resume. When given
   *  and the process is resurrectable, Resurrect becomes the primary action;
   *  Resume-from-snapshot and Restart move to the menu behind a confirmation. */
  onResurrect?: (processId: string) => void;
```

destructure `onResurrect` in `LaunchControls`, and right after the `denyReason` branch:

```tsx
  // Case 0: resurrect — the host still holds a failed run's unsaved work.
  if (onResurrect && isResurrectable(process)) {
    return (
      <ResurrectControls
        process={process} onLaunch={onLaunch} onResurrect={onResurrect}
        size={size} iconStyle={iconStyle}
      />
    );
  }
```

and the component (same file, below `LaunchControls`; a separate component because `Modal.useModal` is a hook):

```tsx
function ResurrectControls({ process, onLaunch, onResurrect, size, iconStyle }: {
  process: any;
  onLaunch: (processId: string, opts?: { resume?: boolean }) => void;
  onResurrect: (processId: string) => void;
  size: ButtonProps['size'];
  iconStyle: { fontSize: number } | undefined;
}) {
  const { t } = useTranslation();
  const [modal, contextHolder] = Modal.useModal();
  const confirmDiscard = (title: string, action: () => void) => {
    modal.confirm({
      title,
      content: t('processes.discardUnsavedWork', {
        defaultValue: 'This discards the unsaved work left by the failed run.',
      }),
      okText: t('processes.discardAndContinue', { defaultValue: 'Discard and continue' }),
      okButtonProps: { danger: true },
      onOk: action,
    });
  };
  const resumeLabel = t('processes.resumeFromSnapshot', { defaultValue: 'Resume from last snapshot' });
  const restartLabel = t('processes.restartDiscarding', { defaultValue: 'Restart' });
  const items: MenuProps['items'] = [];
  if (isResumable(process)) {
    items.push({
      key: 'resume',
      icon: <PlayCircleOutlined />,
      label: resumeLabel,
      onClick: () => confirmDiscard(resumeLabel, () => onLaunch(process._id, { resume: true })),
    });
  }
  items.push({
    key: 'restart',
    icon: <ReloadOutlined />,
    label: restartLabel,
    onClick: () => confirmDiscard(restartLabel, () => onLaunch(process._id, { resume: false })),
  });
  return (
    <>
      {contextHolder}
      <Space.Compact>
        <Tooltip title={t('processes.resurrectHint', {
          defaultValue: 'Save the work left by the failed run, then resume',
        })}>
          <Button
            type="text"
            size={size}
            aria-label={t('processes.resurrect', { defaultValue: 'Resurrect' })}
            icon={<MedicineBoxOutlined style={iconStyle} />}
            style={{ color: '#fa8c16' }}
            onClick={(e) => {
              e.preventDefault();
              onResurrect(process._id);
            }}
          />
        </Tooltip>
        <Dropdown menu={{ items }} trigger={['click']}>
          <Tooltip title={t('processes.moreOptions', { defaultValue: 'More options' })}>
            <Button type="text" size={size} icon={<DownOutlined style={iconStyle} />} />
          </Tooltip>
        </Dropdown>
      </Space.Compact>
    </>
  );
}
```

Update the doc comment above `LaunchControls` with the new first case.

Pass-through:
- `ProcessItem.tsx`: prop `onResurrect?: (id: string) => void;`, destructure it, pass `onResurrect={onResurrect}` to `LaunchControls`.
- `ProcessList.tsx`: prop `onResurrect?: (processId: string) => void;`, destructure, pass to `ProcessItem`.
- `ProcessFilter.tsx` `FilteredProcessListProps`: add `onResurrect?: (processId: string) => void;` (already spread into `ProcessList` by `...rest`).
- `ProcessTreeView.tsx`: add `supportsResurrect?: boolean; hasUnsavedWork?: boolean;` to `ProcessNode` next to `hasSavedState`; prop `onResurrect?: (processId: string) => void;`; add parameter `onResurrect: ((id: string) => void) | undefined` to `treeNodeToDataNode` after `onLaunch`; render `<LaunchControls process={node as any} onLaunch={onLaunch} onResurrect={onResurrect} size="small" />`; pass `onResurrect` in the recursive call and in `ProcessTreeView`'s call.
- `ProcessDetailView.tsx`: `const { launch, cancel, resurrect } = useProcessActions();` and on `ProcessTreeView`: `onResurrect={readOnly ? undefined : (id) => resurrect(id)}`.

`AGENTS.md` (optio-ui): `LaunchControls` cases and `onResurrect`; `useProcessActions().resurrect`; `isResurrectable`; `onResurrect` on the list/tree components.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/__tests__/LaunchControls.test.tsx src/__tests__/process-state.test.ts` then `npx tsc --noEmit -p .` and `pnpm --filter optio-ui build`.
Expected: PASS, no type errors, build OK.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-ui/src/process-state.ts packages/optio-ui/src/hooks/useProcessActions.ts \
        packages/optio-ui/src/components/{LaunchControls,ProcessItem,ProcessList,ProcessFilter,ProcessTreeView,ProcessDetailView}.tsx \
        packages/optio-ui/src/index.ts packages/optio-ui/src/__tests__/LaunchControls.test.tsx \
        packages/optio-ui/src/__tests__/process-state.test.ts packages/optio-ui/AGENTS.md
git commit -m "feat(optio-ui): Resurrect as the primary launch action for failed runs with unsaved work"
```

---

### Task 6: optio-claudecode capture bookkeeping

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/pending_captures.py`
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` (capture split, live points, credentials guard, keep taskdir on failed capture)
- Test: `packages/optio-claudecode/tests/test_capture_bookkeeping.py`

**Interfaces:**
- Consumes: Task 1 (`ctx.mark_unsaved_work`, `ctx.clear_unsaved_work`, `ctx.store_blob(file_id=)`).
- Produces: `pending_captures.record_pending_capture(db, prefix, *, process_id: str, session_blob_id: ObjectId, workdir_blob_id: ObjectId) -> None`, `load_pending_capture(db, prefix, process_id: str) -> dict | None`, `delete_pending_capture(db, prefix, process_id: str) -> None`; `session._store_workdir_snapshot(ctx, host, *, end_state: str, workdir_exclude: list[str] | None, session_blob_id: ObjectId) -> None`; `session._on_agent_live(ctx, config) -> None`; `session._cleanup_after_capture(host, *, capture_failed: bool, cancelled: bool) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-claudecode/tests/test_capture_bookkeeping.py`:

```python
"""Capture bookkeeping for Resurrect: pending-capture record, unsaved-work
flag, credentials guard, keeping the taskdir after a failed capture."""

import os

import pytest
from bson import ObjectId

import optio_claudecode.session as S
from optio_claudecode import pending_captures as PC
from optio_claudecode.snapshots import load_latest_snapshot
from optio_host.host import LocalHost


def _local_host(tmp_path):
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)
    with open(os.path.join(host.workdir, "CLAUDE.md"), "w") as f:
        f.write("work\n")
    return host


async def _flags(mongo_db, ctx, **fields):
    await mongo_db["test_processes"].update_one({"_id": ctx._process_oid}, {"$set": fields})


@pytest.mark.asyncio
async def test_pending_capture_record_roundtrip(mongo_db):
    s, w = ObjectId(), ObjectId()
    await PC.record_pending_capture(mongo_db, "test", process_id="p", session_blob_id=s, workdir_blob_id=w)
    rec = await PC.load_pending_capture(mongo_db, "test", "p")
    assert rec["sessionBlobId"] == s and rec["workdirBlobId"] == w
    await PC.delete_pending_capture(mongo_db, "test", "p")
    assert await PC.load_pending_capture(mongo_db, "test", "p") is None


@pytest.mark.asyncio
async def test_store_workdir_snapshot_records_then_clears(mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True, hasUnsavedWork=True)
    host = _local_host(tmp_path)
    seen = {}
    real_stream = S._stream_archive_to_blob

    async def _spy(ctx_, host_, exclude, wwriter, **kw):
        seen["pending"] = await PC.load_pending_capture(mongo_db, "test", ctx.process_id)
        return await real_stream(ctx_, host_, exclude, wwriter, **kw)
    monkeypatch.setattr(S, "_stream_archive_to_blob", _spy)

    session_id = ObjectId()
    await S._store_workdir_snapshot(ctx, host, end_state="cancelled", workdir_exclude=[".env"], session_blob_id=session_id)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == session_id
    assert seen["pending"]["workdirBlobId"] == snap["workdirBlobId"]
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasSavedState"] is True
    assert doc["hasUnsavedWork"] is False


@pytest.mark.asyncio
async def test_interrupted_workdir_store_leaves_pending_record(mongo_db, tmp_path, ctx_and_captures, monkeypatch):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True, hasUnsavedWork=True)
    host = _local_host(tmp_path)

    async def _boom(*a, **kw):
        raise RuntimeError("cut off")
    monkeypatch.setattr(S, "_stream_archive_to_blob", _boom)
    with pytest.raises(RuntimeError):
        await S._store_workdir_snapshot(ctx, host, end_state="cancelled", workdir_exclude=None, session_blob_id=ObjectId())
    rec = await PC.load_pending_capture(mongo_db, "test", ctx.process_id)
    assert rec is not None
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasUnsavedWork"] is True


@pytest.mark.asyncio
async def test_credentials_guard_clears_unsaved_work(mongo_db, tmp_path, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResume=True, supportsResurrect=True, hasUnsavedWork=True)
    host = _local_host(tmp_path)  # no home/.claude/.credentials.json
    await S._capture_snapshot(ctx, host, end_state="done", workdir_exclude=None)
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasUnsavedWork"] is False


class _Cfg:
    def __init__(self, supports_resume):
        self.supports_resume = supports_resume


@pytest.mark.asyncio
async def test_on_agent_live_marks_only_resumable_tasks(mongo_db, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _flags(mongo_db, ctx, supportsResurrect=True)
    await S._on_agent_live(ctx, _Cfg(False))
    assert "hasUnsavedWork" not in await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    await S._on_agent_live(ctx, _Cfg(True))
    assert (await mongo_db["test_processes"].find_one({"_id": ctx._process_oid}))["hasUnsavedWork"] is True


class _CleanupHost:
    def __init__(self):
        self.calls = []
        self.taskdir = "/t"

    async def cleanup_taskdir(self, aggressive):
        self.calls.append(aggressive)


@pytest.mark.asyncio
async def test_cleanup_after_capture_keeps_taskdir_when_capture_failed():
    h = _CleanupHost()
    await S._cleanup_after_capture(h, capture_failed=True, cancelled=False)
    assert h.calls == []
    await S._cleanup_after_capture(h, capture_failed=False, cancelled=True)
    assert h.calls == [True]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (in `packages/optio-claudecode`): `../../.venv/bin/pytest -q tests/test_capture_bookkeeping.py`
Expected: FAIL (`cannot import name 'pending_captures'`).

- [ ] **Step 3: Implement**

Create `pending_captures.py`:

```python
"""MongoDB `{prefix}_claudecode_pending_captures`: the blobs a capture is
writing, recorded before it streams the workdir and deleted once the
snapshot record is inserted. A capture that is cut off (force-cancel past
the grace) leaves its record behind, so Resurrect can delete exactly that
partial workdir blob. One document per processId.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

PENDING_CAPTURE_COLLECTION_SUFFIX = "_claudecode_pending_captures"


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    return db[f"{prefix}{PENDING_CAPTURE_COLLECTION_SUFFIX}"]


async def record_pending_capture(
    db: AsyncIOMotorDatabase, prefix: str, *,
    process_id: str, session_blob_id: ObjectId, workdir_blob_id: ObjectId,
) -> None:
    await _collection(db, prefix).replace_one(
        {"processId": process_id},
        {
            "processId": process_id,
            "sessionBlobId": session_blob_id,
            "workdirBlobId": workdir_blob_id,
            "startedAt": datetime.now(timezone.utc),
        },
        upsert=True,
    )


async def load_pending_capture(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> dict | None:
    return await _collection(db, prefix).find_one({"processId": process_id})


async def delete_pending_capture(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> None:
    await _collection(db, prefix).delete_many({"processId": process_id})
```

`session.py`:

1. Imports: `from bson import ObjectId` (if not imported) and `from optio_claudecode.pending_captures import record_pending_capture, delete_pending_capture`.

2. Add helpers above `_capture_snapshot`:

```python
async def _on_agent_live(ctx: ProcessContext, config: ClaudeCodeTaskConfig) -> None:
    """claude is up: from here the workdir holds work the last snapshot lacks,
    so a failure leaves something Resurrect can save."""
    if config.supports_resume:
        await ctx.mark_unsaved_work()


async def _cleanup_after_capture(host: Host, *, capture_failed: bool, cancelled: bool) -> None:
    """Remove the task directory, unless the capture failed: then it stays on
    the host (hasUnsavedWork stays set) so Resurrect can save it later."""
    if capture_failed:
        _LOG.warning(
            "snapshot capture failed; keeping task directory %s for Resurrect",
            getattr(host, "taskdir", "?"),
        )
        return
    async with _traced(f"finally: cleanup_taskdir aggressive={cancelled}"):
        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
```

3. Live points: directly after `launched_handle = handle` in `_claudecode_body` (the iframe body, near line 412) and after `launched_handle = handle` that follows `ctx.report_progress(None, f"Launching {AGENT_INFO.name} (conversation)…")` (near line 626), add `await _on_agent_live(ctx, config)` at the same indentation. Do not add it at the respawn site inside the restart handler (near line 850): the flag is already set there.

4. In `_capture_snapshot`'s credentials-guard branch, before `return`:

```python
        # A snapshot without credentials is refused, so a later Resurrect
        # could not save this workdir either: nothing resurrectable is left.
        await ctx.clear_unsaved_work()
```

5. Split `_capture_snapshot`: keep steps 0-4b; replace steps 5-8 with

```python
    await _store_workdir_snapshot(
        ctx, host,
        end_state=end_state,
        workdir_exclude=workdir_exclude,
        session_blob_id=session_blob_id,
    )


async def _store_workdir_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_blob_id: ObjectId,
) -> None:
    """Second half of a capture: archive the workdir, insert the snapshot
    record, prune, flag the state. Resurrect calls it alone when a cut-off
    capture had already stored the session blob and removed home/.claude.
    """
    # 4c. Record the blobs before streaming: a capture cut off past the cancel
    # grace leaves this record, naming the partial workdir blob to delete.
    workdir_blob_id = ObjectId()
    await record_pending_capture(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        session_blob_id=session_blob_id,
        workdir_blob_id=workdir_blob_id,
    )
```

followed by the existing steps 5-8 moved verbatim, with two changes: `ctx.store_blob("workdir")` becomes `ctx.store_blob("workdir", file_id=workdir_blob_id)` (and the line `workdir_blob_id = wwriter.file_id` is removed), and after the `insert_snapshot` block add

```python
    await delete_pending_capture(ctx._db, ctx._prefix, ctx.process_id)
```

and after `await ctx.mark_has_saved_state()` (step 8) add

```python
    await ctx.clear_unsaved_work()
```

6. Teardown in `run_claudecode_session`'s `finally`: declare `capture_failed = False` just before `if config.supports_resume and launched_handle is not None:`; in its `except Exception as exc:` set `capture_failed = True` and change the log text to `"snapshot capture failed; keeping the task directory for Resurrect"`; replace the whole `async with _traced(f"finally: cleanup_taskdir aggressive={cancelled}"): ...` block with

```python
            await _cleanup_after_capture(
                host, capture_failed=capture_failed, cancelled=cancelled,
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `../../.venv/bin/pytest -q tests/test_capture_bookkeeping.py tests/test_rescue_orphan.py tests/test_cancel_trace.py tests/test_session_blob_hooks.py tests/test_session_local.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/pending_captures.py packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_capture_bookkeeping.py
git commit -m "feat(optio-claudecode): track unsaved work and in-flight capture blobs; keep the taskdir after a failed capture"
```

---

### Task 7: optio-claudecode resurrect hook

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/resurrect.py`
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` (`create_claudecode_task` passes the hook)
- Modify: `packages/optio-claudecode/AGENTS.md`
- Test: `packages/optio-claudecode/tests/test_resurrect_hook.py`

**Interfaces:**
- Consumes: Task 1 (`NothingToResurrect`, `TaskInstance.resurrect`), Task 6 (`_store_workdir_snapshot`, pending captures), existing `_capture_snapshot`, `_build_host`, `host_actions.teardown_session_tree`, `snapshots.load_latest_snapshot`.
- Produces: `resurrect.resurrect_claudecode_session(ctx, config) -> None`; `resurrect.find_unreferenced_session_blob(db, prefix, *, process_oid: ObjectId, process_id: str) -> ObjectId | None`; `create_claudecode_task(...)` returns a TaskInstance whose `resurrect` is set when `config.supports_resume`.

- [ ] **Step 1: Write the failing tests**

Create `packages/optio-claudecode/tests/test_resurrect_hook.py`:

```python
"""resurrect_claudecode_session against a real LocalHost workdir + GridFS."""

import os

import pytest
from bson import ObjectId

import optio_claudecode.session as S
from optio_claudecode import pending_captures as PC
from optio_claudecode import resurrect as R
from optio_claudecode import ClaudeCodeTaskConfig
from optio_claudecode.snapshots import insert_snapshot, load_latest_snapshot
from optio_core import NothingToResurrect
from optio_host.host import LocalHost


def _config():
    return ClaudeCodeTaskConfig(
        consumer_instructions="(resurrect test)",
        fs_isolation=False,
        supports_resume=True,
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )


@pytest.fixture
def host(tmp_path, monkeypatch):
    taskdir = str(tmp_path / "task")
    os.makedirs(taskdir, exist_ok=True)
    h = LocalHost(taskdir=taskdir)
    os.makedirs(h.workdir, exist_ok=True)
    monkeypatch.setattr(S, "_build_host", lambda config, process_id: h)

    async def _no_orphan(host_, tmux_path, socket, session):
        return False
    monkeypatch.setattr(S.host_actions, "tmux_session_alive", _no_orphan)
    return h


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


async def _prepare(mongo_db, ctx):
    await mongo_db["test_processes"].update_one({"_id": ctx._process_oid}, {"$set": {
        "supportsResume": True, "supportsResurrect": True, "hasUnsavedWork": True,
        "status.state": "failed",
    }})


async def _blob(ctx, name, payload=b"tar"):
    async with ctx.store_blob(name) as w:
        await w.write(payload)
    return w.file_id


async def _snapshot_before_now(mongo_db, ctx, *, session_blob_id, workdir_blob_id, end_state="cancelled"):
    """Insert a snapshot and move its capturedAt one second back, so blobs
    stored after it are strictly newer (BSON dates have millisecond
    resolution; in production minutes or hours separate the two)."""
    from datetime import timedelta
    doc = await insert_snapshot(mongo_db, prefix="test", process_id=ctx.process_id, end_state=end_state,
                                session_blob_id=session_blob_id, workdir_blob_id=workdir_blob_id,
                                deliverables_emitted=[])
    await mongo_db["test_claudecode_session_snapshots"].update_one(
        {"_id": doc["_id"]}, {"$set": {"capturedAt": doc["capturedAt"] - timedelta(seconds=1)}},
    )


@pytest.mark.asyncio
async def test_resurrect_with_home_claude_present(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    _write(f"{host.workdir}/home/.claude/.credentials.json", '{"t": 1}')
    await R.resurrect_claudecode_session(ctx, _config())
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["endState"] == "resurrected"
    assert not os.path.exists(host.taskdir)
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasSavedState"] is True


@pytest.mark.asyncio
async def test_resurrect_old_code_shape(mongo_db, host, ctx_and_captures):
    """2026-10-06: session blob stored, home/.claude removed, workdir archive
    cut off (partial chunks, no fs.files), no pending record (old code)."""
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    older = await _blob(ctx, "session", b"old")
    old_wd = await _blob(ctx, "workdir", b"oldwd")
    await _snapshot_before_now(mongo_db, ctx, session_blob_id=older, workdir_blob_id=old_wd)
    orphan = await _blob(ctx, "session", b"new-session")
    partial = ObjectId()
    await mongo_db["fs.chunks"].insert_one({"files_id": partial, "n": 0, "data": b"partial"})
    _write(f"{host.workdir}/CLAUDE.md", "six hours of work")

    await R.resurrect_claudecode_session(ctx, _config())

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == orphan
    assert snap["endState"] == "resurrected"
    # Unrecorded partial chunks are not ours to guess at: left alone.
    assert await mongo_db["fs.chunks"].count_documents({"files_id": partial}) == 1


@pytest.mark.asyncio
async def test_pending_record_partial_blob_deleted(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    orphan = await _blob(ctx, "session", b"s")
    partial = ObjectId()
    await mongo_db["fs.chunks"].insert_one({"files_id": partial, "n": 0, "data": b"partial"})
    await PC.record_pending_capture(mongo_db, "test", process_id=ctx.process_id,
                                    session_blob_id=orphan, workdir_blob_id=partial)
    _write(f"{host.workdir}/CLAUDE.md", "work")

    await R.resurrect_claudecode_session(ctx, _config())

    assert await mongo_db["fs.chunks"].count_documents({"files_id": partial}) == 0
    assert await PC.load_pending_capture(mongo_db, "test", ctx.process_id) is None
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id)
    assert snap["sessionBlobId"] == orphan


@pytest.mark.asyncio
async def test_fallback_ignores_session_blob_older_than_latest_snapshot(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    # An unreferenced session blob from before the latest snapshot (e.g. an
    # on_session_saved blob of an earlier run) must not be picked. Uploaded
    # before insert_snapshot, so its uploadDate <= capturedAt.
    await _blob(ctx, "session", b"stale")
    s = await _blob(ctx, "session", b"s")
    w = await _blob(ctx, "workdir", b"w")
    await insert_snapshot(mongo_db, prefix="test", process_id=ctx.process_id, end_state="done",
                          session_blob_id=s, workdir_blob_id=w, deliverables_emitted=[])
    _write(f"{host.workdir}/CLAUDE.md", "work")
    with pytest.raises(NothingToResurrect, match="session state not found"):
        await R.resurrect_claudecode_session(ctx, _config())


@pytest.mark.asyncio
async def test_resurrect_without_credentials_saves_nothing(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    _write(f"{host.workdir}/CLAUDE.md", "work")
    _write(f"{host.workdir}/home/.claude/settings.json", "{}")  # no .credentials.json
    await R.resurrect_claudecode_session(ctx, _config())
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None
    doc = await mongo_db["test_processes"].find_one({"_id": ctx._process_oid})
    assert doc["hasUnsavedWork"] is False


@pytest.mark.asyncio
async def test_empty_workdir_is_nothing_to_resurrect(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    with pytest.raises(NothingToResurrect, match="workdir"):
        await R.resurrect_claudecode_session(ctx, _config())
    assert await load_latest_snapshot(mongo_db, prefix="test", process_id=ctx.process_id) is None


@pytest.mark.asyncio
async def test_missing_workdir_is_nothing_to_resurrect(mongo_db, host, ctx_and_captures):
    ctx, _cap, _flag = ctx_and_captures
    await _prepare(mongo_db, ctx)
    os.rmdir(host.workdir)
    with pytest.raises(NothingToResurrect, match="workdir"):
        await R.resurrect_claudecode_session(ctx, _config())


def test_factory_attaches_hook_only_for_resumable_configs():
    cfg = _config()
    ti = S.create_claudecode_task(process_id="p", name="P", config=cfg)
    assert ti.resurrect is not None
    cfg2 = ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False, supports_resume=False)
    assert S.create_claudecode_task(process_id="p2", name="P2", config=cfg2).resurrect is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `../../.venv/bin/pytest -q tests/test_resurrect_hook.py`
Expected: FAIL (`cannot import name 'resurrect'`).

- [ ] **Step 3: Implement**

Create `resurrect.py`:

```python
"""Resurrect hook for optio-claudecode (TaskInstance.resurrect).

Saves the work a failed run left on its host as a fresh snapshot, then
removes the task directory; optio-core resumes from that snapshot afterwards.
Spec: docs/2026-10-07-resurrect-failed-session-design.md
"""

from __future__ import annotations

import re
import shlex

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core import NothingToResurrect
from optio_core.context import ProcessContext

from optio_claudecode import host_actions
from optio_claudecode import session as S
from optio_claudecode.pending_captures import delete_pending_capture, load_pending_capture
from optio_claudecode.snapshots import _collection as _snapshots, load_latest_snapshot


async def find_unreferenced_session_blob(
    db: AsyncIOMotorDatabase, prefix: str, *, process_oid: ObjectId, process_id: str,
) -> ObjectId | None:
    """Newest session blob of this process stored after its latest snapshot
    and referenced by no snapshot: what a capture cut off after its session
    step left behind."""
    latest = await load_latest_snapshot(db, prefix=prefix, process_id=process_id)
    query: dict = {
        "metadata.processId": str(process_oid),
        "metadata.prefix": prefix,
        "metadata.name": "session",
    }
    if latest is not None:
        query["uploadDate"] = {"$gt": latest["capturedAt"]}
    referenced = set(await _snapshots(db, prefix).distinct(
        "sessionBlobId", {"processId": process_id},
    ))
    async for f in db["fs.files"].find(query).sort("uploadDate", -1):
        if f["_id"] not in referenced:
            return f["_id"]
    return None


async def _workdir_has_content(host) -> bool:
    w = shlex.quote(host.workdir)
    r = await host.run_command(
        f'test -d {w} && [ -n "$(ls -A {w} 2>/dev/null)" ] && echo YES || true',
        cwd="/",
    )
    return "YES" in r.stdout


async def _home_claude_present(host) -> bool:
    r = await host.run_command(
        f"test -d {shlex.quote(host.workdir.rstrip('/') + '/home/.claude')} && echo YES || true",
        cwd="/",
    )
    return "YES" in r.stdout


async def _stop_leftovers(host) -> None:
    """Kill what the failed run may have left: the tmux/ttyd/claude tree on
    the task's socket, and `tail -F <workdir>/optio.log` readers. The pkill
    pattern is anchored and escaped so it cannot match the shell running it."""
    tmux_path = await host_actions._require_tmux(host)
    socket = host_actions._tmux_socket_path(host)
    if await host_actions.tmux_session_alive(host, tmux_path, socket, "optio"):
        await host_actions.teardown_session_tree(
            host,
            tmux_path=tmux_path,
            tmux_socket=socket,
            tmux_session="optio",
            claude_path=S._claude_bin_path(host),
            ttyd_handle=None,
            aggressive=True,
        )
    log_path = f"{host.workdir.rstrip('/')}/optio.log"
    pattern = "^tail -F -n \\+1 " + re.escape(log_path) + "$"
    await host.run_command(f"pkill -f -- {shlex.quote(pattern)} || true", cwd="/")


async def resurrect_claudecode_session(ctx: ProcessContext, config) -> None:
    host = S._build_host(config, ctx.process_id)
    await host.connect()
    try:
        if not await _workdir_has_content(host):
            raise NothingToResurrect("workdir no longer on the host (or empty)")
        ctx.report_progress(None, "Resurrecting: stopping leftovers of the failed run…")
        await _stop_leftovers(host)

        pending = await load_pending_capture(ctx._db, ctx._prefix, ctx.process_id)
        if pending is not None:
            await ctx.delete_blob(pending["workdirBlobId"])
            await delete_pending_capture(ctx._db, ctx._prefix, ctx.process_id)

        if await _home_claude_present(host):
            await S._capture_snapshot(
                ctx, host,
                end_state="resurrected",
                workdir_exclude=config.workdir_exclude,
                session_blob_encrypt=config.session_blob_encrypt,
            )
        else:
            session_blob_id = await find_unreferenced_session_blob(
                ctx._db, ctx._prefix,
                process_oid=ctx._process_oid, process_id=ctx.process_id,
            )
            if session_blob_id is None:
                raise NothingToResurrect("session state not found")
            await S._store_workdir_snapshot(
                ctx, host,
                end_state="resurrected",
                workdir_exclude=config.workdir_exclude,
                session_blob_id=session_blob_id,
            )
        await host.cleanup_taskdir(aggressive=False)
    finally:
        await host.disconnect()
```

Notes for the implementer:
- `ctx.delete_blob` must delete the chunks even when no `fs.files` document exists (motor's `bucket.delete` deletes chunks before raising `NoFile`, and `delete_blob` swallows `NoFile`); `test_pending_record_partial_blob_deleted` pins this.
- If `_capture_snapshot` returns without saving (credentials guard), it has cleared `hasUnsavedWork` (Task 6); the hook then removes the taskdir and returns (`test_resurrect_without_credentials_saves_nothing`). optio-core then launches a resume from the previous snapshot; acceptable, because a run without credentials has nothing resumable to save.

`session.py` `create_claudecode_task`:

```python
    async def _resurrect(ctx: ProcessContext) -> None:
        from optio_claudecode.resurrect import resurrect_claudecode_session
        await resurrect_claudecode_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        ...existing fields...,
        supports_resume=config.supports_resume,
        resurrect=_resurrect if config.supports_resume else None,
        metadata=metadata or {},
    )
```

`packages/optio-claudecode/AGENTS.md`: a "Resurrect" section: when the flag is set and cleared, the pending-capture collection, the hook's steps and fallback, failed captures keep the taskdir.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `../../.venv/bin/pytest -q tests/test_resurrect_hook.py tests/test_capture_bookkeeping.py tests/test_rescue_orphan.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/resurrect.py packages/optio-claudecode/src/optio_claudecode/session.py \
        packages/optio-claudecode/tests/test_resurrect_hook.py packages/optio-claudecode/AGENTS.md
git commit -m "feat(optio-claudecode): resurrect hook saves a failed run's workdir (with session-blob fallback)"
```

---

### Task 8: optio-dashboard wiring, root docs, full suites

**Files:**
- Modify: `packages/optio-dashboard/src/app/App.tsx` (and any other place that passes `onLaunch` to optio-ui components: `grep -rn "onLaunch" packages/optio-dashboard/src`)
- Modify: `AGENTS.md` (root unified reference: process fields, `TaskInstance.resurrect`, `Optio.resurrect`, route, RPC, `LaunchControls`)
- Modify: `packages/optio-dashboard/AGENTS.md` if it documents the wiring

**Interfaces:**
- Consumes: Task 5 (`useProcessActions().resurrect`, `onResurrect` props).

- [ ] **Step 1: Wire the dashboard**

Where `App.tsx` gets `launch` from `useProcessActions()`, also take `resurrect`, and next to `onLaunch={live ? launch : undefined}` add `onResurrect={live ? resurrect : undefined}`. Same at every other `onLaunch` site found by the grep.

- [ ] **Step 2: Type-check and build**

Run: `pnpm --filter optio-ui build && pnpm --filter optio-dashboard build`
Expected: builds without errors.

- [ ] **Step 3: Root AGENTS.md**

Add `supportsResurrect`/`hasUnsavedWork` to the process-document reference, `TaskInstance.resurrect` and `Optio.resurrect` to the core reference, the `POST /processes/:id/resurrect` route and `resurrect` RPC to the API/contract reference, and `onResurrect` to the `LaunchControls` reference (line ~912 describes the split button: add the resurrect case).

- [ ] **Step 4: Full suites (one package at a time; superego)**

```bash
export MONGO_URL=mongodb://127.0.0.1:27217 REDIS_URL=redis://127.0.0.1:6579
for p in optio-contracts optio-api optio-ui optio-dashboard; do (cd packages/$p && npx vitest run) || echo "FAIL $p"; done
for p in optio-core optio-claudecode; do (cd packages/$p && ../../.venv/bin/pytest -q -n 4 -m "not serial" && ../../.venv/bin/pytest -q -m serial) || echo "FAIL $p"; done
```

Expected: no `FAIL` lines. For any failure, fix the code it points at and commit the fix separately as `fix(<pkg>): ...`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-dashboard/src AGENTS.md packages/optio-dashboard/AGENTS.md
git commit -m "feat(optio-dashboard): wire Resurrect; docs: resurrect in the unified reference"
```

---

### Task 9: End-to-end check on superego

Throwaway script, not committed. Proves the whole path: dashboard API -> engine RPC over redis -> hook -> resume, and the button in a real browser.

- [ ] **Step 1: Engine + dashboard against the private mongo/redis**

Write `/tmp/resurrect-e2e/engine.py` (run with `~/resurrect/optio/.venv/bin/python`):

```python
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def execute(ctx):
    # Test-only marker: proves the resume ran and with which flag.
    await ctx._db["e2e_marks"].insert_one({"resume": ctx.resume})


async def hook(ctx):
    ctx.report_progress(None, "hook ran")
    await ctx.mark_has_saved_state()


async def main():
    db = AsyncIOMotorClient("mongodb://127.0.0.1:27217")["resurrect_e2e"]
    fw = Optio()
    await fw.init(mongo_db=db, prefix="e2e", redis_url="redis://127.0.0.1:6579")
    proc = await fw.adhoc_define(TaskInstance(
        execute=execute, process_id="e2e-task", name="E2E", supports_resume=True, resurrect=hook,
    ))
    await db["e2e_processes"].update_one({"_id": proc["_id"]}, {"$set": {
        "status.state": "failed", "hasUnsavedWork": True, "hasSavedState": True,
    }})
    await fw.run()


asyncio.run(main())
```

Dashboard API (serves the built frontend too), from the repo root:

```bash
pnpm --filter optio-dashboard build
MONGODB_URL=mongodb://127.0.0.1:27217/resurrect_e2e REDIS_URL=redis://127.0.0.1:6579 \
  OPTIO_PASSWORD=e2e-pass PORT=3201 pnpm --filter optio-dashboard start
```

Log in: `curl -s -c /tmp/resurrect-e2e/cookies -H 'content-type: application/json' -d '{"email":"admin@optio.local","password":"e2e-pass"}' http://127.0.0.1:3201/api/auth/sign-in/email`.

- [ ] **Step 2: API path**

`curl -s -b /tmp/resurrect-e2e/cookies -X POST 'http://127.0.0.1:3201/api/processes/e2e-task/resurrect?database=resurrect_e2e&prefix=e2e' -H 'content-type: application/json' -d '{"sessionId":null}'`
Expected: 200 with the process. Then poll (60 s ceiling) until all hold:
- `GET /api/processes/e2e-task?database=resurrect_e2e&prefix=e2e` (same cookie): log contains "Resurrect requested" and "Resurrected: unsaved work saved; resuming"; `hasUnsavedWork` is false;
- `~/resurrect/optio/.venv/bin/python -c 'import pymongo; print(list(pymongo.MongoClient("mongodb://127.0.0.1:27217")["resurrect_e2e"]["e2e_marks"].find({}, {"_id": 0})))'` prints `[{'resume': True}]`.

- [ ] **Step 3: Button in the browser**

Reset the process to failed + `hasUnsavedWork=True`; open `http://127.0.0.1:3201/` in `/usr/bin/chromium` (headless, driven over CDP like `~/excavator-shots/snap.mjs`, or `playwright-core` with `executablePath: '/usr/bin/chromium'`), log in as `admin@optio.local` / `e2e-pass`, and screenshot the process row: the primary launch button is the Resurrect icon; open the menu: "Resume from last snapshot" and "Restart"; click Restart: the confirmation appears. Save the screenshots under `/tmp/resurrect-e2e/`.

- [ ] **Step 4: Clean up**

Stop the engine and the dashboard API; drop the `resurrect_e2e` database. Record the outcome (commands run, screenshots) in the review notes for the owner.

---

## After the plan (owner decisions, not part of execution)

- Push `csillag/resurrect` / merge to `main`.
- Releases (optio `docs/release-cookbook.md`, npm and PyPI): optio-contracts + optio-core (`make release-wire`), optio-api, optio-ui, optio-claudecode, optio-dashboard.
- excavator: bump optio packages; add `'resurrect'` to the `launch`/`cancel`/`dismiss` case of `packages/api/src/auth/optio-access.ts`; wire `onResurrect` at the six `onLaunch` sites; owner deploys bobcat with failed workdirs backed up and restored by hand around the deploy.
