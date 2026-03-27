# feldwebel-contracts

Zod schemas and ts-rest contract for the feldwebel process management API.

## Note

Internal shared dependency between feldwebel-api and feldwebel-ui. You usually
don't install this directly.

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

## Contract

`processesContract` — ts-rest router with 9 endpoints:

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

## See Also

- [Feldwebel Overview](../feldwebel/README.md)
