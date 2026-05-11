# Engine RPC Migration Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Working directory:** All work happens on a feature branch in-place at `/home/csillag/deai/optio/` (no worktree per project preference). Create the branch as Task 0 before any other work.

**Goal:** Retire the legacy `${database}/${prefix}:commands` redis stream and consumer. Collapse the four control verbs (`launch`, `cancel`, `dismiss`, `resync`) onto a single implementation on `Optio`; introduce typed outcome dataclasses (`LaunchOutcome` / `CancelOutcome` / `DismissOutcome`); reduce the RPC adapter to wire-translation only; route scheduled launches through `Optio.launch` (respecting launch blocks); codify the convergence rule in `AGENTS.md`.

**Architecture:** Six phases of small commits. (A) retire consumer scaffolding and consumer-only tests. (B) lifecycle refactor: add outcome dataclasses, lift `_resolve` to `Optio`, inline each `_handle_*` into its public method, route the scheduler through a small adapter, delete `_handle_launch_by_process_id`. (C) heartbeat unit test replacing the deleted `test_integration.py`. (D) interop cleanup. (E) documentation rewrites + AGENTS.md convergence section. (F) final acceptance grep + full test runs.

**Tech Stack:** Python 3.12 (`packages/optio-core`), TypeScript (`packages/optio-demo/interop`, `packages/optio-api`), pnpm workspaces, pytest with motor + redis.asyncio, clamator over redis (`clamator_protocol`, `clamator_over_redis`), apscheduler, MongoDB (`motor`), redis-py asyncio.

**Spec reference:** Full design at `docs/2026-05-11-engine-rpc-migration-phase-5-design.md`. Parent spec: `docs/2026-05-08-engine-rpc-migration-design.md` (§8.5). This plan implements the design.

---

## File structure

| Path | Action | Purpose |
|---|---|---|
| `packages/optio-core/src/optio_core/consumer.py` | Delete | Legacy redis-stream consumer |
| `packages/optio-core/src/optio_core/lifecycle.py` | Modify | Drop consumer scaffolding; inline `_handle_*` into public methods; add outcome returns; lift `_resolve`; add scheduler adapter |
| `packages/optio-core/src/optio_core/models.py` | Modify | Add `LaunchOutcome`, `CancelOutcome`, `DismissOutcome` dataclasses |
| `packages/optio-core/src/optio_core/__init__.py` | Modify | Drop `on_command` export; add outcome dataclass exports |
| `packages/optio-core/src/optio_core/_engine_service.py` | Modify | Drop pre-flight state checks; drop local `_resolve`/`_is_objectid`; simplify each method to outcome translation; refresh docstring |
| `packages/optio-core/tests/test_consumer.py` | Delete | Consumer unit tests |
| `packages/optio-core/tests/test_no_redis.py` | Modify | Drop `test_on_command_raises_without_redis` |
| `packages/optio-core/tests/test_launch_guard.py` | Modify | Drop consumer-warning test; migrate `Optio.launch` raise-expectations to outcome assertions; add outcome-reason coverage |
| `packages/optio-core/tests/test_integration.py` | Delete | Whole file (coverage replaced by focused heartbeat test + per-feature suites) |
| `packages/optio-core/tests/test_heartbeat.py` | Create | Focused heartbeat publishing test |
| `packages/optio-core/tests/test_outcomes.py` | Create | Outcome-reason coverage for `Optio.cancel`, `Optio.dismiss` |
| `packages/optio-core/tests/test_engine_service.py` | Modify | Update mocks: `LaunchOutcome` return values instead of `LaunchBlocked` side effect; add cancel/dismiss outcome-translation coverage |
| `packages/optio-demo/interop/run.ts` | Modify | Delete legacy-stream scenario + unused imports/constants |
| `README.md` | Modify | Rewrite Level-2 "Remote Control (+ Redis)" section |
| `packages/optio-core/README.md` | Modify | Update prefix-doc row; rewrite "Remote Control via Redis" + delete `on_command()` subsection |
| `AGENTS.md` | Modify | Drop `on_command` reference + `:commands` description; add "Control-plane convergence" section |

---

## Task 0: Feature branch in-place

**Files:** none (git only)

**Goal:** All phase-5 work lands on a dedicated feature branch off `main`, in the main checkout (no worktree).

- [ ] **Step 1: Confirm clean working tree on main**

Run:
```bash
git -C /home/csillag/deai/optio status -s
git -C /home/csillag/deai/optio branch --show-current
```

Expected: empty (or only `?? .claude/`) status; current branch `main`.

- [ ] **Step 2: Create and switch to the feature branch**

Run:
```bash
git -C /home/csillag/deai/optio checkout -b csillag/rpc-migration-phase-5
```

Expected: `Switched to a new branch 'csillag/rpc-migration-phase-5'`.

- [ ] **Step 3: Verify branch**

Run:
```bash
git -C /home/csillag/deai/optio branch --show-current
```

Expected: `csillag/rpc-migration-phase-5`.

---

## Task 1: Retire consumer scaffolding

