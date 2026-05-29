# Client-Directed Events (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a running optio task drive the operator's client through three new capabilities — `browser_open(url)`, `need_attention(reason)`, and `domain_message(keyword, data)` — folded into the process status document and delivered over SSE, with an opaque client-minted `sessionId` for initiator-scoped routing.

**Architecture:** Approach B — fold each capability's payload into the process doc (`browserOpenRequests`, `sessionEvents`) and ride existing/new SSE feeds; the engine owns all DB writes (optio-api stays read-only). `browser_open` is view-scoped (delivered on the three existing per-process feeds, deduped by `requestId`); attention + domain are session-scoped (delivered on a new always-on `/api/session-events/stream` keyed by `sessionId` matched against the process's `originatingSessionId`). Three new agent-emittable optio.log keywords (`BROWSER:`/`ATTENTION:`/`DOMAIN_MESSAGE:`) are parsed in optio-agents.

**Tech Stack:** Python (optio-core/optio-agents/optio-demo, motor/MongoDB, pytest), TypeScript (optio-contracts/optio-api/optio-ui/optio-dashboard, Zod, ts-rest, clamator, fastify, React, antd, vitest).

---

## PARALLEL-EXECUTION MODEL — READ FIRST

This plan is **parallel-shaped**, which overrides writing-plans' default per-task TDD/commit cadence. The execution model is **ONE concurrent wave (Tasks 1–9) + a final verify-and-commit (Task 10)**:

- **Tasks 1–9 each own a DISJOINT set of files.** No two tasks edit the same file. They can be executed concurrently (e.g. one subagent per task) with no ordering constraint between them.
- **Tasks 1–9 contain ONLY file edits** (real code, bite-sized). They author test files but **DO NOT run any tests, do NOT run `make codegen`, and do NOT `git add`/`git commit`.** A task is "done" when its files are written, not when anything passes.
- **Task 10 is the ONLY task that runs anything.** It runs `make codegen` (regenerates the clamator stubs from the T1 contract change), `make install`, every pytest suite, every TS build/tsc, `make lint-no-direct-writes`, the grep checks, and then makes the git commit(s). No `Co-Authored-By` trailer (per repo memory).

**Why this shape:** the contract change (T1) drives codegen, which T2 (Python `LaunchParams`) and T5/T7 (TS `engine.launch`) depend on at *compile/test* time — so codegen + all verification must happen *after* the whole wave lands, in T10. Authoring against the post-codegen shapes is safe because every task writes the code that the regenerated stubs will satisfy.

**Naming note (grounded in real code):** the spec's pinned SSOT constant name `LOG_CHANNEL_PROTOCOL` does **not** exist; the real constant in `optio-agents/.../protocol/prompt.py` is **`LOG_CHANNEL_PROMPT`**. This plan uses the real name `LOG_CHANNEL_PROMPT` (grounding in actual source overrides the approximate pinned name). All other pinned names match the codebase and are used verbatim.

**File ownership map (sole owner per file):**

| Task | Owns (creates/modifies) |
|---|---|
| T1 | `packages/optio-contracts/src/schemas/process.ts`, `.../src/optio-engine-to-api.ts`, `.../src/api-to-frontend.ts`, `.../src/index.ts`, `.../src/schemas/session-events.ts` (new), `.../src/__tests__/session-events-schema.test.ts` (new), `.../src/__tests__/process-schema.test.ts` |
| T2 | `packages/optio-core/src/optio_core/{context.py,store.py,lifecycle.py,executor.py,_engine_service.py}`, new `optio-core/tests/test_client_directed_events.py`, and the existing `optio-core/tests/*` launch-call-site updates |
| T3 | `packages/optio-agents/src/optio_agents/protocol/{parser.py,session.py,prompt.py,__init__.py}`, `optio-agents/tests/test_protocol_parser.py`, `optio-agents/tests/test_prompt.py`, new `optio-agents/tests/test_client_directed_dispatch.py` |
| T4 | `packages/optio-agents/src/optio_agents/browser_capture.py` (new), `.../src/optio_agents/__init__.py`, new `optio-agents/tests/test_browser_capture.py` |
| T5 | `packages/optio-api/src/stream-poller.ts`, `.../src/adapters/fastify.ts`, `.../src/handlers.ts`, `.../src/__tests__/stream-poller.test.ts`, new `.../src/__tests__/session-events-poller.test.ts` |
| T6 | `packages/optio-ui/src/handlers/browserOpen.ts` (new), `.../src/hooks/useProcessListStream.tsx`, `.../src/context/MultiProcessStreamContext.tsx`, `.../src/hooks/useProcessStream.ts`, new `.../src/__tests__/browserOpen.test.tsx` |
| T7 | `packages/optio-ui/src/session/sessionEvents.ts` (new), `.../src/context/OptioProvider.tsx`, `.../src/hooks/useProcessActions.ts`, `.../src/index.ts`, new `.../src/__tests__/sessionEvents.test.tsx` |
| T8 | `packages/optio-demo/src/optio_demo/tasks/client_directed.py` (new), `.../tasks/__init__.py` |
| T9 | `packages/optio-dashboard/src/app/App.tsx` |
| T10 | (runs codegen/install/tests/lint/grep + commits; touches the auto-generated `_generated/` dirs only via `make codegen`) |

Disjointness check: T3 owns `protocol/__init__.py` (re-exports the new parser events); T4 owns the top-level `optio_agents/__init__.py` (exports `browser_capture`). T6 and T7 both touch optio-ui but never the same file (T6: browserOpen handler + the 3 feed files; T7: sessionEvents manager + OptioProvider + useProcessActions + index.ts). `useProcessActions.launch` reads `sessionId` from T7's module-level `sessionEvents.ts` via import, not via a file T6 owns.

---

### Task 1: optio-contracts — schema fields + launch sessionId + session-events contract

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts`
- Modify: `packages/optio-contracts/src/optio-engine-to-api.ts`
- Modify: `packages/optio-contracts/src/api-to-frontend.ts`
- Modify: `packages/optio-contracts/src/index.ts`
- Create: `packages/optio-contracts/src/schemas/session-events.ts`
- Create: `packages/optio-contracts/src/__tests__/session-events-schema.test.ts`
- Modify: `packages/optio-contracts/src/__tests__/process-schema.test.ts`

Do **NOT** hand-edit `packages/optio-api/src/_generated/` — Task 10 regenerates it via `make codegen`.

- [ ] **Step 1: Add `browserOpenRequests` + `sessionEvents` to `ProcessSchema`.**

In `packages/optio-contracts/src/schemas/process.ts`, insert the two new schemas just above `export const ProcessSchema = z.object({` (after the `LogEntrySchema` block, line 29):

```typescript
export const BrowserOpenRequestSchema = z.object({
  requestId: z.string(),
  url: z.string(),
});

export const SessionEventSchema = z.discriminatedUnion('type', [
  z.object({ requestId: z.string(), type: z.literal('attention'), reason: z.string() }),
  z.object({ requestId: z.string(), type: z.literal('domain'), keyword: z.string(), data: z.unknown() }),
]);
```

Then, inside `ProcessSchema`, add these two fields immediately after the `hasSavedState: z.boolean().optional(),` line (line 62), before `createdAt`:

```typescript
  // Client-directed events (phase 2). Append-only; never GC'd.
  browserOpenRequests: z.array(BrowserOpenRequestSchema).optional(),
  sessionEvents: z.array(SessionEventSchema).optional(),
  originatingSessionId: z.string().nullable().optional(),
```

- [ ] **Step 2: Export the new field schemas + types from `process.ts`.**

At the bottom of `packages/optio-contracts/src/schemas/process.ts`, add after `export type LogEntry = ...` (line 84):

```typescript
export type BrowserOpenRequest = z.infer<typeof BrowserOpenRequestSchema>;
export type SessionEvent = z.infer<typeof SessionEventSchema>;
```

- [ ] **Step 3: Add required nullable `sessionId` to the engine `launch` RPC params.**

In `packages/optio-contracts/src/optio-engine-to-api.ts`, change the `launch` method params (lines 49–55) from:

```typescript
  launch: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      resume: z.boolean().optional(),
    }),
    result: launchResult,
  }),
```

to:

```typescript
  launch: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      resume: z.boolean().optional(),
      // Required (no `.optional()`) but nullable: every caller must
      // consciously supply the initiating session token, or explicit null
      // for unattended launches.
      sessionId: z.string().nullable(),
    }),
    result: launchResult,
  }),
```

- [ ] **Step 4: Add `sessionId` to the UI→API `launch` body.**

In `packages/optio-contracts/src/api-to-frontend.ts`, change the `launch` route body (line 112) from:

```typescript
    body: z.object({ resume: z.boolean().optional() }).optional(),
```

to:

```typescript
    body: z.object({
      resume: z.boolean().optional(),
      sessionId: z.string().nullable().optional(),
    }).optional(),
```

(Optional+nullable here: the UI always sends it, but other adapters may omit it; the handler defaults to `null`.)

- [ ] **Step 5: Create the session-events SSE response contract.**

Create `packages/optio-contracts/src/schemas/session-events.ts`:

```typescript
import { z } from 'zod';
import { SessionEventSchema } from './process.js';

/**
 * Wire shape of one message on the GET /api/session-events/stream SSE feed.
 * The poller emits one `session-events` message per tick carrying the new
 * sessionEvents of every process whose originatingSessionId matches the
 * subscriber's sessionId. `processId` is the process _id hex (string).
 */
export const SessionEventsStreamMessageSchema = z.object({
  type: z.literal('session-events'),
  processId: z.string(),
  events: z.array(SessionEventSchema),
});

export type SessionEventsStreamMessage = z.infer<typeof SessionEventsStreamMessageSchema>;
```

- [ ] **Step 6: Re-export the new schemas/types from the package index.**

In `packages/optio-contracts/src/index.ts`, extend the schema export line (lines 4–5) so the full block reads:

```typescript
export { ProcessSchema, ProcessStateSchema, LogEntrySchema,
         ProcessMetadataFilterSchema, MetadataFilterQueryParamSchema,
         BrowserOpenRequestSchema, SessionEventSchema } from './schemas/process.js';
export { SessionEventsStreamMessageSchema } from './schemas/session-events.js';
```

and extend the types export line (line 8) to:

```typescript
export type { Process, ProcessState, LogEntry, ProcessMetadataFilter,
              BrowserOpenRequest, SessionEvent } from './schemas/process.js';
