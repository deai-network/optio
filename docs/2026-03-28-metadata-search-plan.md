# Generic Metadata Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace application-specific `type` and `targetId` query parameters with generic metadata filtering across all Optio layers.

**Architecture:** Python `list_processes()` accepts a `metadata` dict for exact-match filtering on any metadata key. The REST API accepts `metadata.*` prefixed query params. The UI removes the app-specific `useSourceProcesses` hook.

**Tech Stack:** Python (pytest), TypeScript (Zod, ts-rest, React)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/optio-core/src/optio_core/store.py` | Modify | Replace `type`/`target_id` with `metadata` dict filter |
| `packages/optio-core/src/optio_core/lifecycle.py` | Modify | Update `list_processes` signature |
| `packages/optio-core/tests/test_store.py` | Modify | Add metadata filter test |
| `packages/optio-core/tests/test_no_redis.py` | Modify | Add metadata filter integration test |
| `packages/optio-contracts/src/contract.ts` | Modify | Remove `type`/`targetId` from list query schema |
| `packages/optio-api/src/handlers.ts` | Modify | Replace `type`/`targetId` with `metadata.*` extraction |
| `packages/optio-ui/src/hooks/useProcessQueries.ts` | Modify | Remove `useSourceProcesses` |
| `packages/optio-ui/src/index.ts` | Modify | Remove `useSourceProcesses` export |
| `packages/optio-core/README.md` | Modify | Update `list_processes` docs |
| `packages/optio-ui/README.md` | Modify | Remove `useSourceProcesses` from hooks table |
| `AGENTS.md` | Modify | Update Python, contracts, API, UI sections |

---

### Task 1: Update optio-core — Python layer

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py:249-271`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py:192-209`
- Modify: `packages/optio-core/tests/test_no_redis.py`
- Modify: `packages/optio-core/tests/test_store.py`

- [ ] **Step 1: Write a failing test for metadata filtering**

Add to `packages/optio-core/tests/test_no_redis.py`:

```python
@pytest.mark.asyncio
async def test_list_processes_filter_metadata(mongo_db):
    """list_processes(metadata=...) filters by metadata fields."""
    async def _tasks(services):
        return [
            TaskInstance(execute=_noop, process_id="task_a", name="Task A",
                         metadata={"region": "eu", "priority": "high"}),
            TaskInstance(execute=_noop, process_id="task_b", name="Task B",
                         metadata={"region": "us", "priority": "low"}),
            TaskInstance(execute=_noop, process_id="task_c", name="Task C",
                         metadata={"region": "eu", "priority": "low"}),
        ]

    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="test_meta", get_task_definitions=_tasks)

    # Filter by single metadata key
    procs = await fw.list_processes(metadata={"region": "eu"})
    assert len(procs) == 2
    ids = {p["processId"] for p in procs}
    assert ids == {"task_a", "task_c"}

    # Filter by multiple metadata keys
    procs = await fw.list_processes(metadata={"region": "eu", "priority": "high"})
    assert len(procs) == 1
    assert procs[0]["processId"] == "task_a"

    # No matches
    procs = await fw.list_processes(metadata={"region": "jp"})
    assert len(procs) == 0
```

Note: `_noop` should be `async def _noop(ctx): pass`. Check if it already exists in the test file; if not, add it near the top.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd packages/optio-core && python -m pytest tests/test_no_redis.py::test_list_processes_filter_metadata -v`
Expected: FAIL — `list_processes()` does not accept `metadata` parameter.

- [ ] **Step 3: Update `store.py` — replace `type`/`target_id` with `metadata`**

In `packages/optio-core/src/optio_core/store.py`, replace the `list_processes` function (lines 249-271):

