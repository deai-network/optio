# Engine RPC Migration Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Working directory:** All work happens in the git worktree at `/home/csillag/deai/optio/.worktrees/rpc-migration-phase-3/` on branch `csillag/rpc-migration-phase-3`. Subagents must operate inside this directory. Outputs landing in `/home/csillag/deai/optio/` (the main checkout) are wrong location — flag immediately.

**Goal:** Migrate the optio-api HTTP command path from the legacy redis-stream `${prefix}:commands` channel to clamator RPC, with simultaneous adapter-layer cleanup that extracts framework-agnostic code into a new context layer.

**Architecture:** Two-stage rollout. Stage A (six commits) introduces `OptioContext`, migrates read and command handlers to a uniform `(ctx, query, ...)` signature, extracts shared SSE/poller helpers, hardens the interop runner, and documents binding adapter-layer rules. Stage A keeps the legacy redis-stream channel in use; no behavior changes. Stage B (four commits) per-endpoint swaps `publishX(...)` to `engine.X({...})`, flips 404/409 body shape from `{message}` to `{reason, message}` with per-command typed error bodies, flips resync HTTP 200 → 202 (notification semantics), and deletes `publisher.ts`.

**Tech Stack:** TypeScript (`packages/optio-api`, `packages/optio-contracts`, `packages/optio-demo/interop`), Python 3.12 (`packages/optio-core`), pnpm workspaces, Vitest, fastify / express / Next.js adapters, ts-rest, Zod, ioredis, MongoDB (`mongodb` driver / `motor`), clamator RPC over redis (`@clamator/over-redis`, `clamator_protocol`).

**Spec reference:** Full design at `docs/2026-05-08-engine-rpc-migration-phase-3-design.md`. This plan implements that spec.

---

## File structure

| Path | Action | Purpose |
|---|---|---|
| `packages/optio-demo/run-interop.sh` | Modify | Hard timeouts, readiness probes, distinct exit codes, log tailing |
| `packages/optio-demo/interop/run.ts` | Modify | Per-scenario timeout wrapper, structured logging, top-level safety net |
| `Makefile` | Modify | `test-interop` docstring with `INTEROP_DEBUG` / `INTEROP_KEEP` env hints |
| `packages/optio-api/src/context.ts` | Create | `OptioContext { dbOpts, engineCache, redis }` + `createOptioContext` factory |
| `packages/optio-api/src/__tests__/context.test.ts` | Create | Unit tests for context module |
| `packages/optio-api/src/handlers.ts` | Modify | All read + command handlers migrate to `(ctx, query, ...)` signature; Stage B rewrites command handlers to call `engine.X(...)` |
| `packages/optio-api/src/__tests__/handlers.test.ts` | Modify | Test suite rewritten for new signatures, then for engine mock |
| `packages/optio-api/src/adapters/fastify.ts` | Modify | Routes shrink to one-liners using ctx |
| `packages/optio-api/src/adapters/express.ts` | Modify | Same |
| `packages/optio-api/src/adapters/nextjs-app.ts` | Modify | Same |
| `packages/optio-api/src/adapters/nextjs-pages.ts` | Modify | Same |
| `packages/optio-api/src/adapters/__tests__/*.test.ts` | Modify | Adjust assertions for new wiring + Stage B body shapes |
| `packages/optio-api/src/sse-options.ts` | Create | Shared SSE/poller query parsing helper |
| `packages/optio-api/src/__tests__/sse-options.test.ts` | Create | Unit tests for SSE helper |
| `packages/optio-api/AGENTS.md` | Modify | New "Layer rules (binding)" section |
| `packages/optio-api/README.md` | Modify | Layer-model paragraph |
| `AGENTS.md` (root) | Modify | One-liner pointing to optio-api layer rules |
| `packages/optio-api/src/index.ts` | Modify | Export `OptioContext` (A1); remove `publishX` exports (3d) |
| `packages/optio-contracts/src/api-to-frontend.ts` | Modify | Per-command `LaunchErrorBody` / `CancelErrorBody` / `DismissErrorBody` (Stage B); resync 200 → 202 (3d) |
| `packages/optio-contracts/src/contract.ts` | Modify | Same as above (file may be renamed; check phase-1 status) |
| `packages/optio-api/src/publisher.ts` | Delete | In commit 3d |
| `packages/optio-api/src/__tests__/publisher.test.ts` | Delete | In commit 3d |
| `packages/optio-demo/interop/run-http.ts` | Create | Stage B HTTP-roundtrip scenarios (fastify + real engine over real redis) |

---

## Task 1: A0 — Robustify interop runner

**Files:**
- Modify: `packages/optio-demo/run-interop.sh`
- Modify: `packages/optio-demo/interop/run.ts`
- Modify: `Makefile`

**Goal:** Add hard per-step timeouts, readiness probes with bounded waits, distinct exit codes, real-time log tailing, and a hard wall-clock budget. No scenario changes.

- [ ] **Step 1: Read the current runner**

```bash
cat packages/optio-demo/run-interop.sh
cat packages/optio-demo/interop/run.ts
cat Makefile | grep -A 3 '^test-interop'
```

Note the current shape: 94-line bash script, 209-line ts runner, ~5-line Makefile target. Identify the existing readiness loops (redis 50×0.2s, mongo 75×0.4s, engine 150×0.2s) and the existing exit code (single non-zero on any failure).

- [ ] **Step 2: Rewrite `run-interop.sh` with hardened orchestration**

Replace the entire file with:

```bash
#!/usr/bin/env bash
# Phase-3 hardened interop test for optio engine ↔ TS clamator client.
# Hard per-step timeouts, distinct exit codes, real-time log tailing.
#
# Exit codes:
#   0   success
#   10  docker pre-flight failed (no docker daemon, missing image, pull failed)
#   11  redis not ready within bound
#   12  mongo not ready within bound
#   13  engine not ready within bound
#   14  fastify not ready within bound (Stage B; unused in A0)
#   15  scenario assertion failed
#   16  cleanup error (best-effort, not fatal)
#   124 outer timeout (script wall-clock exceeded)
#
# Env knobs:
#   INTEROP_DEBUG=1   verbose mode, increased timeouts (30/30/60s instead of 5/10/15s)
#   INTEROP_KEEP=1    skip cleanup on failure for postmortem

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$REPO_ROOT/packages/optio-demo"

# Bounds (overridable via INTEROP_DEBUG).
if [[ "${INTEROP_DEBUG:-0}" == "1" ]]; then
  REDIS_TIMEOUT=30
  MONGO_TIMEOUT=30
  ENGINE_TIMEOUT=60
  SCENARIO_TIMEOUT=120
else
  REDIS_TIMEOUT=5
  MONGO_TIMEOUT=10
  ENGINE_TIMEOUT=15
  SCENARIO_TIMEOUT=60
fi

ENGINE_LOG="/tmp/optio-interop-engine.log"
> "$ENGINE_LOG"

phase() { echo "[interop] phase=$1"; }
die() { local code="$1"; shift; echo "[interop] ERROR: $*" >&2; exit "$code"; }

cleanup() {
  set +e
  phase cleanup
  if [[ "${INTEROP_KEEP:-0}" == "1" && "${EXIT_CODE:-0}" -ne 0 ]]; then
    echo "[interop] INTEROP_KEEP=1: skipping container/process cleanup for postmortem" >&2
    echo "[interop] engine log: $ENGINE_LOG" >&2
    [[ -n "${REDIS_CID:-}" ]] && echo "[interop] redis CID: $REDIS_CID" >&2
    [[ -n "${MONGO_CID:-}" ]] && echo "[interop] mongo CID: $MONGO_CID" >&2
    [[ -n "${ENGINE_PID:-}" ]] && echo "[interop] engine PID: $ENGINE_PID" >&2
    return 0
  fi
  if [[ -n "${ENGINE_PID:-}" ]]; then
    kill -9 "$ENGINE_PID" 2>/dev/null
    wait "$ENGINE_PID" 2>/dev/null
  fi
  [[ -n "${REDIS_CID:-}" ]] && docker rm -f "$REDIS_CID" >/dev/null 2>&1
  [[ -n "${MONGO_CID:-}" ]] && docker rm -f "$MONGO_CID" >/dev/null 2>&1
}
trap 'EXIT_CODE=$?; cleanup; exit $EXIT_CODE' EXIT

# Pre-flight: docker daemon + images.
phase docker-pre-flight
docker info >/dev/null 2>&1 || die 10 "docker daemon not reachable"
for img in redis:7 mongo:7; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "[interop] pulling $img..."
    timeout 30 docker pull "$img" >/dev/null 2>&1 || die 10 "pull failed for $img"
  fi
done

# Allocate free ports.
REDIS_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
MONGO_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")

# Start redis.
phase redis-up
REDIS_CID=$(docker run -d -p "${REDIS_PORT}:6379" redis:7)
DEADLINE=$(( $(date +%s) + REDIS_TIMEOUT ))
while (( $(date +%s) < DEADLINE )); do
  if docker exec "$REDIS_CID" redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "[interop] redis ready on port $REDIS_PORT"
    break
  fi
  sleep 0.2
done
if ! docker exec "$REDIS_CID" redis-cli ping 2>/dev/null | grep -q PONG; then
  docker logs --tail 50 "$REDIS_CID" >&2 2>/dev/null
  die 11 "redis not ready within ${REDIS_TIMEOUT}s"
fi

# Start mongo.
phase mongo-up
MONGO_CID=$(docker run -d -p "${MONGO_PORT}:27017" --tmpfs /data/db:rw,noexec,nosuid mongo:7)
DEADLINE=$(( $(date +%s) + MONGO_TIMEOUT ))
while (( $(date +%s) < DEADLINE )); do
  if docker exec "$MONGO_CID" mongosh --eval 'quit(db.adminCommand("ping").ok ? 0 : 1)' --quiet >/dev/null 2>&1; then
    echo "[interop] mongo ready on port $MONGO_PORT"
    break
  fi
  sleep 0.4
done
if ! docker exec "$MONGO_CID" mongosh --eval 'quit(db.adminCommand("ping").ok ? 0 : 1)' --quiet >/dev/null 2>&1; then
  docker logs --tail 50 "$MONGO_CID" >&2 2>/dev/null
  die 12 "mongo not ready within ${MONGO_TIMEOUT}s"
fi

# Start engine.
phase engine-up
MONGODB_URL="mongodb://localhost:${MONGO_PORT}/optio-demo" \
  REDIS_URL="redis://localhost:${REDIS_PORT}" \
  OPTIO_PREFIX="optio" \
  python -m optio_demo > >(tee -a "$ENGINE_LOG" | sed 's/^/[engine] /') 2> >(tee -a "$ENGINE_LOG" | sed 's/^/[engine] /' >&2) &
ENGINE_PID=$!

HEARTBEAT_KEY="optio-demo/optio:heartbeat"
DEADLINE=$(( $(date +%s) + ENGINE_TIMEOUT ))
while (( $(date +%s) < DEADLINE )); do
  if docker exec "$REDIS_CID" redis-cli exists "$HEARTBEAT_KEY" 2>/dev/null | grep -q '^1$'; then
    echo "[interop] engine ready (heartbeat present)"
    break
  fi
  if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    tail -50 "$ENGINE_LOG" >&2
    die 13 "engine subprocess exited before becoming ready"
  fi
  sleep 0.2
done
if ! docker exec "$REDIS_CID" redis-cli exists "$HEARTBEAT_KEY" 2>/dev/null | grep -q '^1$'; then
  tail -50 "$ENGINE_LOG" >&2
  die 13 "engine not ready within ${ENGINE_TIMEOUT}s"
fi

# Run scenarios.
phase running-scenarios
REDIS_URL="redis://localhost:${REDIS_PORT}" \
  timeout "$SCENARIO_TIMEOUT" pnpm --dir "$DEMO_DIR/interop" exec tsx run.ts
SCENARIO_EXIT=$?
if (( SCENARIO_EXIT != 0 )); then
  if (( SCENARIO_EXIT == 124 )); then
    die 15 "scenario runner timed out after ${SCENARIO_TIMEOUT}s"
  fi
  die 15 "scenario runner exited $SCENARIO_EXIT"
fi
echo "[interop] scenarios passed"
exit 0
```