**Files:**
- Delete: `packages/optio-core/src/optio_core/consumer.py`
- Delete: `packages/optio-core/tests/test_consumer.py`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/__init__.py`
- Modify: `packages/optio-core/tests/test_no_redis.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`

**Goal:** Remove `CommandConsumer`, `on_command(...)`, `_handle_launch`, consumer-only test scenarios. The remaining `_handle_cancel/dismiss/resync` and `_handle_launch_by_process_id` stay in place — still called by public methods / scheduler. The codebase remains green; behaviour is unchanged from a user-facing perspective.

- [ ] **Step 1: Delete the consumer test file**

Run:
```bash
git -C /home/csillag/deai/optio rm packages/optio-core/tests/test_consumer.py
```

Expected: `rm 'packages/optio-core/tests/test_consumer.py'`.

- [ ] **Step 2: Delete `test_on_command_raises_without_redis` from `test_no_redis.py`**

Edit `packages/optio-core/tests/test_no_redis.py`. Find and delete the entire test function:

```python
async def test_on_command_raises_without_redis(mongo_db):
    """on_command() raises when Redis is not configured."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test")

    with pytest.raises(RuntimeError, match="Redis"):
        fw.on_command("test", lambda p: None)
```

(Match the actual indentation and any decorators present in the file — verify by reading lines 152-160 before editing.)

- [ ] **Step 3: Delete `test_handle_launch_blocked_logs_warning_and_does_not_launch` from `test_launch_guard.py`**

Read `packages/optio-core/tests/test_launch_guard.py` around lines 313-347. Delete the whole test function (and any decorators). Keep all other tests in the file untouched.

- [ ] **Step 4: Edit `lifecycle.py` — drop consumer import**

Remove this import (around line 30):

```python
from optio_core.consumer import CommandConsumer
```

- [ ] **Step 5: Edit `lifecycle.py` — drop `_consumer` field init**

In `Optio.__init__` (around line 55), remove:

```python
        self._consumer: CommandConsumer | None = None
```

- [ ] **Step 6: Edit `lifecycle.py` — drop the consumer setup block in `init()`**

In `init()` (around lines 124-132), remove the block:

```python
            # Legacy command stream (phase-2 co-existence; phase 5 removes).
            db_name = mongo_db.name
            stream_name = f"{db_name}/{prefix}:commands"
            self._consumer = CommandConsumer(self._redis, stream_name)
            self._consumer.on("launch", self._handle_launch)
            self._consumer.on("cancel", self._handle_cancel)
            self._consumer.on("dismiss", self._handle_dismiss)
            self._consumer.on("resync", self._handle_resync)
            await self._consumer.setup()
```

The `db_name` variable was only used by the deleted block — confirm it has no other uses in the surrounding scope. If unused, drop the assignment; if any line below still references `db_name`, restore the assignment.

- [ ] **Step 7: Edit `lifecycle.py` — drop `on_command` public method**

Remove (around lines 183-187):

```python
    def on_command(self, command_type: str, handler: Callable[..., Awaitable]) -> None:
        """Register a custom command handler (must be called before run)."""
        if self._consumer is None:
            raise RuntimeError("Custom commands require Redis")
        self._consumer.on(command_type, handler)
```

- [ ] **Step 8: Edit `lifecycle.py` — collapse the `run()` consumer branch**

Locate (around lines 615-619):

```python
        try:
            if self._consumer:
                await self._consumer.run()
            else:
                await self._shutdown_event.wait()
        finally:
            await self._scheduler.stop()
```

Replace with:

```python
        try:
            await self._shutdown_event.wait()
        finally:
            await self._scheduler.stop()
```

- [ ] **Step 9: Edit `lifecycle.py` — drop `_consumer.stop()` in `shutdown()`**

Locate (around lines 645-649):

```python
        # 2. Consumer
        if self._consumer:
            self._consumer.stop()
        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()
```

Replace with:

```python
        # 2. Signal shutdown to the main run() loop
        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()
```

- [ ] **Step 10: Edit `lifecycle.py` — delete `_handle_launch`**

Remove the method (around lines 857-871):

```python
    async def _handle_launch(self, payload: dict) -> None:
        process_id = payload.get("processId")
        if not process_id:
            return
        task = self._executor._task_registry.get(process_id)
        if task is not None:
            try:
                self._check_launch_blocks(task.metadata)
            except LaunchBlocked as e:
                logger.warning(
                    f"Launch rejected for processId={process_id!r}: {e}"
                )
                return
        resume = payload.get("resume", False)
        await self._handle_launch_by_process_id(process_id, resume=resume)
```

Keep `_handle_launch_by_process_id` (still used by scheduler; deleted later in Task 8).

- [ ] **Step 11: Edit `lifecycle.py` — drop unused `Callable` / `Awaitable` imports if now orphaned**

Inspect the file's `from typing import …` line near the top. `on_command` was the only consumer of `Callable[..., Awaitable]` for command handlers. `Callable` and `Awaitable` may still be used elsewhere (e.g. `get_task_definitions: Callable[…, Awaitable[…]]`) — only remove what is truly unused. Confirm by `grep`:

```bash
grep -n "Callable\|Awaitable" packages/optio-core/src/optio_core/lifecycle.py
```

Remove imports that have zero remaining references.

- [ ] **Step 12: Delete `consumer.py`**

Run:
```bash
git -C /home/csillag/deai/optio rm packages/optio-core/src/optio_core/consumer.py
```

Expected: `rm 'packages/optio-core/src/optio_core/consumer.py'`.

- [ ] **Step 13: Edit `__init__.py` — drop `on_command` export**

Remove these lines (around lines 11, 28):

```python
on_command = _instance.on_command
```

```python
    "init", "run", "shutdown", "on_command",
```

Replace the second with:

```python
    "init", "run", "shutdown",
```

- [ ] **Step 14: Run the optio-core test suite**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && uv run pytest -x -q
```

Expected: all tests pass. `test_consumer.py`, the consumer-warning test, and `test_on_command_raises_without_redis` are gone. No imports of `CommandConsumer` or `on_command` remain.

If pytest is not driven by `uv` in this repo, fall back to:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

The exact invocation matches whatever `make test` runs for this package — check the root `Makefile` if unsure.

- [ ] **Step 15: Confirm no lingering references**

Run:
```bash
grep -rn "CommandConsumer\|on_command" packages/optio-core/ 2>&1 | grep -v __pycache__
```

Expected: empty output.

- [ ] **Step 16: Commit**

```bash
git -C /home/csillag/deai/optio add -A
git -C /home/csillag/deai/optio commit -m "feat(optio-core): retire CommandConsumer + on_command

Delete consumer.py and its tests; remove the consumer setup block,
on_command public method, _handle_launch dispatcher, and related
lifecycle hooks. Legacy redis stream is no longer consumed by the
engine. Public Python API and clamator RPC adapter remain — phases
behind §8.5 of the parent spec."
```

---

## Task 2: Outcome dataclasses

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py`
- Modify: `packages/optio-core/src/optio_core/__init__.py`
- Create: `packages/optio-core/tests/test_outcomes.py` (initial — outcome construction smoke; expanded in Tasks 4 and 5)

**Goal:** Introduce typed outcome dataclasses for `launch`, `cancel`, `dismiss`. Top-level re-exports from `optio_core`.

- [ ] **Step 1: Write the failing test for outcome construction**

Create `packages/optio-core/tests/test_outcomes.py`:

```python
"""Outcome dataclass smoke tests; real coverage added in later tasks."""

from optio_core.models import LaunchOutcome, CancelOutcome, DismissOutcome


def test_launch_outcome_ok():
    out = LaunchOutcome(ok=True)
    assert out.ok is True
    assert out.reason is None


def test_launch_outcome_failure_reason():
    out = LaunchOutcome(ok=False, reason="not-found")
    assert out.ok is False
    assert out.reason == "not-found"


def test_cancel_outcome_failure_reason():
    out = CancelOutcome(ok=False, reason="not-cancellable")
    assert out.ok is False
    assert out.reason == "not-cancellable"


def test_dismiss_outcome_failure_reason():
    out = DismissOutcome(ok=False, reason="not-dismissable")
    assert out.ok is False
    assert out.reason == "not-dismissable"


def test_outcomes_top_level_reexport():
    import optio_core
    assert optio_core.LaunchOutcome is LaunchOutcome
    assert optio_core.CancelOutcome is CancelOutcome
    assert optio_core.DismissOutcome is DismissOutcome
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_outcomes.py -v
```

Expected: ImportError / AttributeError because `LaunchOutcome` etc. do not exist on `models.py`.

- [ ] **Step 3: Add the dataclasses to `models.py`**

Open `packages/optio-core/src/optio_core/models.py`. Add the following block near the other dataclass definitions (top section; verify imports — `dataclass`, `Literal` may need importing):

```python
from typing import Literal


@dataclass(frozen=True)
class LaunchOutcome:
    """Result of Optio.launch. ok=False carries a typed reason."""
    ok: bool
    reason: Literal[
        "not-found", "not-launchable", "launch-blocked", "no-resume-support",
    ] | None = None


@dataclass(frozen=True)
class CancelOutcome:
    """Result of Optio.cancel."""
    ok: bool
    reason: Literal["not-found", "not-cancellable"] | None = None


@dataclass(frozen=True)
class DismissOutcome:
    """Result of Optio.dismiss."""
    ok: bool
    reason: Literal["not-found", "not-dismissable"] | None = None
```

If `dataclass` and / or `Literal` are already imported at the top of the file, skip the duplicate import. If `from dataclasses import dataclass` is missing, add it.

- [ ] **Step 4: Add top-level re-exports**

Edit `packages/optio-core/src/optio_core/__init__.py`. Add to the existing imports / `__all__`:

```python
from optio_core.models import LaunchOutcome, CancelOutcome, DismissOutcome
```

Update `__all__` to include the three new symbols. Place them adjacent to the other model re-exports (`LaunchBlocked`, etc.); preserve alphabetical or logical grouping that already exists.

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_outcomes.py -v
```

Expected: all five tests PASS.

- [ ] **Step 6: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/src/optio_core/models.py packages/optio-core/src/optio_core/__init__.py packages/optio-core/tests/test_outcomes.py
git -C /home/csillag/deai/optio commit -m "feat(optio-core): add LaunchOutcome / CancelOutcome / DismissOutcome

Typed result dataclasses for the public Optio control verbs. The
refactor of Optio.launch / cancel / dismiss to return these lands
in Tasks 4-7."
```

---

## Task 3: Lift `_resolve` from RPC adapter to `Optio`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`

**Goal:** Move the "accept ObjectId hex or processId string; return doc or None" resolver from `_engine_service.py` onto `Optio`, so the public verb refactors (Tasks 4-7) can use it. RPC adapter starts calling `self._optio._resolve`.

- [ ] **Step 1: Write failing test for `Optio._resolve`**

Append to `packages/optio-core/tests/test_outcomes.py` (or create a new `test_resolve.py` if preferred):

```python
import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


@pytest.mark.asyncio
async def test_resolve_by_process_id(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="resolvetest")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="probe", name="Probe"),
    )

    doc = await fw._resolve("probe")
    assert doc is not None
    assert doc["processId"] == "probe"


@pytest.mark.asyncio
async def test_resolve_by_objectid_hex(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="resolvetest2")
    async def noop(ctx):
        pass
    proc = await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="probe", name="Probe"),
    )

    doc = await fw._resolve(str(proc["_id"]))
    assert doc is not None
    assert doc["_id"] == proc["_id"]


@pytest.mark.asyncio
async def test_resolve_missing(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="resolvetest3")
    assert await fw._resolve("does-not-exist") is None
    assert await fw._resolve(str(ObjectId())) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_outcomes.py -v
```

Expected: the three new tests FAIL with `AttributeError: 'Optio' object has no attribute '_resolve'`.

- [ ] **Step 3: Add `_resolve` (and helper) to `Optio` in `lifecycle.py`**

At the top of `lifecycle.py`, after the existing imports, add:

```python
import re as _re

_OBJECTID_RE = _re.compile(r"^[a-fA-F0-9]{24}$")


def _is_objectid(s: str) -> bool:
    return bool(_OBJECTID_RE.match(s))
```

Then on the `Optio` class, add:

```python
    async def _resolve(self, id_str: str) -> dict | None:
        """Accept an ObjectId hex string or a processId; return the matching
        Mongo process doc, or None if neither lookup matches."""
        coll = self._config.mongo_db[f"{self._config.prefix}_processes"]
        if _is_objectid(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str})
```

Place it near `get_process` and `list_processes` for locality.

- [ ] **Step 4: Run the new tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_outcomes.py -v
```

Expected: the three resolve tests PASS.

- [ ] **Step 5: Update `_engine_service.py` to use the lifted resolver**

Replace every call to `self._resolve(...)` with `self._optio._resolve(...)`. Then delete the local helpers:

```python
_OBJECTID_RE = __import__("re").compile(r"^[a-fA-F0-9]{24}$")


def _is_objectid(s: str) -> bool:
    return bool(_OBJECTID_RE.match(s))
```

and the local method:

```python
    async def _resolve(self, id_str: str) -> dict | None:
        """Accept ObjectId hex or processId string; return the doc or None."""
        coll = self._optio._config.mongo_db[
            f"{self._optio._config.prefix}_processes"
        ]
        if _is_objectid(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str})
```

- [ ] **Step 6: Run the full optio-core suite**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: all tests pass. RPC adapter tests (`test_engine_service.py`, `test_engine_service_resolve.py`) continue to pass; the underlying behaviour is unchanged.

- [ ] **Step 7: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/
git -C /home/csillag/deai/optio commit -m "refactor(optio-core): lift _resolve from RPC adapter to Optio

Single resolver shared by the public verbs (next tasks) and the RPC
adapter. No behaviour change."
```

---

## Task 4: `Optio.cancel` → `CancelOutcome`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`
- Modify: `packages/optio-core/tests/test_outcomes.py`

**Goal:** Inline `_handle_cancel` into `Optio.cancel`; return `CancelOutcome`. RPC adapter drops its `not-found` / `not-cancellable` pre-flight and translates the outcome instead.

- [ ] **Step 1: Write failing tests for the new return shape**

Append to `packages/optio-core/tests/test_outcomes.py`:

```python
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_cancel_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="canceltest")
    out = await fw.cancel("nonexistent")
    assert out == CancelOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_cancel_not_cancellable_when_idle(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="canceltest2")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="idle1", name="Idle"),
    )

    out = await fw.cancel("idle1")
    assert out == CancelOutcome(ok=False, reason="not-cancellable")


@pytest.mark.asyncio
async def test_cancel_returns_ok_when_scheduled(mongo_db):
    """Direct DB seed of a 'scheduled' process — cancel transitions it to 'cancelled'."""
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="canceltest3")
    coll = mongo_db["canceltest3_processes"]
    from bson import ObjectId
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "sched1",
        "status": {"state": "scheduled"},
        "cancellable": True,
    })

    out = await fw.cancel("sched1")
    assert out == CancelOutcome(ok=True)

    proc = await fw.get_process("sched1")
    assert proc["status"]["state"] == "cancelled"
```

Add the missing top-of-file import: `from optio_core.models import LaunchOutcome, CancelOutcome, DismissOutcome` (if not already present).

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_outcomes.py -v -k cancel
```

Expected: three new cancel tests FAIL (return is currently `None`, not a `CancelOutcome`).

- [ ] **Step 3: Refactor `Optio.cancel` in `lifecycle.py`**

Replace the current `async def cancel(self, process_id: str) -> None` (around lines 367-369) with the inlined body returning `CancelOutcome`. New body:

```python
    async def cancel(self, process_id: str) -> CancelOutcome:
        """Cancel a running or scheduled process. Returns a CancelOutcome
        with ok=False for unknown / non-cancellable inputs."""
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
            return CancelOutcome(ok=True)

        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="cancel_requested"),
        )

        found = self._executor.request_cancel_with_deadline(
            proc["_id"],
            deadline=time.monotonic() + self._config.cancel_grace_seconds,
        )
        if found:
            await update_status(
                self._config.mongo_db, self._config.prefix, proc["_id"],
                ProcessStatus(state="cancelling"),
            )
        return CancelOutcome(ok=True)
```

Add the import at the top of `lifecycle.py`:

```python
from optio_core.models import (
    TaskInstance, OptioConfig, ProcessStatus, ProcessMetadataFilter,
    matches_filter, LaunchBlocked,
    LaunchOutcome, CancelOutcome, DismissOutcome,
)
```

- [ ] **Step 4: Delete `_handle_cancel` from `lifecycle.py`**

Remove the method (around lines 877-916):

```python
    async def _handle_cancel(self, payload: dict) -> None:
        ...
```

- [ ] **Step 5: Update `_engine_service.py::cancel`**

Replace the current method body with:

```python
    async def cancel(self, params: CancelParams) -> CancelResult:
        outcome = await self._optio.cancel(params.process_id)
        if not outcome.ok:
            return CancelResult.model_validate(
                {"ok": False, "reason": outcome.reason}
            )
        proc = await self._optio._resolve(params.process_id)
        return CancelResult.model_validate(
            {"ok": True, "process": _to_process_dict(proc)}
        )
```

Also drop `CANCELLABLE_STATES` from the `state_machine` import at the top of the file if it has no remaining references. Verify with:

```bash
grep -n CANCELLABLE_STATES packages/optio-core/src/optio_core/_engine_service.py
```

If zero hits remain after the rewrite, narrow the import line.

- [ ] **Step 6: Verify `Optio.cancel_and_wait` still works**

`cancel_and_wait` (around line 371) calls `self.cancel(process_id)` then polls the DB. The `await self.cancel(...)` now returns a `CancelOutcome` (previously `None`); the caller discards the value, which is harmless. No code change needed. Confirm by reading the method.

- [ ] **Step 7: Run optio-core tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: full suite passes. The three new outcome tests pass. Pre-existing `cancel` callers (test_persistent_launch_blocks, test_group_cancel, test_deadline_cancel, etc.) keep passing because they ignore the return value.

- [ ] **Step 8: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/
git -C /home/csillag/deai/optio commit -m "refactor(optio-core): Optio.cancel returns CancelOutcome

Inline _handle_cancel; RPC adapter drops its pre-flight and translates
the typed outcome. State-machine logic now lives in exactly one place."
```

---

## Task 5: `Optio.dismiss` → `DismissOutcome`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`
- Modify: `packages/optio-core/tests/test_outcomes.py`

**Goal:** Mirror of Task 4 for dismiss. Inline `_handle_dismiss`; return `DismissOutcome`; RPC adapter simplifies.

- [ ] **Step 1: Write failing tests**

Append to `packages/optio-core/tests/test_outcomes.py`:

```python
@pytest.mark.asyncio
async def test_dismiss_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="dismisstest")
    out = await fw.dismiss("nonexistent")
    assert out == DismissOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_dismiss_not_dismissable_when_idle(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="dismisstest2")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(execute=noop, process_id="idle1", name="Idle"),
    )

    out = await fw.dismiss("idle1")
    assert out == DismissOutcome(ok=False, reason="not-dismissable")


@pytest.mark.asyncio
async def test_dismiss_ok_from_done(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="dismisstest3")
    coll = mongo_db["dismisstest3_processes"]
    from bson import ObjectId
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "done1",
        "status": {"state": "done"},
    })

    out = await fw.dismiss("done1")
    assert out == DismissOutcome(ok=True)

    proc = await fw.get_process("done1")
    assert proc["status"]["state"] == "idle"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_outcomes.py -v -k dismiss
```

Expected: the three new tests FAIL (return is `None`).

- [ ] **Step 3: Refactor `Optio.dismiss`**

Replace the current method (around line 543) with:

```python
    async def dismiss(self, process_id: str) -> DismissOutcome:
        """Dismiss a completed process (reset to idle). Returns DismissOutcome."""
        proc = await self._resolve(process_id)
        if proc is None:
            return DismissOutcome(ok=False, reason="not-found")
        if proc["status"]["state"] not in DISMISSABLE_STATES:
            return DismissOutcome(ok=False, reason="not-dismissable")

        await clear_result_fields(
            self._config.mongo_db, self._config.prefix, proc["_id"],
        )
        await update_status(
            self._config.mongo_db, self._config.prefix, proc["_id"],
            ProcessStatus(state="idle"),
        )
        return DismissOutcome(ok=True)
```

`DISMISSABLE_STATES` is content-identical to `END_STATES` (`{"done", "failed", "cancelled"}`) per `state_machine.py`, but the canonical name for this guard is `DISMISSABLE_STATES`. Widen the existing `from optio_core.state_machine import …` line at the top of `lifecycle.py` to include `DISMISSABLE_STATES`. After Task 7 the full set imported will be `ACTIVE_STATES, CANCELLABLE_STATES, DISMISSABLE_STATES, END_STATES, LAUNCHABLE_STATES`.

- [ ] **Step 4: Delete `_handle_dismiss` from `lifecycle.py`**

Remove the method (around lines 918-938).

- [ ] **Step 5: Update `_engine_service.py::dismiss`**

Replace with:

```python
    async def dismiss(self, params: DismissParams) -> DismissResult:
        outcome = await self._optio.dismiss(params.process_id)
        if not outcome.ok:
            return DismissResult.model_validate(
                {"ok": False, "reason": outcome.reason}
            )
        proc = await self._optio._resolve(params.process_id)
        return DismissResult.model_validate(
            {"ok": True, "process": _to_process_dict(proc)}
        )
```

Narrow the `state_machine` import in `_engine_service.py` — drop `DISMISSABLE_STATES` if unused.

- [ ] **Step 6: Run optio-core tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: full suite green; the three new dismiss tests pass.

- [ ] **Step 7: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/
git -C /home/csillag/deai/optio commit -m "refactor(optio-core): Optio.dismiss returns DismissOutcome

Inline _handle_dismiss; RPC adapter simplifies; convergence pattern
established by Task 4 extended to dismiss."
```

---

## Task 6: `Optio.resync` — inline only

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

**Goal:** Resync has no failure modes worth typing (return stays `None`); just inline `_handle_resync` body into the public method and delete the private helper.

- [ ] **Step 1: Read the current public method and the private helper**

Inspect `Optio.resync` (around lines 547-562) and `_handle_resync` (around lines 940-955). The public method currently builds a dict `{"clean": clean, "metadataFilter": metadata_filter}` and calls the private helper, which destructures.

- [ ] **Step 2: Replace `Optio.resync` body**

Replace the method with:

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

- [ ] **Step 3: Delete `_handle_resync`**

Remove the private helper (around lines 940-955).

- [ ] **Step 4: Run optio-core tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: full suite green. `test_resync.py` and `test_resync_cancel_stale.py` continue passing — they exercise the public surface.

- [ ] **Step 5: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/src/optio_core/lifecycle.py
git -C /home/csillag/deai/optio commit -m "refactor(optio-core): inline _handle_resync into Optio.resync

Drops the dict-payload indirection that existed only to mirror the
legacy consumer payload shape."
```

---

## Task 7: `Optio.launch` → `LaunchOutcome`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`
- Modify: `packages/optio-core/tests/test_outcomes.py`

**Goal:** `Optio.launch` no longer raises `LaunchBlocked`; returns `LaunchOutcome` covering `not-found`, `not-launchable`, `no-resume-support`, `launch-blocked`, and success. RPC adapter drops its pre-flight. `_check_launch_blocks` keeps raising (used by `launch_and_wait`, `adhoc_define`, executor); a new `_matches_block` boolean variant is introduced for the non-raising `launch`. Tests previously asserting `pytest.raises(LaunchBlocked)` on `Optio.launch` migrate to outcome assertions.

- [ ] **Step 1: Add `_matches_block` non-raising helper to `Optio`**

In `lifecycle.py`, near `_check_launch_blocks` (around lines 250-263), add:

```python
    def _matches_block(self, metadata: ProcessMetadataFilter | None) -> bool:
        """Return True if `metadata` matches any registered launch block.
        Non-raising sibling of `_check_launch_blocks`. Fast path: empty map → False."""
        if not self._launch_blocks:
            return False
        md = metadata or {}
        for entry in self._launch_blocks.values():
            if matches_filter(md, entry.filter):
                return True
        return False
```

- [ ] **Step 2: Write failing tests for the new `Optio.launch` outcome surface**

Append to `packages/optio-core/tests/test_outcomes.py`:

```python
@pytest.mark.asyncio
async def test_launch_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest")
    out = await fw.launch("nonexistent")
    assert out == LaunchOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_launch_not_launchable_when_already_running(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest2")
    coll = mongo_db["launchtest2_processes"]
    from bson import ObjectId
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "running1",
        "status": {"state": "running"},
    })

    out = await fw.launch("running1")
    assert out == LaunchOutcome(ok=False, reason="not-launchable")


@pytest.mark.asyncio
async def test_launch_no_resume_support(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest3")
    coll = mongo_db["launchtest3_processes"]
    from bson import ObjectId
    await coll.insert_one({
        "_id": ObjectId(),
        "processId": "noresume1",
        "status": {"state": "idle"},
        "supportsResume": False,
    })

    out = await fw.launch("noresume1", resume=True)
    assert out == LaunchOutcome(ok=False, reason="no-resume-support")


@pytest.mark.asyncio
async def test_launch_blocked_outcome(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="launchtest4")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(
            execute=noop, process_id="blocked1", name="Blocked",
            metadata={"project": "p1"},
        ),
    )

    async with fw.block_launches({"project": "p1"}):
        out = await fw.launch("blocked1")

    assert out == LaunchOutcome(ok=False, reason="launch-blocked")
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_outcomes.py -v -k launch
```

Expected: the four new tests FAIL (`Optio.launch` currently returns `None` and raises `LaunchBlocked`).

- [ ] **Step 4: Refactor `Optio.launch`**

Replace the current `Optio.launch` (around lines 341-352) with:

```python
    async def launch(self, process_id: str, resume: bool = False) -> LaunchOutcome:
        """Fire-and-forget launch. Returns LaunchOutcome with a typed reason
        on any precondition failure; on success returns LaunchOutcome(ok=True)
        and the executor task is scheduled in the background."""
        proc = await self._resolve(process_id)
        if proc is None:
            return LaunchOutcome(ok=False, reason="not-found")
        if proc["status"]["state"] not in LAUNCHABLE_STATES:
            return LaunchOutcome(ok=False, reason="not-launchable")
        if resume and not proc.get("supportsResume", False):
            return LaunchOutcome(ok=False, reason="no-resume-support")

        task = self._executor._task_registry.get(proc["processId"])
        if task is not None and self._matches_block(task.metadata):
            return LaunchOutcome(ok=False, reason="launch-blocked")

        asyncio.create_task(
            self._executor.launch_process(proc["processId"], resume=resume),
        )
        return LaunchOutcome(ok=True)
```

Add `LAUNCHABLE_STATES` to the existing `from optio_core.state_machine import …` line at the top of `lifecycle.py`:

```python
from optio_core.state_machine import (
    ACTIVE_STATES, CANCELLABLE_STATES, END_STATES, LAUNCHABLE_STATES,
)
```

(Only add what the existing import does not already provide; preserve the alphabetical order in use.)

- [ ] **Step 5: Update `_engine_service.py::launch`**

Replace the current method with:

```python
    async def launch(self, params: LaunchParams) -> LaunchResult:
        outcome = await self._optio.launch(params.process_id, resume=bool(params.resume))
        if not outcome.ok:
            return LaunchResult.model_validate(
                {"ok": False, "reason": outcome.reason}
            )
        # The executor runs asynchronously; yield once so the state transition
        # (idle → scheduled) can be written before we read it back.
        await asyncio.sleep(0)
        proc = await self._optio._resolve(params.process_id)
        return LaunchResult.model_validate(
            {"ok": True, "process": _to_process_dict(proc)}
        )
```

Narrow the `state_machine` import in `_engine_service.py` — remove `LAUNCHABLE_STATES` if unused. After this task, the import line is likely a single `from optio_core.models import LaunchBlocked` (which itself may now be unused too — drop if so). Verify with grep.

- [ ] **Step 6: Migrate existing `Optio.launch` raise-expectations in `test_launch_guard.py`**

The following test currently expects a raise (around line 234):

```python
async def test_launch_blocked_when_task_metadata_matches(mongo_db):
    """Optio.launch raises LaunchBlocked synchronously for a task whose metadata matches a block.
    ..."""
```

Rewrite the body to assert an outcome:

```python
async def test_launch_blocked_when_task_metadata_matches(mongo_db):
    """Optio.launch returns ok=False/reason=launch-blocked for matching task metadata."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass
    await optio.adhoc_define(
        TaskInstance(
            execute=noop, process_id="blocked1", name="Blocked",
            metadata={"project": "p1"},
        ),
    )

    async with optio.block_launches({"project": "p1"}):
        out = await optio.launch("blocked1")
    assert out.ok is False
    assert out.reason == "launch-blocked"
