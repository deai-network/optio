# How to set up a new optio app

This guide builds a complete optio application from an empty directory: a Python
engine, a TypeScript API server and a React frontend. Every step has been carried out
as written. Where the guide says what a command prints, that is what it printed.

The running example is **pressroom**, a small app that "prints" documents. It shows
both ways an optio app does work:

- a **direct engine call**, which answers at once: estimating how many pages a text
  will take;
- an **optio process**, which runs in the background and reports progress: printing.

Replace `pressroom` with your app's name throughout.

## 1. What you will build

![Architecture](images/architecture-future.png)

An optio app has three layers, plus MongoDB and Redis:

| Layer | Package | Language | Uses | Role |
|---|---|---|---|---|
| Engine | `packages/engine` | Python | `optio-core` | Runs the business logic and the optio processes. **The only writer to MongoDB.** |
| API server | `packages/api` | TypeScript | `optio-api` | Serves data from MongoDB to the frontend. Passes every change to the engine as an RPC call. In production it also serves the built frontend. |
| Frontend | `packages/frontend` | TypeScript | `optio-ui` | The web UI. Talks only to the API server. |

A fourth package, `packages/contracts`, holds the two contracts that connect them:

- **API ↔ frontend:** a [ts-rest](https://ts-rest.com) contract. Both sides import it
  and get typed requests and responses.
- **Engine ↔ API:** a [clamator](https://github.com/deai-network/clamator) contract.
  Clamator is a typed RPC layer over Redis Streams. The contract is written once in Zod,
  and a code generator turns it into a TypeScript client for the API and a Python base
  class for the engine.

### Who writes what

- **The engine is the only writer.** Your domain data and optio's process records are
  written by the engine and nothing else.
- **The API server only reads MongoDB.** For anything that changes state, it calls the
  engine over clamator and returns the engine's answer.
- **The frontend only talks to the API server**, over REST and Server-Sent Events.

### The two ways the engine does work

**A direct call** answers within one request:

```
frontend --REST--> API server --clamator RPC--> engine handler
                                                     |
frontend <--------- API server <-------- result -----+
```

**An optio process** runs in the background, and the UI watches it:

```
frontend (optio-ui) --REST--> optio-api --optio's own RPC--> engine runs the task
                                                                  |
                                                writes progress to MongoDB
                                                                  |
frontend (optio-ui) <--SSE-- optio-api <--- reads/watches MongoDB +
```

Choose a direct call when the answer is ready within a few seconds. Choose an optio
process when the work is long, when the user should see progress or be able to cancel
it, or when it should run on a schedule.

### The app's optio instance: database name + prefix

optio identifies an app's engine by a pair: the **MongoDB database name** and the
**optio prefix**.

- optio's collections are named `<prefix>_processes`, `<prefix>_launch_blocks`, and so
  on, in that database.
- Its Redis keys start with `<database>/<prefix>:`.

**All three layers must use the same pair.** If they differ, the API server's calls to
the engine time out and the UI shows no processes. This guide keeps the pair in one
`.env` file that every layer reads.

Several apps can share one MongoDB server and one Redis, as long as each uses its own
pair. Run exactly one engine per pair.

### Versions this guide was verified with

| Component | Version |
|---|---|
| Node.js | 24.19.0 |
| pnpm | 12.14.0 |
| Python | 3.13.5 |
| Docker Compose | 2.26.1 |
| MongoDB / Redis images | `mongo:7` / `redis:7-alpine` |

## 2. Prerequisites and infrastructure

You need:

- **Node.js 24.** It includes corepack, which installs the pnpm version your repo
  pins.
- **pnpm**, through corepack. If `pnpm` is not on your `PATH`, run
  `corepack enable pnpm` once.
- **Python 3.11 or newer** for the engine.
- **Docker** with the Compose plugin, for MongoDB and Redis. Skip this if you already
  have both running; see below.

### Choose the database name and the prefix

Decide them now. Unless you have a reason not to, use your app's name for both:

```
MONGO_DB=pressroom
OPTIO_PREFIX=pressroom
```

### MongoDB and Redis

Put this `docker-compose.yml` at the root of your repo:

```yaml
# MongoDB (single-node replica set) and Redis for local development.
#
# The replica set lets optio-api watch for changes instead of polling.
# The member is registered as localhost:<port>, so processes on the host can
# connect without directConnection=true. To run on other ports, set
# MONGO_PORT / REDIS_PORT in the environment (or in .env).
services:
  mongodb:
    image: mongo:7
    command: ["--replSet", "rs0", "--bind_ip_all", "--port", "${MONGO_PORT:-27017}"]
    ports:
      - "${MONGO_PORT:-27017}:${MONGO_PORT:-27017}"
    volumes:
      - mongo-data:/data/db
    healthcheck:
      # The first run initiates the replica set; the service is healthy once
      # this node is a writable primary.
      test:
        - CMD-SHELL
        - >-
          mongosh --port ${MONGO_PORT:-27017} --quiet --eval
          "try { rs.status() } catch (e) { rs.initiate({ _id: 'rs0', members: [{ _id: 0, host: 'localhost:${MONGO_PORT:-27017}' }] }) };
          quit(db.hello().isWritablePrimary ? 0 : 1)"
      interval: 2s
      timeout: 10s
      retries: 30
      start_period: 10s

  redis:
    image: redis:7-alpine
    ports:
      - "${REDIS_PORT:-6379}:6379"

volumes:
  mongo-data:
```

Start them, and wait until MongoDB is ready:

```bash
docker compose up -d --wait
```

The command returns once both containers are up and MongoDB is a writable primary,
which takes about ten seconds the first time. To check:

```bash
docker compose exec mongodb mongosh --quiet --eval 'rs.status().members.map(m => m.name + " " + m.stateStr)'
# [ 'localhost:27017 PRIMARY' ]
docker compose exec redis redis-cli ping
# PONG
```

**Why a replica set.** optio-api streams process updates to the browser. On a replica
set it watches MongoDB's change streams. On a standalone server it falls back to
re-reading every second. Everything still works on a standalone server; the replica
set only makes updates cheaper and faster.

**Why `localhost:<port>` as the member name.** A replica-set client connects to the
member names the set reports, not to the address in its URL. The engine and the API
server run on the host, so the member must be reachable from the host under its own
name. A plain `mongodb://localhost:27017` then works, without `directConnection=true`.

**If you already run MongoDB and Redis**, skip the compose file and point `.env` at
them. Use a database name and prefix that no other app uses. To use change streams,
the MongoDB server must be a replica set whose member names are reachable from where
your engine and API server run.

## 3. The repo skeleton

The finished repo looks like this:

```
pressroom/
  docker-compose.yml
  Makefile
  package.json
  pnpm-workspace.yaml
  .env.example        committed
  .env                not committed
  .venv/              the engine's Python environment, not committed
  packages/
    contracts/        ts-rest and clamator contracts            (section 5, 6)
    engine/           Python, optio-core                         (section 4)
    api/              TypeScript, Fastify + optio-api            (section 7)
    frontend/         TypeScript, Vite + React + optio-ui        (section 8)
```

This section creates the root files. Each package is added in its own section.

Create the directory and a git repo:

```bash
mkdir pressroom && cd pressroom
git init -b main
mkdir packages
```

`package.json`. The `packageManager` field pins pnpm, and corepack installs that
version on first use:

```json
{
  "name": "pressroom",
  "private": true,
  "type": "module",
  "packageManager": "pnpm@12.14.0",
  "engines": {
    "node": ">=24"
  }
}
```

`pnpm-workspace.yaml`. Only your own packages are listed; optio's packages come from
the npm registry. (To develop optio and your app side by side, see
[Appendix A](#appendix-a-working-on-optio-and-your-app-at-the-same-time).)

```yaml
packages:
  - 'packages/*'
```

The engine package has no `package.json`, so pnpm ignores it.

`.env.example`, committed, and a copy as `.env`, not committed:

```ini
# Read by all three layers: the engine, the API server and the frontend dev
# server. Copy to .env and adjust.

MONGO_URL=mongodb://localhost:27017
# The database name and the optio prefix together identify this app's optio
# instance. All three layers must use the same pair.
MONGO_DB=pressroom
OPTIO_PREFIX=pressroom

REDIS_URL=redis://localhost:6379

API_PORT=3000
```

```bash
cp .env.example .env
```

`.gitignore`:

```
node_modules/
dist/
.venv/
__pycache__/
*.egg-info/
.env
```

`Makefile`. It grows a target or two in each section; it starts with the
infrastructure and the engine's virtual environment:

```make
.PHONY: deps deps-stop venv

PYTHON ?= python3
VENV := .venv

deps:  ## Start MongoDB and Redis, and wait until they are ready
	docker compose up -d --wait

deps-stop:  ## Stop MongoDB and Redis (the data volume is kept)
	docker compose down

venv: $(VENV)/bin/python  ## Create the Python venv for the engine

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
```

Install and commit:

```bash
pnpm install    # first run: corepack downloads the pinned pnpm
make venv
git add -A && git commit -m "Skeleton"
```

`pnpm install` has nothing to install yet. It creates `pnpm-lock.yaml`, which you
commit.

## 4. The engine

The engine is a Python package that uses optio-core. It owns the database: it runs
the migrations, defines the tasks, and is the only process that writes.

```
packages/engine/
  pyproject.toml
  src/pressroom_engine/
    __init__.py         empty
    __main__.py         startup: migrations, optio init, run
    config.py           settings from the environment / .env
    tasks.py            the task definitions
    migrations/
      __init__.py       the migration registry
      m001_documents_collection.py
```

### The package

`packages/engine/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "pressroom-engine"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "optio-core[redis]>=0.6.1,<0.7",
    "mongo-quaestor>=0.1,<0.2",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["watchfiles>=1.0"]

[tool.setuptools.packages.find]
where = ["src"]
```

- **`optio-core[redis]`** brings motor (the async MongoDB driver), clamator and the
  scheduler. The `redis` extra is what lets the API server reach the engine.
  optio-core is at 0.x, where a minor version can break things, so pin the minor
  version.
- **`mongo-quaestor`** is the migration library. optio-core uses it too. It is
  imported as `quaestor`. Do not install the unrelated PyPI package that is
  actually called `quaestor`: the two collide.
- The module is called `pressroom_engine` rather than `engine`, so it cannot clash
  with anything else in the virtual environment.

### Configuration

`src/pressroom_engine/config.py` reads the shared `.env`:

```python
import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


@dataclass(frozen=True)
class Config:
    mongo_url: str
    mongo_db: str
    redis_url: str
    optio_prefix: str


def load_config() -> Config:
    # The nearest .env above the working directory. Variables already set in
    # the environment win, so production can do without the file.
    load_dotenv(find_dotenv(usecwd=True))
    return Config(
        mongo_url=os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
        # No defaults: these two must match the API server's, so a missing
        # value is an error rather than a silent mismatch.
        mongo_db=os.environ["MONGO_DB"],
        optio_prefix=os.environ["OPTIO_PREFIX"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
    )
```

### Migrations

Your app's database changes over time: new collections, new indexes, renamed fields.
Each change is a migration, a small async function that runs once per database.
quaestor keeps a record of the ones already applied.

`src/pressroom_engine/migrations/__init__.py` holds the registry:

```python
from quaestor import MigrationRegistry

migrations = MigrationRegistry()

# Each module registers its migration on import. Add new ones at the end.
from pressroom_engine.migrations import m001_documents_collection  # noqa: E402, F401
```

`src/pressroom_engine/migrations/m001_documents_collection.py` is the first
migration. It creates the collection that section 5 stores documents in:

```python
from pressroom_engine.migrations import migrations


@migrations.register("documents_collection")
async def documents_collection(db):
    if "documents" not in await db.list_collection_names():
        await db.create_collection("documents")
    await db["documents"].create_index([("createdAt", -1)])
```

- A migration is identified by its **name**, the string passed to `register`. Never
  rename or remove one that has been applied anywhere.
- quaestor orders migrations by `depends_on`, and otherwise alphabetically by
  migration name, not by file name. To keep them in the order you write them, make
  each new migration depend on the previous one:
  `@migrations.register("add_titles", depends_on=["documents_collection"])`.
- If a migration raises, the engine stops, and the migration is not recorded. It
  runs again on the next start.

optio-core also has **deferred migrations** (`optio_core.deferred_migrations`). These
run once after the task definitions have been synced, for the rare change that has
to rewrite optio's own process records. You do not need them to start.

### Tasks

`src/pressroom_engine/tasks.py`:

```python
import asyncio

from optio_core import TaskInstance

TEST_PAGE_COUNT = 10


async def print_test_page(ctx) -> None:
    for page in range(1, TEST_PAGE_COUNT + 1):
        if not ctx.should_continue():
            return  # cancelled
        ctx.report_progress(
            100 * (page - 1) / TEST_PAGE_COUNT,
            f"Printing page {page} of {TEST_PAGE_COUNT}",
        )
        await asyncio.sleep(1)
    ctx.report_progress(100, f"Printed {TEST_PAGE_COUNT} pages")


async def get_task_definitions(services, metadata_filter=None) -> list[TaskInstance]:
    # optio drops the tasks that do not match metadata_filter itself, so the
    # filter can be ignored here.
    return [
        TaskInstance(
            execute=print_test_page,
            process_id="print-test-page",
            name="Print a test page",
        ),
    ]
```

- optio calls **`get_task_definitions(services, metadata_filter)`** at startup and
  on every resync. It creates a process record for each new task, updates the
  changed ones and deletes the ones that are gone, cancelling them first if they are
  running.
  Both arguments are passed positionally, so the function must accept two.
- A task's **`execute`** function receives a context. `ctx.report_progress(percent,
  message)` updates the progress bar and writes a log line. `ctx.should_continue()`
  turns `False` once someone cancels; a task that never checks it cannot be
  cancelled. `ctx.services` is the dict passed to `init()`. `ctx.params` and
  `ctx.metadata` come from the `TaskInstance`.
- This first task is fixed. Section 5 adds one task per document, generated from the
  database. Keep at least one task from the start: optio's instance discovery only
  finds an engine whose processes collection has a record in it.

### Startup

`src/pressroom_engine/__main__.py`:

```python
import asyncio
import logging

import optio_core
from motor.motor_asyncio import AsyncIOMotorClient

from pressroom_engine.config import load_config
from pressroom_engine.migrations import migrations
from pressroom_engine.tasks import get_task_definitions

# Applied migrations are recorded in <MIGRATIONS_PREFIX>_migrations. Keep this
# constant: a new value means a new, empty record, and every migration would
# run again.
MIGRATIONS_PREFIX = "pressroom"


async def main() -> None:
    config = load_config()
    client = AsyncIOMotorClient(config.mongo_url, tz_aware=True)
    db = client[config.mongo_db]

    await migrations.run(db, prefix=MIGRATIONS_PREFIX)

    await optio_core.init(
        mongo_db=db,
        prefix=config.optio_prefix,
        redis_url=config.redis_url,
        services={"db": db},
        get_task_definitions=get_task_definitions,
    )
    # The app's own RPC services are registered here, between init() and
    # run(); see section 5.
    await optio_core.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
```

- **The order matters:** your migrations, then `init()`, then `run()`. `init()` runs
  optio's own migrations, syncs the task definitions and connects to Redis. `run()`
  starts listening for commands and blocks until the process gets SIGINT or SIGTERM,
  then shuts down cleanly.
- **`tz_aware=True`** makes the driver return timezone-aware datetimes. The generated
  RPC models in section 5 reject naive ones.
- **`services`** is handed to every task as `ctx.services`. Put shared clients in it:
  the database, an HTTP client, and so on.
- The **migrations prefix is a constant**, not `OPTIO_PREFIX` from `.env`. If it
  followed `.env`, changing the prefix would point quaestor at an empty record and
  every migration would run again.

### Install and run

Add to the `Makefile`:

```make
.PHONY: install-engine engine

install-engine: venv  ## Install the engine (editable) and its dev tools
	$(VENV)/bin/pip install -e "packages/engine[dev]"

engine:  ## Run the engine; restart it when its source changes
	$(VENV)/bin/watchfiles --sigint-timeout 15 \
	  "$(VENV)/bin/python -m pressroom_engine" packages/engine/src
```

```bash
make install-engine
make engine
```

`watchfiles` restarts the engine whenever a file under `packages/engine/src` changes.
`--sigint-timeout 15` gives a running task time to stop before the old engine is
killed.

The first start prints:

```
INFO:quaestor:[pressroom] 1 migrations to apply: ['documents_collection']
INFO:quaestor:[pressroom] Applying migration: documents_collection
INFO:quaestor:[pressroom] Migration 'documents_collection' applied successfully
INFO:quaestor:[pressroom_fw] 4 migrations to apply: ['status_subdocument', ...]
...
INFO:optio_core_core:Synced 1 task definitions (all)
INFO:quaestor:[pressroom_fw_deferred] All 0 migrations already applied
INFO:optio_core_core:Optio initialized: db='pressroom', prefix='pressroom', redis='redis://localhost:6379'
INFO:optio_core.conversation_archive:conversation archive listening; published http://127.0.0.1:42625/
INFO:optio_core_core:Synced 1 task definitions (all)
```

Later starts print `All 1 migrations already applied`. The `pressroom_fw` lines are
optio's own migrations, kept apart from yours.

The `pressroom` database now has:

| Collection | Written by | Holds |
|---|---|---|
| `documents` | your migration | empty for now |
| `pressroom_migrations` | quaestor | your applied migrations |
| `pressroom_fw_migrations` | optio | optio's applied migrations |
| `pressroom_processes` | optio | one record per task: `print-test-page`, state `idle` |
| `pressroom_engine_endpoints` | optio | where the engine's internal HTTP endpoints listen |

Redis has `pressroom/pressroom:heartbeat`, which the engine refreshes every few
seconds, and `pressroom/pressroom:cmds:optio-engine`, the stream it reads commands
from.

### Test the engine with optio-dashboard

You do not need the API server and the frontend to try the engine.
[optio-dashboard](../packages/optio-dashboard) is a ready-made optio-api server and
optio-ui frontend in one package. Point it at your database and prefix, and you can
launch, watch and cancel every task:

```bash
MONGODB_URL=mongodb://localhost:27017/pressroom \
OPTIO_PREFIX=pressroom \
REDIS_URL=redis://localhost:6379 \
OPTIO_PASSWORD=choose-a-password \
PORT=3999 \
npx -y optio-dashboard
```

Open `http://localhost:3999`, sign in with the password, and the header shows
`pressroom/pressroom`. Click the play icon next to "Print a test page": the progress
bar fills over ten seconds, and the log fills in live.

- **Put the database name in `MONGODB_URL`.** With a database in the path, the
  dashboard looks only at that database. Without one, it lists every optio instance on
  the whole MongoDB server, and picks the first live one. On a server shared with other
  apps, that may not be yours.
- **Set `OPTIO_PREFIX`**, so only your prefix is listed.
- `OPTIO_PASSWORD` is required. The dashboard keeps its login data in the database
  from `MONGODB_URL`, in the collections `user`, `account` and `session`. Without a
  database in the URL, it uses a database called `optio`.
- npm prints peer-dependency warnings while installing it, and Fastify prints a
  deprecation warning at startup. Both are harmless.

What the dashboard cannot test is your app's own engine calls: the direct calls of
section 5 need your own API server.

## 5. The engine ↔ API contract

## 6. The API ↔ frontend contract

## 7. The API server

## 8. The frontend

## 9. Running and checking it

## 10. Production build

## Appendix A: working on optio and your app at the same time

## Appendix B: troubleshooting
