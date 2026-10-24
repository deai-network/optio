# optio-contracts — LLM Reference

## Package

- **Name:** `optio-contracts`
- **Version:** `0.6.1` (published to npm)
- **Type:** ESM (`"type": "module"`)
- **Entry points:** `optio-contracts` → `dist/index.js` / `dist/index.d.ts`; `optio-contracts/optio-engine` → `dist/optio-engine-to-api.js` / `dist/optio-engine-to-api.d.ts` (the clamator contract, which pulls in `@clamator/protocol`)
- **Runtime deps:** `@clamator/protocol ^0.1.9`, `@ts-rest/core ^3.51.0`, `filtrum-core` (workspace), `zod ^3.24.0`
- **Install note:** Inside the monorepo it is referenced via workspace path; outside it, install from npm (`npm install optio-contracts`).

## Package structure

The package hosts two typed contracts that define optio's internal communication surfaces:

| File | Contract type | Purpose |
|------|---------------|---------|
| `src/api-to-frontend.ts` | ts-rest HTTP contract | What `optio-api` exposes to its REST clients (UI, external integrations). Used by `optio-ui` to construct typed clients and by `optio-api` to register typed handlers. |
| `src/optio-engine-to-api.ts` | clamator RPC contract | What `optio-core` (the engine) exposes to its RPC callers (typically `optio-api`, plus any consumer app like Excavator). Used to issue typed RPC calls and to implement typed handlers. |
| `src/schemas/` | Shared Zod schemas | Common types used by both contracts. `common.ts` holds generic primitives (ObjectId, Pagination, Error). `process.ts` holds process-domain types (Process, ProcessState, LogEntry, ProcessMetadataFilter). |

### Naming convention

Contract files follow `<server>-to-<client>.ts`, where the **server** is the side that exposes the contract and the **client** is the side that consumes it. For example, in `optio-engine-to-api.ts`, the engine exposes methods that the API calls. The "to" indicates exposure, not call direction.

### Codegen

The clamator contract (`optio-engine-to-api.ts`, exporting `optioEngineContract`) is the single source of truth for the RPC surface. clamator's codegen produces matching wrappers in both languages:

- **TypeScript output:** `packages/optio-api/src/_generated/optio-engine.ts` — `OptioEngineClient` class, params/result types.
- **Python output:** `packages/optio-core/src/optio_core/_generated/optio_engine.py` — Pydantic models, `OptioEngineService` ABC, `optio_engine_contract` var. Filename is post-processed (hyphen → underscore) in the Makefile codegen target so Python can import it.

Generated files are committed. Regenerate via `make codegen` at the repo root. A pre-commit hook runs codegen and fails on `git diff` non-empty under `_generated/` paths to catch drift.

The wire service name is `'optio-engine'` (clamator requires `^[a-z][a-z0-9-]*$`); this is what travels in redis stream keys (e.g., `<keyPrefix>:cmds:optio-engine`, with `keyPrefix` = `<database>/<prefix>`).

The HTTP contract (`api-to-frontend.ts`) does not require codegen: ts-rest builds typed clients and handlers from the contract object via TypeScript's type system at the consumer's compile time.

## Schemas

### `ObjectIdSchema`

```ts
z.string().regex(/^[a-f\d]{24}$/i)
```

24-character hex string. Validates MongoDB ObjectIds.

---

### `PaginationQuerySchema`

| Field | Type | Constraints | Default |
|-------|------|-------------|---------|
| `cursor` | `string` | optional | — |
| `limit` | `number` (coerced) | int, min 1, max 100 | `20` |

Cursor-based pagination. Used as the base for extended query schemas.

---

### `PaginatedResponseSchema<T>`

Generic factory — call with a Zod item schema to produce:

| Field | Type |
|-------|------|
| `items` | `T[]` |
| `nextCursor` | `string \| null` |
| `totalCount` | `number` (int) |

---

