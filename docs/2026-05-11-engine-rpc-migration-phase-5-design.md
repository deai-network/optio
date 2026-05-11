# Engine-RPC migration — phase 5 design

Date: 2026-05-11
Parent spec: [`docs/2026-05-08-engine-rpc-migration-design.md`](2026-05-08-engine-rpc-migration-design.md) §8.5

## 1. Goal

Retire the legacy `${database}/${prefix}:commands` redis stream and its consumer. After this phase, all process-control operations have **one implementation** on `Optio` in `lifecycle.py`, with two external entry points: the public Python API and the clamator RPC adapter. Scheduled launches respect launch blocks. The convergence rule is codified in `AGENTS.md` so future channels follow the same pattern.

## 2. Context and convergence outcome

Before phase 5, three paths exist for the control verbs:

- **Public Python:** `Optio.launch` / `cancel` / `dismiss` / `resync`.
- **Legacy stream:** `CommandConsumer` dispatch into `Optio._handle_*(payload: dict)` private helpers.
- **RPC:** `OptioEngineService.{launch, cancel, dismiss, resync}` (clamator service).

For `cancel`, `dismiss`, `resync`, all three paths terminate at the same `_handle_*` funnel. For `launch`, the paths diverge — public raises `LaunchBlocked`, the consumer wraps and swallows it as a WARNING log, and RPC validates state preconditively and returns typed reasons. The asymmetry came from each layer needing a different error-handling discipline at its boundary; rather than parameterise the funnel, each layer grew its own wrapper.

After phase 5, only two paths remain. The funnel is collapsed: the public method **is** the implementation. The RPC adapter is thin — it translates wire shape in, translates outcome out, and never duplicates state-machine logic.

```
Path 1 (public Python) ─┐
                        ├─► Optio.<verb>(...) ─► executor / store / state-machine
Path 2 (RPC adapter)   ─┘
```

## 3. Deletions

### `packages/optio-core/src/optio_core/`

- `consumer.py` — whole file.
- `lifecycle.py`:
  - `from optio_core.consumer import CommandConsumer` import.
  - `_consumer: CommandConsumer | None = None` field.
  - Consumer setup block in `init()` (stream name, `CommandConsumer` ctor, four `on()` registrations, `setup()` call).
  - `on_command(command_type, handler)` public method.
  - `_consumer.run()` branch in `run()` (collapses to plain `await self._shutdown_event.wait()`).
  - `_consumer.stop()` call in `shutdown()`.
  - `_handle_launch(payload)` — consumer-only entry point.
  - `_handle_launch_by_process_id(process_id, resume)` — scheduler hook, replaced by `Optio.launch` per §4.
  - `_handle_cancel(payload)`, `_handle_dismiss(payload)`, `_handle_resync(payload)` — bodies inlined into the corresponding public methods.
- `__init__.py`:
  - `on_command = _instance.on_command` re-export.
  - `"on_command"` entry in `__all__`.

## 4. `lifecycle.py` refactor — single-funnel implementation

### 4.1 Typed outcome dataclasses (in `models.py`)

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class LaunchOutcome:
    ok: bool
    reason: Literal[
        "not-found", "not-launchable", "launch-blocked", "no-resume-support"
    ] | None = None

@dataclass(frozen=True)
class CancelOutcome:
    ok: bool
    reason: Literal["not-found", "not-cancellable"] | None = None

@dataclass(frozen=True)
class DismissOutcome:
    ok: bool
    reason: Literal["not-found", "not-dismissable"] | None = None
```

Exported from `optio_core` (top-level package re-export).

### 4.2 Public methods become the implementation

`Optio.launch` absorbs the preconditions previously done in the RPC adapter (resolve, `LAUNCHABLE_STATES` check, resume-support check) plus the launch-block check it already performs. Returns `LaunchOutcome`; no longer raises `LaunchBlocked`. The launch-block check continues to read the `_launch_blocks` map; on match, the outcome carries `reason="launch-blocked"`.

```python
async def launch(self, process_id: str, resume: bool = False) -> LaunchOutcome:
    proc = await self._resolve(process_id)
    if proc is None:
        return LaunchOutcome(ok=False, reason="not-found")
    if proc["status"]["state"] not in LAUNCHABLE_STATES:
        return LaunchOutcome(ok=False, reason="not-launchable")
    if resume and not proc.get("supportsResume", False):
        return LaunchOutcome(ok=False, reason="no-resume-support")
    task = self._executor._task_registry.get(proc["processId"])
    if task is not None and self._matches_block(task.metadata):
        return LaunchOutcome(ok=False, reason="launch-blocked")
    asyncio.create_task(
        self._executor.launch_process(proc["processId"], resume=resume)
    )
    return LaunchOutcome(ok=True)