export type { SessionEventsStreamMessage } from './schemas/session-events.js';
```

- [ ] **Step 7: Author the session-events schema test.**

Create `packages/optio-contracts/src/__tests__/session-events-schema.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { SessionEventSchema, BrowserOpenRequestSchema } from '../schemas/process.js';
import { SessionEventsStreamMessageSchema } from '../schemas/session-events.js';

describe('BrowserOpenRequestSchema', () => {
  it('accepts a requestId + url', () => {
    const parsed = BrowserOpenRequestSchema.parse({ requestId: 'abc', url: 'https://x' });
    expect(parsed.url).toBe('https://x');
  });
});

describe('SessionEventSchema discriminated union', () => {
  it('accepts an attention event', () => {
    const parsed = SessionEventSchema.parse({ requestId: 'r1', type: 'attention', reason: 'need help' });
    expect(parsed.type).toBe('attention');
  });

  it('accepts a domain event with arbitrary data', () => {
    const parsed = SessionEventSchema.parse({ requestId: 'r2', type: 'domain', keyword: 'k', data: { a: [1] } });
    expect(parsed.type).toBe('domain');
    if (parsed.type === 'domain') expect(parsed.data).toEqual({ a: [1] });
  });

  it('rejects an unknown type', () => {
    expect(SessionEventSchema.safeParse({ requestId: 'r', type: 'other' }).success).toBe(false);
  });
});

describe('SessionEventsStreamMessageSchema', () => {
  it('accepts a session-events message', () => {
    const parsed = SessionEventsStreamMessageSchema.parse({
      type: 'session-events',
      processId: '507f1f77bcf86cd799439011',
      events: [{ requestId: 'r1', type: 'attention', reason: 'x' }],
    });
    expect(parsed.events).toHaveLength(1);
  });
});
```

- [ ] **Step 8: Extend the existing ProcessSchema test for the new fields.**

In `packages/optio-contracts/src/__tests__/process-schema.test.ts`, add these `it` blocks inside the `describe('ProcessSchema widget fields', ...)` block (after the existing `it('accepts a process without widget fields', ...)` at line 35):

```typescript
  it('accepts browserOpenRequests', () => {
    const parsed = ProcessSchema.parse({
      ...baseProcess(),
      browserOpenRequests: [{ requestId: 'r1', url: 'https://example.com' }],
    });
    expect(parsed.browserOpenRequests).toEqual([{ requestId: 'r1', url: 'https://example.com' }]);
  });

  it('accepts sessionEvents (attention + domain)', () => {
    const parsed = ProcessSchema.parse({
      ...baseProcess(),
      sessionEvents: [
        { requestId: 'r1', type: 'attention', reason: 'help' },
        { requestId: 'r2', type: 'domain', keyword: 'k', data: { n: 1 } },
      ],
    });
    expect(parsed.sessionEvents).toHaveLength(2);
  });

  it('accepts originatingSessionId string and null', () => {
    expect(ProcessSchema.parse({ ...baseProcess(), originatingSessionId: 'tok' }).originatingSessionId).toBe('tok');
    expect(ProcessSchema.parse({ ...baseProcess(), originatingSessionId: null }).originatingSessionId).toBeNull();
  });
```

---

### Task 2: optio-core — ctx methods, store helpers, session_id threading

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py`
- Modify: `packages/optio-core/src/optio_core/store.py`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/executor.py`
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`
- Create: `packages/optio-core/tests/test_client_directed_events.py`
- Modify (launch-call-site updates): `packages/optio-core/tests/test_engine_service.py`, `test_engine_service_resolve.py`, `test_no_redis.py`, `test_executor.py`, `test_parallel.py`, `test_deadline_cancel.py`, `test_deadline_cancel_launchguard.py`, `test_task_ttl.py`, `test_child_failure_structured.py`, `test_child_failure_cancel_distinction.py`, `test_cancel_propagation.py`, `test_cancel_race_parent_overwrite.py`, `test_group_cancel.py`, `test_resync_cancel_stale.py`, `test_launch_guard.py`, `test_persistent_launch_blocks.py`, `test_outcomes.py`, `test_lifecycle_reconciliation.py`, `test_widget_primitives.py`, `test_child_progress.py`

- [ ] **Step 1: Add the two store helpers.**

In `packages/optio-core/src/optio_core/store.py`, add `from uuid import uuid4` to the imports (it currently imports `re as _re`, `datetime…`, `Any`, `ObjectId`, `AsyncIOMotorDatabase`, models). At the end of the file (after `clear_widget_data`, line 433), append:

```python
async def append_browser_open_request(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, url: str,
) -> str:
    """$push a {requestId, url} record onto browserOpenRequests; return requestId."""
    request_id = uuid4().hex
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$push": {"browserOpenRequests": {"requestId": request_id, "url": url}}},
    )
    return request_id


async def append_session_event(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, event: dict,
) -> str:
    """$push a session event onto sessionEvents; return its requestId.

    `event` is one of:
      {"type": "attention", "reason": <str>}
      {"type": "domain", "keyword": <str>, "data": <json>}
    A fresh requestId is minted and merged into the stored record.
    """
    request_id = uuid4().hex
    record = {"requestId": request_id, **event}
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$push": {"sessionEvents": record}},
    )
    return request_id
```

- [ ] **Step 2: Add the three `ProcessContext` methods + `session_id` storage.**

In `packages/optio-core/src/optio_core/context.py`, add a `session_id` parameter to `ProcessContext.__init__`. Change the signature (lines 56–70) by inserting `session_id: str | None = None,` after `resume: bool = False,`:

```python
        metadata: dict[str, Any] | None = None,
        resume: bool = False,
        session_id: str | None = None,
    ):
```

and store it next to the other simple attributes — add after `self.resume = resume` (line 75):

```python
        self.session_id = session_id
```

Then add the three public methods immediately after `clear_widget_data` (line 254, just before `mark_has_saved_state`):

```python
    async def request_browser_open(self, url: str) -> str:
        """Ask the operator's client to open `url`. View-scoped: delivered
        to any observer of this process. Returns the requestId."""
        from optio_core.store import append_browser_open_request
        return await append_browser_open_request(
            self._db, self._prefix, self._process_oid, url,
        )

    async def need_attention(self, reason: str) -> str:
        """Ask the launching browser session for human attention.
        Session-scoped. Returns the requestId."""
        from optio_core.store import append_session_event
        return await append_session_event(
            self._db, self._prefix, self._process_oid,
            {"type": "attention", "reason": reason},
        )

    async def domain_message(self, keyword: str, data) -> str:
        """Push an application-defined message to the launching browser
        session's frontend. Session-scoped. `data` must be JSON-serializable;
        optio does not interpret it. Returns the requestId."""
        from optio_core.store import append_session_event
        return await append_session_event(
            self._db, self._prefix, self._process_oid,
            {"type": "domain", "keyword": keyword, "data": data},
        )
```

- [ ] **Step 3: Write `originatingSessionId` + propagate `session_id` in the executor.**

In `packages/optio-core/src/optio_core/executor.py`, change `launch_process` (lines 104–131) to require `session_id`:

```python
    async def launch_process(
        self, process_id: str, resume: bool = False, *, session_id: str | None,
    ) -> str | None:
```

and pass it into `_execute_process` at the call near line 128:

```python
        state, _ = await self._execute_process(
            proc, task.execute if task else None, resume=resume,
            session_id=session_id,
        )
```

Change `_execute_process` (lines 133–137) to accept it:

```python
    async def _execute_process(
        self, proc: dict, execute_fn: Callable | None,
        parent_ctx: ProcessContext | None = None,
        resume: bool = False,
        *, session_id: str | None = None,
    ) -> tuple[str, BaseException | None]:
```

Inside `_execute_process`, write `originatingSessionId` onto the doc right after the running-state write (after `await append_log(self._db, self._prefix, oid, "event", "State changed to running")`, line 160). Children inherit from the parent context:

```python
            effective_session_id = (
                parent_ctx.session_id if parent_ctx is not None else session_id
            )
            await _collection(self._db, self._prefix).update_one(
                {"_id": oid},
                {"$set": {"originatingSessionId": effective_session_id}},
            )
```

Add `_collection` to the existing store import at the top of `executor.py` (it already imports several names from `optio_core.store`; add `_collection` to that import list). Then thread `session_id` into the `ProcessContext(...)` construction (lines 162–175) by adding `session_id=effective_session_id,` after `resume=resume,`:

```python
                resume=resume,
                session_id=effective_session_id,
            )
```

`execute_child` already forwards `parent_ctx=parent_ctx` to `_execute_process` (line 342), so children inherit `session_id` automatically through `effective_session_id`. No change to `execute_child` is needed.

- [ ] **Step 4: Thread `session_id` through `lifecycle.Optio.launch`.**

In `packages/optio-core/src/optio_core/lifecycle.py`, change `launch` (line 371) to require `session_id`:

```python
    async def launch(
        self, process_id: str, resume: bool = False, *, session_id: str | None,
    ) -> LaunchOutcome:
```

and pass it to the executor at the `asyncio.create_task` call (lines 391–393):

```python
        asyncio.create_task(
            self._executor.launch_process(oid_str, resume=resume, session_id=session_id),
        )
```

Update `launch_and_wait` (line 401) likewise (required `session_id`):

```python
    async def launch_and_wait(
        self, process_id: str, resume: bool = False, *, session_id: str | None,
    ) -> None:
```

and pass it through at line 412:

```python
        await self._executor.launch_process(process_id, resume=resume, session_id=session_id)
```

Update the scheduler/cron call site `_scheduler_launch_adapter` (line 1134) to pass explicit `None` (unattended):

```python
        outcome = await self.launch(process_id, session_id=None)
```

- [ ] **Step 5: Pass `session_id` from `_engine_service.launch`.**

In `packages/optio-core/src/optio_core/_engine_service.py`, change the `launch` body (lines 77–79) to forward the param:

```python
        outcome = await self._optio.launch(
            params.process_id, resume=bool(params.resume),
            session_id=params.session_id,
        )
```

(After Task 10 codegen, `LaunchParams` has `session_id: str | None = Field(..., alias="sessionId")`, so `params.session_id` exists.)

Also add `browserOpenRequests` and `sessionEvents` to the client-bound wire keys so the launch RPC result carries them. In `_PROCESS_WIRE_KEYS` (lines 29–34), add the two names to the frozenset:

```python
_PROCESS_WIRE_KEYS = frozenset({
    "_id", "processId", "name", "params", "metadata", "parentId", "rootId",
    "depth", "order", "cancellable", "special", "warning", "description",
    "status", "progress", "log", "uiWidget", "widgetData", "supportsResume",
    "hasSavedState", "createdAt", "browserOpenRequests", "sessionEvents",
})
```

(`originatingSessionId` is server-side routing only — intentionally NOT added to the wire keys.)

- [ ] **Step 6: Author the new optio-core test file.**

Create `packages/optio-core/tests/test_client_directed_events.py`:

```python
import asyncio
import pytest
from optio_core.models import TaskInstance
from optio_core.store import (
    upsert_process,
    append_browser_open_request,
    append_session_event,
)
from optio_core.context import ProcessContext
from optio_core.executor import Executor


def _ctx(db, proc) -> ProcessContext:
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={},
        metadata={},
    )


@pytest.mark.asyncio
async def test_append_browser_open_request(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="b1", name="B1"))
    rid = await append_browser_open_request(mongo_db, "test", proc["_id"], "https://x")
    assert isinstance(rid, str) and len(rid) == 32
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["browserOpenRequests"] == [{"requestId": rid, "url": "https://x"}]


@pytest.mark.asyncio
async def test_append_session_event_attention_and_domain(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="s1", name="S1"))
    r1 = await append_session_event(mongo_db, "test", proc["_id"], {"type": "attention", "reason": "help"})
    r2 = await append_session_event(mongo_db, "test", proc["_id"], {"type": "domain", "keyword": "k", "data": {"n": 1}})
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["sessionEvents"] == [
        {"requestId": r1, "type": "attention", "reason": "help"},
        {"requestId": r2, "type": "domain", "keyword": "k", "data": {"n": 1}},
    ]


@pytest.mark.asyncio
async def test_ctx_request_browser_open(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="c1", name="C1"))
    ctx = _ctx(mongo_db, proc)
    rid = await ctx.request_browser_open("https://repo")
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["browserOpenRequests"][0] == {"requestId": rid, "url": "https://repo"}


@pytest.mark.asyncio
async def test_ctx_need_attention_and_domain_message(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="c2", name="C2"))
    ctx = _ctx(mongo_db, proc)
    ra = await ctx.need_attention("look here")
    rd = await ctx.domain_message("alert", {"level": "high"})
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["sessionEvents"] == [
        {"requestId": ra, "type": "attention", "reason": "look here"},
        {"requestId": rd, "type": "domain", "keyword": "alert", "data": {"level": "high"}},
    ]


@pytest.mark.asyncio
async def test_launch_writes_originating_session_id(mongo_db):
    async def noop(ctx):
        pass
    task = TaskInstance(execute=noop, process_id="o1", name="O1")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    state = await executor.launch_process("o1", session_id="tok-123")
    assert state == "done"
    doc = await mongo_db["test_processes"].find_one({"processId": "o1"})
    assert doc["originatingSessionId"] == "tok-123"


@pytest.mark.asyncio
async def test_launch_none_session_id_writes_null(mongo_db):
    async def noop(ctx):
        pass
    task = TaskInstance(execute=noop, process_id="o2", name="O2")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("o2", session_id=None)
    doc = await mongo_db["test_processes"].find_one({"processId": "o2"})
    assert doc["originatingSessionId"] is None


@pytest.mark.asyncio
async def test_child_inherits_parent_session_id(mongo_db):
    async def child(ctx):
        pass
    async def parent(ctx):
        await ctx.run_child(execute=child, process_id="kid", name="Kid")
    task = TaskInstance(execute=parent, process_id="root-si", name="RootSI")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("root-si", session_id="parent-tok")
    child_doc = await mongo_db["test_processes"].find_one({"processId": "kid"})
    assert child_doc["originatingSessionId"] == "parent-tok"
```

- [ ] **Step 7: Update every existing launch call site in the optio-core test suite.**

Mechanical update across the test files listed above. Three forms:

  1. Executor tests call `executor.launch_process("x")` → add `, session_id=None`: e.g. `await executor.launch_process("basic")` becomes `await executor.launch_process("basic", session_id=None)`.
  2. Lifecycle/Optio tests call `optio.launch("x")` / `fw.launch("x")` / `optio.launch_and_wait("x")` / `fw.launch_and_wait("x")` → add `, session_id=None`: e.g. `await fw.launch_and_wait("test_task")` becomes `await fw.launch_and_wait("test_task", session_id=None)`.
  3. Engine-service tests build `LaunchParams.model_validate({"processId": ...})` → add `"sessionId": None` to the dict, and update the `assert_awaited_once_with(...)` to expect the new kwarg.

Concretely for `test_engine_service.py`, change e.g. line 110/113:

```python
    result = await svc.launch(LaunchParams.model_validate({"processId": hex_id, "sessionId": None}))
    ...
    fake_optio.launch.assert_awaited_once_with(hex_id, resume=False, session_id=None)
```

and line 126/131:

```python
    result = await svc.launch(LaunchParams.model_validate({"processId": "p1", "sessionId": None}))
    ...
    fake_optio.launch.assert_awaited_once_with("p1", resume=False, session_id=None)
```

and lines 139/149/159/169/363/364 — add `"sessionId": None` to each `model_validate({...})` dict (the failure-path tests at 139/149/169 do not assert call args, so only the dict changes; the resume test at 159 adds `"sessionId": None` alongside `"resume": True`). For `test_engine_service_resolve.py` lines 233/249, add `"sessionId": None` to each dict.

For every other file in the list, apply form 1 or 2 to each `launch_process(` / `.launch(` / `launch_and_wait(` call. A subagent should grep `launch_process(|\.launch(|launch_and_wait(` per file and add `session_id=None` to each. Spot-check examples:

- `test_executor.py:22` → `await executor.launch_process("basic", session_id=None)`
- `test_no_redis.py:85` → `await fw.launch("slow_task", session_id=None)`
- `test_deadline_cancel_launchguard.py:75` → `await optio.launch("p.parent", session_id=None)`
- `test_widget_primitives.py:204` → `result = await executor.launch_process("t-done", session_id=None)`
- `test_cancel_race_parent_overwrite.py:70` → `asyncio.create_task(optio.launch_and_wait("race-parent", session_id=None))`

(Use `session_id=None` for all existing call sites — they predate sessions and are explicitly "unattended/dummy" per the spec inventory.)

---

### Task 3: optio-agents protocol — three events, regexes, dispatch, SSOT docs

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/protocol/parser.py`
- Modify: `packages/optio-agents/src/optio_agents/protocol/session.py`
- Modify: `packages/optio-agents/src/optio_agents/protocol/prompt.py`
- Modify: `packages/optio-agents/src/optio_agents/protocol/__init__.py`
- Modify: `packages/optio-agents/tests/test_protocol_parser.py`
- Modify: `packages/optio-agents/tests/test_prompt.py`
- Create: `packages/optio-agents/tests/test_client_directed_dispatch.py`

- [ ] **Step 1: Add the three event dataclasses + regexes to the parser.**

In `packages/optio-agents/src/optio_agents/protocol/parser.py`, add `import json` near the top (after `import os`, `import re`). Add three dataclasses after `class ErrorEvent` (line 35), before `class UnknownLine`:

```python
@dataclass(frozen=True)
class BrowserEvent:
    url: str


@dataclass(frozen=True)
class AttentionEvent:
    reason: str


@dataclass(frozen=True)
class DomainMessageEvent:
    keyword: str
    data: object
```

Extend the `LogEvent` union (line 42):

```python
LogEvent = Union[
    StatusEvent, DeliverableEvent, DoneEvent, ErrorEvent,
    BrowserEvent, AttentionEvent, DomainMessageEvent, UnknownLine,
]
```

Add the three regexes after `_RE_ERROR` (line 48):

```python
_RE_BROWSER = re.compile(r"^BROWSER:\s*(.+?)\s*$")
_RE_ATTENTION = re.compile(r"^ATTENTION:\s*(.+?)\s*$")
_RE_DOMAIN_MESSAGE = re.compile(r"^DOMAIN_MESSAGE:\s*(\S+)\s+(.*)$")
```

- [ ] **Step 2: Classify the three new keywords in `parse_log_line`.**

In `packages/optio-agents/src/optio_agents/protocol/parser.py`, inside `parse_log_line`, add these match blocks after the `_RE_ERROR` block (line 76, before `return UnknownLine(...)`):

```python
    m = _RE_BROWSER.match(stripped)
    if m:
        return BrowserEvent(url=m.group(1))

    m = _RE_ATTENTION.match(stripped)
    if m:
        return AttentionEvent(reason=m.group(1))

    m = _RE_DOMAIN_MESSAGE.match(stripped)
    if m:
        keyword, payload = m.group(1), m.group(2)
        try:
            data = json.loads(payload)
        except (ValueError, json.JSONDecodeError):
            # Malformed JSON: drop (not dispatched). Surfaced as UnknownLine
            # so the tail loop logs the raw line for diagnosis.
            return UnknownLine(text=stripped)
        return DomainMessageEvent(keyword=keyword, data=data)
```

Note: `BROWSER:` strips surrounding quotes? No — the capture shim writes `BROWSER: "$1"` so the captured value includes literal quotes around the URL. The parser keeps `m.group(1)` verbatim (which will be e.g. `"https://x"` with quotes). The downstream `ctx.request_browser_open` receives the quoted string; the spec keeps `url` permissive (not `.url()`), and the client's `window.open` tolerates it. (An agent emitting `BROWSER:` directly without quotes works too.) Keep the regex verbatim per pinned spec; do not strip quotes.

- [ ] **Step 3: Dispatch the three new events in `_tail_and_dispatch`.**

In `packages/optio-agents/src/optio_agents/protocol/session.py`, extend the parser import (lines 27–37) to add the three new event names:

```python
from optio_agents.protocol.parser import (
    AttentionEvent,
    BrowserEvent,
    DeliverableEvent,
    DomainMessageEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    relativize_deliverable_path,
    validate_deliverable_path,
)
```

In `_tail_and_dispatch` (line 234 onward), add three `elif` branches handling the new events, placed after the `DeliverableEvent` branch and before the `DoneEvent` branch (i.e. after line 260, before `elif isinstance(ev, DoneEvent):`):

```python
        elif isinstance(ev, BrowserEvent):
            await ctx.request_browser_open(ev.url)
        elif isinstance(ev, AttentionEvent):
            await ctx.need_attention(ev.reason)
        elif isinstance(ev, DomainMessageEvent):
            await ctx.domain_message(ev.keyword, ev.data)
