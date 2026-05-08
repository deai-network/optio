# 2026-05-08 — Engine RPC migration

**Status:** Design.
**Companion:** `docs/2026-05-08-more-rpc-cleanup-todo.md` (seed for follow-up cleanup of polling-based confirmation patterns).
**Depends on:** `clamator` library, design at `~/deai/clamator/docs/2026-05-07-clamator-design.md`.

## 1. Goals

### Primary goal

Replace the current API↔engine command path. Today the API publishes fire-and-forget redis `xadd` messages on `${db}/${prefix}:commands`; the engine consumes them; the engine has no way to reply. The API guesses outcomes locally. The new path uses clamator RPC over redis. The API calls an engine method, the engine validates and acts, the engine returns a typed result, and the API forwards the result to the HTTP caller. Round-trip with a real reply.

### Why this matters

Without a reply channel, the API cannot know whether the engine accepted the command, rejected it, or what new state followed. Today the system has two coping mechanisms:

1. **The API replicates the engine's state-machine logic locally.** `LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES`, plus `cancellable` and `supportsResume` checks in `packages/optio-api/src/handlers.ts`. The API guesses the outcome and returns 409 pre-publish.
2. **The system polls the read path to confirm the command landed.** The API runs a 1-second `setInterval` poller in `stream-poller.ts` reading MongoDB. The UI re-fetches on mutation success and on 5-second `refetchInterval`. The stale 200 response body becomes correct via subsequent SSE / refetch updates.

This is the best the system can do without RPC. Costs: drift risk (the engine evolves, the API silently lags), stale 200 (the response body is pre-command, not post), authority leak (decisions made in the wrong place), and unavoidable 1-second-plus latency before the UI reflects committed state.

RPC removes the gap. The engine replies; the API forwards a real result; none of the above coping mechanisms are needed for command-outcome confirmation.

### Follow-up cleanup (enabled by RPC, included in this same migration)

Once RPC exists, code that exists only because RPC didn't can come out:

- State allowlists in `packages/optio-api/src/handlers.ts`.
- `cancellable` and `supportsResume` precondition checks in the API.
- `packages/optio-api/src/publisher.ts` xadd helpers.
- The pre-command DB read in API command handlers (the engine reads inside its RPC handler instead).
- The legacy `${db}/${prefix}:commands` consumer in `optio-core` and the `on_command(...)` extension hook.

The same migration codifies the architectural rule that this cleanup makes true:

> **Engine owns all writes. The API server reads MongoDB directly for queries (REST GETs, SSE streams, widget proxy) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. The engine is the single source of truth for what commands are allowed and what state results.**

### Non-goals

- No change to the read path. REST GETs, SSE streams, and the widget proxy continue reading MongoDB directly. This is allowed by the rule and already documented in `packages/optio-core/README.md`.
- No change to `optio-opencode` (in-engine task type, in-process writes).
- No change to `optio-host` (no DB access).
- No new clamator features. clamator v0.1 as designed.

## 2. End state: file layout

After full migration:

```
packages/optio-contracts/src/
├── schemas/
│   ├── common.ts                       # generic primitives (ObjectId, Pagination, Error)
│   └── process.ts                      # process-domain types (Process, ProcessState, LogEntry, ProcessMetadataFilter)
├── api-to-frontend.ts                  # renamed from contract.ts — ts-rest HTTP contract
└── engine-to-api.ts                    # NEW — clamator engine RPC contract

packages/optio-api/src/
├── _generated/
│   └── engine.ts                       # NEW — clamator codegen output (committed)
├── handlers.ts                         # rewritten — thin RPC translators, no validation, no DB read for commands
├── engine-cache.ts                     # NEW — framework-agnostic EngineClient cache + lifecycle
├── adapters/
│   ├── express.ts                      # updated — uses engine-cache, framework-specific shutdown hook + return shape
│   ├── fastify.ts                      # updated
│   ├── nextjs-app.ts                   # updated
│   └── nextjs-pages.ts                 # updated
├── stream-poller.ts                    # unchanged (read-path SSE)
├── widget-proxy-core.ts                # unchanged
├── discovery.ts                        # unchanged
├── process-id-resolver.ts              # kept for query-side use; command-side calls removed
├── publisher.ts                        # DELETED
├── auth.ts                             # unchanged
├── metadata-filter-query.ts            # unchanged
└── index.ts                            # updated exports

packages/optio-core/src/optio_core/
├── _generated/
│   └── engine.py                       # NEW — clamator codegen output (committed)
├── _engine_service.py                  # NEW — EngineService impl
├── lifecycle.py                        # updated — init() creates RedisRpcServer, exposes optio_core.rpc_server
├── _command_consumer.py                # DELETED in phase 5
├── store.py                            # unchanged
├── migrations/                         # unchanged
└── … (other existing files unchanged)

Repo root:
├── Makefile                            # NEW — help, install, build, codegen, test, test-interop, lint, clean, clean-codegen, clean-deep
└── docs/
    ├── 2026-05-08-engine-rpc-migration-design.md   # this spec
    └── 2026-05-08-more-rpc-cleanup-todo.md         # companion seed
```

### Generated files

- Both `_generated/` directories are committed. A pre-commit hook re-runs `make codegen` and fails on `git diff` non-empty.
- Every generated file carries `// AUTO-GENERATED by @clamator/codegen … DO NOT EDIT.` (comment syntax adjusted per language).

### Package boundary changes

- `optio-contracts` hosts two contracts (HTTP REST and clamator RPC). Naming convention: `<server>-to-<client>.ts`, where the server side is the one that exposes the contract and the client side is the caller. Convention documented in `packages/optio-contracts/AGENTS.md`.
- `optio-api`'s exported `publishLaunch` and `publishResync` are removed. `registerOptioApi` (and the Next.js equivalents) now return a handle exposing the internally-created `EngineClient`(s) for sharing with non-HTTP code paths:
  - Single-db mode: `const { engine } = registerOptioApi(...)`.
  - Multi-db mode: `const { getEngine } = registerOptioApi(...); const engine = getEngine(database, prefix)`.
  `OptioApiOptions` gains no new fields. `publisher.ts` is deleted.
- `optio-core` exposes a new public attribute `optio_core.rpc_server` (clamator `RedisRpcServer`) after `init()`. Apps register additional services on it before `run()`. This replaces `on_command(type, handler)`, which is removed in phase 5.

### What stays untouched

- Read path: REST GETs, SSE pollers, widget proxy, discovery — all continue reading MongoDB and redis heartbeats directly.
- `optio-opencode`, `optio-host`, `optio-dashboard` — no changes.
- `optio-ui` — no logic change beyond verifying any direct `contract.ts` filename references (entry-point exports unchanged).

## 3. Contract design (`engine-to-api.ts`)

A single Zod source. All engine RPC methods. Discriminated-union results carry typed failure reasons. Failure-reason enums are exported so `api-to-frontend.ts` can reuse them in error response schemas.

### Imports

```typescript
import { z } from 'zod';
import { defineContract, defineMethod, defineNotification } from '@clamator/protocol';
import { ProcessSchema, ProcessMetadataFilterSchema } from './schemas/process.js';
```

### Process identifier

The engine accepts either an `_id` (ObjectId hex) or a `processId` (app-defined string). It resolves internally. The API passes the raw URL parameter through.

```typescript
const ProcessIdParam = z.string().min(1);  // either ObjectId hex or processId; engine resolves
```

### Reason enums (exported for reuse)

```typescript
export const LaunchFailureReason = z.enum([
  'not-found',
  'not-launchable',         // current state not in {idle, done, failed, cancelled}
  'no-resume-support',      // resume=true but supportsResume=false
  'launch-blocked',         // matched a persistent block_launches filter
]);

export const CancelFailureReason = z.enum([
  'not-found',
  'not-cancellable',        // proc.cancellable=false OR state not in {scheduled, running}
]);

export const DismissFailureReason = z.enum([
  'not-found',
  'not-dismissable',        // state not in {done, failed, cancelled}
]);

export const GroupCancelFailureReason = z.enum([
  'invalid-persist-without-block',  // persist=true requires blockNewLaunches=true
]);

export const BlockLaunchesFailureReason = z.enum([
  'invalid-filter',         // reserved
]);

export type LaunchFailureReason = z.infer<typeof LaunchFailureReason>;
export type CancelFailureReason = z.infer<typeof CancelFailureReason>;
export type DismissFailureReason = z.infer<typeof DismissFailureReason>;
```