```

`_matches_block` is a small helper that returns `bool` instead of raising — used here so the method can return an outcome without `try/except`. `_check_launch_blocks` (the raising variant) stays for the surfaces that still raise (`launch_and_wait`, `adhoc_define`, child-launch via `executor`).

`Optio.cancel`, `Optio.dismiss` follow the same shape — resolve, state check, transition, return outcome.

```python
async def cancel(self, process_id: str) -> CancelOutcome:
    proc = await self._resolve(process_id)
    if proc is None:
        return CancelOutcome(ok=False, reason="not-found")
    if not proc.get("cancellable", True) or proc["status"]["state"] not in CANCELLABLE_STATES:
        return CancelOutcome(ok=False, reason="not-cancellable")
    # ... existing state transition logic (scheduled → cancelled OR
    #     cancel_requested → cancelling) ...
    return CancelOutcome(ok=True)

async def dismiss(self, process_id: str) -> DismissOutcome:
    proc = await self._resolve(process_id)
    if proc is None:
        return DismissOutcome(ok=False, reason="not-found")
    if proc["status"]["state"] not in DISMISSABLE_STATES:
        return DismissOutcome(ok=False, reason="not-dismissable")
    # ... existing dismiss logic (clear result fields + state→idle) ...
    return DismissOutcome(ok=True)
```

`Optio.resync(clean=False, metadata_filter=None) -> None` — body inlined from `_handle_resync`. Return type stays `None`; no failure modes worth typing at this layer.

### 4.3 `_resolve` moves from RPC adapter to `Optio`

The "accept ObjectId hex or processId string; return doc or None" helper currently in `_engine_service.py` is the right shape for the public `Optio` methods too. Lifted to `lifecycle.py` as `Optio._resolve(id_str: str) -> dict | None`. RPC adapter uses `self._optio._resolve` for post-success process payload fetch.

### 4.4 What stays raising

- `Optio.launch_and_wait` — keeps raising `LaunchBlocked` and executor exceptions. Future-cleanup note: the wait-for-terminal return shape does not compose cleanly with success/failure outcomes; address separately.
- `Optio.adhoc_define` — keeps raising `LaunchBlocked` at define-time (invariant: cannot define a task whose metadata is currently blocked).
- `executor.launch_process` — keeps raising `LaunchBlocked` for child / internal launch paths.
- `LaunchBlocked` exception class — stays in `models.py`, used by the three surfaces above.

## 5. Scheduler

`_handle_launch_by_process_id` is deleted (per §3). `ProcessScheduler` is wired to a small adapter that funnels through `Optio.launch` and logs on outcome failure, preserving APScheduler-visible observability after the convergence change:

```python
async def _scheduler_launch_adapter(self, process_id: str) -> None:
    outcome = await self.launch(process_id)
    if not outcome.ok:
        logger.warning(
            f"Scheduled launch of {process_id} skipped: {outcome.reason}"
        )
```

`init()` now constructs the scheduler with `launch_fn=self._scheduler_launch_adapter`. Semantic change: scheduled fires now respect launch blocks (previously bypassed). Documented as intentional behaviour change in this phase.

## 6. RPC adapter simplification

`packages/optio-core/src/optio_core/_engine_service.py`:

- Drop preconditive state checks (`LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, `DISMISSABLE_STATES`) from the adapter. State-machine logic is no longer the adapter's concern.
- Each adapter method calls the public `Optio.<verb>`, then maps outcome to typed result. After a successful operation, fetch the process document via `self._optio._resolve` for the wire-shape response payload.
- Drop adapter-local `_resolve` (now on `Optio`).
- Drop `_is_objectid` if unused after `_resolve` moves.
- Update top-of-file docstring (lines 1-6) to one line: `"""Clamator RPC implementation for the optio-engine contract."""`.

Example post-refactor `cancel`:

```python
async def cancel(self, params: CancelParams) -> CancelResult:
    outcome = await self._optio.cancel(params.process_id)
    if not outcome.ok:
        return CancelResult.model_validate(
            {"ok": False, "reason": outcome.reason}
        )
    proc = await self._optio._resolve(params.process_id)
    return CancelResult.model_validate(
        {"ok": True, "process": _to_process_dict(proc)}
    )
```

## 7. Test corpus changes

### Deletions

- `packages/optio-core/tests/test_consumer.py` — whole file.
- `test_no_redis.py::test_on_command_raises_without_redis`.
- `test_launch_guard.py::test_handle_launch_blocked_logs_warning_and_does_not_launch` — consumer-specific warning path.
- `test_integration.py` — whole file. Coverage justification below.

`test_integration.py` removal justification: the lifecycle, child-tree, launch-via-stream, and bad-task-failure concerns are already covered by `test_executor`, `test_child_progress`, `test_launch_guard`, `test_parallel`, `test_group_cancel`, `test_deadline_cancel(_launchguard)`, and `test_persistent_launch_blocks`. The only unique coverage was the heartbeat-key assertion, replaced by a focused new test.