Wrap the whole script in an outer timeout: edit `packages/optio-demo/Makefile` (if present) or add to root `Makefile` so `test-interop` becomes `timeout 120 bash packages/optio-demo/run-interop.sh`. Concretely, in step 4 below.

- [ ] **Step 3: Add per-scenario timeout to `run.ts`**

Read current `packages/optio-demo/interop/run.ts`. Find the `main()` function and the existing scenario runners. Wrap each scenario in a timeout helper. Add at the top of the file (after imports):

```ts
const SCENARIO_TIMEOUT_MS = 5000;
const FORCE_HANG = process.env.INTEROP_FORCE_HANG;

async function withTimeout<T>(name: string, fn: () => Promise<T>): Promise<T> {
  const start = Date.now();
  if (FORCE_HANG === name) {
    console.error(`[scenario] ${name} HANG (forced via INTEROP_FORCE_HANG)`);
    await new Promise(() => {});  // never resolves
  }
  console.log(`[scenario] ${name} started`);
  return await Promise.race<T>([
    fn().then((v) => {
      console.log(`[scenario] ${name} ok (${Date.now() - start}ms)`);
      return v;
    }),
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`[scenario] ${name} timed out after ${SCENARIO_TIMEOUT_MS}ms`)), SCENARIO_TIMEOUT_MS),
    ),
  ]);
}

// Top-level safety net: kill the runner if main() hasn't returned in 60s.
setTimeout(() => {
  console.error('[scenario] FATAL: 60s top-level timeout, exiting 15');
  process.exit(15);
}, 60_000).unref();
```

Then wrap each existing scenario block. For an existing scenario like:

```ts
{
  const r = await engine.launch({ processId: PROC });
  if (!r.ok || !r.process) fail('launch-success', `expected ok, got ${JSON.stringify(r)}`);
  else ok('launch-success');
}
```

rewrite as:

```ts
await withTimeout('launch-success', async () => {
  const r = await engine.launch({ processId: PROC });
  if (!r.ok || !r.process) fail('launch-success', `expected ok, got ${JSON.stringify(r)}`);
  else ok('launch-success');
});
```

Apply the wrap to every numbered scenario in `run.ts`. Keep the existing `fail()` / `ok()` helpers; the new wrapper adds timing and timeout.

- [ ] **Step 4: Update root `Makefile` `test-interop` target**

Find the `test-interop` target in the root `Makefile`. Replace its body with:

```make
## test-interop: End-to-end test: TS clamator client ↔ Py engine over real redis.
##              INTEROP_DEBUG=1 enables verbose mode + increased timeouts (slow CI).
##              INTEROP_KEEP=1  skips cleanup on failure for postmortem.
test-interop:
	timeout 120 bash packages/optio-demo/run-interop.sh
```

If the existing target uses a different make idiom (multi-line or with leading variables), preserve the surrounding context but ensure the body invokes the script with the outer 120s `timeout` wrapper.

- [ ] **Step 5: Run interop end-to-end (positive path)**

Run: `make test-interop`

Expected: exits 0 in <90s on warm cache. Stdout shows `[interop] phase=docker-pre-flight`, `phase=redis-up`, `phase=mongo-up`, `phase=engine-up`, `phase=running-scenarios`, `phase=cleanup` markers in order. All existing phase-2 scenarios print `✓` lines.

If it fails:
- Exit code 10 → docker daemon issue, fix locally.
- Exit code 11/12/13 → bound too tight; verify with `INTEROP_DEBUG=1 make test-interop`. If still failing, the readiness signal is missing — investigate.
- Exit code 15 → scenario assertion failed; read tail of engine log printed to stderr.

- [ ] **Step 6: Run interop with forced hang (negative path)**

Run: `INTEROP_FORCE_HANG=launch-success make test-interop`

Expected: exits with code 15 within ~5s of reaching the `running-scenarios` phase (per-scenario timeout 5000ms triggers, scenario runner exits non-zero). Stderr includes `[scenario] launch-success HANG`.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-demo/run-interop.sh packages/optio-demo/interop/run.ts Makefile
git commit -m "test(interop): hard timeouts, readiness probes, distinct exit codes

Replaces the phase-2 best-effort wait loops with bounded readiness
probes and a phased orchestrator. Each step has a hard timeout
(redis 5s, mongo 10s, engine 15s, scenarios 60s, whole script 120s).
Distinct exit codes per failure phase (10 docker, 11 redis, 12 mongo,
13 engine, 15 scenario, 124 outer timeout) let supervising tools
diagnose hangs without parsing stderr. Engine subprocess output is
tee'd to /tmp/optio-interop-engine.log and prefixed in real time.

Adds INTEROP_DEBUG=1 for verbose mode + 6x timeouts (slow CI) and
INTEROP_KEEP=1 to skip cleanup on failure for postmortem.

run.ts wraps each scenario in Promise.race with a 5s timeout and
a top-level 60s safety net. INTEROP_FORCE_HANG=<scenario> injects
an artificial hang for negative testing.

Phase 3, commit A0 of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

Verify: `git log -1 --stat` shows the three files changed.

---

## Task 2: A1 — Introduce `OptioContext`

**Files:**
- Create: `packages/optio-api/src/context.ts`
- Create: `packages/optio-api/src/__tests__/context.test.ts`
- Modify: `packages/optio-api/src/index.ts` (add export)

**Goal:** Add the new context module. No callers updated yet.

- [ ] **Step 1: Write the failing test**

Create `packages/optio-api/src/__tests__/context.test.ts`:

```ts
import { describe, it, expect, vi } from 'vitest';
import { Redis } from 'ioredis';
import { createOptioContext } from '../context.js';
import type { Db } from 'mongodb';

describe('createOptioContext', () => {
  it('returns a context with the supplied dbOpts and redis', () => {
    const fakeDb = { databaseName: 'testdb' } as unknown as Db;
    const fakeRedis = {} as unknown as Redis;
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    expect(ctx.dbOpts).toEqual({ db: fakeDb });
    expect(ctx.redis).toBe(fakeRedis);
    expect(ctx.engineCache).toBeDefined();
    expect(typeof ctx.engineCache.get).toBe('function');
    expect(typeof ctx.engineCache.closeAll).toBe('function');
  });

  it('engineCache.get returns the same instance for the same key', () => {
    const fakeRedis = {} as unknown as Redis;
    const ctx = createOptioContext({ dbOpts: { db: { databaseName: 'd' } as any }, redis: fakeRedis });
    const a = ctx.engineCache.get('d', 'optio');
    const b = ctx.engineCache.get('d', 'optio');
    expect(a).toBe(b);
  });

  it('closeAll is idempotent', async () => {
    const fakeRedis = {} as unknown as Redis;
    const ctx = createOptioContext({ dbOpts: { db: { databaseName: 'd' } as any }, redis: fakeRedis });
    // Construct an entry; closeAll calls stop() on it.
    const stopSpy = vi.fn().mockResolvedValue(undefined);
    const e = ctx.engineCache.get('d', 'optio');
    (e as any).stop = stopSpy;
    await ctx.engineCache.closeAll();
    await ctx.engineCache.closeAll();   // second call is a no-op
    expect(stopSpy).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/context.test.ts`

Expected: failure on import — `Cannot find module '../context.js'`.

- [ ] **Step 3: Implement `context.ts`**

Create `packages/optio-api/src/context.ts`:

```ts
import type { Redis } from 'ioredis';
import { createEngineCache, type EngineCache } from './engine-cache.js';
import type { DbOptions } from './resolve-db.js';

export interface OptioContext {
  dbOpts: DbOptions;
  engineCache: EngineCache;
  redis: Redis;
}

export function createOptioContext(opts: { dbOpts: DbOptions; redis: Redis }): OptioContext {
  return {
    dbOpts: opts.dbOpts,
    engineCache: createEngineCache(opts.redis),
    redis: opts.redis,
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/context.test.ts`

Expected: 3 passed.

- [ ] **Step 5: Add public export**

Edit `packages/optio-api/src/index.ts`. Find the existing `EngineCache` re-export (added in phase 2) and add the type export for `OptioContext`:

```ts
// add alongside existing exports:
export { type OptioContext } from './context.js';
```

(Do NOT export `createOptioContext` — adapters construct it internally.)

- [ ] **Step 6: Verify whole-package build + tests**

Run: `pnpm -r build && pnpm -r test`

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-api/src/context.ts packages/optio-api/src/__tests__/context.test.ts packages/optio-api/src/index.ts
git commit -m "feat(optio-api): add OptioContext + createOptioContext

Introduces packages/optio-api/src/context.ts. OptioContext bundles
the durable per-app resources (dbOpts, engineCache, redis) that the
adapter layer constructs once at registration time and threads
through every handler call. createOptioContext is the factory.

No callers updated in this commit. Follow-up commits migrate the
read handlers (A2) and command handlers (A3) to the (ctx, query, ...)
signature.

Phase 3, commit A1 of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 3: A2 — Read handlers migrate to ctx; adapter cleanup of redundant defaults

