# OptioTransports Layering Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Working directory:** All work happens in the git worktree at `/home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports/` on branch `csillag/optio-transports`. Subagents must operate inside this directory. Outputs landing in `/home/csillag/deai/optio/` (the main checkout) are wrong location — flag immediately.

**Goal:** Separate optio-api's engine-access concern from its HTTP-binding concern, and replace the engine-specific `EngineCache` with a contract-agnostic `OptioTransports` cache that any clamator contract can wrap. Plus full optio-namespace rename to disambiguate from other "engine" contracts (Excavator's port begins after this lands).

**Architecture:** Four commits. (1) optio-namespace rename across the contract package and all consumers — file rename, var rename, wire contract-name rename, regenerated codegen, all consumer call-site updates. (2) layer separation: replace `engine-cache.ts` with `optio-transports.ts` (caches `RpcClient` not `OptioEngineClient`), refactor `OptioContext` to hold `transports` + `closeAll`, add `resolveOptioEngine` helper, drop `OptioApiHandle` from adapters (return `OptioContext` directly). (3) interop scenarios switch to `createOptioTransports`. (4) docs.

**Tech Stack:** TypeScript (`packages/optio-api`, `packages/optio-contracts`, `packages/optio-demo/interop`), Python 3.12 (`packages/optio-core`), pnpm workspaces, Vitest, fastify/express/Next.js adapters, ts-rest, Zod, ioredis, MongoDB, clamator RPC over redis (`@clamator/over-redis`, `clamator_protocol`).

**Spec reference:** `docs/2026-05-11-optio-transports-design.md`. This plan implements that spec.

---

## Open-question decisions baked in (from spec §9)

1. `optio-contracts/optio-engine` subpath (drop the `-to-api` suffix).
2. `registerOptioApi` two-form: explicit `{ ctx, authenticate }` is the canonical shape; sugar `{ db, redis, authenticate }` retained for simple hosts. Sugar form returns `OptioContext` so host has it for shutdown; explicit form returns `void`.
3. `interop/run-http.ts` switches to `createOptioTransports`.
4. Codegen output filename: depends on `@clamator/codegen` behavior. **Verify in Task 1 Step 5**; if files end up `optio-engine.{ts,py}` instead of `engine.{ts,py}`, the migration step adjusts import paths accordingly. Plan steps below assume codegen filenames change; if they don't, only the in-file class names change and import paths stay.

---

## File structure

| Path | Action | Purpose |
|---|---|---|
| `packages/optio-contracts/src/engine-to-api.ts` | Rename to `optio-engine-to-api.ts` | Hosts `optioEngineContract = defineContract('optio-engine', ...)` |
| `packages/optio-contracts/src/index.ts` | Modify | Re-export `optioEngineContract` (was `engineContract`) |
| `packages/optio-contracts/package.json` | Modify | Subpath export rename: `./engine-to-api` → `./optio-engine` |
| `packages/optio-contracts/AGENTS.md` | Modify | Update filename + contract-name references |
| `Makefile` | Modify | `--ts-contract-import` path → `optio-contracts/optio-engine` |
| `packages/optio-api/src/_generated/engine.ts` | Regenerate (filename may change) | Generated `OptioEngineClient` |
| `packages/optio-core/src/optio_core/_generated/engine.py` | Regenerate (filename may change) | Generated `OptioEngineServiceBase`, params/result types |
| `packages/optio-core/src/optio_core/_engine_service.py` | Modify | `class OptioEngineService(OptioEngineServiceBase)` |
| `packages/optio-core/src/optio_core/lifecycle.py` | Modify | Update import + register-service call to use renamed symbols |
| `packages/optio-core/tests/test_engine_service.py` | Modify | Class name updates |
| `packages/optio-core/tests/test_engine_service_resolve.py` | Modify | Class name updates |
| `packages/optio-api/src/handlers.ts` | Modify | Use `OptioEngineClient`; switch command handlers to `resolveOptioEngine` (in commit 2) |
| `packages/optio-api/src/__tests__/handlers.test.ts` | Modify | `OptioEngineClient` references; update `vi.spyOn` if needed |
| `packages/optio-api/src/adapters/__tests__/{fastify,express,nextjs-app,nextjs-pages}.test.ts` | Modify | `vi.spyOn(EngineClient.prototype, ...)` → `OptioEngineClient.prototype` |
| `packages/optio-api/src/engine-cache.ts` | Delete (commit 2) | Replaced by `optio-transports.ts` |
| `packages/optio-api/src/optio-transports.ts` | Create (commit 2) | `createOptioTransports`, `OptioTransports` |
| `packages/optio-api/src/__tests__/optio-transports.test.ts` | Create (commit 2) | Unit tests for cache behavior |
| `packages/optio-api/src/context.ts` | Modify (commit 2) | `OptioContext { dbOpts, transports, redis, closeAll }` |
| `packages/optio-api/src/__tests__/context.test.ts` | Modify (commit 2) | Replace `engineCache` references with `transports` |
| `packages/optio-api/src/resolve-db.ts` | Rename to `resolve.ts` (commit 2) | Add `resolveOptioEngine` |
| `packages/optio-api/src/adapters/{fastify,express,nextjs-app,nextjs-pages}.ts` | Modify (commit 2) | Two-form signature, return `OptioContext`, drop `OptioApiHandle` |
| `packages/optio-api/src/adapters/__tests__/*.test.ts` | Modify (commit 2) | Drop `OptioApiHandle` assertions; use `ctx.closeAll()` |
| `packages/optio-api/src/index.ts` | Modify (both commits) | Re-export updates per spec §3 |
| `packages/optio-demo/interop/run-http.ts` | Modify (commit 3) | `createOptioTransports` instead of raw `RedisRpcClient`; rename `EngineClient` → `OptioEngineClient` |
| `packages/optio-demo/interop/run.ts` | Modify (commit 3) | Same |
| `packages/optio-api/AGENTS.md` | Modify (commit 4) | Layered architecture section + new exports table |
| `packages/optio-api/README.md` | Modify (commit 4) | Top-level paragraph on layered model; updated code examples |
| `packages/optio-contracts/AGENTS.md` | Modify (commit 4 if missed in commit 1) | Confirm rename references caught |
| Root `AGENTS.md` | Modify (commit 4) | If it mentions `EngineClient` or `engineCache`, update |