### Additions

- `test_heartbeat.py` (or appended to an existing test module) — ~20-line focused test: init with `redis_url`, start `fw.run()` as a task, sleep slightly longer than the 5-second heartbeat interval, assert `redis.get(f"{db}/{prefix}:heartbeat") is not None`, shutdown.
- Outcome-reason coverage for the public methods. Either a new `test_outcomes.py` or extending `test_launch_guard.py` / `test_lifecycle_reconciliation.py`:
  - `launch`: `not-found`, `not-launchable`, `no-resume-support`, `launch-blocked`, `ok=True` (round-trip).
  - `cancel`: `not-found`, `not-cancellable`, `ok=True`.
  - `dismiss`: `not-found`, `not-dismissable`, `ok=True`.

### Migrations

- `test_launch_guard.py::test_launch_blocked_when_task_metadata_matches` (and other tests asserting `pytest.raises(LaunchBlocked)` on `Optio.launch`) — flip to `assert outcome.reason == "launch-blocked"`.
- `test_engine_service.py::test_launch_blocked` (and any sibling outcome-style tests using `fake_optio` mocks) — replace `side_effect=LaunchBlocked(...)` with `return_value=LaunchOutcome(ok=False, reason="launch-blocked")`. Same pattern for any fake_optio-side cancel / dismiss mocks if they exist.

### Untouched

- `launch_and_wait` raise tests.
- `adhoc_define` raise tests.
- `ctx.run_child` / parallel-group / persistent-launch-blocks / deadline-cancel / resync / scheduler / executor / state-machine tests.

## 8. Interop change

`packages/optio-demo/interop/run.ts`:

- Delete scenario #11 `legacy-stream-regression` (publish-to-`:commands` + xpending assertion).
- Delete `IORedis` import and `redis` client construction if no other scenario uses raw redis directly.
- Delete `KEY_PREFIX` constant if unused after the scenario goes.
- Update the top-of-file comment to drop the "legacy `${prefix}:commands` stream still functions during co-existence" line.

Regression defense: the acceptance grep (`grep -rn 'CommandConsumer\|on_command\|optio:commands\|prefix.*:commands' packages/`) guards against accidental re-introduction. Behavioural test deleted because there is nothing to co-exist with — the consumer code path is gone at the engine level.

## 9. Documentation rewrites

### Top-level `README.md`

- §178-180 "Level 2: Remote Control (+ Redis)" — rewrite. New text describes clamator RPC over Redis as the inbound channel: external services use generated TypeScript and Python clients against the `optio-engine` contract. Drop `optio:commands` stream and `on_command()` references.

### `packages/optio-core/README.md`

- Prefix-doc table row (line 84) — drop `:commands` from the "Redis streams" description. Replace with a reference to the clamator service-stream key prefix (`{database}/{prefix}`).
- §501-569 "Remote Control via Redis" and `on_command()` subsection — rewrite. The new section describes the clamator-based control channel: how to construct a client against the generated `optio-engine` contract, what each verb does, and how typed result reasons surface. The `on_command()` subsection is removed. A short migration sentence points custom-command authors at registering an additional clamator service against `optio_core.rpc_server`.

### `AGENTS.md`

- Line 116: drop the `optio_core.on_command(command_type: str, handler: Callable[..., Awaitable]) -> None` API ref.
- Line 127: prefix-doc table — same edit as the optio-core README.
- Line 627: delete the "Write commands to Redis stream `{prefix}:commands`. Used by domain code that needs to trigger processes without HTTP." paragraph. Replace with a brief note that in-process domain code calls `Optio.launch` / `cancel` / `dismiss` / `resync` directly (no wire involved).
- **Add new section** — see §10 below.

### `packages/optio-core/src/optio_core/_engine_service.py`

- Replace lines 1-6 module docstring with one-line `"""Clamator RPC implementation for the optio-engine contract."""`.

### Out of scope (explicit decisions, recorded here)

- Historical design and plan documents under `docs/2026-03-*`, `docs/2026-04-*`, and `docs/superpowers/`. Frozen artifacts; leave untouched. The acceptance grep tolerates matches inside those paths.
- Architecture diagram refresh (per parent spec, out of scope).
- Stale local build artifacts at `packages/optio-api/dist/publisher.*`. The `dist/` directory is git-ignored; a local clean/rebuild handles it.

## 10. `AGENTS.md` — Control-plane convergence section

Verbatim text to add (place at an appropriate top-level section, near the architecture overview):

