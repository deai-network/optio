# Postgres Process-Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deployment can run optio's process plane on Postgres or Mongo, chosen at startup, with Mongo behavior unchanged when Postgres is not selected.

**Architecture:** `optio-core` writes through a `ProcessStore`. The Mongo implementation delegates to today's `store.py`. The Postgres implementation stores one `jsonb` document per process plus lookup columns, and creates those tables with `pg-quaestor`. `optio-api` reads through a `ProcessReader` selected by an `engine` query parameter that defaults to `mongo`. Discovery scans one Mongo server and one Postgres server.

**Tech Stack:** Python 3.11, `asyncpg`, `pg-quaestor` 0.1.0, existing `motor` / `mongo-quaestor`, TypeScript, `pg`, existing `mongodb` driver, Redis RPC unchanged.

**Spec:** `docs/2026-09-28-postgres-process-plane-design.md`

## Global Constraints

- One `Optio` process uses one backend for its whole lifetime.
- The engine writes. The API reads. Mutations stay on Redis RPC.
- `pip install optio-core` does not install a Postgres driver. The extra is `optio-core[postgres]`, depending on `pg-quaestor` (and through it `asyncpg`). `optio-api` depends on `pg`.
- Mongo Redis and RPC prefix stays `{database}/{prefix}`. Postgres uses `postgres/{database}/{prefix}`.
- An omitted `engine` means `mongo`.
- Process JSON and 24-hex ids stay. Ids inside the stored `doc` are 24-hex strings. Datetimes inside `doc` are ISO-8601 strings.
- A Postgres `OPTIO_PREFIX` is a bare identifier of at most 52 characters. Mongo prefixes are unchanged.
- Startup runs only the migration registry for the configured engine.
- Blob methods and `mongo_store` on a Postgres engine raise `RuntimeError` saying they are Mongo-only until the blob work.
- No test may depend on wall-clock time. The expire sweep test calls the delete function.
- Dashboard admin login stays on Mongo. A Mongo connection failure still aborts dashboard startup. A Postgres connection failure logs a warning and Mongo discovery still returns.
- Do not add `Co-Authored-By` lines to commits.
- When a change alters process-row or launch-block storage, update both stores and both readers. A schema change also adds the next migration on both chains.

## File structure

- `packages/optio-core/src/optio_core/storage_config.py` parses engine, URL, database, and prefix.
- `packages/optio-core/src/optio_core/process_store.py` is the `ProcessStore` protocol.
- `packages/optio-core/src/optio_core/mongo_process_store.py` delegates to `store.py` and `_launch_block_store.py`.
- `packages/optio-core/src/optio_core/pg_migrations.py` builds the `pg-quaestor` registry for one prefix.
- `packages/optio-core/src/optio_core/postgres_process_store.py` is the Postgres writer. Python callers still receive `ObjectId` for `_id`, `parentId`, and `rootId`. The stored `doc` keeps those as hex strings.
- `packages/optio-api/src/storage-config.ts` is the dashboard parser.
- `packages/optio-api/src/process-reader.ts` selects a reader.
- `packages/optio-api/src/postgres-reader.ts` returns `doc` and strips `widgetUpstream`.
- Existing Mongo modules stay the Mongo implementation. Call sites in lifecycle, executor, context, and force-cancel take a `ProcessStore`.

## Review Focus

- Two rows can share a `processId`. `get_process_by_process_id` returns the greatest `id`. Pinned in Task 5.
- A launch-block filter written with the same keys in a different order is one row, and two non-null reasons join with ` AND `. Pinned in Task 6.
- A failed Postgres URL that contains a password raises an error whose text does not contain the password. Pinned in Task 7.
- An API that has both Mongo and Postgres, called with no `engine` parameter, reads Mongo. Pinned in Task 10.
- The widget proxy does not forward `engine` to the upstream URL. Pinned in Task 12.

---

### Task 1: Python storage config

**Files:**
- Create: `packages/optio-core/src/optio_core/storage_config.py`
- Test: `packages/optio-core/tests/test_storage_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `StorageConfig(engine: Literal["mongo", "postgres"], url: str, database: str, prefix: str)` and `storage_config_from_env(*, default_mongo_url: str = "mongodb://localhost:27017/optio", default_postgres_url: str = "postgresql://localhost:5432/optio", default_prefix: str = "optio", environ: Mapping[str, str] | None = None) -> StorageConfig`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from optio_core.storage_config import storage_config_from_env

def test_defaults_are_local_mongo():
    cfg = storage_config_from_env(environ={})
    assert cfg.engine == "mongo"
    assert cfg.url == "mongodb://localhost:27017/optio"
    assert cfg.database == "optio"
    assert cfg.prefix == "optio"

def test_demo_default_url_is_honored():
    cfg = storage_config_from_env(
        environ={},
        default_mongo_url="mongodb://localhost:27017/optio-demo",
    )
    assert cfg.database == "optio-demo"

def test_mongodb_url_used_when_engine_is_mongo_and_database_url_unset():
    cfg = storage_config_from_env(environ={"MONGODB_URL": "mongodb://db.example:27017/app"})
    assert cfg.url == "mongodb://db.example:27017/app"
    assert cfg.database == "app"

def test_database_url_wins_over_mongodb_url():
    cfg = storage_config_from_env(environ={
        "OPTIO_DATABASE_URL": "mongodb://other:27017/chosen",
        "MONGODB_URL": "mongodb://db.example:27017/app",
    })
    assert cfg.database == "chosen"

def test_optio_database_overrides_url_path():
    cfg = storage_config_from_env(environ={
        "OPTIO_DATABASE_URL": "mongodb://localhost:27017/frompath",
        "OPTIO_DATABASE": "override",
    })
    assert cfg.database == "override"

def test_bad_engine_names_the_variable():
    with pytest.raises(ValueError, match="OPTIO_ENGINE"):
        storage_config_from_env(environ={"OPTIO_ENGINE": "sqlite"})

def test_missing_database_path():
    with pytest.raises(ValueError, match="OPTIO_DATABASE"):
        storage_config_from_env(environ={"OPTIO_DATABASE_URL": "mongodb://localhost:27017"})

def test_postgres_prefix_rejects_hyphen_and_long_names():
    with pytest.raises(ValueError, match="OPTIO_PREFIX"):
        storage_config_from_env(environ={"OPTIO_ENGINE": "postgres", "OPTIO_PREFIX": "optio-demo"})
    with pytest.raises(ValueError, match="OPTIO_PREFIX"):
        storage_config_from_env(environ={
            "OPTIO_ENGINE": "postgres",
            "OPTIO_PREFIX": "p" * 53,
            "OPTIO_DATABASE_URL": "postgresql://localhost:5432/optio",
        })

def test_mongo_prefix_may_contain_a_hyphen():
    cfg = storage_config_from_env(environ={"OPTIO_PREFIX": "optio-demo"})
    assert cfg.prefix == "optio-demo"
```

