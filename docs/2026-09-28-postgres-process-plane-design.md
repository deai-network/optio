# Postgres as an alternate process-plane backend

## Summary

A deployment picks one backend at startup and runs the process plane on it. Mongo stays a first-class backend. With no new settings, a machine that has the local Mongo container behaves as it does today.

This slice covers process rows, launch blocks, the API reads of those, config parsing, discovery across both servers, and schema migrations. GridFS, session snapshots, seeds, and the seven agent engines stay on the Mongo handle. Copying existing Mongo data into Postgres is out of scope. The dashboard's admin login stays on Mongo.

## Outcome

- One `Optio` process uses one backend for its whole lifetime.
- The engine writes process rows and launch blocks. The API reads them. Mutations still go from the API to the engine over Redis RPC.
- An embedded app can pass an already-open handle. The demo and the dashboard can pass a URL, parsed by code that lives in `optio-core` (Python) and `optio-api` (TypeScript).
- A Postgres deployment can launch, cancel, dismiss, resync, report progress, write logs, build trees, and serve the API for tasks that do not store blobs.
- The JSON the UI and the contracts already use stays the same, including 24-hex ids.

## Architecture

`optio-core` grows a `ProcessStore`. Lifecycle, the executor, process context, and force-cancel call it instead of calling Motor directly.

- `MongoProcessStore` is today's `store.py` and launch-block store behind that interface.
- `PostgresProcessStore` is the new implementation: one row per process, lookup columns plus a `jsonb` document.

On a Postgres engine, blob methods and the existing `mongo_store` handle raise `RuntimeError`. The message says those are Mongo-only until the blob work. Agent packages are not rewritten in this slice, so a Postgres engine fails at the first blob or snapshot call instead of writing somewhere accidental.

`optio-api` grows a `ProcessReader`. REST handlers, the SSE poller, widget-upstream lookup, and discovery call it.

- The Mongo reader is today's queries.
- The Postgres reader returns the same JSON.

The two languages do not share code. The process JSON is the contract between the writer and the reader.

`pip install optio-core` does not install a Postgres driver. Postgres support is the extra `optio-core[postgres]`, which depends on `pg-quaestor` (and, through it, `asyncpg`). `optio-api` depends on `pg`.

## Rows and names

One row per process in `{prefix}_processes`. `doc` is the whole process document, with the same field names Mongo uses. Ids inside `doc` are 24-hex strings. Datetimes inside `doc` are ISO-8601 strings. The API already sends both that way on the wire.

The other columns are copies, written in the same statement as `doc`, and exist for lookup and indexes.

| Column | Type | Role |
|---|---|---|
| `id` | `text` primary key | 24-hex. Generated with `ObjectId`, so sort order stays chronological. |
| `process_id` | `text` not null | Indexed. May repeat. A lookup by `processId` returns the greatest `id`. |
| `parent_id` | `text` | Null for a root. |
| `root_id` | `text` | Tree root. |
| `depth` | `integer` not null | Tree depth. |
| `sort_order` | `integer` not null | Sibling order. The SQL name avoids the reserved word `order`. |
| `state` | `text` not null | Copy of `status.state`. |
| `expire_at` | `timestamptz` | Copy of `expireAt`. Null when the row does not expire. |
| `doc` | `jsonb` not null | The full document. |

Indexes:

- primary key on `id`
- `process_id`
- `parent_id`
- `(root_id, depth, sort_order, id)`
- `state`
- `expire_at`
- GIN on `(doc -> 'metadata')`

`{prefix}_launch_blocks`:

| Column | Type | Role |
|---|---|---|
| `id` | `text` primary key | 24-hex. Internal. Not part of the wire API. |
| `filter` | `jsonb` not null | The launch filter. |
| `created_at` | `timestamptz` not null | |
| `reason` | `text` | Null when no reason was stored. |

Dedupe matches today's Mongo behavior: find a row whose `filter` is equal, otherwise insert. `jsonb` equality ignores key order, which matches Mongo's subdocument match. There is no unique constraint on `filter`.

Postgres has no TTL index. The engine deletes rows with `expire_at <= now()` every 60 seconds, the same cadence as Mongo's TTL monitor. The sweep is a function the engine calls. Tests call that function.

