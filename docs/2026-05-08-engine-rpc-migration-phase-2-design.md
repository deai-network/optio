# 2026-05-08 — Engine RPC migration, phase 2 design

**Status:** Design.
**Parent spec:** `docs/2026-05-08-engine-rpc-migration-design.md`.
**Phase 1 design:** `docs/2026-05-08-engine-rpc-migration-phase-1-design.md`.

This document supplements the parent spec by recording the decisions that resolve phase-2 open questions and by fixing the commit sequence. Everything not addressed here defers to the parent spec.

## 1. Scope

Phase 2 ships clamator RPC end-to-end with **co-existence**. **No HTTP behavior change.**

### What ships

- Engine: `EngineService` implementing **all 8 RPC methods** (launch / cancel / dismiss / resync / groupCancel / groupCancelAndWait / blockLaunches / unblockLaunches). Full validation, full failure-reason coverage, idempotent on at-least-once redelivery.
- Engine: `lifecycle.py` updates — `init()` accepts `rpc_server` parameter; constructs `RedisRpcServer` when `redis_url` set; registers `EngineService`; lifecycle calls in `run()` / `shutdown()`. Legacy `CommandConsumer` setup unchanged.
- Engine: `optio_core.rpc_server` module-level attribute via PEP 562 `__getattr__`.
- API: new `packages/optio-api/src/engine-cache.ts` — framework-agnostic `createEngineCache(redis): EngineCache` factory. Owns `EngineClient` lifecycle.
- API: all four adapters (`fastify`, `express`, `nextjs-app`, `nextjs-pages`) wire the cache. Universal return shape: `{ engine, closeAll }` (single-db) or `{ getEngine, closeAll }` (multi-db).
- API: HTTP command handlers continue calling legacy `publishLaunch / publishCancel / publishDismiss / publishResync` — no behavior change for HTTP requests.
- API: `index.ts` re-exports `EngineClient`, `createEngineCache`, type `EngineCache`.
- Interop substrate: `packages/optio-demo/interop/` TS subpackage; `run-interop.sh` orchestrator; `make test-interop` target wired with body. Phase-2 scenarios cover the direct clamator-client path plus the legacy-stream regression.
- Docs per parent spec §6 phase 2.

### What does not ship

- HTTP path migration to RPC (phase 3).
- API authority-code deletion (`LAUNCHABLE_STATES` etc.) and `ErrorSchema` flip to `{ reason, message }` (phase 4).
- Legacy stream + `CommandConsumer` + `on_command(...)` removal (phase 5).
- CI workflow bootstrapping.
- Excavator port to new RPC (post-migration).
- Architecture diagram refresh.
- Restructuring `packages/optio-contracts/src/schemas/`.

## 2. Phase-2 decisions

