# optio-contracts — LLM Reference

## Package

- **Name:** `optio-contracts`
- **Version:** `0.1.0` (private, not published to npm)
- **Type:** ESM (`"type": "module"`)
- **Entry point:** `dist/index.js` / `dist/index.d.ts`
- **Runtime deps:** `@ts-rest/core ^3.51.0`, `zod ^3.24.0`
- **Install note:** Internal monorepo package — referenced via workspace path, never installed from a registry.

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

Standard error body returned on 404 and 409 responses.

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
| `supportsResume` | `boolean` | optional — task opted into resume/checkpoint support |
| `hasSavedState` | `boolean` | optional — task has a valid checkpoint ready to restore |
| `warning` | `string` | optional |
| `status` | `ProcessStatusSchema` | see below |
| `progress` | `ProgressSchema` | see below |
| `log` | `LogEntry[]` | |
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

ts-rest router exported from `api-to-frontend.ts`. All paths use a `:prefix` segment that scopes
processes to a named domain (e.g., a specific application or worker).

| Name | Method | Path | Path Params | Query Params | Response Codes |
|------|--------|------|-------------|--------------|----------------|
| `list` | GET | `/processes/:prefix` | `prefix: string` | `cursor?`, `limit` (1–100, default 20), `rootId?: ObjectId`, `state?: ProcessState`, `metadataFilter?: <URL-encoded JSON>` (parsed via `MetadataFilterQueryParamSchema`) | 200: `PaginatedResponse<Process>` |
| `get` | GET | `/processes/:prefix/:id` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error` |
| `getTree` | GET | `/processes/:prefix/:id/tree` | `prefix: string`, `id: ObjectId` | `maxDepth?: number` (int, min 0) | 200: `ProcessTreeNode`, 404: `Error` |
| `getLog` | GET | `/processes/:prefix/:id/log` | `prefix: string`, `id: ObjectId` | `cursor?`, `limit` (1–100, default 20) | 200: `PaginatedResponse<LogEntry>`, 404: `Error` |
| `getTreeLog` | GET | `/processes/:prefix/:id/tree/log` | `prefix: string`, `id: ObjectId` | `cursor?`, `limit` (1–100, default 20), `maxDepth?: number` (int, min 0) | 200: `PaginatedResponse<LogEntry & { processId: ObjectId, processLabel: string }>`, 404: `Error` |
| `launch` | POST | `/processes/:prefix/:id/launch` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error`, 409: `Error` (body: `{ resume?: boolean }` — optional; omitting the body entirely is valid) |
| `cancel` | POST | `/processes/:prefix/:id/cancel` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error`, 409: `Error` |
| `dismiss` | POST | `/processes/:prefix/:id/dismiss` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error`, 409: `Error` |
| `resync` | POST | `/processes/:prefix/resync` | `prefix: string` | — | 200: `{ message: string }` (body: `{ clean?: boolean; metadataFilter?: ProcessMetadataFilter }` — both optional) |

**Notes on specific endpoints:**

- `list` — filters are all optional and combinable. `rootId` scopes results to a process subtree. `state` accepts any `ProcessState` value. `metadataFilter` is a URL-encoded JSON string; the contract's transform schema (`MetadataFilterQueryParamSchema`) parses it into a `ProcessMetadataFilter` (an exact-match map). The legacy `metadata.*` prefix form has been removed and now returns 400.
- `getTree` — returns a `ProcessTreeNode`: a `Process` extended with `children: ProcessTreeNode[]` (recursive). `maxDepth` limits traversal depth.
- `getTreeLog` — returns merged log entries across the process subtree, each augmented with `processId` (ObjectId) and `processLabel` (string) to identify the source process.
- `launch` — optional body `{ resume?: boolean }`. Clients may omit the body entirely; setting `resume: true` requests a resume. 409 indicates a state conflict (e.g., launching an already-running process) **or** `resume: true` against a task whose `supportsResume` is `false`.
- `cancel` / `dismiss` — no request body. 409 indicates a state conflict.
- `resync` — body: `{ clean?: boolean; metadataFilter?: ProcessMetadataFilter }`. Triggers re-sync of process definitions from the server-side registry. `metadataFilter` (when present) scopes the re-sync to tasks whose stored metadata matches.
