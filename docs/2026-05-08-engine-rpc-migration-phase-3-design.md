# 2026-05-08 — Engine RPC migration, phase 3 design

**Status:** Design.
**Parent spec:** `docs/2026-05-08-engine-rpc-migration-design.md`.
**Phase 1 design:** `docs/2026-05-08-engine-rpc-migration-phase-1-design.md`.
**Phase 2 design:** `docs/2026-05-08-engine-rpc-migration-phase-2-design.md`.

This document supplements the parent spec by recording the phase-3 decisions, structuring the commit sequence into a Stage A (cleanup) + Stage B (channel swap) split that the parent spec does not prescribe, and pulling the body-shape flip and `publisher.ts` deletion forward from phase 4. Everything not addressed here defers to the parent spec.

## 1. Scope

Phase 3 ships two integrated changes:

1. **Adapter-layer cleanup (Stage A).** Move all framework-agnostic code out of the four web-framework adapters into a new `OptioContext` layer plus shared helpers. Read and command handlers migrate to a uniform `(ctx, query, …)` signature. After Stage A, adapters contain only framework integration code; the legacy `${prefix}:commands` redis-stream channel is still in use end-to-end.

2. **HTTP command-path channel swap (Stage B).** Per endpoint, swap the command handler from legacy `publishX(redis, …)` to the clamator RPC `engine.X({…})` call. The 404/409 response body shape flips from `{message}` to `{reason, message}` per command (typed). `resync` flips from HTTP 200 → 202 (notification, async). `publisher.ts` is deleted in 3d when the last caller disappears.

### Decisions resolved here that deviate from the parent spec

| Decision | Parent spec | Phase 3 | Reason |
|---|---|---|---|
| Body-shape flip to `{reason, message}` | Phase 4 (§5, §6) | **Pulled into Stage B** (each 3a/b/c/d commit) | Each Stage B commit already touches every fail-path return; adding the typed `reason` field costs one line each; phase 4 shrinks to authority-code deletion only. Body shape changes once, not twice. |
| `publisher.ts` + `publisher.test.ts` deletion | Phase 4 (§5) | **Stage B 3d** | After 3d, the API has zero callers of legacy publish. No reason to linger dead code one phase. |
| Per-command `LaunchErrorBody` / `CancelErrorBody` / `DismissErrorBody` in `api-to-frontend.ts` | Phase 4 (§3) | **Pulled into Stage B** alongside the matching handler swap | Frontend gets exhaustive `switch (body.reason)` typing one phase earlier; ts-rest types stay aligned with handler returns. |
| Adapter-layer cleanup (`resolveDb` deduplication, redundant defaults, SSE helper extraction, layer-rule docs) | Not mentioned | **New Stage A in phase 3** | Phase 3 already touches every command-route call site in four adapters. This is the cheapest moment to also extract the framework-agnostic code that has accumulated there. Without this cleanup, phase 3 would add yet another duplicated line (`cache.get(database, prefix)`) per route, making the duplication worse. |

### What ships

**Stage A (no behavior change, six commits):**

- `optio-api`: new `context.ts` with `OptioContext` + `createOptioContext`. Read handlers and command handlers migrate to `(ctx, query, …)` signature; command handlers internally still call legacy `publishX` during Stage A.
- All four adapters (`fastify`, `express`, `nextjs-app`, `nextjs-pages`) shrink to thin route registries; `resolveDb` calls and per-route default fallbacks (`?? 25`, `?? false`) move out.
- New `optio-api/src/sse-options.ts` consolidating shared SSE/poller query parsing logic (`parseMetadataFilterQuery`, `detectLegacyMetadataParams`, `maxDepth` coercion).
- `packages/optio-api/AGENTS.md` and `README.md`: explicit layer rules documenting the binding constraints on adapter code (no framework-agnostic code, no parallel-maintenance code paths, no defaults that belong to the contract layer).
- `run-interop.sh` + `interop/run.ts`: hardened with hard per-step timeouts, readiness probes, distinct exit codes, log tailing — see §3 below.

**Stage B (per-endpoint behavior change, four commits):**