```

(Adjust to match the original test's setup — keep the same fixture wiring; only the assertion mechanism changes.)

Scan `test_launch_guard.py` for other `Optio.launch(...)` calls wrapped in `pytest.raises(LaunchBlocked)` and apply the same flip. **Do not** touch tests that wrap `Optio.launch_and_wait`, `Optio.adhoc_define`, or `ctx.run_child` — those still raise.

- [ ] **Step 7: Run optio-core tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: full suite green. The four new outcome tests pass; the migrated `test_launch_blocked_when_task_metadata_matches` passes.

- [ ] **Step 8: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/
git -C /home/csillag/deai/optio commit -m "refactor(optio-core): Optio.launch returns LaunchOutcome

No longer raises LaunchBlocked. Resolves precondition checks (not-found,
not-launchable, no-resume-support) in one place; RPC adapter drops its
pre-flight. launch_and_wait / adhoc_define / executor still raise."
```

---

## Task 8: Scheduler adapter + delete `_handle_launch_by_process_id`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

**Goal:** The scheduler's `launch_fn` was `_handle_launch_by_process_id`; after Task 7 the public `Optio.launch` is the single launch funnel. Route the scheduler through it, preserving APScheduler-visible observability via a small adapter that logs warnings on `outcome.ok=False`. This semantically upgrades scheduled launches to respect launch blocks (previously bypassed).