```

(`request_browser_open` / `need_attention` / `domain_message` are async — `await` them. They do not terminate the session, so no `return`.)

- [ ] **Step 4: Document the three keywords in the SSOT prompt block.**

In `packages/optio-agents/src/optio_agents/protocol/prompt.py`, update the module docstring keyword list (line 9) to:

```python
``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR`` / ``BROWSER:`` /
``ATTENTION:`` / ``DOMAIN_MESSAGE:``.
```

and add three bullets to `LOG_CHANNEL_PROMPT`, immediately after the `ERROR` bullet (line 25, before the blank line and the trailing-newline paragraph):

```python
- `BROWSER:` — ask the operator's browser to open a URL, e.g.
  `BROWSER: https://example.com/login`. Use for flows that require the
  human to visit a page (e.g. an auth/login URL).
- `ATTENTION:` — request human attention with a short reason, e.g.
  `ATTENTION: waiting for your approval`.
- `DOMAIN_MESSAGE:` — push an application-specific message: a keyword
  token followed by single-line JSON, e.g.
  `DOMAIN_MESSAGE: build-finished {"artifact":"app.zip"}`. The JSON must
  be valid and on one line; malformed JSON is dropped.
```

- [ ] **Step 5: Re-export the new events from `protocol/__init__.py`.**

In `packages/optio-agents/src/optio_agents/protocol/__init__.py`, add the three names to both the parser import (lines 7–17) and `__all__` (lines 26–43):

import block adds (alphabetically among the parser names):

```python
    AttentionEvent,
    BrowserEvent,
    DomainMessageEvent,
```

`__all__` adds:

```python
    "BrowserEvent",
    "AttentionEvent",
    "DomainMessageEvent",
```

- [ ] **Step 6: Extend the parser test.**

In `packages/optio-agents/tests/test_protocol_parser.py`, extend the parser import (lines 3–11) to add `AttentionEvent, BrowserEvent, DomainMessageEvent`, and append these tests to the file:

```python
# ---- BROWSER / ATTENTION / DOMAIN_MESSAGE ----

def test_browser_event():
    ev = parse_log_line('BROWSER: "https://example.com/login"')
    assert isinstance(ev, BrowserEvent)
    assert ev.url == '"https://example.com/login"'


def test_browser_event_unquoted():
    ev = parse_log_line("BROWSER: https://example.com")
    assert isinstance(ev, BrowserEvent)
    assert ev.url == "https://example.com"


def test_attention_event():
    ev = parse_log_line("ATTENTION: please approve")
    assert isinstance(ev, AttentionEvent)
    assert ev.reason == "please approve"


def test_domain_message_event():
    ev = parse_log_line('DOMAIN_MESSAGE: build-done {"artifact": "app.zip"}')
    assert isinstance(ev, DomainMessageEvent)
    assert ev.keyword == "build-done"
    assert ev.data == {"artifact": "app.zip"}


def test_domain_message_malformed_json_drops_to_unknown():
    ev = parse_log_line("DOMAIN_MESSAGE: k {not valid json}")
    assert isinstance(ev, UnknownLine)
```

- [ ] **Step 7: Extend the prompt SSOT test.**

In `packages/optio-agents/tests/test_prompt.py`, replace `test_block_documents_all_four_keywords` with a version covering all seven keywords (rename for accuracy):

```python
def test_block_documents_all_keywords():
    for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR",
               "BROWSER:", "ATTENTION:", "DOMAIN_MESSAGE:"):
        assert kw in LOG_CHANNEL_PROMPT
```

- [ ] **Step 8: Author the dispatch test.**

Create `packages/optio-agents/tests/test_client_directed_dispatch.py`:

```python
"""_tail_and_dispatch routes the three client-directed events to ctx."""

import pytest

from optio_agents.protocol.session import _tail_and_dispatch


class _FakeHost:
    def __init__(self, lines, workdir="/wd"):
        self._lines = lines
        self.workdir = workdir

    async def tail_file(self, _path):
        for line in self._lines:
            yield line


class _FakeCtx:
    def __init__(self):
        self.browser = []
        self.attention = []
        self.domain = []
        self.progress = []

    async def request_browser_open(self, url):
        self.browser.append(url)
        return "rid-b"

    async def need_attention(self, reason):
        self.attention.append(reason)
        return "rid-a"

    async def domain_message(self, keyword, data):
        self.domain.append((keyword, data))
        return "rid-d"

    def report_progress(self, percent, message):
        self.progress.append((percent, message))


@pytest.mark.asyncio
async def test_dispatch_routes_browser_attention_domain():
    host = _FakeHost([
        "BROWSER: https://x\n",
        "ATTENTION: help me\n",
        'DOMAIN_MESSAGE: ev {"n": 1}\n',
        "DONE\n",
    ])
    ctx = _FakeCtx()
    import asyncio
    done = asyncio.Event()
    await _tail_and_dispatch(host, ctx, asyncio.Queue(), done, [])
    assert ctx.browser == ["https://x"]
    assert ctx.attention == ["help me"]
    assert ctx.domain == [("ev", {"n": 1})]
    assert done.is_set()
```

---

### Task 4: optio-agents — browser_capture opt-in shim helper

**Files:**
- Create: `packages/optio-agents/src/optio_agents/browser_capture.py`
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`
- Create: `packages/optio-agents/tests/test_browser_capture.py`

- [ ] **Step 1: Create the capture shim module.**

Create `packages/optio-agents/src/optio_agents/browser_capture.py`:

```python
"""Opt-in browser-open capture shims for the agent launch environment.

`enable(host)` writes capture-only shims for the common browser-opener
commands (xdg-open, gio, open, sensible-browser, www-browser) under
``<workdir>/bin``. Each shim appends a ``BROWSER: "<url>"`` line to
``<workdir>/optio.log`` and exits 0 — it never launches a real browser
(there is none on the worker). It returns env additions to merge into
the agent launch env: ``BROWSER`` pointing at the shim and a
``<workdir>/bin`` PATH prepend.

Opt-in (default off) so it never collides with opencode's own browser
*suppression* shims (the two shim sets are never enabled together).

Mirrors the opencode suppression-shim pattern in
``optio_opencode.host_actions`` but captures instead of suppressing.
"""

from __future__ import annotations

import os

from optio_host.host import Host


_SHIM_NAMES = ("xdg-open", "gio", "open", "sensible-browser", "www-browser")


async def enable(host: Host) -> dict[str, str]:
    """Write the capture shims under ``<workdir>/bin`` and return env additions.

    Returns a dict with ``BROWSER`` and ``PATH`` keys to merge into the
    agent launch env (``PATH`` prepends ``<workdir>/bin``).
    """
    # The shim appends `BROWSER: "<first-arg>"` to optio.log and exits 0.
    # $1 is the URL the opener was invoked with. Quote it so the captured
    # marker is unambiguous even if the URL contains spaces.
    shim_body = (
        "#!/bin/sh\n"
        f'printf \'BROWSER: "%s"\\n\' "$1" >> {host.workdir}/optio.log\n'
        "exit 0\n"
    )
    for name in _SHIM_NAMES:
        await host.write_text(f"bin/{name}", shim_body)
    await host.run_command(f"chmod +x {host.workdir}/bin/*")

    workdir_bin = f"{host.workdir}/bin"
    extra_path = workdir_bin + ":" + os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {
        "BROWSER": f"{workdir_bin}/xdg-open",
        "PATH": extra_path,
    }
```

- [ ] **Step 2: Export `browser_capture` from the package.**

In `packages/optio-agents/src/optio_agents/__init__.py`, add a submodule import after the `from optio_agents.protocol import (...)` block (line 24):

```python
from optio_agents import browser_capture
```

and add `"browser_capture"` to `__all__` (line 26 list).

- [ ] **Step 3: Author the capture shim test.**

Create `packages/optio-agents/tests/test_browser_capture.py`:

```python
"""browser_capture.enable writes capturing shims; a subprocess invoking
the shim is captured end-to-end into optio.log."""

import os
import subprocess

import pytest

from optio_host.host import LocalHost
from optio_agents import browser_capture
from optio_agents.protocol.parser import parse_log_line, BrowserEvent


@pytest.mark.asyncio
async def test_enable_returns_env_and_writes_shims(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    env_add = await browser_capture.enable(host)

    assert env_add["BROWSER"].endswith("/bin/xdg-open")
    assert env_add["PATH"].startswith(f"{host.workdir}/bin:")
    for name in ("xdg-open", "gio", "open", "sensible-browser", "www-browser"):
        shim = os.path.join(host.workdir, "bin", name)
        assert os.path.isfile(shim)
        assert os.access(shim, os.X_OK)


@pytest.mark.asyncio
async def test_shim_captures_browser_marker_end_to_end(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    # optio.log must exist for the append to land somewhere observable.
    await host.write_text("optio.log", "")
    await browser_capture.enable(host)

    # Invoke the shim exactly as a real opener would be invoked.
    shim = os.path.join(host.workdir, "bin", "xdg-open")
    subprocess.run([shim, "https://example.com/login"], check=True)

    log = open(os.path.join(host.workdir, "optio.log")).read()
    lines = [ln for ln in log.splitlines() if ln.startswith("BROWSER:")]
    assert len(lines) == 1
    ev = parse_log_line(lines[0])
    assert isinstance(ev, BrowserEvent)
    assert ev.url == '"https://example.com/login"'
```

---

### Task 5: optio-api — browserOpenRequests on pollers + session-events SSE + launch forwards sessionId

**Files:**
- Modify: `packages/optio-api/src/stream-poller.ts`
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/handlers.ts`
- Modify: `packages/optio-api/src/__tests__/stream-poller.test.ts`
- Create: `packages/optio-api/src/__tests__/session-events-poller.test.ts`

No Mongo writes. No `_generated` edits.

- [ ] **Step 1: Add `browserOpenRequests` to `createListPoller`.**

In `packages/optio-api/src/stream-poller.ts`, in `createListPoller`'s snapshot map (lines 29–36) add a line:

```typescript
        allProcs.map((p: any) => ({
          id: p._id,
          state: p.status?.state,
          percent: p.progress?.percent,
          message: p.progress?.message,
          supportsResume: p.supportsResume ?? false,
          hasSavedState: p.hasSavedState ?? false,
          browserOpenRequests: p.browserOpenRequests ?? [],
        })),