- [ ] **Step 2: Run the test**

Run: `pytest packages/optio-core/tests/test_storage_config.py -v`

Expected: FAIL, `optio_core.storage_config` does not exist.

- [ ] **Step 3: Implement the parser**

```python
@dataclass(frozen=True)
class StorageConfig:
    engine: Literal["mongo", "postgres"]
    url: str
    database: str
    prefix: str

_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def storage_config_from_env(*, default_mongo_url="mongodb://localhost:27017/optio", default_postgres_url="postgresql://localhost:5432/optio", default_prefix="optio", environ=None) -> StorageConfig:
    env = os.environ if environ is None else environ
    engine = env.get("OPTIO_ENGINE", "mongo")
    if engine not in ("mongo", "postgres"):
        raise ValueError(f"OPTIO_ENGINE must be 'mongo' or 'postgres', got {engine!r}")
    url = env.get("OPTIO_DATABASE_URL")
    if url is None and engine == "mongo":
        url = env.get("MONGODB_URL")
    if url is None:
        url = default_mongo_url if engine == "mongo" else default_postgres_url
    database = env.get("OPTIO_DATABASE") or _database_from_url(url)
    if not database:
        raise ValueError("OPTIO_DATABASE is required when the connection URL has no database path")
    prefix = env.get("OPTIO_PREFIX", default_prefix)
    if engine == "postgres" and (_PREFIX.fullmatch(prefix) is None or len(prefix) > 52):
        raise ValueError(
            "OPTIO_PREFIX must be a bare identifier of at most 52 characters "
            f"when OPTIO_ENGINE=postgres, got {prefix!r}"
        )
    return StorageConfig(engine=engine, url=url, database=database, prefix=prefix)
```

`_database_from_url` uses `urllib.parse.urlparse` and returns the path without the leading slash. A missing netloc-only URL returns `""`.

- [ ] **Step 4: Re-run the test**

Run: `pytest packages/optio-core/tests/test_storage_config.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/storage_config.py packages/optio-core/tests/test_storage_config.py
git commit -m "feat(optio-core): parse storage engine, URL, database, and prefix"
```

### Task 2: ProcessStore and the Mongo delegate

**Files:**
- Create: `packages/optio-core/src/optio_core/process_store.py`
- Create: `packages/optio-core/src/optio_core/mongo_process_store.py`
- Test: `packages/optio-core/tests/test_mongo_process_store.py`
- Modify: none of the call sites yet

**Interfaces:**
- Consumes: every public function in `store.py` and `_launch_block_store.py`
- Produces: `MongoProcessStore(db, prefix)`. Methods drop the `db` and `prefix` arguments and otherwise match those functions. Returned documents are unchanged Mongo documents (`_id` is an `ObjectId`).

Methods on the protocol: `upsert_process`, `remove_stale_processes`, `find_stale_process_ids`, `get_process_by_id`, `get_process_by_process_id`, `update_status`, `set_auto_resume_scheduled`, `update_progress`, `append_log`, `create_child_process`, `delete_descendants`, `delete_process`, `purge_processes`, `clear_result_fields`, `get_children`, `list_direct_children`, `cancel_children`, `list_processes`, `update_widget_upstream`, `clear_widget_upstream`, `update_control_upstream`, `clear_control_upstream`, `update_widget_data`, `clear_widget_data`, `append_browser_open_request`, `append_session_event`, `load_launch_blocks`, `upsert_launch_block`, `delete_launch_blocks`.

- [ ] **Step 1: Write the failing test**

```python
from optio_core.models import TaskInstance
from optio_core.mongo_process_store import MongoProcessStore
from optio_core.store import get_process_by_process_id

async def dummy(ctx):
    pass

async def test_delegate_upsert_is_visible_to_the_module_function(mongo_db):
    store = MongoProcessStore(mongo_db, "test")
    task = TaskInstance(execute=dummy, process_id="p", name="P")
    saved = await store.upsert_process(task)
    again = await get_process_by_process_id(mongo_db, "test", "p")
    assert again["_id"] == saved["_id"]
    assert again["status"]["state"] == "idle"
```