- [ ] **Step 1: Add the scheduler adapter to `Optio`**

In `lifecycle.py`, place the new method near `_handle_launch_by_process_id` (which is about to be deleted):

```python
    async def _scheduler_launch_adapter(self, process_id: str) -> None:
        """Scheduler hook: funnel through Optio.launch, log on failure.
        APScheduler discards the return value; the warning preserves visibility
        of skipped scheduled fires (e.g. launch blocks active at fire time)."""
        outcome = await self.launch(process_id)
        if not outcome.ok:
            logger.warning(
                f"Scheduled launch of {process_id} skipped: {outcome.reason}"
            )
```

- [ ] **Step 2: Wire `init()` scheduler construction to the adapter**

Locate (around line 169):

```python
        self._scheduler = ProcessScheduler(
            launch_fn=self._handle_launch_by_process_id,
        )
```

Replace with:

```python
        self._scheduler = ProcessScheduler(
            launch_fn=self._scheduler_launch_adapter,
        )
```

- [ ] **Step 3: Delete `_handle_launch_by_process_id`**

Remove (around lines 873-875):

```python
    async def _handle_launch_by_process_id(self, process_id: str, resume: bool = False) -> None:
        # Run in a background task so the consumer can continue
        asyncio.create_task(self._executor.launch_process(process_id, resume=resume))
```