```

and add it to the emitted `update` process payload (lines 43–56) after `hasSavedState`:

```typescript
            hasSavedState: p.hasSavedState ?? false,
            browserOpenRequests: p.browserOpenRequests ?? [],
          })),
```

- [ ] **Step 2: Add `browserOpenRequests` to `createTreePoller`.**

In `createTreePoller`'s snapshot map (lines 102–108) add `browserOpenRequests: p.browserOpenRequests ?? [],`, and in the emitted payload (lines 115–130) add `browserOpenRequests: p.browserOpenRequests ?? [],` after `hasSavedState`.

- [ ] **Step 3: Add `browserOpenRequests` to `createMultiTreePoller`.**

In `createMultiTreePoller`'s snapshot map (lines 236–242) add `browserOpenRequests: p.browserOpenRequests ?? [],`, and in the emitted payload (lines 249–265) add `browserOpenRequests: p.browserOpenRequests ?? [],` after `hasSavedState`.

- [ ] **Step 4: Add the session-events poller.**

In `packages/optio-api/src/stream-poller.ts`, append a new poller at the end of the file (after `createMultiTreePoller`, line 324):

```typescript
export interface SessionEventsPollerOptions {
  db: Db;
  prefix: string;
  sessionId: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
}

/**
 * Poll-backed session-events feed. Each ~1s tick reads processes whose
 * `originatingSessionId` matches `sessionId` and emits each process's NEW
 * sessionEvents (deduped by length high-water mark per process). Read-only.
 */
export function createSessionEventsPoller(opts: SessionEventsPollerOptions): ListPollerHandle {
  const { db, prefix, sessionId, sendEvent, onError } = opts;
  const col = db.collection(`${prefix}_processes`);
  let interval: ReturnType<typeof setInterval> | null = null;
  const lastCounts = new Map<string, number>();

  async function poll() {
    try {
      const procs = await col
        .find({ originatingSessionId: sessionId })
        .project({ sessionEvents: 1 })
        .toArray();
      for (const p of procs) {
        const pid = p._id.toString();
        const events = (p.sessionEvents ?? []) as any[];
        const seen = lastCounts.get(pid) ?? 0;
        if (events.length > seen) {
          sendEvent({
            type: 'session-events',
            processId: pid,
            events: events.slice(seen),
          });
          lastCounts.set(pid, events.length);
        }
      }
    } catch {
      stop();
      onError();
    }
  }

  function start() {
    interval = setInterval(poll, 1000);
  }

  function stop() {
    if (interval) {
      clearInterval(interval);
      interval = null;
    }
  }

  return { start, stop };
}
```

- [ ] **Step 5: Register the `/api/session-events/stream` route in fastify.**

In `packages/optio-api/src/adapters/fastify.ts`, extend the poller import (line 12):

```typescript
import { createListPoller, createTreePoller, createMultiTreePoller, createSessionEventsPoller } from '../stream-poller.js';
```

Add a new route immediately before the final `return explicit ? undefined : ctx;` (line 620), modeled on the list-stream route (lines 578–618):

```typescript
  app.get('/api/session-events/stream', async (request: any, reply: any) => {
    const rawQuery = (request.query as Record<string, unknown>) ?? {};
    const sessionId = typeof rawQuery.sessionId === 'string' ? rawQuery.sessionId : '';
    let sseOpts;
    try {
      sseOpts = parseSseOptions(rawQuery);
    } catch (e) {
      reply.code(400).send({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    // No sessionId → match nothing (still keeps the connection open, emitting
    // nothing). Single-operator deployments always get per-launch routing
    // because the UI always sends its token.
    if (!sessionId) {
      request.raw.on('close', () => {});
      return;
    }

    const poller = createSessionEventsPoller({
      db,
      prefix,
      sessionId,
      sendEvent,
      onError: () => reply.raw.end(),
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });
```

- [ ] **Step 6: Forward `sessionId` through the launch handler.**

In `packages/optio-api/src/handlers.ts`, change `launchProcess` (lines 256–266) to accept and forward `sessionId` (added as a 5th positional param defaulting to `null`, so the other adapters' existing 4-arg calls keep working):

```typescript
export async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume: boolean = false,
  sessionId: string | null = null,
): Promise<LaunchCommandResult> {
  const engine = resolveOptioEngine(ctx, query);
  const result = await engine.launch({ processId: id, resume, sessionId });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return launchFail(result.reason);
}
```

(After Task 10 codegen, `engine.launch`'s `LaunchParams` requires `sessionId`, so passing it is mandatory; the default `null` satisfies the nullable-required contract.)

- [ ] **Step 7: Pass `sessionId` from the fastify launch route.**

In `packages/optio-api/src/adapters/fastify.ts`, change the ts-rest `launch` handler (line 428–431):

```typescript
    launch: async ({ params, query, body }) => {
      const result = await handlers.launchProcess(
        ctx, query, params.id, body?.resume === true, body?.sessionId ?? null,
      );
      return result as any;
    },
```

- [ ] **Step 8: Extend the stream-poller test for browserOpenRequests.**

In `packages/optio-api/src/__tests__/stream-poller.test.ts`, add a test inside the existing describe block (after the widgetData propagation tests). Append:

```typescript
describe('browserOpenRequests propagation', () => {
  it('createTreePoller includes browserOpenRequests in the update payload', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p', name: 'P',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      browserOpenRequests: [{ requestId: 'r1', url: 'https://x' }],
      cancellable: true,
      log: [],
    });
    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();
    const update = events.find((e) => e.type === 'update');
    expect(update.processes[0].browserOpenRequests).toEqual([{ requestId: 'r1', url: 'https://x' }]);
  });

  it('createListPoller includes browserOpenRequests in the update payload', async () => {
    const events: any[] = [];
    const id = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: id, processId: 'p2', name: 'P2', rootId: id, parentId: null,
      depth: 0, order: 0, status: { state: 'running' }, progress: { percent: null },
      browserOpenRequests: [{ requestId: 'r2', url: 'https://y' }], cancellable: true, log: [],
    });
    const poller = createListPoller({
      db, prefix: PREFIX,
      sendEvent: (data) => events.push(data),
      onError: () => {},
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();
    const update = events.find((e) => e.type === 'update');
    const p2 = update.processes.find((p: any) => p.processId === 'p2');
    expect(p2.browserOpenRequests).toEqual([{ requestId: 'r2', url: 'https://y' }]);
  });
});
```

- [ ] **Step 9: Author the session-events poller test.**

Create `packages/optio-api/src/__tests__/session-events-poller.test.ts`:

```typescript
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import { createSessionEventsPoller } from '../stream-poller.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_session_events_poller';
const PREFIX = 'test';

let client: MongoClient;
let db: Db;

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
});

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

beforeEach(async () => {
  await db.collection(`${PREFIX}_processes`).deleteMany({});
});