### `ErrorSchema`

| Field | Type |
|-------|------|
| `message` | `string` |

Standard error body of the read routes' 404 responses (and `getConversation`'s 401). The command routes' 404/409 bodies are typed `{ reason, message }` instead.

---

### `DateSchema`

```ts
z.coerce.date()
```

Accepts ISO strings and coerces them to `Date` objects.

---

### `ProcessStateSchema`

Enum of all valid process lifecycle states:

```ts
z.enum(['idle', 'scheduled', 'running', 'done', 'failed',
        'cancel_requested', 'cancelling', 'cancelled'])
```

---

### `LogEntrySchema`

| Field | Type | Constraints |
|-------|------|-------------|
| `timestamp` | `Date` | coerced |
| `level` | `string` | enum: `event`, `info`, `debug`, `warning`, `error` |
| `message` | `string` | |
| `data` | `Record<string, unknown>` | optional |

---

### `ProcessSchema`

| Field | Type | Constraints |
|-------|------|-------------|
| `_id` | `string` | ObjectId format |
| `processId` | `string` | |
| `name` | `string` | |
| `description` | `string \| null` | nullable, optional |
| `params` | `Record<string, unknown>` | optional |
| `metadata` | `Record<string, unknown>` | optional |
| `parentId` | `string` | optional, ObjectId format |
| `rootId` | `string` | ObjectId format |
| `depth` | `number` | int, min 0 |
| `order` | `number` | int, min 0 |
| `cancellable` | `boolean` | |
| `special` | `boolean` | optional |
| `taskFactory` | `boolean` | optional — the record is a `TaskFactory` (missing = false) |
| `parameters` | `unknown[]` | optional — a factory's field definitions as data (optio-ui types them with vultus-core) |
| `ttlSeconds` | `number \| null` | optional — how long the record is kept after its run ended, in seconds (a factory's is its launches'); every record has it, null when it has no TTL (kept until dismissed) |
| `supportsResume` | `boolean` | optional — task opted into resume/checkpoint support |
| `hasSavedState` | `boolean` | optional — task has a valid checkpoint ready to restore |
| `supportsResurrect` | `boolean` | optional — task has a resurrect hook (missing = false) |
| `hasUnsavedWork` | `boolean` | optional — the host workdir holds work newer than the last snapshot (missing = false) |
| `resurrecting` | `boolean` | optional — transient, true while a resurrect saves the unsaved work (missing = false) |
| `autoResumeScheduled` | `boolean` | optional — the engine will re-launch this cancelled, state-saved top-level process (resume) after boot |
| `warning` | `string` | optional |
| `status` | `ProcessStatusSchema` | see below |
| `progress` | `ProgressSchema` | see below |
| `log` | `LogEntry[]` | |
| `result` | `{ filename: string, contentType: string, size: number }` | optional — the run's result file (`ctx.store_result`), `size` in bytes (int, min 0); `getResult` serves it. Its GridFS id (`result.fileId`) is server-side only and never appears here |
| `uiWidget` | `string \| null` | nullable, optional |
| `widgetData` | `unknown` | optional (`widgetUpstream` and `controlUpstream` are server-side only and never appear here) |
| `browserOpenRequests` | `BrowserOpenRequest[]` | optional — `{ requestId, url, createdAt? }` |
| `sessionEvents` | `SessionEvent[]` | optional — `{ requestId, type: 'attention', reason }` or `{ requestId, type: 'client', keyword, data }` |
| `originatingSessionId` | `string \| null` | nullable, optional |
| `createdAt` | `Date` | coerced |

**Embedded `status` object** (not exported separately):

| Field | Type | Constraints |
|-------|------|-------------|
| `state` | `ProcessState` | enum |
| `error` | `string` | optional |
| `runningSince` | `Date` | optional, coerced |
| `doneAt` | `Date` | optional, coerced |
| `duration` | `number` | optional |
| `failedAt` | `Date` | optional, coerced |
| `stoppedAt` | `Date` | optional, coerced |