**Files:**
- Modify: `packages/optio-api/src/handlers.ts` (read handlers only)
- Modify: `packages/optio-api/src/__tests__/handlers.test.ts` (read tests)
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/*.test.ts` (assertions stay; check new ctx wiring works through)

**Goal:** Five read handlers (`listProcesses`, `getProcess`, `getProcessTree`, `getProcessLog`, `getProcessTreeLog`) take `(ctx, query, ...)`. Adapters drop inline `resolveDb` calls and redundant default-value fallbacks (`?? 25`, `?? 20`, manual `parseInt(maxDepth, 10)` for ts-rest tree route).

- [ ] **Step 1: Update `handlers.ts` read-handler signatures**

Open `packages/optio-api/src/handlers.ts`. Add at the top after existing imports:

```ts
import type { OptioContext } from './context.js';
import { resolveDb } from './resolve-db.js';
```

Find each read-handler function. Replace its signature and prepend `resolveDb`:

`listProcesses` becomes:

```ts
export interface ListProcessesQuery extends ListQuery {
  database?: string;
  prefix?: string;
}

export async function listProcesses(ctx: OptioContext, query: ListProcessesQuery) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const { cursor, limit, rootId, state, metadataFilter } = query;

  const filter: Record<string, unknown> = {
    ...metadataFilterToMongo(metadataFilter),
  };
  if (rootId) filter.rootId = new ObjectId(rootId);
  if (state) filter['status.state'] = state;
  if (cursor) filter._id = { $gt: new ObjectId(cursor) };

  const [items, totalCount] = await Promise.all([
    col(db, prefix).find(filter).sort({ _id: 1 }).limit(limit + 1).toArray(),
    col(db, prefix).countDocuments(filter),
  ]);
  const hasNext = items.length > limit;
  if (hasNext) items.pop();
  return {
    items: items.map(toResponse),
    nextCursor: hasNext ? items[items.length - 1]._id.toString() : null,
    totalCount,
  };
}
```

`getProcess`:

```ts
export async function getProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return null;
  return toResponse(proc);
}
```

`getProcessTree`:

```ts
export async function getProcessTree(
  ctx: OptioContext,
  query: { database?: string; prefix?: string; maxDepth?: number },
  id: string,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const entry = await findProcessByEitherId(col(db, prefix), id);
  if (!entry) return null;
  return buildTree(db, prefix, entry._id as ObjectId, query.maxDepth);
}
```

(Note: previous signature took `maxDepth` as a separate parameter; now folded into `query`.)

`getProcessLog`:

```ts
export interface GetProcessLogQuery extends PaginationQuery {
  database?: string;
  prefix?: string;
}

export async function getProcessLog(
  ctx: OptioContext,
  query: GetProcessLogQuery,
  id: string,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return null;
  const { cursor, limit } = query;
  const startIdx = cursor ? parseInt(cursor, 10) : 0;
  const logSlice = proc.log.slice(startIdx, startIdx + limit + 1);
  const hasNext = logSlice.length > limit;
  if (hasNext) logSlice.pop();
  return {
    items: logSlice,
    nextCursor: hasNext ? String(startIdx + limit) : null,
    totalCount: proc.log.length,
  };
}
```

`getProcessTreeLog`:

```ts
export interface GetProcessTreeLogQuery extends TreeLogQuery {
  database?: string;
  prefix?: string;
}

export async function getProcessTreeLog(
  ctx: OptioContext,
  query: GetProcessTreeLogQuery,
  id: string,
) {
  const { db, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return null;

  const { maxDepth, cursor, limit } = query;
  const filter: Record<string, unknown> = { rootId: proc.rootId };
  if (maxDepth !== undefined) filter.depth = { $lte: proc.depth + maxDepth };
  const allProcs = await col(db, prefix).find(filter).toArray();

  const allLogs = allProcs.flatMap((p: any) =>
    p.log.map((entry: any) => ({
      ...entry,
      processId: p._id.toString(),
      processLabel: p.name,
    })),
  );
  allLogs.sort((a: any, b: any) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());

  const startIdx = cursor ? parseInt(cursor, 10) : 0;
  const logSlice = allLogs.slice(startIdx, startIdx + limit + 1);
  const hasNext = logSlice.length > limit;
  if (hasNext) logSlice.pop();
  return {
    items: logSlice,
    nextCursor: hasNext ? String(startIdx + limit) : null,
    totalCount: allLogs.length,
  };
}
```

- [ ] **Step 2: Update `handlers.test.ts` read tests for new signatures**

Open `packages/optio-api/src/__tests__/handlers.test.ts`. Find every read-handler test. Each test currently calls e.g. `listProcesses(db, prefix, query)`. Replace with `listProcesses(ctx, query)` where `ctx` is built from a stub. Add a helper near the top of the file:

```ts
import { createOptioContext } from '../context.js';

function makeCtx(db: Db, redis: Redis = {} as any): OptioContext {
  return createOptioContext({ dbOpts: { db }, redis });
}
```

Replace each call site:

```ts
// before
const result = await listProcesses(db, PREFIX, { limit: 10 });
// after
const result = await listProcesses(makeCtx(db), { limit: 10, prefix: PREFIX });
```

Apply the equivalent rewrite to every read-test invocation in the file.

- [ ] **Step 3: Run handlers tests to verify still green**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/handlers.test.ts`

Expected: all green; read tests pass against new signatures, command tests still green (they're untouched in this commit and still call the legacy signature).

- [ ] **Step 4: Update fastify adapter — read routes**

Open `packages/optio-api/src/adapters/fastify.ts`. Around line 357, after the existing `const cache = createEngineCache(redis)` line, replace with:

```ts
import { createOptioContext } from '../context.js';
// ... near top of registerOptioApi(...):
const dbOpts: DbOptions = 'db' in opts && opts.db
  ? { db: opts.db }
  : { mongoClient: opts.mongoClient! };
const ctx = createOptioContext({ dbOpts, redis });
```

(Replace the existing `cache` variable with `ctx.engineCache` everywhere it's used. The `app.addHook('onClose', () => cache.closeAll())` becomes `app.addHook('onClose', () => ctx.engineCache.closeAll())`.)

For each read route in the ts-rest router (lines ~377-400 in current file), replace the body. Currently:

```ts
list: async ({ query }) => {
  const { db, prefix } = resolveDb(dbOpts, query);
  const result = await handlers.listProcesses(db, prefix, {
    cursor: query.cursor,
    limit: query.limit ?? 25,
    rootId: query.rootId,
    state: query.state,
    metadataFilter: query.metadataFilter,
  });
  return { status: 200 as const, body: result };
},
```

Replace with:

```ts
list: async ({ query }) => {
  const result = await handlers.listProcesses(ctx, query);
  return { status: 200 as const, body: result };
},
```

Apply equivalent shrink to `get`, `getTree`, `getLog`, `getTreeLog` routes:

```ts
get: async ({ params, query }) => {
  const result = await handlers.getProcess(ctx, query, params.id);
  if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
  return { status: 200 as const, body: result };
},

getTree: async ({ params, query }) => {
  const result = await handlers.getProcessTree(ctx, query, params.id);
  if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
  return { status: 200 as const, body: result };
},

getLog: async ({ params, query }) => {
  const result = await handlers.getProcessLog(ctx, query, params.id);
  if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
  return { status: 200 as const, body: result };
},

getTreeLog: async ({ params, query }) => {
  const result = await handlers.getProcessTreeLog(ctx, query, params.id);
  if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
  return { status: 200 as const, body: result };
},
```

Verify the existing return-value block (`if ('db' in opts && opts.db) { return { engine: cache.get(...), closeAll: ... }; ... }`) at the bottom of `registerOptioApi` continues to use `ctx.engineCache` instead of the previous `cache` local. Update accordingly.

For SSE/poller routes (search for `stream-poller` or `pollUpdates` calls) — leave inline `resolveDb` calls as-is; they will move to A4's `sse-options.ts`.

Command routes (`launch`, `cancel`, `dismiss`, `resync`) — UNCHANGED in this commit. They keep their existing signature and call sites. A3 migrates them.

- [ ] **Step 5: Update express adapter — read routes**

Same pattern as fastify in `packages/optio-api/src/adapters/express.ts`. Construct `ctx` once near the top of `registerOptioApi`. Each read route shrinks to a one-liner calling `handlers.{name}(ctx, query[, params.id])`. Drop inline `resolveDb` calls in read routes only. Drop `query.limit ?? 25` defaults.

For SSE/poller routes — leave inline `resolveDb` and inline `parseMetadataFilterQuery` / `detectLegacyMetadataParams` calls as-is; A4 handles them.

Command routes — UNCHANGED.

Bottom of `registerOptioApi`: replace `cache` references with `ctx.engineCache` for the return-value block.

- [ ] **Step 6: Update nextjs-app adapter — read routes**

Same pattern as fastify in `packages/optio-api/src/adapters/nextjs-app.ts`. Read routes shrink. SSE/poller routes (which use `url.searchParams.get(...)` for query extraction) keep their query-extraction shape but pass the resulting object to `handlers.{name}(ctx, query, id)` — i.e., the `resolveDb(dbOpts, { database, prefix })` call in those routes goes away because the handler does it internally.

Command routes — UNCHANGED.

- [ ] **Step 7: Update nextjs-pages adapter — read routes**

Same pattern. Read routes shrink, SSE routes adjust the query shape but stay otherwise. Command routes — UNCHANGED.

- [ ] **Step 8: Run adapter tests**

Run: `pnpm --filter optio-api exec vitest run src/adapters/__tests__/`

Expected: all green. Adapter integration tests don't assert the inline `resolveDb` call site; they assert HTTP behavior, which is unchanged.

- [ ] **Step 9: Run whole package build + tests**

Run: `pnpm -r build && pnpm -r test`

Expected: all green.

- [ ] **Step 10: Run interop**

Run: `make test-interop`

Expected: all phase-2 scenarios pass; Stage A2 introduces no scenario changes.

- [ ] **Step 11: Verify cleanup #2 (no redundant defaults remaining in adapters' read routes)**

Run: `grep -nE '\?\? *(20|25|10)' packages/optio-api/src/adapters/`

Expected: only matches in widget proxy (`opts.ttlMs ?? WIDGET_CACHE_TTL_MS` is fine — that's an opt not a query default), or no matches.

- [ ] **Step 12: Commit**

```bash
git add packages/optio-api/src/handlers.ts packages/optio-api/src/__tests__/handlers.test.ts packages/optio-api/src/adapters/
git commit -m "refactor(optio-api): read handlers migrate to OptioContext

Read handlers (listProcesses, getProcess, getProcessTree,
getProcessLog, getProcessTreeLog) now take (ctx, query, [id])
instead of (db, prefix, query[, id, maxDepth]). Each handler
internally resolves dbOpts -> (db, prefix) via resolveDb(ctx.dbOpts,
query); the route handler in the four adapters shrinks to a
single delegating one-liner.

Adapter cleanup: drops inline resolveDb calls in read routes,
drops redundant 'query.limit ?? 25' fallbacks (the contract layer's
PaginationQuerySchema.default(20) is the single source of truth).
Command routes and SSE/poller routes are unchanged in this commit.

No behavior change: HTTP responses for read endpoints are byte-
identical to phase-2 main.

Phase 3, commit A2 of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 4: A3 — Command handlers migrate to ctx (still publish to legacy stream)

**Files:**
- Modify: `packages/optio-api/src/handlers.ts` (command handlers)
- Modify: `packages/optio-api/src/__tests__/handlers.test.ts` (command tests)
- Modify: all four adapters' command routes
- Modify: adapter test files where command routes are exercised

**Goal:** `launchProcess`, `cancelProcess`, `dismissProcess`, `resyncProcesses` take `(ctx, query, id, ...)`. Internally still call `publishLaunch / publishCancel / publishDismiss / publishResync` against `ctx.redis`. Body shape stays `{message}`. No behavior change.

- [ ] **Step 1: Update command-handler signatures in `handlers.ts`**

In `packages/optio-api/src/handlers.ts`, replace each command-handler function:

```ts
export async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume: boolean = false,
): Promise<CommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!LAUNCHABLE_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot launch process in state: ${proc.status.state}` } };
  }
  if (resume && !proc.supportsResume) {
    return { status: 409, body: { message: 'This task does not support resume' } };
  }
  await publishLaunch(ctx.redis, database, prefix, proc.processId, resume);
  return { status: 200, body: toResponse(proc) };
}

export async function cancelProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!proc.cancellable) {
    return { status: 409, body: { message: 'Process is not cancellable' } };
  }
  if (!CANCELLABLE_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot cancel process in state: ${proc.status.state}` } };
  }
  await publishCancel(ctx.redis, database, prefix, proc.processId);
  return { status: 200, body: toResponse(proc) };
}

export async function dismissProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) {
    return { status: 404, body: { message: 'Process not found' } };
  }
  if (!END_STATES.includes(proc.status.state)) {
    return { status: 409, body: { message: `Cannot dismiss process in state: ${proc.status.state}` } };
  }
  await publishDismiss(ctx.redis, database, prefix, proc.processId);
  return { status: 200, body: toResponse(proc) };
}

export async function resyncProcesses(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<{ message: string }> {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  await publishResync(ctx.redis, database, prefix, clean, metadataFilter);
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
```

`LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES`, `CommandResult` all unchanged.

- [ ] **Step 2: Update command-handler tests in `handlers.test.ts`**

Find every command-handler test in `packages/optio-api/src/__tests__/handlers.test.ts`. Each currently calls e.g. `launchProcess(db, redis, 'mydb', PREFIX, id, true)`. Rewrite using the `makeCtx` helper from A2:

```ts
function makeCtx(db: Db, redis: Redis): OptioContext {
  return createOptioContext({ dbOpts: { db }, redis });
}

// before:
const result = await launchProcess(db, redis, 'mydb', PREFIX, id, true);
// after:
const result = await launchProcess(makeCtx(db, redis), { database: 'mydb', prefix: PREFIX }, id, true);
```

For tests asserting `redis.xadd` was called with specific args (those that test publishX behavior through the handler), the assertions stay — we still publish during Stage A. Verify the asserted stream key is constructed from the `database`/`prefix` passed via `query`, matching the previous behavior.

- [ ] **Step 3: Run handlers tests**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/handlers.test.ts`

Expected: all green.

- [ ] **Step 4: Update fastify adapter — command routes**

In `packages/optio-api/src/adapters/fastify.ts`, find each command route in the ts-rest router. Replace each:

```ts
launch: async ({ params, body, query }) => {
  const result = await handlers.launchProcess(ctx, query, params.id, body?.resume ?? false);
  return { status: result.status, body: result.body } as any;
},

cancel: async ({ params, query }) => {
  const result = await handlers.cancelProcess(ctx, query, params.id);
  return { status: result.status, body: result.body } as any;
},

dismiss: async ({ params, query }) => {
  const result = await handlers.dismissProcess(ctx, query, params.id);
  return { status: result.status, body: result.body } as any;
},

resync: async ({ body, query }) => {
  const result = await handlers.resyncProcesses(ctx, query, body.clean, body.metadataFilter);
  return { status: 200 as const, body: result };
},
```

Note `body.clean ?? false` becomes `body.clean` — the handler default param `clean = false` covers `undefined`. Drop the inline `resolveDb` calls; they're gone.

- [ ] **Step 5: Update express adapter — command routes**

Same pattern in `packages/optio-api/src/adapters/express.ts`. Replace each command route with the ctx-based one-liner. Drop inline `resolveDb`. Drop `body.clean ?? false`.

- [ ] **Step 6: Update nextjs-app adapter — command routes**

Same in `packages/optio-api/src/adapters/nextjs-app.ts`.

- [ ] **Step 7: Update nextjs-pages adapter — command routes**

Same in `packages/optio-api/src/adapters/nextjs-pages.ts`.

- [ ] **Step 8: Update adapter integration tests**

Open each `packages/optio-api/src/adapters/__tests__/*.test.ts`. Find tests for command routes. Verify they still pass; they assert HTTP roundtrip (status + body shape `{message}`) which is unchanged. If any test directly stubs `handlers.launchProcess` etc. with a particular signature, update the stub signature.

- [ ] **Step 9: Run whole package build + tests**

Run: `pnpm -r build && pnpm -r test`

Expected: all green.

- [ ] **Step 10: Run interop**

Run: `make test-interop`

Expected: all phase-2 scenarios still pass. Legacy stream still ferries commands; engine still processes via `consumer.py`.

- [ ] **Step 11: Verify cleanup #3 (no `body.clean ??` in adapters)**

Run: `grep -n 'body\.clean *??' packages/optio-api/src/adapters/`

Expected: no matches.

- [ ] **Step 12: Commit**

```bash
git add packages/optio-api/src/handlers.ts packages/optio-api/src/__tests__/handlers.test.ts packages/optio-api/src/adapters/
git commit -m "refactor(optio-api): command handlers migrate to OptioContext

Command handlers (launchProcess, cancelProcess, dismissProcess,
resyncProcesses) now take (ctx, query, id, ...) instead of
(db, redis, database, prefix, id, ...). Each handler internally
resolves dbOpts -> (db, database, prefix) via resolveDb(ctx.dbOpts,
query); the route handler in the four adapters shrinks to a
single delegating one-liner.

Internally the command handlers continue to call publishLaunch /
publishCancel / publishDismiss / publishResync against ctx.redis —
no channel change. The legacy redis-stream ingress is still in use.
HTTP behavior is byte-identical to phase-2 main.

Adapter cleanup: drops inline resolveDb calls in command routes,
drops redundant 'body.clean ?? false' fallback (handler's clean
parameter defaults to false; undefined works).

Phase 3, commit A3 of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 5: A4 — SSE/poller helper extraction

**Files:**
- Create: `packages/optio-api/src/sse-options.ts`
- Create: `packages/optio-api/src/__tests__/sse-options.test.ts`
- Modify: `packages/optio-api/src/adapters/fastify.ts` (SSE routes)
- Modify: `packages/optio-api/src/adapters/express.ts` (SSE routes)
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts` (SSE routes)

**Goal:** Pull the duplicated SSE/poller query parsing logic (`parseMetadataFilterQuery`, `detectLegacyMetadataParams`, `maxDepth` coercion) into one shared helper. Each adapter SSE route reduces to a single `parseSseOptions(rawQuery)` + `checkLegacyMetadataParams(rawQuery)` call.

- [ ] **Step 1: Read current SSE call sites**

Run:

```bash
grep -nE 'parseMetadataFilterQuery|detectLegacyMetadataParams|parseInt\(.*maxDepth' packages/optio-api/src/adapters/
```

Note where each is called and the surrounding shape.

- [ ] **Step 2: Write the failing test for `sse-options.ts`**

Create `packages/optio-api/src/__tests__/sse-options.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { parseSseOptions, checkLegacyMetadataParams, LegacyMetadataParamError } from '../sse-options.js';

describe('parseSseOptions', () => {
  it('parses metadataFilter from JSON string', () => {
    const result = parseSseOptions({ metadataFilter: '{"tag":["demo"]}' });
    expect(result.metadataFilter).toEqual({ tag: ['demo'] });
  });

  it('returns undefined metadataFilter when absent', () => {
    const result = parseSseOptions({});
    expect(result.metadataFilter).toBeUndefined();
  });

  it('coerces maxDepth from string to number', () => {
    const result = parseSseOptions({ maxDepth: '3' });
    expect(result.maxDepth).toBe(3);
  });

  it('returns undefined maxDepth when absent', () => {
    const result = parseSseOptions({});
    expect(result.maxDepth).toBeUndefined();
  });

  it('preserves database and prefix passthrough', () => {
    const result = parseSseOptions({ database: 'mydb', prefix: 'optio' });
    expect(result.database).toBe('mydb');
    expect(result.prefix).toBe('optio');
  });
});

describe('checkLegacyMetadataParams', () => {
  it('throws LegacyMetadataParamError on legacy keys', () => {
    expect(() => checkLegacyMetadataParams({ tag: 'demo' }))
      .toThrow(LegacyMetadataParamError);
  });

  it('does not throw when only valid keys are present', () => {
    expect(() => checkLegacyMetadataParams({ database: 'mydb', metadataFilter: '{}' }))
      .not.toThrow();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/sse-options.test.ts`

Expected: failure on import — `Cannot find module '../sse-options.js'`.

- [ ] **Step 4: Implement `sse-options.ts`**

Create `packages/optio-api/src/sse-options.ts`:

```ts
import {
  parseMetadataFilterQuery,
  detectLegacyMetadataParams,
} from './metadata-filter-query.js';
import type { ProcessMetadataFilter } from './types.js';

export class LegacyMetadataParamError extends Error {
  constructor(public readonly keys: string[]) {
    super(
      `Legacy metadata query parameter(s) detected: ${keys.join(', ')}. ` +
      `Use the metadataFilter JSON parameter instead.`,
    );
    this.name = 'LegacyMetadataParamError';
  }
}

export interface ParsedSseOptions {
  database?: string;
  prefix?: string;
  metadataFilter?: ProcessMetadataFilter;
  maxDepth?: number;
}

export function parseSseOptions(rawQuery: Record<string, unknown>): ParsedSseOptions {
  const out: ParsedSseOptions = {};

  if (typeof rawQuery.database === 'string') out.database = rawQuery.database;
  if (typeof rawQuery.prefix === 'string') out.prefix = rawQuery.prefix;

  const mfRaw = rawQuery.metadataFilter;
  if (typeof mfRaw === 'string' && mfRaw.length > 0) {
    const parsed = parseMetadataFilterQuery(mfRaw);
    if (parsed.ok) out.metadataFilter = parsed.value;
    else throw new Error(`Invalid metadataFilter: ${parsed.error}`);
  } else if (mfRaw && typeof mfRaw === 'object') {
    out.metadataFilter = mfRaw as ProcessMetadataFilter;
  }

  const maxDepthRaw = rawQuery.maxDepth;
  if (typeof maxDepthRaw === 'string' && maxDepthRaw.length > 0) {
    const n = parseInt(maxDepthRaw, 10);
    if (!Number.isFinite(n) || n < 0) throw new Error(`Invalid maxDepth: ${maxDepthRaw}`);
    out.maxDepth = n;
  } else if (typeof maxDepthRaw === 'number') {
    out.maxDepth = maxDepthRaw;
  }

  return out;
}

export function checkLegacyMetadataParams(rawQuery: Record<string, unknown>): void {
  const legacyKeys = detectLegacyMetadataParams(rawQuery);
  if (legacyKeys.length > 0) throw new LegacyMetadataParamError(legacyKeys);
}
```

(Adjust import paths and the `parseMetadataFilterQuery` return-shape destructuring to match the existing implementation in `metadata-filter-query.ts`. If the existing function returns the parsed value directly on success or throws, simplify accordingly.)

- [ ] **Step 5: Run sse-options tests**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/sse-options.test.ts`

Expected: 7 passed (or however many test cases). If any case fails because the existing `parseMetadataFilterQuery` API differs from the assumed shape, adjust the implementation in step 4 to match. The interface contract (parsed object out, throws on bad input) is what matters.

- [ ] **Step 6: Migrate fastify SSE routes to use the helper**

Find each SSE/poller route in `packages/optio-api/src/adapters/fastify.ts`. Each currently has inline shape like:

```ts
const legacyKeys = detectLegacyMetadataParams(request.query ?? {});
// ... if legacyKeys.length, reply 422
const parsed = parseMetadataFilterQuery((request.query as any)?.metadataFilter);
// ... handle parse error
const query = request.query as { database?: string; prefix?: string; maxDepth?: string };
const { db, prefix } = resolveDb(dbOpts, query);
const maxDepthNum = query.maxDepth !== undefined ? parseInt(query.maxDepth, 10) : undefined;
```

Replace with:

```ts
import { parseSseOptions, checkLegacyMetadataParams, LegacyMetadataParamError } from '../sse-options.js';

// inside the route handler:
try {
  checkLegacyMetadataParams(request.query as Record<string, unknown> ?? {});
} catch (e) {
  if (e instanceof LegacyMetadataParamError) {
    return reply.code(422).send({ message: e.message });
  }
  throw e;
}
const sseOpts = parseSseOptions(request.query as Record<string, unknown> ?? {});
// then use sseOpts.database / sseOpts.prefix / sseOpts.maxDepth / sseOpts.metadataFilter directly.
// Delete the inline `resolveDb` call — handlers now resolve via ctx.
// If the SSE route still calls a handler that uses (db, prefix, ...), change it to (ctx, sseOpts, ...).
```

The exact wiring depends on whether the SSE route calls a read handler (which is now ctx-based after A2) or assembles its own poller. Inspect the current code and adapt — the handler-call shape changes, the framework-specific SSE response writing (e.g. `reply.raw.writeHead`) stays.

- [ ] **Step 7: Migrate express SSE routes**

Same pattern in `packages/optio-api/src/adapters/express.ts`. Replace inline parsing with `checkLegacyMetadataParams` + `parseSseOptions`. Inline `resolveDb` for SSE routes goes away (the handler does it).

- [ ] **Step 8: Migrate nextjs-app SSE routes**

Same in `packages/optio-api/src/adapters/nextjs-app.ts`. nextjs-app uses `url.searchParams.get(...)` — first build a `rawQuery` object from the URL searchParams, then hand it to `parseSseOptions` / `checkLegacyMetadataParams`.

```ts
const url = new URL(req.url);
const rawQuery = Object.fromEntries(url.searchParams.entries());
checkLegacyMetadataParams(rawQuery);
const sseOpts = parseSseOptions(rawQuery);
```

- [ ] **Step 9: Run whole package build + tests**

Run: `pnpm -r build && pnpm -r test`

Expected: all green.

- [ ] **Step 10: Run interop**

Run: `make test-interop`

Expected: all phase-2 scenarios still pass.

- [ ] **Step 11: Commit**

```bash
git add packages/optio-api/src/sse-options.ts packages/optio-api/src/__tests__/sse-options.test.ts packages/optio-api/src/adapters/
git commit -m "refactor(optio-api): extract SSE/poller query parsing into sse-options.ts

Pulls duplicated SSE-route query parsing logic (parseMetadataFilterQuery,
detectLegacyMetadataParams, maxDepth coercion) out of the three adapters
that have SSE routes (fastify, express, nextjs-app) into a single
framework-agnostic helper.

parseSseOptions(rawQuery) returns {database, prefix, metadataFilter,
maxDepth}. checkLegacyMetadataParams(rawQuery) throws
LegacyMetadataParamError on legacy keys, which adapters catch and
turn into 422 responses.

Each SSE/poller route is now a one-liner for parsing plus the
framework-specific SSE response writer (reply.raw.writeHead /
res.write / Next.js ReadableStream).

Phase 3, commit A4 of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 6: A5 — Document binding layer rules in AGENTS.md and README.md

**Files:**
- Modify: `packages/optio-api/AGENTS.md`
- Modify: `packages/optio-api/README.md`
- Modify: `AGENTS.md` (root)

**Goal:** Codify the three-layer model (adapter / handler / context) and the binding rules forbidding framework-agnostic code in adapters or repeated code paths across adapters.

- [ ] **Step 1: Read existing optio-api AGENTS.md**

Run: `cat packages/optio-api/AGENTS.md`

Note current sections; we'll add the layer rules section, not replace.

- [ ] **Step 2: Add "Layer rules (binding)" section to optio-api AGENTS.md**

Append to `packages/optio-api/AGENTS.md`:

```markdown

## Layer rules (binding)

The `optio-api` package has three internal layers. Code lives in the layer that matches its responsibility. These rules are binding: PR review will reject violations.

### 1. Adapter layer — `packages/optio-api/src/adapters/{fastify,express,nextjs-app,nextjs-pages}.ts`

**Sole purpose:** integrate with the corresponding web framework.

**Allowed:**
- Framework-native request/response wrangling.
- Route registration via the framework's API.
- Framework lifecycle hooks (e.g. fastify `onClose`).
- Framework-specific SSE response writers (`reply.raw.writeHead` / `res.write` / Next.js `ReadableStream`).
- Body parser and middleware registration.

**Forbidden:** any code that would be repeated identically across the four adapters. This explicitly includes:
- `resolveDb(...)` calls — extract to handler via `OptioContext`.
- Default-value fallbacks (`x ?? N`) — defaults belong in the contract Zod schemas (e.g. `PaginationQuerySchema.default(20)`).
- `parseMetadataFilterQuery`, `detectLegacyMetadataParams`, `maxDepth` coercion — use `sse-options.ts`.
- Engine cache instantiation — use `createOptioContext`.
- Business logic, RPC mechanics, `ObjectId` coercion.

**Test before adding code to an adapter:** *"Would I write this same code in the other three adapters?"* If yes, extract.

**Test before adding a default:** check whether the contract layer (`@optio/contracts`, `processesContract`) can express it via Zod `.default(...)`. Defaults belong in the contract.

### 2. Handler layer — `packages/optio-api/src/handlers.ts` and collaborators

Framework-agnostic. Receives `OptioContext` + per-request data. Owns:
- Read-path Mongo queries.
- Write-path RPC calls (post-phase-3).
- Request → response shaping.
- Status-code mapping.

Collaborators: `process-id-resolver.ts`, `metadata-filter-query.ts`, `sse-options.ts`.

### 3. Context layer — `packages/optio-api/src/context.ts`

Owns durable per-app resources: `dbOpts`, `engineCache`, `redis`. Constructed once at adapter registration via `createOptioContext({ dbOpts, redis })`. Threaded into every handler call.
```

- [ ] **Step 3: Add layer-model paragraph to optio-api README.md**

Read `packages/optio-api/README.md`. Find an "Architecture" or "Internal structure" section, or add one near the top after the package description. Insert:

```markdown

## Internal structure

The package has three layers:

1. **Adapter layer** (`src/adapters/`): one file per supported web framework (`fastify`, `express`, `nextjs-app`, `nextjs-pages`). Owns only framework integration — route registration, request/response wrangling, lifecycle hooks. Framework-agnostic code is forbidden here; see `AGENTS.md` for the binding rules.
2. **Handler layer** (`src/handlers.ts` and collaborators): framework-agnostic functions taking `OptioContext` + per-request data. Owns read-path Mongo queries, write-path RPC calls, request → response shaping.
3. **Context layer** (`src/context.ts`): owns durable per-app resources (`dbOpts`, `engineCache`, `redis`). Constructed once at adapter registration via `createOptioContext`.

When extending the package, the test for placing code in an adapter is: *"Would I write this same code in the other three adapters?"* If yes, the code belongs in the handler or context layer, not the adapter.
```

- [ ] **Step 4: Add cross-reference to root AGENTS.md**

Read root `AGENTS.md`. Find the section that discusses the API package (typically near the architecture overview). Add a one-liner pointing to optio-api's layer rules:

```markdown
- The `optio-api` package has binding internal layer rules (adapter / handler / context); see `packages/optio-api/AGENTS.md`.
```

Place it where it fits the existing prose.

- [ ] **Step 5: Verify build (no code changes; should pass trivially)**

Run: `pnpm -r build && pnpm -r test`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-api/AGENTS.md packages/optio-api/README.md AGENTS.md
git commit -m "docs(optio-api): document binding layer rules

Adds 'Layer rules (binding)' section to packages/optio-api/AGENTS.md
codifying the three-layer model (adapter / handler / context) that
phase-3's Stage A established. Spells out the binding constraints
on adapter code: no framework-agnostic logic, no defaults that
belong in the contract layer, no parallel-maintenance code paths
across the four adapters.

Includes the 'would I write this in the other three adapters?'
test for adapter additions.

README.md gets a short architecture paragraph cross-linking to the
binding rules. Root AGENTS.md gets a one-liner pointer.

Phase 3, commit A5 of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 7: 3a — Launch RPC swap

**Files:**
- Modify: `packages/optio-contracts/src/api-to-frontend.ts` (or `contract.ts` if not yet renamed)
- Modify: `packages/optio-api/src/handlers.ts` (launch only)
- Modify: `packages/optio-api/src/__tests__/handlers.test.ts` (launch tests)
- Modify: `packages/optio-api/src/adapters/__tests__/*.test.ts` (launch route assertions)
- Create: `packages/optio-demo/interop/run-http.ts`
- Modify: `packages/optio-demo/run-interop.sh` (spawn fastify, run HTTP scenarios)

**Goal:** Swap `launchProcess` from `publishLaunch(...)` to `engine.launch({...})`. 404/409 body becomes `{reason, message}` typed via `LaunchErrorBody` in `api-to-frontend.ts`. New `LaunchCommandResult`. New `LAUNCH_STATUS` + launch slice of `MESSAGES`. HTTP-roundtrip interop scenarios added.

- [ ] **Step 1: Update `api-to-frontend.ts` (or `contract.ts`) — launch responses**

Identify the contract source. Per phase-1: `packages/optio-contracts/src/api-to-frontend.ts` (renamed from `contract.ts`). If still at `contract.ts`, that's fine; the same edits apply.

Add at the top:

```ts
import { LaunchFailureReason } from './engine-failure-reasons.js';
```

(`engine-failure-reasons.ts` was added in phase 1; `optio-contracts/src/index.ts` re-exports.)

Add near the existing `ErrorSchema`:

```ts
const LaunchErrorBody = z.object({
  reason: LaunchFailureReason,
  message: z.string(),
});
```

Find `processesContract.launch.responses`. Replace with:

```ts
launch: {
  method: 'POST',
  path: '/processes/:id/launch',
  pathParams: z.object({ id: ProcessIdParamSchema }),
  query: InstanceQuerySchema,
  body: z.object({ resume: z.boolean().optional() }),
  responses: {
    200: ProcessSchema,
    404: LaunchErrorBody,
    409: LaunchErrorBody,
  },
  summary: 'Launch a process',
},
```

(Match the surrounding indentation and any `summary` / `body` shape already present.)

- [ ] **Step 2: Update `handlers.ts` — launch slice**

In `packages/optio-api/src/handlers.ts`, add at the top after existing imports:

```ts
import { LaunchFailureReason } from 'optio-contracts';
import type { ManagedEngineClient } from './engine-cache.js';
```

Add the `LAUNCH_STATUS`, `MESSAGES` (launch entries), and `LaunchCommandResult` near the existing `CommandResult`:

```ts
export type LaunchCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: z.infer<typeof LaunchFailureReason>; message: string } };

const LAUNCH_STATUS: Record<z.infer<typeof LaunchFailureReason>, 404 | 409> = {
  'not-found': 404,
  'not-launchable': 409,
  'no-resume-support': 409,
  'launch-blocked': 409,
};

const MESSAGES: Record<string, string> = {
  'not-found': 'Process not found',
  'not-launchable': 'Process is not in a launchable state',
  'no-resume-support': 'This task does not support resume',
  'launch-blocked': 'Launches matching this filter are currently blocked',
};

function launchFail(reason: z.infer<typeof LaunchFailureReason>): LaunchCommandResult {
  return { status: LAUNCH_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
}
```

Replace the existing `launchProcess` body with:

```ts
export async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume: boolean = false,
): Promise<LaunchCommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);

  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return launchFail('not-found');
  if (!LAUNCHABLE_STATES.includes(proc.status.state)) return launchFail('not-launchable');
  if (resume && !proc.supportsResume) return launchFail('no-resume-support');

  const result = await engine.launch({ processId: proc.processId, resume });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return launchFail(result.reason);
}
```

(`z` may need to be imported if not already; if Zod `.infer` is used, also `import { z } from 'zod';`.)

- [ ] **Step 3: Update launch tests**

In `packages/optio-api/src/__tests__/handlers.test.ts`, find the launch test block. Replace the redis-XADD assertions with EngineClient-mock assertions:

```ts
import { vi } from 'vitest';
// ...
function makeMockEngine(launchResult: any) {
  return { launch: vi.fn().mockResolvedValue(launchResult) };
}
function makeCtxWithMockEngine(db: Db, mockEngine: any): OptioContext {
  return {
    dbOpts: { db },
    redis: {} as any,
    engineCache: {
      get: () => mockEngine,
      closeAll: async () => {},
    },
  } as any;
}

describe('launchProcess — RPC path', () => {
  it('200 on success: engine returns ok with process', async () => {
    const proc = await insertProc({ status: { state: 'idle' }, supportsResume: true });
    const engine = makeMockEngine({ ok: true, process: { ...proc, _id: proc._id.toString(), rootId: proc.rootId.toString() } });
    const ctx = makeCtxWithMockEngine(db, engine);
    const result = await launchProcess(ctx, { prefix: PREFIX }, proc.processId);
    expect(result.status).toBe(200);
    expect(engine.launch).toHaveBeenCalledWith({ processId: proc.processId, resume: false });
  });

  it('404 not-found from pre-check', async () => {
    const engine = makeMockEngine({ ok: false, reason: 'not-found' });
    const ctx = makeCtxWithMockEngine(db, engine);
    const result = await launchProcess(ctx, { prefix: PREFIX }, 'bogus-id');
    expect(result.status).toBe(404);
    expect(result.body).toEqual({ reason: 'not-found', message: 'Process not found' });
    expect(engine.launch).not.toHaveBeenCalled();   // pre-check rejects
  });

  it('409 not-launchable when state is running (pre-check)', async () => {
    const proc = await insertProc({ status: { state: 'running' } });
    const engine = makeMockEngine(null);
    const ctx = makeCtxWithMockEngine(db, engine);
    const result = await launchProcess(ctx, { prefix: PREFIX }, proc.processId);
    expect(result.status).toBe(409);
    expect(result.body).toEqual({ reason: 'not-launchable', message: 'Process is not in a launchable state' });
  });

  it('409 launch-blocked when engine returns the reason', async () => {
    const proc = await insertProc({ status: { state: 'idle' } });
    const engine = makeMockEngine({ ok: false, reason: 'launch-blocked' });
    const ctx = makeCtxWithMockEngine(db, engine);
    const result = await launchProcess(ctx, { prefix: PREFIX }, proc.processId);
    expect(result.status).toBe(409);
    expect(result.body).toEqual({ reason: 'launch-blocked', message: 'Launches matching this filter are currently blocked' });
  });

  it('409 no-resume-support when resume=true and supportsResume=false (pre-check)', async () => {
    const proc = await insertProc({ status: { state: 'idle' }, supportsResume: false });
    const engine = makeMockEngine(null);
    const ctx = makeCtxWithMockEngine(db, engine);
    const result = await launchProcess(ctx, { prefix: PREFIX }, proc.processId, true);
    expect(result.status).toBe(409);
    expect(result.body).toEqual({ reason: 'no-resume-support', message: 'This task does not support resume' });
  });
});
```

Adjust the `insertProc` helper or use whatever fixture pattern the existing test file uses. The key shape: `engine.launch` is asserted on success/launch-blocked paths; pre-check failures return without calling it.

Old `publishX` assertions for launch (in the same test block) get deleted.

- [ ] **Step 4: Run handlers tests for launch**

Run: `pnpm --filter optio-api exec vitest run src/__tests__/handlers.test.ts -t launchProcess`

Expected: all launch tests green; engine mock called with correct args; reason → status mapping verified.

- [ ] **Step 5: Update adapter integration tests for launch**

Open each `packages/optio-api/src/adapters/__tests__/*.test.ts`. Find launch-route tests. Update assertions for the new body shape:

```ts
// before:
expect(response.body).toEqual({ message: 'Process not found' });
// after:
expect(response.body).toEqual({ reason: 'not-found', message: 'Process not found' });
```

For tests that verified `publishLaunch` was called with specific args via redis stream — replace with stub `EngineClient`:

```ts
const engineStub = { launch: vi.fn().mockResolvedValue({ ok: true, process: ... }), ... };
// inject via createEngineCache spy or by passing a custom redis that the adapter cache can build off of
```

Concrete pattern: the simplest path is to spy on `cache.get` (now `ctx.engineCache.get`) and return a stubbed engine. Each adapter test file already has a setup helper for `registerOptioApi` — extend it to inject a stub engine cache.

- [ ] **Step 6: Run adapter tests**

Run: `pnpm --filter optio-api exec vitest run src/adapters/__tests__/`

Expected: all green.

- [ ] **Step 7: Create `interop/run-http.ts` with launch scenarios**

Create `packages/optio-demo/interop/run-http.ts`:

```ts
/**
 * Stage B HTTP-roundtrip scenarios. Hits a fastify server registered
 * with optio-api against the same redis + mongo as the engine. Verifies
 * the full HTTP -> handler -> engine cache -> RPC -> engine chain.
 */