- [ ] **Step 2: Run the test**

Run: `pytest packages/optio-core/tests/test_mongo_process_store.py -v`

Expected: FAIL, module does not exist.

- [ ] **Step 3: Implement the delegate**

Each method is a one-line forward. Example:

```python
async def upsert_process(self, task):
    return await store_mod.upsert_process(self._db, self._prefix, task)

async def load_launch_blocks(self):
    return await launch_blocks.load_all(launch_blocks.collection(self._db, self._prefix))
```

Write one forwarder for every method named in Interfaces. Do not change `store.py`.

- [ ] **Step 4: Re-run**

Run: `pytest packages/optio-core/tests/test_mongo_process_store.py packages/optio-core/tests/test_store.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/process_store.py packages/optio-core/src/optio_core/mongo_process_store.py packages/optio-core/tests/test_mongo_process_store.py
git commit -m "feat(optio-core): add ProcessStore and a Mongo delegate"
```

### Task 3: Engine call sites take the store

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/executor.py`
- Modify: `packages/optio-core/src/optio_core/context.py`
- Modify: `packages/optio-core/src/optio_core/_force_cancel.py`
- Test: existing `packages/optio-core/tests/`

**Interfaces:**
- Consumes: `MongoProcessStore`
- Produces: `Optio.init` still accepts `mongo_db` and `prefix`. After init, engine internals call `self._store` (a `MongoProcessStore`) instead of passing `db` and `prefix` into `store.py`. `optio.mongo_store` still returns the existing `(db, prefix)` binding. Blob methods still use GridFS.

- [ ] **Step 1: Run the current store-touching suite so the baseline is green**

Run: `pytest packages/optio-core/tests/test_store.py packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_widget_primitives.py packages/optio-core/tests/test_persistent_launch_blocks.py -v`

Expected: PASS before the edit.

- [ ] **Step 2: Replace direct store calls**

In `Optio.init`, after the Mongo database is known:

```python
self._store = MongoProcessStore(mongo_db, prefix)
```

In executor, context, and force-cancel, replace `await some_store_fn(self._db, self._prefix, ...)` with `await self._store.some_store_fn(...)`. Keep GridFS code on `self._db`. One example from context:

```python
# before
await update_widget_data(self._db, self._prefix, self._process_oid, data)
# after
await self._store.update_widget_data(self._process_oid, data)
```

Do this for every process-document and launch-block call in the four files. Leave `store_blob`, `load_blob`, and `delete_blob` on GridFS.

- [ ] **Step 3: Re-run the same suite plus the lifecycle tests**

Run: `pytest packages/optio-core/tests/test_store.py packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_widget_primitives.py packages/optio-core/tests/test_persistent_launch_blocks.py packages/optio-core/tests/test_heartbeat.py packages/optio-core/tests/test_no_redis.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/src/optio_core/executor.py packages/optio-core/src/optio_core/context.py packages/optio-core/src/optio_core/_force_cancel.py
git commit -m "refactor(optio-core): route process writes through ProcessStore"
```

### Task 4: Postgres migrations create the tables

**Files:**
- Modify: `packages/optio-core/pyproject.toml` (`postgres = ["pg-quaestor>=0.1,<0.2"]`)
- Create: `packages/optio-core/src/optio_core/pg_migrations.py`
- Test: `packages/optio-core/tests/test_pg_migrations.py`

**Interfaces:**
- Consumes: `pg_quaestor.MigrationRegistry`
- Produces: `async def apply_pg_migrations(conn, prefix: str) -> list[str]`. It calls `build_pg_migrations(prefix).run(conn, prefix)`. The first migration is named `create_process_tables`.

- [ ] **Step 1: Write the failing test**

```python
import asyncpg
import pytest
from optio_core.pg_migrations import apply_pg_migrations

pytestmark = pytest.mark.asyncio

async def test_create_process_tables_is_idempotent(pg_conn, prefix):
    first = await apply_pg_migrations(pg_conn, prefix)
    second = await apply_pg_migrations(pg_conn, prefix)
    assert first == ["create_process_tables"]
    assert second == []
    tables = await pg_conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    names = {row["tablename"] for row in tables}
    assert f"{prefix}_processes" in names
    assert f"{prefix}_launch_blocks" in names
    assert f"{prefix}_migrations" in names
```

The `pg_conn` fixture connects to `POSTGRES_URL` (default `postgresql://localhost:5432/postgres`), creates database `optio_pg_{pid}`, yields a connection to it, then drops the database. `prefix` is `"t"`.

- [ ] **Step 2: Run the test**

Run: `pytest packages/optio-core/tests/test_pg_migrations.py -v`

Expected: FAIL, import error.

- [ ] **Step 3: Implement the migration**

`build_pg_migrations(prefix)` registers `create_process_tables`. The function closes over `prefix` and executes `CREATE TABLE` for `{prefix}_processes` and `{prefix}_launch_blocks` with the columns and indexes in the spec, plus the GIN index on `(doc -> 'metadata')`. Quote the prefix with `asyncpg` identifier quoting after the same 52-character check. `apply_pg_migrations` refuses a connection that `is_in_transaction()`.

- [ ] **Step 4: Re-run**

