# Launch Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a project-agnostic launch-guard mechanism to optio-core: an async context manager `Optio.block_launches(filter)` that rejects any launch or process definition whose metadata matches the filter, raising a new `LaunchBlocked` exception.

**Architecture:** Single primitive on `Optio`. In-memory dict `_launch_blocks` keyed by `uuid.UUID`. Every doorway that creates or starts a process (`Optio.launch`, `Optio.launch_and_wait`, `Optio.adhoc_define`, `Executor.execute_child`, `Optio._handle_launch`) calls a private `_check_launch_blocks` helper synchronously before any asyncio Task is scheduled or DB write happens. The Redis consumer path catches the exception, logs at WARNING, and ACKs. All other paths let `LaunchBlocked` propagate to the caller.

**Tech Stack:** Python 3.11+, asyncio, motor (Mongo), pytest, pytest-asyncio.

**Design spec:** `docs/2026-04-29-launch-guard-design.md`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/optio-core/src/optio_core/models.py` | Modify | Add `LaunchBlocked` exception class |
| `packages/optio-core/src/optio_core/lifecycle.py` | Modify | Add `_launch_blocks` attribute, `block_launches` async context manager, `_check_launch_blocks` helper, and wire the check into `launch`, `launch_and_wait`, `adhoc_define`, `_handle_launch` |
| `packages/optio-core/src/optio_core/executor.py` | Modify | Wire the check into `execute_child` |
| `packages/optio-core/src/optio_core/__init__.py` | Modify | Export `LaunchBlocked` and `block_launches` |
| `packages/optio-core/tests/test_launch_guard.py` | Create | All launch-guard tests |

---

### Task 1: `LaunchBlocked` exception class

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py`
- Modify: `packages/optio-core/src/optio_core/__init__.py`
- Create: `packages/optio-core/tests/test_launch_guard.py`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-core/tests/test_launch_guard.py` with a single test that imports the new symbol:

```python
"""Tests for the launch-guard mechanism."""

from optio_core.models import LaunchBlocked


def test_launch_blocked_is_runtime_error():
    """LaunchBlocked subclasses RuntimeError so generic except clauses still catch it."""
    err = LaunchBlocked("blocked by filter {'project': 'p1'}; metadata={'project': 'p1'}")
    assert isinstance(err, RuntimeError)
    assert "blocked by filter" in str(err)


def test_launch_blocked_exported_from_package():
    """LaunchBlocked is exported from the top-level optio_core package."""
    import optio_core
    assert optio_core.LaunchBlocked is LaunchBlocked
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd packages/optio-core && pytest tests/test_launch_guard.py -v
```

Expected: FAIL with `ImportError: cannot import name 'LaunchBlocked' from 'optio_core.models'`.

- [ ] **Step 3: Add `LaunchBlocked` to `models.py`**

Append to `packages/optio-core/src/optio_core/models.py`:

```python
class LaunchBlocked(RuntimeError):
    """Raised when a launch is rejected by an active launch block.

    The exception message includes both the matching filter and the
    task metadata so the rejection is traceable from logs alone.
    """
```

- [ ] **Step 4: Export from `optio_core/__init__.py`**

Modify `packages/optio-core/src/optio_core/__init__.py`. Replace the `from optio_core.models import` line and the `__all__` list:

```python
from optio_core.models import TaskInstance, ChildResult, LaunchBlocked
```

And add `"LaunchBlocked"` to `__all__`:

```python
__all__ = [
    "TaskInstance", "ChildResult", "LaunchBlocked",
    "init", "run", "shutdown", "on_command",
    "adhoc_define", "adhoc_delete",
    "launch", "launch_and_wait", "cancel", "dismiss", "resync",
    "get_process", "list_processes",
]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_launch_guard.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py \
        packages/optio-core/src/optio_core/__init__.py \
        packages/optio-core/tests/test_launch_guard.py
git commit -m "feat(optio-core): add LaunchBlocked exception"
```

---

### Task 2: `Optio._launch_blocks` storage and `block_launches` context manager

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/__init__.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_launch_guard.py`:

```python
import pytest
from optio_core.lifecycle import Optio



async def test_block_launches_registers_and_unregisters():
    """block_launches() adds a token to _launch_blocks on enter and removes it on exit."""
    optio = Optio()
    assert optio._launch_blocks == {}

    async with optio.block_launches({"project": "p1"}):
        # Inside: exactly one block registered with the given filter.
        assert len(optio._launch_blocks) == 1
        (token,) = optio._launch_blocks.keys()
        assert optio._launch_blocks[token] == {"project": "p1"}

    # After exit: dict is empty again.
    assert optio._launch_blocks == {}



async def test_block_launches_two_concurrent_same_filter():
    """Two simultaneous block_launches() with the same filter create two distinct tokens."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        async with optio.block_launches({"project": "p1"}):
            assert len(optio._launch_blocks) == 2
        # Inner exited; one block remains.
        assert len(optio._launch_blocks) == 1
    # Outer exited; empty.
    assert optio._launch_blocks == {}



async def test_block_launches_lifted_on_body_exception():
    """The block is removed even when the body raises."""
    optio = Optio()
    with pytest.raises(ValueError, match="boom"):
        async with optio.block_launches({"project": "p1"}):
            assert len(optio._launch_blocks) == 1
            raise ValueError("boom")
    # Block was lifted regardless of the exception.
    assert optio._launch_blocks == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_launch_guard.py -v -k block_launches
```

Expected: FAIL with `AttributeError: 'Optio' object has no attribute '_launch_blocks'` (or similar) on every test.

- [ ] **Step 3: Add `_launch_blocks` attribute and `block_launches` method**

Modify `packages/optio-core/src/optio_core/lifecycle.py`.

Add `import uuid` and `from contextlib import asynccontextmanager` at the top of the file alongside the existing imports.

In `Optio.__init__` (around the existing initialisations like `self._config = None`), add:

```python
self._launch_blocks: dict[uuid.UUID, dict] = {}
```

Add the `block_launches` method on the `Optio` class (place it after `on_command`, before `adhoc_define`):

```python
@asynccontextmanager
async def block_launches(self, filter: dict):
    """Async context manager: while active, reject launches whose
    task metadata matches `filter` (raises LaunchBlocked).

    Multiple concurrent block_launches() calls — overlapping or
    identical filters — stack independently. Each context owns
    its own block; exiting one does not lift another's block.

    An empty filter `{}` matches every task metadata — registering
    it blocks all launches.
    """
    token = uuid.uuid4()
    self._launch_blocks[token] = filter
    try:
        yield
    finally:
        self._launch_blocks.pop(token, None)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_launch_guard.py -v -k block_launches
```

Expected: 3 passed.

- [ ] **Step 5: Export `block_launches` from `__init__.py`**

Modify `packages/optio-core/src/optio_core/__init__.py`. Add a line under the existing `_instance.<method>` aliases:

```python
block_launches = _instance.block_launches
```

And add `"block_launches"` to `__all__`:

```python
__all__ = [
    "TaskInstance", "ChildResult", "LaunchBlocked",
    "init", "run", "shutdown", "on_command",
    "adhoc_define", "adhoc_delete",
    "launch", "launch_and_wait", "cancel", "dismiss", "resync",
    "get_process", "list_processes",
    "block_launches",
]
```

Add a tiny test verifying the export:

```python

async def test_block_launches_exported_from_package():
    """block_launches is exported from the top-level optio_core package."""
    import optio_core
    async with optio_core.block_launches({"project": "p1"}):
        # Uses the module-level _instance singleton.
        assert len(optio_core._instance._launch_blocks) == 1
    assert optio_core._instance._launch_blocks == {}
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_launch_guard.py -v
```

Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/src/optio_core/__init__.py \
        packages/optio-core/tests/test_launch_guard.py
git commit -m "feat(optio-core): add Optio.block_launches context manager"
```

---

### Task 3: `_check_launch_blocks` helper

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_launch_guard.py`:

```python
from optio_core.models import LaunchBlocked



async def test_check_launch_blocks_passes_when_no_blocks_registered():
    """No registered blocks → check is a fast no-op."""
    optio = Optio()
    # Should not raise.
    optio._check_launch_blocks({"project": "p1"})



async def test_check_launch_blocks_raises_when_metadata_matches():
    """Registered block whose filter matches the metadata raises LaunchBlocked."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked, match="project"):
            optio._check_launch_blocks({"project": "p1", "sourceId": "s1"})



async def test_check_launch_blocks_passes_when_metadata_does_not_match():
    """Registered block whose filter does not match the metadata is a no-op."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        optio._check_launch_blocks({"project": "p2"})
        optio._check_launch_blocks({"unrelated": "x"})



async def test_check_launch_blocks_handles_none_metadata():
    """metadata=None is treated as empty dict — empty filter still matches."""
    optio = Optio()
    async with optio.block_launches({}):
        with pytest.raises(LaunchBlocked):
            optio._check_launch_blocks(None)



async def test_check_launch_blocks_empty_filter_blocks_everything():
    """An empty filter `{}` matches every task metadata."""
    optio = Optio()
    async with optio.block_launches({}):
        with pytest.raises(LaunchBlocked):
            optio._check_launch_blocks({"project": "p1"})
        with pytest.raises(LaunchBlocked):
            optio._check_launch_blocks({"anything": "else"})



async def test_check_launch_blocks_message_includes_filter_and_metadata():
    """The LaunchBlocked message contains both the matching filter and the rejected metadata."""
    optio = Optio()
    async with optio.block_launches({"project": "p1"}):
        try:
            optio._check_launch_blocks({"project": "p1", "sourceId": "s1"})
        except LaunchBlocked as e:
            msg = str(e)
            assert "p1" in msg
            assert "s1" in msg
            assert "project" in msg
        else:
            pytest.fail("LaunchBlocked not raised")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_launch_guard.py -v -k _check_launch_blocks
```

Expected: FAIL with `AttributeError: 'Optio' object has no attribute '_check_launch_blocks'` on every test.

- [ ] **Step 3: Add `_check_launch_blocks` to `Optio`**

Modify `packages/optio-core/src/optio_core/lifecycle.py`. Add the import for `matches_filter` if not already present:

```python
from optio_core.models import (
    TaskInstance, OptioConfig, ProcessStatus, ProcessMetadataFilter,
    matches_filter, LaunchBlocked,
)
```

(The exact existing import line varies; add `matches_filter` and `LaunchBlocked` to whatever names are already imported from `optio_core.models`.)

Add the `_check_launch_blocks` method on the `Optio` class (place it adjacent to `block_launches`):

```python
def _check_launch_blocks(self, metadata: dict | None) -> None:
    """Raise LaunchBlocked if `metadata` matches any registered block.

    Fast path: empty `_launch_blocks` returns immediately.
    """
    if not self._launch_blocks:
        return
    md = metadata or {}
    for filter in self._launch_blocks.values():
        if matches_filter(md, filter):
            raise LaunchBlocked(
                f"Launch blocked by filter {filter}; task metadata={md}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_launch_guard.py -v -k _check_launch_blocks
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_launch_guard.py
git commit -m "feat(optio-core): add _check_launch_blocks helper"
```

---

### Task 4: Wire check into `Optio.adhoc_define`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_launch_guard.py`:

```python
from optio_core.models import TaskInstance



async def test_adhoc_define_blocked_when_metadata_matches(mongo_db):
    """adhoc_define raises LaunchBlocked for a task whose metadata matches a registered block.

    Critically, NO process record is created in Mongo — the check happens
    before any DB write.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="adhoc1",
        name="adhoc1",
        metadata={"project": "p1", "sourceId": "s1"},
    )

    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked):
            await optio.adhoc_define(task)

    # Verify no record was created.
    coll = mongo_db["test_processes"]
    assert await coll.find_one({"processId": "adhoc1"}) is None



async def test_adhoc_define_passes_when_metadata_does_not_match(mongo_db):
    """adhoc_define succeeds when the task metadata does not match any block."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="adhoc2",
        name="adhoc2",
        metadata={"project": "p2"},
    )

    async with optio.block_launches({"project": "p1"}):
        proc = await optio.adhoc_define(task)
        assert proc["processId"] == "adhoc2"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_launch_guard.py -v -k adhoc_define
```

Expected: `test_adhoc_define_blocked_when_metadata_matches` FAILS — the call does not raise, and a process record is created.

- [ ] **Step 3: Add the check at the top of `adhoc_define`**

Modify `packages/optio-core/src/optio_core/lifecycle.py`. At the beginning of `Optio.adhoc_define` (immediately after the docstring, before `from optio_core.store import ...`), add:

```python
self._check_launch_blocks(task.metadata)
```

The method signature and the rest of the body are unchanged.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_launch_guard.py -v -k adhoc_define
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_launch_guard.py
git commit -m "feat(optio-core): block adhoc_define on matching launch block"
```

---

### Task 5: Wire check into `Executor.execute_child`

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_launch_guard.py`:

```python

async def test_execute_child_blocked_when_parent_metadata_matches(mongo_db):
    """A parent task with matching metadata observes LaunchBlocked from run_child.

    Children inherit parent metadata; the block matches the parent's project
    so the run_child call is rejected.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    observed = {}

    async def child_task(ctx):
        observed["child_ran"] = True

    async def parent_task(ctx):
        try:
            await ctx.run_child(
                execute=child_task,
                process_id="child1",
                name="child1",
                params={},
            )
        except LaunchBlocked as e:
            observed["blocked"] = str(e)

    parent = TaskInstance(
        execute=parent_task,
        process_id="parent1",
        name="parent1",
        metadata={"project": "p1"},
    )
    optio._executor.register_tasks([parent])
    await optio.adhoc_define(parent)

    async with optio.block_launches({"project": "p1"}):
        # The parent itself was defined BEFORE the block (above), so it
        # can run; but its run_child will be blocked because the child
        # inherits {"project": "p1"} from parent_ctx.metadata.
        await optio.launch_and_wait("parent1")

    assert "blocked" in observed
    assert "child_ran" not in observed
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_launch_guard.py::test_execute_child_blocked_when_parent_metadata_matches -v
```

Expected: FAIL — `observed["blocked"]` is missing because `run_child` succeeds and the child task runs.

- [ ] **Step 3: Wire the check in `Executor.execute_child`**

Modify `packages/optio-core/src/optio_core/executor.py`. The Executor needs a reference back to the Optio instance to call `_check_launch_blocks`. Two options:

**Option A (preferred):** Pass an `optio` reference into `Executor.__init__` and store it.

In `Executor.__init__`, change the signature:

```python
def __init__(
    self,
    db: AsyncIOMotorDatabase,
    prefix: str,
    services: dict[str, Any],
    optio: "Optio | None" = None,
):
    self._db = db
    self._prefix = prefix
    self._services = services
    self._optio = optio
    self._cancellation_flags: dict[ObjectId, asyncio.Event] = {}
    self._task_registry: dict[str, TaskInstance] = {}
```

In `Optio.init` in `lifecycle.py`, where the executor is constructed (`self._executor = Executor(mongo_db, prefix, services)`), add the `optio=self` argument:

```python
self._executor = Executor(mongo_db, prefix, services, optio=self)
```

Then at the top of `Executor.execute_child` (before `order = parent_ctx._next_child_order()`), add:

```python
if self._optio is not None:
    self._optio._check_launch_blocks(parent_ctx.metadata)
```

The `if self._optio is not None` guard preserves backward compatibility for tests that construct `Executor` standalone (e.g. `tests/test_executor.py:19` does `executor = Executor(mongo_db, "test", {})`).

- [ ] **Step 4: Run tests to verify the new test passes and existing executor tests still pass**

```bash
pytest tests/test_launch_guard.py::test_execute_child_blocked_when_parent_metadata_matches -v
pytest tests/test_executor.py -v
```

Expected: new test passes; all `test_executor.py` tests still pass.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py \
        packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_launch_guard.py
git commit -m "feat(optio-core): block execute_child on matching launch block"
```

---

### Task 6: Wire check into `Optio.launch` and `Optio.launch_and_wait`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_launch_guard.py`:

```python

async def test_launch_blocked_when_task_metadata_matches(mongo_db):
    """Optio.launch raises LaunchBlocked synchronously for a task whose metadata matches a block.

    The check happens before the asyncio Task is scheduled, so the caller
    observes the exception directly.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="launch1",
        name="launch1",
        metadata={"project": "p1"},
    )
    optio._executor.register_tasks([task])
    # Use upsert_process to create the record so launch can find it.
    from optio_core.store import upsert_process
    await upsert_process(mongo_db, "test", task)

    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked):
            await optio.launch("launch1")



async def test_launch_and_wait_blocked_when_task_metadata_matches(mongo_db):
    """Optio.launch_and_wait raises LaunchBlocked for a blocked task."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="launch2",
        name="launch2",
        metadata={"project": "p1"},
    )
    optio._executor.register_tasks([task])
    from optio_core.store import upsert_process
    await upsert_process(mongo_db, "test", task)

    async with optio.block_launches({"project": "p1"}):
        with pytest.raises(LaunchBlocked):
            await optio.launch_and_wait("launch2")



async def test_launch_passes_when_task_metadata_does_not_match(mongo_db):
    """Launches with non-matching metadata succeed normally."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="launch3",
        name="launch3",
        metadata={"project": "p2"},
    )
    optio._executor.register_tasks([task])
    from optio_core.store import upsert_process
    await upsert_process(mongo_db, "test", task)

    async with optio.block_launches({"project": "p1"}):
        # No exception; awaitable returns normally.
        await optio.launch_and_wait("launch3")



async def test_launch_passes_when_pid_unknown(mongo_db):
    """An unknown process_id has no metadata to match — falls through to the
    existing 'no execute function found' failure path inside the executor.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async with optio.block_launches({"project": "p1"}):
        # No raise; the launch is harmless because no record exists.
        await optio.launch("nope")
```