import IORedis from 'ioredis';
import { MongoClient } from 'mongodb';
import Fastify from 'fastify';
import { registerOptioApi } from 'optio-api/fastify';

const REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';
const MONGODB_URL = process.env.MONGODB_URL ?? 'mongodb://localhost:27017/optio-demo';
const HTTP_PORT = parseInt(process.env.HTTP_PORT ?? '0', 10);
const PROC = 'opencode-demo';
const SCENARIO_TIMEOUT_MS = 10_000;

const redis = new IORedis(REDIS_URL);
const mongoClient = new MongoClient(MONGODB_URL);
let baseUrl = '';
let exitCode = 0;

function fail(name: string, msg: string) {
  console.error(`✗ ${name}: ${msg}`);
  exitCode = 1;
}
function ok(name: string, info?: string) {
  console.log(`✓ ${name}${info ? ` (${info})` : ''}`);
}

async function withTimeout<T>(name: string, fn: () => Promise<T>): Promise<T> {
  const start = Date.now();
  console.log(`[scenario] ${name} started`);
  return await Promise.race<T>([
    fn().then((v) => {
      console.log(`[scenario] ${name} ok (${Date.now() - start}ms)`);
      return v;
    }),
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`[scenario] ${name} timed out after ${SCENARIO_TIMEOUT_MS}ms`)), SCENARIO_TIMEOUT_MS),
    ),
  ]);
}