- Command handlers swap `publishX(...)` → `engine.X({...})`. Per-command typed error bodies (`LaunchErrorBody` / `CancelErrorBody` / `DismissErrorBody`) added to `api-to-frontend.ts`; HTTP 404/409 body becomes `{reason, message}`.
- `resync` becomes a clamator notification; HTTP response status flips 200 → 202.
- `publisher.ts` and `publisher.test.ts` deleted in 3d. `index.ts` removes `publishLaunch / publishCancel / publishDismiss / publishResync` exports.
- Per-endpoint HTTP-roundtrip interop scenarios in a new `interop/run-http.ts` covering 200 success, 404 not-found, 409 each business-state failure reason. `run-interop.sh` extended to spawn a fastify server alongside the existing direct-clamator client substrate.

### What does not ship

Defer to phase 4:

- Pre-RPC validation deletion in command handlers (`LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES`, `cancellable` check, `findProcessByEitherId` pre-read in command handlers).
- Removal of the `db` and `prefix` parameters from command handlers (kept through phase 3 for the pre-check).
- Adversarial test matrix forcing every failure reason via the HTTP path.

Defer to phase 5:

- Engine-side legacy `${prefix}:commands` stream + `CommandConsumer` removal.
- `optio_core.on_command(...)` removal.

Out of scope entirely:

- Excavator port to the new RPC (tracked separately, post-migration).
- Architecture diagram refresh.
- Engine-side changes (`EngineService` is complete from phase 2).
- CI workflow bootstrapping.

## 2. Architectural layer model (introduced and documented in Stage A)

The optio-api package has three internal layers. Stage A brings the layout in line with this model and Stage A5 documents it in `AGENTS.md`. Phase 3 hard-binds these rules; future contributors get an enforceable reference.

1. **Adapter layer** — `packages/optio-api/src/adapters/{fastify,express,nextjs-app,nextjs-pages}.ts`.

   *Sole purpose:* integrate with the corresponding web framework.

   *Allowed:* framework-native request/response wrangling, route registration via the framework's API, framework lifecycle hooks (e.g. fastify `onClose`), framework-specific SSE response writers (`reply.raw.writeHead` / `res.write` / Next.js `ReadableStream`), body parser registration.

   *Forbidden:* any code that would be repeated identically across the four adapters. Specifically:
   - `resolveDb(...)` — moves to handler via ctx.
   - Default-value fallbacks (`x ?? N`) — defaults belong in the contract Zod schemas.
   - `parseMetadataFilterQuery`, `detectLegacyMetadataParams`, `maxDepth` coercion — move to `sse-options.ts`.
   - Engine cache instantiation — moves to `createOptioContext`.
   - Business logic, RPC mechanics, ObjectId coercion.

   Test for adding code to an adapter: *"Would I write this same code in the other three adapters?"* If yes, extract.

2. **Handler layer** — `packages/optio-api/src/handlers.ts` and collaborators (`process-id-resolver.ts`, `metadata-filter-query.ts`, `sse-options.ts`).

   Framework-agnostic. Receives `OptioContext` + per-request data. Owns: read-path Mongo queries, write-path RPC calls, request-to-response shaping, status-code mapping.

3. **Context layer** — new `packages/optio-api/src/context.ts`.

   Owns durable per-app resources. `OptioContext { dbOpts, engineCache, redis }`. Constructed once at adapter registration via `createOptioContext({ dbOpts, redis })`. Threaded into every handler call. After Stage B, `redis` is no longer needed for command handlers (still used by `discovery.ts` heartbeat reads, but those are separate from handler request flow). Whether `redis` stays on `OptioContext` past Stage B is a small follow-up; recommend keeping it for now and revisiting in phase 4.

## 3. Stage A — adapter-layer cleanup (six commits)

Each commit leaves the tree green: `make build && make test && make test-interop` pass.

### A0 — Robustify interop runner (no scope change to scenarios)

The phase-2 interop substrate (`run-interop.sh` + `interop/run.ts`) is sound on the happy path but provides no diagnostics when subcomponents hang. Past sessions saw multi-minute hangs from docker-port conflicts, container-not-responding states, engine-import errors, and scenario-runner deadlocks. A0 hardens it before Stage A's structural work begins.

**`run-interop.sh` changes:**

