# Engine RPC Migration Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Working directory:** All work happens in the git worktree at `/home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4/` on branch `csillag/rpc-migration-phase-4`. Subagents must operate inside this directory. Outputs landing in `/home/csillag/deai/optio/` (the main checkout) are wrong location — flag immediately.

**Goal:** Finish the architectural rule from parent §1.3 (engine owns all writes; API is a pure RPC translator) by deleting the API-side defense-in-depth pre-checks, restoring single-source-of-truth for state-set constants in `optio-core`, and adversarially testing the engine's authority over the failure-reason matrix.

**Architecture:** Five small commits. (a-prime) bonus engine cleanup — `_engine_service.py` and `lifecycle.py` import state sets from `state_machine.py` instead of redefining locally; side effect: re-cancel on `cancel_requested` returns 409 instead of misleading 200. (a) API handler cleanup — delete `LAUNCHABLE_STATES` / `CANCELLABLE_STATES` / `END_STATES` constants, pre-RPC `findProcessByEitherId` lookups, and the state/`cancellable`/`supportsResume` guard blocks; pass raw `id` to `engine.X(...)`. (b) New Python tests for `EngineService._resolve` covering both id forms exhaustively. (c) Adversarial interop scenarios filling the failure-reason matrix and validating the (a-prime) cancel-during-cancel behavior. (d) Doc updates: optio-api AGENTS.md gains the architectural rule statement and loses the state-guards block; README scrubs state-validation language; parent design records phase-4 as shipped.

**Tech Stack:** TypeScript (`packages/optio-api`, `packages/optio-contracts`, `packages/optio-demo/interop`), Python 3.12 (`packages/optio-core`), pnpm workspaces, Vitest, fastify / express / Next.js adapters, ts-rest, Zod, ioredis, MongoDB (`mongodb` driver / `motor`), clamator RPC over redis (`@clamator/over-redis`, `clamator_protocol`).

**Spec reference:** Full design at `docs/2026-05-10-engine-rpc-migration-phase-4-design.md`. Parent spec: `docs/2026-05-08-engine-rpc-migration-design.md` (see §11 phase-3 scope addendum and §8.4 phase-4 narrowed scope). This plan implements the design.

---

## File structure

| Path | Action | Purpose |
|---|---|---|
| `packages/optio-core/src/optio_core/_engine_service.py` | Modify | Delete local state-set constants; import from `state_machine` |
| `packages/optio-core/src/optio_core/lifecycle.py` | Modify | Replace anonymous state-set literals (lines 780, 929) with named imports |
| `packages/optio-api/src/handlers.ts` | Modify | Delete state-set constants + pre-check blocks in 3 command handlers; pass raw `id` to engine |
| `packages/optio-api/src/__tests__/handlers.test.ts` | Modify | Drop 7 pre-check unit tests; add 3 missing engine-reason coverage tests |
| `packages/optio-api/src/adapters/__tests__/fastify.test.ts` | Modify | Drop 6 pre-check HTTP tests; keep one engine-failure roundtrip |
| `packages/optio-api/src/adapters/__tests__/express.test.ts` | Modify | Same |
| `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts` | Modify | Same |
| `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts` | Modify | Same |
| `packages/optio-core/tests/test_engine_service_resolve.py` | Create | Exhaustive `_resolve` matrix + per-id-form smoke |
| `packages/optio-demo/interop/run-http.ts` | Modify | Add `http-launch-no-resume-support`, `http-launch-launch-blocked`, `http-cancel-during-cancel` scenarios |
| `packages/optio-api/AGENTS.md` | Modify | Add architectural rule statement; delete State guards block |
| `packages/optio-api/README.md` | Modify | Scrub state-validation language in REST Endpoints table |
| `docs/2026-05-08-engine-rpc-migration-design.md` | Modify | Mark phase-4 deliverables done; record actual scope (no engine migration; bonus a-prime) |

---

## Task 1: a-prime — State-set SoT cleanup (engine)

**Files:**
- Modify: `packages/optio-core/src/optio_core/_engine_service.py`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

**Goal:** `state_machine.py` is the single source of truth for state sets. Delete redundant local definitions in `_engine_service.py` and replace anonymous set literals in `lifecycle.py`. Side effect: `EngineService.cancel` now uses `CANCELLABLE_STATES = {"scheduled", "running"}` (no `cancel_requested`), restoring consistency with the lifecycle guard.

- [ ] **Step 1: Verify current state of files**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
sed -n '30,40p' packages/optio-core/src/optio_core/_engine_service.py
sed -n '28p;780p;929p' packages/optio-core/src/optio_core/lifecycle.py
sed -n '14,18p' packages/optio-core/src/optio_core/state_machine.py
```

Expected: `_engine_service.py:33-37` defines local `LAUNCHABLE_STATES`, `CANCELLABLE_STATES = {"scheduled", "running", "cancel_requested"}`, `DISMISSABLE_STATES`. `lifecycle.py:28` imports `ACTIVE_STATES, CANCELLABLE_STATES`. `lifecycle.py:780` has anon set `{"scheduled","running","cancel_requested","cancelling"}`. `lifecycle.py:929` has anon set `{"done","failed","cancelled"}`. `state_machine.py:14-18` defines all five canonical sets.

- [ ] **Step 2: Edit `_engine_service.py` — replace local definitions with import**

Locate the import block (around lines 14-27) and add the new import line right after the `bson` import:

```python
from optio_core._generated.engine import (
    EngineService as EngineServiceBase,
    LaunchParams, LaunchResult,
    CancelParams, CancelResult,
    DismissParams, DismissResult,
    GroupCancelParams, GroupCancelResult,
    GroupCancelAndWaitParams, GroupCancelAndWaitResult,
    BlockLaunchesParams, BlockLaunchesResult,
    UnblockLaunchesParams, UnblockLaunchesResult,
    ResyncParams,
)
from optio_core.models import LaunchBlocked
from optio_core.state_machine import LAUNCHABLE_STATES, CANCELLABLE_STATES, DISMISSABLE_STATES
```

Then delete lines 33-37 (the comment block and three local set constants). Specifically remove:

```python
# State allowlists from packages/optio-api/src/handlers.ts. Mirrored here so
# the engine — not the API — owns the rule (parent spec authority statement).
LAUNCHABLE_STATES = {"idle", "done", "failed", "cancelled"}
CANCELLABLE_STATES = {"scheduled", "running", "cancel_requested"}
DISMISSABLE_STATES = {"done", "failed", "cancelled"}
```

The references at lines 100, 133, 146 (the `not in *_STATES` checks in `launch`/`cancel`/`dismiss`) stay unchanged — symbol names match canonical exports.

- [ ] **Step 3: Edit `lifecycle.py` — widen import + replace anon sets**

Line 28: change

```python
from optio_core.state_machine import ACTIVE_STATES, CANCELLABLE_STATES
```

to

```python
from optio_core.state_machine import ACTIVE_STATES, CANCELLABLE_STATES, END_STATES
```

Line 780: change

```python
        non_terminal = {"scheduled", "running", "cancel_requested", "cancelling"}
