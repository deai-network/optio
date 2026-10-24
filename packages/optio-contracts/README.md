# optio-contracts

Zod schemas and ts-rest contract for the Optio process management API.

> **Note:** This package is an implementation detail — it defines the API contract used for communication between `optio-ui` and `optio-api`. You only need to interact with this package directly if you are building an alternative frontend or a custom API adapter.

## Exported Schemas

| Schema | Description |
|--------|-------------|
| `ObjectIdSchema` | 24-character hex MongoDB ObjectId string |
| `PaginationQuerySchema` | Cursor-based pagination query params (`cursor`, `limit`) |
| `PaginatedResponseSchema` | Generic paginated response wrapper (`items`, `nextCursor`, `totalCount`) |
| `ErrorSchema` | Standard error response with a `message` string |
| `DateSchema` | Coerced date value |
| `ProcessStateSchema` | Enum of all valid process lifecycle states |
| `ProcessSchema` | Full process document including status, progress, and log |
| `LogEntrySchema` | Single structured log entry with level, message, and optional data |

## Exported Types

| Type | Description |
|------|-------------|
| `Process` | TypeScript type inferred from `ProcessSchema` |
| `ProcessState` | Union of state strings: `idle`, `scheduled`, `running`, `done`, `failed`, `cancel_requested`, `cancelling`, `cancelled` |
| `LogEntry` | TypeScript type inferred from `LogEntrySchema` |

## Contracts

The package hosts two contract files. Contract files follow `<server>-to-<client>.ts` naming: the server side is the one that exposes the contract; the client side calls it.

- `processesContract` (in `src/api-to-frontend.ts`) — ts-rest HTTP contract that `optio-api` exposes to its REST clients. The same file exports `discoveryContract` (`GET /optio/instances`).
- `optioEngineContract` (in `src/optio-engine-to-api.ts`) — clamator RPC contract that `optio-core` exposes to its RPC callers. Wire service name: `'optio-engine'`.

### `processesContract` (HTTP, ts-rest)

ts-rest router with 12 endpoints, used by `optio-ui` to call `optio-api`. Every endpoint takes the optional query parameters `?database=&prefix=` that select the optio instance; `:id` is either the process `_id` or its `processId`:

| Name | Method | Path |
|------|--------|------|
| `list` | GET | `/processes` |
| `get` | GET | `/processes/:id` |
| `getResult` | GET | `/processes/:id/result` |
| `getTree` | GET | `/processes/:id/tree` |
| `getLog` | GET | `/processes/:id/log` |
| `getTreeLog` | GET | `/processes/:id/tree/log` |
| `getConversation` | GET | `/processes/:id/conversation` |
| `launch` | POST | `/processes/:id/launch` |
| `cancel` | POST | `/processes/:id/cancel` |
| `dismiss` | POST | `/processes/:id/dismiss` |
| `resurrect` | POST | `/processes/:id/resurrect` |
| `resync` | POST | `/processes/resync` |

Used at runtime by ts-rest; no codegen step.

### `optioEngineContract` (RPC, clamator)

clamator service named `optio-engine`, used by `optio-api` to call `optio-core`. Methods:

| Method | Kind | Purpose |
|--------|------|---------|
| `launch` | request/reply | Launch a process; returns post-command process state or typed failure reason. |
| `cancel` | request/reply | Cancel a running or scheduled process; returns post-command state or typed failure reason. |
| `dismiss` | request/reply | Reset a terminal process to idle; returns post-command state or typed failure reason. |
| `resurrect` | request/reply | Save the work a failed run left on its host, then resume; returns post-command state or typed failure reason. |
| `groupCancel` | request/reply | Cancel all processes matching a metadata filter; returns count. |
| `groupCancelAndWait` | request/reply | Cancel all matching processes and wait until they reach a terminal state. |
| `blockLaunches` | request/reply | Add a persistent launch block. |
| `unblockLaunches` | request/reply | Remove a persistent launch block; returns count removed. |
| `materializeUpload` | request/reply | Land a staged upload (a GridFS blob) in a running process's workdir; returns its path. |
| `resync` | notification | Re-sync task definitions. Fire-and-forget; no reply. |

Failure modes use discriminated-union result types (e.g. `{ ok: true, process } | { ok: false, reason: 'not-found' | 'not-launchable' | … }`) so consumers get exhaustive type coverage on success and failure branches.

Generated wrappers ship next to consumers: `packages/optio-api/src/_generated/optio-engine.ts` for TypeScript, `packages/optio-core/src/optio_core/_generated/optio_engine.py` for Python. Regenerate via `make codegen` at the repo root.

## See Also

- [Optio Overview](../../README.md)
