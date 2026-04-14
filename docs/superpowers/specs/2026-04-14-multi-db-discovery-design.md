# Multi-Database Instance Discovery

## Problem

The dashboard's prefix auto-discovery only works within a single MongoDB database. Users who run multiple optio instances across different databases on the same server must manually configure the connection. The `prefix` path parameter in all API routes adds unnecessary coupling between URL structure and instance selection.

## Solution

Move `prefix` from a URL path parameter to an optional query parameter. Add an optional `database` query parameter alongside it. Extend the discovery mechanism to scan across all databases on a MongoDB server when configured with a `MongoClient` instead of a single `Db`.

## API Configuration

`registerOptioApi` accepts either:

- **Single-db mode:** `{ db, redis, authenticate }` — as today. The `database` query param is ignored; `prefix` defaults to `'optio'` when omitted.
- **Multi-db mode:** `{ mongoClient, redis, authenticate }` — pass `MongoClient` instead of `Db`. The `database` query param selects which database to use; `prefix` defaults to `'optio'` when omitted.

TypeScript enforces mutual exclusivity via discriminated union:

```typescript
type OptioApiOptions = {
  redis: Redis;
  authenticate: AuthCallback;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);
```

## Route Structure

All routes drop the `:prefix` path segment. `database` and `prefix` become optional query parameters on every route.

Before:
- `GET /api/processes/:prefix`
- `GET /api/processes/:prefix/:id`
- `POST /api/processes/:prefix/:id/launch`
- `GET /api/processes/:prefix/stream`
- `GET /api/processes/:prefix/:id/tree/stream`

After:
- `GET /api/processes?database=mydb&prefix=myapp`
- `GET /api/processes/:id?database=mydb&prefix=myapp`
- `POST /api/processes/:id/launch?database=mydb&prefix=myapp`
- `GET /api/processes/stream?database=mydb&prefix=myapp`
- `GET /api/processes/:id/tree/stream?database=mydb&prefix=myapp`

When omitted, `prefix` defaults to `'optio'`. In single-db mode, `database` is ignored. In multi-db mode, `database` is required (return error if missing).

## Db/Prefix Resolution

A shared helper function resolves `(db, prefix)` from the query parameters and the adapter options. This helper is used by all adapters and handlers to avoid code duplication:

- In single-db mode: returns the provided `db` and the `prefix` query param (or `'optio'`).
- In multi-db mode: calls `mongoClient.db(database)` to get a `Db` handle (cheap, reuses connection pool) and returns it with the `prefix` query param (or `'optio'`).

Handlers continue to receive `(db, prefix, ...)` — they are unaware of multi-db.

## Discovery Endpoint

`GET /api/optio/instances`

Response:
```json
{
  "instances": [
    { "database": "mydb", "prefix": "optio" },
    { "database": "mydb", "prefix": "myapp" },
    { "database": "otherdb", "prefix": "optio" }
  ]
}
```

Empty: `{ "instances": [] }`

- **Single-db mode:** scans the one database, wraps results with the database name obtained from `db.databaseName`.
- **Multi-db mode:** calls `adminDb.listDatabases()`, iterates each database, runs the existing collection scan + schema validation on each, aggregates results.

Both modes return the same response shape.

## Contract Changes (optio-contracts)

- Remove `:prefix` from all path params in `processesContract`.
- Add `database` and `prefix` as optional query params on every route.
- Replace `discoveryContract` endpoint: path changes from `/optio/prefixes` to `/optio/instances`, response schema changes to `{ instances: z.array(z.object({ database: z.string(), prefix: z.string() })) }`.

## UI Changes (optio-ui)

- **`usePrefixes()`** renamed to **`useInstances()`** — calls `GET /api/optio/instances`, returns `{ instances, isLoading, error }`.
- **`usePrefixDiscovery()`** renamed to **`useInstanceDiscovery()`** — if exactly one instance, returns it; otherwise returns `null`. Exposes full list.
- **`OptioProvider`** — accepts optional `prefix?: string` and `database?: string` props. Falls back to auto-discovery (single instance), then defaults (`'optio'` for prefix). Context value includes both `database` and `prefix`.
- All query hooks pass `database` and `prefix` as query params instead of path params.
- SSE stream URLs pass `database` and `prefix` as query params.

## Dashboard Changes (optio-dashboard)

- Server passes `mongoClient` (not `db`) to `registerOptioApi` for full multi-db discovery.
- `AppContent` selects an instance (`{ database, prefix }`) instead of a prefix string.
- One instance: auto-select.
- Multiple instances: dropdown formatted as `"database/prefix"`.
- Zero instances: "no optio instance detected" message.
- Selected instance's `database` and `prefix` passed to `OptioProvider`.

## Testing

- **Discovery endpoint (Fastify):** integration tests for both single-db and multi-db modes. Verify `{ instances }` response shape. Verify cross-database discovery in multi-db mode.
- **UI hooks:** unit tests for `useInstances()` and `useInstanceDiscovery()` with mocked responses (zero, one, multiple instances).
- **OptioProvider:** test priority chain with `database` and `prefix` props.