```

to

```python
        non_terminal = ACTIVE_STATES
```

Line 929: change

```python
        if proc["status"]["state"] not in {"done", "failed", "cancelled"}:
```

to

```python
        if proc["status"]["state"] not in END_STATES:
```

- [ ] **Step 4: Verify post-edit content**

```bash
sed -n '14,27p' packages/optio-core/src/optio_core/_engine_service.py
grep -n "LAUNCHABLE_STATES\|CANCELLABLE_STATES\|DISMISSABLE_STATES" packages/optio-core/src/optio_core/_engine_service.py
sed -n '28p' packages/optio-core/src/optio_core/lifecycle.py
sed -n '780p;929p' packages/optio-core/src/optio_core/lifecycle.py
```

Expected: `_engine_service.py` import block contains the new `from optio_core.state_machine import ...` line; `LAUNCHABLE_STATES`/`CANCELLABLE_STATES`/`DISMISSABLE_STATES` greps return only the import line + the three usage lines (100, 133, 146) — no local definition. `lifecycle.py:28` shows widened import. `lifecycle.py:780` reads `non_terminal = ACTIVE_STATES`. `lifecycle.py:929` reads `not in END_STATES`.

- [ ] **Step 5: Run optio-core test suite**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4/packages/optio-core
pytest 2>&1 | tail -30
```

Expected: green. If any `EngineService.cancel` test asserts a 200 result for a `cancel_requested` proc, it must be updated to expect `{"ok": False, "reason": "not-cancellable"}`. (Read the failure first — do not blindly edit. The behavior change is intentional: re-cancel was returning a misleading 200 with no state change, now correctly returns `not-cancellable`.)

If a test fails because of this behavior change, update the test in this same commit; document the change in the commit message.

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
git add packages/optio-core/src/optio_core/_engine_service.py packages/optio-core/src/optio_core/lifecycle.py
# If tests changed, also stage them:
# git add packages/optio-core/tests/<file>
git commit -m "$(cat <<'EOF'
refactor(optio-core): single source of truth for state-set constants

_engine_service.py and lifecycle.py imported (or redefined) state-set
constants in three different ways. Consolidate on state_machine.py as
canonical:

- _engine_service.py drops local LAUNCHABLE/CANCELLABLE/DISMISSABLE
  copies; imports from state_machine.
- lifecycle.py widens its existing import to include END_STATES;
  replaces two anonymous set literals (lines 780, 929) with named
  references.

Side effect: EngineService.cancel pre-check no longer accepts
cancel_requested. Old behavior was a misleading 200 — pre-check
admitted the call but lifecycle._handle_cancel guarded against
cancel_requested and no-oped. New behavior returns
{ok: false, reason: not-cancellable}, matching long-standing API
contract. Phase 4 commit (a) deletes the API guard, so this
restores authority where it belongs.
EOF
)"
```

---

## Task 2: a — API handler cleanup

**Files:**
- Modify: `packages/optio-api/src/handlers.ts`
- Modify: `packages/optio-api/src/__tests__/handlers.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/express.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`
- Modify: `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`

**Goal:** Delete API-side state guards and pre-RPC DB lookups in command handlers. Pass raw `id` to engine. Update tests: drop pre-check assertions, add missing engine-reason coverage.

- [ ] **Step 1: Verify pre-edit content**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
sed -n '203,205p' packages/optio-api/src/handlers.ts
sed -n '260,312p' packages/optio-api/src/handlers.ts
```

Expected: lines 203-205 hold the three constants; `launchProcess` (260-277), `cancelProcess` (279-295), `dismissProcess` (297-312) each include a pre-check block calling `findProcessByEitherId` and three/two/one guard `if`s.

- [ ] **Step 2: Edit `handlers.ts` — delete state-set constants**

Delete lines 203-205 entirely:

```typescript
const LAUNCHABLE_STATES = ['idle', 'done', 'failed', 'cancelled'];
const CANCELLABLE_STATES = ['running', 'scheduled'];
const END_STATES = ['done', 'failed', 'cancelled'];
```

- [ ] **Step 3: Edit `handlers.ts` — rewrite `launchProcess`**

Replace the entire body of `launchProcess` (lines 260-277) with:

```typescript
export async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume: boolean = false,
): Promise<LaunchCommandResult> {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);

  const result = await engine.launch({ processId: id, resume });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return launchFail(result.reason);
}
```

Note: `db` dropped from the destructure; `findProcessByEitherId` call removed; three guard `if`s removed; `proc.processId` → raw `id`.

- [ ] **Step 4: Edit `handlers.ts` — rewrite `cancelProcess`**

Replace the entire body of `cancelProcess` (lines 279-295) with:

```typescript
export async function cancelProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CancelCommandResult> {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);

  const result = await engine.cancel({ processId: id });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return cancelFail(result.reason);
}
```

- [ ] **Step 5: Edit `handlers.ts` — rewrite `dismissProcess`**

Replace the entire body of `dismissProcess` (lines 297-312) with:

```typescript
export async function dismissProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<DismissCommandResult> {
  const { database, prefix } = resolveDb(ctx.dbOpts, query);
  const engine = ctx.engineCache.get(database, prefix);

  const result = await engine.dismiss({ processId: id });
  if (result.ok) return { status: 200, body: toResponse(result.process) };
  return dismissFail(result.reason);
}
```