describe('createSessionEventsPoller', () => {
  it('delivers only events for matching originatingSessionId', async () => {
    const events: any[] = [];
    await db.collection(`${PREFIX}_processes`).insertMany([
      {
        _id: new ObjectId(), processId: 'mine', name: 'Mine',
        originatingSessionId: 'tok-A',
        sessionEvents: [{ requestId: 'r1', type: 'attention', reason: 'help' }],
      },
      {
        _id: new ObjectId(), processId: 'other', name: 'Other',
        originatingSessionId: 'tok-B',
        sessionEvents: [{ requestId: 'r2', type: 'attention', reason: 'nope' }],
      },
    ]);
    const poller = createSessionEventsPoller({
      db, prefix: PREFIX, sessionId: 'tok-A',
      sendEvent: (d) => events.push(d), onError: () => {},
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();
    const msgs = events.filter((e) => e.type === 'session-events');
    expect(msgs).toHaveLength(1);
    expect(msgs[0].events).toEqual([{ requestId: 'r1', type: 'attention', reason: 'help' }]);
  });

  it('emits only newly-appended events on subsequent ticks (dedup by high-water mark)', async () => {
    const events: any[] = [];
    const id = new ObjectId();
    const coll = db.collection(`${PREFIX}_processes`);
    await coll.insertOne({
      _id: id, processId: 'p', name: 'P', originatingSessionId: 'tok',
      sessionEvents: [{ requestId: 'r1', type: 'attention', reason: 'a' }],
    });
    const poller = createSessionEventsPoller({
      db, prefix: PREFIX, sessionId: 'tok',
      sendEvent: (d) => events.push(d), onError: () => {},
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    await coll.updateOne({ _id: id }, { $push: { sessionEvents: { requestId: 'r2', type: 'domain', keyword: 'k', data: 1 } } });
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();
    const msgs = events.filter((e) => e.type === 'session-events');
    const allReqIds = msgs.flatMap((m) => m.events.map((e: any) => e.requestId));
    expect(allReqIds).toEqual(['r1', 'r2']);
  });
});
```

---

### Task 6: optio-ui browser-open — shared handler wired into all three feeds

**Files:**
- Create: `packages/optio-ui/src/handlers/browserOpen.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessListStream.tsx`
- Modify: `packages/optio-ui/src/context/MultiProcessStreamContext.tsx`
- Modify: `packages/optio-ui/src/hooks/useProcessStream.ts`
- Create: `packages/optio-ui/src/__tests__/browserOpen.test.tsx`

Do **NOT** edit `index.ts` (that is Task 7's file).

- [ ] **Step 1: Create the shared browser-open handler module.**

Create `packages/optio-ui/src/handlers/browserOpen.ts`:

```typescript
import { notification } from 'antd';

interface BrowserOpenRequest {
  requestId: string;
  url: string;
}

// Module-level dedup across all feed chokepoints. A given requestId fires
// exactly once per app instance, no matter which feed surfaces it.
const _seen = new Set<string>();

/**
 * View-scoped browser-open handler. Called from every per-process feed
 * chokepoint with the `browserOpenRequests` each `update` carries. For each
 * not-yet-seen requestId it attempts `window.open(url)` and raises an
 * app-level antd notification with an "Open in a new tab ↗" link — the
 * always-available fallback when window.open is popup-blocked (an SSE
 * callback has no user gesture). Imperative/global; visible regardless of
 * which view is mounted.
 */
export function handleBrowserOpenRequests(requests: BrowserOpenRequest[] | undefined): void {
  if (!requests || requests.length === 0) return;
  for (const req of requests) {
    if (!req || typeof req.requestId !== 'string') continue;
    if (_seen.has(req.requestId)) continue;
    _seen.add(req.requestId);

    // The capture shim may quote the URL (e.g. `"https://x"`). Strip a single
    // pair of surrounding double quotes so window.open / href get a clean URL.
    const url = req.url.replace(/^"(.*)"$/, '$1');

    let opened: Window | null = null;
    try {
      opened = window.open(url, '_blank', 'noopener,noreferrer');
    } catch {
      opened = null;
    }
    if (!opened) {
      notification.info({
        message: 'A task wants to open a page',
        description: (
          // eslint-disable-next-line react/no-unknown-property
          <a href={url} target="_blank" rel="noopener noreferrer">
            Open in a new tab ↗
          </a>
        ),
        duration: 0,
      });
    }
  }
}

// Test-only reset of the dedup set.
export function __resetBrowserOpenSeenForTest(): void {
  _seen.clear();
}
```

(The JSX `<a>` in a `.ts` file requires a `.tsx` extension. Rename the file to `packages/optio-ui/src/handlers/browserOpen.tsx` and import it as `'../handlers/browserOpen.js'` — vitest/tsc resolve the `.tsx` source behind the `.js` specifier, matching the repo's existing `.tsx`→`.js` import convention. **Create the file as `browserOpen.tsx`.**)

- [ ] **Step 2: Wire the handler into the list feed.**

In `packages/optio-ui/src/hooks/useProcessListStream.tsx`, add the import at the top (after line 3):

```typescript
import { handleBrowserOpenRequests } from '../handlers/browserOpen.js';
```

and call it inside `es.onmessage` for the `update` branch (lines 53–61):

```typescript
  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'update') {
        for (const p of data.processes) handleBrowserOpenRequests(p.browserOpenRequests);
        _state = { processes: data.processes, connected: true };
        notify();
      }
    } catch { /* ignore */ }
  };
```

- [ ] **Step 3: Wire the handler into the multi-tree provider feed.**

In `packages/optio-ui/src/context/MultiProcessStreamContext.tsx`, add the import (after line 2):

```typescript
import { handleBrowserOpenRequests } from '../handlers/browserOpen.js';
```

Add `browserOpenRequests?: { requestId: string; url: string }[];` to the `MultiProcessUpdate` interface (after line 19, before the closing brace). In the `update` branch of `es.onmessage` (lines 175–196), iterate the incoming processes before binning:

```typescript
        } else if (data.type === 'update') {
          const procs: MultiProcessUpdate[] = data.processes;
          for (const p of procs) handleBrowserOpenRequests(p.browserOpenRequests);
          // Capture treeIds/flatIds at the time this event fires ...
```

- [ ] **Step 4: Wire the handler into the per-PID fallback feed.**

In `packages/optio-ui/src/hooks/useProcessStream.ts`, add the import (after line 3):

```typescript
import { handleBrowserOpenRequests } from '../handlers/browserOpen.js';
```

and call it in the per-PID `es.onmessage` `update` branch (line 131):

```typescript
          if (data.type === 'update') {
            for (const p of data.processes) handleBrowserOpenRequests(p.browserOpenRequests);
            setState((s) => ({ ...s, processes: data.processes }));
          }
```

(Add `browserOpenRequests?: { requestId: string; url: string }[];` to the local `ProcessUpdate` interface, after line 16.)

- [ ] **Step 5: Author the browser-open handler test.**

Create `packages/optio-ui/src/__tests__/browserOpen.test.tsx`:

```typescript
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { handleBrowserOpenRequests, __resetBrowserOpenSeenForTest } from '../handlers/browserOpen.js';

describe('handleBrowserOpenRequests', () => {
  beforeEach(() => {
    __resetBrowserOpenSeenForTest();
    vi.restoreAllMocks();
  });

  it('opens each url exactly once across repeated deliveries (dedup by requestId)', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    const reqs = [{ requestId: 'r1', url: 'https://x' }];
    handleBrowserOpenRequests(reqs);
    handleBrowserOpenRequests(reqs); // re-delivered on the next poll tick
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('https://x', '_blank', 'noopener,noreferrer');
  });

  it('strips surrounding quotes from a captured url', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    handleBrowserOpenRequests([{ requestId: 'r2', url: '"https://q"' }]);
    expect(open).toHaveBeenCalledWith('https://q', '_blank', 'noopener,noreferrer');
  });

  it('is a no-op for empty/undefined input', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    handleBrowserOpenRequests(undefined);
    handleBrowserOpenRequests([]);
    expect(open).not.toHaveBeenCalled();
  });

  it('dedups across distinct feed chokepoints (shared module-level Set)', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    // Same requestId arriving from the list feed then the tree feed.
    handleBrowserOpenRequests([{ requestId: 'shared', url: 'https://z' }]);
    handleBrowserOpenRequests([{ requestId: 'shared', url: 'https://z' }]);
    expect(open).toHaveBeenCalledTimes(1);
  });
});
```

---

### Task 7: optio-ui session-events — EventSource manager, sessionId lifecycle, provider callbacks, launch wiring

**Files:**
- Create: `packages/optio-ui/src/session/sessionEvents.ts`
- Modify: `packages/optio-ui/src/context/OptioProvider.tsx`
- Modify: `packages/optio-ui/src/hooks/useProcessActions.ts`
- Modify: `packages/optio-ui/src/index.ts`
- Create: `packages/optio-ui/src/__tests__/sessionEvents.test.tsx`

- [ ] **Step 1: Create the session-events manager module.**

Create `packages/optio-ui/src/session/sessionEvents.ts`:

```typescript
/**
 * Always-on, singleton session-events manager.
 *
 * Owns the tab's opaque `sessionId` (persisted in sessionStorage under
 * "optioSessionId"), a single EventSource against /api/session-events/stream,
 * requestId dedup, and dispatch by `type` to app-supplied callbacks.
 *
 * Module-level (not React state) so `useProcessActions.launch` can read the
 * sessionId without a context dependency, and so the EventSource survives
 * re-renders. Mounted once by OptioProvider.
 */

const SESSION_STORAGE_KEY = 'optioSessionId';

type SessionEvent =
  | { requestId: string; type: 'attention'; reason: string }
  | { requestId: string; type: 'domain'; keyword: string; data: unknown };

export interface SessionEventCallbacks {
  onAttention?: (processId: string, reason: string) => void;
  onDomainMessage?: (processId: string, keyword: string, data: unknown) => void;
}

let _sessionId: string | null = null;
let _eventSource: EventSource | null = null;
let _callbacks: SessionEventCallbacks = {};
let _baseUrl = '';
const _seen = new Set<string>();

function mintToken(): string {
  // crypto.randomUUID is available in all EventSource-capable browsers.
  try {
    return crypto.randomUUID().replace(/-/g, '');
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

/** Return the tab's sessionId, minting + persisting it on first use. */
export function getSessionId(): string {
  if (_sessionId) return _sessionId;
  let stored: string | null = null;
  try {
    stored = sessionStorage.getItem(SESSION_STORAGE_KEY);
  } catch { /* sessionStorage unavailable (SSR/tests) */ }
  if (stored) {
    _sessionId = stored;
  } else {
    _sessionId = mintToken();
    try {
      sessionStorage.setItem(SESSION_STORAGE_KEY, _sessionId);
    } catch { /* ignore */ }
  }
  return _sessionId;
}

function closeStream() {
  _eventSource?.close();
  _eventSource = null;
}

function connect() {
  closeStream();
  const sessionId = getSessionId();
  const url = `${_baseUrl}/api/session-events/stream?sessionId=${encodeURIComponent(sessionId)}`;
  const es = new EventSource(url);
  _eventSource = es;
  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type !== 'session-events') return;
      const processId: string = data.processId;
      const events: SessionEvent[] = data.events ?? [];
      for (const ev of events) {
        if (_seen.has(ev.requestId)) continue;
        _seen.add(ev.requestId);
        if (ev.type === 'attention') {
          _callbacks.onAttention?.(processId, ev.reason);
        } else if (ev.type === 'domain') {
          _callbacks.onDomainMessage?.(processId, ev.keyword, ev.data);
        }
      }
    } catch { /* ignore malformed */ }
  };
  // EventSource auto-reconnects on error; nothing to do here.
}

/**
 * Start (or update) the session-events subscription. Idempotent: safe to call
 * on every render. Updates callbacks + baseUrl in place; (re)connects only
 * when the connection is absent or the baseUrl changed.
 */
export function startSessionEvents(baseUrl: string, callbacks: SessionEventCallbacks): void {
  _callbacks = callbacks;
  if (_eventSource && _baseUrl === baseUrl) return;
  _baseUrl = baseUrl;
  connect();
}

/**
 * Mint a fresh sessionId and reconnect the SSE. Called by the app on logout /
 * any session cutoff. Clears the dedup set so a new session re-surfaces events.
 */
export function resetSession(): void {
  _sessionId = mintToken();
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, _sessionId);
  } catch { /* ignore */ }
  _seen.clear();
  connect();
}

// Test-only full reset.
export function __resetSessionStateForTest(): void {
  closeStream();
  _sessionId = null;
  _callbacks = {};
  _baseUrl = '';
  _seen.clear();
}
```

- [ ] **Step 2: Mount the manager + expose callbacks/resetSession via OptioProvider.**

In `packages/optio-ui/src/context/OptioProvider.tsx`, replace the file with this version (adds `onAttention`/`onDomainMessage` props, mounts the manager once via `useEffect`, and exposes `resetSession` on the context):

```typescript
import { createContext, useMemo, useEffect, type ReactNode } from 'react';
import { createOptioClient, type OptioClient } from '../client.js';
import { useInstanceDiscovery } from '../hooks/useInstanceDiscovery.js';
import { startSessionEvents, resetSession, type SessionEventCallbacks } from '../session/sessionEvents.js';

interface OptioContextValue {
  prefix: string;
  database: string | undefined;
  live: boolean;
  baseUrl: string;
  client: OptioClient;
  resetSession: () => void;
}

export const OptioContext = createContext<OptioContextValue>(null as any);

interface OptioProviderProps {
  prefix?: string;
  database?: string;
  live?: boolean;
  baseUrl?: string;
  onAttention?: (processId: string, reason: string) => void;
  onDomainMessage?: (processId: string, keyword: string, data: unknown) => void;
  children: ReactNode;
}