### Result schemas

```typescript
const launchResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: LaunchFailureReason }),
]);

const cancelResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: CancelFailureReason }),
]);

const dismissResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), process: ProcessSchema }),
  z.object({ ok: z.literal(false), reason: DismissFailureReason }),
]);

const groupCancelResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), cancelledCount: z.number().int().nonnegative() }),
  z.object({ ok: z.literal(false), reason: GroupCancelFailureReason }),
]);

const groupCancelAndWaitResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true), cancelledCount: z.number().int().nonnegative() }),
  z.object({ ok: z.literal(false), reason: GroupCancelFailureReason }),
]);

const blockLaunchesResult = z.discriminatedUnion('ok', [
  z.object({ ok: z.literal(true) }),
  z.object({ ok: z.literal(false), reason: BlockLaunchesFailureReason }),
]);

const unblockLaunchesResult = z.object({
  removed: z.number().int().nonnegative(),
});
```

### Contract definition

```typescript
export const engineContract = defineContract('engine', {

  // Per-process commands
  launch: defineMethod({
    params: z.object({
      processId: ProcessIdParam,
      resume: z.boolean().optional(),
    }),
    result: launchResult,
  }),

  cancel: defineMethod({
    params: z.object({ processId: ProcessIdParam }),
    result: cancelResult,
  }),

  dismiss: defineMethod({
    params: z.object({ processId: ProcessIdParam }),
    result: dismissResult,
  }),

  // Group commands
  groupCancel: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
    }),
    result: groupCancelResult,
  }),

  groupCancelAndWait: defineMethod({
    params: z.object({
      metadataFilter: ProcessMetadataFilterSchema,
      blockNewLaunches: z.boolean().optional(),
      persist: z.boolean().optional(),
      reason: z.string().optional(),
    }),
    result: groupCancelAndWaitResult,
  }),

  // Launch blocks (persistent variant only — async-CM form stays Python-only)
  blockLaunches: defineMethod({
    params: z.object({
      launchFilter: ProcessMetadataFilterSchema,
      reason: z.string().optional(),
    }),
    result: blockLaunchesResult,
  }),

  unblockLaunches: defineMethod({
    params: z.object({ launchFilter: ProcessMetadataFilterSchema }),
    result: unblockLaunchesResult,
  }),

  // Notifications (fire-and-forget)
  resync: defineNotification({
    params: z.object({
      clean: z.boolean().optional(),
      metadataFilter: ProcessMetadataFilterSchema.optional(),
    }),
  }),

});
```

`engineContract` is consumed via the `optio-contracts/engine-to-api` subpath export only. `packages/optio-contracts/src/index.ts` re-exports failure-reason enums (`LaunchFailureReason`, `CancelFailureReason`, `DismissFailureReason`, `GroupCancelFailureReason`, `BlockLaunchesFailureReason`) for direct import by consumers, but does not re-export `engineContract` itself.

### Design notes

- The `{ ok: true | false }` envelope is universal across methods that can fail with a typed reason. Even single-failure-mode methods carry it for shape consistency.
- `groupCancelAndWait` is a method, not a notification — the caller wants confirmation that all matching processes terminated. Long-running; per-call timeout override expected (clamator `RpcClientCore.call` accepts a timeout). See open question in §9.
- `resync` is a notification. The API returns `202 Accepted` with `{ message: 'Resync requested' }`.
- `launch-blocked` is a distinct `LaunchFailureReason` because persistent launch blocks reject launches via `LaunchBlocked` exceptions today; the API needs to surface this distinctly from `not-launchable` so the frontend can show appropriate UI.
- The API never checks `cancellable` directly. If `proc.cancellable=false`, the engine returns `{ ok: false, reason: 'not-cancellable' }`.
- Discriminated unions codegen cleanly to Pydantic `Field(discriminator='ok')` unions. Both languages get exhaustive switch coverage.

### Failure-reason enums and `api-to-frontend.ts` reuse

The five failure-reason enums (`LaunchFailureReason`, `CancelFailureReason`, `DismissFailureReason`, `GroupCancelFailureReason`, `BlockLaunchesFailureReason`) live in `packages/optio-contracts/src/engine-failure-reasons.ts` and import only `zod`. `engine-to-api.ts` imports them from there to use as discriminator values inside the result schemas. `packages/optio-contracts/src/index.ts` re-exports them from `engine-failure-reasons.ts`, so external consumers (`optio-api/src/handlers.ts`, `optio-ui` error UI, custom adapters) import them from the package root: `import { LaunchFailureReason } from 'optio-contracts'`. The split exists so browser bundles can re-export the enums without pulling in `@clamator/protocol` (which uses `node:crypto` and is Node-only). The package root does not re-export `engineContract` (Q9).

`api-to-frontend.ts` (the ts-rest HTTP contract) does not import the failure-reason enums in phase 1; the new `LaunchErrorBody` / `CancelErrorBody` / `DismissErrorBody` response schemas — which would consume the enums — land in phase 4 alongside the handler rewrite.

```typescript
import {
  LaunchFailureReason,
  CancelFailureReason,
  DismissFailureReason,
} from './engine-failure-reasons.js';

const LaunchErrorBody  = z.object({ reason: LaunchFailureReason,  message: z.string() });
const CancelErrorBody  = z.object({ reason: CancelFailureReason,  message: z.string() });
const DismissErrorBody = z.object({ reason: DismissFailureReason, message: z.string() });
```

REST responses use these as 404 / 409 bodies. The reason `'not-found'` maps to HTTP 404; everything else maps to HTTP 409. The mapping table is a small private const in `packages/optio-api/src/handlers.ts`.

### Out of contract (engine internals, no RPC exposure)

- `optio_core.adhoc_define`, `optio_core.adhoc_delete` — not exposed via REST today; stay Python-only.
- `optio_core.get_process`, `optio_core.list_processes` — read-only; the API reads MongoDB directly.
- `optio_core.run`, `optio_core.shutdown` — engine lifecycle.
- The async-CM form of `optio_core.block_launches(...)` — the CM cannot span the RPC wire; only the persistent variant is exposed.
- `optio_core.on_command` — removed in phase 5; replaced by `optio_core.rpc_server.register_service(...)`.

## 4. Engine-side changes

### `init()` signature

Adds an `rpc_server` override for tests. Keeps `redis_url` for production.

```python
async def init(
    self,
    mongo_db: AsyncIOMotorDatabase,
    prefix: str = "optio",
    redis_url: str | None = None,             # production: optio creates RedisRpcServer internally
    rpc_server: RpcServerCore | None = None,  # tests: app provides MemoryRpcServer or pre-built
    services: dict[str, Any] | None = None,
    get_task_definitions: ... = None,
    cancel_grace_seconds: float = 5.0,
) -> None:
```