- [ ] **Step 4: Add a focused scheduler-adapter test**

Append to `packages/optio-core/tests/test_outcomes.py`:

```python
import logging


@pytest.mark.asyncio
async def test_scheduler_adapter_logs_warning_when_blocked(mongo_db, caplog):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="schedtest")
    async def noop(ctx):
        pass
    await fw.adhoc_define(
        TaskInstance(
            execute=noop, process_id="sched-blocked", name="Sched-Blocked",
            metadata={"project": "p1"},
        ),
    )

    caplog.set_level(logging.WARNING)
    async with fw.block_launches({"project": "p1"}):
        await fw._scheduler_launch_adapter("sched-blocked")

    assert any(
        rec.levelno == logging.WARNING and "sched-blocked" in rec.message
        and "launch-blocked" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]
```

- [ ] **Step 5: Run optio-core tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: full suite green; the new scheduler-adapter test passes.

- [ ] **Step 6: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/
git -C /home/csillag/deai/optio commit -m "refactor(optio-core): scheduler routes through Optio.launch via adapter

Delete _handle_launch_by_process_id. New _scheduler_launch_adapter
funnels through the single launch implementation and logs warnings
on outcome failure. Semantic upgrade: scheduled fires now respect
launch blocks."
```

---

## Task 9: Update `test_engine_service.py` outcome mocks

**Files:**
- Modify: `packages/optio-core/tests/test_engine_service.py`

**Goal:** The fake-optio mock that previously raised `LaunchBlocked` to drive RPC's `reason=launch-blocked` translation now returns `LaunchOutcome(ok=False, reason="launch-blocked")`. Sibling tests for `cancel` / `dismiss` outcome translation may need the same treatment if they exist.

- [ ] **Step 1: Inspect `test_engine_service.py` to identify all `fake_optio` mocks for launch/cancel/dismiss**

Run:
```bash
grep -n "fake_optio\.launch\|fake_optio\.cancel\|fake_optio\.dismiss\|side_effect=LaunchBlocked\|side_effect=" packages/optio-core/tests/test_engine_service.py
```

For each mock that drives an outcome-failure return path through the RPC adapter, identify whether it currently uses `side_effect=…` (raise) or `return_value=…`.

- [ ] **Step 2: Update `test_launch_blocked` (line ~150) and any sibling raise-driven tests**

Replace this pattern:

```python
fake_optio.launch = AsyncMock(side_effect=LaunchBlocked("blocked by test"))
```

with:

```python
fake_optio.launch = AsyncMock(return_value=LaunchOutcome(ok=False, reason="launch-blocked"))
```

Top-of-file import: add `from optio_core.models import LaunchOutcome` (and `CancelOutcome` / `DismissOutcome` if used).

For any other test that mocks `fake_optio.cancel` / `fake_optio.dismiss` to drive RPC failure translation, similarly switch from `side_effect=Exception(...)` (if used) or `return_value=None` patterns to `return_value=CancelOutcome(ok=False, reason="not-cancellable")` etc. Match the new public-API return shape so the adapter's outcome translation is exercised.

For tests that drive RPC success paths, mock the new outcome return:

```python
fake_optio.cancel = AsyncMock(return_value=CancelOutcome(ok=True))
```

- [ ] **Step 3: Verify any post-success `_resolve` mocking still works**

The RPC adapter now calls `self._optio._resolve(...)` (Task 3 + 4 + 5 + 7). If existing tests mocked the adapter-local `_resolve` (now gone), they must mock `self._optio._resolve` instead — e.g. `fake_optio._resolve = AsyncMock(return_value={...})`. Adjust.

- [ ] **Step 4: Run optio-core tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: full suite green. `test_engine_service.py` tests now exercise the outcome-translation path.

- [ ] **Step 5: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/tests/test_engine_service.py
git -C /home/csillag/deai/optio commit -m "test(optio-core): engine-service tests exercise outcome translation

fake_optio mocks now return typed outcomes; the RPC adapter is tested
against the new public surface."
```