A Postgres `OPTIO_PREFIX` must be a bare identifier of at most 52 characters (`^[A-Za-z_][A-Za-z0-9_]*$`). That is `pg-quaestor`'s rule, because the prefix becomes the migrations table name. Mongo keeps accepting the prefixes it accepts today.

### Redis and RPC names

Mongo keeps `{database}/{prefix}` for the RPC prefix and the heartbeat key. A Postgres engine uses `postgres/{database}/{prefix}` for both. An omitted `engine` means Mongo, so current clients keep talking to Mongo engines.

## Migrations

Postgres schema changes go through `pg-quaestor` 0.1.0. A registry of named migrations, each with `depends_on`, runs at startup. `pg-quaestor` takes one `asyncpg` connection and a prefix. It records applied names in `{prefix}_migrations`, holds an advisory lock while it runs, and runs each migration in its own transaction.

Two registries, two directories:

- Mongo stays `optio_core/migrations/`, registry `fw_migrations`. Startup still calls `fw_migrations.run(mongo_db, prefix=f"{prefix}_fw")`.
- Postgres is a new `optio_core/pg_migrations/`. `pg-quaestor` gives each migration function only the connection, so `build_pg_migrations(prefix)` builds the registry at init and the functions close over that prefix. Startup acquires a connection that is not already in a transaction and calls `registry.run(conn, prefix)`.

Startup runs only the registry for the configured engine. A Mongo engine does not open Postgres to migrate it. A Postgres engine does not open Mongo to migrate it.

The first Postgres migration is named `create_process_tables`. It creates `{prefix}_processes`, `{prefix}_launch_blocks`, and the indexes above. There is no existing Postgres data, so this migration is the schema. Mongo's `m001` through `m004` stay as they are. A later structural change adds the next file on each chain. It does not replay the old Mongo migrations onto Postgres.

## Config

An engine is given exactly one of these:

- the existing `mongo_db` handle, plus `prefix`
- a new `postgres_pool` handle, plus `prefix`
- a `StorageConfig`

The handle forms leave the connection owned by the caller. The config form makes `optio-core` open the connection and close it on shutdown. Passing more than one is a `ValueError`.

`StorageConfig` is `engine` (`"mongo"` or `"postgres"`), `url`, `database`, and `prefix`. `storage_config_from_env` returns that.

`storage_config_from_env` in `optio-core` reads:

| Variable | Meaning |
|---|---|
| `OPTIO_ENGINE` | `mongo` or `postgres`. Default `mongo`. |
| `OPTIO_DATABASE_URL` | Connection URL. First choice. |
| `MONGODB_URL` | Used when the engine is Mongo and `OPTIO_DATABASE_URL` is unset. |
| `OPTIO_DATABASE` | Database name. Overrides the URL path. |
| `OPTIO_PREFIX` | Table and collection namespace. Default `optio`. |

If neither URL variable is set, the caller supplies the default. The library defaults are `mongodb://localhost:27017/optio` and `postgresql://localhost:5432/optio`. The demo passes `mongodb://localhost:27017/optio-demo` and `postgresql://localhost:5432/optio-demo`, so an unset demo environment still opens today's database.

The database name is `OPTIO_DATABASE`, otherwise the URL path. A missing path is a startup error.

The dashboard parser lives in `optio-api` and is imported by the server started with `make run-api`. The dashboard watches many instances, so its config is two server URLs:

| Variable | Default | Meaning |
|---|---|---|
| `MONGODB_URL` | `mongodb://localhost:27017/optio` | Mongo server to scan. The URL path is the database where the dashboard stores its admin user. |
| `OPTIO_POSTGRES_URL` | `postgresql://localhost:5432/postgres` | Postgres server to scan. An empty value means do not scan Postgres. |
| `REDIS_URL` | `redis://localhost:6379` | Unchanged. |

Discovery lists every database on each server that the connection can read, and every `{prefix}_processes` table or collection that has the process fields (`processId`, `rootId`, `depth`). A Postgres connection failure logs a warning and the dashboard still starts on Mongo. A Mongo connection failure still aborts startup, because the admin login is stored there.

