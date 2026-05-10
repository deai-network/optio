# 2026-05-10 — Engine RPC migration, phase 4 design

**Status:** Design.
**Parent spec:** `docs/2026-05-08-engine-rpc-migration-design.md` (see §11 phase-3 scope addendum and §8.4 phase-4 narrowed scope).
**Phase 1–3 designs:** `docs/2026-05-08-engine-rpc-migration-phase-1-design.md`, `…-phase-2-design.md`, `…-phase-3-design.md`.

This document supplements the parent spec by recording the phase-4 commit sequence, scope decisions, and a small bonus cleanup that surfaced during scope verification (state-set single source of truth). Everything not addressed here defers to the parent spec and addendum.

## 1. Scope

Phase 4 finishes the architectural rule from parent §1.3: **Engine owns all writes; the API is a pure RPC translator.** Phase 3 already swapped the wire (HTTP → clamator) and shipped the body-shape flip and `publisher.ts` deletion. Phase 4 deletes the API-side defense-in-depth pre-checks and adversarially tests the engine's authority over the failure-reason matrix.

### What ships

- **API:** `packages/optio-api/src/handlers.ts` — delete `LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES` constants. Delete pre-RPC `findProcessByEitherId(...)` calls and state/`cancellable`/`supportsResume` guard blocks in `launchProcess`, `cancelProcess`, `dismissProcess`. Pass raw `id` string to `engine.launch/cancel/dismiss` instead of pre-resolved `proc.processId`. Drop unused `db` local from `resolveDb` destructure in command handlers.
- **API tests:** delete the 7 "from pre-check: engine.X never called" unit tests in `handlers.test.ts`. Add 3 missing `from engine` reason coverage tests (launch: `not-launchable`, `no-resume-support`, `launch-blocked`). Per adapter (4 files), drop 6 pre-check HTTP tests (3 state-list — `for-running` launch / `for-non-cancellable` cancel / `for-non-terminal` dismiss — plus 3 `for-nonexistent-id` for launch/cancel/dismiss, all currently passing only because the API pre-check rejects before the always-`ok:true` mock fires); keep one engine-returned-failure roundtrip per adapter to verify framework wiring.
- **Engine:** `packages/optio-core/src/optio_core/_engine_service.py` — replace local `LAUNCHABLE_STATES` / `CANCELLABLE_STATES` / `DISMISSABLE_STATES` constants with imports from `state_machine.py`. Delete the now-redundant local definitions. Side effect: engine `cancel_requested` divergence vanishes — re-cancel returns `not-cancellable` instead of a misleading 200 no-op.
- **Engine:** `packages/optio-core/src/optio_core/lifecycle.py` — replace anonymous state-set literals at lines 780 (`{"scheduled","running","cancel_requested","cancelling"}` → `ACTIVE_STATES`) and 929 (`{"done","failed","cancelled"}` → `END_STATES`).
- **Engine tests:** new `packages/optio-core/tests/test_engine_service_resolve.py` covering `EngineService._resolve(id_str)` exhaustively (hex `_id`, `processId` string, miss, ambiguity edge cases) plus one launch invocation per id form to prove integration.
- **Interop:** `packages/optio-demo/interop/run-http.ts` — add `http-launch-no-resume-support`, `http-launch-launch-blocked`, and `http-cancel-during-cancel` (race) scenarios. Fill the failure-reason matrix and validate the engine SoT cleanup behavior change.
- **Docs:** `packages/optio-api/AGENTS.md` — delete the "State guards enforced by command handlers" block; add the architectural rule statement at the top. `packages/optio-api/README.md` — REST Endpoints table descriptions stop suggesting the API does state validation. Parent spec phase-4 entries marked done with commit refs.

### What does not ship

- Engine-side migration of API pre-checks (engine already mirrored them in phase 2; no migration required — only deletion of API copies).
- Phase 5 (legacy stream + `CommandConsumer` + `on_command(...)` removal).
- `optio-ui/src/process-state.ts` state-set duplication — out of phase-4 scope per parent §2 ("optio-ui — no logic change"). Flagged as follow-up: promote shared state sets to `optio-contracts` runtime exports, separate spec.
- Refactoring `process-id-resolver.ts` — kept; read handlers still consume it.
- Refactoring `resolveDb` — kept; command handlers still need `database, prefix` for engine cache lookup.

