# optio-contracts — LLM Reference

## Package

- **Name:** `optio-contracts`
- **Version:** `0.1.0` (private, not published to npm)
- **Type:** ESM (`"type": "module"`)
- **Entry point:** `dist/index.js` / `dist/index.d.ts`
- **Runtime deps:** `@ts-rest/core ^3.51.0`, `zod ^3.24.0`
- **Install note:** Internal monorepo package — referenced via workspace path, never installed from a registry.

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

ts-rest router exported from `contract.ts`. All paths use a `:prefix` segment that scopes
processes to a named domain (e.g., a specific application or worker).

| Name | Method | Path | Path Params | Query Params | Response Codes |
|------|--------|------|-------------|--------------|----------------|
| `list` | GET | `/processes/:prefix` | `prefix: string` | `cursor?`, `limit` (1–100, default 20), `rootId?: ObjectId`, `state?: ProcessState`, plus arbitrary `metadata.*` keys via `.passthrough()` | 200: `PaginatedResponse<Process>` |
| `get` | GET | `/processes/:prefix/:id` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error` |
| `getTree` | GET | `/processes/:prefix/:id/tree` | `prefix: string`, `id: ObjectId` | `maxDepth?: number` (int, min 0) | 200: `ProcessTreeNode`, 404: `Error` |
| `getLog` | GET | `/processes/:prefix/:id/log` | `prefix: string`, `id: ObjectId` | `cursor?`, `limit` (1–100, default 20) | 200: `PaginatedResponse<LogEntry>`, 404: `Error` |
| `getTreeLog` | GET | `/processes/:prefix/:id/tree/log` | `prefix: string`, `id: ObjectId` | `cursor?`, `limit` (1–100, default 20), `maxDepth?: number` (int, min 0) | 200: `PaginatedResponse<LogEntry & { processId: ObjectId, processLabel: string }>`, 404: `Error` |
| `launch` | POST | `/processes/:prefix/:id/launch` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error`, 409: `Error` |
| `cancel` | POST | `/processes/:prefix/:id/cancel` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error`, 409: `Error` |
| `dismiss` | POST | `/processes/:prefix/:id/dismiss` | `prefix: string`, `id: ObjectId` | — | 200: `Process`, 404: `Error`, 409: `Error` |
| `resync` | POST | `/processes/:prefix/resync` | `prefix: string` | — | 200: `{ message: string }` |

**Notes on specific endpoints:**

- `list` — filters are all optional and combinable. `rootId` scopes results to a process subtree. `state` accepts any `ProcessState` value. Additional `metadata.*` query params are passed through for metadata filtering.
- `getTree` — returns a `ProcessTreeNode`: a `Process` extended with `children: ProcessTreeNode[]` (recursive). `maxDepth` limits traversal depth.
- `getTreeLog` — returns merged log entries across the process subtree, each augmented with `processId` (ObjectId) and `processLabel` (string) to identify the source process.
- `launch` / `cancel` / `dismiss` — no request body. 409 indicates a state conflict (e.g., launching an already-running process).
- `resync` — body: `{ clean?: boolean }`. Triggers re-sync of process definitions from the server-side registry.