| # | Open question | Decision |
|---|---------------|----------|
| 1 | EngineService scope | All 8 methods, full validation. |
| 2 | `groupCancelAndWait` long-call strategy | Tune `consumer_claim_idle_ms` to 600000 ms (10 min) on the `RedisRpcServer`. Per-call client timeout is caller-supplied with a 600000 ms default. Verify the knob is exposed during commit 1; if absent, file an upstream clamator issue and block until fixed. |
| 3 | Multi-db cache eviction | Unbounded `Map`. TODO comment in `engine-cache.ts` plus measurement criterion ("if cache exceeds 100 entries in production, file an issue and revisit"). |
| 4 | Adapter return shape | Universal: single-db → `{ engine, closeAll }`; multi-db → `{ getEngine, closeAll }`. `cache.closeAll()` is idempotent. Fastify auto-wires `app.addHook('onClose', () => cache.closeAll())`; express / nextjs callers wire manually; tests use `closeAll` everywhere. |
| 5 | Interop test home | `packages/optio-demo/interop/` TS subpackage. `run-interop.sh` orchestrates docker-compose redis + python engine subprocess + node test runner. No HTTP server in phase 2 — direct clamator client only, plus legacy `XADD` regression. |
| 6 | Filename drift | Phase-2 plan amends parent spec to use the real filename `consumer.py` (not `_command_consumer.py`). No code rename. |
| 7 | `optio_core.rpc_server` exposure | Module-level PEP 562 `__getattr__` in `packages/optio-core/src/optio_core/__init__.py` forwards `rpc_server` to `_instance.rpc_server`. Avoids the import-time stale-`None` binding that would result from `rpc_server = _instance.rpc_server`. |
| 8 | `EngineService` name collision | Codegenned ABC imported as `EngineServiceBase`; local subclass keeps the name `EngineService`. Matches parent §4. |
| 9 | No-redis mode preserved | `init(redis_url=None, rpc_server=None)` → no rpc_server, no consumer (today's behavior). `redis_url` and `rpc_server` mutually exclusive; setting both raises `ValueError`. |
| 10 | Validation pattern | Pre-check (resolve doc + state-allowlist check) **and** try/except on `LaunchBlocked`. Defense in depth; matches parent §4. |
| 11 | EngineService → engine call surface | Calls public `Optio` methods (`self._optio.launch(...)`, `self._optio.cancel(...)`, etc.). Never writes the store directly. Avoids double-dispatch with `_handle_*` methods that are still wired up to the legacy consumer. |
| 12 | Pydantic field aliasing | clamator codegen emits Pydantic models with `Field(alias='processId')` and `model_config = ConfigDict(populate_by_name=True)` so wire field names stay camelCase while Python field names stay snake_case. Verify in commit 1 by parsing a real wire payload; if codegen drops aliasing, file an upstream clamator issue and block until fixed. |

## 3. Commit sequence

Five commits. Each leaves the tree green.

### Commit 1 — Engine: `EngineService` + lifecycle integration + module attribute

Files:

- New `packages/optio-core/src/optio_core/_engine_service.py`. `class EngineService(EngineServiceBase)` implementing all 8 methods. `_resolve(id_str)` accepts ObjectId hex or `processId` string; queries Mongo collection `{prefix}_processes`.
- `packages/optio-core/src/optio_core/lifecycle.py`:
  - `init()` signature gains `rpc_server: RpcServerCore | None = None`.
  - Mutual-exclusivity validation: `redis_url` and `rpc_server` both set → raise `ValueError("redis_url and rpc_server are mutually exclusive")`.
  - When `redis_url` set: construct `RedisRpcServer(redis=self._redis, key_prefix=f"{db_name}/{prefix}", consumer_claim_idle_ms=600000)`. Set `self.rpc_server = ...`. Set `self._owned_rpc_server = True`. Construct `EngineService(self)` and register on `self.rpc_server`.
  - When `rpc_server` set: register `EngineService` on the supplied server. Set `self._owned_rpc_server = False`. Skip redis client construction and skip legacy `CommandConsumer` setup.
  - `run()`: `await self.rpc_server.start()` before the existing block on `self._consumer.run()` (or `self._shutdown_event.wait()` when no consumer). `start()` is non-blocking per clamator §6.
  - `shutdown()`: stop `rpc_server` after the consumer stop (existing call) and before closing the redis client. Only call `stop(grace_ms=int(grace * 1000))` when `self._owned_rpc_server`.
- `packages/optio-core/src/optio_core/__init__.py`: add module-level `__getattr__`:

  ```python
  def __getattr__(name: str):
      if name == 'rpc_server':
          return _instance.rpc_server
      raise AttributeError(f"module 'optio_core' has no attribute {name!r}")
  ```

- New `packages/optio-core/tests/test_engine_service.py`:
  - Per RPC method: success path; every documented failure reason.
  - Idempotency on redelivery: replay the same RPC twice; second call returns the appropriate "already done" failure reason.
  - `_resolve(id_str)` covers ObjectId hex, processId string, and not-found.
  - Mutual-exclusivity validation in `init()`.

Acceptance:

- `make test` green (Python suite includes new `test_engine_service.py`; existing tests unchanged).
- A manual `python -c "import optio_core; print(optio_core.rpc_server)"` after `await init(redis_url=...)` returns a non-None `RedisRpcServer` instance.
- A manual `await init(redis_url=URL, rpc_server=server)` raises `ValueError`.

Verification gates (one-time, before merging commit 1):

- Confirm clamator's `RedisRpcServer.__init__` accepts `consumer_claim_idle_ms`. If absent, file an upstream issue and block. (Phase-2 design assumes the knob exists.)
- Confirm clamator's `RedisRpcServer.start()` is non-blocking. If blocking, wrap in `asyncio.create_task` and document the workaround.
- Confirm Pydantic models in `_generated/engine.py` carry `Field(alias='processId')` and `populate_by_name=True`. If absent, file an upstream issue and block. (Verify by parsing a real JSON payload `{"processId": "..."}` into the generated `LaunchParams`.)

### Commit 2 — API: `engine-cache.ts` standalone module

Files:

- New `packages/optio-api/src/engine-cache.ts` per parent §5:

  ```typescript
  import type { Redis } from 'ioredis';
  import { RedisRpcClient } from '@clamator/over-redis';
  import { EngineClient } from './_generated/engine.js';

  export interface EngineCache {
    get(database: string, prefix: string): EngineClient;
    closeAll(): Promise<void>;
  }

  export function createEngineCache(redis: Redis): EngineCache {
    const map = new Map<string, EngineClient>();
    return {
      get(database, prefix) {
        const key = `${database}/${prefix}`;
        let engine = map.get(key);
        if (!engine) {
          engine = new EngineClient(new RedisRpcClient({ redis, keyPrefix: key }));
          engine.start();
          map.set(key, engine);
        }
        return engine;
      },
      async closeAll() {
        await Promise.all([...map.values()].map(e => e.stop()));
        map.clear();
      },
    };
  }
  ```

  Plus a TODO comment immediately above `Map`:

  ```typescript
  // TODO: cache is unbounded by design. Multi-db deployments are expected to
  // have a small (~10) number of (database, prefix) pairs. If the cache exceeds
  // 100 entries in production, file an issue and revisit eviction strategy.
  ```

- New `packages/optio-api/src/__tests__/engine-cache.test.ts`:
  - `get` returns the same instance for the same `(database, prefix)`.
  - `get` returns distinct instances for distinct keys.
  - `closeAll()` calls `stop()` on every cached client (mocked or via `vi.spyOn`).
  - Second `closeAll()` is a no-op (no error, no double-stop).

Adapters and `index.ts` are not touched in this commit.

Acceptance: `pnpm -r test` green.

### Commit 3 — API adapters: cache integration + return shape

Files:

- `packages/optio-api/src/adapters/fastify.ts`:
  - Top of `registerOptioApi`: `const cache = createEngineCache(opts.redis);`.
  - `app.addHook('onClose', () => cache.closeAll());`.
  - At the end, return value:

    ```typescript
    if ('db' in opts && opts.db) {
      const prefix = opts.prefix ?? 'optio';
      return {
        engine: cache.get(opts.db.databaseName, prefix),
        closeAll: () => cache.closeAll(),
      };
    }
    return {
      getEngine: (database: string, prefix: string) => cache.get(database, prefix),
      closeAll: () => cache.closeAll(),
    };
    ```

  - HTTP command handlers (`launch`, `cancel`, `dismiss`, `resync`) continue to call legacy `handlers.launchProcess(db, redis, ...)` etc. — no behavior change.
- `packages/optio-api/src/adapters/express.ts`: same cache wiring + return shape. No `onClose` (express has no built-in close hook); caller wires `closeAll` manually.
- `packages/optio-api/src/adapters/nextjs-app.ts`: same cache wiring + return shape. No framework close hook; caller wires `closeAll`.
- `packages/optio-api/src/adapters/nextjs-pages.ts`: same.
- `packages/optio-api/src/index.ts`: add re-exports:

  ```typescript
  export { EngineClient } from './_generated/engine.js';
  export { createEngineCache, type EngineCache } from './engine-cache.js';
  ```

- New tests in each adapter's `__tests__/`:
  - Single-db: return value has `engine` (an `EngineClient` instance) and `closeAll` (a function). No `getEngine`.
  - Multi-db: return value has `getEngine` and `closeAll`. No `engine`.
  - `getEngine(database, prefix)` returns the same instance on repeat calls (cache reuse).
  - `closeAll()` then `closeAll()` succeeds (idempotent).

Existing tests and `packages/optio-dashboard/src/server.ts` are unaffected — all in-repo call sites ignore the return value.

Acceptance:

- `pnpm -r test` green; new adapter assertions pass.
- Type-check across the workspace succeeds.

### Commit 4 — Interop substrate

Files:

- New `packages/optio-demo/interop/`:
  - `package.json` — name `optio-demo-interop`, private, depends on `optio-api` (workspace), `optio-contracts` (workspace), `@clamator/over-redis`, `ioredis`, `vitest`, `tsx`.
  - `tsconfig.json` — extends repo base.
  - `run.ts` — entrypoint for the test runner. Constructs `Redis` (ioredis pointing at `localhost:6379`), constructs `RedisRpcClient` with `keyPrefix='optio-demo/optio'`, wraps in `EngineClient`, calls `start()`, executes scenarios, asserts, calls `stop()`. Exits non-zero on failure.
- New `packages/optio-demo/run-interop.sh`:
  1. Move to repo root for relative paths.
  2. `docker compose -f packages/optio-demo/docker-compose.yml up -d`.
  3. Wait for redis ready: `until docker exec optio-demo-redis redis-cli ping >/dev/null 2>&1; do sleep 0.2; done` (timeout 10s).
  4. Spawn `python -m optio_demo &` from `packages/optio-demo`. Capture PID. Wait for engine ready: poll `redis-cli exists optio-demo/optio:engine:heartbeat` (or whatever the existing heartbeat key path is — confirm during commit 4) for up to 30s.
  5. Run `pnpm --filter optio-demo-interop exec tsx run.ts` (or `node` after build).
  6. Capture exit code.
  7. Teardown: `kill $ENGINE_PID; wait $ENGINE_PID 2>/dev/null; docker compose -f packages/optio-demo/docker-compose.yml down -v`.
  8. Exit with the captured code.
- Top-level `Makefile`: replace empty `test-interop` body with:

  ```make
  test-interop:  ## End-to-end test: TS clamator client ↔ Py engine over real redis (clamator wire verification)
  	bash packages/optio-demo/run-interop.sh
  ```

Phase-2 interop scenarios (in `run.ts`):

1. **Direct clamator success matrix.** `engine.launch({processId: 'opencode-demo'})` → `{ ok: true, process }` with `process.status.state` in `{'scheduled', 'running'}`. Then `engine.cancel({processId: 'opencode-demo'})` → `{ ok: true, process }` with terminal state. Then `engine.dismiss({processId: 'opencode-demo'})` → `{ ok: true, process }` with idle state.
2. **Failure-reason coverage.**
   - Launch on running process → `{ ok: false, reason: 'not-launchable' }`.
   - Cancel on idle process → `{ ok: false, reason: 'not-cancellable' }`.
   - Dismiss on running process → `{ ok: false, reason: 'not-dismissable' }`.
   - Launch nonexistent processId → `{ ok: false, reason: 'not-found' }`.
   - Launch with `resume: true` on no-resume task → `{ ok: false, reason: 'no-resume-support' }`.
   - `engine.blockLaunches({launchFilter: {tag: ['demo']}})` → `{ ok: true }`. Then launch matching → `{ ok: false, reason: 'launch-blocked' }`. Then `engine.unblockLaunches({launchFilter: {tag: ['demo']}})` → `{ removed: 1 }`. Re-launch ok.
3. **Resync notification.** `engine.resync({})` returns void; engine logs / re-syncs definitions (verify side effect via a sentinel — e.g. count of process docs unchanged but a definition-load timestamp advanced).
4. **Group cancel.** `engine.groupCancel({metadataFilter: {tag: ['demo']}})` → `{ ok: true, cancelledCount: N }`. Validation branch: `engine.groupCancel({metadataFilter: {...}, persist: true})` (without `blockNewLaunches`) → `{ ok: false, reason: 'invalid-persist-without-block' }`.
5. **Group cancel and wait.** `engine.groupCancelAndWait({metadataFilter: {tag: ['demo']}})` (with at least one matching process scheduled) → `{ ok: true, cancelledCount: N }` after every matching process reaches a terminal state. Default per-call timeout 600000 ms; phase-2 scenario completes well under that.
6. **Legacy regression.** Bash-side `XADD optio-demo/optio:commands * type launch processId opencode-demo` (or via the node runner using ioredis `xadd`). Engine consumes; verify state transition. This proves both ingress paths are alive.

Each scenario assertion has a clear failure message. Total runtime budget: under 60 seconds.

Acceptance: `make test-interop` exits 0.

### Commit 5 — Docs + parent-spec corrections

Files:

- `packages/optio-core/README.md`: new "RPC service" section per parent §6 phase 2 — document `optio_core.rpc_server`, the B′ extension pattern (apps register additional services on `optio_core.rpc_server` before calling `optio_core.run()`), and co-existence with the legacy `${prefix}:commands` stream during phases 2–4.
- `packages/optio-core/AGENTS.md`: document the `rpc_server` attribute and the new `init()` `rpc_server` parameter.
- `packages/optio-api/README.md`: update OptioApiOptions doc; document return shape of `registerOptioApi` (`{ engine, closeAll }` / `{ getEngine, closeAll }`); add an `EngineClient` sharing example.
- `packages/optio-api/AGENTS.md`: document return shape; note the new `EngineClient` and `createEngineCache` exports; add `engine-cache.ts` to the "Building Custom Adapters" section.
- Root `AGENTS.md`: add `optio_core.rpc_server` to the public Python API table; add return-shape note for `registerOptioApi`, `createOptioRouteHandlers`, and `createOptioHandler`.
- Parent-spec corrections in `docs/2026-05-08-engine-rpc-migration-design.md`:
  - §4 and §8 phase 5: rename `_command_consumer.py` references to `consumer.py`.
  - Any §4 or §10 corrections discovered during commit 1 (e.g. exact `RedisRpcServer` constructor signature differences).

No code edits in this commit.

Acceptance:

- `make build` and `make test` green.
- Doc grep: `grep -rn '_command_consumer' docs/` returns only this design doc and the phase-2 plan (which both note the historical name).

## 4. Acceptance (overall, after commit 5)

- `make build` green across all TS + Python packages.
- `make test` green: new `EngineService` unit tests, idempotency-on-redelivery tests, `engine-cache.ts` cache tests, adapter return-shape tests.
- `make test-interop` green: success matrix + every documented failure reason + resync notification + block/unblock cycle + legacy-stream regression.
- `redis-cli xrange "${db}/${prefix}:cmds:engine"` after a clamator-client launch shows entries.
- `redis-cli xrange "${db}/${prefix}:commands"` after a legacy `XADD` shows entries; engine still consumes.
- `python -c "import optio_core; print(optio_core.rpc_server)"` after `await init(redis_url=...)` returns a non-None `RedisRpcServer`.
- `registerOptioApi(app, {db, redis})` returns `{ engine, closeAll }`; multi-db form returns `{ getEngine, closeAll }`. Calling `closeAll()` twice succeeds.
- Calling `init(redis_url=URL, rpc_server=server)` raises `ValueError`.
- HTTP behavior identical to pre-phase-2 — `/api/processes/:id/launch` etc. still go through the legacy `publishX` path, response status codes and body shapes unchanged.

## 5. Risks

- **Codegen field aliasing.** clamator codegen may not emit Pydantic `Field(alias='processId')` + `populate_by_name=True`. TS sends camelCase wire payloads; Pydantic would reject them silently or surface confusing validation errors. Verify in commit 1 by parsing a real wire payload into `LaunchParams`. If broken, file an upstream clamator issue; commit 1 blocks.
- **`consumer_claim_idle_ms` knob.** Parent §9 assumes `RedisRpcServer` accepts the param. If absent: file an upstream issue (preferred), or fall back to the contract change of moving `groupCancelAndWait` to a notification + status-poll method (amends phase 1, expensive). Verify in commit 1.
- **Lifecycle ordering.** `await self._consumer.run()` blocks the main coroutine. If `await self.rpc_server.start()` is also blocking, `run()` deadlocks. clamator §6 says `start()` is non-blocking — verify in commit 1; if blocking, wrap in `asyncio.create_task(self.rpc_server.start())` and document.
- **PEP 562 module `__getattr__` with bound methods.** `__init__.py` already binds `init = _instance.init` etc. Module `__getattr__` only fires for attributes not pre-bound at module level. `rpc_server` is not pre-bound → `__getattr__` fires. Verify in commit 1 with an explicit unit test (`assert optio_core.rpc_server is None` before `init`; `assert optio_core.rpc_server is not None` after).
- **Test redis pollution.** Per-instance reply streams persist if `closeAll()` not called. Test infra must call `closeAll()` in `afterEach` / `afterAll`. Optional reinforcement: prefix test stream keys with a random ID (already inherent in `keyPrefix='${database}/${prefix}'` if tests use a unique database).
- **Interop subprocess startup race.** Engine not ready when client connects. Mitigation: `run-interop.sh` polls `redis-cli exists` for the engine's heartbeat key before launching the test runner, with a 30 s timeout.
- **Double-dispatch hazard.** `Optio._handle_*` methods are still wired to the legacy consumer. `EngineService` calls public `Optio.launch / cancel / dismiss / resync` methods, which themselves delegate to `_handle_*`. Confirm the public methods do NOT also publish to the legacy stream — `Optio.launch` is in-process only (it's a public API), so calling it from `EngineService` does not produce a second redis message. Verified by reading `lifecycle.py` line 302–310 (current code).

## 6. Out of scope

- Anything not in §1 "What ships" — including all of parent-spec phases 3 / 4 / 5.
- Any HTTP-handler edits. `handlers.launchProcess` etc. still call `publishLaunch`. No status-code, body-shape, or signature changes in phase 2.
- API authority-code deletion (`LAUNCHABLE_STATES`, `cancellable` checks, `findProcessByEitherId` pre-RPC reads). Phase 4.
- `ErrorSchema` flip to `{ reason, message }`. Phase 4.
- Legacy stream removal, `CommandConsumer` deletion, `on_command(...)` removal. Phase 5.
- CI workflow bootstrapping. Tracked separately.
- Excavator port to the new RPC. Tracked separately, post-migration. The new `getEngine` return on `registerOptioApi` will be useful at that point.
- Architecture diagram refresh.
- Restructuring `packages/optio-contracts/src/schemas/`.
- Filing a `consumer_claim_idle_ms` upstream PR if the knob is missing — file an issue and block, do not patch upstream from this branch.

## 7. Parent-spec corrections (applied during phase 2)

- §4 and §8 phase 5: rename references `_command_consumer.py` → `consumer.py` (the actual filename in `packages/optio-core/src/optio_core/`). The internal class `CommandConsumer` is unchanged.
- Any §4 / §10 mismatches discovered during commit 1's verification gates (clamator constructor signature differences, codegen output shape) — corrected in the same commit 5 alongside the new doc additions.

The parent spec is the authoritative end-state document; its corrections ship in commit 5.