- [ ] **Step 6: Verify `handlers.ts` has no stale state-set or pre-check references**

```bash
grep -n "LAUNCHABLE_STATES\|CANCELLABLE_STATES\|END_STATES" packages/optio-api/src/handlers.ts
grep -n "findProcessByEitherId" packages/optio-api/src/handlers.ts
```

Expected first grep: no matches. Expected second grep: only the import line (1) and 4 call sites in read handlers (`getProcess`, `getProcessTree`, `getProcessLog`, `getProcessTreeLog`) — nothing in `launchProcess`/`cancelProcess`/`dismissProcess`.

- [ ] **Step 7: Edit `handlers.test.ts` — delete pre-check unit tests**

Open `packages/optio-api/src/__tests__/handlers.test.ts` and locate the three `describe` blocks for `launchProcess`, `cancelProcess`, `dismissProcess`. Delete these 7 `it(...)` tests (the ones whose body asserts `expect(engine.X).not.toHaveBeenCalled()`):

In `describe('launchProcess — pre-checks + engine RPC')`:
- `it('404 not-found from pre-check: engine.launch never called', ...)` (around line 168)
- `it('409 not-launchable from pre-check (state=running): engine.launch never called', ...)` (around line 177)
- `it('409 no-resume-support from pre-check: engine.launch never called', ...)` (around line 189)

In `describe('cancelProcess — pre-checks + engine RPC')`:
- `it('404 not-found from pre-check: engine.cancel never called', ...)` (around line 281)
- `it('409 not-cancellable from pre-check (cancellable=false): engine.cancel never called', ...)` (around line 290)
- `it('409 not-cancellable from pre-check (state=idle): engine.cancel never called', ...)` (around line 302)

In `describe('dismissProcess — pre-checks + engine RPC')`:
- `it('404 not-found from pre-check: engine.dismiss never called', ...)` (around line 394)
- `it('409 not-dismissable from pre-check (state=running): engine.dismiss never called', ...)` (around line 403)

