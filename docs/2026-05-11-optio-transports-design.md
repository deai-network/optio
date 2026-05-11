# 2026-05-11 — OptioTransports layering refactor

**Status:** Design.

## 1. Goals

Two coupled architectural fixes:

1. **Layer separation.** `registerOptioApi` today smushes engine-access into the HTTP-binding return value — it creates the cache internally and re-exposes `engine` / `getEngine` / `closeAll` on `OptioApiHandle` because the host has no other way to reach the cache. This conflates two concerns: programmatic RPC access vs HTTP route binding. Separate them: host explicitly constructs the context (Layer 2), uses it directly for any programmatic access, and passes it into `registerOptioApi` for HTTP binding only.
2. **Transport-cache rename + extraction.** The internal `EngineCache` caches `RpcClient` per `(database, prefix)` and wraps an `EngineClient` around each. RPC-only consumers (e.g., Excavator porting to clamator) need the `RpcClient` itself so they can construct **their own** contract clients on the same transport. Expose the transport cache; let consumers wrap any contract on top.

The two changes touch the same surfaces. Ship together.

## 2. Layered architecture

| # | Layer | Lives in | Builds on | Provides |
|---|---|---|---|---|
| 0 | RPC primitive | `@clamator/over-redis` | `Redis` connection | `RedisRpcClient` keyed to a redis namespace |
| 1 | Transport cache | **`optio-api`** | Layer 0 + `Redis` | `OptioTransports.get(database, prefix) → RpcClient`, caches per namespace |
| 2 | Optio context | **`optio-api`** | Layer 1 + `Db` | `OptioContext { dbOpts, transports }`, helpers `resolveDb` / `resolveOptioEngine` for per-request resolution |
| 3a | HTTP adapter | **`optio-api/adapters/*`** | Layer 2 + framework (fastify/express/nextjs) | Wires HTTP routes to handlers; lifecycle hook into framework's onClose |
| 3b | Application code | host project (optio-demo, Excavator, custom server, etc.) | whichever layers it needs | the app |

Key architectural property: **Layer 1 is contract-agnostic.** It caches `RpcClient` per `(db, prefix)` — nothing about engines. RPC-only consumers stop at Layer 1 and wrap whatever client class their contract demands. Layer 2 is HTTP-handler-specific (bundles Mongo because handlers need it); RPC-only consumers do not touch Layer 2.

## 3. Public surface

| Symbol | Layer | Audience | Notes |
|---|---|---|---|
| `createOptioTransports(redis): OptioTransports` | 1 | RPC-only consumers + adapter authors | Factory for the transport cache |
| `OptioTransports` (type) | 1 | typing | `{ get(db, prefix): RpcClient; closeAll(): Promise<void> }` |
| `createOptioContext({ dbOpts, redis }): OptioContext` | 2 | HTTP hosts (typical) | Builds an `OptioTransports` internally and stores it in the context |
| `OptioContext` (type) | 2 | typing | `{ dbOpts: DbOptions; transports: OptioTransports; redis: Redis }` (redis stays exposed for consumers needing direct access) |
| `resolveDb(dbOpts, query)` | 2 | adapter authors / handler-using code | Unchanged behavior |
| `resolveOptioEngine(ctx, query): OptioEngineClient` | 2 | adapter authors / handler-using code | `resolveDb` + `new OptioEngineClient(ctx.transports.get(...))` |
| `registerOptioApi(app, { ctx, authenticate })` (fastify et al.) | 3a | HTTP hosts | Takes ctx; binds routes; framework's onClose calls `ctx.closeAll()` |
| `OptioEngineClient` (type + class) | 0/3b | anyone constructing engine clients (hosts, consumers) | Generated; renamed from `EngineClient` |
| `launchProcess`, `cancelProcess`, `dismissProcess`, `resyncProcesses` | 2 | adapter authors | Unchanged signatures; internally use `resolveOptioEngine` |
| `listProcesses`, `getProcess`, `getProcessTree`, `getProcessLog`, `getProcessTreeLog` | 2 | adapter authors | Unchanged |
| `optioEngineContract` | (contracts) | codegen + advanced consumers | Renamed from `engineContract`; re-exported from `optio-contracts` |

**Removed:**

- `createEngineCache` factory (no replacement; use `createOptioTransports`)
- `EngineCache` type (no replacement; `OptioTransports`)
- `OptioContext.engineCache` field (renamed to `.transports`)
- `OptioApiHandle.engine`, `.getEngine`, `.closeAll` (host owns ctx and calls `ctx.closeAll()` directly)
- `OptioApiHandle` type itself (registerOptioApi returns `void`)

**Renamed at codegen level:**

- TS: `EngineClient` → `OptioEngineClient` (in `_generated/engine.ts`)
- Python: `EngineService` → `OptioEngineService` (in `_generated/engine.py` and `_engine_service.py`)
- Wire contract name: `'engine'` → `'optio-engine'` (in `optioEngineContract = defineContract('optio-engine', ...)`)

## 4. File-level changes

### `packages/optio-contracts/src/`

