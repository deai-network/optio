# Persistent Launch Blocks ("Perma-Ban") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Mongo-persisted launch blocks to the `Optio` class. Blocks installed with `persist=True` survive process restarts; blocks are loaded into the in-memory `_launch_blocks` dict during `Optio.init()`. New `unblock_launches(filter)` removes them. `block_launches`, `group_cancel`, and `group_cancel_and_wait` accept new keyword-only kwargs `persist=False` and `reason=None`.

**Architecture:** A new module `optio_core/_launch_block_store.py` exposes pure async functions (`load_all`, `upsert_block`, `delete_by_filter`) over a `{prefix}_launch_blocks` Mongo collection. `Optio` calls those functions on init/persist/unblock paths. The in-memory dict is changed from `dict[UUID, ProcessMetadataFilter]` to `dict[UUID, _BlockEntry]` (small dataclass holding filter and reason) so that `LaunchBlocked` can quote the reason. The existing transient `block_launches` semantics are preserved — the new flag is purely additive.

**Tech Stack:** Python 3.11+, asyncio, motor (MongoDB), pytest + pytest-asyncio. Edits live entirely in `packages/optio-core/`.

**Spec:** `docs/2026-04-30-persistent-launch-blocks-design.md`. Read it before starting.

**Branch:** `csillag/perma-ban`. Already created and checked out (worktree at `.worktrees/perma-ban`).

---

## File Structure

**Create:**
- `packages/optio-core/src/optio_core/_launch_block_store.py` — pure async helpers over the persistent-blocks collection.
- `packages/optio-core/tests/test_persistent_launch_blocks.py` — all tests for this feature, store-level + Optio-level.

**Modify:**
- `packages/optio-core/src/optio_core/lifecycle.py`
  - introduce internal `_BlockEntry` dataclass and migrate `_launch_blocks` value type to it
  - extend `block_launches` with `persist` and `reason` kwargs
  - extend `group_cancel` and `group_cancel_and_wait` with `persist` and `reason` kwargs and the constraint
  - add `unblock_launches`
  - load persisted blocks at the end of `init()`
  - extend the `LaunchBlocked` raise-site message
- `packages/optio-core/src/optio_core/__init__.py`
  - export `unblock_launches`

No file splits. Everything new lives next to existing pieces.

---

## Standard Patterns Used Throughout

These appear in many tests and tasks below — included once here so each task can reference them without repeating.

**Pytest async marker (top of every test file):**
```python
pytestmark = pytest.mark.asyncio
```

**Boilerplate for spinning up an Optio with running tasks (already used by `test_group_cancel.py`):**
```python
import asyncio
import pytest
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


async def _start_optio(mongo_db, prefix, tasks=(), cancel_grace_seconds=2.0):
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

**MongoDB:** the `mongo_db` fixture in `tests/conftest.py` provides a fresh test database; the user's environment requires a running MongoDB at `MONGO_URL` (default `mongodb://localhost:27017`). Memory says: MongoDB only via Docker — make sure a container is running before executing the tests.

**Test command:** `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v` (run from the repo root). For running a single test: append `::test_name`.

**Commit style:** Conventional Commits, no `Co-Authored-By` trailer. Examples seen in `git log`: `feat(...)`, `fix(...)`, `docs(...)`, `test(...)`. Subject scope `persistent-launch-blocks`.

---

## Task 1: Pure-function store module

**Files:**
- Create: `packages/optio-core/src/optio_core/_launch_block_store.py`
- Create: `packages/optio-core/tests/test_persistent_launch_blocks.py` (initial — store-level tests only)

**Goal:** A self-contained module of async functions over a Mongo collection ref. No coupling to `Optio`. TDD-driven.

- [ ] **Step 1.1: Write failing tests for `load_all` / `upsert_block` / `delete_by_filter`**

Create `packages/optio-core/tests/test_persistent_launch_blocks.py`:

```python
"""Tests for persistent launch blocks ("perma-ban").

Spec: docs/2026-04-30-persistent-launch-blocks-design.md
"""
import asyncio
import pytest

from optio_core import _launch_block_store as store
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


# ---------- Store-level tests ----------

def _coll(mongo_db, prefix="optio_test"):
    return store.collection(mongo_db, prefix)


async def test_load_all_empty(mongo_db):
    """load_all returns [] for a fresh / missing collection."""
    rows = await store.load_all(_coll(mongo_db))
    assert rows == []


async def test_upsert_inserts_record(mongo_db):
    """First upsert with a new filter inserts a record."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason="bad behavior")

    rows = await store.load_all(coll)
    assert len(rows) == 1
    row = rows[0]
    assert row.filter == {"tenant": "acme"}
    assert row.reason == "bad behavior"
    # createdAt is set
    assert row.created_at is not None


async def test_upsert_dedupes_by_exact_filter(mongo_db):
    """Second upsert with the same filter does NOT create a new record."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason="x")
    await store.upsert_block(coll, {"tenant": "acme"}, reason="y")

    rows = await store.load_all(coll)
    assert len(rows) == 1
    # Reason concatenated when both sides non-null.
    assert rows[0].reason == "x AND y"


async def test_upsert_reason_concat_null_handling(mongo_db):
    """Reason concat skips when either side is null."""
    coll = _coll(mongo_db)

    # Existing reason None, new reason set -> existing kept (None).
    await store.upsert_block(coll, {"a": 1}, reason=None)
    await store.upsert_block(coll, {"a": 1}, reason="later")
    rows = await store.load_all(coll)
    assert [r.reason for r in rows if r.filter == {"a": 1}] == [None]

    # Existing reason set, new reason None -> existing kept.
    await store.upsert_block(coll, {"b": 2}, reason="initial")
    await store.upsert_block(coll, {"b": 2}, reason=None)
    rows = await store.load_all(coll)
    assert [r.reason for r in rows if r.filter == {"b": 2}] == ["initial"]


async def test_upsert_different_filter_inserts_separate_record(mongo_db):
    """Different filters yield separate records."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)
    await store.upsert_block(coll, {"tenant": "globex"}, reason=None)

    rows = await store.load_all(coll)
    filters = sorted([tuple(sorted(r.filter.items())) for r in rows])
    assert filters == [(("tenant", "acme"),), (("tenant", "globex"),)]


async def test_delete_by_filter_removes_matching(mongo_db):
    """delete_by_filter deletes all matching rows; returns count."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)
    await store.upsert_block(coll, {"tenant": "globex"}, reason=None)

    deleted = await store.delete_by_filter(coll, {"tenant": "acme"})
    assert deleted == 1

    rows = await store.load_all(coll)
    assert len(rows) == 1
    assert rows[0].filter == {"tenant": "globex"}


async def test_delete_by_filter_no_match_is_noop(mongo_db):
    """delete_by_filter returns 0 when no record matches."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)

    deleted = await store.delete_by_filter(coll, {"tenant": "nonexistent"})
    assert deleted == 0
    rows = await store.load_all(coll)
    assert len(rows) == 1
```