Run: `pytest packages/optio-core/tests/test_pg_migrations.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/pyproject.toml packages/optio-core/src/optio_core/pg_migrations.py packages/optio-core/tests/test_pg_migrations.py
git commit -m "feat(optio-core): create Postgres process tables with pg-quaestor"
```

### Task 5: Postgres process rows

**Files:**
- Create: `packages/optio-core/src/optio_core/postgres_process_store.py`
- Test: `packages/optio-core/tests/test_postgres_store.py`

**Interfaces:**
- Consumes: `apply_pg_migrations`, the `ProcessStore` method names from Task 2
- Produces: `PostgresProcessStore(pool, prefix)`. Document methods return dicts whose `_id`, `parentId`, and `rootId` are `ObjectId`. The stored `doc` holds those three as hex strings. `get_process_by_process_id` returns the row with the greatest `id`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_upsert_and_status_round_trip(pg_store):
    task = TaskInstance(execute=dummy, process_id="p", name="P", metadata={"k": "v"})
    saved = await pg_store.upsert_process(task)
    assert isinstance(saved["_id"], ObjectId)
    await pg_store.update_status(saved["_id"], ProcessStatus(state="running"))
    again = await pg_store.get_process_by_process_id("p")
    assert again["status"]["state"] == "running"
    assert again["name"] == "P"

async def test_duplicate_process_id_returns_greatest_id(pg_store, pg_conn, prefix):
    first = await pg_store.upsert_process(TaskInstance(execute=dummy, process_id="dup", name="old"))
    later = ObjectId()
    assert later > first["_id"]
    await pg_conn.execute(
        f'INSERT INTO "{prefix}_processes" (id, process_id, root_id, depth, sort_order, state, doc) '
        "VALUES ($1, 'dup', $1, 0, 0, 'idle', $2::jsonb)",
        str(later),
        json.dumps({"_id": str(later), "rootId": str(later), "processId": "dup", "name": "new",
                    "status": {"state": "idle"}, "progress": {"percent": 0}, "log": []}),
    )
    found = await pg_store.get_process_by_process_id("dup")
    assert found["_id"] == later
    assert found["name"] == "new"

async def test_metadata_number_does_not_match_string(pg_store):
    await pg_store.upsert_process(TaskInstance(execute=dummy, process_id="n", name="N", metadata={"n": 1}))
    assert await pg_store.list_processes(metadata={"n": "1"}) == []
    assert len(await pg_store.list_processes(metadata={"n": 1})) == 1

async def test_append_log_keeps_previous_entries(pg_store):
    saved = await pg_store.upsert_process(TaskInstance(execute=dummy, process_id="l", name="L"))
    await pg_store.append_log(saved["_id"], "info", "one")
    await pg_store.append_log(saved["_id"], "warning", "two")
    doc = await pg_store.get_process_by_id(saved["_id"])
    assert [e["message"] for e in doc["log"]] == ["one", "two"]
```

- [ ] **Step 2: Run the test**

Run: `pytest packages/optio-core/tests/test_postgres_store.py -v`

Expected: FAIL, class does not exist.

- [ ] **Step 3: Implement document writes**

On every write, update the lookup columns and `doc` in one statement. `doc` is the full document with string ids and ISO datetimes. On read, parse `_id`, `parentId`, and `rootId` back into `ObjectId` before returning.

`get_process_by_process_id` uses `ORDER BY id DESC LIMIT 1`. `list_processes` filters `state`, `root_id`, and `doc->'metadata' @> $jsonb`, and orders by `depth, sort_order, id`.

Implement the rest of the document methods from Task 2 the same way: one SQL statement updates both the column and `doc`. `remove_stale_processes` deletes roots (`parent_id IS NULL`) whose `process_id` is outside the valid set, optionally matching metadata.

- [ ] **Step 4: Re-run**

Run: `pytest packages/optio-core/tests/test_postgres_store.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/postgres_process_store.py packages/optio-core/tests/test_postgres_store.py
git commit -m "feat(optio-core): store process documents in Postgres"
```

### Task 6: Launch blocks and expiry

**Files:**
- Modify: `packages/optio-core/src/optio_core/postgres_process_store.py`
- Test: `packages/optio-core/tests/test_postgres_store.py`

**Interfaces:**
- Consumes: `PostgresProcessStore`
- Produces: `upsert_launch_block`, `load_launch_blocks`, `delete_launch_blocks`, and `delete_expired(now)`. `delete_expired` deletes rows with `expire_at <= now`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_launch_block_dedupes_on_key_order(pg_store):
    await pg_store.upsert_launch_block({"b": 1, "a": "x"}, "first")
    await pg_store.upsert_launch_block({"a": "x", "b": 1}, "second")
    rows = await pg_store.load_launch_blocks()
    assert len(rows) == 1
    assert rows[0].reason == "first AND second"

async def test_delete_expired_removes_only_due_rows(pg_store):
    saved = await pg_store.upsert_process(TaskInstance(execute=dummy, process_id="e", name="E"))
    due = datetime(2020, 1, 1, tzinfo=timezone.utc)
    await pg_store.update_status(saved["_id"], ProcessStatus(state="done"), expire_at=due)
    removed = await pg_store.delete_expired(datetime(2020, 1, 2, tzinfo=timezone.utc))
    assert removed == 1
    assert await pg_store.get_process_by_id(saved["_id"]) is None
```

- [ ] **Step 2: Run the new tests**