setTimeout(() => {
  console.error('[scenario] FATAL: 60s top-level timeout, exiting 15');
  process.exit(15);
}, 60_000).unref();

async function http(method: string, path: string, body?: unknown) {
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  return { status: res.status, body: json };
}

async function dismissIfTerminal() {
  await http('POST', `/api/processes/${PROC}/dismiss`).catch(() => null);
}

async function main() {
  await mongoClient.connect();
  const db = mongoClient.db('optio-demo');
  const app = Fastify();
  await registerOptioApi(app, { db, redis, prefix: 'optio' });
  await app.listen({ port: HTTP_PORT, host: '127.0.0.1' });
  const address = app.server.address();
  if (!address || typeof address !== 'object') throw new Error('fastify did not bind');
  baseUrl = `http://127.0.0.1:${address.port}/api`;
  console.log(`[http] listening on ${baseUrl}`);

  try {
    await dismissIfTerminal();

    // 1. Launch success.
    await withTimeout('http-launch-success', async () => {
      const r = await http('POST', `/processes/${PROC}/launch`, {});
      if (r.status !== 200) return fail('http-launch-success', `expected 200, got ${r.status} ${JSON.stringify(r.body)}`);
      if (typeof r.body?._id !== 'string') return fail('http-launch-success', `body missing _id: ${JSON.stringify(r.body)}`);
      ok('http-launch-success', `state=${r.body.status?.state}`);
    });

    // 2. Launch on running process -> 409 not-launchable.
    await withTimeout('http-launch-not-launchable', async () => {
      const r = await http('POST', `/processes/${PROC}/launch`, {});
      if (r.status !== 409) return fail('http-launch-not-launchable', `expected 409, got ${r.status}`);
      if (r.body?.reason !== 'not-launchable')
        return fail('http-launch-not-launchable', `expected reason 'not-launchable', got ${r.body?.reason}`);
      ok('http-launch-not-launchable');
    });

    // 3. Launch nonexistent processId -> 404 not-found.
    await withTimeout('http-launch-not-found', async () => {
      const r = await http('POST', `/processes/this-id-does-not-exist/launch`, {});
      if (r.status !== 404) return fail('http-launch-not-found', `expected 404, got ${r.status}`);
      if (r.body?.reason !== 'not-found')
        return fail('http-launch-not-found', `expected reason 'not-found', got ${r.body?.reason}`);
      ok('http-launch-not-found');
    });

    // 4. Cancel -> dismiss back to baseline (uses still-legacy redis-stream path until 3b/3c land).
    await dismissIfTerminal();
    await withTimeout('http-launch-baseline-reset', async () => {
      const r = await http('POST', `/processes/${PROC}/launch`, {});
      if (r.status !== 200) return fail('http-launch-baseline-reset', `expected 200, got ${r.status}`);
      ok('http-launch-baseline-reset');
    });

    // Note: launch-blocked + no-resume-support scenarios cover narrower ground; add in follow-up
    // if optio-demo task definitions include a no-resume-support task. For 3a, the four scenarios
    // above prove the HTTP -> RPC chain.
  } finally {
    await app.close();
    await redis.quit();
    await mongoClient.close();
  }
}