- [ ] **Step 2: Run tests to verify the new ones fail**

```bash
pytest tests/test_launch_guard.py -v -k "launch_blocked or launch_and_wait_blocked"
```

Expected: FAIL — current implementations of `launch` / `launch_and_wait` schedule the asyncio Task without checking blocks; no `LaunchBlocked` is raised at the call site.

- [ ] **Step 3: Add the synchronous check to `launch` and `launch_and_wait`**

Modify `packages/optio-core/src/optio_core/lifecycle.py`. Find the existing `launch` and `launch_and_wait` methods on `Optio` (around lifecycle.py:186-200):

Replace `Optio.launch`:

```python
async def launch(self, process_id: str, resume: bool = False) -> None:
    """Fire-and-forget launch. Returns immediately, process runs in background.

    If resume is True, the task is launched with ctx.resume=True so it can
    restore previous state rather than start fresh.

    Raises LaunchBlocked if a registered launch block matches the task's metadata.
    """
    task = self._executor._task_registry.get(process_id)
    if task is not None:
        self._check_launch_blocks(task.metadata)
    asyncio.create_task(self._executor.launch_process(process_id, resume=resume))
```

Replace `Optio.launch_and_wait`:

```python
async def launch_and_wait(self, process_id: str, resume: bool = False) -> None:
    """Launch and wait for the process to complete. Full progress tracking.

    If resume is True, the task is launched with ctx.resume=True so it can
    restore previous state rather than start fresh.

    Raises LaunchBlocked if a registered launch block matches the task's metadata.
    """
    task = self._executor._task_registry.get(process_id)
    if task is not None:
        self._check_launch_blocks(task.metadata)
    await self._executor.launch_process(process_id, resume=resume)
```

- [ ] **Step 4: Run all launch-guard tests**

```bash
pytest tests/test_launch_guard.py -v
```

Expected: all tests pass so far (Task 1–6 cases).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_launch_guard.py
git commit -m "feat(optio-core): block Optio.launch / launch_and_wait synchronously"
```

---

### Task 7: Wire check into Redis launch consumer (`Optio._handle_launch`)

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_launch_guard.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_launch_guard.py`:

```python
import logging



async def test_handle_launch_blocked_logs_warning_and_does_not_launch(mongo_db, caplog):
    """The Redis launch command consumer catches LaunchBlocked, logs a WARNING, and ACKs.

    No process state transition occurs; no exception escapes _handle_launch.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    started = {"count": 0}

    async def noop(ctx):
        started["count"] += 1

    task = TaskInstance(
        execute=noop,
        process_id="consumer1",
        name="consumer1",
        metadata={"project": "p1"},
    )
    optio._executor.register_tasks([task])
    from optio_core.store import upsert_process
    await upsert_process(mongo_db, "test", task)

    caplog.set_level(logging.WARNING)
    async with optio.block_launches({"project": "p1"}):
        # _handle_launch is the dispatcher invoked by the Redis CommandConsumer.
        await optio._handle_launch({"processId": "consumer1"})

    # No process started.
    assert started["count"] == 0

    # A WARNING was emitted, naming the rejected processId.
    assert any(
        rec.levelno == logging.WARNING and "consumer1" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_launch_guard.py::test_handle_launch_blocked_logs_warning_and_does_not_launch -v
```

Expected: FAIL — current `_handle_launch` does not catch LaunchBlocked; the exception propagates out (or is wrapped in some other way) and no warning is logged.

- [ ] **Step 3: Wrap the consumer dispatch with the check + log**

Modify `packages/optio-core/src/optio_core/lifecycle.py`. Find the existing `_handle_launch` method (around lifecycle.py:447-451):

Replace it with:

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

- [ ] **Step 4: Run tests to verify the new one passes and existing tests still pass**

```bash
pytest tests/test_launch_guard.py -v
pytest tests/ -v --timeout=30
```

Expected: all launch-guard tests pass; nothing in the existing test suite regresses.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py \
        packages/optio-core/tests/test_launch_guard.py