Run: `pytest packages/optio-core/tests/test_postgres_store.py -v -k "launch_block or delete_expired"`

Expected: FAIL

- [ ] **Step 3: Implement**

`upsert_launch_block` selects `WHERE filter = $1::jsonb`. On a hit with both reasons non-null, set `reason` to `f"{existing} AND {reason}"`. Otherwise leave the existing reason. On a miss, insert. `delete_expired` is `DELETE FROM {prefix}_processes WHERE expire_at IS NOT NULL AND expire_at <= $1`.

- [ ] **Step 4: Re-run the file**

Run: `pytest packages/optio-core/tests/test_postgres_store.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/postgres_process_store.py packages/optio-core/tests/test_postgres_store.py
git commit -m "feat(optio-core): Postgres launch blocks and row expiry"
```

### Task 7: Init selects the engine

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`
- Modify: `packages/optio-core/src/optio_core/context.py` (blob methods)
- Modify: `packages/optio-core/src/optio_core/__init__.py` if it re-exports `init`
- Test: `packages/optio-core/tests/test_postgres_init.py`

**Interfaces:**
- Consumes: `StorageConfig`, `MongoProcessStore`, `PostgresProcessStore`, `apply_pg_migrations`
- Produces: `Optio.init` accepts exactly one of `mongo_db`, `postgres_pool`, or `storage`. `storage` opens the connection and shutdown closes it. Postgres init runs `apply_pg_migrations` and does not run `fw_migrations`. Mongo init does the opposite. Postgres RPC prefix is `postgres/{database}/{prefix}`. `delete_expired` is called from the existing periodic loop every 60 seconds. `mongo_store` and blob methods raise on a Postgres engine.

- [ ] **Step 1: Write the failing tests**

```python
async def test_postgres_init_does_not_require_mongo(pg_pool):
    optio = Optio()
    await optio.init(postgres_pool=pg_pool, prefix="t", redis_url=None)
    assert optio._store.__class__.__name__ == "PostgresProcessStore"
    with pytest.raises(RuntimeError, match="Mongo-only"):
        _ = optio.mongo_store

async def test_both_handles_rejected():
    optio = Optio()
    with pytest.raises(ValueError, match="mongo_db"):
        await optio.init(mongo_db=object(), postgres_pool=object(), prefix="t")

def test_connection_error_redacts_password():
    err = _redact_connect_error(
        "postgresql://user:s3cret@db.example:5432/optio",
        OSError("password authentication failed"),
    )
    assert isinstance(err, ConnectionError)
    assert str(err) == "Postgres connection failed for db.example: OSError"
    assert "s3cret" not in str(err)
```

`_connect_postgres` calls `asyncpg.create_pool`. On any exception it raises `_redact_connect_error(url, err)`. That helper returns `ConnectionError(f"Postgres connection failed for {host}: {type(err).__name__}")`. The message has the host and the exception type. It does not contain the URL or the password, and `__cause__` is not set.

- [ ] **Step 2: Run the test**

Run: `pytest packages/optio-core/tests/test_postgres_init.py -v`

Expected: FAIL, `init` has no `postgres_pool`.

- [ ] **Step 3: Implement init selection**

Count how many of `mongo_db`, `postgres_pool`, and `storage` were passed. Zero or more than one raises `ValueError` naming the three forms. `storage.engine == "mongo"` opens Motor and continues down today's path, including `fw_migrations`. `postgres` creates or accepts a pool, runs only `apply_pg_migrations`, sets the RPC prefix to `postgres/{database}/{prefix}`, and starts a 60-second loop that calls `delete_expired`. Shutdown closes a pool that `init` opened from `storage`. It does not close a `postgres_pool` the caller passed in. Blob methods check `isinstance(self._store, PostgresProcessStore)` and raise `RuntimeError("blobs and mongo_store are Mongo-only until the blob work")`.

- [ ] **Step 4: Re-run init tests and a Mongo lifecycle test**

Run: `pytest packages/optio-core/tests/test_postgres_init.py packages/optio-core/tests/test_no_redis.py packages/optio-core/tests/test_heartbeat.py -v`

Expected: PASS. Mongo heartbeat key remains `{database}/{prefix}:heartbeat`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/lifecycle.py packages/optio-core/src/optio_core/context.py packages/optio-core/tests/test_postgres_init.py
git commit -m "feat(optio-core): select Mongo or Postgres at init"
```

### Task 8: TypeScript server endpoints parser

**Files:**
- Create: `packages/optio-api/src/storage-config.ts`
- Test: `packages/optio-api/src/__tests__/storage-config.test.ts`
- Modify: `packages/optio-api/package.json` (add dependency `pg`)
- Modify: `packages/optio-api/src/index.ts` to export the parser

**Interfaces:**
- Consumes: nothing from the Python parser. The env names match the spec.
- Produces: `readDashboardStorage(env: NodeJS.ProcessEnv): { mongoUrl: string; postgresUrl: string | null; redisUrl: string }`. Empty `OPTIO_POSTGRES_URL` yields `postgresUrl: null`.

- [ ] **Step 1: Write the failing test**

