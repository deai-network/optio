# Process collection indexes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** optio-core ensures six indexes on `{prefix}_processes` at every `init()`, a relaunch clears `expireAt`, and children inherit their parent's `ttlSeconds`.

**Architecture:** A new `store.ensure_process_indexes(db, prefix)` runs idempotent `create_index` calls; `Optio.init` calls it after the migrations. `store.relaunch_reset` gains an `$unset` of `expireAt` in its one update. `store.create_child_process` gains a `ttl_seconds` argument that the executor (from the parent's record, via its `ProcessContext`) and `adhoc_define` (from the parent document) fill in.

**Tech Stack:** Python 3, motor/pymongo, pytest-asyncio against the real test Mongo.

**Spec:** `docs/2026-10-09-process-indexes-design.md`

## Global Constraints

- Index names, keys and options exactly as the spec's table: `processId_1__id_-1` `{processId: 1, _id: -1}`; `parentId_1_order_1` `{parentId: 1, order: 1}`; `rootId_1_depth_1_order_1` `{rootId: 1, depth: 1, order: 1}`; `status.state_1` `{"status.state": 1}`; `originatingSessionId_1` `{originatingSessionId: 1}`; `expireAt_ttl` `{expireAt: 1}` with `expireAfterSeconds=0`.
- Conflict codes 85 (IndexOptionsConflict) and 86 (IndexKeySpecsConflict) are a logged warning naming the index; any other error propagates.
- optio-api is not touched (read-only rule). m004 is not touched.
- Tests must not depend on wall-clock time (AGENTS.md); hang ceilings 60 s; no test of MongoDB's TTL monitor.
- Tests run on the excavator host from a worktree of optio, never in `~/deai/optio` (the dev engine restarts on every Python change there). Run: `cd <worktree>/packages/optio-core && PYTHONPATH=$PWD/src ~/deai/optio/.venv/bin/pytest <args>`.
- `main` in `~/deai/optio` receives the commits once, at the end (Task 4), announced in topics.log first. No Co-Authored-By lines.

## Review Focus

- **An operator's own index on the same keys** (e.g. `{processId: 1}` named differently): startup must go on, with one warning, and the other indexes must still be created. Pinned in Task 1.
- **A deployment whose process collection does not exist yet** (first start of a new prefix): `ensure_process_indexes` creates it with its indexes, and `init` works. Pinned in Task 1 (new prefix) and Task 2.
- **Every terminal path sets `expireAt` again after a relaunch cleared it** (done, failed, cancelled, cancelled before running, the startup reconcile, force-cancel): the existing `test_task_ttl.py` tests cover done / failed / cancelled / early cancel; the reconcile and force-cancel writers read `ttlSeconds` the same way and are not changed.
- **A grandchild** must carry the root's TTL as well as a child does. Pinned in Task 3.
- **An ad-hoc child** created under a parent with a TTL must carry it. Pinned in Task 3.

---

### Task 1: store -- `ensure_process_indexes`

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py` (new function after `compute_expire_at`; add `import logging` and `logger = logging.getLogger(__name__)`)
- Create: `packages/optio-core/tests/test_process_indexes.py`

**Interfaces:**
- Produces: `async def ensure_process_indexes(db: AsyncIOMotorDatabase, prefix: str) -> None` and the module constant `PROCESS_INDEXES: list[tuple[str, list[tuple[str, int]], dict]]` (name, keys, options) that it walks.

- [ ] **Step 1: Write the failing tests in `tests/test_process_indexes.py`**

```python
EXPECTED = {
    "_id_": ([("_id", 1)], None),
    "processId_1__id_-1": ([("processId", 1), ("_id", -1)], None),
    "parentId_1_order_1": ([("parentId", 1), ("order", 1)], None),
    "rootId_1_depth_1_order_1": ([("rootId", 1), ("depth", 1), ("order", 1)], None),
    "status.state_1": ([("status.state", 1)], None),
    "originatingSessionId_1": ([("originatingSessionId", 1)], None),
    "expireAt_ttl": ([("expireAt", 1)], 0),
}

async def _indexes(coll):
    # name -> (list of (key, direction), expireAfterSeconds or None)

async def test_ensure_creates_the_six_indexes_on_a_new_prefix(mongo_db):
    await ensure_process_indexes(mongo_db, "idx")
    assert await _indexes(mongo_db["idx_processes"]) == EXPECTED

async def test_ensure_twice_changes_nothing(mongo_db):
    # call twice; _indexes after the second call == EXPECTED

async def test_an_index_with_the_same_keys_under_another_name_is_a_warning(mongo_db, caplog):
    # create_index([("processId", 1), ("_id", -1)], name="my_pid") first
    # ensure_process_indexes does not raise
    # exactly one WARNING record from "optio_core.store" whose message contains "processId_1__id_-1"
    # indexes == EXPECTED minus "processId_1__id_-1", plus "my_pid" with the same keys

async def test_lookups_use_the_indexes(mongo_db):
    # ensure, insert 3 docs with distinct processId and a shared parentId
    # explain of find({"processId": "p1"}).sort("_id", -1).limit(1): an IXSCAN stage on "processId_1__id_-1"
    # explain of find({"parentId": oid}).sort("order", 1): an IXSCAN stage on "parentId_1_order_1"
    # (walk queryPlanner.winningPlan recursively for {"stage": "IXSCAN", "indexName": ...})
```

- [ ] **Step 2: Run them, see them fail** -- `pytest tests/test_process_indexes.py -v`; expected: ImportError on `ensure_process_indexes`.

- [ ] **Step 3: Implement `ensure_process_indexes`.** One `create_index(keys, name=..., **options)` per entry of `PROCESS_INDEXES`; catch `pymongo.errors.OperationFailure` with `e.code in (85, 86)` and `logger.warning(...)` naming the index and the collection; re-raise others.

- [ ] **Step 4: Run `tests/test_process_indexes.py tests/test_store.py`** -- expected PASS.

- [ ] **Step 5: Commit** -- `feat(optio-core): ensure_process_indexes creates the process collection's indexes`.

### Task 2: `Optio.init` ensures the indexes

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:240-242` (after `fw_migrations.run(...)`, before `_load_persisted_blocks`)
- Test: `packages/optio-core/tests/test_process_indexes.py`

**Interfaces:**
- Consumes: `ensure_process_indexes(db, prefix)` (Task 1).

- [ ] **Step 1: Write the failing test**

```python
async def test_init_ensures_the_indexes(mongo_db):
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="idxinit")
    try:
        assert await _indexes(mongo_db["idxinit_processes"]) == EXPECTED
    finally:
        await optio.shutdown()