**Embedded `progress` object** (not exported separately):

| Field | Type | Constraints |
|-------|------|-------------|
| `percent` | `number \| null` | min 0, max 100 |
| `message` | `string` | optional |

## Types

### `ProcessState`

```ts
type ProcessState = 'idle' | 'scheduled' | 'running' | 'done' | 'failed'
                 | 'cancel_requested' | 'cancelling' | 'cancelled'
```

### `LogEntry`

```ts
type LogEntry = {
  timestamp: Date;
  level: 'event' | 'info' | 'debug' | 'warning' | 'error';
  message: string;
  data?: Record<string, unknown>;
}
```

### `Process`

Full inferred type from `ProcessSchema` — see schema table above for all fields.

## Contract: processesContract

ts-rest router exported from `api-to-frontend.ts`. No path has a `:prefix` segment: every route
takes the optional query parameters `database?` and `prefix?`, which select the optio instance
(optio-api defaults the prefix to `'optio'`; `database` is required only in multi-db mode). `id`
is `ProcessIdParamSchema` (a non-empty string): either the process `_id` (ObjectId hex) or its
`processId`.

| Name | Method | Path | Path Params | Query Params | Response Codes |
|------|--------|------|-------------|--------------|----------------|
| `list` | GET | `/processes` | — | `database?`, `prefix?`, `cursor?`, `limit` (1–100, default 20), `rootId?: ObjectId`, `state?: ProcessState`, `metadataFilter?: <URL-encoded JSON>` (parsed via `MetadataFilterQueryParamSchema`) | 200: `PaginatedResponse<Process>` |
| `get` | GET | `/processes/:id` | `id: string` | `database?`, `prefix?` | 200: `Process`, 404: `Error` |
| `getResult` | GET | `/processes/:id/result` | `id: string` | `database?`, `prefix?` | 200: the result file's bytes (declared `c.otherResponse({ contentType: 'application/octet-stream', body: z.unknown() })`), 404: `{ reason: 'not-found' \| 'no-result', message: string }` |
| `getTree` | GET | `/processes/:id/tree` | `id: string` | `database?`, `prefix?`, `maxDepth?: number` (int, min 0) | 200: `ProcessTreeNode`, 404: `Error` |
| `getLog` | GET | `/processes/:id/log` | `id: string` | `database?`, `prefix?`, `cursor?`, `limit` (1–100, default 20) | 200: `PaginatedResponse<LogEntry>`, 404: `Error` |
| `getTreeLog` | GET | `/processes/:id/tree/log` | `id: string` | `database?`, `prefix?`, `cursor?`, `limit` (1–100, default 20), `maxDepth?: number` (int, min 0) | 200: `PaginatedResponse<LogEntry & { processId: ObjectId, processLabel: string }>`, 404: `Error` |
| `getConversation` | GET | `/processes/:id/conversation` | `id: string` | `database?`, `prefix?` | 200: `ConversationRecord` (`{ events: unknown[], capturedAt: string \| null }`), 401: `Error`, 403/404/409/500/503: `{ reason: ConversationFailureReason, message: string }` |
| `launch` | POST | `/processes/:id/launch` | `id: string` | `database?`, `prefix?` | 200: `Process`, 400/404/409: `{ reason: LaunchFailureReason, message: string }` (body: `{ resume?: boolean; sessionId?: string \| null; params?: Record<string, unknown>; ephemeral?: boolean }` — optional; omitting the body entirely is valid) |
| `cancel` | POST | `/processes/:id/cancel` | `id: string` | `database?`, `prefix?` | 200: `Process`, 404/409: `{ reason: CancelFailureReason, message: string }` |
| `dismiss` | POST | `/processes/:id/dismiss` | `id: string` | `database?`, `prefix?` | 200: `Process \| null` (null: dismissing deleted the record, an ended factory launch), 404/409: `{ reason: DismissFailureReason, message: string }` |
| `resurrect` | POST | `/processes/:id/resurrect` | `id: string` | `database?`, `prefix?` | 200: `Process`, 404/409: `{ reason: ResurrectFailureReason, message: string }` (body: `{ sessionId?: string \| null }` — optional; omitting the body entirely is valid) |
| `resync` | POST | `/processes/resync` | — | `database?`, `prefix?` | 202: `{ message: string }` (body: `{ clean?: boolean; metadataFilter?: ProcessMetadataFilter }` — both optional) |