- [ ] **Step 1.2: Run the tests — they should fail with import error**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k store`

Expected: collection error / `ModuleNotFoundError: No module named 'optio_core._launch_block_store'` (or equivalent).

- [ ] **Step 1.3: Implement the store module**

Create `packages/optio-core/src/optio_core/_launch_block_store.py`:

```python
"""Persistent launch-block store — pure async helpers over a Mongo collection.

Spec: docs/2026-04-30-persistent-launch-blocks-design.md
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import ProcessMetadataFilter


@dataclass
class StoredLaunchBlock:
    """One row of the persistent launch-blocks collection."""
    filter: ProcessMetadataFilter
    created_at: datetime
    reason: str | None


def collection(db: AsyncIOMotorDatabase, prefix: str):
    """Return the persistent launch-blocks collection for `prefix`."""
    return db[f"{prefix}_launch_blocks"]


async def load_all(coll) -> list[StoredLaunchBlock]:
    """Load every record. Empty/missing collection -> []."""
    rows: list[StoredLaunchBlock] = []
    async for doc in coll.find({}):
        rows.append(StoredLaunchBlock(
            filter=doc["filter"],
            created_at=doc["createdAt"],
            reason=doc.get("reason"),
        ))
    return rows


async def upsert_block(
    coll,
    launch_filter: ProcessMetadataFilter,
    reason: str | None,
) -> None:
    """Insert a new record OR dedupe-update an existing one.

    Existing record matched by exact filter equality. On dedupe, when both
    the existing reason and `reason` are non-null, the stored reason is
    set to ``f"{existing} AND {reason}"``. Otherwise the existing reason is
    left unchanged.
    """
    existing = await coll.find_one({"filter": launch_filter})
    if existing is None:
        await coll.insert_one({
            "filter": launch_filter,
            "createdAt": datetime.now(timezone.utc),
            "reason": reason,
        })
        return

    # Dedupe path
    existing_reason = existing.get("reason")
    if existing_reason is not None and reason is not None:
        new_reason = f"{existing_reason} AND {reason}"
        await coll.update_one(
            {"_id": existing["_id"]},
            {"$set": {"reason": new_reason}},
        )
    # else: keep existing record's reason untouched


async def delete_by_filter(
    coll,
    launch_filter: ProcessMetadataFilter,
) -> int:
    """Delete every record whose filter equals `launch_filter` exactly.

    Returns the number of rows deleted.
    """
    result = await coll.delete_many({"filter": launch_filter})
    return result.deleted_count
```

- [ ] **Step 1.4: Run the tests — they should pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k store`

Expected: all 6 store-level tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add packages/optio-core/src/optio_core/_launch_block_store.py packages/optio-core/tests/test_persistent_launch_blocks.py
git commit -m "feat(persistent-launch-blocks): add Mongo-backed launch-block store"
```

---

## Task 2: Internal `_BlockEntry` dataclass

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

**Goal:** Replace `dict[UUID, ProcessMetadataFilter]` with `dict[UUID, _BlockEntry]` (filter + reason) so `LaunchBlocked` can quote the reason. Pure refactor — no behavior change yet.

- [ ] **Step 2.1: Verify existing tests pass before refactor**

Run: `pytest packages/optio-core/tests/test_launch_guard.py packages/optio-core/tests/test_group_cancel.py -v`

Expected: all PASS. Capture the count for comparison post-refactor.

- [ ] **Step 2.2: Add `_BlockEntry` dataclass and update field type**

In `packages/optio-core/src/optio_core/lifecycle.py`:

Near the top of the file (after imports, before `class Optio`), add:

```python
from dataclasses import dataclass


@dataclass
class _BlockEntry:
    """One in-memory launch-block entry."""
    filter: ProcessMetadataFilter
    reason: str | None
```

In `Optio.__init__`, change line 48 from:

```python
        self._launch_blocks: dict[uuid.UUID, ProcessMetadataFilter] = {}
```

to:

```python
        self._launch_blocks: dict[uuid.UUID, _BlockEntry] = {}
```

- [ ] **Step 2.3: Update `block_launches` to construct `_BlockEntry`**

Replace the body of `block_launches` (lines 137–153) with:

```python
    @asynccontextmanager
    async def block_launches(self, launch_filter: ProcessMetadataFilter) -> AsyncIterator[None]:
        """Async context manager: while active, reject launches whose
        task metadata matches `launch_filter` (raises LaunchBlocked).

        Multiple concurrent block_launches() calls — overlapping or
        identical filters — stack independently. Each context owns
        its own block; exiting one does not lift another's block.

        An empty filter `{}` matches every task metadata — registering
        it blocks all launches.
        """
        token = uuid.uuid4()
        self._launch_blocks[token] = _BlockEntry(filter=launch_filter, reason=None)
        try:
            yield
        finally:
            self._launch_blocks.pop(token, None)
```

(`reason=None` is correct: this is the transient signature today; persist+reason wiring lands in Task 3.)

- [ ] **Step 2.4: Update `_check_launch_blocks` to walk entries**

Replace the body of `_check_launch_blocks` (lines 155–167) with:

```python
    def _check_launch_blocks(self, metadata: ProcessMetadataFilter | None) -> None:
        """Raise LaunchBlocked if `metadata` matches any registered block.

        Fast path: empty `_launch_blocks` returns immediately.
        """
        if not self._launch_blocks:
            return
        md = metadata or {}
        for entry in self._launch_blocks.values():
            if matches_filter(md, entry.filter):
                msg = f"Launch blocked by filter {entry.filter}; task metadata={md}"
                if entry.reason is not None:
                    msg += f"; reason={entry.reason}"
                raise LaunchBlocked(msg)
```

- [ ] **Step 2.5: Run the prior tests — must still pass**

Run: `pytest packages/optio-core/tests/test_launch_guard.py packages/optio-core/tests/test_group_cancel.py -v`

Expected: identical PASS count as Step 2.1.

- [ ] **Step 2.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py
git commit -m "refactor(persistent-launch-blocks): introduce _BlockEntry for in-memory launch blocks"
```

---

## Task 3: `block_launches(persist, reason)` flag

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_persistent_launch_blocks.py`

**Goal:** Add `persist=False` and `reason=None` kwargs. When `persist=True`, write a record on entry and skip removal on exit. When `persist=False`, behavior is unchanged.

- [ ] **Step 3.1: Write failing tests**

Append to `packages/optio-core/tests/test_persistent_launch_blocks.py`:

```python
# ---------- block_launches(persist=True) ----------

async def test_block_launches_persist_writes_record_and_blocks(mongo_db):
    """persist=True writes a Mongo record and the block stays after exit."""
    optio, run_task = await _start_optio(mongo_db, "optio_p1", tasks=())
    try:
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="r"):
            pass  # exit immediately
        # Memory: block is still there.
        assert any(
            entry.filter == {"tenant": "acme"} and entry.reason == "r"
            for entry in optio._launch_blocks.values()
        )
        # DB: one record.
        rows = await store.load_all(store.collection(mongo_db, "optio_p1"))
        assert len(rows) == 1
        assert rows[0].reason == "r"
    finally:
        await _stop_optio(optio, run_task)


async def test_block_launches_persist_false_unchanged(mongo_db):
    """persist=False (default) behaves exactly as today: block lifted on exit, no DB record."""
    optio, run_task = await _start_optio(mongo_db, "optio_p2", tasks=())
    try:
        async with optio.block_launches({"tenant": "acme"}):
            assert len(optio._launch_blocks) == 1
        assert len(optio._launch_blocks) == 0
        rows = await store.load_all(store.collection(mongo_db, "optio_p2"))
        assert rows == []
    finally:
        await _stop_optio(optio, run_task)


async def test_block_launches_persist_dedupes_db_keeps_memory(mongo_db):
    """Two persist calls with same filter -> one DB row, two memory entries."""
    optio, run_task = await _start_optio(mongo_db, "optio_p3", tasks=())
    try:
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="a"):
            pass
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="b"):
            pass
        rows = await store.load_all(store.collection(mongo_db, "optio_p3"))
        assert len(rows) == 1
        assert rows[0].reason == "a AND b"
        # Memory: two entries (one per call).
        matching = [e for e in optio._launch_blocks.values() if e.filter == {"tenant": "acme"}]
        assert len(matching) == 2
    finally:
        await _stop_optio(optio, run_task)


async def test_launch_blocked_message_includes_reason(mongo_db):
    """LaunchBlocked.args[0] includes '; reason=...' when block has a reason."""
    async def _noop_execute(ctx):  # pragma: no cover - never reached
        return

    task = TaskInstance(
        execute=_noop_execute,
        process_id="p_banned",
        name="banned task",
        metadata={"tenant": "acme"},
    )

    optio, run_task = await _start_optio(mongo_db, "optio_p4", tasks=(task,))
    try:
        async with optio.block_launches({"tenant": "acme"}, persist=True, reason="r"):
            pass  # block stays
        with pytest.raises(LaunchBlocked) as excinfo:
            await optio.launch("p_banned")
        assert "; reason=r" in str(excinfo.value)
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 3.2: Run the new tests — they should fail**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k "persist or reason"`

Expected: all 4 tests FAIL with `TypeError: block_launches() got an unexpected keyword argument 'persist'` (or similar).

- [ ] **Step 3.3: Add `persist` and `reason` kwargs to `block_launches`**

Replace `block_launches` in `lifecycle.py` with:

```python
    @asynccontextmanager
    async def block_launches(
        self,
        launch_filter: ProcessMetadataFilter,
        *,
        persist: bool = False,
        reason: str | None = None,
    ) -> AsyncIterator[None]:
        """Async context manager: while active, reject launches whose
        task metadata matches `launch_filter` (raises LaunchBlocked).

        Multiple concurrent block_launches() calls — overlapping or
        identical filters — stack independently. Each context owns
        its own block; exiting one does not lift another's block.

        An empty filter `{}` matches every task metadata — registering
        it blocks all launches.

        When `persist=True`, a Mongo record is written on entry and the
        block remains active after the context manager exits. `reason`
        is stored on the record (default None). Spec:
        docs/2026-04-30-persistent-launch-blocks-design.md.
        """
        token = uuid.uuid4()
        self._launch_blocks[token] = _BlockEntry(filter=launch_filter, reason=reason)
        if persist:
            from optio_core import _launch_block_store as _lb_store
            coll = _lb_store.collection(
                self._config.mongo_db, self._config.prefix,
            )
            await _lb_store.upsert_block(coll, launch_filter, reason)
        try:
            yield
        finally:
            if not persist:
                self._launch_blocks.pop(token, None)
```

- [ ] **Step 3.4: Run the new tests — they should pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k "persist or reason"`

Expected: all 4 tests PASS.

- [ ] **Step 3.5: Run prior tests — must still pass**

Run: `pytest packages/optio-core/tests/test_launch_guard.py packages/optio-core/tests/test_group_cancel.py -v`

Expected: same PASS count as before.

- [ ] **Step 3.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_persistent_launch_blocks.py
git commit -m "feat(persistent-launch-blocks): add persist/reason kwargs to block_launches"
```

---

## Task 4: Load persisted blocks in `Optio.init`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_persistent_launch_blocks.py`

**Goal:** During `init()`, after migrations and before `_sync_definitions`, load every record from the launch-blocks collection and seed `_launch_blocks` with one entry per record.

- [ ] **Step 4.1: Write failing test for load-on-init**

Append to `packages/optio-core/tests/test_persistent_launch_blocks.py`:

```python
# ---------- Load-on-init ----------

async def test_init_loads_persisted_blocks(mongo_db):
    """Pre-existing records in `{prefix}_launch_blocks` block matching launches after init."""
    # Seed the DB before init.
    coll = store.collection(mongo_db, "optio_init1")
    await store.upsert_block(coll, {"tenant": "banned"}, reason="precooked")

    async def _noop_execute(ctx):  # pragma: no cover - never reached
        return

    task = TaskInstance(
        execute=_noop_execute,
        process_id="p_x",
        name="x",
        metadata={"tenant": "banned"},
    )

    optio, run_task = await _start_optio(mongo_db, "optio_init1", tasks=(task,))
    try:
        # The pre-existing block must be loaded.
        assert any(
            entry.filter == {"tenant": "banned"} and entry.reason == "precooked"
            for entry in optio._launch_blocks.values()
        )
        with pytest.raises(LaunchBlocked) as excinfo:
            await optio.launch("p_x")
        assert "; reason=precooked" in str(excinfo.value)
    finally:
        await _stop_optio(optio, run_task)


async def test_init_with_no_blocks_works(mongo_db):
    """Empty / missing collection produces an empty load."""
    optio, run_task = await _start_optio(mongo_db, "optio_init2", tasks=())
    try:
        assert optio._launch_blocks == {}
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 4.2: Run the new tests — they should fail**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py::test_init_loads_persisted_blocks -v`

Expected: FAIL — pre-existing record not in `_launch_blocks`.

- [ ] **Step 4.3: Add load-on-init step**

In `lifecycle.py`, in `Optio.init()` (around line 113, just after the `await fw_migrations.run(...)` line and before the scheduler is created), insert:

```python
        # Load persisted launch blocks ("perma-bans"). Spec:
        # docs/2026-04-30-persistent-launch-blocks-design.md.
        await self._load_persisted_blocks()
```

Then add a new private method on `Optio` (place it next to `_check_launch_blocks`):

```python
    async def _load_persisted_blocks(self) -> None:
        """Load every record from `{prefix}_launch_blocks` into the in-memory
        `_launch_blocks` dict. Each record gets a fresh UUID token. Empty or
        missing collection produces an empty load.
        """
        from optio_core import _launch_block_store as _lb_store
        coll = _lb_store.collection(
            self._config.mongo_db, self._config.prefix,
        )
        rows = await _lb_store.load_all(coll)
        for row in rows:
            token = uuid.uuid4()
            self._launch_blocks[token] = _BlockEntry(
                filter=row.filter, reason=row.reason,
            )
        if rows:
            logger.info(f"Loaded {len(rows)} persistent launch block(s)")
```

- [ ] **Step 4.4: Run the new tests — they should pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py::test_init_loads_persisted_blocks packages/optio-core/tests/test_persistent_launch_blocks.py::test_init_with_no_blocks_works -v`

Expected: both PASS.

- [ ] **Step 4.5: Run all earlier tests in the file — must still pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v`

Expected: every test PASS.

- [ ] **Step 4.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_persistent_launch_blocks.py
git commit -m "feat(persistent-launch-blocks): load persisted blocks during Optio.init"
```

---

## Task 5: `unblock_launches`

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/__init__.py`
- Modify: `packages/optio-core/tests/test_persistent_launch_blocks.py`

**Goal:** Add `Optio.unblock_launches(filter) -> int`. Removes every Mongo record AND every in-memory entry whose filter equals `filter` exactly. Returns the count of in-memory entries removed.

- [ ] **Step 5.1: Write failing tests**

Append to `packages/optio-core/tests/test_persistent_launch_blocks.py`:

```python
# ---------- unblock_launches ----------

async def test_unblock_launches_removes_memory_and_db(mongo_db):
    """unblock_launches removes matching memory entries and DB rows; returns count."""
    optio, run_task = await _start_optio(mongo_db, "optio_u1", tasks=())
    try:
        async with optio.block_launches({"a": 1}, persist=True, reason="r1"):
            pass
        async with optio.block_launches({"a": 1}, persist=True, reason="r2"):
            pass
        async with optio.block_launches({"b": 2}, persist=True, reason=None):
            pass
        # Sanity: 3 memory entries, 2 DB rows ({a:1} dedup'd, {b:2} separate).
        assert len(optio._launch_blocks) == 3
        rows = await store.load_all(store.collection(mongo_db, "optio_u1"))
        assert len(rows) == 2

        removed = await optio.unblock_launches({"a": 1})
        assert removed == 2  # two memory entries with filter {a:1}

        # Memory: only {b:2} remains.
        assert all(e.filter != {"a": 1} for e in optio._launch_blocks.values())
        # DB: only {b:2} remains.
        rows = await store.load_all(store.collection(mongo_db, "optio_u1"))
        assert len(rows) == 1
        assert rows[0].filter == {"b": 2}
    finally:
        await _stop_optio(optio, run_task)


async def test_unblock_launches_no_match_returns_zero(mongo_db):
    """No match -> 0; no error."""
    optio, run_task = await _start_optio(mongo_db, "optio_u2", tasks=())
    try:
        removed = await optio.unblock_launches({"never": "set"})
        assert removed == 0
    finally:
        await _stop_optio(optio, run_task)


async def test_unblock_removes_transient_block_too(mongo_db):
    """Transient (persist=False) blocks with the same filter are also removed."""
    optio, run_task = await _start_optio(mongo_db, "optio_u3", tasks=())
    try:
        # Install a persistent and a transient block with same filter.
        async with optio.block_launches({"x": 1}, persist=True, reason=None):
            pass
        async with optio.block_launches({"x": 1}):
            assert len(optio._launch_blocks) == 2

            removed = await optio.unblock_launches({"x": 1})
            # Both removed (persistent + transient).
            assert removed == 2
            assert len(optio._launch_blocks) == 0
        # Transient CM exit tolerates the missing token (pop with default).
        assert len(optio._launch_blocks) == 0
    finally:
        await _stop_optio(optio, run_task)


async def test_unblock_persisted_block_then_launch_succeeds(mongo_db):
    """After unblock, matching launches go through."""
    completed = asyncio.Event()

    async def _execute(ctx):
        completed.set()

    task = TaskInstance(
        execute=_execute,
        process_id="p_y",
        name="y",
        metadata={"tenant": "banned"},
    )

    optio, run_task = await _start_optio(mongo_db, "optio_u4", tasks=(task,))
    try:
        async with optio.block_launches({"tenant": "banned"}, persist=True, reason=None):
            pass
        with pytest.raises(LaunchBlocked):
            await optio.launch("p_y")

        await optio.unblock_launches({"tenant": "banned"})

        await optio.launch_and_wait("p_y")
        assert completed.is_set()
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 5.2: Run the tests — they should fail**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k unblock`

Expected: all FAIL with `AttributeError: 'Optio' object has no attribute 'unblock_launches'`.

- [ ] **Step 5.3: Implement `unblock_launches`**

In `lifecycle.py`, add a new method on `Optio` (place it next to `block_launches`):

```python
    async def unblock_launches(
        self,
        launch_filter: ProcessMetadataFilter,
    ) -> int:
        """Remove every persistent record and every in-memory block entry
        whose filter equals `launch_filter` by exact dict equality. Returns
        the count of in-memory entries removed.

        Spec: docs/2026-04-30-persistent-launch-blocks-design.md.
        """
        from optio_core import _launch_block_store as _lb_store
        coll = _lb_store.collection(
            self._config.mongo_db, self._config.prefix,
        )
        await _lb_store.delete_by_filter(coll, launch_filter)

        tokens = [
            t for t, entry in self._launch_blocks.items()
            if entry.filter == launch_filter
        ]
        for t in tokens:
            self._launch_blocks.pop(t, None)
        return len(tokens)
```

- [ ] **Step 5.4: Run the tests — they should pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k unblock`

Expected: all 4 PASS.

- [ ] **Step 5.5: Export `unblock_launches`**

In `packages/optio-core/src/optio_core/__init__.py`, add the binding and the `__all__` entry:

```python
unblock_launches = _instance.unblock_launches
```

(insert next to `block_launches = _instance.block_launches`), and add `"unblock_launches"` to the `__all__` list (next to `"block_launches"`).

- [ ] **Step 5.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/src/optio_core/__init__.py packages/optio-core/tests/test_persistent_launch_blocks.py
git commit -m "feat(persistent-launch-blocks): add unblock_launches and export it"
```

---

## Task 6: `group_cancel(persist, reason)` constraint + plumbing

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_persistent_launch_blocks.py`

**Goal:** Add `persist=False` and `reason=None` kwargs (keyword-only) to `group_cancel`. Pass through to the internal `block_launches` call. Reject `persist=True` + `block_new_launches=False` with `ValueError`.

- [ ] **Step 6.1: Write failing tests**

Append to `packages/optio-core/tests/test_persistent_launch_blocks.py`:

```python
# ---------- group_cancel(persist=...) ----------

async def test_group_cancel_persist_requires_block_new_launches(mongo_db):
    """persist=True with block_new_launches=False raises ValueError."""
    optio = Optio()
    with pytest.raises(ValueError, match="block_new_launches"):
        await optio.group_cancel(
            {"tenant": "x"}, block_new_launches=False, persist=True, reason=None,
        )


async def test_group_cancel_persist_installs_persistent_block(mongo_db):
    """persist=True + block_new_launches=True writes a record and the block survives."""
    completed = asyncio.Event()

    async def _execute(ctx):
        completed.set()

    task = TaskInstance(
        execute=_execute,
        process_id="p_gc",
        name="gc",
        metadata={"tenant": "acme"},
    )
    optio, run_task = await _start_optio(mongo_db, "optio_gc1", tasks=(task,))
    try:
        await optio.group_cancel(
            {"tenant": "acme"},
            block_new_launches=True,
            persist=True,
            reason="banned",
        )
        # After group_cancel returns, the block must still be in memory.
        assert any(
            e.filter == {"tenant": "acme"} and e.reason == "banned"
            for e in optio._launch_blocks.values()
        )
        # And in the DB.
        rows = await store.load_all(store.collection(mongo_db, "optio_gc1"))
        assert len(rows) == 1
        assert rows[0].reason == "banned"

        # Subsequent launches blocked.
        with pytest.raises(LaunchBlocked):
            await optio.launch("p_gc")
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 6.2: Run the tests — they should fail**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k group_cancel_persist`

Expected: FAIL — `TypeError: group_cancel() got an unexpected keyword argument 'persist'`.

- [ ] **Step 6.3: Update `group_cancel` signature and body**

Replace the `group_cancel` method (lines 336–353) with:

```python
    async def group_cancel(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
        *,
        persist: bool = False,
        reason: str | None = None,
    ) -> None:
        """Cancel every active process matching `metadata_filter`. Does NOT
        wait for terminal state.

        See docs/2026-04-30-group-cancel-design.md and
        docs/2026-04-30-persistent-launch-blocks-design.md (for `persist`).
        """
        if not metadata_filter:
            raise ValueError(
                "group_cancel requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        if persist and not block_new_launches:
            raise ValueError(
                "group_cancel: persist=True requires block_new_launches=True"
            )
        async with AsyncExitStack() as stack:
            if block_new_launches:
                await stack.enter_async_context(
                    self.block_launches(
                        metadata_filter, persist=persist, reason=reason,
                    )
                )
            await self._group_cancel_issue(metadata_filter, block_new_launches)
```

- [ ] **Step 6.4: Run the new tests — they should pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k group_cancel_persist`

Expected: both PASS.

- [ ] **Step 6.5: Run prior `test_group_cancel.py` tests — must still pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`

Expected: identical PASS count as before.

- [ ] **Step 6.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_persistent_launch_blocks.py
git commit -m "feat(persistent-launch-blocks): add persist/reason kwargs to group_cancel"
```

---

## Task 7: `group_cancel_and_wait(persist, reason)` constraint + plumbing

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/tests/test_persistent_launch_blocks.py`

**Goal:** Same shape and constraint as `group_cancel`. Persistent block is installed; running matching processes terminate; the call waits.

- [ ] **Step 7.1: Write failing tests**

Append to `packages/optio-core/tests/test_persistent_launch_blocks.py`:

```python
# ---------- group_cancel_and_wait(persist=...) ----------

async def test_group_cancel_and_wait_persist_requires_block_new_launches(mongo_db):
    """persist=True with block_new_launches=False raises ValueError."""
    optio = Optio()
    with pytest.raises(ValueError, match="block_new_launches"):
        await optio.group_cancel_and_wait(
            {"tenant": "x"}, block_new_launches=False, persist=True, reason=None,
        )


async def test_group_cancel_and_wait_persist_installs_persistent_block(mongo_db):
    """persist=True + block_new_launches=True: blocks survives the wait, record written."""
    started = asyncio.Event()

    async def _execute(ctx):
        started.set()
        try:
            await asyncio.sleep(60)  # will be cancelled
        except asyncio.CancelledError:
            raise

    task = TaskInstance(
        execute=_execute,
        process_id="p_gcw",
        name="gcw",
        metadata={"tenant": "acme"},
    )
    optio, run_task = await _start_optio(mongo_db, "optio_gcw1", tasks=(task,))
    try:
        # Launch one process; wait for it to start.
        await optio.launch("p_gcw")
        await asyncio.wait_for(started.wait(), timeout=5.0)

        await optio.group_cancel_and_wait(
            {"tenant": "acme"},
            block_new_launches=True,
            persist=True,
            reason="bw",
        )
        # Persistent block survives the wait.
        assert any(
            e.filter == {"tenant": "acme"} and e.reason == "bw"
            for e in optio._launch_blocks.values()
        )
        rows = await store.load_all(store.collection(mongo_db, "optio_gcw1"))
        assert len(rows) == 1

        # Subsequent launch blocked.
        with pytest.raises(LaunchBlocked):
            await optio.launch("p_gcw")
    finally:
        await _stop_optio(optio, run_task)
```

- [ ] **Step 7.2: Run the tests — they should fail**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k group_cancel_and_wait_persist`

Expected: FAIL — `TypeError: ... unexpected keyword argument 'persist'`.

- [ ] **Step 7.3: Update `group_cancel_and_wait`**

Replace the `group_cancel_and_wait` method (lines 355–399) with:

```python
    async def group_cancel_and_wait(
        self,
        metadata_filter: ProcessMetadataFilter,
        block_new_launches: bool = False,
        *,
        persist: bool = False,
        reason: str | None = None,
    ) -> None:
        """Cancel every active process matching `metadata_filter` and wait
        for all of them to reach a terminal state. See
        docs/2026-04-30-group-cancel-design.md and
        docs/2026-04-30-persistent-launch-blocks-design.md (for `persist`).

        Do not call from inside a task whose metadata matches the filter —
        use group_cancel for self-cancel.
        """
        if not metadata_filter:
            raise ValueError(
                "group_cancel_and_wait requires a non-empty metadata_filter; "
                "use Optio.shutdown() to drain everything."
            )
        if persist and not block_new_launches:
            raise ValueError(
                "group_cancel_and_wait: persist=True requires block_new_launches=True"
            )
        async with AsyncExitStack() as stack:
            if block_new_launches:
                await stack.enter_async_context(
                    self.block_launches(
                        metadata_filter, persist=persist, reason=reason,
                    )
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

- [ ] **Step 7.4: Run the new tests — they should pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py -v -k group_cancel_and_wait_persist`

Expected: both PASS.

- [ ] **Step 7.5: Run prior `test_group_cancel.py` tests — must still pass**

Run: `pytest packages/optio-core/tests/test_group_cancel.py -v`

Expected: identical PASS count as before.

- [ ] **Step 7.6: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_persistent_launch_blocks.py
git commit -m "feat(persistent-launch-blocks): add persist/reason kwargs to group_cancel_and_wait"
```

---

## Task 8: Restart simulation test + final sweep

**Files:**
- Modify: `packages/optio-core/tests/test_persistent_launch_blocks.py`

**Goal:** Cover the restart-survives scenario (the headline guarantee of the feature) and run the entire optio-core suite.

- [ ] **Step 8.1: Write the restart simulation test**

Append to `packages/optio-core/tests/test_persistent_launch_blocks.py`:

```python
# ---------- Restart survives ----------

async def test_persistent_block_survives_restart(mongo_db):
    """Install a persistent block, dispose Optio, instantiate a new one
    against the same DB — block is reloaded and matching launches stay blocked.
    """
    async def _noop_execute(ctx):  # pragma: no cover
        return

    task = TaskInstance(
        execute=_noop_execute,
        process_id="p_z",
        name="z",
        metadata={"tenant": "banned"},
    )

    # First run: install the block.
    optio, run_task = await _start_optio(mongo_db, "optio_restart1", tasks=(task,))
    try:
        async with optio.block_launches(
            {"tenant": "banned"}, persist=True, reason="bad",
        ):
            pass
    finally:
        await _stop_optio(optio, run_task)

    # Second run: same DB, fresh Optio instance.
    optio2, run_task2 = await _start_optio(mongo_db, "optio_restart1", tasks=(task,))
    try:
        # Block is reloaded.
        assert any(
            e.filter == {"tenant": "banned"} and e.reason == "bad"
            for e in optio2._launch_blocks.values()
        )
        with pytest.raises(LaunchBlocked) as excinfo:
            await optio2.launch("p_z")
        assert "; reason=bad" in str(excinfo.value)
    finally:
        await _stop_optio(optio2, run_task2)
```

- [ ] **Step 8.2: Run the new test — should pass**

Run: `pytest packages/optio-core/tests/test_persistent_launch_blocks.py::test_persistent_block_survives_restart -v`

Expected: PASS.

- [ ] **Step 8.3: Run the entire `optio-core` test suite**

Run: `pytest packages/optio-core/tests/ -v`

Expected: every test PASSES. Note any non-related failures (e.g., infra issues like MongoDB not running) and resolve them before committing.

- [ ] **Step 8.4: Commit**

```bash
git add packages/optio-core/tests/test_persistent_launch_blocks.py
git commit -m "test(persistent-launch-blocks): cover restart-survives scenario"
```

---

## Task 9: Update AGENTS.md

**Files:**
- Modify: `AGENTS.md` (top-level)
- Possibly modify: `packages/optio-core/AGENTS.md` if one exists

**Goal:** Document the new public API additions in the AGENTS.md surface so downstream packages and future agents see them.

- [ ] **Step 9.1: Inspect existing AGENTS.md content for `block_launches` / `group_cancel`**

Run: `grep -n "block_launches\|group_cancel\|launch block" AGENTS.md`

Read the section that mentions them (likely the public-API table around line 70). Note the format used.

- [ ] **Step 9.2: Add `unblock_launches` and `persist` documentation**

In the same area, add an entry for `unblock_launches` matching the surrounding row format. Update the `block_launches`, `group_cancel`, and `group_cancel_and_wait` rows to mention the new `persist=False, reason=None` keyword-only kwargs and the spec reference `docs/2026-04-30-persistent-launch-blocks-design.md`.

(Use `Read` + `Edit` to perform the changes; exact lines depend on what the inspection in 9.1 finds.)

- [ ] **Step 9.3: Check `packages/optio-core/AGENTS.md` if it exists**

Run: `ls packages/optio-core/AGENTS.md 2>/dev/null && cat packages/optio-core/AGENTS.md | head -40 || echo "no per-package AGENTS.md"`

If it exists and documents the same API, mirror the additions there.

- [ ] **Step 9.4: Commit**

```bash
git add AGENTS.md packages/optio-core/AGENTS.md 2>/dev/null
git commit -m "docs(persistent-launch-blocks): document new public API in AGENTS.md"
```

(If `packages/optio-core/AGENTS.md` does not exist, the `git add` of a missing path is silently dropped by the shell because of the `2>/dev/null`; the commit still succeeds with whatever was actually added.)

---

## Self-Review Notes

Spec coverage map (each spec section → task):

- Spec "Public API → Modified `block_launches`" → Task 3
- Spec "Public API → Modified `group_cancel` / `group_cancel_and_wait`" → Tasks 6, 7
- Spec "Public API → New `unblock_launches`" → Task 5
- Spec "Public API → Modified `LaunchBlocked`" message → Task 2 (entry refactor) + Task 3 (reason wiring)
- Spec "Behavior → Load on initialization" → Task 4
- Spec "Behavior → Write-through on persist" + dedupe + reason concat → Task 1 (store) + Task 3 (call site)
- Spec "Behavior → Unblock" → Task 5
- Spec "Behavior → persist+block_new_launches constraint" → Tasks 6, 7
- Spec "Behavior → Concurrency / restart" → Task 8 (restart) + Task 1 (concurrency is best-effort, no special code)
- Spec "Persistence Contract" (collection name, schema, no index, lazy create, no migration) → Task 1
- Spec "Test Strategy" (every bullet) → Tasks 1, 3, 4, 5, 6, 7, 8

No placeholders. Every code step shows the actual code. Method names (`load_all`, `upsert_block`, `delete_by_filter`, `_BlockEntry`, `_load_persisted_blocks`, `unblock_launches`) are consistent across tasks.