main()
  .then(() => process.exit(exitCode))
  .catch((e) => {
    console.error('[scenario] FATAL:', e);
    process.exit(15);
  });
```

Add `fastify` and `mongodb` and `optio-api` to `packages/optio-demo/interop/package.json` dependencies (if not already present). Adjust the package.json:

```json
{
  "name": "optio-demo-interop",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "dependencies": {
    "@clamator/over-redis": "*",
    "ioredis": "*",
    "mongodb": "*",
    "fastify": "*",
    "optio-api": "workspace:*",
    "optio-contracts": "workspace:*"
  },
  "devDependencies": {
    "tsx": "*",
    "typescript": "*",
    "@types/node": "*"
  }
}
```

Run `pnpm install` after editing.

- [ ] **Step 8: Update `run-interop.sh` to spawn fastify and run http scenarios**

Edit `packages/optio-demo/run-interop.sh`. After the existing `phase running-scenarios` block (which runs `run.ts`), add a new fastify-up phase + http scenarios run:

```bash
# Fastify is started inside run-http.ts, not as a separate subprocess —
# the scenario script binds, runs, and shuts down. So no separate phase=fastify-up
# is needed; the scenario timeout (SCENARIO_TIMEOUT) covers startup.

phase running-scenarios-http
REDIS_URL="redis://localhost:${REDIS_PORT}" \
  MONGODB_URL="mongodb://localhost:${MONGO_PORT}/optio-demo" \
  timeout "$SCENARIO_TIMEOUT" pnpm --dir "$DEMO_DIR/interop" exec tsx run-http.ts