The discovery payload gains `engine: "mongo" | "postgres"`. The instance picker label is `{engine} {database}/{prefix}`.

## Request path

Reads take an `engine` query parameter, default `mongo`, beside the existing `database` and `prefix`. The API selects the reader from that. The Postgres reader returns `doc`. `widgetUpstream` is removed before the response, as it is today. The SSE poller uses the same reader, and the stream URL carries `engine` the same way.

`OptioProvider` gains an `engine` prop. The client sends it on reads, writes, and streams.

Mutations stay on RPC. The transport cache key is the Redis prefix from above.

Mongo widget URLs stay `/api/widget/{database}/{prefix}/{id}/`. A Postgres widget uses that path plus `?engine=postgres`. The proxy reads `engine` and does not forward it to the upstream. The same rule applies to the widget-control URL.

Metadata filters stay the exact-match map they are today. On Postgres that is a `jsonb` containment check on `doc -> 'metadata'`. `filtrum-mongo` is unchanged.

## Errors

- `OPTIO_ENGINE` is neither `mongo` nor `postgres`: startup error naming the variable.
- A Postgres prefix is not a bare identifier of at most 52 characters: startup error naming `OPTIO_PREFIX` and the rule.
- No database in the URL and no `OPTIO_DATABASE`: startup error.
- More than one of `mongo_db`, `postgres_pool`, and `storage`: `ValueError`.
- Postgres is selected and the extra is not installed: `ImportError` naming `pip install optio-core[postgres]`.
- The engine cannot connect: startup fails. The message includes the host and the driver error, and leaves out any password.
- Blob methods, or `mongo_store`, on a Postgres engine: `RuntimeError` saying those are Mongo-only until the blob work.
- `engine=postgres` on an API whose host has no Postgres pool: `503`. The body says Postgres is not configured.
- Any other `engine` value: `400`.
- A missing process: `404`, as today.
- A driver failure on a read: `500` with a generic message. The server log has the details. The connection string is not in the response.

## Tests

The existing Mongo suite stays as it is and must still pass.

New Postgres tests run against a real Postgres server.

- Store operations: upsert, status update, log append, child create, list by state and by metadata, stale removal, launch-block dedupe, and the expire sweep. The sweep test calls the delete function. It does not sleep.
- One cross-language test writes a process with the Python store and reads it with the TypeScript reader. The JSON matches what the Mongo reader returns for the same logical process, including string ids and the absence of `widgetUpstream`.
- Config: an unset demo environment resolves to Mongo and `mongodb://localhost:27017/optio-demo`. Postgres without the extra raises the install error. A bad prefix fails in the parser.
- Discovery: a Mongo instance and a Postgres instance both appear. A down Postgres server still returns the Mongo instances.
- API: omitting `engine` reads Mongo. `engine=postgres` with no pool returns `503`. `engine=postgres` reads the row the Python store wrote. The widget proxy does not forward `engine` upstream.

## AGENTS.md rule

When a change affects how process rows or launch blocks are stored, that same change updates both backends:

- In `optio-core`, both `MongoProcessStore` and `PostgresProcessStore`.
- In `optio-api`, both the Mongo reader and the Postgres reader.
- When the change alters the schema (a new stored field that has to be backfilled, an index, a new table, or a changed column), it also adds a migration on both chains: the next `mongo-quaestor` migration under `optio_core/migrations/`, and the next `pg-quaestor` migration under `optio_core/pg_migrations/`.

`init()` runs only the chain for the engine it was configured with.

The root `AGENTS.md` gains this rule in the same change as the implementation. The package `AGENTS.md` files for `optio-core` and `optio-api` gain the corresponding public-API notes (the store, the reader, the `engine` query, the env vars, and the Postgres extra).

## Out of scope

- GridFS, session snapshots, seeds, uploads, and the seven agent engines' direct Mongo access. On a Postgres engine those calls raise.
- A tool that copies existing Mongo process data into Postgres.
- Moving the dashboard's better-auth admin user off Mongo. better-auth can take a `pg` Pool in place of `mongodbAdapter`; that is a later reconfiguration, not this spec.
- `filtrum-mongo`, and any change to the process JSON or the 24-hex id format.