- Rename `engine-to-api.ts` → `optio-engine-to-api.ts`.
- Inside that file: `engineContract = defineContract('engine', ...)` → `optioEngineContract = defineContract('optio-engine', ...)`.
- `index.ts`: update re-exports.
- `package.json` exports field: update subpath `optio-contracts/engine-to-api` → `optio-contracts/optio-engine-to-api` (or keep the subpath, just rename the file — choice in §9 open questions).

### `packages/optio-core/src/optio_core/`

- `_generated/engine.py` regenerated from renamed contract. Module name stays `engine.py` (filename is implementation detail, the class names are what matter).
- `_engine_service.py`: `class EngineService(EngineServiceBase)` → `class OptioEngineService(OptioEngineServiceBase)`. Update imports.
- `lifecycle.py`: the call `self.rpc_server.register_service(engine_contract, self._engine_service)` updates symbol names. Other lifecycle code unchanged.
- Tests: `test_engine_service.py`, `test_engine_service_resolve.py` — rename class references; behavior unchanged.

### `packages/optio-api/src/`

- New `optio-transports.ts`:
  ```ts
  export interface OptioTransports {
    get(database: string, prefix: string): RpcClient;
    closeAll(): Promise<void>;
  }
  export function createOptioTransports(redis: Redis): OptioTransports { /* ... */ }
  ```
  Replaces `engine-cache.ts`. Returns `RpcClient` (not `EngineClient`). Lifecycle (`start()` on each cached client, `stop()` on closeAll) stays.
- Delete `engine-cache.ts`. The old behavior is reproducible via `createOptioTransports(redis)` + `new OptioEngineClient(transports.get(...))`.
- `context.ts`:
  - `OptioContext { dbOpts, transports, redis }` (field renamed from `engineCache`; `redis` retained for direct-access consumers).
  - `createOptioContext({ dbOpts, redis })` builds an `OptioTransports` internally; exposes it as `ctx.transports`.
  - Add `OptioContext.closeAll(): Promise<void>` — delegates to `ctx.transports.closeAll()`. Host calls this on shutdown. (Does NOT close the redis connection — host owns redis lifecycle.)
- New `resolve.ts` (renamed from `resolve-db.ts`):
  - Keeps `resolveDb(dbOpts, query)`.
  - Adds `resolveOptioEngine(ctx, query): OptioEngineClient`:
    ```ts
    export function resolveOptioEngine(
      ctx: OptioContext,
      query: { database?: string; prefix?: string },
    ): OptioEngineClient {
      const { database, prefix } = resolveDb(ctx.dbOpts, query);
      return new OptioEngineClient(ctx.transports.get(database, prefix));
    }
    ```
- `handlers.ts`:
  - Command handlers use `resolveOptioEngine(ctx, query)`. Single line each.
  - Import `OptioEngineClient` from `./_generated/engine.js`.
- `adapters/{fastify,express,nextjs-app,nextjs-pages}.ts`:
  - Signature change: `registerOptioApi(app, { ctx, authenticate })` (and equivalents).
  - Convenience sugar form: `registerOptioApi(app, { db, redis, authenticate })` still accepted; internally constructs `ctx` and returns it as the return value so the host has it for `closeAll`.
  - Return type: either `OptioContext` (sugar form) or `void` (explicit ctx form). Discriminated.
  - Framework onClose hook: `app.addHook('onClose', () => ctx.closeAll())`.
  - Delete `OptioApiHandle` type.
- `index.ts`: update re-exports per §3.

### Adapter tests

- Update `EngineClient` → `OptioEngineClient` mock-prototype patches.
- Drop assertions against `OptioApiHandle.engine` / `.getEngine` / `.closeAll`; replace with assertions against `ctx.closeAll()` lifecycle.

### `packages/optio-demo/`