- **Pre-flight (fail in <2s):** `docker info >/dev/null` else abort with exit 10. `docker image inspect redis:7 mongo:7 >/dev/null` else `timeout 30 docker pull` per image; on pull failure abort.
- **Hard per-step timeouts.** Every blocking operation gets a bound:

  | Step | Bound |
  |---|---|
  | Redis ready (`redis-cli ping` poll) | 5 s |
  | Mongo ready (analogous) | 10 s |
  | Engine ready (heartbeat key OR `[engine] ready` log line) | 15 s |
  | Fastify ready (TCP `LISTEN` on chosen port) | 5 s |
  | Scenario runner total | 60 s |
  | Whole script | 120 s (outer `timeout 120 bash` wrapper) |

- **Phase markers.** Stdout writes `[interop] phase=docker-pre-flight`, `phase=redis-up`, `phase=mongo-up`, `phase=engine-up`, `phase=fastify-up`, `phase=running-scenarios`, `phase=cleanup`. Allows a supervising agent to read progress structurally.
- **Real-time log tailing.** Engine and (in Stage B) fastify subprocess stdout/stderr `tee`'d to `/tmp/optio-interop-engine.log` and `/tmp/optio-interop-fastify.log` AND streamed to terminal with `[engine]` / `[fastify]` prefixes. On any timeout abort, dump last 50 lines of relevant log to stderr before exit.
- **Distinct exit codes** per failure phase:

  | Code | Meaning |
  |---|---|
  | 0 | Success |
  | 10 | Docker pre-flight failed |
  | 11 | Redis not ready |
  | 12 | Mongo not ready |
  | 13 | Engine not ready |
  | 14 | Fastify not ready (Stage B) |
  | 15 | Scenario assertion failed |
  | 16 | Cleanup error |
  | 124 | Outer timeout (script-level wall clock) |

- **Cleanup is bulletproof.** EXIT trap unconditionally `docker rm -f $REDIS_CID $MONGO_CID` and `kill -9 $ENGINE_PID $FASTIFY_PID`. Idempotent: works under SIGINT, normal exit, and timeout.

**`interop/run.ts` changes:**

- Each scenario wrapped in `Promise.race([scenario, timeout(5000, scenarioName)])`.
- Console output uses structured prefixes: `[scenario] ${name} started`, `[scenario] ${name} ok (${ms}ms)`, `[scenario] ${name} failed: ${reason}`.
- Top-level `setTimeout(() => process.exit(15), 60000)` armed on entry as a safety net.
- Optional `INTEROP_FORCE_HANG=<scenarioName>` env injects an artificial hang for negative testing.

**`Makefile` changes:**

- `test-interop` target docstring documents `INTEROP_DEBUG=1` (verbose mode, increased timeouts for slow CI: 30/30/60s) and `INTEROP_KEEP=1` (skip cleanup on failure for postmortem).

**Tests:**

- Existing phase-2 scenarios green under the new timeouts.
- Negative test: `INTEROP_FORCE_HANG=launch-success make test-interop` aborts within bound and exits 15.

**Acceptance:** `make test-interop` exits 0 in <90s on a warm cache; phase-marker output present; negative-test scenario fails fast with correct exit code.

### A1 — Introduce context module

Files:

- New `packages/optio-api/src/context.ts`:

  ```ts
  import type { Redis } from 'ioredis';
  import { createEngineCache, type EngineCache } from './engine-cache.js';
  import type { DbOptions } from './resolve-db.js';

  export interface OptioContext {
    dbOpts: DbOptions;
    engineCache: EngineCache;
    redis: Redis;
  }

  export function createOptioContext(opts: { dbOpts: DbOptions; redis: Redis }): OptioContext {
    return {
      dbOpts: opts.dbOpts,
      engineCache: createEngineCache(opts.redis),
      redis: opts.redis,
    };
  }
  ```

- New `packages/optio-api/src/__tests__/context.test.ts`:
  - `createOptioContext` returns a context with the supplied `dbOpts`, an `engineCache`, and the supplied `redis`.
  - `engineCache.get` returns the same instance on repeat calls (delegation to engine-cache).
  - `engineCache.closeAll()` is idempotent.

No callers updated yet. `index.ts` exports `OptioContext` type for adapter consumption; does not export `createOptioContext` (internal).

Acceptance: `pnpm -r test` green; new context test passes.

### A2 — Read handlers migrate to ctx + adapter cleanup #2

Files:

- `packages/optio-api/src/handlers.ts`: read-handler signature changes:

  ```ts
  export interface ListProcessesQuery extends ListQuery {
    database?: string;
    prefix?: string;
  }
  export async function listProcesses(ctx: OptioContext, query: ListProcessesQuery)

  export async function getProcess(ctx, query: { database?; prefix? }, id)
  export async function getProcessTree(ctx, query: { database?; prefix?; maxDepth? }, id)
  export async function getProcessLog(ctx, query: PaginationQuery & { database?; prefix? }, id)
  export async function getProcessTreeLog(ctx, query: TreeLogQuery & { database?; prefix? }, id)
  ```

  Each handler internally does `const { db, prefix } = resolveDb(ctx.dbOpts, query)` at the top, then existing body unchanged.

- All four adapters: every read route shrinks to a one-liner of the shape

  ```ts
  list: async ({ query }) =>
    ({ status: 200 as const, body: await handlers.listProcesses(ctx, query) }),
  ```

  - Drop adapter-side default fallbacks `?? 25` (and any other adapter-side numeric defaults that duplicate contract `.default(...)` values); rely on the contract layer.
  - Inline `resolveDb` calls deleted.
  - Manual `parseInt(maxDepth, 10)` for ts-rest routes deleted (contract has `z.coerce.number()`); SSE-route equivalents stay in adapters until A4.

- `packages/optio-api/src/__tests__/handlers.test.ts`: read-handler tests rewrite for ctx. Adapter `__tests__/*.test.ts` unchanged in surface (still hit HTTP routes); they pass against the new wiring.

**Cleanup #2 verified:** `grep -nE '\?\? [0-9]' packages/optio-api/src/adapters/` returns nothing in read routes.

Acceptance: `pnpm -r test` green. `make test` green. `make test-interop` green.

### A3 — Command handlers migrate to ctx; still publish to legacy stream + adapter cleanup #3

Files:

- `packages/optio-api/src/handlers.ts`: command-handler signatures change to `(ctx, query, id, …)`:

  ```ts
  export async function launchProcess(
    ctx: OptioContext,
    query: { database?: string; prefix?: string },
    id: string,
    resume: boolean = false,
  ): Promise<CommandResult> {
    const { db, database, prefix } = resolveDb(ctx.dbOpts, query);

    const proc = await findProcessByEitherId(col(db, prefix), id);
    if (!proc) return { status: 404, body: { message: 'Process not found' } };
    if (!LAUNCHABLE_STATES.includes(proc.status.state))
      return { status: 409, body: { message: `Cannot launch process in state: ${proc.status.state}` } };
    if (resume && !proc.supportsResume)
      return { status: 409, body: { message: 'This task does not support resume' } };

    await publishLaunch(ctx.redis, database, prefix, proc.processId, resume);   // STILL LEGACY
    return { status: 200, body: toResponse(proc) };
  }
  ```

  Same shape change for `cancelProcess`, `dismissProcess`, `resyncProcesses`. Internals continue to call `publishLaunch / publishCancel / publishDismiss / publishResync` against `ctx.redis`. **The body shape stays `{message}` during Stage A** — the `{reason, message}` flip lands per-endpoint in Stage B.

- All four adapters: every command route shrinks:

  ```ts
  launch: async ({ params, body, query }) => {
    const result = await handlers.launchProcess(ctx, query, params.id, body?.resume ?? false);
    return { status: result.status, body: result.body } as any;
  },

  resync: async ({ body, query }) => {
    const result = await handlers.resyncProcesses(ctx, query, body.clean, body.metadataFilter);
    return { status: 200 as const, body: result };
  },
  ```

  - Drop `body.clean ?? false` from adapters (handler default param `clean = false` covers it).
  - Inline `resolveDb` calls in command routes deleted.

- `packages/optio-api/src/__tests__/handlers.test.ts`: command-handler tests rewrite for ctx signature; assertions still verify `publishX` called via `ctx.redis` with correct args. Existing `publisher.test.ts` unchanged.

**Cleanup #3 verified:** `grep -n 'body\.clean ??' packages/optio-api/src/adapters/` returns nothing.

Acceptance: `pnpm -r test` green. `make test` green. `make test-interop` green (legacy stream still ferries commands).

### A4 — SSE/poller helper extraction

Files:

- New `packages/optio-api/src/sse-options.ts` (name TBD; `poller-options.ts` also acceptable). Exports:

  ```ts
  export interface ParsedSseOptions {
    metadataFilter?: ProcessMetadataFilter;
    maxDepth?: number;
  }

  export function parseSseOptions(rawQuery: Record<string, unknown>): ParsedSseOptions;
  export function checkLegacyMetadataParams(rawQuery: Record<string, unknown>): void;  // throws on legacy keys
  ```

  Internally: wraps the existing `parseMetadataFilterQuery`, `detectLegacyMetadataParams`, and `maxDepth` coercion. Single call site per adapter SSE route.

- `packages/optio-api/src/__tests__/sse-options.test.ts`:
  - Happy path: valid `metadataFilter` JSON returns parsed object.
  - Invalid: malformed JSON throws or returns error (match existing `parseMetadataFilterQuery` behavior).
  - Legacy keys: `checkLegacyMetadataParams` throws on `tag=` / `taskKey=` / etc. (existing behavior).
  - `maxDepth` coercion: string `"3"` → number `3`; missing → `undefined`; invalid → error.

- All three adapters with SSE routes (express, fastify, nextjs-app): each SSE route replaces inline parsing with a single `const opts = parseSseOptions(rawQuery)` plus a single `checkLegacyMetadataParams(rawQuery)` call.

- nextjs-app's `url.searchParams.get(...)` extraction stays in the adapter (genuinely framework-specific — Next.js handler API does not auto-parse query the way ts-rest does), but is reduced to a small per-adapter helper that returns a normalized `Record<string, unknown>` which is then handed to `parseSseOptions`.

Acceptance: `pnpm -r test` green; SSE-options tests cover the parse cases. `make test` green. `make test-interop` green.

### A5 — Layer-rule docs

Files:

- `packages/optio-api/AGENTS.md`: new section `## Layer rules (binding)` containing the three-layer model from §2 above, the test ("would I write this same code in the other three adapters?"), the contract-default check, and the explicit forbidden list.
- `packages/optio-api/README.md`: short paragraph summarizing the three-layer model with a cross-link to `AGENTS.md`.
- `AGENTS.md` (root): one-liner pointer to optio-api layer rules, in the existing API section.

No code changes.

Acceptance: `make build` green (docs change only). Manual review confirms rules clearly state what is and is not allowed in adapters.

## 4. Stage B — channel swap (four commits)

Each commit changes per-endpoint behavior. Each leaves the tree green.

### Stage B prerequisites in `handlers.ts`

Reason-to-status mapping tables and message strings are added incrementally as each command commits land. By 3c they look like:

```ts
import {
  LaunchFailureReason, CancelFailureReason, DismissFailureReason,
} from 'optio-contracts';

const LAUNCH_STATUS:  Record<LaunchFailureReason,  404 | 409> = {
  'not-found':         404,
  'not-launchable':    409,
  'no-resume-support': 409,
  'launch-blocked':    409,
};
const CANCEL_STATUS:  Record<CancelFailureReason,  404 | 409> = {
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
```

The state-name detail in pre-check messages (`Cannot launch process in state: running`) is dropped in favor of the uniform `MESSAGES` table; the `reason` field on the body carries the precise discriminator.

### 3a — Launch RPC swap

Files:

- `packages/optio-contracts/src/api-to-frontend.ts`: import `LaunchFailureReason` from `engine-failure-reasons.ts`; add

  ```ts
  const LaunchErrorBody = z.object({ reason: LaunchFailureReason, message: z.string() });
  ```

  `processesContract.launch.responses` becomes `{ 200: ProcessSchema, 404: LaunchErrorBody, 409: LaunchErrorBody }`.

- `packages/optio-api/src/handlers.ts`:

  ```ts
  export type LaunchCommandResult =
    | { status: 200; body: any }
    | { status: 404 | 409; body: { reason: LaunchFailureReason; message: string } };

  export async function launchProcess(
    ctx: OptioContext,
    query: { database?: string; prefix?: string },
    id: string,
    resume: boolean = false,
  ): Promise<LaunchCommandResult> {
    const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
    const engine = ctx.engineCache.get(database, prefix);

    const proc = await findProcessByEitherId(col(db, prefix), id);
    if (!proc) return launchFail('not-found');
    if (!LAUNCHABLE_STATES.includes(proc.status.state)) return launchFail('not-launchable');
    if (resume && !proc.supportsResume) return launchFail('no-resume-support');

    const result = await engine.launch({ processId: proc.processId, resume });
    if (result.ok) return { status: 200, body: toResponse(result.process) };
    return launchFail(result.reason);
  }

  function launchFail(reason: LaunchFailureReason): LaunchCommandResult {
    return { status: LAUNCH_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
  }
  ```

  `LAUNCH_STATUS` and the launch slice of `MESSAGES` added to the file.