```typescript
import { readDashboardStorage } from '../storage-config.js';

test('defaults scan local mongo and local postgres', () => {
  const cfg = readDashboardStorage({} as NodeJS.ProcessEnv);
  expect(cfg.mongoUrl).toBe('mongodb://localhost:27017/optio');
  expect(cfg.postgresUrl).toBe('postgresql://localhost:5432/postgres');
  expect(cfg.redisUrl).toBe('redis://localhost:6379');
});

test('empty postgres url disables the scan', () => {
  const cfg = readDashboardStorage({ OPTIO_POSTGRES_URL: '' } as NodeJS.ProcessEnv);
  expect(cfg.postgresUrl).toBeNull();
});

test('MONGODB_URL is honored', () => {
  const cfg = readDashboardStorage({ MONGODB_URL: 'mongodb://db.example:27017/app' } as NodeJS.ProcessEnv);
  expect(cfg.mongoUrl).toBe('mongodb://db.example:27017/app');
});
```

- [ ] **Step 2: Run the test**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/storage-config.test.ts`

Expected: FAIL, module missing. The package test script is `vitest run`.

- [ ] **Step 3: Implement and export**

Default the three variables as the test states. Export `readDashboardStorage` from the package entry.

- [ ] **Step 4: Re-run**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-api/src/storage-config.ts packages/optio-api/src/__tests__/storage-config.test.ts packages/optio-api/src/index.ts packages/optio-api/package.json
git commit -m "feat(optio-api): parse dashboard Mongo and Postgres server URLs"
```

### Task 9: Engine on the contract and the RPC key

**Files:**
- Modify: `packages/optio-contracts/src/api-to-frontend.ts`
- Modify: `packages/optio-api/src/optio-transports.ts`
- Test: `packages/optio-contracts/src/__tests__/` existing contract tests, plus a new assertion
- Test: `packages/optio-api/src/__tests__/transports-engine.test.ts`

**Interfaces:**
- Consumes: `InstanceQuerySchema`, `createOptioTransports`
- Produces: `engine` on `InstanceQuerySchema` and on the list query, enum `mongo | postgres`, optional. `InstanceSchema` gains required `engine`. `OptioTransports.get(database, prefix, engine = "mongo")`. The Redis key is `${database}/${prefix}` when engine is `mongo`, and `postgres/${database}/${prefix}` otherwise.

- [ ] **Step 1: Write the failing transport test**

```typescript
test('mongo key is unchanged and postgres key is prefixed', () => {
  const keys: string[] = [];
  const redis = { duplicate() { return this; } } as any;
  // construct via the real helper and spy on RedisRpcClient's keyPrefix by
  // exporting a keyFor(engine, database, prefix) pure function and testing that.
  expect(rpcKey('mongo', 'optio', 'app')).toBe('optio/app');
  expect(rpcKey('postgres', 'optio', 'app')).toBe('postgres/optio/app');
});
```

Put `rpcKey` in `optio-transports.ts` and use it inside `get`.

- [ ] **Step 2: Run the test and the contract tests**

Expected: FAIL, `rpcKey` missing. Contract tests fail once `engine` is required on instances and the fixtures omit it. Update those fixtures to `engine: "mongo"` in this same task.

- [ ] **Step 3: Add the schema fields and `rpcKey`**

`engine: z.enum(["mongo", "postgres"]).optional()` on query schemas. `engine: z.enum(["mongo", "postgres"])` on `InstanceSchema`.

- [ ] **Step 4: Re-run contract tests and the new test**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-contracts packages/optio-api/src/optio-transports.ts packages/optio-api/src/__tests__/transports-engine.test.ts
git commit -m "feat(optio-contracts): add engine to instance identity and RPC keys"
```

### Task 10: Readers, and omitted engine stays on Mongo

**Files:**
- Create: `packages/optio-api/src/postgres-reader.ts`
- Create: `packages/optio-api/src/process-reader.ts`
- Modify: `packages/optio-api/src/handlers.ts`
- Modify: `packages/optio-api/src/stream-poller.ts`
- Modify: `packages/optio-api/src/resolve.ts`
- Test: `packages/optio-api/src/__tests__/postgres-reader.test.ts`

**Interfaces:**
- Consumes: `rpcKey`, a `pg.Pool`, the stored `doc` shape from Task 5
- Produces: `createPostgresReader(pool, prefix)` with `list`, `get`, `getTree`, `getLog`, `getTreeLog`. Each returns the same JSON the Mongo handlers return, including string ids and no `widgetUpstream`. `resolveReader(ctx, query)` uses Mongo when `query.engine` is omitted.

- [ ] **Step 1: Write the failing tests**

```typescript
test('omitted engine resolves to mongo even if a postgres pool exists', () => {
  const choice = selectEngine(undefined);
  expect(choice).toBe('mongo');
});

test('postgres reader returns doc and drops widgetUpstream', async () => {
  const pool = fakePool([{
    doc: {
      _id: 'a'.repeat(24),
      rootId: 'a'.repeat(24),
      processId: 'p',
      name: 'P',
      status: { state: 'idle' },
      progress: { percent: 0 },
      log: [],
      widgetUpstream: { url: 'http://secret' },
    },
  }]);
  const reader = createPostgresReader(pool, 't');
  const proc = await reader.get('p');
  expect(proc._id).toBe('a'.repeat(24));
  expect(proc.widgetUpstream).toBeUndefined();
});