```

- [ ] **Step 2: Run it, see it fail** (only `_id_`, or the collection missing).
- [ ] **Step 3: Implement:** `await ensure_process_indexes(mongo_db, prefix)` in `init` at that point, with a one-line comment citing the spec.
- [ ] **Step 4: Run `tests/test_process_indexes.py tests/test_lifecycle_reconciliation.py tests/test_outcomes.py`** -- expected PASS.
- [ ] **Step 5: Commit** -- `feat(optio-core): init ensures the process collection's indexes`.

### Task 3: TTL fixes -- relaunch clears `expireAt`, children inherit `ttlSeconds`

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py` (`relaunch_reset`; `create_child_process` signature + doc field)
- Modify: `packages/optio-core/src/optio_core/context.py` (`ProcessContext.__init__`: `self._ttl_seconds: int | None = None`)
- Modify: `packages/optio-core/src/optio_core/executor.py` (`_execute_process`: `ctx._ttl_seconds = ttl_seconds` next to `ctx._executor = self`; `execute_child`: pass `ttl_seconds=parent_ctx._ttl_seconds`)
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:409-420` (`adhoc_define` child branch: pass `ttl_seconds=parent.get("ttlSeconds")`)
- Test: `packages/optio-core/tests/test_task_ttl.py`

**Interfaces:**
- Produces: `create_child_process(..., ttl_seconds: int | None = None)` storing `"ttlSeconds": ttl_seconds` on the child document; `relaunch_reset` update = `{"$set": ..., "$unset": {"expireAt": ""}}` (still one `update_one`).