---

## Task 10: Delete `test_integration.py` + add `test_heartbeat.py`

**Files:**
- Delete: `packages/optio-core/tests/test_integration.py`
- Create: `packages/optio-core/tests/test_heartbeat.py`

**Goal:** Remove the whole `test_integration.py` (its unique coverage was heartbeat publishing; everything else is covered elsewhere). Replace with a focused heartbeat test.

- [ ] **Step 1: Write the heartbeat test first**

Create `packages/optio-core/tests/test_heartbeat.py`:

```python
"""Heartbeat publishing — covers what test_integration.py used to."""

import asyncio
import pytest
from redis.asyncio import Redis

from optio_core.lifecycle import Optio


@pytest.mark.asyncio
async def test_heartbeat_key_set_during_run(mongo_db, redis_url):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="hbtest", redis_url=redis_url)

    run_task = asyncio.create_task(fw.run())
    try:
        # Heartbeat loop ticks every 5s; allow a comfortable margin.
        await asyncio.sleep(6.0)
        redis = Redis.from_url(redis_url)
        try:
            key = f"{mongo_db.name}/hbtest:heartbeat"
            value = await redis.get(key)
            assert value is not None, (
                f"Heartbeat key {key!r} not set; expected the heartbeat loop "
                f"to have written it by now"
            )
        finally:
            await redis.aclose()
    finally:
        await fw.shutdown()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
```

The `mongo_db` and `redis_url` fixtures already exist in `conftest.py` (used by other tests). Verify by `grep -n "mongo_db\|redis_url" packages/optio-core/tests/conftest.py`.

- [ ] **Step 2: Run the new test**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest tests/test_heartbeat.py -v
```

Expected: PASS (heartbeat code in `lifecycle.py` is untouched by phase 5; behaviour preserved).

- [ ] **Step 3: Delete `test_integration.py`**

Run:
```bash
git -C /home/csillag/deai/optio rm packages/optio-core/tests/test_integration.py
```

- [ ] **Step 4: Run the full suite**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: green. No regression from removing the large integration test (other suites cover the lifecycle / launch / child-tree / executor concerns).

- [ ] **Step 5: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/tests/test_heartbeat.py
git -C /home/csillag/deai/optio commit -m "test(optio-core): focused heartbeat test replaces test_integration

test_integration.py's unique coverage was the heartbeat-key assertion;
its other concerns are covered by test_executor, test_launch_guard,
test_child_progress, test_parallel, test_group_cancel, and the
deadline-cancel / persistent-launch-blocks suites. Trade a 200+ line
brittle smoke for a 20-line targeted assertion."
```

---

## Task 11: Interop cleanup — delete legacy-stream scenario

**Files:**
- Modify: `packages/optio-demo/interop/run.ts`