test('engine postgres with no pool is 503', async () => {
  await expect(resolveReader({ postgresPool: undefined }, { engine: 'postgres' }))
    .rejects.toMatchObject({ statusCode: 503 });
});
```

- [ ] **Step 2: Run the test**

Expected: FAIL

- [ ] **Step 3: Implement**

`selectEngine` returns `query.engine ?? "mongo"`. `resolveReader` throws an error object `{ statusCode: 503, message: "Postgres is not configured" }` when the engine is postgres and no pool was passed to the API. The Postgres reader runs `SELECT doc FROM {prefix}_processes ...` and maps rows through the existing `stripServerSideFields`. `list` filters `state` with the `state` column, a cursor with `id > $cursor`, and `metadataFilter` with `doc->'metadata' @> $filter::jsonb`. A driver error caught at the adapter becomes HTTP 500 `{ message: "Internal server error" }`. The response body does not include the SQL text or the connection string. Handlers and the SSE poller call the reader instead of `db.collection` for process documents. Mongo queries stay in the Mongo branch of the reader, moved out of `handlers.ts` without changing their filters.

An unknown `engine` value is rejected by the zod schema with 400 before the handler runs. Add one contract test that `engine=sqlite` fails validation.

- [ ] **Step 4: Re-run API handler tests**

Run the package test script.

Expected: PASS, including existing Mongo handler tests.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-api/src/postgres-reader.ts packages/optio-api/src/process-reader.ts packages/optio-api/src/handlers.ts packages/optio-api/src/stream-poller.ts packages/optio-api/src/resolve.ts packages/optio-api/src/__tests__/postgres-reader.test.ts
git commit -m "feat(optio-api): read process documents from Postgres"
```

### Task 11: Discovery across both servers

**Files:**
- Modify: `packages/optio-api/src/discovery.ts`
- Modify: the fastify adapter's options type so a `pg.Pool | null` can be passed
- Test: `packages/optio-api/src/__tests__/discovery-engines.test.ts`

**Interfaces:**
- Consumes: `readDashboardStorage` is used by the dashboard in Task 12. This task consumes a Mongo client and an optional Postgres pool.
- Produces: `discoverInstances` returns `{ engine, database, prefix, live }`. Postgres instances use heartbeat key `postgres/${database}/${prefix}:heartbeat`. A Postgres query error is logged and does not drop the Mongo instances.

- [ ] **Step 1: Write the failing test**

```typescript
test('postgres failure still returns mongo instances', async () => {
  const mongo = fakeMongo([{ name: 'app_processes', doc: { processId: 'p', rootId: 'r', depth: 0 } }]);
  const postgres = { query: async () => { throw new Error('connection refused'); } };
  const found = await discoverInstances({ mongoClient: mongo, postgresPool: postgres }, redis);
  expect(found.map((i) => i.engine)).toEqual(['mongo']);
});

test('a postgres table ending in _processes is an instance', async () => {
  const postgres = fakePostgres([{ datname: 'app', tables: ['optio_processes'] }]);
  const found = await discoverInstances({ postgresPool: postgres }, undefined);
  expect(found).toEqual([
    expect.objectContaining({ engine: 'postgres', database: 'app', prefix: 'optio', live: false }),
  ]);
});
```

Confirm a candidate table by reading one row and checking `processId`, `rootId`, and `depth` are present in `doc`, matching the Mongo check.

- [ ] **Step 2: Run the test**

Expected: FAIL

- [ ] **Step 3: Implement the scan**

Connect to the maintenance database, `SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate`, then in each database look for tables ending in `_processes`. Skip a database that rejects the connection. Prefix is the table name with `_processes` removed.

- [ ] **Step 4: Re-run discovery tests**

Expected: PASS. Existing Mongo discovery tests still pass and now include `engine: "mongo"`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-api/src/discovery.ts packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/__tests__/discovery-engines.test.ts
git commit -m "feat(optio-api): discover Postgres instances beside Mongo"
```

### Task 12: Dashboard, UI, and the widget query

**Files:**
- Modify: `packages/optio-dashboard/src/cli.ts`
- Modify: `packages/optio-dashboard/src/server.ts`
- Modify: `packages/optio-ui/src/context/OptioProvider.tsx`
- Modify: `packages/optio-ui/src/hooks/useInstanceDiscovery.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessQueries.ts`
- Modify: `packages/optio-ui/src/hooks/useProcessListStream.tsx`
- Modify: `packages/optio-ui/src/session/sessionEvents.ts`
- Modify: `packages/optio-ui/src/components/ProcessWidget.tsx`
- Modify: `packages/optio-dashboard/src/app/App.tsx`
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Test: `packages/optio-api/src/adapters/__tests__/fastify-widget-proxy.test.ts`
- Test: `packages/optio-ui` unit test for the widget URL

**Interfaces:**
- Consumes: `readDashboardStorage`, discovery `engine`, `selectEngine`
- Produces: `OptioProvider` accepts `engine?: "mongo" | "postgres"`. The client sets `engine` on every query. The picker label is `` `${engine} ${database}/${prefix}` ``. Postgres widget URLs are the existing path plus `?engine=postgres`. The proxy deletes `engine` from the query before it builds the upstream URL.

- [ ] **Step 1: Write the failing widget test**

```typescript
it('does not forward engine to the upstream', async () => {
  const seen: string[] = [];
  // reuse the suite's upstream fake, which records the requested URL
  const res = await app.inject({
    method: 'GET',
    url: `/api/widget/${DB}/${PREFIX}/${oid}/page?engine=postgres&x=1`,
  });
  expect(res.statusCode).not.toBe(404);
  expect(seen[0]).not.toContain('engine=');
  expect(seen[0]).toContain('x=1');
});
```

And a UI test:

```typescript
expect(widgetProxyUrl({ engine: 'postgres', database: 'app', prefix: 'optio', id, baseUrl: '' }))
  .toBe(`/api/widget/app/optio/${id}/?engine=postgres`);
