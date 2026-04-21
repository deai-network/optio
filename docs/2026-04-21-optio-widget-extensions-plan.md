# Optio Widget Extensions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the four widget primitives defined in `docs/2026-04-21-optio-widget-extensions-design.md` (registry, upstream proxy, widget data, generic iframe widget) plus a marimo reference task that exercises the whole stack end-to-end.

**Architecture:** Follows the spec. Python core grows new `TaskInstance` / `ProcessContext` surface and store-layer writes; optio-contracts gains two new optional schema fields (`uiWidget`, `widgetData`); optio-api gains a widget-proxy module and extends the tree stream-poller to carry `widgetData`; optio-ui gains a widget registry, a `ProcessDetailView` dispatcher, and a generic iframe widget; optio-dashboard swaps its inline detail pane for `ProcessDetailView`; optio-demo gets a marimo reference task.

**Tech Stack:** Python 3.11+ (Motor + pytest + pytest-asyncio), TypeScript (Zod + ts-rest + Fastify 5 + `@fastify/http-proxy` + vitest + @testing-library/react), React 19.

**Commit policy:** Per project convention, the entire plan results in **one commit at the end**, not one per task. During work you may create intermediate work-in-progress commits as checkpoints; squash to a single commit as the final step.

**Base revision:** `5b0d3b1` on branch `main` (the spec-update commit).

---

## Scope Check (from spec)

All four primitives flow into one user-facing capability (widget-backed process detail view). No sub-decomposition — this is one plan.

The spec's "Resolved Decisions" section pre-locks: proxy library = `@fastify/http-proxy`; iframe URL = `${apiBaseUrl}/api/widget/${processId}/`; no router; widgetData rides tree-stream `update`; `widgetUpstream` never leaves the server.

Two items are explicitly deferred to implementation time (not the plan):
- The exact `@fastify/http-proxy` WS hook shape for auth (plan Task 15 investigates and decides).
- `ProcessDetailView` self-fetching vs. data-in-props (plan Task 20 picks; default: self-fetching).

One **plan deviation** from the spec worth user acknowledgement: the spec describes "invalidation via the tree poller" for the in-memory `widgetUpstream` cache. This cannot work reliably (tree pollers are per-connection; no active connection means no invalidation). The plan uses a **5-second TTL cache** instead — same intent (eventually consistent on worker-side writes), simpler implementation. Flagged in Task 11.

---

## File Structure

Files are grouped by package. Paths are relative to repo root `/home/csillag/deai/optio/`.

### optio-core (Python)

- **Modify** `packages/optio-core/src/optio_core/models.py` — add `ui_widget` field to `TaskInstance`; add `BasicAuth`, `QueryAuth`, `HeaderAuth`, `InnerAuth` types; add `WidgetUpstream` helper.
- **Modify** `packages/optio-core/src/optio_core/store.py` — add `uiWidget` to `upsert_process` $set; new functions `update_widget_upstream`, `clear_widget_upstream`, `update_widget_data`, `clear_widget_data`; extend `clear_result_fields` to also reset `widgetData` and `widgetUpstream`.
- **Modify** `packages/optio-core/src/optio_core/context.py` — add `set_widget_upstream`, `clear_widget_upstream`, `set_widget_data`, `clear_widget_data` methods to `ProcessContext`.
- **Modify** `packages/optio-core/src/optio_core/executor.py` — after terminal-state transitions (done/failed/cancelled) in `_execute_process`, clear `widgetUpstream` via store. Also ensure the heartbeat-based dead-process handler (if separate) clears it; if terminal-state writes go through one code path, one change suffices.
- **Create** `packages/optio-core/tests/test_widget_primitives.py` — round-trip tests for all of the above.

No schema migrations required: the new fields are optional on the MongoDB doc and `None` on Python side by default.

### optio-contracts (TypeScript)

- **Modify** `packages/optio-contracts/src/schemas/process.ts` — add `uiWidget: z.string().optional()` and `widgetData: z.unknown().optional()` to `ProcessSchema`. Deliberately **do not** add `widgetUpstream`.
- **Modify** `packages/optio-contracts/src/index.ts` — re-export anything new if it was externalized (nothing new here — just fields on an existing schema).
- **Create** `packages/optio-contracts/src/__tests__/process-schema.test.ts` (if no such file exists) — or extend the existing test — to assert parse-success/with-absent and parse-success/with-present.

### optio-api (TypeScript)

- **Create** `packages/optio-api/src/widget-upstream-registry.ts` — in-memory TTL cache keyed by `processId`. Internal to this package.
- **Create** `packages/optio-api/src/widget-proxy-core.ts` — framework-agnostic logic: `resolveWidgetUpstream(db, prefix, processId)`, `applyInnerAuth(innerAuth, headers, url)`, and helpers. Pure functions + the cache.
- **Modify** `packages/optio-api/src/adapters/fastify.ts` — add `registerWidgetProxy(app, opts)` export. Registers `@fastify/http-proxy` plugin under `/api/widget`, with preHandler for auth + upstream lookup, `replyOptions.getUpstream`, `replyOptions.rewriteRequestHeaders`, `websocket: true`, and a WS auth hook.
- **Modify** `packages/optio-api/src/index.ts` — re-export `resolveWidgetUpstream` if needed by tests; do **not** export the registry (internal).
- **Modify** `packages/optio-api/src/stream-poller.ts` — extend `createTreePoller` snapshot fingerprint and `update`-event per-process payload to include `widgetData`. Do not modify `createListPoller`.
- **Modify** `packages/optio-api/package.json` — add `@fastify/http-proxy` to dependencies.
- **Create** `packages/optio-api/src/__tests__/widget-upstream-registry.test.ts` — cache unit tests.
- **Create** `packages/optio-api/src/__tests__/widget-proxy-core.test.ts` — resolver + inner-auth unit tests.
- **Create** `packages/optio-api/src/__tests__/stream-poller.test.ts` — fingerprint + payload + widgetUpstream-never-leaks tests.
- **Create** `packages/optio-api/src/adapters/__tests__/fastify-widget-proxy.test.ts` — integration tests for the Fastify adapter against a small in-process upstream server.

### optio-ui (TypeScript / React)

- **Create** `packages/optio-ui/src/widgets/registry.ts` — module-level registry with `registerWidget(name, component)`, internal `getWidget(name)`, and `WidgetProps` type.
- **Create** `packages/optio-ui/src/widgets/IframeWidget.tsx` — generic iframe widget. Reads `widgetData`, writes `localStorageOverrides` pre-mount, mounts iframe, cleans up on unmount. Registers itself as `'iframe'` on module import (side effect).
- **Create** `packages/optio-ui/src/components/ProcessDetailView.tsx` — dispatcher component. Takes `processId`; self-fetches via `useProcessStream` / `useProcess`; branches on `process.uiWidget` to render a registered widget or the default tree+log.
- **Modify** `packages/optio-ui/src/index.ts` — export `registerWidget`, type `WidgetProps`, `ProcessDetailView`, and the iframe widget's module (so importing optio-ui registers the built-in iframe widget).
- **Create** `packages/optio-ui/src/__tests__/widget-registry.test.tsx`.
- **Create** `packages/optio-ui/src/__tests__/IframeWidget.test.tsx`.
- **Create** `packages/optio-ui/src/__tests__/ProcessDetailView.test.tsx`.

### optio-dashboard

- **Modify** `packages/optio-dashboard/src/app/App.tsx` — replace the inline `<ProcessTreeView /> + <ProcessLogPanel />` render in the `<Content>` with `<ProcessDetailView processId={selectedProcessId} />`.

### optio-demo

- **Create** `packages/optio-demo/src/marimo_task.py` — defines a marimo reference task using the new primitives.
- **Create** `packages/optio-demo/src/notebooks/sample.py` — a trivial marimo notebook to run.
- **Modify** `packages/optio-demo/src/main.py` (or wherever tasks are registered) to include the marimo task in `get_task_definitions`.
- **Modify** `packages/optio-demo/README.md` — add the user-verifiable smoke-test steps.

### Playwright smoke test (demo package)

Deferred to Task 24 (see below) — spec calls for this but it's a significant infra lift. Marked "optional if time permits" within that task; if the engineer can't deliver it, the manual smoke test in the README is the acceptance gate.

---

## Tasks

### Task 1: Add `ui_widget` field to TaskInstance and persist it

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py:8-20`
- Modify: `packages/optio-core/src/optio_core/store.py:15-62`
- Test: `packages/optio-core/tests/test_widget_primitives.py` (create)

- [ ] **Step 1: Write the failing test**

Create `packages/optio-core/tests/test_widget_primitives.py` with:

```python
import pytest
from optio_core.models import TaskInstance
from optio_core.store import upsert_process


