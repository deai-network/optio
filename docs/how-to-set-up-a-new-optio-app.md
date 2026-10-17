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

*(next)*

## 5. The engine ↔ API contract

## 6. The API ↔ frontend contract

## 7. The API server

## 8. The frontend

## 9. Running and checking it

## 10. Production build

## Appendix A: working on optio and your app at the same time

## Appendix B: troubleshooting