HTTP_EXIT=$?
if (( HTTP_EXIT != 0 )); then
  if (( HTTP_EXIT == 124 )); then
    die 15 "http scenario runner timed out after ${SCENARIO_TIMEOUT}s"
  fi
  die 15 "http scenario runner exited $HTTP_EXIT"
fi
echo "[interop] http scenarios passed"
```

Place this right before the final `echo "[interop] scenarios passed"; exit 0` block.

- [ ] **Step 9: Run interop end-to-end**

Run: `make test-interop`

Expected: phase markers go through `running-scenarios` → `running-scenarios-http` → `cleanup`. All direct-clamator scenarios (existing) pass. All HTTP scenarios pass. Total runtime ≤90s warm cache.

- [ ] **Step 10: Run whole package tests**

Run: `pnpm -r build && pnpm -r test`

Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add packages/optio-contracts/src/api-to-frontend.ts packages/optio-contracts/src/contract.ts packages/optio-api/src/handlers.ts packages/optio-api/src/__tests__/handlers.test.ts packages/optio-api/src/adapters/__tests__/ packages/optio-demo/interop/run-http.ts packages/optio-demo/interop/package.json packages/optio-demo/run-interop.sh pnpm-lock.yaml
git commit -m "feat(optio-api): launch HTTP path swaps to clamator RPC

handlers.launchProcess now calls engine.launch({processId, resume})
via OptioContext.engineCache instead of publishLaunch on the legacy
redis stream. The 404/409 response body becomes {reason, message}
typed via LaunchErrorBody in api-to-frontend.ts. Per-command typed
result LaunchCommandResult lands in handlers.ts alongside LAUNCH_STATUS
and the launch slice of MESSAGES.

Pre-check stays as defense-in-depth: handler still queries Mongo
for the doc, validates state via LAUNCHABLE_STATES, and validates
supportsResume. Phase 4 removes those.

Adds packages/optio-demo/interop/run-http.ts with HTTP-roundtrip
scenarios for launch (200, 404 not-found, 409 not-launchable). Adds
fastify dep to interop subpackage. run-interop.sh runs the new
HTTP scenarios after the existing direct-clamator scenarios.

Phase 3, commit 3a of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 8: 3b — Cancel RPC swap

**Files:**
- Modify: `packages/optio-contracts/src/api-to-frontend.ts` (or `contract.ts`)
- Modify: `packages/optio-api/src/handlers.ts` (cancel)
- Modify: `packages/optio-api/src/__tests__/handlers.test.ts` (cancel tests)
- Modify: adapter test files (cancel route assertions)
- Modify: `packages/optio-demo/interop/run-http.ts` (add cancel scenarios)

**Goal:** Same pattern as 3a, applied to cancel.

- [ ] **Step 1: Add `CancelErrorBody` to api-to-frontend.ts**

```ts
import { CancelFailureReason } from './engine-failure-reasons.js';
// ... near other body schemas:
const CancelErrorBody = z.object({
  reason: CancelFailureReason,
  message: z.string(),
});
```

Update `processesContract.cancel.responses`:

```ts
responses: {
  200: ProcessSchema,
  404: CancelErrorBody,
  409: CancelErrorBody,
},
```

- [ ] **Step 2: Update `handlers.ts` — cancel slice**

Add to existing `handlers.ts`:

```ts
import { CancelFailureReason } from 'optio-contracts';

export type CancelCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: z.infer<typeof CancelFailureReason>; message: string } };

const CANCEL_STATUS: Record<z.infer<typeof CancelFailureReason>, 404 | 409> = {
  'not-found': 404,
  'not-cancellable': 409,
};

// extend MESSAGES:
const MESSAGES: Record<string, string> = {
  // ... existing launch entries
  'not-cancellable': 'Process is not cancellable in its current state',
};

function cancelFail(reason: z.infer<typeof CancelFailureReason>): CancelCommandResult {
  return { status: CANCEL_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
}
```

Replace `cancelProcess`:

```ts
export async function cancelProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CancelCommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);

  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return cancelFail('not-found');
  if (!proc.cancellable) return cancelFail('not-cancellable');
  if (!CANCELLABLE_STATES.includes(proc.status.state)) return cancelFail('not-cancellable');

  const result = await engine.cancel({ processId: proc.processId });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return cancelFail(result.reason);
}
```

- [ ] **Step 3: Update cancel tests in handlers.test.ts**

Same pattern as 3a launch tests, applied to cancel:
- 200 success: engine returns ok
- 404 not-found from pre-check
- 409 not-cancellable from pre-check (cancellable=false or state)
- 409 not-cancellable from engine

Old redis-XADD assertions for cancel deleted.

- [ ] **Step 4: Update adapter integration tests for cancel**

Same shape as 3a. Cancel route body shape becomes `{reason, message}`.

- [ ] **Step 5: Add cancel scenarios to run-http.ts**

Append to `run-http.ts` (within `main()`, after launch scenarios):

```ts
// 5. Cancel success: launch then cancel.
await withTimeout('http-cancel-success', async () => {
  await dismissIfTerminal();
  await http('POST', `/processes/${PROC}/launch`, {});
  // wait briefly for state to settle if engine response is async
  await new Promise(r => setTimeout(r, 100));
  const r = await http('POST', `/processes/${PROC}/cancel`);
  if (r.status !== 200) return fail('http-cancel-success', `expected 200, got ${r.status} ${JSON.stringify(r.body)}`);
  ok('http-cancel-success', `state=${r.body.status?.state}`);
});

// 6. Cancel idle proc -> 409 not-cancellable.
await withTimeout('http-cancel-not-cancellable', async () => {
  await dismissIfTerminal();
  const r = await http('POST', `/processes/${PROC}/cancel`);
  if (r.status !== 409) return fail('http-cancel-not-cancellable', `expected 409, got ${r.status}`);
  if (r.body?.reason !== 'not-cancellable')
    return fail('http-cancel-not-cancellable', `expected reason 'not-cancellable', got ${r.body?.reason}`);
  ok('http-cancel-not-cancellable');
});

// 7. Cancel nonexistent -> 404 not-found.
await withTimeout('http-cancel-not-found', async () => {
  const r = await http('POST', `/processes/bogus-id/cancel`);
  if (r.status !== 404) return fail('http-cancel-not-found', `expected 404, got ${r.status}`);
  if (r.body?.reason !== 'not-found') return fail('http-cancel-not-found', `expected reason 'not-found'`);
  ok('http-cancel-not-found');
});
```

- [ ] **Step 6: Run handlers tests, adapter tests, interop**

Commands:
```bash
pnpm -r build && pnpm -r test
make test-interop
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-contracts/ packages/optio-api/src/handlers.ts packages/optio-api/src/__tests__/handlers.test.ts packages/optio-api/src/adapters/__tests__/ packages/optio-demo/interop/run-http.ts
git commit -m "feat(optio-api): cancel HTTP path swaps to clamator RPC

handlers.cancelProcess now calls engine.cancel({processId}) instead
of publishCancel on the legacy redis stream. 404/409 body becomes
{reason, message} typed via CancelErrorBody. CancelCommandResult
lands in handlers.ts alongside CANCEL_STATUS and the cancel slice
of MESSAGES.

Pre-check stays as defense-in-depth (cancellable + state check).
Phase 4 removes both.

run-http.ts extended with HTTP-roundtrip cancel scenarios.

Phase 3, commit 3b of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 9: 3c — Dismiss RPC swap

**Files:** Same pattern as 3a/3b applied to dismiss.

- [ ] **Step 1: `DismissErrorBody` in api-to-frontend.ts**

```ts
import { DismissFailureReason } from './engine-failure-reasons.js';
const DismissErrorBody = z.object({
  reason: DismissFailureReason,
  message: z.string(),
});
```

Update `processesContract.dismiss.responses`:

```ts
responses: {
  200: ProcessSchema,
  404: DismissErrorBody,
  409: DismissErrorBody,
},
```

- [ ] **Step 2: `handlers.ts` — dismiss slice**

```ts
import { DismissFailureReason } from 'optio-contracts';

export type DismissCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: z.infer<typeof DismissFailureReason>; message: string } };

const DISMISS_STATUS: Record<z.infer<typeof DismissFailureReason>, 404 | 409> = {
  'not-found': 404,
  'not-dismissable': 409,
};

const MESSAGES: Record<string, string> = {
  // ... existing
  'not-dismissable': 'Process is not in a dismissable state',
};

function dismissFail(reason: z.infer<typeof DismissFailureReason>): DismissCommandResult {
  return { status: DISMISS_STATUS[reason], body: { reason, message: MESSAGES[reason] } };
}

export async function dismissProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<DismissCommandResult> {
  const { db, database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);

  const proc = await findProcessByEitherId(col(db, prefix), id);
  if (!proc) return dismissFail('not-found');
  if (!END_STATES.includes(proc.status.state)) return dismissFail('not-dismissable');

  const result = await engine.dismiss({ processId: proc.processId });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return dismissFail(result.reason);
}
```