@pytest.mark.asyncio
async def test_upsert_persists_ui_widget(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(
        execute=noop,
        process_id="widget-task",
        name="Widget Task",
        ui_widget="iframe",
    )
    result = await upsert_process(mongo_db, "test", task)
    assert result["uiWidget"] == "iframe"


@pytest.mark.asyncio
async def test_upsert_ui_widget_absent_when_unset(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="plain-task", name="Plain Task")
    result = await upsert_process(mongo_db, "test", task)
    assert result.get("uiWidget") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v`
Expected: FAIL — `TaskInstance` has no `ui_widget` field.

- [ ] **Step 3: Add the field to TaskInstance**

In `packages/optio-core/src/optio_core/models.py`, locate the `TaskInstance` dataclass (lines 8–20). Add `ui_widget: str | None = None` after the `cancellable` field. Final class:

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
```

- [ ] **Step 4: Persist `uiWidget` in `upsert_process`**

In `packages/optio-core/src/optio_core/store.py`, locate `upsert_process` (lines 15–62). Add `"uiWidget": task.ui_widget,` to the `$set` dict (after `"warning": task.warning`). Final `$set`:

```python
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
},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v`
Expected: both tests PASS.

---

### Task 2: Define InnerAuth variants and widgetUpstream store helpers

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py` (add types)
- Modify: `packages/optio-core/src/optio_core/store.py` (add helpers)
- Test: `packages/optio-core/tests/test_widget_primitives.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_widget_primitives.py`:

```python
from bson import ObjectId
from optio_core.models import BasicAuth, QueryAuth, HeaderAuth
from optio_core.store import update_widget_upstream, clear_widget_upstream


@pytest.mark.asyncio
async def test_widget_upstream_round_trip_basic_auth(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u1", name="U1")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(
        mongo_db, "test", oid,
        url="http://127.0.0.1:9000",
        inner_auth=BasicAuth(username="u", password="p"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"]["url"] == "http://127.0.0.1:9000"
    assert doc["widgetUpstream"]["innerAuth"] == {
        "kind": "basic", "username": "u", "password": "p",
    }


@pytest.mark.asyncio
async def test_widget_upstream_round_trip_query_auth(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u2", name="U2")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(
        mongo_db, "test", oid,
        url="http://127.0.0.1:9000",
        inner_auth=QueryAuth(name="tok", value="secret"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"]["innerAuth"] == {
        "kind": "query", "name": "tok", "value": "secret",
    }


@pytest.mark.asyncio
async def test_widget_upstream_round_trip_header_auth(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u3", name="U3")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(
        mongo_db, "test", oid,
        url="http://127.0.0.1:9000",
        inner_auth=HeaderAuth(name="X-Tok", value="s"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"]["innerAuth"] == {
        "kind": "header", "name": "X-Tok", "value": "s",
    }


@pytest.mark.asyncio
async def test_widget_upstream_clear_sets_null(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="u4", name="U4")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_upstream(mongo_db, "test", oid, url="http://x")
    await clear_widget_upstream(mongo_db, "test", oid)

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k widget_upstream`
Expected: ImportError / AttributeError on `BasicAuth`, etc.

- [ ] **Step 3: Add InnerAuth types to models.py**

Append to `packages/optio-core/src/optio_core/models.py`:

```python
from typing import Union


@dataclass
class BasicAuth:
    username: str
    password: str

    def to_dict(self) -> dict:
        return {"kind": "basic", "username": self.username, "password": self.password}


@dataclass
class QueryAuth:
    name: str
    value: str

    def to_dict(self) -> dict:
        return {"kind": "query", "name": self.name, "value": self.value}


@dataclass
class HeaderAuth:
    name: str
    value: str

    def to_dict(self) -> dict:
        return {"kind": "header", "name": self.name, "value": self.value}


InnerAuth = Union[BasicAuth, QueryAuth, HeaderAuth]
```

- [ ] **Step 4: Add store helpers**

Append to `packages/optio-core/src/optio_core/store.py`:

```python
from optio_core.models import InnerAuth  # add at top if imports are grouped


async def update_widget_upstream(
    db: AsyncIOMotorDatabase,
    prefix: str,
    process_oid: ObjectId,
    url: str,
    inner_auth: InnerAuth | None = None,
) -> None:
    """Set widgetUpstream on a process (used by the proxy for forwarding)."""
    entry: dict = {"url": url}
    if inner_auth is not None:
        entry["innerAuth"] = inner_auth.to_dict()
    else:
        entry["innerAuth"] = None
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetUpstream": entry}},
    )


async def clear_widget_upstream(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear widgetUpstream on a process."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetUpstream": None}},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k widget_upstream`
Expected: all four tests PASS.

---

### Task 3: Add `set_widget_upstream` / `clear_widget_upstream` methods to ProcessContext

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py` (add methods)
- Test: `packages/optio-core/tests/test_widget_primitives.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_widget_primitives.py`:

```python
from optio_core.context import ProcessContext


@pytest.mark.asyncio
async def test_process_context_set_widget_upstream(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="c1", name="C1")
    proc = await upsert_process(mongo_db, "test", task)

    ctx = ProcessContext(
        db=mongo_db, prefix="test",
        process_oid=proc["_id"], root_oid=proc["_id"],
        process_id="c1", params={}, metadata={},
        services={}, depth=0,
    )
    await ctx.set_widget_upstream(
        "http://127.0.0.1:9000",
        inner_auth=HeaderAuth(name="X-Tok", value="s"),
    )

    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetUpstream"]["url"] == "http://127.0.0.1:9000"
    assert doc["widgetUpstream"]["innerAuth"]["kind"] == "header"

    await ctx.clear_widget_upstream()
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetUpstream"] is None
```

Note on the ProcessContext constructor signature: read `packages/optio-core/src/optio_core/context.py:19-64` and use the parameters it actually exposes. The exact parameter names may differ; adjust the test to match the real constructor.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k test_process_context_set_widget_upstream`
Expected: AttributeError on `set_widget_upstream`.

- [ ] **Step 3: Add methods to ProcessContext**

In `packages/optio-core/src/optio_core/context.py`, add after the `mark_ephemeral` method (around line 86):

```python
async def set_widget_upstream(
    self,
    url: str,
    inner_auth: "InnerAuth | None" = None,
) -> None:
    """Register the upstream URL and (optional) inner auth for the widget proxy."""
    from optio_core.store import update_widget_upstream
    await update_widget_upstream(
        self._db, self._prefix, self._process_oid, url, inner_auth,
    )

async def clear_widget_upstream(self) -> None:
    """Clear widgetUpstream so the proxy returns 404 for this process."""
    from optio_core.store import clear_widget_upstream
    await clear_widget_upstream(self._db, self._prefix, self._process_oid)
```

Also add the import at the top (if missing): `from optio_core.models import InnerAuth`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k test_process_context_set_widget_upstream`
Expected: PASS.

---

### Task 4: Add widgetData store helpers + ProcessContext methods

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py`
- Modify: `packages/optio-core/src/optio_core/context.py`
- Test: `packages/optio-core/tests/test_widget_primitives.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-core/tests/test_widget_primitives.py`:

```python
@pytest.mark.asyncio
async def test_widget_data_round_trip_nested_json(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="d1", name="D1")
    proc = await upsert_process(mongo_db, "test", task)

    ctx = ProcessContext(
        db=mongo_db, prefix="test",
        process_oid=proc["_id"], root_oid=proc["_id"],
        process_id="d1", params={}, metadata={},
        services={}, depth=0,
    )
    payload = {
        "localStorageOverrides": {
            "opencode.settings.dat": '{"defaultServerUrl":"/api/widget/abc/"}',
        },
        "allow": "clipboard-read",
        "custom_worker_key": [1, 2, 3],
    }
    await ctx.set_widget_data(payload)

    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetData"] == payload


@pytest.mark.asyncio
async def test_widget_data_clear_sets_null(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="d2", name="D2")
    proc = await upsert_process(mongo_db, "test", task)

    ctx = ProcessContext(
        db=mongo_db, prefix="test",
        process_oid=proc["_id"], root_oid=proc["_id"],
        process_id="d2", params={}, metadata={},
        services={}, depth=0,
    )
    await ctx.set_widget_data({"a": 1})
    await ctx.clear_widget_data()

    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["widgetData"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k widget_data`
Expected: AttributeError on `set_widget_data`.

- [ ] **Step 3: Add store helpers**

Append to `packages/optio-core/src/optio_core/store.py`:

```python
async def update_widget_data(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, data,
) -> None:
    """Overwrite widgetData with an arbitrary JSON-serializable value."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetData": data}},
    )


async def clear_widget_data(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear widgetData (sets to null)."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetData": None}},
    )
```

- [ ] **Step 4: Add ProcessContext methods**

In `packages/optio-core/src/optio_core/context.py`, add after `clear_widget_upstream`:

```python
async def set_widget_data(self, data) -> None:
    """Overwrite widgetData. Must be JSON-serializable. Optio does not interpret."""
    from optio_core.store import update_widget_data
    await update_widget_data(self._db, self._prefix, self._process_oid, data)

async def clear_widget_data(self) -> None:
    """Clear widgetData."""
    from optio_core.store import clear_widget_data
    await clear_widget_data(self._db, self._prefix, self._process_oid)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k widget_data`
Expected: both tests PASS.

---

### Task 5: Terminal-state teardown clears widgetUpstream

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:110-160`
- Test: `packages/optio-core/tests/test_widget_primitives.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_widget_primitives.py`:

```python
import asyncio
from optio_core.executor import Executor


@pytest.mark.asyncio
async def test_widget_upstream_cleared_on_done(mongo_db):
    async def task_setting_upstream(ctx):
        await ctx.set_widget_upstream("http://127.0.0.1:9000")
        await asyncio.sleep(0)  # yield so write lands

    task = TaskInstance(execute=task_setting_upstream, process_id="t-done", name="T")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("t-done")
    assert result == "done"

    doc = await mongo_db["test_processes"].find_one({"processId": "t-done"})
    assert doc["widgetUpstream"] is None


@pytest.mark.asyncio
async def test_widget_upstream_cleared_on_failed(mongo_db):
    async def task_fails(ctx):
        await ctx.set_widget_upstream("http://127.0.0.1:9000")
        raise RuntimeError("boom")

    task = TaskInstance(execute=task_fails, process_id="t-fail", name="T")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    result = await executor.launch_process("t-fail")
    assert result == "failed"

    doc = await mongo_db["test_processes"].find_one({"processId": "t-fail"})
    assert doc["widgetUpstream"] is None


@pytest.mark.asyncio
async def test_widget_upstream_cleared_on_cancelled(mongo_db):
    started = asyncio.Event()

    async def task_waits(ctx):
        await ctx.set_widget_upstream("http://127.0.0.1:9000")
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    task = TaskInstance(execute=task_waits, process_id="t-cancel", name="T")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    run = asyncio.create_task(executor.launch_process("t-cancel"))
    await started.wait()
    doc = await mongo_db["test_processes"].find_one({"processId": "t-cancel"})
    executor.request_cancel(doc["_id"])
    result = await run
    assert result == "cancelled"

    doc = await mongo_db["test_processes"].find_one({"processId": "t-cancel"})
    assert doc["widgetUpstream"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k "cleared_on"`
Expected: all three FAIL — `widgetUpstream` is still set after the process ends.

- [ ] **Step 3: Clear widgetUpstream in `_execute_process` terminal branches**

In `packages/optio-core/src/optio_core/executor.py`, locate `_execute_process` (around line 110–160). Import `clear_widget_upstream` from the store at the top of the file. Add a call to `clear_widget_upstream` in all three terminal branches: after the failed-branch status update (around line 130, before the `return "failed"` on line 133), after the done-branch status update (around line 147), and after the cancelled-branch status update (around line 156). DRY it by setting `end_state` and writing the clear after the status updates in one place, or by a small helper — implementer's choice.

Minimal diff: add these lines right before `self._cancellation_flags.pop(oid, None)` on line 158 (this path is shared by done and cancelled, and is also reached on the first `return "failed"` via line 131 above). Concretely:

```python
# After the failed branch's status+log update (currently around line 130):
await clear_widget_upstream(self._db, self._prefix, oid)
# Then: self._cancellation_flags.pop(...); await self._cleanup_ephemeral(...); return "failed"

# After the done/cancelled branch's status+log update (currently around line 156), before line 158:
await clear_widget_upstream(self._db, self._prefix, oid)
```

Choose the DRY variant the implementer prefers as long as the three tests pass.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k "cleared_on"`
Expected: all three PASS.

- [ ] **Step 5: Run full optio-core test suite to check for regressions**

Run: `cd packages/optio-core && pytest -v`
Expected: all tests (including pre-existing) PASS.

---

### Task 6: Dismiss and relaunch clear widgetUpstream and widgetData

**Files:**
- Modify: `packages/optio-core/src/optio_core/store.py:204-226`
- Test: `packages/optio-core/tests/test_widget_primitives.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `packages/optio-core/tests/test_widget_primitives.py`:

```python
from optio_core.store import clear_result_fields


@pytest.mark.asyncio
async def test_clear_result_fields_clears_widget_data_and_upstream(mongo_db):
    async def noop(ctx):
        pass

    task = TaskInstance(execute=noop, process_id="r1", name="R1")
    proc = await upsert_process(mongo_db, "test", task)
    oid = proc["_id"]

    await update_widget_data(mongo_db, "test", oid, {"a": 1})
    await update_widget_upstream(mongo_db, "test", oid, url="http://x")

    await clear_result_fields(mongo_db, "test", oid)

    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetData"] is None
    assert doc["widgetUpstream"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k test_clear_result_fields_clears_widget`
Expected: FAIL — fields are still present.

- [ ] **Step 3: Extend `clear_result_fields`**

In `packages/optio-core/src/optio_core/store.py:204-226`, add two entries to the `$set` dict:

```python
"widgetData": None,
"widgetUpstream": None,
```

Full updated `$set` becomes:

```python
"$set": {
    "status.error": None,
    "status.runningSince": None,
    "status.doneAt": None,
    "status.duration": None,
    "status.failedAt": None,
    "status.stoppedAt": None,
    "progress": Progress().to_dict(),
    "log": [],
    "widgetData": None,
    "widgetUpstream": None,
},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && pytest tests/test_widget_primitives.py -v -k test_clear_result_fields_clears_widget`
Expected: PASS.

- [ ] **Step 5: Run full optio-core test suite**

Run: `cd packages/optio-core && pytest -v`
Expected: all tests PASS (this change also covers the dismiss path, since `_handle_dismiss` in lifecycle.py:355-375 calls `clear_result_fields`, and the relaunch path for the same reason).

---

### Task 7: Add `uiWidget` and `widgetData` to optio-contracts ProcessSchema

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts:31-56`
- Test: `packages/optio-contracts/src/__tests__/process-schema.test.ts` (create or extend)

- [ ] **Step 1: Check for an existing schema test file**

Run: `ls packages/optio-contracts/src/__tests__/ 2>/dev/null || echo "no tests dir"`

If no test file exists for `ProcessSchema`, create one.

- [ ] **Step 2: Write the failing test**

Create `packages/optio-contracts/src/__tests__/process-schema.test.ts` (or extend the existing one):

```typescript
import { describe, it, expect } from 'vitest';
import { ProcessSchema } from '../schemas/process.js';

function baseProcess() {
  return {
    _id: '507f1f77bcf86cd799439011',
    processId: 'p1',
    name: 'P1',
    rootId: '507f1f77bcf86cd799439011',
    depth: 0,
    order: 0,
    cancellable: true,
    status: { state: 'idle' as const },
    progress: { percent: null },
    log: [],
    createdAt: new Date(),
  };
}

describe('ProcessSchema widget fields', () => {
  it('accepts uiWidget as an optional string', () => {
    const parsed = ProcessSchema.parse({ ...baseProcess(), uiWidget: 'iframe' });
    expect(parsed.uiWidget).toBe('iframe');
  });

  it('accepts widgetData as arbitrary JSON', () => {
    const data = { localStorageOverrides: { foo: 'bar' }, nested: { a: [1, 2] } };
    const parsed = ProcessSchema.parse({ ...baseProcess(), widgetData: data });
    expect(parsed.widgetData).toEqual(data);
  });

  it('accepts a process without widget fields', () => {
    expect(() => ProcessSchema.parse(baseProcess())).not.toThrow();
  });

  it('rejects widgetUpstream (server-side only; must not be in the schema)', () => {
    // widgetUpstream is intentionally NOT part of ProcessSchema.
    // Strict parsing would reject it; default (non-strict) strips it. Assert it is stripped.
    const parsed = ProcessSchema.parse({
      ...baseProcess(),
      widgetUpstream: { url: 'http://x' },
    } as any);
    expect((parsed as any).widgetUpstream).toBeUndefined();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd packages/optio-contracts && node_modules/.bin/vitest run __tests__/process-schema.test.ts`

(If vitest isn't wired into this package, run via workspace root: `pnpm --filter optio-contracts test` — check the package's package.json `test` script first.)

Expected: FAIL — `uiWidget` and `widgetData` fields are not in the schema.

- [ ] **Step 4: Add fields to ProcessSchema**

In `packages/optio-contracts/src/schemas/process.ts`, locate `ProcessSchema` (lines 31–56). Add two new optional fields at the end, before `createdAt`:

```typescript
uiWidget: z.string().optional(),
widgetData: z.unknown().optional(),
```

Deliberately **do not** add `widgetUpstream` — it is server-side only.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/optio-contracts && node_modules/.bin/vitest run __tests__/process-schema.test.ts`
Expected: all four tests PASS.

- [ ] **Step 6: Run optio-contracts typecheck**

Run: `cd packages/optio-contracts && node_modules/.bin/tsc --noEmit`
Expected: no errors.

---

### Task 8: Tree-stream poller includes widgetData

**Files:**
- Modify: `packages/optio-api/src/stream-poller.ts:77-169`
- Test: `packages/optio-api/src/__tests__/stream-poller.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `packages/optio-api/src/__tests__/stream-poller.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { createTreePoller } from '../stream-poller.js';

describe('createTreePoller widgetData propagation', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  const prefix = 'test';

  beforeEach(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('t');
  });

  afterEach(async () => {
    await client.close();
    await mongod.stop();
  });

  it('includes widgetData in the update event payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${prefix}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      widgetData: { hello: 'world' },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();

    // Poller runs every 1000ms; fake timers for determinism
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes[0].widgetData).toEqual({ hello: 'world' });
  });

  it('fires an update event when ONLY widgetData changes', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    const coll = db.collection(`${prefix}_processes`);
    await coll.insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      widgetData: { v: 1 },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));

    const before = events.filter((e) => e.type === 'update').length;
    expect(before).toBeGreaterThanOrEqual(1);

    await coll.updateOne(
      { _id: rootId },
      { $set: { widgetData: { v: 2 } } },
    );
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const after = events.filter((e) => e.type === 'update').length;
    expect(after).toBeGreaterThan(before);
    const last = [...events].reverse().find((e) => e.type === 'update');
    expect(last.processes[0].widgetData).toEqual({ v: 2 });
  });

  it('never includes widgetUpstream in the payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${prefix}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      widgetUpstream: {
        url: 'http://127.0.0.1:9000',
        innerAuth: { kind: 'basic', username: 'u', password: 'p' },
      },
      cancellable: true,
      log: [],
    });

    const poller = createTreePoller({
      db, prefix,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    const update = events.find((e) => e.type === 'update');
    expect(update).toBeDefined();
    expect(update.processes[0].widgetUpstream).toBeUndefined();
    // Belt and suspenders — no proc should have any auth-adjacent field.
    for (const p of update.processes) {
      expect(Object.keys(p)).not.toContain('widgetUpstream');
    }
  });
});
```

Note: this test uses `mongodb-memory-server`, which is already a devDependency of optio-api (see package.json line 59).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/stream-poller.test.ts`
Expected: first two tests FAIL (widgetData not in payload / fingerprint); third test PASSES (widgetUpstream was never in payload anyway, but assert it stays that way).

- [ ] **Step 3: Update `createTreePoller` snapshot fingerprint and payload**

In `packages/optio-api/src/stream-poller.ts:77-169`, modify `createTreePoller`. Two edits:

1. Line 94–95, extend the snapshot fingerprint to include `widgetData`:

```typescript
const snapshot = JSON.stringify(
  allProcs.map((p: any) => ({
    id: p._id, status: p.status, progress: p.progress, widgetData: p.widgetData,
  })),
);
```

2. Lines 101–110, extend the per-process payload to include `widgetData`:

```typescript
sendEvent({
  type: 'update',
  processes: allProcs.map((p: any) => ({
    _id: p._id.toString(),
    parentId: p.parentId?.toString() ?? null,
    name: p.name,
    status: p.status,
    progress: p.progress,
    cancellable: p.cancellable ?? false,
    depth: p.depth,
    order: p.order,
    widgetData: p.widgetData,
  })),
});
```

Do not touch `createListPoller` (lines 15–69) — the list stream stays slim.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/stream-poller.test.ts`
Expected: all three PASS.

---

### Task 9: Widget-upstream registry (TTL cache)

**Files:**
- Create: `packages/optio-api/src/widget-upstream-registry.ts`
- Test: `packages/optio-api/src/__tests__/widget-upstream-registry.test.ts`

**Note (deviation from spec):** The spec describes "invalidation via the tree poller." This plan uses a simpler 5-second TTL cache — same intent, avoids the spec's unreliable per-connection-invalidation. If a worker writes `widgetUpstream` and the cache is stale, up to 5 seconds of incorrect upstream. In practice, workers set upstream once at go-live and clear on teardown; the TTL is a safety net against teardown-staleness, not a common invalidation path.

- [ ] **Step 1: Write the failing test**

Create `packages/optio-api/src/__tests__/widget-upstream-registry.test.ts`:

```typescript
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { createWidgetUpstreamRegistry } from '../widget-upstream-registry.js';

describe('widgetUpstreamRegistry', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('caches a value and returns it within TTL', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', { url: 'http://a', innerAuth: null });
    expect(reg.get('proc1')).toEqual({ url: 'http://a', innerAuth: null });
  });

  it('returns undefined for an unknown key', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    expect(reg.get('nope')).toBeUndefined();
  });

  it('expires after TTL', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', { url: 'http://a', innerAuth: null });
    vi.advanceTimersByTime(5001);
    expect(reg.get('proc1')).toBeUndefined();
  });

  it('supports explicit invalidate', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', { url: 'http://a', innerAuth: null });
    reg.invalidate('proc1');
    expect(reg.get('proc1')).toBeUndefined();
  });

  it('stores null as a distinct cached-miss value', () => {
    // Caching a known-missing upstream avoids re-reading MongoDB repeatedly.
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', null);
    expect(reg.has('proc1')).toBe(true);
    expect(reg.get('proc1')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/widget-upstream-registry.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the registry**

Create `packages/optio-api/src/widget-upstream-registry.ts`:

```typescript
export interface WidgetUpstreamValue {
  url: string;
  innerAuth: InnerAuthDoc | null;
}

export type InnerAuthDoc =
  | { kind: 'basic'; username: string; password: string }
  | { kind: 'query'; name: string; value: string }
  | { kind: 'header'; name: string; value: string };

export interface WidgetUpstreamRegistry {
  get(processId: string): WidgetUpstreamValue | null | undefined;
  has(processId: string): boolean;
  set(processId: string, value: WidgetUpstreamValue | null): void;
  invalidate(processId: string): void;
}

interface CachedEntry {
  value: WidgetUpstreamValue | null;
  expiresAt: number;
}

export function createWidgetUpstreamRegistry(opts: { ttlMs: number }): WidgetUpstreamRegistry {
  const cache = new Map<string, CachedEntry>();

  function getEntry(processId: string): CachedEntry | undefined {
    const entry = cache.get(processId);
    if (!entry) return undefined;
    if (Date.now() > entry.expiresAt) {
      cache.delete(processId);
      return undefined;
    }
    return entry;
  }

  return {
    get(processId) {
      return getEntry(processId)?.value;
    },
    has(processId) {
      return getEntry(processId) !== undefined;
    },
    set(processId, value) {
      cache.set(processId, { value, expiresAt: Date.now() + opts.ttlMs });
    },
    invalidate(processId) {
      cache.delete(processId);
    },
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/widget-upstream-registry.test.ts`
Expected: all five PASS.

---

### Task 10: Widget-proxy core — upstream resolver and inner-auth injection

**Files:**
- Create: `packages/optio-api/src/widget-proxy-core.ts`
- Test: `packages/optio-api/src/__tests__/widget-proxy-core.test.ts`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-api/src/__tests__/widget-proxy-core.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { MongoMemoryServer } from 'mongodb-memory-server';
import {
  resolveWidgetUpstream,
  applyInnerAuthHeaders,
  applyInnerAuthQuery,
} from '../widget-proxy-core.js';
import { createWidgetUpstreamRegistry } from '../widget-upstream-registry.js';

describe('resolveWidgetUpstream', () => {
  let mongod: MongoMemoryServer;
  let client: MongoClient;
  let db: Db;
  const prefix = 'test';

  beforeEach(async () => {
    mongod = await MongoMemoryServer.create();
    client = new MongoClient(mongod.getUri());
    await client.connect();
    db = client.db('t');
  });

  afterEach(async () => {
    await client.close();
    await mongod.stop();
  });

  it('returns null when process is not found', async () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const missing = new ObjectId().toString();
    const result = await resolveWidgetUpstream(db, prefix, reg, missing);
    expect(result).toBeNull();
  });

  it('returns null when widgetUpstream is null', async () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const oid = new ObjectId();
    await db.collection(`${prefix}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: null,
    });

    const result = await resolveWidgetUpstream(db, prefix, reg, oid.toString());
    expect(result).toBeNull();
  });

  it('returns widgetUpstream when set and caches it', async () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const oid = new ObjectId();
    await db.collection(`${prefix}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: {
        url: 'http://127.0.0.1:9000',
        innerAuth: { kind: 'header', name: 'X-Tok', value: 's' },
      },
    });

    const first = await resolveWidgetUpstream(db, prefix, reg, oid.toString());
    expect(first?.url).toBe('http://127.0.0.1:9000');

    // Delete from DB; cache should still return the value on next call.
    await db.collection(`${prefix}_processes`).deleteOne({ _id: oid });
    const second = await resolveWidgetUpstream(db, prefix, reg, oid.toString());
    expect(second?.url).toBe('http://127.0.0.1:9000');
  });
});

describe('applyInnerAuthHeaders', () => {
  it('adds Authorization: Basic for BasicAuth', () => {
    const headers = applyInnerAuthHeaders(
      { kind: 'basic', username: 'u', password: 'p' },
      { host: 'x' },
    );
    expect(headers.authorization).toBe('Basic ' + Buffer.from('u:p').toString('base64'));
    expect(headers.host).toBe('x');
  });

  it('adds a custom header for HeaderAuth', () => {
    const headers = applyInnerAuthHeaders(
      { kind: 'header', name: 'X-Tok', value: 's' },
      {},
    );
    expect(headers['x-tok']).toBe('s');
  });

  it('is a no-op for null inner auth', () => {
    const h = applyInnerAuthHeaders(null, { foo: 'bar' });
    expect(h).toEqual({ foo: 'bar' });
  });

  it('does not modify headers for QueryAuth (that goes through URL)', () => {
    const h = applyInnerAuthHeaders(
      { kind: 'query', name: 'tok', value: 's' },
      { x: '1' },
    );
    expect(h).toEqual({ x: '1' });
  });
});

describe('applyInnerAuthQuery', () => {
  it('appends ?name=value for QueryAuth on a URL with no query', () => {
    const out = applyInnerAuthQuery(
      { kind: 'query', name: 'tok', value: 's' },
      '/foo/bar',
    );
    expect(out).toBe('/foo/bar?tok=s');
  });

  it('appends &name=value for QueryAuth on a URL with existing query', () => {
    const out = applyInnerAuthQuery(
      { kind: 'query', name: 'tok', value: 's' },
      '/foo?x=1',
    );
    expect(out).toBe('/foo?x=1&tok=s');
  });

  it('url-encodes the query value', () => {
    const out = applyInnerAuthQuery(
      { kind: 'query', name: 'tok', value: 'a b+c' },
      '/foo',
    );
    expect(out).toBe('/foo?tok=a%20b%2Bc');
  });

  it('is a no-op for non-query auth', () => {
    const out = applyInnerAuthQuery(
      { kind: 'header', name: 'X-Tok', value: 's' },
      '/foo',
    );
    expect(out).toBe('/foo');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/widget-proxy-core.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement widget-proxy-core.ts**

Create `packages/optio-api/src/widget-proxy-core.ts`:

```typescript
import { ObjectId, type Db } from 'mongodb';
import type { InnerAuthDoc, WidgetUpstreamRegistry, WidgetUpstreamValue } from './widget-upstream-registry.js';

export async function resolveWidgetUpstream(
  db: Db,
  prefix: string,
  registry: WidgetUpstreamRegistry,
  processId: string,
): Promise<WidgetUpstreamValue | null> {
  if (registry.has(processId)) {
    return registry.get(processId) ?? null;
  }

  let oid: ObjectId;
  try {
    oid = new ObjectId(processId);
  } catch {
    registry.set(processId, null);
    return null;
  }

  const doc = await db.collection(`${prefix}_processes`).findOne(
    { _id: oid },
    { projection: { widgetUpstream: 1 } },
  );
  const upstream = (doc?.widgetUpstream ?? null) as WidgetUpstreamValue | null;
  registry.set(processId, upstream);
  return upstream;
}

export function applyInnerAuthHeaders(
  innerAuth: InnerAuthDoc | null,
  headers: Record<string, string | string[] | undefined>,
): Record<string, string | string[] | undefined> {
  if (!innerAuth) return headers;

  if (innerAuth.kind === 'basic') {
    const encoded = Buffer.from(`${innerAuth.username}:${innerAuth.password}`).toString('base64');
    return { ...headers, authorization: `Basic ${encoded}` };
  }

  if (innerAuth.kind === 'header') {
    return { ...headers, [innerAuth.name.toLowerCase()]: innerAuth.value };
  }

  // query auth does not touch headers
  return headers;
}

export function applyInnerAuthQuery(
  innerAuth: InnerAuthDoc | null,
  url: string,
): string {
  if (!innerAuth || innerAuth.kind !== 'query') return url;
  const separator = url.includes('?') ? '&' : '?';
  const encodedValue = encodeURIComponent(innerAuth.value);
  return `${url}${separator}${innerAuth.name}=${encodedValue}`;
}

export function isWriteMethod(method: string): boolean {
  const m = method.toUpperCase();
  return m !== 'GET' && m !== 'HEAD' && m !== 'OPTIONS';
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/__tests__/widget-proxy-core.test.ts`
Expected: all tests PASS.

---

### Task 11: Install `@fastify/http-proxy`

**Files:**
- Modify: `packages/optio-api/package.json`

- [ ] **Step 1: Add dependency**

In `packages/optio-api/package.json`, add to the `dependencies` block (alphabetical order):

```json
"@fastify/http-proxy": "^11.0.0",
```

Pick the latest `11.x` available at implementation time (Fastify 5 compatible). If a newer major is current, use that and verify WS hook shape in the next task.

- [ ] **Step 2: Install**

Run from repo root: `pnpm install`
Expected: `@fastify/http-proxy` installed into `packages/optio-api/node_modules` (hoisted to workspace root).

- [ ] **Step 3: Verify the package is importable**

Run: `cd packages/optio-api && node -e "import('@fastify/http-proxy').then(m => console.log('ok:', typeof m.default))"`
Expected: `ok: function` (or `object` depending on the module shape — either indicates a successful import).

---

### Task 12: Fastify widget-proxy adapter — HTTP path, preHandler, and inner-auth injection

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts` (add `registerWidgetProxy` export)
- Modify: `packages/optio-api/src/index.ts` (re-export anything needed)
- Test: `packages/optio-api/src/adapters/__tests__/fastify-widget-proxy.test.ts`

- [ ] **Step 1: Write the failing test (HTTP path only — WS in Task 15)**

Create `packages/optio-api/src/adapters/__tests__/fastify-widget-proxy.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, beforeAll, afterAll } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { MongoMemoryServer } from 'mongodb-memory-server';
import { createServer, type Server } from 'http';
import { registerWidgetProxy } from '../fastify.js';

describe('registerWidgetProxy — HTTP path', () => {
  let mongod: MongoMemoryServer;
  let mongoClient: MongoClient;
  let db: Db;
  let upstream: Server;
  let upstreamPort: number;
  let upstreamRequests: Array<{ url: string; method: string; headers: any; body: string }>;
  let upstreamResponder: (req: any, res: any, body: string) => void;

  beforeAll(async () => {
    mongod = await MongoMemoryServer.create();
    mongoClient = new MongoClient(mongod.getUri());
    await mongoClient.connect();
    db = mongoClient.db('t');
  });

  afterAll(async () => {
    await mongoClient.close();
    await mongod.stop();
  });

  beforeEach(async () => {
    upstreamRequests = [];
    upstreamResponder = (req, res, _body) => {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/plain');
      res.end('hi');
    };
    upstream = createServer((req, res) => {
      let body = '';
      req.on('data', (c) => (body += c));
      req.on('end', () => {
        upstreamRequests.push({
          url: req.url!, method: req.method!,
          headers: { ...req.headers }, body,
        });
        upstreamResponder(req, res, body);
      });
    });
    await new Promise<void>((r) => upstream.listen(0, () => r()));
    upstreamPort = (upstream.address() as any).port;
    await db.collection('test_processes').deleteMany({});
  });

  afterEach(async () => {
    await new Promise<void>((r) => upstream.close(() => r()));
  });

  async function makeApp(authenticate: (req: any) => any = () => 'operator'): Promise<FastifyInstance> {
    const app = Fastify();
    registerWidgetProxy(app, {
      db, prefix: 'test', authenticate,
    });
    await app.ready();
    return app;
  }

  async function insertProcess(upstreamConfig?: any): Promise<ObjectId> {
    const oid = new ObjectId();
    await db.collection('test_processes').insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: upstreamConfig,
    });
    return oid;
  }

  it('returns 401 when authenticate returns null', async () => {
    const app = await makeApp(() => null);
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: `/api/widget/${oid}/foo` });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('returns 403 on POST when authenticate returns viewer', async () => {
    const app = await makeApp(() => 'viewer');
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'POST', url: `/api/widget/${oid}/foo`, payload: '' });
    expect(res.statusCode).toBe(403);
    await app.close();
  });

  it('allows viewer on GET and forwards to upstream', async () => {
    const app = await makeApp(() => 'viewer');
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: `/api/widget/${oid}/foo` });
    expect(res.statusCode).toBe(200);
    expect(upstreamRequests[0].url).toBe('/foo');
    await app.close();
  });

  it('returns 404 when process is unknown', async () => {
    const app = await makeApp();
    const unknownOid = new ObjectId();
    const res = await app.inject({ method: 'GET', url: `/api/widget/${unknownOid}/foo` });
    expect(res.statusCode).toBe(404);
    await app.close();
  });

  it('returns 404 when widgetUpstream is null', async () => {
    const app = await makeApp();
    const oid = await insertProcess(null);
    const res = await app.inject({ method: 'GET', url: `/api/widget/${oid}/anything` });
    expect(res.statusCode).toBe(404);
    await app.close();
  });

  it('injects BasicAuth as Authorization header', async () => {
    const app = await makeApp();
    const oid = await insertProcess({
      url: `http://127.0.0.1:${upstreamPort}`,
      innerAuth: { kind: 'basic', username: 'u', password: 'p' },
    });
    await app.inject({ method: 'GET', url: `/api/widget/${oid}/foo` });
    const expected = 'Basic ' + Buffer.from('u:p').toString('base64');
    expect(upstreamRequests[0].headers.authorization).toBe(expected);
    await app.close();
  });

  it('injects HeaderAuth as named header', async () => {
    const app = await makeApp();
    const oid = await insertProcess({
      url: `http://127.0.0.1:${upstreamPort}`,
      innerAuth: { kind: 'header', name: 'X-Opencode-Token', value: 'secret' },
    });
    await app.inject({ method: 'GET', url: `/api/widget/${oid}/foo` });
    expect(upstreamRequests[0].headers['x-opencode-token']).toBe('secret');
    await app.close();
  });

  it('injects QueryAuth into URL', async () => {
    const app = await makeApp();
    const oid = await insertProcess({
      url: `http://127.0.0.1:${upstreamPort}`,
      innerAuth: { kind: 'query', name: 'auth_token', value: 'secret' },
    });
    await app.inject({ method: 'GET', url: `/api/widget/${oid}/foo?x=1` });
    // One of the forwarded URLs must contain auth_token=secret AND x=1.
    const forwarded = upstreamRequests[0].url;
    expect(forwarded).toContain('auth_token=secret');
    expect(forwarded).toContain('x=1');
    await app.close();
  });

  it('passes upstream 502 when upstream is down', async () => {
    await new Promise<void>((r) => upstream.close(() => r()));
    const app = await makeApp();
    const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
    const res = await app.inject({ method: 'GET', url: `/api/widget/${oid}/foo` });
    expect([502, 503]).toContain(res.statusCode);
    await app.close();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/adapters/__tests__/fastify-widget-proxy.test.ts`
Expected: FAIL — `registerWidgetProxy` is not exported.

- [ ] **Step 3: Implement `registerWidgetProxy` in the Fastify adapter**

Add to `packages/optio-api/src/adapters/fastify.ts` (after existing exports):

```typescript
import httpProxy from '@fastify/http-proxy';
import type { Db } from 'mongodb';
import type { AuthCallback } from '../auth.js';
import { checkAuth } from '../auth.js';
import { createWidgetUpstreamRegistry, type WidgetUpstreamValue } from '../widget-upstream-registry.js';
import {
  resolveWidgetUpstream,
  applyInnerAuthHeaders,
  applyInnerAuthQuery,
  isWriteMethod,
} from '../widget-proxy-core.js';

const WIDGET_CACHE_TTL_MS = 5000;

export interface OptioWidgetProxyOptions {
  db: Db;
  prefix: string;
  authenticate: AuthCallback<import('fastify').FastifyRequest>;
  ttlMs?: number;
}

export function registerWidgetProxy(app: import('fastify').FastifyInstance, opts: OptioWidgetProxyOptions): void {
  const registry = createWidgetUpstreamRegistry({ ttlMs: opts.ttlMs ?? WIDGET_CACHE_TTL_MS });

  // Extract processId from an incoming URL like /api/widget/<processId>/...
  function extractProcessId(url: string): string | null {
    const m = url.match(/^\/api\/widget\/([a-f0-9]{24})(?:\/|$|\?)/i);
    return m ? m[1] : null;
  }

  app.register(httpProxy, {
    upstream: 'http://unused.invalid/', // overridden by getUpstream
    prefix: '/api/widget',
    rewritePrefix: '', // strips /api/widget; we further strip /<processId> below
    websocket: true,  // actual WS auth wired in Task 15

    preHandler: async (req, reply) => {
      const processId = extractProcessId(req.url);
      if (!processId) {
        reply.code(404).send({ message: 'Invalid widget URL' });
        return;
      }

      const authResult = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
      if (authResult) {
        reply.code(authResult.status).send(authResult.body);
        return;
      }

      const upstream = await resolveWidgetUpstream(opts.db, opts.prefix, registry, processId);
      if (!upstream) {
        reply.code(404).send({ message: 'Widget upstream not found' });
        return;
      }

      // Stash on raw request so replyOptions (which sees IncomingMessage) can read it.
      (req.raw as any).__optioWidget = { processId, upstream };

      // Strip /<processId> from req.raw.url so @fastify/http-proxy's rewritePrefix='' forwards just the inner path.
      // Incoming has already had /api/widget stripped by Fastify's prefix matching? Depends on version;
      // defensive approach: do the full rewrite here.
      const fullPath = req.raw.url ?? req.url;
      const inner = fullPath.replace(new RegExp(`^/api/widget/${processId}`), '') || '/';
      req.raw.url = applyInnerAuthQuery(upstream.innerAuth, inner);
    },

    replyOptions: {
      getUpstream: (req: any) => {
        const widget = (req as any).__optioWidget;
        return widget?.upstream.url ?? 'http://unused.invalid/';
      },
      rewriteRequestHeaders: (req: any, headers: Record<string, any>) => {
        const widget = (req as any).__optioWidget;
        if (!widget) return headers;
        return applyInnerAuthHeaders(widget.upstream.innerAuth, headers);
      },
    },
  });
}
```

**Known caveat:** the interaction between Fastify's `prefix`, `@fastify/http-proxy`'s `rewritePrefix`, and the `preHandler` mutation of `req.raw.url` is version-dependent. The test suite in Step 1 exercises all the important cases; if any test fails, iterate on the `rewritePrefix` + preHandler URL mutation until all tests pass. Consult `@fastify/http-proxy` README for the pinned version. The WS subsystem is exercised separately in Task 15.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/adapters/__tests__/fastify-widget-proxy.test.ts`
Expected: all HTTP-path tests PASS. If some fail because of URL-rewriting interactions, adjust the preHandler or `rewritePrefix` value until all pass before moving on.

- [ ] **Step 5: Run the full optio-api test suite**

Run: `cd packages/optio-api && node_modules/.bin/vitest run`
Expected: all tests PASS.

---

### Task 13: Export `registerWidgetProxy` from the Fastify adapter entry

**Files:**
- Modify: `packages/optio-api/src/index.ts` (if framework-agnostic re-exports live here)
- Verify: `packages/optio-api/package.json` `exports` block

- [ ] **Step 1: Verify `registerWidgetProxy` is reachable via `optio-api/fastify`**

Run:

```bash
cd packages/optio-api && node_modules/.bin/tsc --noEmit
```

and then:

```bash
node -e "import('./dist/adapters/fastify.js').then(m => console.log(Object.keys(m)))"
```

Expected: list includes `registerOptioApi` (existing) and `registerWidgetProxy` (new).

If the build output isn't produced by `tsc --noEmit`, run `node_modules/.bin/tsc` (without `--noEmit`) first, then the node eval.

No code change typically needed — the adapter's public exports are surfaced via the `./fastify` subpath export in package.json. Confirm and move on.

---

### Task 14: Tree-stream poller invalidates widget-upstream cache on per-process change

**Files:**
- Modify: `packages/optio-api/src/stream-poller.ts` (optional hook)
- Modify: `packages/optio-api/src/adapters/fastify.ts`

**Decision:** The cache already has a 5-second TTL. Adding per-poller invalidation is a nice-to-have for processes that have an active tree stream viewer. For MVP, skip this — the TTL handles correctness. If you want the nice-to-have, the simplest path is to pass the registry into `createTreePoller` via options and invalidate on change events. The spec leaves this open; YAGNI applies.

- [ ] **Step 1: Skip, or explicitly implement.**

Minimum: no code change. Document in the spec's "future work" that per-poller invalidation is not implemented; TTL handles it. Leave this task checkbox here so the implementer sees the decision and confirms.

---

### Task 15: WebSocket upgrade auth in the widget proxy

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts` (extend `registerWidgetProxy`)
- Test: `packages/optio-api/src/adapters/__tests__/fastify-widget-proxy.test.ts` (extend)

**Plan-time deliverable (from spec):** confirm `@fastify/http-proxy`'s WS hook shape in the pinned version.

- [ ] **Step 1: Read the WS hook docs for the pinned version**

Open `node_modules/@fastify/http-proxy/README.md` and search for `wsHooks`, `preConnect`, or `websocket`. Capture:
- The exact option name for a pre-upgrade hook.
- The shape of the request object passed to the hook.
- Whether the hook can reject by throwing or by calling a callback.

- [ ] **Step 2: Write the failing test**

Extend the existing test file with a WS-upgrade test. Outline (fill in concrete shapes based on Step 1):

```typescript
import { WebSocketServer, WebSocket } from 'ws';

// In beforeEach, upgrade the http server with a WS server echoing bytes:
//   const wss = new WebSocketServer({ server: upstream });
//   wss.on('connection', (ws) => ws.on('message', (m) => ws.send(m)));
// Then in tests:

it('WS upgrade is rejected when authenticate returns null', async () => {
  const app = await makeApp(() => null);
  const oid = await insertProcess({ url: `http://127.0.0.1:${upstreamPort}`, innerAuth: null });
  await app.listen({ port: 0 });
  const port = (app.server.address() as any).port;

  const ws = new WebSocket(`ws://127.0.0.1:${port}/api/widget/${oid}/ws`);
  const result = await new Promise<string>((resolve) => {
    ws.on('error', () => resolve('error'));
    ws.on('open', () => resolve('open'));
    setTimeout(() => resolve('timeout'), 1000);
  });
  expect(result).toBe('error');
  await app.close();
});

it('WS upgrade is allowed when authenticate returns viewer and forwards messages', async () => {
  // ... echo round-trip over proxied WS ...
});

it('WS upgrade carries HeaderAuth inner auth to the upstream handshake', async () => {
  // Upstream WS server asserts the expected header on connection.
});
```

- [ ] **Step 3: Wire WS auth into `registerWidgetProxy`**

Extend the `httpProxy` registration in `registerWidgetProxy` with the WS hook. Concrete shape depends on Step 1 findings. Typical shape (v10+):

```typescript
wsHooks: {
  onIncomingMessage: (/* ... */) => { /* pass */ },
  // Pre-upgrade authentication hook — exact name may differ by version.
},
wsServerOptions: {
  verifyClient: async (info, callback) => {
    const processId = extractProcessId(info.req.url ?? '');
    if (!processId) return callback(false, 404, 'Invalid widget URL');

    const role = await opts.authenticate(info.req as any);
    if (!role) return callback(false, 401, 'Unauthorized');

    const upstream = await resolveWidgetUpstream(opts.db, opts.prefix, registry, processId);
    if (!upstream) return callback(false, 404, 'Widget upstream not found');

    (info.req as any).__optioWidget = { processId, upstream };
    // URL rewrite for WS: same as HTTP path
    const inner = (info.req.url ?? '').replace(new RegExp(`^/api/widget/${processId}`), '') || '/';
    info.req.url = applyInnerAuthQuery(upstream.innerAuth, inner);
    // Header auth for WS: must be applied to the upstream handshake headers,
    // which @fastify/http-proxy supports via wsUpstream config / upgrade options — check README.
    callback(true);
  },
},
```

The concrete shape is **version-dependent**. The implementer decides based on Step 1 findings. If the WS hooks do not give us async auth cleanly, fall back to wiring the upgrade event on the underlying Node HTTP server manually (`app.server.on('upgrade', ...)`) and delegating to the same helpers used for HTTP. Acceptance criterion is the test suite passing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-api && node_modules/.bin/vitest run src/adapters/__tests__/fastify-widget-proxy.test.ts`
Expected: all tests PASS.

---

### Task 16: Widget registry (optio-ui)

**Files:**
- Create: `packages/optio-ui/src/widgets/registry.ts`
- Test: `packages/optio-ui/src/__tests__/widget-registry.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-ui/src/__tests__/widget-registry.test.tsx`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest';
import { registerWidget, getWidget, _clearWidgetRegistry } from '../widgets/registry.js';

describe('widget registry', () => {
  beforeEach(() => {
    _clearWidgetRegistry();
  });

  it('registers and retrieves a widget', () => {
    const Foo = () => null;
    registerWidget('foo', Foo);
    expect(getWidget('foo')).toBe(Foo);
  });

  it('returns undefined for unregistered names', () => {
    expect(getWidget('nope')).toBeUndefined();
  });

  it('replaces on re-registration', () => {
    const A = () => null;
    const B = () => null;
    registerWidget('x', A);
    registerWidget('x', B);
    expect(getWidget('x')).toBe(B);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/widget-registry.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the registry**

Create `packages/optio-ui/src/widgets/registry.ts`:

```typescript
import type { ComponentType } from 'react';

export interface WidgetProps {
  process: any; // Process from optio-contracts; import when shape is stable.
  apiBaseUrl: string;
  widgetProxyUrl: string; // ends with '/' — trailing slash is load-bearing
  prefix: string;
  database?: string;
}

export type WidgetComponent = ComponentType<WidgetProps>;

const widgets = new Map<string, WidgetComponent>();

export function registerWidget(name: string, component: WidgetComponent): void {
  widgets.set(name, component);
}

export function getWidget(name: string): WidgetComponent | undefined {
  return widgets.get(name);
}

// Test-only reset. Not exported from package entry point.
export function _clearWidgetRegistry(): void {
  widgets.clear();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/widget-registry.test.tsx`
Expected: all three PASS.

---

### Task 17: Iframe widget component

**Files:**
- Create: `packages/optio-ui/src/widgets/IframeWidget.tsx`
- Test: `packages/optio-ui/src/__tests__/IframeWidget.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-ui/src/__tests__/IframeWidget.test.tsx`:

```typescript
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import React from 'react';
import { IframeWidget } from '../widgets/IframeWidget.js';

function makeProps(overrides: Partial<any> = {}) {
  return {
    process: {
      _id: 'abc',
      processId: 'p',
      name: 'P',
      status: { state: 'running' },
      progress: { percent: null },
      ...overrides,
    },
    apiBaseUrl: 'http://localhost:3000',
    widgetProxyUrl: 'http://localhost:3000/api/widget/abc/',
    prefix: 'optio',
  };
}

describe('IframeWidget', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('renders a loading placeholder when widgetData is absent', () => {
    render(<IframeWidget {...makeProps({ widgetData: undefined })} />);
    expect(screen.queryByTestId('optio-widget-iframe')).toBeNull();
    expect(screen.getByTestId('optio-widget-loading')).toBeTruthy();
  });

  it('mounts iframe when widgetData is present', () => {
    render(<IframeWidget {...makeProps({ widgetData: {} })} />);
    const iframe = screen.getByTestId('optio-widget-iframe') as HTMLIFrameElement;
    expect(iframe.src).toContain('/api/widget/abc/');
  });

  it('writes localStorageOverrides before mount and clears on unmount', () => {
    const props = makeProps({
      widgetData: { localStorageOverrides: { 'my.key': 'v1' } },
    });
    const { unmount } = render(<IframeWidget {...props} />);
    expect(localStorage.getItem('my.key')).toBe('v1');
    unmount();
    expect(localStorage.getItem('my.key')).toBeNull();
  });

  it('honors iframeSrc override', () => {
    const props = makeProps({
      widgetData: { iframeSrc: 'http://other.example/' },
    });
    render(<IframeWidget {...props} />);
    const iframe = screen.getByTestId('optio-widget-iframe') as HTMLIFrameElement;
    expect(iframe.src).toBe('http://other.example/');
  });

  it('honors sandbox and allow overrides', () => {
    const props = makeProps({
      widgetData: { sandbox: 'allow-scripts', allow: 'clipboard-read' },
    });
    render(<IframeWidget {...props} />);
    const iframe = screen.getByTestId('optio-widget-iframe') as HTMLIFrameElement;
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.getAttribute('allow')).toBe('clipboard-read');
  });

  it('shows session-ended banner on terminal state but keeps iframe mounted', () => {
    const props = makeProps({
      status: { state: 'done' },
      widgetData: {},
    });
    render(<IframeWidget {...props} />);
    expect(screen.getByTestId('optio-widget-iframe')).toBeTruthy();
    expect(screen.getByTestId('optio-widget-session-ended')).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/IframeWidget.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `IframeWidget`**

Create `packages/optio-ui/src/widgets/IframeWidget.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { WidgetProps } from './registry.js';
import { registerWidget } from './registry.js';

interface IframeWidgetData {
  localStorageOverrides?: Record<string, string>;
  iframeSrc?: string;
  sandbox?: string;
  allow?: string;
  title?: string;
}

const TERMINAL_STATES = new Set(['done', 'failed', 'cancelled']);

export function IframeWidget(props: WidgetProps) {
  const widgetData = (props.process.widgetData ?? undefined) as IframeWidgetData | undefined;
  const state: string | undefined = props.process.status?.state;
  const isTerminal = state !== undefined && TERMINAL_STATES.has(state);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  useEffect(() => {
    if (!widgetData?.localStorageOverrides) return;
    const keys = Object.keys(widgetData.localStorageOverrides);
    for (const k of keys) {
      localStorage.setItem(k, widgetData.localStorageOverrides[k]);
    }
    return () => {
      for (const k of keys) localStorage.removeItem(k);
    };
  }, [widgetData?.localStorageOverrides]);

  if (!widgetData) {
    return <div data-testid="optio-widget-loading">Loading…</div>;
  }

  const src = widgetData.iframeSrc ?? props.widgetProxyUrl;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <iframe
        data-testid="optio-widget-iframe"
        src={src}
        title={widgetData.title ?? props.process.name}
        sandbox={widgetData.sandbox}
        allow={widgetData.allow}
        style={{ width: '100%', height: '100%', border: 'none' }}
      />
      {isTerminal && !bannerDismissed && (
        <div
          data-testid="optio-widget-session-ended"
          style={{
            position: 'absolute', top: 0, left: 0, right: 0,
            padding: 8, background: '#fffbe6', borderBottom: '1px solid #ffe58f',
          }}
        >
          Session ended.
          <button onClick={() => setBannerDismissed(true)} style={{ marginLeft: 8 }}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

registerWidget('iframe', IframeWidget);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/IframeWidget.test.tsx`
Expected: all six PASS.

---

### Task 18: ProcessDetailView dispatcher

**Files:**
- Create: `packages/optio-ui/src/components/ProcessDetailView.tsx`
- Test: `packages/optio-ui/src/__tests__/ProcessDetailView.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-ui/src/__tests__/ProcessDetailView.test.tsx`:

```tsx
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import React from 'react';
import { registerWidget, _clearWidgetRegistry } from '../widgets/registry.js';

// Mock the self-fetching hook used by ProcessDetailView before import.
const mockProcessStream = vi.fn();
vi.mock('../hooks/useProcessStream.js', () => ({
  useProcessStream: (...args: any[]) => mockProcessStream(...args),
}));

vi.mock('../context/OptioProvider.js', () => ({
  useOptioContext: () => ({ prefix: 'optio', baseUrl: 'http://host' }),
}));

// Import after mocks are set up.
const { ProcessDetailView } = await import('../components/ProcessDetailView.js');

describe('ProcessDetailView', () => {
  beforeEach(() => {
    _clearWidgetRegistry();
    mockProcessStream.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows loading when tree is null', () => {
    mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('optio-detail-loading')).toBeTruthy();
  });

  it('renders default tree+log when uiWidget is absent', () => {
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null, children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
  });

  it('dispatches to a registered widget', () => {
    registerWidget('my-widget', (props) => (
      <div data-testid="my-widget">widget:{props.process._id}</div>
    ));
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('my-widget').textContent).toBe('widget:abc');
  });

  it('falls back to default when uiWidget is set but unregistered', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'no-such-widget',
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/ProcessDetailView.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `ProcessDetailView`**

Create `packages/optio-ui/src/components/ProcessDetailView.tsx`:

```tsx
import { useProcessStream } from '../hooks/useProcessStream.js';
import { useOptioContext } from '../context/OptioProvider.js';
import { getWidget } from '../widgets/registry.js';
import { ProcessTreeView } from './ProcessTreeView.js';
import { ProcessLogPanel } from './ProcessLogPanel.js';

export interface ProcessDetailViewProps {
  processId: string | null | undefined;
}

export function ProcessDetailView({ processId }: ProcessDetailViewProps) {
  const { tree, logs, connected } = useProcessStream(processId ?? undefined);
  const ctx = useOptioContext();

  if (!processId) {
    return (
      <div data-testid="optio-detail-empty" style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
        Select a process to view details
      </div>
    );
  }

  if (!tree) {
    return <div data-testid="optio-detail-loading">Loading…</div>;
  }

  const widgetName = (tree as any).uiWidget as string | undefined;
  if (widgetName) {
    const Widget = getWidget(widgetName);
    if (Widget) {
      const widgetProxyUrl = `${ctx.baseUrl}/api/widget/${tree._id}/`;
      return (
        <Widget
          process={tree as any}
          apiBaseUrl={ctx.baseUrl}
          widgetProxyUrl={widgetProxyUrl}
          prefix={ctx.prefix}
          database={(ctx as any).database}
        />
      );
    }
    console.warn(`[optio-ui] No widget registered under name "${widgetName}"; falling back to default rendering.`);
  }

  return (
    <div data-testid="optio-detail-default">
      <ProcessTreeView treeData={tree} sseState={{ connected }} />
      <ProcessLogPanel logs={logs} />
    </div>
  );
}
```

Note: the `useOptioContext` export name may differ — look at `packages/optio-ui/src/context/OptioProvider.tsx` to see the real exported hook that provides `{ prefix, baseUrl, database }`, and use that instead. Adjust the mock in the test accordingly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run src/__tests__/ProcessDetailView.test.tsx`
Expected: all four PASS.

---

### Task 19: Export widget API and ensure built-in iframe widget is registered on package import

**Files:**
- Modify: `packages/optio-ui/src/index.ts`

- [ ] **Step 1: Add exports**

Append to `packages/optio-ui/src/index.ts`:

```typescript
// Widgets
export { registerWidget } from './widgets/registry.js';
export type { WidgetProps, WidgetComponent } from './widgets/registry.js';
export { IframeWidget } from './widgets/IframeWidget.js';

// Components
export { ProcessDetailView } from './components/ProcessDetailView.js';
export type { ProcessDetailViewProps } from './components/ProcessDetailView.js';
```

Importing `IframeWidget` has the side effect of calling `registerWidget('iframe', ...)` during module load. Consumers who import anything from optio-ui get the built-in iframe widget registered.

- [ ] **Step 2: Verify build**

Run: `cd packages/optio-ui && node_modules/.bin/tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Run full optio-ui test suite**

Run: `cd packages/optio-ui && node_modules/.bin/vitest run`
Expected: all tests PASS.

---

### Task 20: Wire `ProcessDetailView` into optio-dashboard

**Files:**
- Modify: `packages/optio-dashboard/src/app/App.tsx:22-65`

- [ ] **Step 1: Swap the inline render for `ProcessDetailView`**

In `packages/optio-dashboard/src/app/App.tsx`, modify the `Dashboard` component:

1. Remove `useProcessStream` import/usage (ProcessDetailView handles it).
2. Remove the inline `<ProcessTreeView /> + <ProcessLogPanel />` render.
3. Import `ProcessDetailView` from optio-ui.
4. Replace the `<Content>` body with `<ProcessDetailView processId={selectedProcessId} />`.

Updated `Dashboard` component:

```tsx
function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { launch, cancel } = useProcessActions();
  const live = useOptioLive();

  return (
    <WithFilteredProcesses>
      <Layout>
        <Layout>
          <Sider width={400} style={{ background: '#fff', overflow: 'auto' }}>
            <ProcessFilters />
            <FilteredProcessList
              processes={processes}
              loading={!listConnected}
              onLaunch={live ? launch : undefined}
              onCancel={live ? cancel : undefined}
              onProcessClick={setSelectedProcessId}
            />
          </Sider>
          <Content style={{ padding: '24px', overflow: 'auto' }}>
            <ProcessDetailView processId={selectedProcessId} />
          </Content>
        </Layout>
      </Layout>
    </WithFilteredProcesses>
  );
}
```

Update imports at the top of the file: add `ProcessDetailView` to the `optio-ui` import, remove `ProcessTreeView`, `ProcessLogPanel`, and `useProcessStream` if they are no longer used elsewhere in this file.

- [ ] **Step 2: Verify TypeScript build**

Run: `cd packages/optio-dashboard && node_modules/.bin/tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual smoke test**

Bring up the dashboard (per your usual dev workflow — `pnpm dev` or the equivalent) and verify:
- Selecting a non-widget process still shows the tree + log (default rendering).
- Nothing appears broken relative to before.

Automated coverage for this integration is thin; the dashboard has no prior test suite. Manual verification is the gate.

---

### Task 21: Marimo reference task in optio-demo

**Files:**
- Create: `packages/optio-demo/src/marimo_task.py`
- Create: `packages/optio-demo/src/notebooks/sample.py`
- Modify: `packages/optio-demo/src/main.py` (or equivalent task-registration entry point — check existing structure)
- Modify: `packages/optio-demo/pyproject.toml` (add `marimo` dependency)

- [ ] **Step 1: Inspect optio-demo's current structure**

Run: `ls packages/optio-demo/src && cat packages/optio-demo/pyproject.toml`
Identify where `get_task_definitions` is defined today.

- [ ] **Step 2: Add marimo dependency**

In `packages/optio-demo/pyproject.toml`, add `marimo` to the dependency list. Version: `marimo>=0.9`.

- [ ] **Step 3: Create a sample notebook**

Create `packages/optio-demo/src/notebooks/sample.py`:

```python
import marimo

__generated_with = "0.9.0"
app = marimo.App()


@app.cell
def __():
    import marimo as mo
    return (mo,)


@app.cell
def __(mo):
    mo.md("# Optio widget demo\n\nHello from marimo.")
    return


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 4: Create the marimo task**

Create `packages/optio-demo/src/marimo_task.py`:

```python
"""Marimo reference task for optio widget extensions."""
import asyncio
import socket
from pathlib import Path

from optio_core.context import ProcessContext


NOTEBOOK = Path(__file__).parent / "notebooks" / "sample.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_for_listening(port: int, timeout_s: float = 10.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.1)
    return False


async def run_marimo(ctx: ProcessContext) -> None:
    port = _free_port()
    ctx.report_progress(None, f"Starting marimo on 127.0.0.1:{port}")

    proc = await asyncio.create_subprocess_exec(
        "marimo", "edit", str(NOTEBOOK),
        "--host", "127.0.0.1",
        "--port", str(port),
        "--headless",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        ready = await _wait_for_listening(port)
        if not ready:
            raise RuntimeError(f"marimo did not listen on port {port} within timeout")

        await ctx.set_widget_upstream(f"http://127.0.0.1:{port}")
        # Empty widgetData is enough: its presence is the "go-live" signal.
        await ctx.set_widget_data({})
        ctx.report_progress(None, "marimo is live; widget mounted")

        # Monitor the subprocess while allowing cancellation.
        while ctx.should_continue():
            if proc.returncode is not None:
                raise RuntimeError(f"marimo exited with code {proc.returncode}")
            await asyncio.sleep(0.5)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
```

- [ ] **Step 5: Register the task in `get_task_definitions`**

Modify `packages/optio-demo/src/main.py` (or the file that defines `get_task_definitions`) to include a `TaskInstance` for the marimo task:

```python
from optio_core.models import TaskInstance
from optio_demo.marimo_task import run_marimo

# inside get_task_definitions(services):
tasks.append(
    TaskInstance(
        execute=run_marimo,
        process_id="marimo-notebook",
        name="Marimo Notebook",
        description="Live marimo notebook embedded via the widget proxy",
        ui_widget="iframe",
    )
)
```

Exact integration depends on the existing file's structure.

- [ ] **Step 6: Update the demo README**

In `packages/optio-demo/README.md`, add a section describing the user-verifiable smoke test:

```markdown
## Widget smoke test

1. `docker compose up` to bring up Mongo + Redis.
2. `pnpm --filter optio-dashboard dev` in one terminal.
3. `make run` (or the project's usual demo-run command) in another to start optio-demo.
4. Authenticate in the dashboard. Select the "Marimo Notebook" task and launch it.
5. Open the process — the iframe widget should mount and show a live marimo notebook.
6. Interact with the notebook. Reactive updates over WS should flow through the proxy.
7. Cancel the process. The "session ended" banner appears; the subprocess is terminated.
8. Dismiss the process. The widget unmounts; `widgetUpstream` and `widgetData` are cleared in the DB.
```

- [ ] **Step 7: Install new deps**

Run from repo root (or from the demo package): `pip install -e packages/optio-demo` (or however optio-demo is installed locally). Confirm `marimo` is installed.

- [ ] **Step 8: Manual smoke test**

Follow the README steps above and confirm the happy path works end-to-end. This is the acceptance gate for this task.

---

### Task 22: Optional Playwright smoke test

**Status:** optional; deliver if time permits. Acceptance for the plan is the manual smoke test in Task 21.

**Files (if done):**
- Create: `packages/optio-demo/tests/e2e/marimo-widget.spec.ts`
- Modify: `packages/optio-demo/package.json` (add playwright + test script)

- [ ] **Step 1 (optional):** Skip or implement per available time.

If skipped, note in the PR description that automated E2E coverage for this feature is deferred; manual smoke test documented in the demo README is the current gate.

---

### Task 23: Root AGENTS.md and per-package AGENTS.md updates

Per project convention: when changing public API, update the relevant AGENTS.md files in the same commit.

**Files:**
- Modify: `packages/optio-core/AGENTS.md`
- Modify: `packages/optio-api/AGENTS.md`
- Modify: `packages/optio-ui/AGENTS.md`
- Modify: `AGENTS.md` (root)

- [ ] **Step 1: Update optio-core AGENTS.md**

Add (or extend existing sections for):
- `TaskInstance.ui_widget: str | None` field.
- `ProcessContext.set_widget_upstream(url, inner_auth=None)`, `clear_widget_upstream()`, `set_widget_data(data)`, `clear_widget_data()`.
- `InnerAuth` dataclasses (`BasicAuth`, `QueryAuth`, `HeaderAuth`).
- MongoDB document schema: `uiWidget`, `widgetUpstream`, `widgetData` fields with shapes.

- [ ] **Step 2: Update optio-api AGENTS.md**

Add:
- `registerWidgetProxy(app, opts)` export from `optio-api/fastify`.
- `OptioWidgetProxyOptions` shape.
- `widgetData` now in tree-stream `update` event payload (but not in list-stream).
- `widgetUpstream` is explicitly never in any client-facing payload.

- [ ] **Step 3: Update optio-ui AGENTS.md**

Add:
- `registerWidget(name, component)`, `WidgetProps` type.
- `ProcessDetailView` component.
- Built-in `'iframe'` widget registered on package import.

- [ ] **Step 4: Update root AGENTS.md**

Mirror the changes into the unified reference; ensure consistency.

---

### Task 24: Squash to a single commit

Per project convention ("one commit per execution plan"), all work done while executing this plan ends up as a single commit.

- [ ] **Step 1: Check status**

Run: `git status`
Expected: all changes staged or committed as work-in-progress on a feature branch.

- [ ] **Step 2: Squash work-in-progress commits (if any)**

Options:
- If you committed each task as a WIP commit: `git reset --soft <base-commit>` to collapse them.
- If you worked in one session and haven't committed yet: `git add -A`.

Either way, end with all the feature's file changes staged and no prior commits on the branch since the base.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: implement optio widget extensions

Implements the four primitives defined in
docs/2026-04-21-optio-widget-extensions-design.md:
- Widget registry (uiWidget on TaskInstance; client-side registerWidget)
- Widget upstream proxy (Fastify, via @fastify/http-proxy)
- Widget data (live JSON blob on process document)
- Generic iframe widget (ships in optio-ui as 'iframe')

Also ships a marimo reference task in optio-demo that exercises all four
primitives end-to-end, and updates AGENTS.md across affected packages."
```

No Co-Authored-By line (per project convention).

- [ ] **Step 4: Run the full test suite one last time**

```bash
cd packages/optio-core && pytest -v
cd packages/optio-contracts && node_modules/.bin/vitest run
cd packages/optio-api && node_modules/.bin/vitest run
cd packages/optio-ui && node_modules/.bin/vitest run
```

Expected: every package's tests pass.

---

## Open follow-ups (not part of this plan)

- Deep-linkable per-process URLs in optio-dashboard (requires a router). Called out in the spec's "out of scope / future work".
- Express, nextjs-pages, nextjs-app adapters for the widget proxy.
- App-to-app `postMessage` between parent dashboard and iframe.
- Playwright E2E (if deferred from Task 22).
- Tree-poller-driven cache invalidation (currently handled by a 5s TTL; see Task 14's decision note).