optio-api mounts the contract under `/api` (`/api/processes/...`). The same file exports
`discoveryContract`: `instances`, GET `/optio/instances`, 200:
`{ instances: Array<{ database: string, prefix: string, live: boolean }> }`.

**Notes on specific endpoints:**

- `list` — filters are all optional and combinable. `rootId` scopes results to a process subtree. `state` accepts any `ProcessState` value. `metadataFilter` is a URL-encoded JSON string; the contract's transform schema (`MetadataFilterQueryParamSchema`) parses it into a `ProcessMetadataFilter` (a predicate tree, or a legacy flat exact-match map). The legacy `metadata.*` prefix form has been removed and now returns 400.
- `getResult` — the process's result file (its `result`). The 200 is the file's bytes, with `Content-Type` the result's `contentType` (the contract's `application/octet-stream` is nominal), `Content-Length` its size and `Content-Disposition: inline` with its name. 404 `no-result`: the process has no result; 404 `not-found`: no such process, or one the request may not see (as `getConversation` answers).
- `getTree` — returns a `ProcessTreeNode`: a `Process` extended with `children: ProcessTreeNode[]` (recursive). `maxDepth` limits traversal depth.
- `getTreeLog` — returns merged log entries across the process subtree, each augmented with `processId` (ObjectId) and `processLabel` (string) to identify the source process.
- `launch` — optional body `{ resume?: boolean; sessionId?: string | null; params?: Record<string, unknown>; ephemeral?: boolean }`. Clients may omit the body entirely; setting `resume: true` requests a resume; `sessionId` is the initiating session; `params` are a task factory's parameter values (the record's `parameters`), and a factory's launch answers 200 with the child's record; `ephemeral` overrides the factory's default retention of that record (true: deleted when it ends; false: kept until dismissed), and is refused with `not-a-factory` for a process that is no factory. The body carries no `metadata`: a factory launch's stamp comes only from optio-api's `launchMetadata` hook (the engine RPC `launch`'s `metadata`). 400 is `parameters-required`, `invalid-parameters` (whose `message` is optio-core's `"<id>: <what>"` text, unchanged) or `invalid-metadata` (the stamp collided with the factory's metadata; its `message`, naming the keys, is optio-core's text, unchanged); 404 is `not-found`; 409 is every other reason: a state conflict (`not-launchable`, e.g. launching an already-running process or a factory's launch again), `resume: true` against a task whose `supportsResume` is `false` (`no-resume-support`), `launch-blocked`, `not-a-factory` and `shutting-down`.
- `cancel` / `dismiss` — no request body. 409 indicates a state conflict. Dismissing an ended factory launch deletes its record: `dismiss` answers 200 with a `null` body (engine RPC: `{ ok: true }` without `process`).
- `getConversation` — the saved conversation record of a suspended process (cancelled, `supportsResume`, `hasSavedState`), read-only; optio-api proxies it from the engine's conversation archive, which decrypts. `events` are `[seq, event]` pairs (the recent part a resume replays); `capturedAt` is the ISO time of the snapshot, or null. The 401 is optio-api's authentication (`{ message }`); the others carry a `ConversationFailureReason`: 403 `forbidden`; 404 `not-found` / `no-conversation-support` / `no-conversation`; 409 `not-suspended`; 500 `read-failed`; 503 `engine-unavailable`.
- `resurrect` — saves the work a failed run left on its host, then resumes (engine RPC `resurrect`). Optional body `{ sessionId?: string | null }`, the initiating session. Error responses carry a typed `{ reason, message }` body, `reason` being a `ResurrectFailureReason`.
- `resync` — body: `{ clean?: boolean; metadataFilter?: ProcessMetadataFilter }`. Triggers re-sync of process definitions from the server-side registry. `metadataFilter` (when present) scopes the re-sync to tasks whose stored metadata matches.