`redis_url` and `rpc_server` are mutually exclusive. Both absent means no RPC (today's no-Redis mode preserved).

### Server construction (production path)

```python
if redis_url:
    self._redis = Redis.from_url(redis_url)
    self._owned_rpc_server = True
    self.rpc_server = RedisRpcServer(
        redis=self._redis,
        key_prefix=f"{mongo_db.name}/{prefix}",
        # other knobs default per clamator §8
    )
    self._engine_service = EngineService(self)
    self.rpc_server.register_service(engine_contract, self._engine_service)

    # Phases 2-4: legacy consumer co-exists. Phase 5: deleted.
    self._consumer = CommandConsumer(self._redis, f"{mongo_db.name}/{prefix}:commands")
    self._consumer.on("launch", self._handle_launch)
    self._consumer.on("cancel", self._handle_cancel)
    self._consumer.on("dismiss", self._handle_dismiss)
    self._consumer.on("resync", self._handle_resync)
    await self._consumer.setup()
```

### Server construction (tests / app-provided)

```python
if rpc_server is not None:
    self._owned_rpc_server = False
    self.rpc_server = rpc_server
    self._engine_service = EngineService(self)
    self.rpc_server.register_service(engine_contract, self._engine_service)
    # No CommandConsumer in test/app-provided mode.
```

### Public surface added to `Optio`

```python
self.rpc_server: RpcServerCore | None = None
```

Apps access it as `optio_core.rpc_server` (the existing module-level singleton pattern). Apps register additional services before `run()`:

```python
await optio_core.init(mongo_db=db, redis_url=URL, prefix='myapp')
optio_core.rpc_server.register_service(domain_contract, MyDomainService())
await optio_core.run()
```

### `EngineService` implementation

New file: `packages/optio-core/src/optio_core/_engine_service.py`. Imports the codegenned ABC and Pydantic models.

```python
from optio_core._generated.engine import (
    EngineService as EngineServiceBase,
    LaunchParams, LaunchResult,
    CancelParams, CancelResult,
    DismissParams, DismissResult,
    GroupCancelParams, GroupCancelResult,
    GroupCancelAndWaitParams, GroupCancelAndWaitResult,
    BlockLaunchesParams, BlockLaunchesResult,
    UnblockLaunchesParams, UnblockLaunchesResult,
    ResyncParams,
)


class EngineService(EngineServiceBase):
    def __init__(self, optio: 'Optio') -> None:
        self._optio = optio

    async def launch(self, params: LaunchParams) -> LaunchResult:
        proc = await self._resolve(params.process_id)
        if proc is None:
            return LaunchResult(ok=False, reason='not-found')
        if proc['status']['state'] not in LAUNCHABLE_STATES:
            return LaunchResult(ok=False, reason='not-launchable')
        if params.resume and not proc.get('supportsResume', False):
            return LaunchResult(ok=False, reason='no-resume-support')

        try:
            await self._optio.launch(proc['processId'], resume=params.resume or False)
        except LaunchBlocked:
            return LaunchResult(ok=False, reason='launch-blocked')

        # Re-read post-mutation; the engine has updated state to scheduled.
        updated = await store.get_process_by_processid(...)
        return LaunchResult(ok=True, process=_to_process_schema(updated))

    async def cancel(self, params: CancelParams) -> CancelResult:
        proc = await self._resolve(params.process_id)
        if proc is None:
            return CancelResult(ok=False, reason='not-found')
        if not proc.get('cancellable', True) or proc['status']['state'] not in CANCELLABLE_STATES:
            return CancelResult(ok=False, reason='not-cancellable')
        await self._optio.cancel(proc['processId'])
        updated = ...
        return CancelResult(ok=True, process=_to_process_schema(updated))

    async def dismiss(self, params: DismissParams) -> DismissResult:
        proc = await self._resolve(params.process_id)
        if proc is None:
            return DismissResult(ok=False, reason='not-found')
        if proc['status']['state'] not in DISMISSABLE_STATES:
            return DismissResult(ok=False, reason='not-dismissable')
        await self._optio.dismiss(proc['processId'])
        updated = ...
        return DismissResult(ok=True, process=_to_process_schema(updated))

    async def group_cancel(self, params: GroupCancelParams) -> GroupCancelResult:
        if params.persist and not params.block_new_launches:
            return GroupCancelResult(ok=False, reason='invalid-persist-without-block')
        count = await self._optio.group_cancel(
            metadata_filter=params.metadata_filter,
            block_new_launches=params.block_new_launches or False,
            persist=params.persist or False,
            reason=params.reason,
        )
        return GroupCancelResult(ok=True, cancelled_count=count)

    async def group_cancel_and_wait(self, params: GroupCancelAndWaitParams) -> GroupCancelAndWaitResult:
        if params.persist and not params.block_new_launches:
            return GroupCancelAndWaitResult(ok=False, reason='invalid-persist-without-block')
        count = await self._optio.group_cancel_and_wait(...)
        return GroupCancelAndWaitResult(ok=True, cancelled_count=count)

    async def block_launches(self, params: BlockLaunchesParams) -> BlockLaunchesResult:
        coll = _launch_block_store.collection(
            self._optio._config.mongo_db,
            self._optio._config.prefix,
        )
        await _launch_block_store.upsert_block(coll, params.launch_filter, params.reason)
        await self._optio._load_persisted_blocks()
        return BlockLaunchesResult(ok=True)

    async def unblock_launches(self, params: UnblockLaunchesParams) -> UnblockLaunchesResult:
        removed = await self._optio.unblock_launches(params.launch_filter)
        return UnblockLaunchesResult(removed=removed)

    async def resync(self, params: ResyncParams) -> None:
        await self._optio.resync(clean=params.clean or False, metadata_filter=params.metadata_filter)

    async def _resolve(self, id_str: str) -> dict | None:
        """Accept either ObjectId hex or processId string. Return the doc or None."""
        coll = self._optio._config.mongo_db[f"{self._optio._config.prefix}_processes"]
        if _is_objectid(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str})
```

### Idempotency notes (clamator at-least-once delivery)

clamator over-redis can redeliver messages whose owner went idle (see clamator §8 "Crash recovery"). Engine handlers must be idempotent.

- **`launch`**: today's `optio_core.launch()` checks state; the `LAUNCHABLE_STATES` allowlist plus the `LaunchBlocked` raise mean a redelivered launch on an already-running process becomes `not-launchable`. Acceptable.
- **`cancel`**: similar — already-cancelled or already-cancelling produces `not-cancellable`. Acceptable.
- **`dismiss`**: idle process → `not-dismissable`. Acceptable.
- **`group_cancel*`**: filters select the active set; redelivery operates on whatever is active at the second delivery. Net effect bounded.
- **`block_launches` (persistent)**: `upsert_block` is idempotent by filter equality (existing `_launch_block_store.py` uses `update_one` upsert). Re-delivery is a no-op.
- **`unblock_launches`**: redelivery on an already-unblocked filter returns 0 removed. Acceptable.
- **`resync` (notification)**: clamator does not retry notifications — fire-and-forget, no PEL entry. Single delivery. Already idempotent.

### Lifecycle integration

```python
async def run(self) -> None:
    if self.rpc_server:
        await self.rpc_server.start()
    if self._consumer:
        await self._consumer.start()  # legacy; phases 2-4 only
    # ... existing scheduler / supervisor startup ...

async def shutdown(self, grace_seconds: float = 5.0) -> None:
    # ... existing graceful task shutdown ...
    if self._consumer:
        await self._consumer.stop()
    if self.rpc_server and self._owned_rpc_server:
        await self.rpc_server.stop(grace_ms=int(grace_seconds * 1000))
    if self._redis:
        await self._redis.aclose()
```

App-provided `rpc_server` (test mode) is the app's responsibility to start and stop.

### Phase 5 deletions

- `_command_consumer.py` (entire file).
- `_handle_launch`, `_handle_cancel`, `_handle_dismiss`, `_handle_resync` if their only call site was the consumer dispatch (the inner methods they delegate to — `optio.launch()` etc. — stay).
- `on_command(...)` public method on `Optio`.
- The `optio_core.on_command` re-export.

## 5. API-side changes

### Handlers (`packages/optio-api/src/handlers.ts`) — full rewrite

Read handlers (`listProcesses`, `getProcess`, `getProcessTree`, `getProcessLog`, `getProcessTreeLog`) stay. Command handlers become thin RPC translators.

```typescript
import type { EngineClient } from './_generated/engine.js';
import type {
  LaunchFailureReason,
  CancelFailureReason,
  DismissFailureReason,
} from 'optio-contracts';

export type CommandResult =
  | { status: 200; body: any }
  | { status: 404; body: { reason: string; message: string } }
  | { status: 409; body: { reason: string; message: string } };

const LAUNCH_STATUS: Record<LaunchFailureReason, 404 | 409> = {
  'not-found':         404,
  'not-launchable':    409,
  'no-resume-support': 409,
  'launch-blocked':    409,
};

const CANCEL_STATUS: Record<CancelFailureReason, 404 | 409> = {
  'not-found':       404,
  'not-cancellable': 409,
};

const DISMISS_STATUS: Record<DismissFailureReason, 404 | 409> = {
  'not-found':       404,
  'not-dismissable': 409,
};

const MESSAGES: Record<string, string> = {
  'not-found':         'Process not found',
  'not-launchable':    'Process is not in a launchable state',
  'no-resume-support': 'This task does not support resume',
  'launch-blocked':    'Launches matching this filter are currently blocked',
  'not-cancellable':   'Process is not cancellable in its current state',
  'not-dismissable':   'Process is not in a dismissable state',
};

export async function launchProcess(
  engine: EngineClient, id: string, resume: boolean = false,
): Promise<CommandResult> {
  const result = await engine.launch({ processId: id, resume });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return {
    status: LAUNCH_STATUS[result.reason],
    body:   { reason: result.reason, message: MESSAGES[result.reason] },
  };
}

export async function cancelProcess(
  engine: EngineClient, id: string,
): Promise<CommandResult> {
  const result = await engine.cancel({ processId: id });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return {
    status: CANCEL_STATUS[result.reason],
    body:   { reason: result.reason, message: MESSAGES[result.reason] },
  };
}

export async function dismissProcess(
  engine: EngineClient, id: string,
): Promise<CommandResult> {
  const result = await engine.dismiss({ processId: id });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return {
    status: DISMISS_STATUS[result.reason],
    body:   { reason: result.reason, message: MESSAGES[result.reason] },
  };
}

export async function resyncProcesses(
  engine: EngineClient, clean: boolean = false, metadataFilter?: ProcessMetadataFilter,
): Promise<{ message: string }> {
  await engine.resync({ clean, metadataFilter });   // notification, returns void
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
```

### What goes away

- `LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES` constants in `handlers.ts`.
- `cancellable` and `supportsResume` precondition checks.
- The pre-RPC DB read via `findProcessByEitherId` in command handlers — the engine resolves both id forms.
- `publisher.ts` — entire file deleted.
- `OptioApiOptions.redis` is still passed in (still needed by `discovery.ts` for `redis.exists` heartbeat checks), but is no longer threaded through to command handlers. Adapters construct the `EngineClient` from it.

`process-id-resolver.ts` is kept — it still has callers in the read-path query handlers.

### Handler signature changes

```typescript
// before
async function launchProcess(db: Db, redis: Redis, database: string, prefix: string,
                             id: string, resume?: boolean): Promise<CommandResult>

// after
async function launchProcess(engine: EngineClient,
                             id: string, resume?: boolean): Promise<CommandResult>
```

`cancelProcess`, `dismissProcess`, `resyncProcesses` similarly drop `db`, `redis`, `database`, `prefix`. The engine carries all of that internally via its keyPrefix-bound RPC client.

### Engine cache (shared, framework-agnostic)

The cache that maps `(database, prefix)` to a long-lived `EngineClient` belongs in a framework-agnostic module so all four adapters consume the same logic. New file: `packages/optio-api/src/engine-cache.ts`.

**Consumer dep note (zod).** `@clamator/protocol` declares `zod` as a `peerDependency`. Any package consuming the codegenned `_generated/engine.ts` (so: `optio-api` here, plus any external adapter) must declare a compatible `zod` (currently `^3.x`) in its own `dependencies` even if it does not import `zod` directly. Without it, pnpm/npm can resolve the peer requirement against a different physical zod copy than `optio-contracts` uses, and TypeScript will reject the generated discriminated-union types with hard-to-read dual-instance errors. See `@clamator/protocol`'s README for the canonical statement of this requirement. Phase 1 added the necessary `"zod": "^3"` declaration to `packages/optio-api/package.json`.

```typescript
import type { Redis } from 'ioredis';
import { RedisRpcClient } from '@clamator/over-redis';
import { EngineClient } from './_generated/engine.js';

export interface EngineCache {
  get(database: string, prefix: string): EngineClient;
  closeAll(): Promise<void>;
}

export function createEngineCache(redis: Redis): EngineCache {
  const map = new Map<string, EngineClient>();

  return {
    get(database, prefix) {
      const key = `${database}/${prefix}`;
      let engine = map.get(key);
      if (!engine) {
        engine = new EngineClient(new RedisRpcClient({ redis, keyPrefix: key }));
        engine.start();
        map.set(key, engine);
      }
      return engine;
    },

    async closeAll() {
      await Promise.all([...map.values()].map(e => e.stop()));
      map.clear();
    },
  };
}
```

The cache owns the `EngineClient` lifecycle (`start()` on lazy create, `stop()` via `closeAll()`). It does not know about HTTP frameworks, request shapes, or shutdown semantics. Each adapter consumes it identically.

### Adapter updates

Each adapter (`express.ts`, `fastify.ts`, `nextjs-app.ts`, `nextjs-pages.ts`) does only the framework-specific work:

1. On registration: instantiate the shared cache via `createEngineCache(opts.redis)`.
2. Per command-endpoint request: resolve `(db, database, prefix)` via `resolveDb`, look up the engine via `cache.get(database, prefix)`, call the handler with the engine.
3. Wire the framework's shutdown hook to call `cache.closeAll()`.
4. Return value: single-db mode returns `{ engine }`; multi-db mode returns `{ getEngine }`.

Sketch (fastify):

```typescript
import { createEngineCache } from '../engine-cache.js';

export function registerOptioApi(app: FastifyInstance, opts: OptioApiOptions) {
  const cache = createEngineCache(opts.redis);

  // ... route registration; command-route bodies call e.g.:
  //   const { database, prefix } = resolveDb(opts, query);
  //   const result = await launchProcess(cache.get(database, prefix), params.id, body?.resume);

  app.addHook('onClose', () => cache.closeAll());

  if ('db' in opts && opts.db) {
    const prefix = opts.prefix ?? 'optio';
    return { engine: cache.get(opts.db.databaseName, prefix) };
  }
  return { getEngine: cache.get.bind(cache) };
}
```

Per-adapter shutdown wiring:

- **Fastify:** `app.addHook('onClose', () => cache.closeAll())`.
- **Express:** no built-in close hook; expose `closeAll` on the return value (e.g. `{ engine, closeAll }` or `{ getEngine, closeAll }`) for the caller to invoke from their `server.close` callback.
- **Next.js (both adapters):** no framework lifecycle hook — clients stop implicitly when the redis connection closes on process termination. Acceptable for serverless; the return value still exposes `closeAll` for callers that want to invoke it explicitly.

### Engine lifecycle (`start()` / `stop()`)

- `start()` is non-blocking; fire-and-forget after construction (clamator §6: opens transport, spins reply loop). Calling immediately after `new EngineClient(...)` is fine.
- `stop()` on shutdown drains in-flight handlers up to `shutdownGraceMs`, deletes the per-instance reply stream, and closes the transport if owned.

### Discovery, SSE, widget proxy

Unchanged. `discovery.ts` reads `redis.exists` for heartbeat keys (engine-owned, not clamator stream). SSE pollers and the widget proxy read MongoDB only.

### Updated public exports (`packages/optio-api/src/index.ts`)

```typescript
// Handlers (framework-agnostic, used by adapters)
export {
  listProcesses, getProcess, getProcessTree, getProcessLog, getProcessTreeLog,
  launchProcess, cancelProcess, dismissProcess, resyncProcesses,
  type ListQuery, type PaginationQuery, type TreeLogQuery, type CommandResult,
} from './handlers.js';

// Engine client — re-exported from generated for app convenience
export { EngineClient } from './_generated/engine.js';

// Engine cache (used internally by adapters; exported for custom adapters)
export { createEngineCache, type EngineCache } from './engine-cache.js';

// SSE pollers (unchanged)
export {
  createListPoller, createTreePoller,
  type StreamPollerOptions, type TreePollerOptions, type ListPollerHandle,
} from './stream-poller.js';

// REMOVED: publishLaunch, publishResync (publisher.ts deleted)
```

## 6. Documentation update inventory

Per repo `AGENTS.md`: when a package's public API changes, that package's `AGENTS.md` is updated in the same commit as the code. The root `AGENTS.md` is updated when consistency requires it.

This section enumerates what each doc needs across the migration. Per-phase plans pull from this list.

### `README.md` (root)

- **Phase 1.** Architecture section — add the canonical architectural-rule statement.
- **Phase 5.** Level 2 description rewritten. "External systems publish commands to the `optio:commands` Redis stream" becomes "External systems control processes via clamator RPC clients calling the `engine` service." Remove `on_command()` reference. Architecture diagram refresh out of scope.

### `AGENTS.md` (root)

- **Phase 1.** Architecture Notes section gets the architectural rule.
- **Phase 2.** Add `optio_core.rpc_server` to the public Python API table. Add return-shape note for `registerOptioApi`.
- **Phase 4.** optio-api Handler Functions section: signatures updated; `CommandResult` body now `{ reason, message }`.
- **Phase 5.** Remove `on_command(...)` from the optio-core API. Remove `publishLaunch` / `publishResync` from the optio-api Publishers section. Architecture Notes: remove `${prefix}:commands` stream description; replace with the clamator service-streams pattern.

### `packages/optio-contracts/AGENTS.md`

Create if missing.

- **Phase 1.** Document file structure: `src/schemas/`, `api-to-frontend.ts`, `engine-to-api.ts`. Document the `<server>-to-<client>.ts` naming convention. Document codegen output destinations and invocation. Document the pre-commit drift check.

### `packages/optio-contracts/README.md`

- **Phase 1.** Section on the two contract types and the naming convention.

### `packages/optio-core/README.md`

- **Phase 2.** New section "RPC service" — document `optio_core.rpc_server`, the B′ extension pattern, and co-existence with the legacy stream during phases 2-4.
- **Phase 5.** "Remote Control via Redis" section rewritten to describe clamator as the inbound channel. Remove `on_command()` documentation. Brief migration sentence (no detailed migration guide per user preference). The existing "Optio-core is the sole owner of the data" passage stays; expand slightly to state the rule on both directions.

### `packages/optio-core/AGENTS.md`

- **Phase 2.** Document `rpc_server` attribute and the new `init()` parameter.
- **Phase 5.** Remove `on_command(...)` from public API list and imports section.

### `packages/optio-api/README.md`

- **Phase 1.** Update import paths if any reference `contract.ts` (now `api-to-frontend.ts`).
- **Phase 2.** Update OptioApiOptions doc; document the return shape of `registerOptioApi` / `createOptioRouteHandlers` / `createOptioHandler`. Add an EngineClient sharing example.
- **Phase 4.** REST Endpoints table descriptions: remove text suggesting the API does state validation. Delete the "Exported Publishers" section. Add an "Engine Client" section.

### `packages/optio-api/AGENTS.md`

- **Phase 1.** Update file paths if `contract.ts` is referenced.
- **Phase 2.** Document the new return shape of `registerOptioApi`. Note the `EngineClient` and `createEngineCache` exports. Add `engine-cache.ts` to the "Building Custom Adapters" section so custom-adapter authors know to use the shared cache rather than rolling their own.
- **Phase 4.**
  - Delete the "State guards enforced by command handlers" block.
  - Rewrite Handler Functions section with new signatures.
  - Rewrite the `CommandResult` doc — body now `{ reason, message }`.
  - Add the architectural rule statement at the top.
  - Replace the Publishers section with an Engine Client section. Remove all `publishLaunch` / `publishResync` / `publishCancel` / `publishDismiss` references.

### `packages/optio-ui/README.md` and `AGENTS.md`

- **Phase 1.** Verify no direct references to `contract.ts` filename. No content change otherwise.

### `packages/optio-dashboard/README.md` and `AGENTS.md`, `packages/optio-host/*`, `packages/optio-opencode/*`

- No changes.

### Spec docs

- `docs/2026-05-08-engine-rpc-migration-design.md` — this spec, written before any code work.
- `docs/2026-05-08-more-rpc-cleanup-todo.md` — companion seed already created.
- Per-phase implementation plans: `docs/2026-05-08-engine-rpc-migration-phase-1-plan.md` through `…-phase-5-plan.md` — produced via the writing-plans skill, one per phase.

### Pre-commit / CI

- Pre-commit hook: re-run `make codegen`, fail on `git diff` non-empty under `_generated/` paths. Phase 1.
- CI bootstrapping (running `make lint && make test` and the codegen drift check on PRs) is out of scope for this migration. Tracked separately. The pre-commit hook is the sole drift guard until CI exists.

## 7. Top-level Makefile

Lives at `/home/csillag/deai/optio/Makefile`. Self-documenting via `##` comments parsed by the `help` target.

```makefile
.DEFAULT_GOAL := help
.PHONY: help install build codegen test test-interop lint clean clean-codegen clean-deep

PY_PACKAGES := optio-core optio-host optio-opencode

help:  ## Show this help
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install dependencies (TS workspace + Python packages)
	pnpm install
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && pip install -e .[dev] 2>/dev/null || pip install -e .); \
	done

build:  ## Build all packages
	pnpm -r build
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && python -m build); \
	done

codegen:  ## Regenerate clamator RPC client/server stubs from optio-contracts source
	pnpm exec clamator-codegen \
	  --src packages/optio-contracts/src \
	  --out-ts packages/optio-api/src/_generated \
	  --out-py packages/optio-core/src/optio_core/_generated \
	  --ts-contract-import 'optio-contracts/engine-to-api'

test:  ## Run all tests (TS + Python; per-package, no docker)
	pnpm -r test
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && pytest); \
	done

test-interop:  ## End-to-end test: TS API ↔ Py engine over real redis (clamator wire verification)
	cd packages/optio-demo && bash run-interop.sh

lint:  ## Lint all packages
	pnpm -r lint
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && ruff check .); \
	done

clean:  ## Remove build artifacts and dependency caches (KEEPS committed _generated/)
	pnpm -r clean 2>/dev/null || true
	rm -rf node_modules packages/*/node_modules packages/*/dist
	for pkg in $(PY_PACKAGES); do \
	  (cd packages/$$pkg && rm -rf build dist *.egg-info .pytest_cache); \
	done
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +

clean-codegen:  ## Remove generated clamator stubs (require make codegen to rebuild)
	rm -rf packages/optio-api/src/_generated
	rm -rf packages/optio-core/src/optio_core/_generated

clean-deep: clean clean-codegen  ## clean + clean-codegen (full reset)
```

### Design notes

- `help` is the default target; `make` with no args prints the target list. AWK extracts `target: ## description` lines from the Makefile itself.
- `clean` does NOT remove `_generated/` paths — they are committed; a casual `make clean` should not break the type-checker. `clean-codegen` is opt-in for that. `clean-deep` does both.
- `codegen` is idempotent (clamator §5 deterministic output). The pre-commit hook runs it plus `git diff --exit-code`.
- `test-interop` lives in `packages/optio-demo/` per repo convention. It spins ephemeral redis, starts engine and API subprocesses, issues HTTP requests, and asserts end-to-end behavior.

### Out of scope for the Makefile

- No release / publish targets.
- No watch mode for codegen.
- No docker / docker-compose targets at root.
- No cross-language clamator interop suite (lives in the clamator repo).

## 8. Phased migration plan

Five phases, each a separate implementation plan executable independently. Tests run after each phase. The working tree stays green between phases — no half-built state across phase boundaries.

### Phase 1 — Contracts and tooling

**Goal.** Add the new contract surface and supporting tooling. No runtime behavior change. Engine and API still talk via the legacy redis stream.

**Deliverables.**

- `packages/optio-contracts/src/engine-failure-reasons.ts` — browser-safe Zod enums (`LaunchFailureReason` etc.); imports only `zod`.
- `packages/optio-contracts/src/engine-to-api.ts` — full clamator engine contract per §3; imports the failure-reason enums from `engine-failure-reasons.ts`.
- `packages/optio-contracts/src/api-to-frontend.ts` — renamed from `contract.ts`. HTTP error-body schema unchanged in phase 1 (flips in phase 4 alongside handler rewrite); does not yet consume failure-reason enums.
- `packages/optio-contracts/src/index.ts` — re-exports failure-reason enums from `engine-failure-reasons.ts` for package-root consumers (does not re-export `engineContract`).
- `packages/optio-api/src/_generated/engine.ts` — codegen output, committed.
- `packages/optio-core/src/optio_core/_generated/engine.py` — codegen output, committed.
- Clamator runtime deps added: `@clamator/protocol`, `@clamator/over-redis`, and `zod` (consumer requirement per §5) in `packages/optio-api/package.json`; `clamator-protocol`, `clamator-over-redis`, `pydantic` in `packages/optio-core/pyproject.toml`. `@clamator/codegen` as root devDep.
- Pre-commit hook installed via `git config core.hooksPath scripts/git-hooks` (`scripts/git-hooks/pre-commit` runs `make codegen` + drift check; `scripts/install-hooks.sh` is the one-line installer).
- Top-level `Makefile` per §7.
- Top-level `README.md` Architecture section: canonical architectural-rule statement.
- Top-level `AGENTS.md` Architecture Notes: rule statement.
- `packages/optio-contracts/AGENTS.md` — created or updated per §6.
- All references to `contract.ts` in any file updated to `api-to-frontend.ts`.

**Acceptance.**

- `make codegen` produces deterministic output.
- `make build` green across all packages.
- `pnpm -r test` and per-package Python tests green (no behavior change).
- `make test-interop` does not exist yet.

**Risks.**

- Codegen produces unexpected Pydantic / TS shape for `discriminatedUnion`. Mitigation: small interop test (in clamator's repo or temporary here) verifying both sides parse the same envelope.
- `contract.ts` import sweep — easy to miss. Mitigation: rg / grep before merging.

### Phase 2 — Clamator infrastructure (co-existence)

**Goal.** Add clamator RPC end to end. Both ingress paths (legacy stream and clamator RPC) live and functional. The API still publishes to the legacy stream — RPC is callable but unused by HTTP handlers.

**Deliverables.**

Engine side:

- `packages/optio-core/src/optio_core/_engine_service.py` per §4 — `EngineService` class implementing all RPC methods with full validation and discriminated-union results.
- `packages/optio-core/src/optio_core/lifecycle.py` updated:
  - `init()` accepts new `rpc_server` parameter.
  - When `redis_url` is provided, creates `RedisRpcServer` and registers `EngineService`. Sets `self.rpc_server`.
  - When `rpc_server` is provided, registers `EngineService` on it. Skips redis client creation.
  - Mutually-exclusive validation; raise on conflict.
  - Legacy `CommandConsumer` setup unchanged.
  - `run()` starts both `rpc_server` and `_consumer`.
  - `shutdown()` stops both.
- `optio_core.rpc_server` exposed at module level.

API side:

- New file `packages/optio-api/src/engine-cache.ts` per §5: framework-agnostic `createEngineCache(redis): EngineCache` factory. Owns `EngineClient` lifecycle (lazy create + `start()` on first lookup, `closeAll()` on teardown). No framework dependencies.
- All four adapters updated to use the shared cache:
  - On registration: `const cache = createEngineCache(opts.redis)`.
  - Per request: `cache.get(database, prefix)` to obtain the `EngineClient`.
  - Framework-specific shutdown hook calls `cache.closeAll()` (fastify `onClose`; express / nextjs expose `closeAll` on the return handle for callers to invoke).
  - Return value: single-db mode returns `{ engine }`; multi-db mode returns `{ getEngine }`. Express and nextjs adapters additionally expose `closeAll` since their frameworks lack a built-in close hook.
  - HTTP command handlers continue to call legacy `handlers.launchProcess(db, redis, ...)` etc. — no behavior change for HTTP yet.
- `packages/optio-api/src/index.ts` re-exports `EngineClient` and `createEngineCache` (plus the `EngineCache` type).

Tests / interop:

- `packages/optio-demo/run-interop.sh` initial scenarios: launch / cancel / dismiss / resync via direct clamator client (bypassing HTTP); failure reasons surface correctly; resync notification reaches engine.
- `make test-interop` target wired.

Docs: per §6 phase 2 entries.

**Acceptance.**

- `make test` green.
- `make test-interop` green for basic scenarios + key failure reasons.
- HTTP behavior unchanged (legacy path still exclusive for HTTP requests).

**Risks.**

- Clamator client lifecycle bugs (start/stop ordering with framework shutdown). Mitigation: integration test for graceful shutdown.
- Multi-db engine cache memory growth. Bounded by number of distinct `(db, prefix)` ever requested. Phase 2 plan adds a soft cap or eviction if a real concern is measured.

### Phase 3 — Migrate HTTP command path to RPC (per endpoint)

**Goal.** Switch HTTP command handlers from legacy `publishX` to `engine.X`. Per endpoint, with tests after each. The API's own pre-RPC validation stays as defense-in-depth during this phase.

**Deliverables.** One commit per endpoint:

- **3a — `launch`.** `handlers.launchProcess` calls `engine.launch(...)` instead of `publishLaunch(...)`. Maps the RPC result to the existing `CommandResult` shape. The API's pre-RPC checks remain; if they reject pre-flight, no RPC call.
- **3b — `cancel`.** Same pattern.
- **3c — `dismiss`.** Same pattern.
- **3d — `resync`.** Switches to `engine.resync(...)` notification. The API returns `202 Accepted`. (Note: response status changes from 200 to 202 — minor breaking change for any client checking exact status.)

For each sub-step:

- Per-endpoint integration test: HTTP roundtrip, verify 200/404/409 + body shape.
- Per-endpoint scenario in `make test-interop`.

**Acceptance.**

- After each sub-step: `make test`, `make test-interop` green.
- HTTP command requests go through clamator. Manual verification: `redis-cli xrange "${db}/${prefix}:cmds:engine"` shows entries; `redis-cli xrange "${db}/${prefix}:commands"` shows none.

**Risks.**

- Latency increase: HTTP request now waits for an RPC reply instead of fire-and-forget. Should be fast (engine responds in milliseconds), but worth measuring.
- The failure-reason set must be exhaustive at the engine. If the engine returns a reason the API's status table doesn't know, the response falls through to 500. Phase 3 plan ensures coverage.

### Phase 4 — Remove API-side authority code

**Goal.** Delete API code that exists only because of pre-RPC validation. The API becomes a pure RPC translator.

**Deliverables.**

Code:

- `packages/optio-api/src/handlers.ts`:
  - Delete `LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES` constants.
  - Delete `cancellable` and `supportsResume` precondition checks.
  - Delete pre-RPC `findProcessByEitherId(...)` calls in command handlers.
  - Handler signatures change to `(engine, id, ...)` per §5.
  - Update `api-to-frontend.ts` error response schemas (404 / 409 bodies) to `{ reason, message }`. Either extend `ErrorSchema` or introduce per-command error bodies (`LaunchErrorBody`, `CancelErrorBody`, `DismissErrorBody`) per §3.
- `packages/optio-api/src/process-id-resolver.ts` — kept (query-side callers remain).
- `packages/optio-api/src/publisher.ts` — deleted.
- Adapter call-sites updated.
- `packages/optio-api/src/index.ts` — remove `publishLaunch`, `publishResync` exports.

Engine side:

- `EngineService._resolve(id)` confirmed handles both ID forms. Phase 4 plan adds dedicated tests.

Docs: per §6 phase 4 entries.

Tests:

- `make test-interop` scenarios for the full failure-reason matrix.

**Acceptance.**

- `grep -E 'LAUNCHABLE_STATES|CANCELLABLE_STATES|END_STATES|publisher\.|publishLaunch|publishCancel|publishDismiss|publishResync' packages/optio-api/src/` returns nothing.
- `make test`, `make test-interop` green.

**Risks.**

- Removing defense-in-depth pre-checks means bugs in engine validation surface as wrong HTTP status codes. Mitigation: phase 4 plan includes adversarial tests forcing every failure reason.

### Phase 5 — Retire legacy stream

**Goal.** Remove the legacy `${prefix}:commands` redis stream and its consumer. Single ingress path.

**Deliverables.**

Code:

- `packages/optio-core/src/optio_core/lifecycle.py`:
  - Delete `CommandConsumer` setup in `init()`.
  - Delete `_consumer` field and lifecycle calls in `run()` / `shutdown()`.
  - Delete `on_command(command_type, handler)` public method.
  - Delete `_handle_launch`, `_handle_cancel`, `_handle_dismiss`, `_handle_resync` if their only call site was the consumer dispatch (the inner methods they delegate to stay).
- Delete `packages/optio-core/src/optio_core/_command_consumer.py`.
- `packages/optio-core/src/optio_core/__init__.py` — remove `on_command` re-export.

Docs: per §6 phase 5 entries.

Tests:

- Delete tests for `CommandConsumer` and `on_command`.
- `make test-interop` scenario: publish to `${prefix}:commands` after merge — assert engine ignores.

**Acceptance.**

- `grep -rn 'CommandConsumer\|on_command\|optio:commands\|prefix.*:commands' packages/` returns only spec / doc references.
- `make test`, `make test-interop` green.
- `redis-cli xrange "${db}/${prefix}:commands"` after running through full HTTP test suite shows zero entries.

**Risks.**

- External consumers (Excavator etc.) break. User handles these out-of-band per the user's preference. Phase 5 plan flags coordination as out-of-scope but lists known consumers from project memory for visibility.
- Removing `on_command` may break user-defined custom commands. Replacement path documented (register additional clamator service on `optio_core.rpc_server`).

### Cross-phase notes

- Each phase's implementation plan is generated separately via the writing-plans skill, after this spec is approved and committed.
- Plans land at `docs/2026-05-08-engine-rpc-migration-phase-N-plan.md`.
- Each phase gets its own feature branch off main, merged after green tests.
- Phase plans are not auto-executed; the user kicks off each phase separately.

## 9. Out of scope and open questions

### Out of scope

- **Read path.** REST GETs, SSE pollers, the widget proxy, discovery — all keep reading MongoDB and redis heartbeats directly.
- **Other packages.** `optio-opencode`, `optio-host`, `optio-dashboard`, `optio-ui` (beyond the contract.ts rename verification).
- **Engine surfaces that stay Python-only.** `adhoc_define`, `adhoc_delete`, `get_process`, `list_processes`, `run`, `shutdown`, the async-CM form of `block_launches`.
- **External consumer migration.** Excavator and any other Level-2 consumer migration handled by user out-of-band.
- **Polling-based confirmation cleanup.** The 1-second SSE poll, 5-second `refetchInterval`, cache invalidation patterns. Recorded in companion seed `2026-05-08-more-rpc-cleanup-todo.md`.
- **Architecture diagram refresh.** Defer to whoever maintains diagrams.
- **Clamator itself.** Consumed as-designed.
- **Build / release infrastructure beyond Makefile basics.**

### Open questions (deferred to phase plans)

**Phase 1.**

- Pre-commit hook delivery mechanism: committed bash script under `scripts/` with one-line install in README, vs. husky-style npm package, vs. manual install. Recommendation lean: committed bash script.
- Whether `optio-api/src/index.ts` re-exports failure-reason types from `optio-contracts`, or consumers import from `optio-contracts` directly. Recommendation lean: import from `optio-contracts`.

**Phase 2.**

- `groupCancelAndWait` timeout strategy: hardcoded server-side timeout, vs. caller-supplied per-call timeout, vs. method becomes a notification + separate poll. Recommendation lean: caller-supplied with sane default.
- Multi-db engine cache eviction: unbounded `Map`, vs. soft cap with LRU, vs. periodic eviction of unused clients. Default: unbounded; revisit if measured.
- Adapter shutdown hook wiring details per framework.
- `consumer_claim_idle_ms` tuning: a long-running `groupCancelAndWait` could exceed clamator's default 60s and trigger `XCLAIM` retry. Phase 2 plan addresses by tuning upward or by periodic heartbeat-extending.

**Phase 3.**

- Status code change for `resync` (200 → 202) — document in commit message.
- Latency budget for HTTP roundtrip via RPC.

**Phase 4.**

- Whether to slim `process-id-resolver.ts` after command-side calls disappear.
- Confirm `EngineService._resolve` exhaustive coverage.

**Phase 5.**

- Internal callers of `on_command`. Phase 5 plan greps before deletion.
- Stale references to `${prefix}:commands` in fixtures / docs / examples.

## 10. Testing strategy

### Test layers

**Unit / per-package (`make test`).** Existing per-package suites continue. Each package's tests stay within its own boundary — no cross-language calls. Fast; no docker.

- `packages/optio-core/tests/`: Python tests for engine logic. New tests for `EngineService` validation, idempotency on redelivery, discriminated-union result construction.
- `packages/optio-api/src/__tests__/` and `*.test.ts`: TS tests for handlers as pure functions. Mock `EngineClient`; verify reason → HTTP status mapping.
- `packages/optio-contracts`: Zod schema parse tests; ensure failure-reason enums stay exhaustive.

**End-to-end interop (`make test-interop`).** Real redis (docker), real engine subprocess, real API subprocess. Lives in `packages/optio-demo/run-interop.sh`. Slow; opt-in.

**Pre-commit drift (`make codegen && git diff --exit-code`).** Verifies generated stubs match the source contract. CI also runs as a separate job.

### Per-phase test additions

**Phase 1.**

- `optio-contracts`: parse tests for `engine-to-api.ts` discriminated unions and reason enums.
- Codegen smoke test: deterministic output (run twice, diff empty).

**Phase 2.**

- `optio-core`: `EngineService` unit tests, one per RPC method, covering success path plus every documented failure reason.
- `optio-core`: redelivery / idempotency tests.
- `optio-api`: adapter tests verifying `getEngine(database, prefix)` cache reuses instances; lifecycle hooks call `stop()` on every cached client.
- `optio-api`: `registerOptioApi` return-shape tests for both single-db and multi-db modes.
- Interop: basic launch / cancel / dismiss / resync via direct clamator client; failure reasons surface correctly.
- Interop: HTTP path still uses legacy redis stream (verify both ingress paths active).

**Phase 3.** Per-endpoint as each migrates:

- HTTP-level integration test.
- Interop scenario: command sent via HTTP routes through clamator.
- Latency check: P50 of full HTTP roundtrip under 100 ms over loopback redis.

**Phase 4.**

- Adversarial tests forcing every failure reason via HTTP path.
- Verify engine handles either id form for all command methods.
- Negative test: search source for removed symbols; fail build if any survive.

**Phase 5.**

- Negative test: publish to legacy `${prefix}:commands` stream after merge; verify engine ignores.
- Search source for removed symbols.
- Verify `make test-interop` still green.

### Test environment

- **Redis.** Docker only. `packages/optio-demo/docker-compose.yml` provisions redis; reused for `make test-interop`.
- **MongoDB.** `mongodb-memory-server` for fast unit tests; docker-provisioned for interop.
- **Process orchestration.** Engine and API subprocesses spawned by `run-interop.sh`. READY-line stdout convention (per clamator interop pattern §9) for synchronization.

### CI structure

Note: this section describes the target CI shape once CI infrastructure exists in the repo. CI bootstrapping is not part of this migration.

- **Per-PR.** `make lint && make test` (fast; no docker).
- **Per-PR.** `make codegen && git diff --exit-code` (drift check).
- **Per-PR or post-merge.** `make test-interop` (slower; docker required). Decision deferred to phase 1 plan based on CI cost budget.

### What's NOT tested

- Cross-clamator-version compatibility — clamator's lockstep versioning means we always ship together.
- Concurrent-call fairness — clamator's own interop suite covers this at the protocol level.
- Network-partition scenarios — recovery semantics are clamator's concern.

### Test debt

- Existing `optio-api` tests in `__tests__/` may have mocks tied to `publisher.ts` shape. Phase 4 cleanup updates these alongside the handler signature change.
- Existing `optio-core` tests for `CommandConsumer` deleted in phase 5 alongside the consumer code.

## Appendix A — Verbatim documentation prose for phase 1

The blocks below are ready-to-paste prose for the doc updates that ship in phase 1. They describe the post-phase-1 state of optio's architectural-rule documentation. Copy each block into the corresponding file when executing phase 1; no further drafting needed.

### A.1 Top-level `README.md` — Architecture section

The current section contains only the architecture image. Insert the following after the image (keep the image line as-is):

```markdown
### Authority and data flow

Optio enforces a clean separation between writes and reads:

- **`optio-core` (the engine) is the sole writer to MongoDB.** All state transitions, validation, scheduling, and policy decisions happen in the engine process. The engine is the single source of truth for what commands are allowed and what state results.
- **`optio-api` (the REST API) is read-only against MongoDB.** It serves REST GETs, SSE streams, the widget proxy, and instance discovery by reading directly from MongoDB and from redis heartbeat keys. It performs no DB writes.
- **Mutating operations (launch, cancel, dismiss, resync, group-cancel, launch blocks) flow from the API to the engine via clamator RPC over redis.** The API translates an HTTP request into a typed RPC call; the engine validates, acts, and returns a typed result; the API translates the result back into an HTTP response. The API enforces no state machine, no `cancellable` policy, no command-acceptance rules of its own.
- **External applications** that need to control the engine without going through HTTP can use the engine's Python API directly (in-process), or register as a clamator RPC client (cross-process). They never write to MongoDB themselves.
```

### A.2 Top-level `AGENTS.md` — Architecture Notes section

Replace the existing "Redis stream" bullet with the new "Engine RPC" bullet, insert the new "Authority rule" bullet at the very top of the section, and adjust the "No Redis mode" bullet text to mention `rpc_server`. All other bullets in the section remain unchanged.

```markdown
- **Authority rule.** `optio-core` is the sole writer to MongoDB. `optio-api` reads MongoDB directly for queries (REST GETs, SSE, widget proxy, discovery) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. Engine is single source of truth for what commands are allowed and what state results. Full statement: top-level README "Authority and data flow".
- **Engine RPC.** clamator over-redis. Engine hosts a `RedisRpcServer` constructed by `optio_core.init()` with `key_prefix=f"{database}/{prefix}"`, registering the `engine` service defined in `optio-contracts/src/engine-to-api.ts`. API uses a `RedisRpcClient` per `(database, prefix)` constructed by `registerOptioApi`. Apps can register additional services on `optio_core.rpc_server` before calling `optio_core.run()`.
- **Collection name**: `{prefix}_processes` (MongoDB)
- **No Redis mode**: `init()` with `redis_url=None` and no `rpc_server` disables the command surface; use direct Python API calls (`optio.launch()`, etc.) instead.
```

(The remaining bullets — "Progress flushing", "Child processes", "Ephemeral processes", "Migrations", "Scheduler", "Process state reconciliation", "Persistent launch blocks" — are unchanged from today's content.)

### A.3 `packages/optio-contracts/AGENTS.md` — new "Package structure" section

Insert this section between the existing "## Package" block and the existing "## Schemas" block:

```markdown
## Package structure

The package hosts two typed contracts that define optio's internal communication surfaces:

| File | Contract type | Purpose |
|------|---------------|---------|
| `src/api-to-frontend.ts` | ts-rest HTTP contract | What `optio-api` exposes to its REST clients (UI, external integrations). Used by `optio-ui` to construct typed clients and by `optio-api` to register typed handlers. |
| `src/engine-to-api.ts` | clamator RPC contract | What `optio-core` (the engine) exposes to its RPC callers (typically `optio-api`). Used by `optio-api` to issue typed RPC calls and by `optio-core` to implement typed handlers. |
| `src/schemas/` | Shared Zod schemas | Common types used by both contracts. `common.ts` holds generic primitives (ObjectId, Pagination, Error). `process.ts` holds process-domain types (Process, ProcessState, LogEntry, ProcessMetadataFilter). |

### Naming convention

Contract files follow `<server>-to-<client>.ts`, where the **server** is the side that exposes the contract and the **client** is the side that consumes it. For example, in `engine-to-api.ts`, the engine exposes methods that the API calls. The "to" indicates exposure, not call direction.

### Codegen

The clamator contract (`engine-to-api.ts`) is the single source of truth for the RPC surface. clamator's codegen produces matching wrappers in both languages:

- **TypeScript output:** `packages/optio-api/src/_generated/engine.ts` — `EngineClient` class, params/result types.
- **Python output:** `packages/optio-core/src/optio_core/_generated/engine.py` — Pydantic models, `EngineService` ABC.

Generated files are committed. Regenerate via `make codegen` at the repo root. A pre-commit hook runs codegen and fails on `git diff` non-empty under `_generated/` paths to catch drift.

The HTTP contract (`api-to-frontend.ts`) does not require codegen: ts-rest builds typed clients and handlers from the contract object via TypeScript's type system at the consumer's compile time.
```

Plus one inline edit elsewhere in the same file: line 158 currently reads `ts-rest router exported from contract.ts`. Change to `ts-rest router exported from api-to-frontend.ts`.

### A.4 `packages/optio-contracts/README.md` — replace "## Contract" section

Replace the current "## Contract" section (lines 28-43 in the present file) with:

```markdown
## Contracts

The package hosts two typed contracts. Contract files follow `<server>-to-<client>.ts` naming: the server side is the one that exposes the contract; the client side calls it.

- `processesContract` (in `src/api-to-frontend.ts`) — ts-rest HTTP contract that `optio-api` exposes to its REST clients.
- `engineContract` (in `src/engine-to-api.ts`) — clamator RPC contract that `optio-core` exposes to its RPC callers.

### `processesContract` (HTTP, ts-rest)

ts-rest router with 9 endpoints, used by `optio-ui` to call `optio-api`:

| Name | Method | Path |
|------|--------|------|
| `list` | GET | `/processes/:prefix` |
| `get` | GET | `/processes/:prefix/:id` |
| `getTree` | GET | `/processes/:prefix/:id/tree` |
| `getLog` | GET | `/processes/:prefix/:id/log` |
| `getTreeLog` | GET | `/processes/:prefix/:id/tree/log` |
| `launch` | POST | `/processes/:prefix/:id/launch` |
| `cancel` | POST | `/processes/:prefix/:id/cancel` |
| `dismiss` | POST | `/processes/:prefix/:id/dismiss` |
| `resync` | POST | `/processes/:prefix/resync` |

Used at runtime by ts-rest; no codegen step.

### `engineContract` (RPC, clamator)

clamator service named `engine`, used by `optio-api` to call `optio-core`. Methods:

| Method | Kind | Purpose |
|--------|------|---------|
| `launch` | request/reply | Launch a process; returns post-command process state or typed failure reason. |
| `cancel` | request/reply | Cancel a running or scheduled process; returns post-command state or typed failure reason. |
| `dismiss` | request/reply | Reset a terminal process to idle; returns post-command state or typed failure reason. |
| `groupCancel` | request/reply | Cancel all processes matching a metadata filter; returns count. |
| `groupCancelAndWait` | request/reply | Cancel all matching processes and wait until they reach a terminal state. |
| `blockLaunches` | request/reply | Add a persistent launch block. |
| `unblockLaunches` | request/reply | Remove a persistent launch block; returns count removed. |
| `resync` | notification | Re-sync task definitions. Fire-and-forget; no reply. |

Failure modes use discriminated-union result types (e.g. `{ ok: true, process } | { ok: false, reason: 'not-found' | 'not-launchable' | … }`) so consumers get exhaustive type coverage on success and failure branches.

Generated wrappers ship next to consumers: `packages/optio-api/src/_generated/engine.ts` for TypeScript, `packages/optio-core/src/optio_core/_generated/engine.py` for Python. Regenerate via `make codegen` at the repo root.
```