> ### Control-plane convergence
>
> Every control verb (launch, cancel, dismiss, resync, group_cancel, …) has **exactly one implementation**: a method on `Optio` in `lifecycle.py`. All external entry points — Python callers, RPC adapters, schedulers, future channels — funnel through that public method.
>
> Adapters do two things and only two things:
> 1. Translate inbound wire shape to a `(process_id, …)` tuple.
> 2. Translate the public method's return / raised exception to the adapter's wire result.
>
> State-machine logic, side effects, and authority decisions live on `Optio`, never in adapters. If an adapter needs to short-circuit with a typed reason (e.g. RPC's `not-found` / `not-cancellable`), it pre-flights against shared constants (`CANCELLABLE_STATES`, etc.) and only then delegates — it does not duplicate the state transition.
>
> When you add a new channel, you call existing `Optio` methods. When you add a new verb, you add one method to `Optio` and one thin adapter per existing channel. Never duplicate verb logic across layers.

Note for spec correctness: after §4's refactor, the RPC adapter no longer pre-flights state — it relies on `Optio` to return typed outcomes. The rule's "pre-flight against shared constants" clause documents the *allowed* pattern for situations where the public method genuinely cannot return a typed outcome (e.g. when wire-contract reasons are richer than the Python API surface). In the current codebase post-phase-5, no adapter needs to pre-flight.

## 11. Out of scope (full list)

- `Optio.launch_and_wait` outcome refactor — keeps raising. Future cleanup; the wait-for-terminal return shape needs separate design.
- `Optio.adhoc_define` and `executor.launch_process` — keep raising `LaunchBlocked` (define-time invariant and internal child path, respectively).
- `group_cancel`, `group_cancel_and_wait`, `block_launches`, `unblock_launches` — current return shapes (`int`, `None`) preserved; convergence rule already satisfied (single implementation, RPC translates).
- State-validation duplication beyond what §4–§6 removes — none remains in the current codebase after this phase, so no separate cleanup is needed.
- Historical design and plan documents.
- Architecture diagram refresh.
- Stale `packages/optio-api/dist/publisher.*` local artifacts.
- External-consumer migration (Excavator and others). Handled by the user out-of-band before this phase was unblocked.

## 12. Risks

1. **Scheduler observability change.** APScheduler currently surfaces `LaunchBlocked` as a job-error log. After this phase the launch returns an outcome; the adapter in §5 emits a WARNING log instead. Net: same level of visibility, different log source.
2. **Custom `on_command` handlers in user code break.** Replacement path is documented in optio-core README — register an additional clamator service against `optio_core.rpc_server`. External consumers already migrated.
3. **`Optio.launch` raise → outcome shift breaks Python callers expecting `LaunchBlocked`.** Inside the project, only `launch_and_wait` and `adhoc_define` still raise; internal callers are updated as part of the refactor. External-caller migration handled out-of-band.
4. **`LaunchBlocked` class remains exported** but the most common entry point no longer raises it. Spec records which surfaces still raise so the kept class is not a mystery.

## 13. Acceptance criteria

### Code

- `grep -rn 'CommandConsumer\|on_command\|optio:commands\|prefix.*:commands' packages/` returns only spec / doc references (no live code, no test code).
- `grep -n 'on_command' AGENTS.md README.md packages/optio-core/README.md` returns zero.
- `packages/optio-core/src/optio_core/consumer.py` does not exist.
- `Optio.launch`, `Optio.cancel`, `Optio.dismiss` return typed outcome dataclasses. `LaunchOutcome`, `CancelOutcome`, `DismissOutcome` exported from `optio_core.models` and re-exported from `optio_core`.
- `_engine_service.py` contains no references to `LAUNCHABLE_STATES`, `CANCELLABLE_STATES`, or `DISMISSABLE_STATES`.
- `AGENTS.md` contains the "Control-plane convergence" section verbatim per §10.

### Tests

- `make test` green (all package suites).
- `make test-interop` green with scenario #11 deleted.
- New `test_heartbeat` passes; old `test_integration.py` absent.
- Outcome-reason coverage exists for `launch` (`not-found`, `not-launchable`, `no-resume-support`, `launch-blocked`), `cancel` (`not-found`, `not-cancellable`), `dismiss` (`not-found`, `not-dismissable`), plus a happy-path `ok=True` round-trip for each.

### Runtime

- `redis-cli xrange "${db}/${prefix}:commands" - +` after running the HTTP test suite end-to-end shows zero entries.
- Scheduled launches with an active matching block emit a WARNING log line and do not launch (no executor task spawned).

## 14. Cross-phase notes

- Implementation plan: written separately via the writing-plans skill once this spec is approved. Lands at `docs/2026-05-11-engine-rpc-migration-phase-5-plan.md`.
- Feature branch: created in-place off `main` before executing the plan (per project convention).
- No further engine-RPC migration phases follow. Future related work (HTTP/RPC adapter cleanup, polling-based confirmation cleanup) is tracked in `docs/2026-05-08-more-rpc-cleanup-todo.md`.
