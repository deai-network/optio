# TaskFactory: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** optio tasks launched with parameters: a `TaskFactory` declares vultus field definitions; launched with values, it makes an ephemeral ad-hoc child process with the values in `ctx.params`; optio-ui renders a form for it.

**Architecture:** optio-core gains `TaskFactory` (synced as a process record with `taskFactory`/`parameters`), a value checker, and `launch(..., params=)` that defines and launches the child under the factory record (`adhoc_define(parent_id=..., ephemeral=...)`). The wire (contracts, engine RPC, optio-api) passes `params` through. optio-ui opens `TaskFactoryLaunchModal` from a factory's launch control.

**Tech Stack:** Python 3 (optio-core, pytest, asyncio strict), TypeScript (zod, ts-rest, clamator codegen, React, antd 6, vultus-core/vultus-antd, vitest).

**Spec:** `docs/2026-10-20-task-factory-design.md` (read it whole first; it is the authority).

## Global Constraints

- Repository: the clone `/home/csillag/praeceptor-worktrees/optio-process-template`, branch `csillag/task-factory`. Never touch other optio checkouts or worktrees (on superego or excavator), never push, never release.
- You are a subagent of claude:praeceptor-a8: do not read or write `~/commissura`.
- optio's AGENTS.md rules: **no `Co-Authored-By` or other self-credit lines in commits**; a package's `AGENTS.md` (and the root `AGENTS.md`'s unified reference) updated in the same commit as that package's public API change; **no test depends on wall-clock time** (wait on conditions with the existing helpers, 60 s hang ceilings only); optio-api production code never writes to MongoDB (`make lint-no-direct-writes`).
- The owner's shell rules: one plain command per Bash call; absolute paths; no `cd` (use `pnpm --dir <path>`, `git -C <path>`, `<repo>/.venv/bin/pytest <abs test path>`); no `sed`; no loops, no shell variables, no `$(…)`; no inline `python -c`, no pipes into interpreters; no environment-variable assignments in any form; Read/Edit/Write for files; scratch only under `<repo>/.superpowers/`, never `/tmp`.
- MongoDB for tests: if a test needs one, the default `localhost:27017` answers (another session's test container, shared by agreement: tests make their own databases and drop them). Do not start or stop containers. Redis-bound interop tests (`make test-interop`) are out of scope.
- Commits by explicit path, optio's conventional style (`feat(optio-core): …`), no trailers.
- Match the surrounding code's style and docstring density; the spec's section numbers in comments where they help.

## Review Focus

1. A factory record must never be launched as a task (no `params`, a scheduler tick, a resync, the auto-resume sweep): it is refused or skipped, never run. *Task 1.*
2. Two launches of one factory at once get two distinct children (process ids differ), each with its own values. *Task 1.*
3. A value of the wrong JSON type (a string for a `numeric`, `true` for a `oneOf`, a nested object) is refused with the parameter's id, never coerced. *Task 1.*
4. An ephemeral child's record goes away when it ends; a factory with `ttl_seconds` keeps its children until the TTL (the child copies the parent's `ttlSeconds`). *Task 1.*
5. The modal shows a launch's `invalid-parameters` message on the right field, and keeps the entered values. *Task 3.*

---

### Task 1: optio-core: `TaskFactory`, the checks, the factory record, `launch(..., params=)`

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py` (`TaskFactory`, beside `TaskInstance`), a new `packages/optio-core/src/optio_core/task_factory.py` (the parameter definitions' check at sync, the values' check at launch, the child's `TaskInstance`), `store.py` (the factory record: `taskFactory`, `parameters`, `$set` on sync), `lifecycle.py` (`_sync_definitions` accepts factories; `launch`, `launch_and_wait`, `launch_and_await_result` take `params`), the executor/scheduler/auto-resume paths so a factory record never runs, `optio_core/__init__.py` (exports)
- Test: new `packages/optio-core/tests/test_task_factory.py` (and existing tests stay green)
- Docs: `packages/optio-core/AGENTS.md`, root `AGENTS.md` (Public API, TaskInstance/TaskFactory fields, the record schema: `taskFactory`, `parameters`; launch reasons)

**Interfaces (produces):**
- `TaskFactory` as spec 1.1 (field names exactly).
- `def check_parameters(parameters: list[dict]) -> None` (raises `ValueError("<id>: <what>")`) and `def check_values(parameters: list[dict], values: dict[str, Any]) -> str | None` (the refusal message, `"<id>: <what>"`, or None), in `task_factory.py`; spec 1.3, 1.4.
- `launch(process_id, resume=False, *, session_id, params: dict[str, Any] | None = None) -> LaunchOutcome`; new reasons `parameters-required`, `not-a-factory`, `invalid-parameters` (with `LaunchOutcome.message` carrying the check's text; add the field if `LaunchOutcome` has none); a factory launch returns `ok=True, proc=<the child's record>`.
- The child: process id `f"{factory.process_id}:{secrets.token_hex(4)}"`, `adhoc_define(child, parent_id=<factory record _id>, ephemeral=factory.ttl_seconds is None)`, then launched with the same `session_id`.

- [ ] **Step 1: Failing tests** (`test_task_factory.py`): the check tables (spec 1.3, 1.4: each kind's good and bad values; Review Focus 3); sync writes the factory record (`taskFactory`, `parameters`) and refuses bad definitions; launch refusals (no params; params on a task; `resume=True`; invalid values naming the id); a good launch runs `execute` with `ctx.params == values` as a child of the factory record, `name_for` naming it; two launches make two children (Review Focus 2); ephemeral vs TTL (Review Focus 4); the factory record never runs by itself (Review Focus 1: a direct launch, a resync, the auto-resume sweep).
- [ ] **Step 2:** Run: `<repo>/.venv/bin/pytest <repo>/packages/optio-core/tests/test_task_factory.py -q` FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** The new file and the whole optio-core suite pass (`<repo>/.venv/bin/pytest <repo>/packages/optio-core/tests -q -m "not serial"`, then `-m serial`).
- [ ] **Step 5:** Docs; commit.

### Task 2: the wire: optio-contracts, the engine RPC, optio-api

**Files:**
- Modify: `packages/optio-contracts/src/api-to-frontend.ts` (`ProcessSchema`: `taskFactory?`, `parameters?`; the `launch` body's `params?`), `optio-engine-to-api.ts` (`LaunchParams.params` optional), `engine-failure-reasons.ts` and `route-error-reasons.ts` (the three reasons), `index.ts`; then `make -C <repo> codegen` (commit the regenerated `_generated/` files); `packages/optio-core/src/optio_core/_engine_service.py` (pass `params`); `packages/optio-api/src/handlers.ts` and the adapters (pass `params`; map the reasons: `invalid-parameters` and `parameters-required` → 400, `not-a-factory` → 409, keeping the `{ reason, message }` body)
- Test: `packages/optio-contracts/src/__tests__/…` (schemas accept the new fields), `packages/optio-api/src/__tests__/handlers.test.ts` (pass-through and status mapping), optio-core's engine-service test if one exists for launch
- Docs: `packages/optio-contracts/AGENTS.md`, `packages/optio-api/AGENTS.md`, root `AGENTS.md` (the contract table, the reasons)

**Interfaces:** consumes Task 1's `launch(..., params=)` and reasons; produces the wire for Task 3: `POST /processes/:id/launch` with `{ resume?, sessionId?, params? }`.

- [ ] Failing tests → implement → `pnpm --dir <repo>/packages/optio-contracts test`, `pnpm --dir <repo>/packages/optio-api test`, the optio-core engine-service tests, `make -C <repo> lint` → commit (generated files in the same commit as their source).

### Task 3: optio-ui: the launch form

**Files:**
- Create: `packages/optio-ui/src/components/TaskFactoryLaunchModal.tsx` (+ test)
- Modify: `packages/optio-ui/src/components/LaunchControls.tsx`, `ProcessItem.tsx`, `ProcessDetailView.tsx` (a factory record's launch opens the modal), `src/hooks/useProcessActions.ts` (`launch(id, { resume?, params? })`), optio-ui's i18n strings (the modal's title and buttons, in the languages optio-ui ships), `package.json` (`vultus-antd` as a peer and dev dependency, the version Praeceptor uses, `^0.7.2`), `src/index.ts` (export the modal)
- Docs: `packages/optio-ui/AGENTS.md`, root `AGENTS.md`

**Interfaces:** consumes Task 2's wire; produces `TaskFactoryLaunchModal` (props: the factory `Process`, `open`, `onClose`, `onLaunched?(child: Process)`) and the launch controls' behaviour.

The modal (spec 3): the factory's `warning` on top; one vultus-antd `Field` per parameter (labels resolved by the UI language: that language, else `en`, else the first); initial values from `initialValue`; one vultus action launching with `params`; an `invalid-parameters` refusal on its field (the message's `<id>:` prefix names it), the values kept (Review Focus 5); closes on success and calls `onLaunched`.

- [ ] Failing tests (each kind renders and submits; the refusal on its field; opens from `ProcessItem` and `ProcessDetailView`; a non-factory's launch unchanged) → implement → `pnpm --dir <repo>/packages/optio-ui test`, `pnpm --dir <repo>/packages/optio-ui typecheck` (or its build), `pnpm --dir <repo>/packages/optio-dashboard build` if the dashboard compiles against optio-ui → commit.

---

## Task 4 (controller): finish

- [ ] Final whole-branch review (most capable model); one fix wave; re-review.
- [ ] `make -C <repo> test` once (TS + Python), `make -C <repo> lint`.
- [ ] Release, only on the owner's word, per `docs/release-cookbook.md`: optio-core and optio-contracts (lockstep, `release-wire`), optio-api, optio-ui (minor bumps).
- [ ] Then Praeceptor's own spec: the engine registers module factories; `/admin` with optio-ui's form; pius-x-cath's "drop proposals" factory.