- [ ] **Step 1: Write the failing tests in `tests/test_task_ttl.py`**

```python
async def test_a_relaunch_clears_expire_at_until_the_run_ends(mongo_db):
    # Executor(mongo_db, "test", {}); task ttl_seconds=3600 whose body, on its
    # second run, sets `running` and awaits `release` (asyncio.Events)
    # run 1: doc["expireAt"] is not None
    # run 2 started as a task; after running.wait(): "expireAt" not in doc
    # release.set(); after the run: doc["expireAt"] is not None

async def test_dismiss_clears_expire_at(mongo_db):
    # Optio.init(prefix="ttldis"); insert a done row with expireAt set
    # optio.dismiss(...) -> ok; "expireAt" not in doc

async def test_children_and_grandchildren_take_the_parents_ttl(mongo_db):
    # parent ttl_seconds=3600 runs a child that runs a grandchild (ctx.run_child), all return
    # child and grandchild docs: ttlSeconds == 3600 and expireAt is not None

async def test_children_of_a_parent_without_ttl_get_none(mongo_db):
    # same shape, no ttl_seconds: child doc.get("ttlSeconds") is None and doc.get("expireAt") is None

async def test_an_adhoc_child_takes_its_parents_ttl(mongo_db):
    # Optio.init(prefix="ttladhoc"); parent = adhoc_define(TaskInstance(..., ttl_seconds=3600))
    # child = adhoc_define(TaskInstance(...), parent_id=parent["_id"])
    # child doc ttlSeconds == 3600
```

- [ ] **Step 2: Run them, see them fail** -- expireAt still present while running and after dismiss; children's `ttlSeconds` missing.
- [ ] **Step 3: Implement** the changes listed under Files.
- [ ] **Step 4: Run `tests/test_task_ttl.py tests/test_process_writes.py tests/test_store.py tests/test_executor.py tests/test_parallel.py`** -- expected PASS (`test_process_writes.py` still counts one update per relaunch and dismiss).
- [ ] **Step 5: Run the whole optio-core suite** -- `pytest -n 4 --dist loadscope -q`; expected all pass (491 + the new tests).
- [ ] **Step 6: Commit** -- `fix(optio-core): a relaunch clears expireAt; children inherit the parent's TTL`.

### Task 4: land, measure, then release for other deployments

Measured on the excavator host's dev stack (tmux `excavator-stack`, main MongoDB
`excavator-mongodb-1`). Its engine runs optio from the `~/deai/optio` checkout,
so landing there is what the measurement needs; no release is involved.

- [ ] **Step 1:** Baseline: a 30 s profiler sample of `gm_processes` (documents examined per `processId` / `parentId` query) and two minutes of `/tmp/fwmeasure/sample2.py`.
- [ ] **Step 2:** Announce in topics.log, then fast-forward `main` in `~/deai/optio` to the branch and push to GitHub. The dev engine restarts once and creates the indexes on `gm_processes`; check them with pymongo.
- [ ] **Step 3:** Re-measure as in Step 1; record both in `~/chat/idlecost/fw/indexes-{before,after}.log`.
- [ ] **Step 4:** Add a line to the AGENTS.md feature list ("Process indexes: ...", with the spec path) and update the "Child processes" architecture note if it still names `clear_result_fields`; commit and push.
- [ ] **Step 5 (other deployments, whose images install optio-core from PyPI):** wire patch release per `docs/release-cookbook.md` (0.5.3 → 0.5.4), then excavator's `optio-core>=0.5.4,<0.6` in `packages/engine/pyproject.toml` with a comment line, engine suite, commit, push.