- `interop/run.ts` and `interop/run-http.ts`: rename `EngineClient` → `OptioEngineClient`. Use `createOptioTransports` where appropriate (interop's run-http currently wires `RedisRpcClient` + `EngineClient` directly; can switch to `createOptioTransports().get(...)` for consistency, or leave as the lowest-level form for diagnostic purposes — open Q).

### Docs

- `packages/optio-api/AGENTS.md`: rewrite "Exports" section + add "Layered architecture" section with the §2 table. Document the new `registerOptioApi({ ctx, authenticate })` shape. Document `createOptioTransports` as the entry point for RPC-only consumers and custom HTTP adapters.
- `packages/optio-api/README.md`: top-level paragraph on the layered model. Update code examples.
- `packages/optio-contracts/AGENTS.md`: note the file rename + contract-name rename.
- Root `AGENTS.md`: if it mentions `EngineClient` or `engineCache`, update.

## 5. Wire-level break

Renaming the clamator contract from `'engine'` to `'optio-engine'` changes the redis stream keys clamator uses for routing (`<keyPrefix>:cmds:engine:*` → `<keyPrefix>:cmds:optio-engine:*` or whatever clamator's exact key scheme is). Engine and API must rebuild and redeploy in lockstep.

In this monorepo, lockstep is the default (single-PR change). External consumers (Excavator if it predates this rename — it doesn't; Excavator's port begins after) are unaffected.

## 6. Backward compatibility

**Hard break.** All renamed/removed symbols disappear; no aliases, no deprecation warnings.

Rationale:
- The renamed symbols (`createEngineCache`, `EngineCache`, `OptioApiHandle`, `EngineClient`) shipped to `main` ~3 days ago (phase 3 of the engine-RPC migration, 2026-05-08 design). No external consumer has built on them yet.
- Excavator's port (sole concrete external consumer) starts after this lands and adopts the new shape directly.
- Soft-break ceremony (alias + deprecation logging) costs more in code surface and confusion than the cleanup buys.

## 7. Migration of internal call sites

Search-and-replace pattern:

| Find | Replace |
|---|---|
| `EngineClient` (import + class name) | `OptioEngineClient` |
| `engineContract` | `optioEngineContract` |
| `engine-to-api` (import path) | `optio-engine-to-api` |
| `EngineService` (Python class) | `OptioEngineService` |
| `createEngineCache(redis)` | `createOptioTransports(redis)` |
| `EngineCache` (type) | `OptioTransports` |
| `ctx.engineCache.get(...)` | `ctx.transports.get(...)` |
| `ctx.engineCache.closeAll()` | `ctx.closeAll()` |
| `ctx.engineCache.get(db, prefix)` (inside handlers; one-line engine construction) | `resolveOptioEngine(ctx, query)` |
| `handle.engine` (in consumer code) | construct via `resolveOptioEngine(ctx, {})` if Mongo-bound, or `new OptioEngineClient(transports.get(...))` if RPC-only |
| `handle.getEngine(db, prefix)` | `resolveOptioEngine(ctx, { database: db, prefix })` or `new OptioEngineClient(ctx.transports.get(db, prefix))` |
| `handle.closeAll()` | `ctx.closeAll()` |
| Defining the contract `defineContract('engine', ...)` | `defineContract('optio-engine', ...)` |

Tests: every adapter test file (handlers + 4 adapter tests + 2 interop runners) imports `EngineClient` or refers to `engineCache`. Sweep all.

## 8. Testing strategy

- `pnpm test` per package green after refactor.
- `make test-interop` green (covers the wire-rename — interop is the only place that exercises end-to-end RPC over real redis with engine subprocess).
- New unit test for `createOptioTransports`: verifies caching behavior (same `(db, prefix)` returns the same `RpcClient`, different pairs return different ones), verifies `closeAll` stops all and clears the cache, verifies lifecycle errors aggregate.
- Adapter tests verify `ctx` is constructed correctly in both sugar form (`{ db, redis, authenticate }`) and explicit form (`{ ctx, authenticate }`).
- No new interop scenarios needed — existing ones exercise the wire path, and the rename has to work for those to pass.

## 9. Open questions

1. **`optio-contracts` subpath export name.** Today `optio-contracts/engine-to-api`. After file rename: `optio-contracts/optio-engine-to-api` (verbose but consistent), or shorten to `optio-contracts/optio-engine` (drop the `-to-api` suffix, since the new name already says optio)? Recommend `optio-contracts/optio-engine`.
2. **`registerOptioApi` two-form vs one-form.** Two-form (sugar `{ db, redis, authenticate }` + explicit `{ ctx, authenticate }`) is more flexible; one-form (require explicit ctx) is simpler. Recommend two-form because the sugar matches today's shape; explicit is opt-in for power users.
3. **`interop/run-http.ts`: switch to `createOptioTransports` or keep raw `RedisRpcClient`?** Raw form documents the lowest-level interface; the new form exercises optio-api Layer 1. Recommend switch — interop validates the public API surface end-to-end.
4. **Codegen output filenames.** `_generated/engine.ts` and `_generated/engine.py` — rename to `_generated/optio-engine.ts` / `optio_engine.py`? Filenames are derived from the contract file (`optio-engine-to-api.ts`). Confirm clamator codegen's naming convention; rename if needed.

## 10. Risks

1. **Wire-rename + monorepo lockstep.** If a partial build or stale generated stub is loaded after the wire rename, RPC silently routes to nothing (old name not registered) or fails to register (new name not yet implemented). Mitigation: run `make codegen` + full rebuild in the same commit as the rename; pre-commit hook already checks codegen drift.
2. **Test mock breakage.** All 4 adapter test files `vi.spyOn(EngineClient.prototype, ...)`. Class rename means 12+ spy lines update across 4 files. Mechanical but easy to miss one. Mitigation: `grep -r EngineClient packages/` after edit returns nothing.
3. **OptioApiHandle removal.** Tests asserting against `OptioApiHandle.engine` / `.closeAll` must be rewritten to assert against `ctx.closeAll()`. Mechanical sweep of test files.
4. **External documentation / runbooks** referencing the old name (`EngineClient`, `engineCache`). Out of scope; user updates externally.

## 11. Out of scope

- Watchdog/supervisor for autonomous-mode crash recovery (separate spec).
- Phase 5 of the engine-RPC migration (`CommandConsumer` removal, `on_command` removal).
- `optio-ui` state-set duplication follow-up.
- Excavator's actual port to clamator (downstream of this spec).
- Changing clamator's own APIs.
- Optio-host integration (no engine access today; unaffected).