**Goal:** Remove scenario #11 (`legacy-stream-regression`), unused redis import / client / `KEY_PREFIX`, and the top-of-file comment line about legacy co-existence.

- [ ] **Step 1: Read the file to confirm current state**

Read `packages/optio-demo/interop/run.ts` lines 1-25 and lines 195-240.

- [ ] **Step 2: Delete scenario #11 block (lines ~197-233)**

Remove the entire `await withTimeout('legacy-stream-regression', async () => { ... });` block including the leading comment.

- [ ] **Step 3: Remove unused imports / constants**

If `redis` (the `IORedis` client) is unused by any other scenario:

- Delete the import: `import IORedis from 'ioredis';`
- Delete the client construction: `const redis = new IORedis(REDIS_URL);`
- Delete the `await redis.quit();` in the `finally` block.
- Delete `KEY_PREFIX` constant if unused.

Verify by `grep -n "redis\.\|KEY_PREFIX" packages/optio-demo/interop/run.ts` after edits.

- [ ] **Step 4: Update top-of-file comment**

Edit lines 1-5 (the leading docstring). Drop the sentence:

> Verifies the wire works end-to-end and the legacy ${prefix}:commands stream still functions during co-existence.

Replace with a sentence focused on what the file does now: end-to-end clamator-RPC scenarios against optio-demo.

- [ ] **Step 5: Run interop**

Run:
```bash
cd /home/csillag/deai/optio && make test-interop
```