(That's 8 — counted again. Double-check and remove all `it` blocks whose body includes `expect(engine.X).not.toHaveBeenCalled()`. The exact count is whatever satisfies that grep predicate — author the edit by reading the file, not by counting.)

Also rename the three `describe` headings: `'launchProcess — pre-checks + engine RPC'` → `'launchProcess — engine RPC'` (and likewise for cancel/dismiss). The "pre-checks" phrasing no longer reflects the code.

- [ ] **Step 8: Edit `handlers.test.ts` — update engine-call assertions**

The remaining "from engine (race)" tests today assert `expect(engine.launch).toHaveBeenCalledWith({ processId: 'p', resume: ... })` where `'p'` is the test fixture's processId. Today the handler resolves `id` (a hex `_id`) → `proc.processId === 'p'` → passes `'p'`. After the rewrite, the handler passes raw `id`. To keep tests valid, choose ONE per call site:

A. Update the test invocation to pass the processId directly: `await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, 'p', false)` — assertion stays `processId: 'p'`.
B. Keep passing the hex `id` and update the assertion to expect that hex: `expect(engine.launch).toHaveBeenCalledWith({ processId: id, resume: ... })`.

Pick approach A everywhere — it exercises the new pass-through semantics most naturally (engine `_resolve` will get the processId form, which is the fast path).

For each remaining engine-RPC test in the launch / cancel / dismiss describes, change `id` → `'p'` (or whatever processId was seeded in the fixture) in the call argument and verify the `toHaveBeenCalledWith` assertion still uses `processId: 'p'`.

- [ ] **Step 9: Edit `handlers.test.ts` — add missing engine-reason coverage**

Inside `describe('launchProcess — engine RPC')`, after the existing tests, add three new `it` blocks. Use the existing `makeMockEngine` and `makeCtxWithMockEngine` helpers (search the file for their definition; they're defined near the top alongside `EngineClient` mocking).

```typescript
  it('409 not-launchable from engine: engine returns ok=false reason=not-launchable', async () => {
    const id = (await seedProcess({ status: { state: 'idle' } }))._id.toString();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'not-launchable' }));
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, 'p');
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'not-launchable',
      message: 'Process is not in a launchable state',
    });
    expect(engine.launch).toHaveBeenCalledTimes(1);
  });

  it('409 no-resume-support from engine: engine returns ok=false reason=no-resume-support', async () => {
    const id = (await seedProcess({ status: { state: 'idle' } }))._id.toString();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'no-resume-support' }));
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, 'p', true);
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'no-resume-support',
      message: 'This task does not support resume',
    });
    expect(engine.launch).toHaveBeenCalledTimes(1);
  });

  it('409 launch-blocked from engine: engine returns ok=false reason=launch-blocked', async () => {
    const id = (await seedProcess({ status: { state: 'idle' } }))._id.toString();
    const engine = makeMockEngine(() => ({ ok: false, reason: 'launch-blocked' }));
    const result = await launchProcess(makeCtxWithMockEngine(db, engine), { prefix: PREFIX }, 'p');
    expect(result.status).toBe(409);
    expect((result as any).body).toEqual({
      reason: 'launch-blocked',
      message: 'Launches matching this filter are currently blocked',
    });
    expect(engine.launch).toHaveBeenCalledTimes(1);
  });
```

The `seedProcess` calls are unused (just there to make the DB non-empty if some helper needs it). If the test pattern doesn't require seeding (since the engine mock returns failure unconditionally), drop them. Read the file to see the established pattern in the existing engine-RPC tests and match it.

- [ ] **Step 10: Run handlers unit tests**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4/packages/optio-api
./node_modules/.bin/vitest run src/__tests__/handlers.test.ts 2>&1 | tail -40
```

Expected: green. All "from pre-check: engine.X never called" tests are gone; engine-RPC tests pass; new "from engine" coverage tests pass.

- [ ] **Step 11: Edit each adapter test file — drop 6 pre-check HTTP tests**

For each of:

- `packages/optio-api/src/adapters/__tests__/fastify.test.ts`
- `packages/optio-api/src/adapters/__tests__/express.test.ts`
- `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`
- `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`

Locate and delete these 6 `it(...)` tests:

1. `it('POST /api/processes/:id/launch — returns 409 for running process', ...)` (or equivalent — search for `reason: 'not-launchable'`)
2. `it('POST /api/processes/:id/launch — returns 404 for nonexistent id', ...)` (search for `reason: 'not-found'` near a `/launch` path)
3. `it('POST /api/processes/:id/cancel — returns 409 for non-cancellable process', ...)` (search for `reason: 'not-cancellable'`)
4. `it('POST /api/processes/:id/cancel — returns 404 for nonexistent id', ...)` (search for `reason: 'not-found'` near `/cancel`)
5. `it('POST /api/processes/:id/dismiss — returns 409 for non-terminal process', ...)` (search for `reason: 'not-dismissable'`)
6. `it('POST /api/processes/:id/dismiss — returns 404 for nonexistent id', ...)` (search for `reason: 'not-found'` near `/dismiss`)

The exact `it` titles vary slightly per adapter. The unifying property: each test depends on the API's `findProcessByEitherId` returning null (or a guard rejecting on state), because the file-level `EngineClient.prototype.X` mocks unconditionally return `{ok: true, ...}`. After this commit those tests would erroneously pass with status 200. They must go.

- [ ] **Step 12: Edit each adapter test file — add one engine-failure roundtrip per adapter**

Inside the existing `describe(...)` block (one per adapter) for the launch/cancel/dismiss endpoints, add ONE new test per adapter that uses `vi.spyOn(EngineClient.prototype, 'launch').mockImplementationOnce(...)` to override the file-level always-`{ok: true}` stub for one call. Insert after the success-path test for `/launch`:

```typescript
  it('POST /api/processes/:id/launch — propagates engine failure (404 reason=not-found)', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();
    vi.spyOn(EngineClient.prototype, 'launch').mockImplementationOnce(async () => ({
      ok: false,
      reason: 'not-found',
    } as any));

    const res = await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/launch`,
    });

    expect(res.statusCode).toBe(404);
    expect(JSON.parse(res.body)).toEqual({
      reason: 'not-found',
      message: 'Process not found',
    });
  });
```

Adapt `app.inject(...)` to each framework's request idiom (express uses supertest, nextjs uses fetch against a handler — match the pattern already in the file). The intent: verify the framework correctly serializes the engine's 404 reason into HTTP body + status.

For express, the equivalent is `request(app).post(...).expect(404)` etc. For nextjs-app/pages, follow whatever invocation pattern the existing success tests use.

Only need ONE such test per adapter (not three for launch/cancel/dismiss separately). It validates the HTTP-roundtrip wiring works for the failure-body shape; per-handler reason/status mapping is fully covered by `handlers.test.ts` units.

- [ ] **Step 13: Run adapter tests**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4/packages/optio-api
./node_modules/.bin/vitest run src/adapters/__tests__/ 2>&1 | tail -40
```

Expected: green. 6 pre-check tests gone per adapter (24 tests total deleted). 1 engine-failure roundtrip per adapter (4 tests added).

- [ ] **Step 14: Run full optio-api test suite + tsc**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4/packages/optio-api
./node_modules/.bin/tsc --noEmit 2>&1 | tail -10
pnpm test 2>&1 | tail -20
```

Expected: tsc clean; tests green.

- [ ] **Step 15: Acceptance grep**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
grep -E "LAUNCHABLE_STATES|CANCELLABLE_STATES|END_STATES" packages/optio-api/src/
```

Expected: no output.

- [ ] **Step 16: Commit**

```bash
git add packages/optio-api/src/handlers.ts \
        packages/optio-api/src/__tests__/handlers.test.ts \
        packages/optio-api/src/adapters/__tests__/fastify.test.ts \
        packages/optio-api/src/adapters/__tests__/express.test.ts \
        packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts \
        packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts
git commit -m "$(cat <<'EOF'
feat(optio-api): delete API-side state guards (engine owns authority)

Phase 4 of the engine-RPC migration. The engine has owned the
launch/cancel/dismiss state-machine rules since phase 2 (mirrored in
_engine_service.py with full failure-reason coverage). The API's
LAUNCHABLE_STATES / CANCELLABLE_STATES / END_STATES constants and
pre-RPC findProcessByEitherId lookups were defense-in-depth that
predates the RPC reply channel.

handlers.ts:
- Delete state-set constants
- Delete pre-check blocks in launchProcess/cancelProcess/dismissProcess
- Pass raw id (hex _id or processId) to engine; engine resolves both

Tests:
- handlers.test.ts: drop 8 "from pre-check: engine.X never called"
  assertions; add missing not-launchable / no-resume-support /
  launch-blocked engine-reason coverage
- adapter tests: drop 24 pre-check HTTP roundtrip tests (6 per
  adapter × 4 adapters) that worked only because findProcessByEitherId
  rejected before the always-{ok:true} EngineClient mock fired; add 4
  engine-failure roundtrip tests (1 per adapter) verifying framework
  wiring of failure body shape
EOF
)"
```

---

## Task 3: b — Engine `_resolve` Python tests

**Files:**
- Create: `packages/optio-core/tests/test_engine_service_resolve.py`

**Goal:** Exhaustive coverage of `EngineService._resolve(id_str)` proving it accepts both ObjectId hex and processId-string inputs and pins the `_id` precedence on collision. Plus one launch-via-each-id-form smoke to prove integration.

- [ ] **Step 1: Inspect existing EngineService test fixture pattern**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
ls packages/optio-core/tests/
grep -l "EngineService\|_resolve" packages/optio-core/tests/*.py | head -3
sed -n '1,40p' packages/optio-core/tests/conftest.py 2>/dev/null
```

Expected: existing `test_engine_service.py` and `conftest.py` show the established fixture pattern (Mongo Docker container, `Optio` instance, `EngineService` setup). Mirror that.

- [ ] **Step 2: Read an existing EngineService test for reference**

```bash
sed -n '1,80p' packages/optio-core/tests/test_engine_service.py
```

Note how the fixture instantiates `EngineService`, how it seeds processes (probably via `Optio.adhoc_define` or direct mongo writes), and what helpers exist.

- [ ] **Step 3: Create `test_engine_service_resolve.py`**

Create file `packages/optio-core/tests/test_engine_service_resolve.py`. The exact import surface and fixture wiring depends on what step 2 revealed; the structure below shows the test cases — adapt the fixture to match the existing pattern.

```python
"""Tests for EngineService._resolve — both id forms + edge cases.

Phase 4 deletes the API's pre-RPC findProcessByEitherId; the engine
becomes solely responsible for resolving either ObjectId hex or
processId string. Pin the resolution semantics here.
"""

from __future__ import annotations

import pytest
from bson import ObjectId

# Import path matches existing test_engine_service.py — adapt as needed:
from optio_core._engine_service import EngineService

# The conftest provides whatever fixture(s) the existing engine tests
# use to spin up an EngineService bound to a real Mongo db. Adapt the
# fixture name below to match.


@pytest.mark.asyncio
async def test_resolve_by_object_id_hex(engine_service, processes_collection):
    """ObjectId hex input → matching doc returned."""
    oid = ObjectId()
    doc = {
        "_id": oid,
        "processId": "alpha",
        "status": {"state": "idle"},
        "name": "test", "params": {}, "metadata": {},
        "depth": 0, "order": 0, "rootId": oid,
        "cancellable": True, "log": [], "progress": {"percent": 0, "message": ""},
        "supportsResume": False,
    }
    await processes_collection.insert_one(doc)

    result = await engine_service._resolve(str(oid))
    assert result is not None
    assert result["_id"] == oid


@pytest.mark.asyncio
async def test_resolve_by_process_id_string(engine_service, processes_collection):
    """Non-hex processId string → matching doc returned."""
    oid = ObjectId()
    doc = {
        "_id": oid,
        "processId": "my-task",
        "status": {"state": "idle"},
        "name": "test", "params": {}, "metadata": {},
        "depth": 0, "order": 0, "rootId": oid,
        "cancellable": True, "log": [], "progress": {"percent": 0, "message": ""},
        "supportsResume": False,
    }
    await processes_collection.insert_one(doc)

    result = await engine_service._resolve("my-task")
    assert result is not None
    assert result["processId"] == "my-task"


@pytest.mark.asyncio
async def test_resolve_unknown_object_id_returns_none(engine_service, processes_collection):
    """Hex input matching no _id and no processId → None."""
    result = await engine_service._resolve(str(ObjectId()))
    assert result is None


@pytest.mark.asyncio
async def test_resolve_unknown_process_id_returns_none(engine_service, processes_collection):
    """Non-hex string with no matching processId → None."""
    result = await engine_service._resolve("nope-not-here")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_empty_string_returns_none(engine_service, processes_collection):
    """Empty string → None on both branches."""
    result = await engine_service._resolve("")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_hex_falls_through_to_process_id(engine_service, processes_collection):
    """24-char hex matching no _id but matching some proc's processId
    field → returns that proc (proves the fallback)."""
    fake_hex = str(ObjectId())  # valid hex, not used as any _id
    real_oid = ObjectId()
    doc = {
        "_id": real_oid,
        "processId": fake_hex,  # processId happens to be a valid hex string
        "status": {"state": "idle"},
        "name": "test", "params": {}, "metadata": {},
        "depth": 0, "order": 0, "rootId": real_oid,
        "cancellable": True, "log": [], "progress": {"percent": 0, "message": ""},
        "supportsResume": False,
    }
    await processes_collection.insert_one(doc)

    result = await engine_service._resolve(fake_hex)
    assert result is not None
    assert result["_id"] == real_oid
    assert result["processId"] == fake_hex


@pytest.mark.asyncio
async def test_resolve_collision_id_wins(engine_service, processes_collection):
    """When the input hex matches one proc's _id AND another proc's
    processId field, _id wins (current behavior; pin it)."""
    oid = ObjectId()
    proc_a = {
        "_id": oid,
        "processId": "a-task",
        "status": {"state": "idle"},
        "name": "A", "params": {}, "metadata": {},
        "depth": 0, "order": 0, "rootId": oid,
        "cancellable": True, "log": [], "progress": {"percent": 0, "message": ""},
        "supportsResume": False,
    }
    other_oid = ObjectId()
    proc_b = {
        "_id": other_oid,
        "processId": str(oid),  # B's processId equals A's _id hex
        "status": {"state": "idle"},
        "name": "B", "params": {}, "metadata": {},
        "depth": 0, "order": 0, "rootId": other_oid,
        "cancellable": True, "log": [], "progress": {"percent": 0, "message": ""},
        "supportsResume": False,
    }
    await processes_collection.insert_many([proc_a, proc_b])

    result = await engine_service._resolve(str(oid))
    assert result is not None
    assert result["_id"] == oid  # _id branch wins; A returned, not B
    assert result["processId"] == "a-task"


# --- Integration smoke: launch resolves both id forms end-to-end ---

@pytest.mark.asyncio
async def test_launch_accepts_object_id_hex(engine_service, processes_collection):
    """EngineService.launch resolves the hex _id form via _resolve."""
    from optio_core._generated.engine import LaunchParams
    oid = ObjectId()
    doc = {
        "_id": oid,
        "processId": "launch-by-hex",
        "status": {"state": "idle"},
        "name": "test", "params": {}, "metadata": {},
        "depth": 0, "order": 0, "rootId": oid,
        "cancellable": True, "log": [], "progress": {"percent": 0, "message": ""},
        "supportsResume": False,
    }
    await processes_collection.insert_one(doc)

    # The launch will fail with not-launchable or succeed depending on
    # whether the underlying executor knows about a task named
    # "launch-by-hex". We only care that _resolve returned the doc —
    # i.e. the result is NOT not-found.
    result = await engine_service.launch(LaunchParams(processId=str(oid), resume=False))
    # The discriminated-union access pattern depends on the generated
    # Pydantic shape; adapt to whatever existing tests use.
    assert result.root.reason != "not-found" if result.root.ok is False else True


@pytest.mark.asyncio
async def test_launch_accepts_process_id_string(engine_service, processes_collection):
    """EngineService.launch resolves the processId string form via _resolve."""
    from optio_core._generated.engine import LaunchParams
    oid = ObjectId()
    doc = {
        "_id": oid,
        "processId": "launch-by-pid",
        "status": {"state": "idle"},
        "name": "test", "params": {}, "metadata": {},
        "depth": 0, "order": 0, "rootId": oid,
        "cancellable": True, "log": [], "progress": {"percent": 0, "message": ""},
        "supportsResume": False,
    }
    await processes_collection.insert_one(doc)

    result = await engine_service.launch(LaunchParams(processId="launch-by-pid", resume=False))
    assert result.root.reason != "not-found" if result.root.ok is False else True
```

If the existing fixtures don't expose `engine_service` and `processes_collection` directly, write fixtures here at the top of the file using whatever lower-level helpers `conftest.py` provides. Read the existing `test_engine_service.py` setup carefully and match it.

Note on the launch-smoke assertions: the discriminated-union access pattern depends on the generated Pydantic shape. If the existing tests use `result.root.ok` etc., match that. If they use a different access (e.g., direct attribute, model_dump), match that. The intent of the assertion is "result is NOT not-found" — express it however the codebase expresses it.

- [ ] **Step 4: Run the new test file**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4/packages/optio-core
pytest tests/test_engine_service_resolve.py -v 2>&1 | tail -40
```

Expected: 9 tests pass. If any fails because of fixture mismatch, fix and rerun.

- [ ] **Step 5: Run full optio-core suite to ensure no regression**

```bash
pytest 2>&1 | tail -10
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
git add packages/optio-core/tests/test_engine_service_resolve.py
git commit -m "$(cat <<'EOF'
test(optio-core): exhaustive coverage for EngineService._resolve

Phase 4 deletes the API's pre-RPC findProcessByEitherId so the engine
is solely responsible for resolving either ObjectId hex or processId
string into a process doc. Pin the semantics with 7 unit tests
(hex/string/miss/empty/fallback/collision) plus 2 integration smoke
tests proving EngineService.launch accepts both id forms end-to-end.
EOF
)"
```

---

## Task 4: c — Adversarial interop matrix

**Files:**
- Modify: `packages/optio-demo/interop/run-http.ts`

**Goal:** Add three scenarios filling the failure-reason matrix and validating the (a-prime) cancel-during-cancel behavior change. Reuse existing `withTimeout` / `fail` / `ok` / `waitForState` helpers.

- [ ] **Step 1: Read current `run-http.ts` to understand scenario shape**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
sed -n '1,50p' packages/optio-demo/interop/run-http.ts
sed -n '95,170p' packages/optio-demo/interop/run-http.ts
```

Note the helper signatures (`withTimeout(name, fn)`, `fail(name, msg)`, `ok(name, info?)`, `waitForState(states)`), the HTTP request helper (likely `fetch` against the in-process fastify), and the existing `http-cancel-success` shape (launch → wait for non-terminal → cancel → assert).

- [ ] **Step 2: Read `run.ts` to understand direct clamator client wiring (for blockLaunches)**

```bash
sed -n '1,60p' packages/optio-demo/interop/run.ts
grep -n "engine\|EngineClient\|clamator\|blockLaunches\|RpcClient" packages/optio-demo/interop/run.ts | head -20
```

Note how `run.ts` constructs the clamator `EngineClient`. The launch-blocked scenario in `run-http.ts` will need similar wiring to call `engine.blockLaunches(...)` directly (not over HTTP).

- [ ] **Step 3: Add `http-launch-no-resume-support` scenario**

Choose insertion point: immediately after the existing `http-launch-not-found` scenario (around line 120). Add:

```typescript
    // Seed a process whose task does not support resume, in a launchable
    // state. The opencode-demo task does not declare supports_resume=True
    // (TaskInstance default is False), so seeding any state=idle proc with
    // supportsResume=false reproduces the failure.
    await withTimeout('http-launch-no-resume-support', async () => {
      const seedId = await seedProcess({
        status: { state: 'idle' },
        supportsResume: false,
      });
      const r = await postLaunch(seedId, { resume: true });
      if (r.status !== 409) return fail('http-launch-no-resume-support', `expected 409, got ${r.status} ${JSON.stringify(r.body)}`);
      if (r.body?.reason !== 'no-resume-support')
        return fail('http-launch-no-resume-support', `expected reason 'no-resume-support', got ${r.body?.reason}`);
      ok('http-launch-no-resume-support');
    });
```

If a `seedProcess(...)` helper does not exist in `run-http.ts`, define one at the top of the file using a direct mongo write (using whatever `MongoClient` is already wired). Pattern (adapt to existing imports):

```typescript
async function seedProcess(overrides: Record<string, unknown>): Promise<string> {
  const oid = new ObjectId();
  const doc = {
    _id: oid,
    processId: `seeded-${oid.toString()}`,
    name: 'Seeded test proc',
    status: { state: 'idle' },
    progress: { percent: 0, message: '' },
    log: [],
    depth: 0, order: 0,
    rootId: oid,
    cancellable: true,
    metadata: {},
    supportsResume: false,
    ...overrides,
  };
  await mongoCollection.insertOne(doc);
  return oid.toString();
}
```

The `postLaunch(id, body)` helper similarly may already exist; if not, add a thin wrapper around `fetch(\`${BASE}/api/processes/${id}/launch\`, { method: 'POST', body: JSON.stringify(body), headers: ... })`.

- [ ] **Step 4: Add `http-launch-launch-blocked` scenario**

Insert after the no-resume-support scenario:

```typescript
    // Launch a persistent block matching the proc's metadata, then attempt
    // to launch the proc. Engine returns launch-blocked. Cleanup with
    // unblockLaunches.
    await withTimeout('http-launch-launch-blocked', async () => {
      const seedId = await seedProcess({
        status: { state: 'idle' },
        metadata: { tag: 'block-test' },
      });
      // Use direct clamator client (engine RPC) to install the block.
      // The `engineClient` variable is constructed at the top of the file
      // (mirror run.ts wiring if not yet present).
      await engineClient.blockLaunches({
        launchFilter: { tag: 'block-test' },
        reason: 'phase-4 interop test',
      });
      try {
        const r = await postLaunch(seedId, {});
        if (r.status !== 409) return fail('http-launch-launch-blocked', `expected 409, got ${r.status} ${JSON.stringify(r.body)}`);
        if (r.body?.reason !== 'launch-blocked')
          return fail('http-launch-launch-blocked', `expected reason 'launch-blocked', got ${r.body?.reason}`);
        ok('http-launch-launch-blocked');
      } finally {
        await engineClient.unblockLaunches({ launchFilter: { tag: 'block-test' } });
      }
    });
```

If `engineClient` is not yet wired into `run-http.ts`, import and construct it the same way `run.ts` does. Place the construction at the top of the `main` async function (or wherever the fastify base URL is set up) so cleanup is straightforward.

- [ ] **Step 5: Add `http-cancel-during-cancel` (race) scenario**

Insert after `http-cancel-not-found` (around line 170). Use the existing pattern that launches `opencode-demo` and waits for it to enter `running` (or any non-terminal):

```typescript
    // Validates phase-4 (a-prime) state-set SoT cleanup. Engine pre-check
    // now uses CANCELLABLE_STATES = {scheduled, running} (matches lifecycle).
    // Re-cancel on a proc that is already cancel_requested returns 409
    // not-cancellable instead of the previous misleading 200 no-op.
    await withTimeout('http-cancel-during-cancel', async () => {
      // Reset proc to a launchable state.
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      const launchRes = await postLaunch(PROC, {});
      if (launchRes.status !== 200)
        return fail('http-cancel-during-cancel', `pre-launch failed: ${launchRes.status} ${JSON.stringify(launchRes.body)}`);

      // Wait until the proc is in a cancellable state.
      await waitForState(['scheduled', 'running']);

      const cancel1 = await postCancel(PROC);
      if (cancel1.status !== 200)
        return fail('http-cancel-during-cancel', `cancel #1 expected 200, got ${cancel1.status} ${JSON.stringify(cancel1.body)}`);

      // Immediately re-cancel, no async yield. The first cancel transitioned
      // the proc to cancel_requested (or beyond); the second should be
      // rejected by the engine pre-check.
      const cancel2 = await postCancel(PROC);
      if (cancel2.status !== 409)
        return fail('http-cancel-during-cancel', `cancel #2 expected 409, got ${cancel2.status} ${JSON.stringify(cancel2.body)}`);
      if (cancel2.body?.reason !== 'not-cancellable')
        return fail('http-cancel-during-cancel', `cancel #2 expected reason 'not-cancellable', got ${cancel2.body?.reason}`);
      ok('http-cancel-during-cancel');
    });