- All four adapters: launch route unchanged in shape — already the one-liner from A3 — but `result.body` is now `{reason, message}` on failures, which ts-rest validates against the new `LaunchErrorBody`.

- `packages/optio-api/src/__tests__/handlers.test.ts`: launch suite rewritten. Mocks `EngineClient.launch` (vitest `vi.fn()`); asserts call args (`{processId, resume}`) plus mapping of every failure reason to status + body. Pre-check tests stay (still valid behavior).

- `packages/optio-api/src/adapters/__tests__/*.test.ts`: launch route assertions updated for `{reason, message}` body. Stub `EngineClient` (or use clamator's `MemoryRpcClient` if available — verify during commit; if absent, hand-roll a minimal stub returning configured discriminated-union results).

- New `packages/optio-demo/interop/run-http.ts`: HTTP-roundtrip scenarios for launch:
  1. POST `/api/processes/opencode-demo/launch` → 200 with valid `process` body in scheduled/running state.
  2. POST `/api/processes/bogus-id/launch` → 404 with `{reason: 'not-found', message: ...}`.
  3. Launch when already running → 409 with `{reason: 'not-launchable', ...}`.
  4. Launch with `{resume: true}` on no-resume-support task → 409 `{reason: 'no-resume-support', ...}`.
  5. After `engine.blockLaunches({...})` matches, launch → 409 `{reason: 'launch-blocked', ...}`. Cleanup with `engine.unblockLaunches`.

  `run-http.ts` constructs a fastify server, registers `optio-api/fastify` against the same redis + mongo as the engine, and uses `fetch` (or `undici`) for HTTP calls.

- `packages/optio-demo/run-interop.sh`: spawn the fastify server alongside the engine subprocess, wait for `LISTEN` on its port (≤5s), run `run-http.ts` after `run.ts`. Capture exit codes from both runners; aggregate.

Acceptance: `pnpm -r test` green; new launch unit/adapter assertions pass. `make test-interop` green; HTTP-launch scenarios all assert; existing direct-clamator scenarios still pass.

### 3b — Cancel RPC swap

Same pattern as 3a. New `CancelErrorBody` in `api-to-frontend.ts`. New `CancelCommandResult` in `handlers.ts`. `cancelProcess` body rewritten; `cancelFail` helper added. `CANCEL_STATUS` and the cancel slice of `MESSAGES` added. Adapter call sites unchanged in shape. Unit, adapter, and HTTP-interop tests added per cancel.

Acceptance: `pnpm -r test` green; `make test-interop` green; cancel HTTP scenarios cover 200, 404, and 409 (`not-cancellable`).

### 3c — Dismiss RPC swap

Same pattern. `DismissErrorBody`. `DismissCommandResult`. `dismissFail`. `DISMISS_STATUS` + dismiss slice of `MESSAGES`. Tests at all three layers per dismiss.

Acceptance: `pnpm -r test` green; `make test-interop` green; dismiss scenarios cover 200, 404, 409 (`not-dismissable`).

### 3d — Resync RPC swap + delete legacy publisher

Files:

- `packages/optio-contracts/src/contract.ts`: `processesContract.resync.responses` becomes `{ 202: z.object({ message: z.string() }) }` (was `200`). The body schema is unchanged.

- `packages/optio-api/src/handlers.ts`:

  ```ts
  export async function resyncProcesses(
    ctx: OptioContext,
    query: { database?: string; prefix?: string },
    clean: boolean = false,
    metadataFilter?: ProcessMetadataFilter,
  ): Promise<{ message: string }> {
    const { database, prefix } = resolveDb(ctx.dbOpts, query);
    const engine = ctx.engineCache.get(database, prefix);
    await engine.resync({ clean, metadataFilter });   // notification, returns void
    return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
  }
  ```

- All four adapters: resync route adjusts return status to 202:

  ```ts
  resync: async ({ body, query }) => {
    const result = await handlers.resyncProcesses(ctx, query, body.clean, body.metadataFilter);
    return { status: 202 as const, body: result };
  },
  ```

- `packages/optio-api/src/publisher.ts` — **deleted**.
- `packages/optio-api/src/__tests__/publisher.test.ts` — **deleted**.
- `packages/optio-api/src/index.ts` — remove `publishLaunch`, `publishCancel`, `publishDismiss`, `publishResync` exports.

- `packages/optio-api/src/__tests__/handlers.test.ts`: resync suite rewritten. Mocks `engine.resync` notification; asserts call args.

- `packages/optio-api/src/adapters/__tests__/*.test.ts`: resync route assertions updated for status 202. Adjust any ts-rest contract expectation in tests that asserted 200.

- `packages/optio-demo/interop/run-http.ts`: resync scenario — POST `/api/processes/resync` with `{clean: true}` → 202 with `{message: 'Nuke and resync requested'}`. After the call, verify the engine processed the notification (e.g., a definition-load timestamp advances within a 5s bound).

- `packages/optio-ui` and `packages/optio-dashboard`: no code change needed. ts-rest mutation hooks are status-code-agnostic (verified by reading `useProcessActions.ts:20` — `useMutation` callback receives `data` regardless of which 2xx fires).

**Verification gates:**

- `grep -rn 'publishLaunch\|publishCancel\|publishDismiss\|publishResync\|publisher\.' packages/optio-api/src/` returns nothing.
- `grep -n '200' packages/optio-contracts/src/contract.ts | grep -i resync` returns nothing.

Acceptance: `pnpm -r test` green. `make test` green. `make test-interop` green; resync HTTP scenario asserts 202 + body. `redis-cli xrange "${db}/${prefix}:cmds:engine" -` after the HTTP test suite shows entries; `redis-cli xrange "${db}/${prefix}:commands"` shows nothing for entries originating from API code (the legacy stream is still consumed by the engine through phase 5, but no API code writes to it).

## 5. Test plan summary

### Layers

- **`packages/optio-api/src/__tests__/`**: pure-function unit tests for handlers and the new `context.ts`, `sse-options.ts`. Mock `EngineClient` (Stage B) or rely on real `publishX` against an ioredis mock (Stage A and pre-3a).
- **`packages/optio-api/src/adapters/__tests__/`**: per-adapter integration tests. Existing fastify / express / nextjs-{app,pages} test files; assert HTTP request → handler → response cycle. Stage B updates body-shape and status-code assertions per adapter.
- **`packages/optio-demo/interop/`**: real-redis + real-engine + (Stage B) real-fastify roundtrip. `run.ts` for direct-clamator scenarios (existing), `run-http.ts` (new in 3a) for HTTP-roundtrip scenarios.

### Per-stage acceptance gates

**Stage A:** at every commit, `pnpm -r test`, `make test`, `make test-interop` green. No interop scenarios added; existing scenarios cover the regression surface (legacy stream behavior unchanged).

**Stage B:** at every commit, all of Stage A's gates plus new HTTP-roundtrip scenarios for the endpoint switched in that commit. By 3d, the HTTP-interop matrix covers: launch (200, 404, 409 × 4 reasons), cancel (200, 404, 409 × 1), dismiss (200, 404, 409 × 1), resync (202).

### What is NOT tested in phase 3

- Adversarial test forcing every failure reason from a state the API's pre-check would have rejected (i.e., racing pre-check against the engine). Phase 4 plan covers this once the pre-check is gone.
- External-consumer (Excavator) HTTP integration. Tracked separately.
- Performance regression vs. fire-and-forget publish. Phase 3 expects an additive RPC-roundtrip latency on each command call (engine responds in milliseconds per phase-2 measurements), but no formal benchmark.

## 6. Branch and merge

- Phase 3 work happens on branch `csillag/rpc-migration-phase-3`, in the existing git worktree at `.worktrees/rpc-migration-phase-3/`. Branch is already cut from main HEAD `1b0feeb` (phase 2 landed). User explicitly chose worktree for this phase, overriding the project-memory default of in-place feature branches.
- Commits land in order A0 → A1 → A2 → A3 → A4 → A5 → 3a → 3b → 3c → 3d. Each is independently green.
- Merge after 3d via the project's standard finishing-a-development-branch flow (drift check, rebase if needed, fast-forward or squash-merge per the user's preference at completion time).