(Or whatever the project's interop runner command is — check the root `Makefile`.)

Expected: all remaining scenarios pass. The legacy-stream scenario is gone.

- [ ] **Step 6: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-demo/interop/run.ts
git -C /home/csillag/deai/optio commit -m "test(optio-demo): drop legacy-stream interop scenario

Engine no longer consumes the legacy redis stream; co-existence test
is moot. Regression defended by the acceptance grep."
```

---

## Task 12: Top-level README rewrite

**Files:**
- Modify: `README.md`

**Goal:** Replace the Level-2 "Remote Control (+ Redis)" prose with clamator-RPC framing.

- [ ] **Step 1: Read existing section**

Read `README.md` lines 175-195 to confirm current text and the surrounding section flow.

- [ ] **Step 2: Replace §178-180**

Replace the paragraph:

```markdown
### Level 2: Remote Control (+ Redis)

Adds external command ingestion via Redis Streams, enabling remote control of processes from other services. External systems can publish commands (launch, cancel, dismiss, resync, or custom) to the `optio:commands` Redis stream (customizable via the `prefix` parameter). Custom command handlers can be registered with `on_command()`.
```

with:

```markdown
### Level 2: Remote Control (+ Redis)

Adds external command ingestion via [clamator](https://example.invalid/clamator) — a typed RPC channel layered on Redis Streams. External services use the generated TypeScript or Python client for the `optio-engine` contract to launch, cancel, dismiss, and resync processes. Calls return typed result reasons (`not-found`, `not-cancellable`, `launch-blocked`, etc.) instead of fire-and-forget messages, so callers can react meaningfully. Custom verbs are added by registering additional clamator services against `optio_core.rpc_server`.
```

Adjust the clamator link target — if there is an in-repo doc URL for clamator, use that; otherwise drop the link and just write `clamator`. Do not invent URLs.

- [ ] **Step 3: Confirm no other top-level README hits**

Run:
```bash
grep -n "on_command\|optio:commands\|CommandConsumer\|:commands" README.md
```

Expected: empty.

- [ ] **Step 4: Commit**

```bash
git -C /home/csillag/deai/optio add README.md
git -C /home/csillag/deai/optio commit -m "docs(readme): Level 2 describes clamator RPC, not legacy stream"
```

---

## Task 13: optio-core README rewrite

**Files:**
- Modify: `packages/optio-core/README.md`

**Goal:** Two edits — (a) the prefix-doc table row drops the `:commands` stream reference; (b) the "Remote Control via Redis" section and `on_command()` subsection (lines ~501-569) are rewritten to describe clamator as the inbound channel.

- [ ] **Step 1: Read the affected sections**

Read `packages/optio-core/README.md` lines 80-90 (prefix table) and lines 495-575 (Remote Control + on_command).

- [ ] **Step 2: Edit prefix-doc table row (line 84)**

Replace:

```markdown
| `prefix` | `str` | `"optio"` | Namespace for collections (`{prefix}_processes`) and Redis streams (`{database}/{prefix}:commands`). Override if you need to avoid name collisions in a shared database. Redis streams are automatically scoped by both database name and prefix, so instances using different databases won't collide even on a shared Redis server. |
```

with:

```markdown
| `prefix` | `str` | `"optio"` | Namespace for collections (`{prefix}_processes`) and clamator service streams (`{database}/{prefix}` key prefix). Override if you need to avoid name collisions in a shared database. Streams are automatically scoped by both database name and prefix, so instances using different databases won't collide even on a shared Redis server. |
```

- [ ] **Step 3: Rewrite the "Remote Control via Redis" section (and delete `on_command()` subsection)**

Find the section starting around line 501 (or wherever the heading currently lives — confirm by reading the surrounding lines). Replace the prose with text along the lines of:

```markdown
### Remote Control via Clamator RPC

When Redis is configured (by passing `redis_url` to `init()`), Optio registers an `optio-engine` clamator service against a Redis-backed `RpcServerCore`. External services control processes by holding a clamator client and calling typed methods: `launch`, `cancel`, `dismiss`, `resync`, `group_cancel`, `group_cancel_and_wait`, `block_launches`, `unblock_launches`. Results are discriminated unions — `ok: true` carries the updated process payload; `ok: false` carries a typed `reason` field (e.g. `"not-found"`, `"not-cancellable"`, `"launch-blocked"`, `"no-resume-support"`).

For TypeScript clients, use the generated `optio-engine` contract from `optio-api` / `optio-contracts`. For Python clients, build a `RedisRpcClient` against the same contract module.

Custom command verbs are added by registering an additional clamator service against `optio_core.rpc_server` after `init()`; the legacy `on_command()` Python helper is gone.
```

Delete the entire `#### on_command()` subsection (around lines 544-569), including its code example. If the file has a table-of-contents-style anchor list that referenced `on_command`, remove that entry too.

- [ ] **Step 4: Grep for stragglers**

Run:
```bash
grep -n "on_command\|:commands\|CommandConsumer\|optio:commands" packages/optio-core/README.md
```

Expected: empty.

- [ ] **Step 5: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/README.md
git -C /home/csillag/deai/optio commit -m "docs(optio-core): rewrite Remote Control section for clamator RPC

Drop on_command() subsection; update prefix-doc to describe the
clamator service-stream key prefix rather than the legacy commands
stream."
```

---

## Task 14: AGENTS.md — drop legacy refs + add convergence section

**Files:**
- Modify: `AGENTS.md`

**Goal:** Remove `on_command` API reference (line ~116), update the prefix-doc row (line ~127), delete the Custom Commands paragraph (line ~627), and add the new "Control-plane convergence" section.

- [ ] **Step 1: Read affected lines**

Read `AGENTS.md` lines 110-135 and 620-635.

- [ ] **Step 2: Delete `on_command` API reference (line ~116)**

Remove the line:

```
optio_core.on_command(command_type: str, handler: Callable[..., Awaitable]) -> None
```

If it sits inside a bullet list / definition block, remove the surrounding bullet too. Preserve neighbour bullets.

- [ ] **Step 3: Update prefix-doc row (line ~127)**

Same edit as Task 13 Step 2 (mirror the optio-core README change exactly).

- [ ] **Step 4: Delete the Custom Commands paragraph (line ~627)**

Remove:

```
Write commands to Redis stream `{prefix}:commands`. Used by domain code that needs to trigger processes without HTTP.
```

(And the heading it sits under, if the heading exists solely for this paragraph — e.g. `#### Custom Commands`.) Replace with a short note:

```
In-process domain code triggers processes by calling `Optio.launch` / `cancel` / `dismiss` / `resync` directly. External services use the clamator RPC channel (see Remote Control via Clamator RPC in `packages/optio-core/README.md`).
```

- [ ] **Step 5: Add the "Control-plane convergence" section**

Place it under an appropriate top-level heading (look for an existing architecture / design-principles section in AGENTS.md — likely near the engine-RPC migration discussion). Insert verbatim:

```markdown
### Control-plane convergence

Every control verb (launch, cancel, dismiss, resync, group_cancel, …) has **exactly one implementation**: a method on `Optio` in `lifecycle.py`. All external entry points — Python callers, RPC adapters, schedulers, future channels — funnel through that public method.

Adapters do two things and only two things:
1. Translate inbound wire shape to a `(process_id, …)` tuple.
2. Translate the public method's return / raised exception to the adapter's wire result.

State-machine logic, side effects, and authority decisions live on `Optio`, never in adapters. If an adapter needs to short-circuit with a typed reason (e.g. RPC's `not-found` / `not-cancellable`), it pre-flights against shared constants (`CANCELLABLE_STATES`, etc.) and only then delegates — it does not duplicate the state transition.

When you add a new channel, you call existing `Optio` methods. When you add a new verb, you add one method to `Optio` and one thin adapter per existing channel. Never duplicate verb logic across layers.
```

- [ ] **Step 6: Grep for stragglers**

Run:
```bash
grep -n "on_command\|:commands\|CommandConsumer" AGENTS.md
```

Expected: empty.

- [ ] **Step 7: Commit**

```bash
git -C /home/csillag/deai/optio add AGENTS.md
git -C /home/csillag/deai/optio commit -m "docs(agents): drop legacy stream / on_command; add convergence rule

Codifies the rule the phase-5 refactor enforces: one Optio.<verb>
implementation; adapters translate wire and never duplicate state-
machine logic."
```

---

## Task 15: `_engine_service.py` docstring refresh

**Files:**
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`

**Goal:** Replace the multi-line stale docstring (lines 1-6) with a one-line statement that matches post-phase-5 reality.

- [ ] **Step 1: Read the current top of the file**

Read lines 1-10 of `packages/optio-core/src/optio_core/_engine_service.py`.

- [ ] **Step 2: Replace the docstring**

Replace:

```python
"""OptioEngineService — clamator RPC implementation for the optio engine.

Phase 2 of the engine-RPC migration. Co-exists with the legacy
${prefix}:commands stream consumer; HTTP handlers still route through
the legacy stream until phase 3.
"""
```

with:

```python
"""Clamator RPC implementation for the optio-engine contract."""
```

- [ ] **Step 3: Run optio-core tests**

Run:
```bash
cd /home/csillag/deai/optio/packages/optio-core && pytest -x -q
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git -C /home/csillag/deai/optio add packages/optio-core/src/optio_core/_engine_service.py
git -C /home/csillag/deai/optio commit -m "docs(optio-core): refresh _engine_service module docstring"
```

---

## Task 16: Acceptance — grep + full suites + runtime check

**Files:** none (verification only)

**Goal:** Confirm every acceptance criterion from the spec is met.

- [ ] **Step 1: Live-code grep**

Run:
```bash
grep -rn 'CommandConsumer\|on_command\|optio:commands\|prefix.*:commands' packages/ 2>&1 | grep -v __pycache__
```

Expected: only references in `docs/` paths or spec / plan files. No matches inside `packages/*/src/`, `packages/*/tests/`, or `packages/*/interop/`. If matches appear in live code, fix and amend the most relevant prior task's commit.

- [ ] **Step 2: Doc-source grep**

Run:
```bash
grep -n 'on_command' AGENTS.md README.md packages/optio-core/README.md
```

Expected: zero matches.

- [ ] **Step 3: Consumer file absent**

Run:
```bash
ls packages/optio-core/src/optio_core/consumer.py 2>&1
```

Expected: `ls: cannot access ...: No such file or directory`.

- [ ] **Step 4: Outcome dataclasses present and exported**

Run:
```bash
python -c "from optio_core import LaunchOutcome, CancelOutcome, DismissOutcome; print('ok')"
```

(Run inside the optio-core package's environment.) Expected: `ok`.

- [ ] **Step 5: RPC adapter has no state-set references**

Run:
```bash
grep -n 'LAUNCHABLE_STATES\|CANCELLABLE_STATES\|DISMISSABLE_STATES' packages/optio-core/src/optio_core/_engine_service.py
```

Expected: zero matches.

- [ ] **Step 6: AGENTS.md contains convergence section**

Run:
```bash
grep -n 'Control-plane convergence' AGENTS.md
```

Expected: one match.

- [ ] **Step 7: Full Python suite**

Run:
```bash
cd /home/csillag/deai/optio && make test
```

Expected: green.

- [ ] **Step 8: Interop**

Run:
```bash
cd /home/csillag/deai/optio && make test-interop
```

Expected: green; scenario count one less than before phase 5.

- [ ] **Step 9: Stream-empty runtime check (manual / optional)**

After running the HTTP test suite end-to-end:

```bash
redis-cli xrange "<db>/<prefix>:commands" - + | wc -l
```

Expected: `0` entries (the engine no longer publishes; nothing reads).

- [ ] **Step 10: Confirm scheduler observability**

Manual sanity: register a task with `metadata={"project": "x"}` and a schedule, register a block matching `{"project": "x"}`, wait for the scheduled fire, look for a WARNING log line containing `launch-blocked` and the process id. (Optional: capture this as an integration test if desired in a follow-up.)

- [ ] **Step 11: Push the branch and open a PR (user-controlled)**

Do **not** push or open a PR automatically. Surface the branch name (`csillag/rpc-migration-phase-5`) to the user along with the summary; the user opens / pushes when ready, per project preference.

---

## Out of scope (recorded here for the implementing engineer)

- `Optio.launch_and_wait` outcome refactor. Keeps raising `LaunchBlocked` and executor exceptions. Do not migrate its tests to outcome assertions.
- `Optio.adhoc_define`. Keeps raising `LaunchBlocked` at define-time.
- `executor.launch_process` internal / child-launch paths. Keep raising.
- `group_cancel`, `group_cancel_and_wait`, `block_launches`, `unblock_launches`. Current return shapes preserved; convergence rule already satisfied.
- Historical design / plan docs under `docs/2026-03-*`, `docs/2026-04-*`, `docs/superpowers/`. Frozen artifacts.
- Architecture diagram refresh.
- Stale `packages/optio-api/dist/publisher.*` local build artifacts (dist/ is git-ignored).
- External-consumer migration (Excavator etc.). Handled out-of-band before phase 5 was unblocked.