```

`PROC` is the `opencode-demo` constant defined at the top of `run-http.ts` (line 13). `postCancel(id)` may already exist as a helper; if not, add it.

Race assumption: opencode-demo fails fast in the interop env (no SSH host) but takes long enough for the `cancel_requested` window to be observable. If the race is flaky in practice, fall back to polling for `cancel_requested` between cancel #1 and cancel #2 (`waitForState(['cancel_requested', 'cancelling'])` with a short timeout).

- [ ] **Step 6: Run the interop suite**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
make test-interop 2>&1 | tail -60
```

Expected: green. New scenarios appear in the `[scenario] ... ok` log lines. Total scenario count grew by 3.

If a scenario flakes intermittently:
- For `http-cancel-during-cancel`: switch to explicit `waitForState(['cancel_requested', 'cancelling'])` between the two cancels.
- For `http-launch-launch-blocked`: ensure the cleanup `unblockLaunches` call runs even on failure (the `try/finally` handles this).
- For `http-launch-no-resume-support`: if `seedProcess` collides with the demo registry, use a process_id that is not in the demo task list.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-demo/interop/run-http.ts
git commit -m "$(cat <<'EOF'
test(optio-demo): adversarial interop scenarios for phase-4 matrix

Phase 4 deletes API-side pre-checks; bugs in engine validation now
surface as wrong HTTP status codes. Add three scenarios filling the
failure-reason matrix not yet covered by existing run-http.ts:

- http-launch-no-resume-support: seeded proc with supportsResume=false,
  POST /launch with resume=true → 409 no-resume-support
- http-launch-launch-blocked: persistent launch block via direct
  clamator client → POST /launch → 409 launch-blocked → cleanup
- http-cancel-during-cancel (race): cancel a running proc, immediately
  cancel again → 409 not-cancellable. Validates phase-4 (a-prime)
  state-set SoT cleanup behavior change (no more misleading 200 no-op).
EOF
)"
```

---

## Task 5: d — Docs

**Files:**
- Modify: `packages/optio-api/AGENTS.md`
- Modify: `packages/optio-api/README.md`
- Modify: `docs/2026-05-08-engine-rpc-migration-design.md`

**Goal:** Reflect the post-phase-4 architecture in package docs and the parent migration spec.

- [ ] **Step 1: Read current `optio-api/AGENTS.md` to find the State guards block + insertion point for the rule statement**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
grep -n "State guards\|LAUNCHABLE\|CANCELLABLE\|END_STATES" packages/optio-api/AGENTS.md
sed -n '1,40p' packages/optio-api/AGENTS.md
```

Identify (a) the exact heading + bounds of the "State guards enforced by command handlers" block (or however it's titled), and (b) a suitable insertion point for the architectural rule at the top — typically just after the `# optio-api — LLM Reference` heading and before the first `## Package` section.

- [ ] **Step 2: Edit `optio-api/AGENTS.md` — add the architectural rule**

Insert immediately after the `# optio-api — LLM Reference` heading, as the first content section:

```markdown
## Architectural rule

**Engine owns all writes.** This package reads MongoDB directly for queries
(REST GETs, SSE streams, widget proxy) and forwards every mutating operation
to the engine via clamator RPC. The API enforces no state machine, no
policy, no command-acceptance rules. The engine is the single source of
truth for what commands are allowed and what state results.

---
```

- [ ] **Step 3: Edit `optio-api/AGENTS.md` — delete the "State guards" block**

Delete the section identified in step 1 (the block listing LAUNCHABLE_STATES / CANCELLABLE_STATES / END_STATES with prose explaining when each guard rejects). Reading the file to find exact bounds is mandatory; do not delete content beyond the block.

If the section title is something like `### State guards enforced by command handlers`, the block typically ends at the next `###` or `##` heading.

- [ ] **Step 4: Read `optio-api/README.md` REST Endpoints table**

```bash
grep -n "REST Endpoints\|launch\|cancel\|dismiss\|409\|state" packages/optio-api/README.md | head -30
```

Locate the REST Endpoints table or section. Identify per-endpoint description prose that asserts the API does state validation (e.g., "Returns 409 if the process is not in a launchable state").

- [ ] **Step 5: Edit `optio-api/README.md` — scrub state-validation language**

For each command endpoint description (launch / cancel / dismiss), rewrite the failure-mode prose to attribute the rejection to the engine. Example transformations:

- "Returns 409 if the process is not in a launchable state" →
  "Returns 409 if the engine rejects the launch. Reasons enumerated by `LaunchFailureReason`."
- "Returns 404 if the process does not exist" →
  "Returns 404 if the engine cannot resolve the process id."
- "Returns 409 if the process is not cancellable" →
  "Returns 409 if the engine rejects the cancel. Reasons enumerated by `CancelFailureReason`."
- "Returns 409 if the process is not in a terminal state" →
  "Returns 409 if the engine rejects the dismiss. Reasons enumerated by `DismissFailureReason`."

Adapt to the actual prose in the file. Read the file before editing.

- [ ] **Step 6: Edit parent design spec — record phase-4 actual scope**

Open `docs/2026-05-08-engine-rpc-migration-design.md`. Append a new sub-section under §11 (or extend §11 with a phase-4 note):

```markdown
### Phase-4 actual scope (2026-05-XX)

Phase 4 shipped as designed in `docs/2026-05-10-engine-rpc-migration-phase-4-design.md`. Two notes worth recording:

- **No engine migration required.** The §8.4 phase-4 deliverable text frames pre-checks as moving from API to engine; in practice phase 2 already mirrored them in `_engine_service.py` (with the comment "the engine — not the API — owns the rule"). Phase 4 is therefore deletion-only on the API side.
- **Bonus: state-set SoT cleanup.** `_engine_service.py` redefined `LAUNCHABLE_STATES` / `CANCELLABLE_STATES` / `DISMISSABLE_STATES` locally instead of importing from `state_machine.py`. The local `CANCELLABLE_STATES` included `cancel_requested`, which created a divergence with `lifecycle._handle_cancel`'s guard — re-cancel returned a misleading 200 no-op. Commit (a-prime) consolidates on `state_machine.py` as the single source of truth across `optio-core`; side effect: re-cancel correctly returns 409 `not-cancellable`. `lifecycle.py` lines 780 and 929 also gained named-import replacements for anonymous set literals.
- **Follow-up: optio-ui state-set duplication.** `optio-ui/src/process-state.ts` redefines `LAUNCHABLE_STATES` and `ACTIVE_STATES` locally (different language; no Python import path). Out of phase-4 scope per parent §2 ("optio-ui — no logic change"). The proper fix is to promote shared state sets to `optio-contracts` runtime exports; tracked separately.
```

Update the §6 phase-4 entries — wherever the parent spec lists deliverables to be done in phase 4 — to mark them done with commit refs once you have them. Use placeholders like `done in commit <a>` etc., to be filled in after committing each task. Or leave the commit refs unfilled in this doc-edit commit and amend after the merge.

For the present commit, use the commit hashes from this branch's `git log` so far (the spec commit `f283528`, plus a-prime, a, b, c hashes once they've landed). Run `git log --oneline -10` to discover them. If a-prime/a/b/c have already been committed by the time this task runs, the hashes are available; substitute them.