## 7. Risks

- **HTTP-interop adds a fastify dependency to `optio-demo/interop/`.** The TS subpackage already depends on `optio-api`; `optio-api/fastify` brings in `fastify` peer-dep. Confirm during 3a; if it causes resolution issues, fall back to a hand-rolled `node:http` server registering the express adapter (smaller surface, no peer-dep). Adapter integration tests for fastify already exist independently.

- **clamator `MemoryRpcClient` availability for adapter tests.** If clamator does not export a `MemoryRpcClient` for TS, hand-roll a minimal stub during 3a. Don't spend phase-3 time pursuing clamator upstream; stub is fine.

- **Engine-failure-reason set goes stale between engine and contract.** Stage B introduces three reason-to-status tables in `handlers.ts`. If a future engine change adds a new reason without updating the contract enum and the table, TypeScript will reject the change at compile time (`Record<LaunchFailureReason, …>` exhaustiveness). Contract enum is the single source; codegen drift is detected by the existing pre-commit hook from phase 1.

- **Race: pre-check passes, engine returns failure.** Possible if process state changes between the pre-check Mongo read and the RPC call (~milliseconds). Handler maps engine failure correctly; behavior is uniform with engine-only path; no regression. Phase 4 deletes the pre-check, eliminating the race.

- **Interop runtime growth.** Stage B adds a fastify server and HTTP roundtrip overhead. Total interop budget held to 90s soft / 120s hard via A0's bounds.