## Contract: optioEngineContract

clamator RPC contract exported from `optio-engine-to-api.ts` (service `'optio-engine'`).
`processId` is a non-empty string; `process` is a `Process`.

| Method | Params | Result |
|--------|--------|--------|
| `launch` | `{ processId, resume?: boolean, sessionId: string \| null, params?: Record<string, unknown>, ephemeral?: boolean, metadata?: Record<string, string \| number \| boolean \| null> }` | `{ ok: true, process } \| { ok: false, reason: LaunchFailureReason, message?: string }` |
| `cancel` | `{ processId }` | `{ ok: true, process } \| { ok: false, reason: CancelFailureReason }` |
| `dismiss` | `{ processId }` | `{ ok: true, process? } (no process: the record was deleted) \| { ok: false, reason: DismissFailureReason }` |
| `resurrect` | `{ processId, sessionId: string \| null }` | `{ ok: true, process } \| { ok: false, reason: ResurrectFailureReason }` |
| `groupCancel` / `groupCancelAndWait` | `{ metadataFilter, blockNewLaunches?, persist?, reason?, purgeRecords? }` | `{ ok: true, cancelledCount } \| { ok: false, reason: GroupCancelFailureReason }` |
| `blockLaunches` | `{ launchFilter, reason? }` | `{ ok: true } \| { ok: false, reason: BlockLaunchesFailureReason }` |
| `unblockLaunches` | `{ launchFilter }` | `{ removed: number }` |
| `materializeUpload` | `{ processId, blobId, filename }` | `{ ok: true, path } \| { ok: false, reason: string }` |
| `resync` (notification) | `{ clean?, metadataFilter? }` | — |

`sessionId` on `launch` and `resurrect` is required but nullable: the initiating session
token, or explicit `null` for unattended calls.

`metadata` on `launch` is a task factory launch's stamp (optio-api's `launchMetadata` hook,
server-side only), merged into the new launch's metadata; a key of the factory's own metadata
or the reserved `taskFactory` refuses the launch with `invalid-metadata`, its `message` naming
the keys.

## Failure-reason enums

Zod enums (each also exported as a type of the same name) in `engine-failure-reasons.ts`,
re-exported from the package root. Browser-safe: they do not pull in `@clamator/protocol`.

| Enum | Values |
|------|--------|
| `LaunchFailureReason` | `not-found`, `not-launchable`, `no-resume-support`, `launch-blocked`, `shutting-down`, `parameters-required`, `not-a-factory`, `invalid-parameters`, `invalid-metadata` (every reason optio-core's `launch` can return; a refusal may carry `message`, the text of `invalid-parameters` or `invalid-metadata`) |
| `CancelFailureReason` | `not-found`, `not-cancellable` |
| `DismissFailureReason` | `not-found`, `not-dismissable` |
| `ResurrectFailureReason` | `not-found`, `not-resurrectable`, `no-resurrect-support`, `resurrect-in-progress`, `launch-blocked`, `shutting-down` |
| `GroupCancelFailureReason` | `invalid-persist-without-block` |
| `BlockLaunchesFailureReason` | `invalid-filter` |
| `ConversationFailureReason` (in `api-to-frontend.ts`) | `forbidden`, `not-found`, `no-conversation-support`, `no-conversation`, `not-suspended`, `read-failed`, `engine-unavailable` |