- [ ] **Step 7: Verify build still green**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
pnpm -r build --filter optio-api --filter optio-contracts --filter optio-core 2>&1 | tail -10
```

Expected: green. (Doc-only commit but worth a sanity check.)

- [ ] **Step 8: Commit**

```bash
git add packages/optio-api/AGENTS.md \
        packages/optio-api/README.md \
        docs/2026-05-08-engine-rpc-migration-design.md
git commit -m "$(cat <<'EOF'
docs(optio-api): record post-phase-4 architecture

- AGENTS.md: add architectural rule statement at top (engine owns
  all writes); delete the State guards block (the guards no longer
  exist).
- README.md: REST Endpoints prose stops asserting the API does state
  validation. Failure modes attributed to the engine, with reason
  enums named.
- Parent design spec §11: append phase-4 actual-scope note covering
  (1) no engine migration was required (phase 2 already mirrored the
  rule), (2) bonus state-set SoT cleanup, (3) optio-ui follow-up.
EOF
)"
```

---

## Final acceptance

Run the full acceptance gate from the design spec §4:

- [ ] **Step 1: Acceptance greps**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
grep -E "LAUNCHABLE_STATES|CANCELLABLE_STATES|END_STATES" packages/optio-api/src/
echo "---"
grep -rE "LAUNCHABLE_STATES|CANCELLABLE_STATES|DISMISSABLE_STATES" packages/optio-core/src/ | grep -v _generated
```

Expected: first grep returns nothing. Second grep returns matches only in `state_machine.py` (the canonical defs) and in `_engine_service.py` import line + 3 usage lines.

- [ ] **Step 2: Test sweep**

```bash
cd /home/csillag/deai/optio/.claude/worktrees/csillag+rpc-migration-phase-4
cd packages/optio-api && pnpm test 2>&1 | tail -10 && cd ../..
cd packages/optio-core && pytest 2>&1 | tail -10 && cd ../..
make test-interop 2>&1 | tail -20
```

Expected: all green.

- [ ] **Step 3: Build sweep**

```bash
pnpm -r build --filter optio-api --filter optio-contracts --filter optio-core 2>&1 | tail -10
```

Expected: green. (optio-ui pre-existing build break — `@quaesitor-textus/*` missing — is out of phase-4 scope.)

- [ ] **Step 4: Commit log review**

```bash
git log --oneline main..HEAD
```

Expected: 6 commits (spec + a-prime + a + b + c + d).