git commit -m "feat(optio-core): Redis consumer logs and ACKs blocked launches"
```

---

### Task 8: Concurrency / nesting integration tests

**Files:**
- Modify: `packages/optio-core/tests/test_launch_guard.py`

This task adds end-to-end tests that exercise concurrency and nesting through the public API (no implementation changes — the design supports these correctly via uuid tokens, but tests lock in the expected behaviour).

- [ ] **Step 1: Write the tests**

Append to `packages/optio-core/tests/test_launch_guard.py`:

```python

async def test_two_concurrent_blocks_both_required_to_unblock(mongo_db):
    """Two concurrent block_launches() with the same filter — exiting one
    keeps the block in force; both must exit before launches are accepted.
    """
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="conc1",
        name="conc1",
        metadata={"project": "p1"},
    )
    optio._executor.register_tasks([task])
    from optio_core.store import upsert_process
    await upsert_process(mongo_db, "test", task)

    outer = optio.block_launches({"project": "p1"})
    inner = optio.block_launches({"project": "p1"})
    await outer.__aenter__()
    await inner.__aenter__()
    try:
        # Both registered.
        with pytest.raises(LaunchBlocked):
            await optio.launch_and_wait("conc1")
    finally:
        await inner.__aexit__(None, None, None)

    # Inner exited, outer still in force — still blocked.
    with pytest.raises(LaunchBlocked):
        await optio.launch_and_wait("conc1")

    await outer.__aexit__(None, None, None)
    # Both exited — launch succeeds.
    await optio.launch_and_wait("conc1")



async def test_nested_block_in_same_coroutine(mongo_db):
    """Nested `async with` adds a second token; both exits required."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async with optio.block_launches({"project": "p1"}):
        async with optio.block_launches({"project": "p1"}):
            assert len(optio._launch_blocks) == 2
        # Inner exited.
        assert len(optio._launch_blocks) == 1
    assert optio._launch_blocks == {}



async def test_overlapping_filters(mongo_db):
    """A launch is blocked iff matches_filter is True for ANY registered filter."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")

    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="ovl1",
        name="ovl1",
        metadata={"project": "p1", "tenant": "t2"},
    )
    optio._executor.register_tasks([task])
    from optio_core.store import upsert_process
    await upsert_process(mongo_db, "test", task)

    # Two non-overlapping filters; the task matches the second.
    async with optio.block_launches({"tenant": "t1"}):
        async with optio.block_launches({"tenant": "t2"}):
            with pytest.raises(LaunchBlocked):
                await optio.launch_and_wait("ovl1")
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_launch_guard.py -v
```

Expected: all tests pass; no implementation changes required.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_launch_guard.py
git commit -m "test(optio-core): cover concurrency and overlapping launch-block filters"
```

---

### Task 9: Final integration pass — full optio-core test suite

**Files:**
- (none — verification only)

- [ ] **Step 1: Run the entire optio-core test suite**

```bash
cd packages/optio-core && pytest tests/ -v --timeout=60
```

Expected: all tests pass, including the new `test_launch_guard.py` (target ≥17 launch-guard tests across Tasks 1–8) and every pre-existing test file (`test_executor.py`, `test_lifecycle_reconciliation.py`, `test_consumer.py`, etc.).

- [ ] **Step 2: If any pre-existing test fails, diagnose and fix**

The most likely regression source is the `Executor.__init__` signature change in Task 5 (added `optio` parameter with default `None`). Existing tests construct `Executor(mongo_db, "test", {})` — the new signature is backward-compatible because `optio` defaults to `None`. If a test fails with a TypeError on Executor construction, audit Task 5's signature change.

If a regression is found, fix it inline (do not roll back the new feature) and add a regression test.

- [ ] **Step 3: Run the launch-guard tests one more time in isolation as a sanity check**

```bash
pytest tests/test_launch_guard.py -v
```

Expected: every launch-guard test passes; no warnings about deprecated APIs or unhandled coroutines.

- [ ] **Step 4: Commit anything outstanding**

If Step 2 produced fixes:

```bash
git add <fixed files>
git commit -m "fix(optio-core): <specific fix>"
```

If everything was already green, skip this step.

---

## Cross-references

- **Spec:** `docs/2026-04-29-launch-guard-design.md` (committed in this branch).
- **Related design:** deadline-driven cooperative cancel (`docs/2026-04-29-deadline-driven-cancel-design.md` on branch `csillag/deadline-cancel`). Independent of this work; either can land first.
- **Downstream consumer:** `~/deai/excavator/docs/2026-04-29-project-delete-design.md` references this guard as a prerequisite for project-delete teardown safety.