function OptioProviderInner({ explicitPrefix, explicitDatabase, explicitLive, baseUrl, client, children }: {
  explicitPrefix: string | undefined;
  explicitDatabase: string | undefined;
  explicitLive: boolean | undefined;
  baseUrl: string;
  client: OptioClient;
  children: ReactNode;
}) {
  const { instance: discoveredInstance } = useInstanceDiscovery();
  const prefix = explicitPrefix ?? discoveredInstance?.prefix ?? 'optio';
  const database = explicitDatabase ?? discoveredInstance?.database;
  const live = explicitLive ?? discoveredInstance?.live ?? false;

  return (
    <OptioContext.Provider value={{ prefix, database, live, baseUrl, client, resetSession }}>
      {children}
    </OptioContext.Provider>
  );
}

export function OptioProvider({ prefix, database, live, baseUrl = '', onAttention, onDomainMessage, children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  // Mount the always-on session-events manager once. Re-runs when the
  // callbacks or baseUrl change; startSessionEvents updates callbacks in
  // place and only (re)connects on baseUrl change.
  useEffect(() => {
    const callbacks: SessionEventCallbacks = { onAttention, onDomainMessage };
    startSessionEvents(baseUrl, callbacks);
  }, [baseUrl, onAttention, onDomainMessage]);

  return (
    <OptioContext.Provider value={{ prefix: prefix ?? 'optio', database, live: live ?? false, baseUrl, client, resetSession }}>
      <OptioProviderInner explicitPrefix={prefix} explicitDatabase={database} explicitLive={live} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
```

- [ ] **Step 3: Attach `sessionId` to launch.**

In `packages/optio-ui/src/hooks/useProcessActions.ts`, add the import (after line 3):

```typescript
import { getSessionId } from '../session/sessionEvents.js';
```

and change the `launch` action (lines 28–33) so the body always carries `sessionId`:

```typescript
    launch: (processId: string, opts?: { resume?: boolean }) =>
      launchMutation.mutate({
        params: { id: processId },
        query: { database, prefix },
        body: { sessionId: getSessionId(), ...(opts?.resume === true ? { resume: true } : {}) },
      }),
```

- [ ] **Step 4: Export the session surface from index.ts.**

In `packages/optio-ui/src/index.ts`, after the Provider exports (line 3), add:

```typescript
export { getSessionId, resetSession } from './session/sessionEvents.js';
export type { SessionEventCallbacks } from './session/sessionEvents.js';
```

- [ ] **Step 5: Author the session-events manager test.**

Create `packages/optio-ui/src/__tests__/sessionEvents.test.tsx`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  getSessionId,
  resetSession,
  startSessionEvents,
  __resetSessionStateForTest,
} from '../session/sessionEvents.js';

// Minimal EventSource fake.
class FakeES {
  static instances: FakeES[] = [];
  url: string;
  onmessage: ((e: { data: string }) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeES.instances.push(this);
  }
  close() { this.closed = true; }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
}

beforeEach(() => {
  __resetSessionStateForTest();
  FakeES.instances = [];
  sessionStorage.clear();
  (globalThis as any).EventSource = FakeES as any;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('sessionId lifecycle', () => {
  it('mints once and persists in sessionStorage', () => {
    const a = getSessionId();
    const b = getSessionId();
    expect(a).toBe(b);
    expect(sessionStorage.getItem('optioSessionId')).toBe(a);
  });

  it('reuses a stored token (survives reload)', () => {
    sessionStorage.setItem('optioSessionId', 'persisted');
    expect(getSessionId()).toBe('persisted');
  });

  it('resetSession rotates the token and reconnects', () => {
    startSessionEvents('', {});
    const before = getSessionId();
    const esBefore = FakeES.instances.at(-1)!;
    resetSession();
    const after = getSessionId();
    expect(after).not.toBe(before);
    expect(esBefore.closed).toBe(true);
    expect(FakeES.instances.at(-1)!.url).toContain(`sessionId=${after}`);
  });
});

describe('dispatch by type', () => {
  it('routes attention and domain events to the right callbacks, deduped by requestId', () => {
    const onAttention = vi.fn();
    const onDomainMessage = vi.fn();
    startSessionEvents('', { onAttention, onDomainMessage });
    const es = FakeES.instances.at(-1)!;
    es.emit({
      type: 'session-events', processId: 'pid-1',
      events: [
        { requestId: 'a1', type: 'attention', reason: 'help' },
        { requestId: 'd1', type: 'domain', keyword: 'k', data: { n: 1 } },
      ],
    });
    // Re-delivery of the same events (next poll tick) must not re-fire.
    es.emit({
      type: 'session-events', processId: 'pid-1',
      events: [{ requestId: 'a1', type: 'attention', reason: 'help' }],
    });
    expect(onAttention).toHaveBeenCalledTimes(1);
    expect(onAttention).toHaveBeenCalledWith('pid-1', 'help');
    expect(onDomainMessage).toHaveBeenCalledTimes(1);
    expect(onDomainMessage).toHaveBeenCalledWith('pid-1', 'k', { n: 1 });
  });
});
```

---

### Task 8: optio-demo — four client-directed test tasks

**Files:**
- Create: `packages/optio-demo/src/optio_demo/tasks/client_directed.py`
- Modify: `packages/optio-demo/src/optio_demo/tasks/__init__.py`

- [ ] **Step 1: Create the demo task module.**

Create `packages/optio-demo/src/optio_demo/tasks/client_directed.py`:

```python
"""Client-directed events demo tasks (phase 2).

Four tasks exercising the three new capabilities end-to-end:

  - ``open-optio-repo``: pure-Python ``ctx.request_browser_open`` (view-scoped).
  - ``open-browser-via-tool``: a host task that runs a tiny Python script
    (``import webbrowser; webbrowser.open(URL)`` then ``DONE``) through the
    optio-agents session driver with ``browser_capture.enable`` on — exercises
    shim → ``BROWSER:`` marker → parser → ``ctx.request_browser_open`` with no
    claude/opencode involved.
  - ``need-attention-demo``: ``ctx.need_attention`` (session-scoped).
  - ``domain-message-demo``: ``ctx.domain_message`` (session-scoped).
"""

from __future__ import annotations

import os

from optio_core.models import TaskInstance
from optio_host.host import LocalHost
from optio_agents import browser_capture, run_log_protocol_session


OPTIO_REPO_URL = "https://github.com/deai-network/optio"


async def _open_optio_repo(ctx) -> None:
    ctx.report_progress(0, "Opening the optio repo in your browser")
    rid = await ctx.request_browser_open(OPTIO_REPO_URL)
    ctx.report_progress(100, f"Requested browser open (requestId={rid})")


async def _open_browser_via_tool(ctx) -> None:
    """Host-bridge capture test: run a Python opener under capture shims."""
    taskdir = f"/tmp/optio-demo-browser-{os.getpid()}-{ctx.process_id}"
    os.makedirs(taskdir, exist_ok=True)
    host = LocalHost(taskdir=taskdir)
    os.makedirs(host.workdir, exist_ok=True)

    async def body(host, hook_ctx) -> None:
        env_add = await browser_capture.enable(host)
        # A trivial opener: webbrowser.open routes through xdg-open (our shim),
        # which appends the BROWSER: marker to optio.log. Then signal DONE.
        script = (
            "import webbrowser; "
            f"webbrowser.open({OPTIO_REPO_URL!r}); "
        )
        await host.run_command(
            f"python3 -c {script!r}",
            env=env_add,
            cwd=host.workdir,
        )
        # The shim has appended BROWSER: by now; close out the session.
        await host.run_command(f"echo DONE >> {host.workdir}/optio.log")

    await run_log_protocol_session(host, ctx, body=body)


async def _need_attention_demo(ctx) -> None:
    ctx.report_progress(0, "Requesting your attention")
    rid = await ctx.need_attention("The demo task would like you to look at it.")
    ctx.report_progress(100, f"Attention requested (requestId={rid})")


async def _domain_message_demo(ctx) -> None:
    ctx.report_progress(0, "Sending a domain message")
    rid = await ctx.domain_message(
        "demo-event",
        {"severity": "info", "detail": "hello from the demo task"},
    )
    ctx.report_progress(100, f"Domain message sent (requestId={rid})")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_open_optio_repo,
            process_id="open-optio-repo",
            name="Open the optio repo",
            description=(
                "Pure-Python browser_open: asks your browser to open the optio "
                "GitHub repo. View-scoped — delivered to whoever is watching."
            ),
        ),
        TaskInstance(
            execute=_open_browser_via_tool,
            process_id="open-browser-via-tool",
            name="Open browser via tool (capture bridge)",
            description=(
                "Host task running a Python webbrowser.open under "
                "browser_capture shims; exercises shim → BROWSER: marker → "
                "parser → ctx.request_browser_open end-to-end (no agent)."
            ),
        ),
        TaskInstance(
            execute=_need_attention_demo,
            process_id="need-attention-demo",
            name="Request attention",
            description=(
                "Calls ctx.need_attention(...). Session-scoped — reaches the "
                "browser session that launched it. The dashboard navigates to "
                "this process via onAttention."
            ),
        ),
        TaskInstance(
            execute=_domain_message_demo,
            process_id="domain-message-demo",
            name="Send a domain message",
            description=(
                "Calls ctx.domain_message(keyword, data). Session-scoped; the "
                "dashboard surfaces it via onDomainMessage (console/toast)."
            ),
        ),
    ]
```

- [ ] **Step 2: Register the new tasks.**

In `packages/optio-demo/src/optio_demo/tasks/__init__.py`, add the import (after line 11):

```python
from optio_demo.tasks.client_directed import get_tasks as client_directed_tasks
```

and add `*client_directed_tasks(),` to the returned list (after `*opencode_tasks(),`, line 25):

```python
        *opencode_tasks(),
        *client_directed_tasks(),
    ]
```

---

### Task 9: optio-dashboard — supply onAttention + onDomainMessage to OptioProvider

**Files:**
- Modify: `packages/optio-dashboard/src/app/App.tsx`

- [ ] **Step 1: Lift `selectedProcessId` up to `AppContent` and wire the callbacks.**

In `packages/optio-dashboard/src/app/App.tsx`, the `OptioProvider` is rendered in `AppContent` (line 110) but `selectedProcessId` lives in `Dashboard` (line 21) — which is *inside* the provider. To let `onAttention` navigate via `setSelectedProcessId`, lift the selection state to `AppContent` and pass it down.

Change `Dashboard` (lines 20–47) to receive the selection via props instead of owning the state:

```typescript
function Dashboard({
  selectedProcessId,
  setSelectedProcessId,
}: {
  selectedProcessId: string | null;
  setSelectedProcessId: (id: string | null) => void;
}) {
  const { processes, connected: listConnected } = useProcessListStream();
  const { launch, cancel, dismiss } = useProcessActions();
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

In `AppContent`, add the lifted state + the two callbacks, and pass them to `OptioProvider` and `Dashboard`. Add to the top of `AppContent` (after line 55, `const [selectedKey, setSelectedKey] = useState<string | null>(null);`):

```typescript
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);

  // Initiator-scoped attention: navigate to the process that asked for it.
  const onAttention = (processId: string, reason: string) => {
    setSelectedProcessId(processId);
    notification.info({ message: 'A task needs your attention', description: reason });
  };
  // Domain messages: surface to the console (apps can do richer handling).
  const onDomainMessage = (processId: string, keyword: string, data: unknown) => {
    // eslint-disable-next-line no-console
    console.log('[optio domain_message]', { processId, keyword, data });
    notification.info({ message: `Domain message: ${keyword}`, description: JSON.stringify(data) });
  };
```

Add `notification` to the antd import (line 2):

```typescript
import { Alert, Button, Layout, Select, Typography, notification } from 'antd';
```

Change the `OptioProvider` open tag (line 110) to pass the callbacks, and the `<Dashboard />` usage (line 116) to pass the selection props:

```typescript
    <OptioProvider
      prefix={selected.prefix}
      database={selected.database}
      live={selected.live}
      onAttention={onAttention}
      onDomainMessage={onDomainMessage}
    >
      <Layout style={{ height: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
          {headerRight}
        </Header>
        <Dashboard selectedProcessId={selectedProcessId} setSelectedProcessId={setSelectedProcessId} />
      </Layout>
    </OptioProvider>
```

(The `useState` import is already present at line 1.)

---

### Task 10: VERIFY + COMMIT (the only task that runs anything)

**Files:** none edited by hand. Runs `make codegen` which regenerates `packages/optio-api/src/_generated/optio-engine.ts` and `packages/optio-core/src/optio_core/_generated/optio_engine.py` from the Task 1 contract.

This task assumes Tasks 1–9 have all landed. Run from the repo root `/home/csillag/deai/optio`.

- [ ] **Step 1: Regenerate the clamator stubs from the contract.**

Run: `make codegen`
Expected: regenerates the two `_generated` dirs with `sessionId`/`session_id` on `LaunchParams`; exits 0.

- [ ] **Step 2: Install (TS workspace + editable Python packages into the repo venv).**

Run: `make install`
Expected: pnpm install + `pip install -e` for `optio-core optio-host optio-agents optio-opencode` into `.venv`; exits 0. (optio-demo is installed via its own target if needed — see Step 6.)

- [ ] **Step 3: Run the optio-contracts TS tests + type-check.**

Run: `cd packages/optio-contracts && ../../node_modules/.bin/vitest run && ../../node_modules/.bin/tsc --noEmit`
Expected: all tests pass (incl. `session-events-schema.test.ts` and the new ProcessSchema cases); tsc clean.

- [ ] **Step 4: Run the optio-core pytest suite.**

Run: `cd packages/optio-core && ../../.venv/bin/python -m pytest`
Expected: all tests pass, including `test_client_directed_events.py` and every updated launch call site (no `TypeError: missing required argument 'session_id'`, no `LaunchParams` validation errors).

- [ ] **Step 5: Run the optio-agents pytest suite.**

Run: `cd packages/optio-agents && ../../.venv/bin/python -m pytest`
Expected: all tests pass, including `test_protocol_parser.py` (new cases), `test_prompt.py`, `test_client_directed_dispatch.py`, `test_browser_capture.py`, and `test_package_exports.py`.

- [ ] **Step 6: Run the optio-demo import/smoke check.**

Run: `cd packages/optio-demo && ../../.venv/bin/pip install -e . >/dev/null 2>&1; ../../.venv/bin/python -c "from optio_demo.tasks import get_task_definitions; import asyncio; print(len(asyncio.get_event_loop().run_until_complete(get_task_definitions({}))))"`
Expected: prints a process count that includes the 4 new tasks (no import errors from `client_directed.py`).

- [ ] **Step 7: Build + type-check optio-api.**

Run: `cd packages/optio-api && ../../node_modules/.bin/tsc --noEmit && ../../node_modules/.bin/vitest run`
Expected: tsc clean (the regenerated `engine.launch` requires `sessionId`, which `handlers.launchProcess` supplies); vitest passes including `stream-poller.test.ts` (new browserOpenRequests cases) and `session-events-poller.test.ts`. (These vitest suites need a Mongo per the test header `MONGO_URL`; use Docker/`mongodb-memory-server` per repo convention — set `MONGO_URL` if not default.)

- [ ] **Step 8: Build + type-check optio-ui.**

Run: `cd packages/optio-ui && ../../node_modules/.bin/tsc --noEmit && ../../node_modules/.bin/vitest run`
Expected: tsc clean; vitest passes including `browserOpen.test.tsx` and `sessionEvents.test.tsx`.

- [ ] **Step 9: Build + type-check optio-dashboard.**

Run: `cd packages/optio-dashboard && ../../node_modules/.bin/tsc --noEmit`
Expected: tsc clean (App.tsx compiles with the new OptioProvider props + lifted state).

- [ ] **Step 10: Run the no-direct-writes lint.**

Run: `make lint-no-direct-writes`
Expected: `OK: no direct Mongo writes in packages/optio-api/src/`. (The new session-events poller only reads — `find`/`project`.)

- [ ] **Step 11: Grep checks (spec-coverage sanity).**

Run each; confirm the described output:

```bash
# Three ctx methods present
grep -n "def request_browser_open\|def need_attention\|def domain_message" packages/optio-core/src/optio_core/context.py
# Two store helpers present
grep -n "def append_browser_open_request\|def append_session_event" packages/optio-core/src/optio_core/store.py
# Three parser events + regexes
grep -n "_RE_BROWSER\|_RE_ATTENTION\|_RE_DOMAIN_MESSAGE" packages/optio-agents/src/optio_agents/protocol/parser.py
# browserOpenRequests added to all three pollers (expect 3 payload hits + 3 snapshot hits)
grep -c "browserOpenRequests" packages/optio-api/src/stream-poller.ts
# session-events route + poller
grep -n "createSessionEventsPoller\|/api/session-events/stream" packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/stream-poller.ts
# UI handler wired into all 3 feeds
grep -rn "handleBrowserOpenRequests" packages/optio-ui/src/hooks packages/optio-ui/src/context
# sessionStorage key + resetSession
grep -n "optioSessionId\|resetSession" packages/optio-ui/src/session/sessionEvents.ts
```

Expected: each grep returns the matching lines; `grep -c "browserOpenRequests" .../stream-poller.ts` returns at least 7 (6 poller occurrences + the interface/new poller; ≥6 is the floor).

- [ ] **Step 12: Commit.**

Confirm a feature branch is checked out (the prompt states `feat/optio-browser-open`); if on `main`, branch first. Stage exactly the files touched by Tasks 1–9 plus the regenerated `_generated` dirs, and commit. **No `Co-Authored-By` trailer** (per repo memory).

```bash
git add packages/optio-contracts packages/optio-core packages/optio-agents \
        packages/optio-api packages/optio-ui packages/optio-demo \
        packages/optio-dashboard
git commit -m "feat(optio): client-directed events (browser_open, need_attention, domain_message)

Three running-task→client capabilities folded into the process status doc and
delivered over SSE (engine owns writes; optio-api stays read-only). Adds an
opaque client-minted sessionId (required launch param) recorded as
originatingSessionId, an always-on session-events SSE, and three new
agent-emittable optio.log keywords (BROWSER:/ATTENTION:/DOMAIN_MESSAGE:)."
```

(If splitting into multiple commits is preferred, group by package; either way no co-author trailer.)

---

## Self-Review

**Spec coverage:**
- optio-core: ctx methods + store helpers + `session_id` threading + `originatingSessionId` + child inheritance + `_engine_service` → **T2** (+ wire keys). ✅
- optio-agents parser/session/prompt (3 events, regexes, dispatch, SSOT, malformed-drop) → **T3**. ✅
- optio-agents `browser_capture.enable` (5 shims, env additions) → **T4**. ✅
- optio-contracts (ProcessSchema fields, launch `sessionId`, session-events contract) → **T1**. ✅
- optio-api (3 pollers + browserOpenRequests in payload AND comparison snapshot, session-events poller + route, launch forwards sessionId, read-only) → **T5**. ✅
- optio-ui browser-open shared handler in all 3 feeds → **T6**; session-events manager + sessionId lifecycle + provider callbacks + launch attaches sessionId + exports → **T7**. ✅
- optio-demo 4 tasks (repo open; capture bridge; attention; domain) → **T8**. ✅
- optio-dashboard onAttention(navigate)/onDomainMessage → **T9**. ✅
- Verification + codegen + commit → **T10**. ✅

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to". Every code step shows real code grounded in the read source.

**Type/name consistency:** ctx methods `request_browser_open`/`need_attention`/`domain_message`; store `append_browser_open_request`/`append_session_event` (uuid4().hex, $push); doc fields `browserOpenRequests`/`sessionEvents`/`originatingSessionId`; launch `session_id` required-no-default threaded `lifecycle.launch`→`launch_process`→`_execute_process`→`ProcessContext(session_id)` with child inheritance via `parent_ctx.session_id`; `_engine_service` passes `session_id=params.session_id`; parser `BrowserEvent`/`AttentionEvent`/`DomainMessageEvent` + the three pinned regexes; SSOT constant uses the **real** name `LOG_CHANNEL_PROMPT`; capture `browser_capture.enable(host)->dict`; api `createSessionEventsPoller` + `/api/session-events/stream?sessionId=`; ui `handleBrowserOpenRequests` + sessionStorage `"optioSessionId"` + `resetSession`. All consistent across tasks.

**Known deviation from pinned names:** the spec's `LOG_CHANNEL_PROTOCOL` is the only pinned name that does not exist in the codebase; the real export is `LOG_CHANNEL_PROMPT`, used throughout this plan (see the parallel-execution note above).