## 2. Phase-4 decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Engine pre-check coverage vs API pre-check coverage | Verified 1:1 in §2 of brainstorm: engine `_engine_service.py:96-152` mirrors every API guard. No engine-side migration required — phase 2 already moved the rule. |
| 2 | Stage as one commit or split | Five commits. (a-prime) state-set SoT cleanup — separable engine cleanup. (a) API handler cleanup + test updates — single atomic deletion. (b) engine `_resolve` Python tests — separable, different language and package. (c) adversarial interop matrix. (d) docs. Mirrors phase-3 cadence but smaller. |
| 3 | Cancel-on-`cancel_requested` behavior | Tighten engine: `_engine_service.py` imports `CANCELLABLE_STATES = {"scheduled", "running"}` from `state_machine.py`. Re-cancel returns 409 `not-cancellable`. Restores consistency between engine pre-check and engine lifecycle guard (currently divergent: pre-check passes, lifecycle no-ops, returning 200 with no state change — misleading success). Conservative; matches old API behavior. |
| 4 | State-set single source of truth | `state_machine.py` is canonical. Phase-4 commit (a-prime) imports from there in `_engine_service.py` and `lifecycle.py:780,929`. Search showed no other Python violations. `handlers.ts` constants disappear in commit (a). `optio-ui` violation deferred (cross-language; needs `optio-contracts` runtime exports). |
| 5 | Adapter test failure coverage | Drop 24 per-adapter pre-check assertions (6 per adapter × 4 adapters): 3 state-list + 3 nonexistent-id. All currently work only because API rejects before the always-`{ok: true}` engine mock fires. Keep one engine-failure roundtrip per adapter to verify framework wiring (e.g., 404 from engine returns 404 to client with right body shape). Failure-mapping fully covered at handlers.test.ts unit level + interop e2e. |
| 6 | `id` pass-through to engine | API passes raw `id` to `engine.launch/cancel/dismiss`. Engine `_resolve` accepts ObjectId hex or `processId` string (verified at `_engine_service.py:117-126`). Phase-4 commit (b) tests `_resolve` exhaustively. |
| 7 | Adversarial interop scope | Static scenarios for every failure reason (deterministic, exhaustive). Plus one race scenario: `http-cancel-during-cancel`, validating the (a-prime) behavior change. Skip race scenarios for other reasons (low value once static covers them). |
| 8 | Architectural rule statement wording | Parent spec §1.3 wording, lightly trimmed. Added to top of `optio-api/AGENTS.md`. |

## 3. Commit sequence

Five commits. Each leaves the tree green.

### Commit a-prime — State-set SoT cleanup (engine)

Files:

- `packages/optio-core/src/optio_core/_engine_service.py`:
  - Delete lines 33–37 (the comment plus `LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `DISMISSABLE_STATES` local constants).
  - Add to module-level imports: `from optio_core.state_machine import LAUNCHABLE_STATES, CANCELLABLE_STATES, DISMISSABLE_STATES`.
  - Symbol references at lines 100, 133, 146 unchanged (names match canonical exports).
- `packages/optio-core/src/optio_core/lifecycle.py`:
  - Line 28 import widens: `from optio_core.state_machine import ACTIVE_STATES, CANCELLABLE_STATES, END_STATES`.
  - Line 780: `non_terminal = {"scheduled","running","cancel_requested","cancelling"}` → `non_terminal = ACTIVE_STATES`.
  - Line 929: `if proc["status"]["state"] not in {"done","failed","cancelled"}:` → `... not in END_STATES:`.

Tests:

- No new tests in this commit. Existing `optio-core/tests/test_state_machine.py` continues to pin canonical exports. Existing `EngineService` unit tests in `test_engine_service.py` validate that `cancel_requested` is now rejected (or, if a current test asserts the misleading 200, update it to expect `not-cancellable`).

Acceptance: `cd packages/optio-core && pytest` green. `grep -E "LAUNCHABLE_STATES|CANCELLABLE_STATES|DISMISSABLE_STATES" packages/optio-core/src/` returns matches only in `state_machine.py`.

### Commit a — API handler cleanup

Files:

- `packages/optio-api/src/handlers.ts`:
  - Delete lines 203–205 (`LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `END_STATES`).
  - In `launchProcess` (lines 260–277): remove pre-check block (lines 269–272: `findProcessByEitherId` lookup + 3 guards). Replace `proc.processId` argument to `engine.launch` with raw `id`. Drop `db` from `resolveDb` destructure.
  - In `cancelProcess` (lines 279–295): remove pre-check block (lines 287–290). Replace `proc.processId` with `id`. Drop `db`.
  - In `dismissProcess` (lines 297–312): remove pre-check block (lines 305–307). Replace `proc.processId` with `id`. Drop `db`.
  - `findProcessByEitherId` import (line 4) stays — read handlers (`getProcess`, `getProcessTree`, `getProcessLog`, `getProcessTreeLog`) consume it.