- **Adapter-layer rule documentation goes stale.** A5 documents rules, but if subsequent commits violate them, the rules become decorative. Mitigation: include the AGENTS.md text verbatim in the Stage A commit message for A2 / A3 / A4 so each cleanup commit reinforces the rule's intent.

## 8. Out of scope

- **Phase 4 work.** Pre-RPC validation deletion (`LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES`, `cancellable` and `supportsResume` checks, `findProcessByEitherId` pre-read in command handlers). Removal of `db` and `prefix` from command-handler signatures.
- **Phase 5 work.** Engine-side `consumer.py` removal; legacy `${prefix}:commands` stream retirement; `optio_core.on_command` removal.
- **Excavator port.** Tracked via project memory; no API contract guarantees beyond what already exists.
- **Architecture diagram refresh.** Text rules in AGENTS.md only.
- **CI workflow bootstrapping.** Tracked separately.
- **Cross-adapter parity audit beyond the items listed in §2.** A5's rule prevents future drift; an exhaustive audit of every line in every adapter file is out of scope (the listed items are the known major offenders).

## 9. Parent-spec corrections (applied during phase 3 commits as relevant)

- §5 phase-4 spec lists `publisher.ts` deletion in phase 4. Phase 3 design (this doc) supersedes that placement; the deletion happens in 3d. Parent spec phase-4 section to be amended in commit 3d's docs.
- §3 phase-4 spec shows per-command error bodies (`LaunchErrorBody` / `CancelErrorBody` / `DismissErrorBody`) landing in phase 4 alongside the handler rewrite. Phase 3 design pulls the body-shape flip forward; per-command schemas land in `api-to-frontend.ts` per-endpoint in 3a / 3b / 3c. Parent spec to be amended in 3a's docs commit (or as a small standalone doc-only commit between A5 and 3a, optional).
- §6 phase-4 entry "`CommandResult` doc — body now `{ reason, message }`" — applies in phase 3 for the reasons above.

## 10. Open questions deferred to phase plans

- Whether `OptioContext` should be exported from `optio-api/index.ts` for custom-adapter authors. Recommendation: yes — same rationale as `EngineClient` and `EngineCache` exports from phase 2. Decide during A1.
- Whether the `redis` field on `OptioContext` survives past Stage B. Recommendation: keep through phase 4; revisit when `db` and `prefix` are also stripped.
- Whether the new SSE-options helper module is named `sse-options.ts` or `poller-options.ts`. Cosmetic; decide during A4. (`sse-options.ts` is closer to the consumer terminology.)
- Whether to ship a `MemoryRpcClient` stub of our own in `optio-api/test-utils/` for downstream adapter authors. Out of phase 3; tracked as a separate hygiene task.