```python
async def list_processes(
    db: AsyncIOMotorDatabase,
    prefix: str,
    state: str | None = None,
    root_id: ObjectId | None = None,
    metadata: dict[str, str] | None = None,
) -> list[dict]:
    """List processes with optional filters."""
    coll = _collection(db, prefix)
    filter: dict = {}
    if state is not None:
        filter["status.state"] = state
    if root_id is not None:
        filter["rootId"] = root_id
    if metadata is not None:
        for key, value in metadata.items():
            filter[f"metadata.{key}"] = value

    return await coll.find(filter).sort([
        ("depth", 1), ("order", 1), ("_id", 1),
    ]).to_list(None)
```

- [ ] **Step 4: Update `lifecycle.py` — match the new signature**

In `packages/optio-core/src/optio_core/lifecycle.py`, replace the `list_processes` method (lines 192-209):

```python
    async def list_processes(
        self,
        state: str | None = None,
        root_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> list[dict]:
        """List processes with optional filters."""
        from bson import ObjectId as OID
        from optio_core.store import list_processes as _list_processes
        return await _list_processes(
            self._config.mongo_db,
            self._config.prefix,
            state=state,
            root_id=OID(root_id) if root_id else None,
            metadata=metadata,
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd packages/optio-core && python -m pytest tests/test_no_redis.py::test_list_processes_filter_metadata -v`
Expected: PASS

- [ ] **Step 6: Run all existing tests to check for regressions**

Run: `cd packages/optio-core && python -m pytest tests/ -v`
Expected: All pass. The existing `test_list_processes_no_filter` and `test_list_processes_filter_state` tests don't use `type` or `target_id`, so they should be unaffected.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-core/src/optio_core/store.py packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/tests/test_no_redis.py
git commit -m "feat: replace type/target_id with generic metadata filter in list_processes"
```

---

### Task 2: Update optio-contracts — remove type/targetId from query schema

**Files:**
- Modify: `packages/optio-contracts/src/contract.ts:17-22`

- [ ] **Step 1: Remove `type` and `targetId` from the list query schema**

In `packages/optio-contracts/src/contract.ts`, replace lines 17-22:

```typescript
    query: PaginationQuerySchema.extend({
      rootId: ObjectIdSchema.optional(),
      type: z.string().optional(),
      state: ProcessStateSchema.optional(),
      targetId: z.string().optional(),
    }),
```

With:

```typescript
    query: PaginationQuerySchema.extend({
      rootId: ObjectIdSchema.optional(),
      state: ProcessStateSchema.optional(),
    }).passthrough(),
```

The `.passthrough()` tells Zod to allow additional properties (the `metadata.*` params) through without stripping them.

- [ ] **Step 2: Build to verify**

Run: `cd packages/optio-contracts && npm run build`
Expected: Success

- [ ] **Step 3: Commit**

```bash
git add packages/optio-contracts/src/contract.ts
git commit -m "feat: remove type/targetId from list query schema, allow passthrough for metadata"
```

---

### Task 3: Update optio-api — extract metadata.* query params

**Files:**
- Modify: `packages/optio-api/src/handlers.ts:20-36`

- [ ] **Step 1: Update `ListQuery` interface and `listProcesses` handler**

In `packages/optio-api/src/handlers.ts`, replace the `ListQuery` interface and the filter-building section of `listProcesses`:

Replace:

```typescript
export interface ListQuery {
  cursor?: string;
  limit: number;
  rootId?: string;
  type?: string;
  state?: string;
  targetId?: string;
}