---

## Task 1: Optio-namespace rename (atomic, mechanical)

**Files:** see "File structure" table — every entry tagged commit 1 (everything *except* the layer-separation entries).

**Goal:** Rename the clamator contract from `engine` to `optio-engine` everywhere (wire name, var name, file name, generated class/module name) and update every consumer in lockstep. The engine-cache architecture is unchanged in this commit; only names.

### Step 1: Rename the contract file

- [ ] **Step 1.1: Rename `engine-to-api.ts` → `optio-engine-to-api.ts`**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
git mv packages/optio-contracts/src/engine-to-api.ts \
       packages/optio-contracts/src/optio-engine-to-api.ts
```

- [ ] **Step 1.2: Edit `packages/optio-contracts/src/optio-engine-to-api.ts`**

Find the export:

```typescript
export const engineContract = defineContract('engine', {
```

Change to:

```typescript
export const optioEngineContract = defineContract('optio-engine', {
```

Other exports in this file (failure-reason enum re-exports, result schemas) keep their names unless they include the word "engine" in a way that's now ambiguous. Read the full file and search for occurrences of `engine` — if any are bare and not already optio-namespaced, prefix them. The failure-reason enums (`LaunchFailureReason`, `CancelFailureReason`, etc.) live in `engine-failure-reasons.ts` — leave that filename and those symbol names unchanged (they're not the engine contract itself, they're shared reason enums; renaming them out of scope here).

### Step 2: Update optio-contracts re-exports + package config

- [ ] **Step 2.1: Edit `packages/optio-contracts/src/index.ts`**

Find every export referencing `engineContract` and rename to `optioEngineContract`. If the index re-exports from the renamed file, update the import path too:

```typescript
// Before:
export { engineContract } from './engine-to-api.js';
// After:
export { optioEngineContract } from './optio-engine-to-api.js';
```

(Spec §3 says `engineContract` is **not** re-exported from package root; only the failure-reason enums are. So index.ts may not have a re-export to update. Verify by reading the file. If no re-export exists, this step is a no-op for index.ts.)

- [ ] **Step 2.2: Edit `packages/optio-contracts/package.json`**

Find the exports block:

```json
"./engine-to-api": {
  "import": "./dist/engine-to-api.js",
  "types": "./dist/engine-to-api.d.ts"
}
```

Change to:

```json
"./optio-engine": {
  "import": "./dist/optio-engine-to-api.js",
  "types": "./dist/optio-engine-to-api.d.ts"
}
```

(Subpath name shortens to `optio-engine` per spec §9.1; file inside `dist/` keeps the full name since it mirrors the source file.)

### Step 3: Update Makefile codegen invocation

- [ ] **Step 3: Edit `Makefile`**

Locate the codegen target:

```makefile
codegen:  ## Regenerate clamator RPC client/server stubs from optio-contracts source
	pnpm exec clamator-codegen \
	  --src packages/optio-contracts/src \
	  --out-ts packages/optio-api/src/_generated \
	  --out-py packages/optio-core/src/optio_core/_generated \
	  --ts-contract-import 'optio-contracts/engine-to-api'
```

Change the `--ts-contract-import` value:

```makefile
	  --ts-contract-import 'optio-contracts/optio-engine'
```

### Step 4: Build optio-contracts so the renamed file is in `dist/`

- [ ] **Step 4: Build optio-contracts**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
pnpm -r --filter optio-contracts build 2>&1 | tail -5
```

Expected: green. The build copies `optio-engine-to-api.ts` → `dist/optio-engine-to-api.js`.

### Step 5: Regenerate clamator codegen output

- [ ] **Step 5.1: Run codegen**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
make clean-codegen
make codegen 2>&1 | tail -10
```

Expected: codegen succeeds. `_generated/` directories get fresh content.

- [ ] **Step 5.2: Verify codegen output filenames + class names**

```bash
ls packages/optio-api/src/_generated/
ls packages/optio-core/src/optio_core/_generated/
grep -n 'class.*Client\|class.*Service' packages/optio-api/src/_generated/*.ts
grep -n 'class.*Client\|class.*Service' packages/optio-core/src/optio_core/_generated/*.py
```

Two possible outcomes:

**Outcome A — codegen names files after contract:** files are `optio-engine.ts` and `optio_engine.py`. Classes are `OptioEngineClient` and `OptioEngineServiceBase`.

**Outcome B — codegen names files after source:** files are `optio-engine-to-api.ts` and `optio_engine_to_api.py` (or some variant). Classes still `OptioEngineClient`/`OptioEngineServiceBase`.

**Outcome C — codegen uses original short name:** files stay `engine.ts` / `engine.py`. Classes still `OptioEngineClient` (driven by var name in the contract).

Note which outcome holds. Subsequent steps refer to "the generated TS file" and "the generated Python module" — adjust paths to match what codegen actually produced. Class names are stable across outcomes: `OptioEngineClient`, `OptioEngineServiceBase`.

If the class names came out as something *other* than `OptioEngineClient` / `OptioEngineServiceBase`, codegen has a different naming convention than expected. STOP and report — the var-name → class-name assumption needs to be revisited.

### Step 6: Update optio-api consumer of the generated client

- [ ] **Step 6.1: Update `packages/optio-api/src/handlers.ts` imports**

Find the import of the generated engine:

```typescript
import type {
  // ...failure reason types
} from 'optio-contracts';
```

Currently there is no direct `EngineClient` import in handlers.ts (the engineCache returns it). After this step there still isn't — `handlers.ts` continues to use `ctx.engineCache.get(...)` (renamed in commit 2, not commit 1).

The grep below verifies handlers.ts has no stale `engineContract` reference:

```bash
grep -n 'engineContract\|EngineClient\|engine-to-api' packages/optio-api/src/handlers.ts
```

Expected: nothing matches.

- [ ] **Step 6.2: Sweep all of optio-api/src for stale symbols**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
grep -rn 'EngineClient\|engineContract\|engine-to-api' packages/optio-api/src/ \
  | grep -v _generated
```

Expected matches (these need renaming):
- `engine-cache.ts:3: import { EngineClient } from './_generated/engine.js';` → either update to `OptioEngineClient` (if codegen kept filename) or the new path. Update both filename in the import and the symbol name.
- `engine-cache.ts:9-15:` `type ManagedEngineClient = EngineClient & { ... }` → rename type to `ManagedOptioEngineClient` and update the `EngineClient` reference.
- `engine-cache.ts:30-34:` `new EngineClient(rpc)` → `new OptioEngineClient(rpc)`.
- `adapters/fastify.ts:35: import type { EngineClient } from '../_generated/engine.js';` → rename symbol + path.
- `adapters/express.ts`, `nextjs-app.ts`, `nextjs-pages.ts` — likely similar imports + uses for the `engine` getter return type.

Apply the renames in each file. Path updates depend on codegen Outcome from Step 5.2:
- If codegen filename is `optio-engine.ts`: change imports to `'../_generated/optio-engine.js'`.
- If filename stayed `engine.ts`: only rename the symbol.

- [ ] **Step 6.3: Sweep adapter test files**

```bash
grep -n 'EngineClient' packages/optio-api/src/adapters/__tests__/*.test.ts
```

Update `import { EngineClient } from '../../_generated/engine.js'` → use new symbol + (if changed) new path. And update every `vi.spyOn(EngineClient.prototype, ...)` → `vi.spyOn(OptioEngineClient.prototype, ...)`.

There are typically 3-4 `vi.spyOn` lines per adapter test file (`launch`, `cancel`, `dismiss`, `resync`). 4 files. ~14 lines total.

- [ ] **Step 6.4: Sweep handlers test file**

```bash
grep -n 'EngineClient' packages/optio-api/src/__tests__/handlers.test.ts
```

Update any matches.

### Step 7: Update optio-core consumer of the generated service

- [ ] **Step 7.1: Update `_engine_service.py`**

```bash
sed -n '15,30p' packages/optio-core/src/optio_core/_engine_service.py
```

Identify the import block and class declaration. Apply renames:

```python
# Before:
from optio_core._generated.engine import (
    EngineService as EngineServiceBase,
    LaunchParams, LaunchResult,
    # ...
)

class EngineService(EngineServiceBase):
    """..."""
```

After:

```python
from optio_core._generated.optio_engine import (   # or .engine, depending on Step 5.2 outcome
    OptioEngineService as OptioEngineServiceBase,
    LaunchParams, LaunchResult,
    # ...
)

class OptioEngineService(OptioEngineServiceBase):
    """..."""
```

The module path depends on codegen output filename from Step 5.2.

- [ ] **Step 7.2: Update `lifecycle.py`**

Find the import + service-registration call:

```bash
grep -n '_engine_service\|EngineService\|engine_contract\|register_service' packages/optio-core/src/optio_core/lifecycle.py
```

Update imports and call sites. The local symbol `EngineService` becomes `OptioEngineService`. The registration call probably looks like:

```python
from optio_core._engine_service import EngineService
# ...
self._engine_service = EngineService(self)
self.rpc_server.register_service(engine_contract, self._engine_service)
```

Update to:

```python
from optio_core._engine_service import OptioEngineService
# ...
self._engine_service = OptioEngineService(self)
self.rpc_server.register_service(optio_engine_contract, self._engine_service)
```

Where does `engine_contract` come from in lifecycle.py? Grep first:

```bash
grep -n 'engine_contract' packages/optio-core/src/optio_core/
```

If it's imported from `_generated/engine.py`, update the import path and symbol. If it's imported differently (e.g., from clamator), check what the new symbol name is. The codegen produces a `Contract` instance for use in `register_service` — its Python variable name follows the var-name convention too: `engine_contract` → `optio_engine_contract`.

- [ ] **Step 7.3: Update Python tests**

```bash
grep -rn 'EngineService\|engine_contract\|_engine_service' packages/optio-core/tests/ | head -20
```

Apply renames in:
- `test_engine_service.py`
- `test_engine_service_resolve.py`
- any other tests that touch these symbols

### Step 8: Verify codegen drift is clean

- [ ] **Step 8: Codegen idempotency check**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
make codegen
git diff --exit-code -- packages/optio-api/src/_generated packages/optio-core/src/optio_core/_generated
```

Expected: exit 0. Generated files match what's already in the working tree (codegen is deterministic).

### Step 9: Build everything

- [ ] **Step 9.1: TS build**

```bash
pnpm -r --filter optio-contracts --filter optio-api build 2>&1 | tail -10
```

Expected: green.

- [ ] **Step 9.2: TS tests**

```bash
cd packages/optio-api && pnpm test 2>&1 | tail -10
```

Expected: green (the only delta vs phase-4 acceptance is the renamed class symbol; behavior unchanged). The pre-existing `fastify-widget-proxy.test.ts > WS upgrade injects HeaderAuth` flake may surface; if it's the only failure and matches the known signature, proceed.

- [ ] **Step 9.3: Python tests**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports/packages/optio-core
pytest 2>&1 | tail -10
```

Expected: green.

### Step 10: Verify interop still works post-rename

- [ ] **Step 10: Run interop**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
make test-interop 2>&1 | tail -30
```

The interop scripts still use `EngineClient`/`engineContract` symbols at this point (they're updated in Task 3). They WILL FAIL to import the renamed symbols. To keep tree green for this commit, also update interop:

- [ ] **Step 10.1: Update interop runners' imports + class names (no behavior change)**

```bash
grep -n 'EngineClient' packages/optio-demo/interop/run.ts packages/optio-demo/interop/run-http.ts
```

For each match, replace `EngineClient` with `OptioEngineClient`. (Don't switch to `createOptioTransports` yet — that's Task 3.)

- [ ] **Step 10.2: Rerun interop**

```bash
make test-interop 2>&1 | tail -30
```

Expected: green incl. all scenarios from phase-4.

### Step 11: Commit

- [ ] **Step 11: Commit Task 1**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
git add -A
git status --short  # verify only expected files
git commit -m "$(cat <<'EOF'
refactor: rename engine contract to optio-engine (TS + Python + wire)

Disambiguates optio's engine RPC contract from other "engine" contracts
that consumer apps (e.g. Excavator) will introduce on the same redis
infrastructure. Pure rename — no behavior change, no architectural
change in this commit.

- packages/optio-contracts/src/engine-to-api.ts → optio-engine-to-api.ts
- engineContract → optioEngineContract
- defineContract('engine', ...) → defineContract('optio-engine', ...)
  (changes the redis routing key on the wire)
- Generated TS: EngineClient → OptioEngineClient
- Generated Python: EngineService → OptioEngineService
- optio-contracts subpath: ./engine-to-api → ./optio-engine
- Makefile codegen --ts-contract-import path updated
- All consumers (handlers.ts, adapters, lifecycle.py, _engine_service.py,
  interop runners, tests) updated to new symbol names

Engine subprocess and API rebuild in lockstep — wire-name change
requires both sides regenerated together. Pre-commit codegen drift
check passes; interop tests pass; per-package tests pass.
EOF
)"
```

---

## Task 2: Layer separation (OptioTransports + OptioContext shape)

**Files:**
- Modify: `packages/optio-api/src/engine-cache.ts` → Delete
- Create: `packages/optio-api/src/optio-transports.ts`
- Create: `packages/optio-api/src/__tests__/optio-transports.test.ts`
- Modify: `packages/optio-api/src/context.ts`
- Modify: `packages/optio-api/src/__tests__/context.test.ts`
- Rename: `packages/optio-api/src/resolve-db.ts` → `packages/optio-api/src/resolve.ts`
- Modify: `packages/optio-api/src/handlers.ts`
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/express.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`
- Modify: `packages/optio-api/src/index.ts`

**Goal:** Replace `EngineCache` (engine-specific) with `OptioTransports` (contract-agnostic, caches `RpcClient`). Add `resolveOptioEngine` helper. Drop `OptioApiHandle` — adapters return `OptioContext` (sugar form) or `void` (explicit form). `ctx.closeAll()` becomes the lifecycle entry point.

### Step 1: Create `optio-transports.ts`

- [ ] **Step 1: Create the new module**

Create file `packages/optio-api/src/optio-transports.ts`:

```typescript
import type { Redis } from 'ioredis';
import { RedisRpcClient, type RpcClient } from '@clamator/over-redis';

export interface OptioTransports {
  get(database: string, prefix: string): RpcClient;
  closeAll(): Promise<void>;
}

// Caches one RpcClient per (database, prefix) pair. Each RpcClient is bound
// to a unique redis namespace via keyPrefix = `${database}/${prefix}` and
// can be wrapped by any number of clamator contract clients (OptioEngineClient,
// custom domain clients, etc.).
//
// Cache is unbounded by design. Multi-db deployments are expected to have a
// small (~10) number of pairs. If the cache exceeds 100 entries in
// production, file an issue and revisit eviction strategy.
export function createOptioTransports(redis: Redis): OptioTransports {
  const map = new Map<string, RpcClient>();

  return {
    get(database, prefix) {
      const key = `${database}/${prefix}`;
      let rpc = map.get(key);
      if (!rpc) {
        rpc = new RedisRpcClient({ redis, keyPrefix: key });
        rpc.start().catch((err) => {
          console.error(`[optio-transports] start failed for ${key}:`, err);
        });
        map.set(key, rpc);
      }
      return rpc;
    },

    async closeAll() {
      const results = await Promise.allSettled([...map.values()].map((r) => r.stop()));
      map.clear();
      const rejections = results
        .filter((r): r is PromiseRejectedResult => r.status === 'rejected')
        .map((r) => r.reason);
      if (rejections.length > 0) {
        throw new AggregateError(rejections, 'closeAll: some transports failed to stop');
      }
    },
  };
}
```

Verify the import for `RpcClient` resolves — check `@clamator/over-redis`'s public exports:

```bash
grep -E 'export.*RpcClient' node_modules/@clamator/over-redis/dist/*.d.ts 2>/dev/null | head -3
```

If `RpcClient` is exported from a different path (e.g., `@clamator/protocol`), adjust the import.

### Step 2: Write tests for `optio-transports.ts`

- [ ] **Step 2.1: Create the test file**

Create `packages/optio-api/src/__tests__/optio-transports.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createOptioTransports } from '../optio-transports.js';

// Mock the RedisRpcClient: track instantiation + lifecycle.
const startMock = vi.fn(async () => {});
const stopMock = vi.fn(async () => {});

vi.mock('@clamator/over-redis', () => ({
  RedisRpcClient: vi.fn().mockImplementation((opts: any) => ({
    keyPrefix: opts.keyPrefix,
    start: startMock,
    stop: stopMock,
  })),
}));

const fakeRedis: any = { duplicate: () => fakeRedis };

beforeEach(() => {
  startMock.mockClear();
  stopMock.mockClear();
});

describe('createOptioTransports', () => {
  it('returns a fresh RpcClient on first get for a (db, prefix) pair', async () => {
    const transports = createOptioTransports(fakeRedis);
    const rpc = transports.get('mydb', 'optio');
    expect((rpc as any).keyPrefix).toBe('mydb/optio');
    // Allow the queued start() to fire.
    await new Promise((res) => setImmediate(res));
    expect(startMock).toHaveBeenCalledTimes(1);
  });

  it('returns the same RpcClient instance for the same (db, prefix) on subsequent calls', () => {
    const transports = createOptioTransports(fakeRedis);
    const rpc1 = transports.get('mydb', 'optio');
    const rpc2 = transports.get('mydb', 'optio');
    expect(rpc2).toBe(rpc1);
  });

  it('returns distinct RpcClient instances for different (db, prefix) pairs', () => {
    const transports = createOptioTransports(fakeRedis);
    const a = transports.get('mydb', 'optio');
    const b = transports.get('mydb', 'excavator');
    const c = transports.get('otherdb', 'optio');
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
    expect(b).not.toBe(c);
  });

  it('closeAll stops every cached RpcClient and clears the cache', async () => {
    const transports = createOptioTransports(fakeRedis);
    transports.get('mydb', 'optio');
    transports.get('mydb', 'excavator');
    await transports.closeAll();
    expect(stopMock).toHaveBeenCalledTimes(2);

    // After closeAll, a new get() must construct a fresh RpcClient.
    const fresh = transports.get('mydb', 'optio');
    expect(fresh).toBeDefined();
  });

  it('closeAll aggregates rejections without short-circuiting', async () => {
    stopMock.mockRejectedValueOnce(new Error('first stop failed'));
    stopMock.mockResolvedValueOnce(undefined);
    const transports = createOptioTransports(fakeRedis);
    transports.get('mydb', 'optio');
    transports.get('mydb', 'excavator');
    await expect(transports.closeAll()).rejects.toThrow(AggregateError);
    expect(stopMock).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2.2: Run the tests (RED if optio-transports.ts not yet created — but it is from Step 1)**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports/packages/optio-api
./node_modules/.bin/vitest run src/__tests__/optio-transports.test.ts 2>&1 | tail -15
```

Expected: 5/5 passing. If a test fails, investigate the divergence between expected and actual `RedisRpcClient` mock behavior.

### Step 3: Update `context.ts`

- [ ] **Step 3.1: Read current contents**

```bash
cat packages/optio-api/src/context.ts
```

Current shape:

```typescript
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

- [ ] **Step 3.2: Replace `context.ts`**

```typescript
import type { Redis } from 'ioredis';
import { createOptioTransports, type OptioTransports } from './optio-transports.js';
import type { DbOptions } from './resolve.js';

export interface OptioContext {
  dbOpts: DbOptions;
  transports: OptioTransports;
  redis: Redis;
  closeAll(): Promise<void>;
}

export function createOptioContext(opts: { dbOpts: DbOptions; redis: Redis }): OptioContext {
  const transports = createOptioTransports(opts.redis);
  return {
    dbOpts: opts.dbOpts,
    transports,
    redis: opts.redis,
    closeAll() {
      return transports.closeAll();
    },
  };
}
```

Note: import path changed from `./resolve-db.js` to `./resolve.js` (the file rename in Step 6 below). Apply this Step 3.2 only after Step 6 if you prefer; or temporarily import from `./resolve-db.js` and update after the rename. Cleanest: do Step 6 (file rename) first, then Step 3.2.

### Step 4: Update `context.test.ts`

- [ ] **Step 4: Update tests**

```bash
grep -n 'engineCache\|EngineCache\|createEngineCache' packages/optio-api/src/__tests__/context.test.ts
```

Replace every `engineCache` reference with `transports`, every `EngineCache` type with `OptioTransports`, and every `createEngineCache` with `createOptioTransports`. Add a test for `ctx.closeAll()`:

```typescript
  it('exposes closeAll that delegates to transports.closeAll', async () => {
    const ctx = createOptioContext({ dbOpts: { db: fakeDb }, redis: fakeRedis });
    const spy = vi.spyOn(ctx.transports, 'closeAll').mockResolvedValue(undefined);
    await ctx.closeAll();
    expect(spy).toHaveBeenCalledTimes(1);
  });
```

(`fakeDb` shape: whatever the existing test uses. If the test file doesn't have one, define a minimal stub.)

### Step 5: Delete `engine-cache.ts`

- [ ] **Step 5: Delete the old module**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
git rm packages/optio-api/src/engine-cache.ts
```

After this, every consumer of `createEngineCache` / `EngineCache` / `ManagedOptioEngineClient` / `ctx.engineCache` breaks compile. Subsequent steps fix them.

### Step 6: Rename `resolve-db.ts` → `resolve.ts` and add `resolveOptioEngine`

- [ ] **Step 6.1: Rename the file**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
git mv packages/optio-api/src/resolve-db.ts packages/optio-api/src/resolve.ts
```

- [ ] **Step 6.2: Add `resolveOptioEngine` to `resolve.ts`**

Append to `packages/optio-api/src/resolve.ts`:

```typescript
import type { OptioContext } from './context.js';
import { OptioEngineClient } from './_generated/optio-engine.js';  // adjust path if Step 5.2 outcome differs

export function resolveOptioEngine(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
): OptioEngineClient {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  return new OptioEngineClient(ctx.transports.get(database, prefix));
}
```

Note: this creates an import cycle (context.ts ↔ resolve.ts). To break it: change context.ts import to `import type { DbOptions } from './resolve.js'` (type-only import; no runtime cycle). The new function `resolveOptioEngine` imports `OptioContext` as a *value-bearing* type, not actually a value, so type-only import on the context side is the right pattern. If TypeScript still complains about the cycle, factor `DbOptions` into a separate small file or use an inline type.

If the cycle persists and is annoying, alternative: put `resolveOptioEngine` in its own file `resolve-engine.ts` that imports from both `context.ts` and `resolve.ts`. Recommend this if the cycle warning is loud.

- [ ] **Step 6.3: Update imports in callers**

```bash
grep -rn 'resolve-db' packages/optio-api/src/
```

Update every match: `'./resolve-db.js'` → `'./resolve.js'`.

### Step 7: Update `handlers.ts`

- [ ] **Step 7.1: Read current command handlers**

```bash
grep -n 'engineCache\|launchProcess\|cancelProcess\|dismissProcess\|resyncProcesses' packages/optio-api/src/handlers.ts
```

- [ ] **Step 7.2: Replace command-handler engine-acquisition lines**

For each of the four command handlers (`launchProcess`, `cancelProcess`, `dismissProcess`, `resyncProcesses`), replace:

```typescript
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);
```

with:

```typescript
  const engine = resolveOptioEngine(ctx, query);
```

Also: the `resyncProcesses` handler today destructures `{ database, prefix } = resolveDb(...)` without `db`. After change:

```typescript
export async function resyncProcesses(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  clean: boolean = false,
  metadataFilter?: ProcessMetadataFilter,
): Promise<{ message: string }> {
  const engine = resolveOptioEngine(ctx, query);
  await engine.resync({ clean, metadataFilter });
  return { message: clean ? 'Nuke and resync requested' : 'Resync requested' };
}
```

- [ ] **Step 7.3: Update handlers.ts imports**

Add to the imports block at top of `handlers.ts`:

```typescript
import { resolveOptioEngine } from './resolve.js';
```

If `resolveDb` import is no longer used in handlers.ts (read handlers may still use it), keep it. Verify:

```bash
grep -n 'resolveDb\|resolveOptioEngine' packages/optio-api/src/handlers.ts
```

Read handlers (`listProcesses`, `getProcess`, `getProcessTree`, `getProcessLog`, `getProcessTreeLog`) still call `resolveDb` because they need the `db` mongo handle. Their signatures are unchanged.

### Step 8: Update adapters

The four adapters share a pattern. Apply the same change to each, with framework-specific variations.

- [ ] **Step 8.1: Update `fastify.ts`**

Read the current `registerOptioApi`:

```bash
sed -n '359,410p' packages/optio-api/src/adapters/fastify.ts
sed -n '530,545p' packages/optio-api/src/adapters/fastify.ts
```

Identify:
- The `OptioApiOptions` type (the input)
- The current `OptioApiHandle` return type
- The function body that creates the context

Change in three places:

1. **Remove the `OptioApiHandle` type definition** (around line 38-40).

2. **Change `OptioApiOptions` to support both sugar and explicit form.** Define:

   ```typescript
   export type OptioApiOptions =
     | (DbOptions & { redis: Redis; authenticate: AuthCallback<FastifyRequest> })  // sugar
     | { ctx: OptioContext; authenticate: AuthCallback<FastifyRequest> };           // explicit
   ```

3. **Change `registerOptioApi` body** to detect form, build ctx if needed, return ctx (sugar) or void (explicit):

   ```typescript
   export function registerOptioApi(
     app: FastifyInstance,
     opts: OptioApiOptions,
   ): OptioContext | void {
     const ctx: OptioContext = 'ctx' in opts
       ? opts.ctx
       : createOptioContext({ dbOpts: opts as DbOptions, redis: (opts as any).redis });
     const authenticate = opts.authenticate;

     // [body: register routes onto app using ctx]
     // [existing route registration code, which currently uses `ctx.engineCache.get(...)`
     //  doesn't change — that's the path through handlers.ts, which already takes ctx]

     // Fastify-specific: wire teardown only if we own the ctx (sugar form).
     // If caller passed ctx in, they're responsible for closeAll.
     if (!('ctx' in opts)) {
       app.addHook('onClose', async () => { await ctx.closeAll(); });
       return ctx;  // sugar form: return ctx so host has it
     }
     return;  // explicit form: void
   }
   ```

The widget-proxy registration and other internal route-binding code in `fastify.ts` continues to use `ctx` the same way (no change to handler call sites).

- [ ] **Step 8.2: Update `express.ts`**

Same pattern. Express has no built-in onClose hook — sugar form's returned `ctx` is the host's responsibility:

```typescript
export function registerOptioApi(
  app: Express,
  opts: OptioApiOptions,
): OptioContext | void {
  const ctx: OptioContext = 'ctx' in opts
    ? opts.ctx
    : createOptioContext({ dbOpts: opts as DbOptions, redis: (opts as any).redis });
  // [route registration]
  return 'ctx' in opts ? undefined : ctx;
}
```

- [ ] **Step 8.3: Update `nextjs-app.ts`**

Same as express. The exported factory may be named differently (`createOptioRouteHandlers` etc.). Read the file to confirm:

```bash
grep -n 'export function\|OptioContext\|engineCache\|OptioApiHandle' packages/optio-api/src/adapters/nextjs-app.ts
```

Apply equivalent pattern. The function signature and return type follow the same `OptioContext | void` discrimination.

- [ ] **Step 8.4: Update `nextjs-pages.ts`**

Same as nextjs-app.

### Step 9: Update adapter tests

- [ ] **Step 9.1: Per adapter test file (`fastify.test.ts`, `express.test.ts`, `nextjs-app.test.ts`, `nextjs-pages.test.ts`)**

```bash
grep -n 'OptioApiHandle\|\.engine\b\|\.getEngine\|\.closeAll' packages/optio-api/src/adapters/__tests__/fastify.test.ts | head -20
```

For each adapter test file, do these in order:

1. Replace `const handle = registerOptioApi(...)` → `const ctx = registerOptioApi(...)`. Adjust based on form used in the test (sugar form returns ctx; explicit doesn't — tests should use sugar for brevity, matching the more common host case).

2. Replace `handle.engine` → if a test was asserting on it, replace with `new OptioEngineClient(ctx.transports.get(opts.db.databaseName, 'optio'))` — but most tests don't assert on `handle.engine` directly; they exercise HTTP routes.

3. Replace `handle.closeAll()` calls (in teardown) → `ctx.closeAll()`.

4. Replace `handle.getEngine(db, prefix)` → `new OptioEngineClient(ctx.transports.get(db, prefix))`.

5. Drop any test specifically asserting against `OptioApiHandle` shape (e.g., "registerOptioApi return shape > single-db mode returns { engine, closeAll }"). Replace with a single test confirming the sugar form returns an `OptioContext` and the explicit form returns void.

### Step 10: Update `index.ts` exports

- [ ] **Step 10: Update re-exports**

```bash
cat packages/optio-api/src/index.ts
```

Make the public surface match spec §3:

- Add: `createOptioTransports`, `OptioTransports` (type), `resolveOptioEngine`, `OptioEngineClient` (re-export from generated module).
- Remove: `createEngineCache`, `EngineCache`, `OptioApiHandle`.
- Keep: `createOptioContext`, `OptioContext`, `resolveDb`, `launchProcess`, `cancelProcess`, `dismissProcess`, `resyncProcesses`, read handlers, stream pollers, anything previously exported.

### Step 11: Build + test

- [ ] **Step 11.1: Build**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
pnpm -r --filter optio-contracts --filter optio-api build 2>&1 | tail -10
```

Expected: green.

- [ ] **Step 11.2: Test**

```bash
cd packages/optio-api && pnpm test 2>&1 | tail -10
```

Expected: green. The new `optio-transports.test.ts` adds 5 tests; total count grows accordingly. Pre-existing widget-proxy flake may surface.

### Step 12: Commit

- [ ] **Step 12: Commit Task 2**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
git add -A
git status --short
git commit -m "$(cat <<'EOF'
refactor(optio-api): separate Engine-access layer from HTTP-binding layer

Replaces engine-specific EngineCache with contract-agnostic OptioTransports
(caches RpcClient). Any clamator contract — OptioEngineClient, plus future
consumer contracts like Excavator — wraps a cached transport. Engine access
becomes a pure function of OptioContext, decoupled from HTTP route binding.

Changes:
- New optio-transports.ts (createOptioTransports, OptioTransports type)
  + unit tests covering caching, lifecycle, error aggregation
- engine-cache.ts deleted
- context.ts: OptioContext { dbOpts, transports, redis, closeAll }
- resolve-db.ts renamed to resolve.ts; adds resolveOptioEngine helper
- handlers.ts: command handlers shrink to one-line engine acquisition via
  resolveOptioEngine
- Adapters: registerOptioApi now takes either sugar form
  { db, redis, authenticate } (returns OptioContext) or explicit form
  { ctx, authenticate } (returns void). OptioApiHandle type deleted.
  Sugar form wires framework onClose to ctx.closeAll() where possible.
- index.ts: public surface per design §3

Hard break. No external consumer depends on the removed symbols (phase-3
shipped them ~3 days ago; Excavator port begins after this lands).
EOF
)"
```

---

## Task 3: Update interop scenarios

**Files:**
- Modify: `packages/optio-demo/interop/run-http.ts`
- Modify: `packages/optio-demo/interop/run.ts`

**Goal:** Replace raw `RedisRpcClient` construction with `createOptioTransports` to exercise the new Layer-1 API end-to-end.

### Step 1: Update `run.ts`

- [ ] **Step 1.1: Read current setup**

```bash
sed -n '1,70p' packages/optio-demo/interop/run.ts
```

Identify the import + setup lines (around line 14-15 and 61-63):

```typescript
import { RedisRpcClient } from '@clamator/over-redis';
import { OptioEngineClient } from 'optio-api';
// ...
const rpc = new RedisRpcClient({ redis, keyPrefix: KEY_PREFIX });
const engine = new OptioEngineClient(rpc);
```

- [ ] **Step 1.2: Replace with `createOptioTransports`**

```typescript
import { createOptioTransports, OptioEngineClient } from 'optio-api';
// (drop the RedisRpcClient import — no longer needed at top level)
// ...
const transports = createOptioTransports(redis);
const engine = new OptioEngineClient(transports.get(DATABASE, PREFIX));
```

Update `await rpc.start()` and `await rpc.stop()` calls in `main()`:

- `await rpc.start()` → drop (transports manages start internally per cache.get).
- `await rpc.stop()` → `await transports.closeAll()`.

Search:

```bash
grep -n 'rpc\.start\|rpc\.stop\|await rpc' packages/optio-demo/interop/run.ts
```

Replace each match per above.

### Step 2: Update `run-http.ts`

- [ ] **Step 2.1: Read current setup**

```bash
sed -n '1,30p' packages/optio-demo/interop/run-http.ts
sed -n '85,95p' packages/optio-demo/interop/run-http.ts
sed -n '230,245p' packages/optio-demo/interop/run-http.ts
```

Currently has at top:

```typescript
import { RedisRpcClient } from '@clamator/over-redis';
import { OptioEngineClient } from 'optio-api';
// ...
const rpc = new RedisRpcClient({ redis, keyPrefix: KEY_PREFIX });
const engine = new OptioEngineClient(rpc);
```

And in `main()`: `await rpc.start()` near the top, `await rpc.stop()` in finally.

- [ ] **Step 2.2: Replace setup**

```typescript
import { createOptioTransports, OptioEngineClient } from 'optio-api';
// (drop RedisRpcClient import)
// ...
const transports = createOptioTransports(redis);
const engine = new OptioEngineClient(transports.get(DATABASE, PREFIX));
```

- [ ] **Step 2.3: Replace lifecycle calls**

```bash
grep -n 'rpc\.start\|rpc\.stop\|await rpc' packages/optio-demo/interop/run-http.ts
```

Replace `await rpc.start()` → drop. Replace `await rpc.stop()` → `await transports.closeAll().catch(() => null)`.

### Step 3: Run interop

- [ ] **Step 3: Verify interop green**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
make test-interop 2>&1 | tail -40
```

Expected: all scenarios pass, including the phase-4 additions (`http-launch-no-resume-support`, `http-launch-launch-blocked`, `http-cancel-during-cancel`) and the direct-clamator scenarios.

If python packages need reinstall after worktree switch (per memory `reference_worktree_editable_install.md`):

```bash
pip install -e packages/optio-core -e packages/optio-opencode -e packages/optio-demo -e packages/optio-host 2>&1 | tail -3
```

### Step 4: Commit

- [ ] **Step 4: Commit Task 3**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
git add packages/optio-demo/interop/run.ts packages/optio-demo/interop/run-http.ts
git commit -m "$(cat <<'EOF'
test(optio-demo): interop uses createOptioTransports

run.ts and run-http.ts switch from constructing RedisRpcClient directly
to constructing it via createOptioTransports + cache.get(). Exercises
the new Layer-1 public API in the same end-to-end path the engine RPC
migration validates.
EOF
)"
```

---

## Task 4: Documentation

**Files:**
- Modify: `packages/optio-api/AGENTS.md`
- Modify: `packages/optio-api/README.md`
- Modify: `packages/optio-contracts/AGENTS.md` (verify rename references)
- Modify: Root `AGENTS.md` (if it mentions renamed symbols)

**Goal:** Documentation reflects the new layered architecture, naming, and public surface.

### Step 1: Update `packages/optio-api/AGENTS.md`

- [ ] **Step 1.1: Replace the existing "Exports" section**

```bash
grep -n '^##\|^###' packages/optio-api/AGENTS.md | head -20
```

Identify section boundaries. Rewrite the "Exports" section to reflect spec §3's public-surface table. Mention the four layers (per spec §2), the two-form `registerOptioApi` signature, and the difference between `createOptioTransports` (RPC-only consumers) and `createOptioContext` (HTTP hosts).

Concretely add or update:

```markdown
## Layered architecture

| Layer | Provides | Audience |
|-------|----------|----------|
| 1 | `createOptioTransports(redis): OptioTransports` — cache of `RpcClient` per `(database, prefix)` | RPC-only consumers (e.g., Excavator), custom HTTP adapter authors |
| 2 | `createOptioContext({ dbOpts, redis }): OptioContext` — bundles dbOpts, transports, redis, closeAll | HTTP hosts (typical) |
| 3a | `registerOptioApi(app, { ctx, authenticate })` (or sugar form) — binds HTTP routes onto a framework | HTTP hosts |

External consumers wrapping a clamator contract on a cached transport:

\`\`\`typescript
import { createOptioTransports, OptioEngineClient } from 'optio-api';

const transports = createOptioTransports(redis);
const optioEngine = new OptioEngineClient(transports.get('mydb', 'optio'));
await optioEngine.launch({ processId: 'foo' });
\`\`\`

HTTP hosts:

\`\`\`typescript
import { createOptioContext, registerOptioApi, resolveOptioEngine } from 'optio-api';

const ctx = createOptioContext({ dbOpts: { db }, redis });
registerOptioApi(app, { ctx, authenticate });
app.addHook('onClose', () => ctx.closeAll());

// Programmatic engine access in the host's own code:
const engine = resolveOptioEngine(ctx, {});
\`\`\`
```

- [ ] **Step 1.2: Remove obsolete content**

Delete any section describing:
- `createEngineCache` / `EngineCache` (removed).
- `OptioApiHandle` (removed).
- `handle.engine` / `handle.getEngine` / `handle.closeAll` (removed).

The "Architectural rule" section added in phase 4 stays — it's about engine ownership of writes and remains accurate.

### Step 2: Update `packages/optio-api/README.md`

- [ ] **Step 2.1: Replace the layered-architecture text and code examples**

Mirror the AGENTS.md changes at a high level (README is shorter, more user-facing).

```bash
grep -n '^##\|^###' packages/optio-api/README.md | head -15
```

Replace any code example using `createEngineCache` or `engineCache` with the new pattern. Replace references to `OptioApiHandle` with `OptioContext`.

### Step 3: Update `packages/optio-contracts/AGENTS.md`

- [ ] **Step 3: Sweep for stale references**

```bash
grep -n 'engine-to-api\|engineContract\|EngineClient' packages/optio-contracts/AGENTS.md packages/optio-contracts/README.md 2>/dev/null
```

Update each match: filename references (`engine-to-api.ts` → `optio-engine-to-api.ts`), symbol references (`engineContract` → `optioEngineContract`), subpath references (`./engine-to-api` → `./optio-engine`).

### Step 4: Update root `AGENTS.md`

- [ ] **Step 4: Sweep root AGENTS.md**

```bash
grep -n 'EngineClient\|engineCache\|OptioApiHandle\|engineContract' AGENTS.md
```

Update each match per the rename + surface changes.

### Step 5: Commit

- [ ] **Step 5: Commit Task 4**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports
git add packages/optio-api/AGENTS.md packages/optio-api/README.md \
        packages/optio-contracts/AGENTS.md \
        AGENTS.md
git status --short
git commit -m "$(cat <<'EOF'
docs: layered architecture + optio-namespace rename

- optio-api AGENTS.md and README.md: new "Layered architecture" section
  per design §2; replace removed symbols (createEngineCache, EngineCache,
  OptioApiHandle, handle.engine/closeAll); add example code for both
  external RPC consumers (createOptioTransports + new XClient(...)) and
  HTTP hosts (createOptioContext + registerOptioApi + resolveOptioEngine).
- optio-contracts AGENTS.md: file rename + subpath rename references.
- Root AGENTS.md: sweep for renamed symbols.
EOF
)"
```

---

## Final acceptance sweep

- [ ] **Step 1: Acceptance greps**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports

# No stale renamed symbols anywhere outside generated/dist
echo "=== EngineClient (should only appear in _generated as the old comment header if codegen comment retained, or nothing) ==="
grep -rE 'EngineClient' packages/ | grep -v _generated | grep -v dist/ | grep -v node_modules

echo "=== engineContract (should be empty) ==="
grep -rE 'engineContract' packages/ | grep -v _generated | grep -v dist/ | grep -v node_modules

echo "=== createEngineCache (should be empty) ==="
grep -rE 'createEngineCache|EngineCache' packages/ | grep -v node_modules | grep -v dist/

echo "=== OptioApiHandle (should be empty) ==="
grep -rE 'OptioApiHandle' packages/ | grep -v dist/ | grep -v node_modules

echo "=== handle.engine / handle.getEngine / handle.closeAll (should be empty) ==="
grep -rE 'handle\.engine\b|handle\.getEngine|handle\.closeAll' packages/ | grep -v dist/ | grep -v node_modules

echo "=== engine-cache.ts (should not exist) ==="
ls packages/optio-api/src/engine-cache.ts 2>&1 | head -1
```

Expected: every grep returns empty (or, for `EngineClient`, only an explanatory grep result indicating its generated-file context). The `ls` returns "no such file."

- [ ] **Step 2: Full test sweep**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+optio-transports

pnpm -r --filter optio-contracts --filter optio-api build 2>&1 | tail -10

cd packages/optio-api && pnpm test 2>&1 | tail -10
cd ../optio-core && pytest 2>&1 | tail -10

cd ../..
make test-interop 2>&1 | tail -20
```

Expected: all green. (Pre-existing fastify widget-proxy WS flake may surface; allowed.)

- [ ] **Step 3: Commit log review**

```bash
git log --oneline main..HEAD
```

Expected: 4 implementation commits + spec commit + plan commit = 6 commits total.