- `packages/optio-api/src/__tests__/handlers.test.ts`:
  - Delete 7 "from pre-check: engine.X never called" tests (lines 168–198, 281–311, 394–412 ranges).
  - Update `engine.launch / cancel / dismiss` `toHaveBeenCalledWith` assertions where the handler test passes a hex `id` — assertion now expects the raw `id`, not `proc.processId`. (Or update the test fixture to pass the processId form so the assertion stays unchanged.)
  - Add three new tests covering missing engine-side reasons:
    - `409 not-launchable from engine: pre-check absent, engine returns ok=false reason=not-launchable`
    - `409 no-resume-support from engine: pre-check absent, engine returns ok=false reason=no-resume-support`
    - `409 launch-blocked from engine: pre-check absent, engine returns ok=false reason=launch-blocked`
- `packages/optio-api/src/adapters/__tests__/{fastify,express,nextjs-app,nextjs-pages}.test.ts` (each):
  - Delete the 6 pre-check HTTP tests:
    - 3 state-list: `launch — for-running-proc`, `cancel — for-non-cancellable-proc`, `dismiss — for-non-terminal-proc`.
    - 3 nonexistent-id: `launch — for-nonexistent-id`, `cancel — for-nonexistent-id`, `dismiss — for-nonexistent-id`. All depend on the API's `findProcessByEitherId` returning null pre-engine; the always-`{ok:true}` engine prototype mock would otherwise return 200.
  - Keep one engine-returned-failure roundtrip test per adapter: stub `EngineClient.prototype.launch` (or cancel/dismiss) per-test to return `{ok: false, reason: 'not-found'}`, assert HTTP 404 + body `{reason: 'not-found', message: 'Process not found'}`. Verifies framework wiring at HTTP level.

Tests: `cd packages/optio-api && pnpm test` green.

Acceptance: `grep -E "LAUNCHABLE_STATES|CANCELLABLE_STATES|END_STATES" packages/optio-api/src/` returns nothing.

### Commit b — Engine `_resolve` Python tests

Files:

- New `packages/optio-core/tests/test_engine_service_resolve.py`:
  - Import `EngineService` and a stub `Optio` exposing `_config.mongo_db` and `_config.prefix`.
  - Coverage matrix:
    1. Existing proc with `_id`=hex_X, `processId`="alpha"; lookup by hex_X → returns doc.
    2. Same proc; lookup by "alpha" → returns doc.
    3. Lookup by `new ObjectId().hex` (not in DB) AND no proc has that string in `processId` → returns None.
    4. Lookup by random non-hex string with no matching `processId` → returns None.
    5. Lookup by empty string → returns None.
    6. Lookup by 24-char hex that matches some proc's `processId` field but no `_id` → returns the doc (proves fallback).
    7. Collision: ObjectId hex_Y equals proc-A's `_id` AND equals proc-B's `processId` → returns proc-A (`_id` wins; pin current behavior).
  - Plus integration smoke per id form, one method:
    - `EngineService.launch({processId: hex_X, resume: false})` → resolves to doc and returns `ok=True` (or appropriate failure if test fixture state forbids).
    - `EngineService.launch({processId: "alpha", resume: false})` → same.

Setup: existing `packages/optio-core/tests/conftest.py` provides Mongo via Docker (per CLAUDE.md MongoDB rule).

Tests: `cd packages/optio-core && pytest tests/test_engine_service_resolve.py` green.

### Commit c — Adversarial interop matrix

Files:

- `packages/optio-demo/interop/run-http.ts`:
  - New scenario `http-launch-no-resume-support`. Setup: seed proc whose task definition has `supportsResume=false`, in terminal state; POST `/launch` with body `{resume: true}`. Assert 409 + `reason: 'no-resume-support'`.
  - New scenario `http-launch-launch-blocked`. Setup: terminal-state proc; call `engine.blockLaunches(...)` via direct clamator client (re-using `run.ts` clamator-client wiring) with a filter matching the proc's metadata; POST `/launch`. Assert 409 + `reason: 'launch-blocked'`. Cleanup with `engine.unblockLaunches(...)`.
  - New scenario `http-cancel-during-cancel` (race). Setup: launch a long-running task (opencode-demo task already used by `http-cancel-success`); POST `/cancel` #1 → 200 with state `cancel_requested`; immediately POST `/cancel` #2 → 409 + `reason: 'not-cancellable'`. No async yield between the two POSTs.
- Re-use existing `withTimeout` / `fail` / `ok` helpers and `waitForState`.

Tests: `make test-interop` green including the three new scenarios.

### Commit d — Docs

Files:

- `packages/optio-api/AGENTS.md`:
  - Delete the "State guards enforced by command handlers" block (the LAUNCHABLE/CANCELLABLE/END table and prose).
  - Add at top of file (after the package header / TOC):
    > ## Architectural rule
    >
    > **Engine owns all writes.** This package reads MongoDB directly for queries (REST GETs, SSE streams, widget proxy) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. The engine is the single source of truth for what commands are allowed and what state results.
- `packages/optio-api/README.md`:
  - REST Endpoints table: scrub language suggesting the API does state validation. Each command endpoint description ends with: "Returns 409 if engine rejects (reasons enumerated by `LaunchFailureReason` / `CancelFailureReason` / `DismissFailureReason`)."
- `docs/2026-05-08-engine-rpc-migration-design.md`:
  - §11 addendum (or new sub-section) records phase-4 actual scope: pre-checks were never migrated to engine — phase 2 already mirrored them with engine as canonical owner; phase 4 deleted API copies. State-set SoT cleanup (a-prime) is bonus scope. Mark phase-4 deliverables `done` with commit refs.

Tests: `pnpm -r build` green for optio-api / optio-contracts / optio-core. (optio-ui pre-existing build break out of scope; tracked separately.)

## 4. Acceptance criteria

- `grep -E "LAUNCHABLE_STATES|CANCELLABLE_STATES|END_STATES" packages/optio-api/src/` returns nothing.
- `grep -E "LAUNCHABLE_STATES|CANCELLABLE_STATES|DISMISSABLE_STATES" packages/optio-core/src/` returns matches only in `state_machine.py`.
- `cd packages/optio-api && pnpm test` green.
- `cd packages/optio-core && pytest` green.
- `make test-interop` green including new scenarios.
- `pnpm -r build --filter optio-api --filter optio-contracts` green.

## 5. Risks

1. **Engine `cancel_requested` set tightening (a-prime).** Any external caller calling cancel twice expecting 200 will now get 409. Mitigation: behavior matches the long-standing API contract; surfaces the inconsistency rather than hiding it. Documented in commit message.
2. **Adapter test coverage drop.** Removing 16 per-adapter pre-check tests reduces HTTP-roundtrip failure assertions. Mitigation: handlers.test.ts unit + interop e2e + retained one-failure-per-adapter cover it; coverage shifts layers, not lost.
3. **Race-test flakiness.** `http-cancel-during-cancel` depends on getting cancel #2 in before lifecycle progresses past `cancel_requested`. Mitigation: opencode-demo task fails fast in interop env; cancel #2 sent without async yield between the two HTTP calls. If still flaky, fall back to explicit poll-until-state.
4. **Hex-id vs processId pass-through.** Phase 4 sends raw `id` to engine; engine resolves both. If `_resolve` has a bug on a specific input form, it now surfaces as a 404 from RPC instead of 404 from API pre-check. Mitigation: commit (b) `_resolve` matrix tests prevent regression.

## 6. Out of scope (follow-up)

- `optio-ui/src/process-state.ts` state-set duplication. Cross-language; requires runtime exports from `optio-contracts` (currently only types). Separate spec, post-phase-4. The duplication's existing comment at line 6 acknowledges the risk.
- Phase 5 (`CommandConsumer` removal, `on_command(...)` removal) per parent spec §8.5.
- Excavator port to clamator RPC (post-migration, user-handled out of band).