export async function listProcesses(db: Db, prefix: string, query: ListQuery) {
  const { cursor, limit, rootId, type, state, targetId } = query;
  const filter: Record<string, unknown> = {};

  if (rootId) filter.rootId = new ObjectId(rootId);
  if (type) filter.type = type;
  if (state) filter['status.state'] = state;
  if (targetId) filter['metadata.targetId'] = targetId;
  if (cursor) filter._id = { $gt: new ObjectId(cursor) };
```

With:

```typescript
export interface ListQuery {
  cursor?: string;
  limit: number;
  rootId?: string;
  state?: string;
  [key: string]: unknown;
}

export async function listProcesses(db: Db, prefix: string, query: ListQuery) {
  const { cursor, limit, rootId, state, ...rest } = query;
  const filter: Record<string, unknown> = {};

  if (rootId) filter.rootId = new ObjectId(rootId);
  if (state) filter['status.state'] = state;
  if (cursor) filter._id = { $gt: new ObjectId(cursor) };

  // Extract metadata.* query params
  for (const [key, value] of Object.entries(rest)) {
    if (key.startsWith('metadata.') && typeof value === 'string') {
      filter[key] = value;
    }
  }
```

- [ ] **Step 2: Build to verify**

Run: `cd packages/optio-api && npm run build`
Expected: Success

- [ ] **Step 3: Commit**

```bash
git add packages/optio-api/src/handlers.ts
git commit -m "feat: replace type/targetId with generic metadata.* query param extraction"
```

---

### Task 4: Update optio-ui — remove useSourceProcesses

**Files:**
- Modify: `packages/optio-ui/src/hooks/useProcessQueries.ts:54-66`
- Modify: `packages/optio-ui/src/index.ts:13-14`

- [ ] **Step 1: Remove `useSourceProcesses` from `useProcessQueries.ts`**

Delete lines 54-66 from `packages/optio-ui/src/hooks/useProcessQueries.ts` (the entire `useSourceProcesses` function).

- [ ] **Step 2: Remove `useSourceProcesses` from `index.ts`**

In `packages/optio-ui/src/index.ts`, replace line 13-14:

```typescript
export { useProcessList, useProcess, useProcessTree, useProcessTreeLog,
         useSourceProcesses } from './hooks/useProcessQueries.js';
```

With:

```typescript
export { useProcessList, useProcess, useProcessTree, useProcessTreeLog } from './hooks/useProcessQueries.js';
```

- [ ] **Step 3: Build to verify**

Run: `cd packages/optio-ui && npm run build`
Expected: Success

- [ ] **Step 4: Commit**

```bash
git add packages/optio-ui/src/hooks/useProcessQueries.ts packages/optio-ui/src/index.ts
git commit -m "feat: remove app-specific useSourceProcesses hook"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `packages/optio-core/README.md`
- Modify: `packages/optio-ui/README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update optio-core README — `list_processes` signature and parameter table**

In `packages/optio-core/README.md`, find the `list_processes()` section and replace the signature and parameter table. The new signature:

```python
await optio_core.list_processes(
    state: str | None = None,
    root_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> list[dict]
```

New parameter table:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `state` | `str \| None` | `None` | Filter by `status.state` (e.g., `"running"`, `"done"`) |
| `root_id` | `str \| None` | `None` | Filter by `rootId` (string; converted to ObjectId internally) |
| `metadata` | `dict[str, str] \| None` | `None` | Filter by metadata fields. Each key-value pair matches against `metadata.{key}` in the process document. Multiple entries are combined with AND. |

Also remove `target_id` from the MongoDB Document Schema description (the line that says `metadata.targetId` is used by `list_processes`). Replace with a generic note that metadata fields can be filtered via `list_processes(metadata=...)`.

- [ ] **Step 2: Update optio-ui README — remove `useSourceProcesses` from hooks table**

In `packages/optio-ui/README.md`, find the hooks table and remove the row for `useSourceProcesses`.

- [ ] **Step 3: Update AGENTS.md**

In `AGENTS.md`, make these changes:

1. **Python section** — update `list_processes` signature: remove `type=None, target_id=None`, add `metadata: dict[str, str] | None = None`. Update the docstring.
2. **Contracts section** — remove `type` and `targetId` from the `list` endpoint query column. Add note about `metadata.*` passthrough.
3. **API section** — update `ListQuery` interface: remove `type` and `targetId`. Add note about `metadata.*` extraction.
4. **UI section** — remove `useSourceProcesses` hook documentation.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/README.md packages/optio-ui/README.md AGENTS.md
git commit -m "docs: update all docs for generic metadata search"
```