expect(widgetProxyUrl({ engine: 'mongo', database: 'app', prefix: 'optio', id, baseUrl: '' }))
  .toBe(`/api/widget/app/optio/${id}/`);
```

- [ ] **Step 2: Run those tests**

Expected: FAIL. The postgres URL has no `engine`, and the proxy forwards the query string unchanged.

- [ ] **Step 3: Implement**

Dashboard `server.ts` calls `readDashboardStorage`, connects to Mongo as it does today, and connects to Postgres only when `postgresUrl` is non-null. On Postgres failure, log a warning and pass `postgresPool: null`. On Mongo failure, startup still throws.

`App.tsx` `instanceKey` becomes `` `${inst.engine} ${inst.database}/${inst.prefix}` `` and passes `engine` into `OptioProvider`.

In `fastify.ts`, read `engine` from the query, use it for the reader that loads `widgetUpstream`, then delete it from the query string copied to the upstream request.

- [ ] **Step 4: Re-run the widget suite and the UI unit test**

Expected: PASS. Existing Mongo widget URLs in the suite stay free of `engine`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-dashboard packages/optio-ui packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/adapters/__tests__/fastify-widget-proxy.test.ts
git commit -m "feat(optio-ui): select and proxy instances by engine"
```

### Task 13: Demo, docs, and the cross-language read

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/__main__.py`
- Modify: `AGENTS.md`
- Modify: `packages/optio-core/AGENTS.md`
- Modify: `packages/optio-api/AGENTS.md`
- Test: `packages/optio-core/tests/test_postgres_doc_shape.py`
- Test: `packages/optio-api/src/__tests__/postgres-reader-contract.test.ts`

**Interfaces:**
- Consumes: `storage_config_from_env`, `PostgresProcessStore`
- Produces: the demo calls `storage_config_from_env(default_mongo_url="mongodb://localhost:27017/optio-demo", default_postgres_url="postgresql://localhost:5432/optio-demo")` and `init(storage=cfg, ...)`. Root `AGENTS.md` contains the dual-backend rule from the spec, verbatim in meaning.

- [ ] **Step 1: Write the failing doc-shape test**

```python
async def test_typescript_reader_sees_the_python_row(pg_store, pg_dsn, prefix):
    saved = await pg_store.upsert_process(TaskInstance(execute=dummy, process_id="p", name="From Python"))
    env = {**os.environ, "CONTRACT_DATABASE_URL": pg_dsn, "CONTRACT_PREFIX": prefix, "CONTRACT_ID": str(saved["_id"])}
    proc = subprocess.run(
        ["pnpm", "--filter", "optio-api", "exec", "vitest", "run", "src/__tests__/postgres-reader-contract.test.ts"],
        check=True, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0
```

```typescript
import pg from 'pg';
import { createPostgresReader } from '../postgres-reader.js';

test('reads the row the Python store wrote', async () => {
  const pool = new pg.Pool({ connectionString: process.env.CONTRACT_DATABASE_URL });
  const reader = createPostgresReader(pool, process.env.CONTRACT_PREFIX!);
  const proc = await reader.get(process.env.CONTRACT_ID!);
  expect(proc.name).toBe('From Python');
  expect(proc._id).toBe(process.env.CONTRACT_ID);
  await pool.end();
});
```

Task 13 runs after Task 10, so `createPostgresReader` already exists.

- [ ] **Step 2: Run it**

Run: `pytest packages/optio-core/tests/test_postgres_doc_shape.py -v`

Expected: PASS when Tasks 5 and 10 stored and returned the hex id and the name. A mismatch fails this test. The vitest file is written in Step 1, next to the pytest.

- [ ] **Step 3: Point the demo at the parser and update the three AGENTS files**

```python
cfg = storage_config_from_env(
    default_mongo_url="mongodb://localhost:27017/optio-demo",
    default_postgres_url="postgresql://localhost:5432/optio-demo",
)
services = {"optio": fw, "prefix": cfg.prefix}
if cfg.engine == "mongo":
    client = AsyncIOMotorClient(cfg.url)
    services["db"] = client[cfg.database]
await fw.init(storage=cfg, redis_url=redis_url, services=services, get_task_definitions=get_task_definitions)
```

The extra Motor client is only for the demo's agent tasks, which read `services["db"]`. `init(storage=cfg)` opens the engine's own connection. On Postgres, `services` has no `db`. Those agent tasks are the blob path and are out of scope.

Copy the AGENTS.md rule from the spec into the root file. In the package files, document `StorageConfig`, the `postgres` extra, the `engine` query, and `readDashboardStorage`.

- [ ] **Step 4: Run the demo import and the doc-shape test**

Run: `pytest packages/optio-core/tests/test_postgres_doc_shape.py packages/optio-core/tests/test_storage_config.py -v`

Expected: PASS. `python -c "import optio_demo"` is not required; the demo module imports Motor only inside `main` after this change, or still imports it only when the engine is Mongo. Do not import Motor at demo import time when the config is Postgres.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-demo/src/optio_demo/__main__.py AGENTS.md packages/optio-core/AGENTS.md packages/optio-api/AGENTS.md packages/optio-core/tests/test_postgres_doc_shape.py
git commit -m "docs: require process-store changes on both backends"
```