- [ ] **Step 3: Dismiss tests, adapter tests, run-http.ts dismiss scenarios**

Same pattern as 3b.

run-http.ts dismiss scenarios:

```ts
// dismiss success: cancel proc to a terminal state, then dismiss.
await withTimeout('http-dismiss-success', async () => {
  await dismissIfTerminal();
  await http('POST', `/processes/${PROC}/launch`, {});
  await new Promise(r => setTimeout(r, 100));
  await http('POST', `/processes/${PROC}/cancel`);
  await new Promise(r => setTimeout(r, 200));
  const r = await http('POST', `/processes/${PROC}/dismiss`);
  if (r.status !== 200) return fail('http-dismiss-success', `expected 200, got ${r.status}`);
  ok('http-dismiss-success', `state=${r.body.status?.state}`);
});

// dismiss running proc -> 409 not-dismissable.
await withTimeout('http-dismiss-not-dismissable', async () => {
  await dismissIfTerminal();
  await http('POST', `/processes/${PROC}/launch`, {});
  await new Promise(r => setTimeout(r, 100));
  const r = await http('POST', `/processes/${PROC}/dismiss`);
  if (r.status !== 409) return fail('http-dismiss-not-dismissable', `expected 409, got ${r.status}`);
  if (r.body?.reason !== 'not-dismissable') return fail('http-dismiss-not-dismissable', `expected 'not-dismissable'`);
  ok('http-dismiss-not-dismissable');
});

// dismiss nonexistent -> 404.
await withTimeout('http-dismiss-not-found', async () => {
  const r = await http('POST', `/processes/bogus-id/dismiss`);
  if (r.status !== 404) return fail('http-dismiss-not-found', `expected 404`);
  ok('http-dismiss-not-found');
});
```

- [ ] **Step 4: Run tests**

```bash
pnpm -r build && pnpm -r test
make test-interop
```

- [ ] **Step 5: Commit**

```bash
git add packages/optio-contracts/ packages/optio-api/src/handlers.ts packages/optio-api/src/__tests__/handlers.test.ts packages/optio-api/src/adapters/__tests__/ packages/optio-demo/interop/run-http.ts
git commit -m "feat(optio-api): dismiss HTTP path swaps to clamator RPC

handlers.dismissProcess now calls engine.dismiss({processId}) instead
of publishDismiss. 404/409 body becomes {reason, message} typed via
DismissErrorBody. DismissCommandResult, DISMISS_STATUS, dismiss slice
of MESSAGES added.

Pre-check stays. Phase 4 removes.

run-http.ts extended with HTTP-roundtrip dismiss scenarios.

Phase 3, commit 3c of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Task 10: 3d — Resync RPC swap + delete `publisher.ts`

**Files:**
- Modify: `packages/optio-contracts/src/contract.ts` (resync 200 → 202)
- Modify: `packages/optio-api/src/handlers.ts` (resync)
- Modify: `packages/optio-api/src/__tests__/handlers.test.ts` (resync tests)
- Modify: adapters' resync route assertions (status 202)
- Modify: `packages/optio-demo/interop/run-http.ts` (resync scenario)
- Delete: `packages/optio-api/src/publisher.ts`
- Delete: `packages/optio-api/src/__tests__/publisher.test.ts`
- Modify: `packages/optio-api/src/index.ts` (remove publishX exports)

**Goal:** Resync becomes a clamator notification; HTTP status 200 → 202. Delete legacy publisher (no callers remain).

- [ ] **Step 1: Update contract resync response status**

In `packages/optio-contracts/src/contract.ts` (line ~130-138), find:

```ts
resync: {
  method: 'POST',
  path: '/processes/resync',
  query: InstanceQuerySchema,
  body: z.object({ clean: z.boolean().optional(), metadataFilter: ProcessMetadataFilterSchema.optional() }),
  responses: {
    200: z.object({ message: z.string() }),
  },
  summary: 'Trigger resync',
},
```

Replace `200:` with `202:`:

```ts
responses: {
  202: z.object({ message: z.string() }),
},
```

- [ ] **Step 2: Update `resyncProcesses` in handlers.ts**

```ts
export async function resyncProcesses(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<{ message: string }> {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);
  await engine.resync({ clean, metadataFilter });   // notification, returns void
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
```

- [ ] **Step 3: Update each adapter's resync route to return status 202**

In each adapter's resync route:

```ts
resync: async ({ body, query }) => {
  const result = await handlers.resyncProcesses(ctx, query, body.clean, body.metadataFilter);
  return { status: 202 as const, body: result };
},
```

(The status literal narrows to 202.)

- [ ] **Step 4: Update resync tests in handlers.test.ts**

Replace assertions that check `redis.xadd` was called with assertions that check `engine.resync` was called:

```ts
describe('resyncProcesses — RPC notification', () => {
  it('calls engine.resync with the clean flag and metadataFilter', async () => {
    const engine = { resync: vi.fn().mockResolvedValue(undefined) };
    const ctx = makeCtxWithMockEngine(db, engine);
    const result = await resyncProcesses(ctx, { prefix: PREFIX }, true, { tag: ['demo'] });
    expect(engine.resync).toHaveBeenCalledWith({ clean: true, metadataFilter: { tag: ['demo'] } });
    expect(result).toEqual({ message: 'Nuke and resync requested' });
  });
  it('passes clean=false when omitted', async () => {
    const engine = { resync: vi.fn().mockResolvedValue(undefined) };
    const ctx = makeCtxWithMockEngine(db, engine);
    const result = await resyncProcesses(ctx, { prefix: PREFIX });
    expect(engine.resync).toHaveBeenCalledWith({ clean: false, metadataFilter: undefined });
    expect(result).toEqual({ message: 'Resync requested' });
  });
});
```

Old redis-XADD resync tests deleted.

- [ ] **Step 5: Update adapter integration tests for resync (status 202)**

Each adapter test for the resync route: change expected status from 200 to 202.

- [ ] **Step 6: Add resync HTTP scenario in run-http.ts**

```ts
// resync notification.
await withTimeout('http-resync', async () => {
  const r = await http('POST', `/processes/resync`, {});
  if (r.status !== 202) return fail('http-resync', `expected 202, got ${r.status}`);
  if (r.body?.message !== 'Resync requested') return fail('http-resync', `unexpected body ${JSON.stringify(r.body)}`);
  ok('http-resync');
});

await withTimeout('http-resync-clean', async () => {
  const r = await http('POST', `/processes/resync`, { clean: true });
  if (r.status !== 202) return fail('http-resync-clean', `expected 202, got ${r.status}`);
  if (r.body?.message !== 'Nuke and resync requested') return fail('http-resync-clean', `unexpected body`);
  ok('http-resync-clean');
});
```

- [ ] **Step 7: Delete publisher.ts and its tests**

```bash
git rm packages/optio-api/src/publisher.ts
git rm packages/optio-api/src/__tests__/publisher.test.ts
```

- [ ] **Step 8: Remove publisher exports from index.ts**

In `packages/optio-api/src/index.ts`, remove the `export { publishLaunch, publishCancel, publishDismiss, publishResync, getStreamName } from './publisher.js';` line(s).

Also remove the `publishX` imports from `handlers.ts` (they're unreferenced after 3a/b/c/d landed).

- [ ] **Step 9: Verify cleanup**

Run:

```bash
grep -rn 'publishLaunch\|publishCancel\|publishDismiss\|publishResync\|publisher\.' packages/optio-api/src/
```

Expected: no matches (or only in commented-out lines / type-only imports already absent).

```bash
grep -n '200' packages/optio-contracts/src/contract.ts | grep -i resync
```

Expected: no matches.

- [ ] **Step 10: Run full build, tests, interop**

```bash
pnpm -r build && pnpm -r test
make test-interop
```

Expected: all green. HTTP-roundtrip resync returns 202.

- [ ] **Step 11: Commit**

```bash
git add packages/optio-contracts/ packages/optio-api/src/handlers.ts packages/optio-api/src/__tests__/handlers.test.ts packages/optio-api/src/adapters/ packages/optio-api/src/index.ts packages/optio-demo/interop/run-http.ts
git commit -m "feat(optio-api): resync swaps to clamator notification + 202; delete publisher.ts

handlers.resyncProcesses now calls engine.resync({clean,
metadataFilter}) as a clamator notification. HTTP status flips
200 -> 202 (semantically: notification, async work). Frontend
(useProcessActions.ts) is unaffected — tanstack mutations are
status-code-agnostic on success.

Deletes packages/optio-api/src/publisher.ts and its test file —
zero callers remain after 3a/3b/3c. Removes publishLaunch /
publishCancel / publishDismiss / publishResync exports from
index.ts.

After 3d the API ingress for all four commands is clamator RPC.
The engine still consumes the legacy \${prefix}:commands stream
during phases 3-4 (consumer.py removed in phase 5).

run-http.ts extended with HTTP-roundtrip resync scenarios (clean
and non-clean paths, both expecting 202).

Phase 3, commit 3d of docs/2026-05-08-engine-rpc-migration-phase-3-design.md."
```

---

## Final verification

After all 10 commits land, run from the worktree root:

- [ ] `pnpm -r build` — green.
- [ ] `pnpm -r test` — green; all test suites pass; reason-to-status mapping covered for launch/cancel/dismiss; engine mock assertions in place.
- [ ] `make test` — green (Python suite unchanged from phase 2 baseline).
- [ ] `make test-interop` — exits 0 in <90s. Phase markers progress through `docker-pre-flight` → `redis-up` → `mongo-up` → `engine-up` → `running-scenarios` → `running-scenarios-http` → `cleanup`. All direct-clamator scenarios + all HTTP-roundtrip scenarios pass.
- [ ] `grep -rn 'publishLaunch\|publishCancel\|publishDismiss\|publishResync\|publisher\.' packages/optio-api/src/` → no matches.
- [ ] `grep -nE '\?\? *(20|25)' packages/optio-api/src/adapters/` → no query-default matches.
- [ ] `grep -n 'body\.clean *??' packages/optio-api/src/adapters/` → no matches.
- [ ] `redis-cli xrange "${db}/${prefix}:cmds:engine" -` after `make test-interop` shows entries (clamator RPC ingress active).
- [ ] `redis-cli xrange "${db}/${prefix}:commands"` shows no entries originating from API code (the legacy stream is still consumed by the engine through phase 5, but no phase-3-or-later API code writes to it).
- [ ] `git log --oneline main..HEAD` shows exactly 10 phase-3 commits in order: A0, A1, A2, A3, A4, A5, 3a, 3b, 3c, 3d.

Open a PR for review against `main`. Use `/ultrareview` if available before merge.
