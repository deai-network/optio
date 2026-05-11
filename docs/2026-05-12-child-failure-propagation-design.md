# Child failure propagation — design

## Problem

When a child task's execute function raises a typed exception (e.g.
`DownloadFailed(url, exit_code, stderr_tail)`), `optio-core`'s executor catches
it inside `_execute_process` (`executor.py:183-196`), writes
`status.error = str(e)` and the terminal `failed` state to Mongo, then returns
the string `"failed"` to `execute_child`. The caller of `ctx.run_child(...)`
does not see the original exception — it sees
`RuntimeError(f"Child process '{name}' failed")` (`executor.py:279`), which
carries only the name string. The original exception type, fields, and
traceback are gone by the time control returns to the parent's execute body.

The `ParallelGroup` path is similarly lossy: each child runs with
`survive_failure=True` internally; the group aggregates state into
`ChildResult` entries (containing only `process_id`, `state`, `error` string),
and on aggregate breach raises `RuntimeError("Parallel group failed: ...")` from
`__aexit__` (`context.py:595-597`).

Net effect: any task factory whose execute raises a structured exception cannot
communicate that structure to the parent. Callers must either
`except RuntimeError` (no type discrimination) or read the child's terminal
`status.error` text out of Mongo and re-parse it. The download task
(`HookContext.download_file`) hits this with `DownloadFailed`.

## Scope decisions

The following choices were settled during brainstorming and constrain the
design:

1. **Same-run only.** Exception preservation is in-memory, within a single
   executor instance. Cross-process / resume scenarios (child terminated in a
   prior run, parent resumes from Mongo state) are out of scope. No pickling,
   no serialized exception form in Mongo. `status.error = str(e)` text remains
   the only persistent record.

2. **`ChildProcessFailed` wrapper.** Parent observes a typed wrapper exception
   carrying the original. Caller pattern:
   `except ChildProcessFailed as e: if isinstance(e.original, DownloadFailed): ...`.
   Not direct re-raise of the original; the wrapper carries
   process identity (`name`, `process_id`) so multi-child parents can
   discriminate.

3. **Full parallel-group treatment.** `ChildResult` gains
   `original_exception`. `ParallelGroup.__aexit__` raises an
   `ExceptionGroup[ChildProcessFailed]` (builtin, py3.11+) on aggregate
   breach. Callers use `except*` for structured multi-failure handling.

4. **`run_child` return type widens.** `ChildOutcome(state, original_exception)`
   dataclass replaces the bare `str` return, exposing the original even when
   `survive_failure=True` suppresses the raise.

5. **Direct return between `_execute_process` and `execute_child`.** No
   side-channel dict on the executor. The two methods are adjacent in the
   call graph; the exception passes by tuple return.

## Architecture

Single-package change in `optio-core`. Three new public types and signature
changes on two methods. No persistence, no Mongo schema change, no resume
impact.

### New types

**`optio_core.exceptions`** (new module):

```python
class ChildProcessFailed(Exception):
    """Raised by ProcessContext.run_child when a child task fails and
    survive_failure=False. Carries the child's identifying name, its
    process_id, and the original exception raised inside the child's
    execute function."""

    def __init__(self, name: str, process_id: str, original: BaseException):
        self.name = name
        self.process_id = process_id
        self.original = original
        super().__init__(
            f"Child '{name}' (process_id={process_id}) failed: {original!r}"
        )
```

`ParallelGroupFailed` is not a new class. Aggregate failure raises the builtin
`ExceptionGroup("Parallel group failed", [ChildProcessFailed, ...])` directly.
Callers use `except* ChildProcessFailed`.

**`optio_core.models`** (extend):

```python
@dataclass
class ChildOutcome:
    """Return value of ProcessContext.run_child."""
    state: str  # "done" | "failed" | "cancelled"
    original_exception: BaseException | None = None
```

`ChildResult` (existing) gains two fields:

```python
@dataclass
class ChildResult:
    process_id: str
    state: str
    error: str | None = None
    name: str = ""                                       # new
    original_exception: BaseException | None = None      # new
```

Both new fields default for backwards compatibility of constructors; in
practice both raise sites in `context.py` (lines 539, 557) will set them.

### Signature changes

**`Executor._execute_process`** (`executor.py:113`):

Return type changes from `str` to `tuple[str, BaseException | None]`.

- Success: `("done", None)` or `("cancelled", None)`.
- Exception path (catch block at line 183): the catch already binds `e` via
  `except Exception as e:`. Change the existing `return "failed"` (line 196)
  to `return ("failed", e)`. No other rewrite of the catch block; all DB
  writes inside it are unchanged.
- No-exec-fn early-fail path (line 174): change `return "failed"` to
  `return ("failed", None)`.
- Done/cancelled return (line 225): change `return end_state` to
  `return (end_state, None)`.
- Mongo `status.error = str(e)` write unchanged.

**Executor root entry** (`executor.py:109`): `state, _ = await self._execute_process(...)`;
`return state`. Public signature of `Executor.execute()` unchanged — the root
has no parent to propagate to.

**`Executor.execute_child`** (`executor.py:230`):

Return type changes from `str` to `ChildOutcome`.

- `(end_state, exc) = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)`.
- Existing alpha-notify logic unchanged (uses `end_state` and survive flags
  only).
- If `end_state == "failed" and not survive_failure`:
  `raise ChildProcessFailed(name, process_id, exc) from exc`.
- Otherwise: `return ChildOutcome(end_state, exc if end_state == "failed" else None)`.

**`ProcessContext.run_child`** (`context.py:295`):

Return type changes from `str` to `ChildOutcome` (forwarded from
`execute_child`).

**`ParallelGroup.spawn._run`** (`context.py:546-583`):

- `outcome = await self._ctx.run_child(..., survive_failure=True, survive_cancel=True)`.
- `self._results.append(ChildResult(name=name, process_id=process_id, state=outcome.state, error=None if outcome.state == "done" else f"Child {outcome.state}", original_exception=outcome.original_exception))`.
- Breach-detection logic unchanged (compares `outcome.state` to group
  `survive_*` flags).

**`ParallelGroup.__aexit__`** (`context.py:590-598`):

On `self._failed`, build per-child wrappers and raise an `ExceptionGroup`:

```python
failed_results = [r for r in self._results if r.state != "done"]
failures = [
    ChildProcessFailed(
        r.name, r.process_id,
        r.original_exception or RuntimeError(f"child {r.state}"),
    )
    for r in failed_results
]
raise ExceptionGroup("Parallel group failed", failures)
```

A cancelled child (no exception object) gets a synthetic
`RuntimeError(f"child cancelled")` as its `original` so the
`ChildProcessFailed` invariant (`original` is never `None`) holds.

## Data flow

### Single child, raise path (`survive_failure=False`)

```
child execute_fn raises DownloadFailed(url, exit_code, stderr_tail)
  → _execute_process catch block:
      writes status.error = str(e), state="failed" to Mongo (unchanged)
      runs cleanup_ephemeral, clear_widget_upstream (unchanged)
      returns ("failed", DownloadFailed_instance)
  → execute_child:
      receives (state="failed", exc=DownloadFailed_instance)
      schedules alpha notify_parent_abnormal (abnormal=True; unchanged)
      raises ChildProcessFailed(name, pid, exc) from exc
  → ProcessContext.run_child: exception propagates unchanged
  → parent execute_fn:
      try:
          await ctx.run_child(...)
      except ChildProcessFailed as e:
          if isinstance(e.original, DownloadFailed):
              # access e.original.url, e.original.exit_code, e.original.stderr_tail
              ...
```

### Single child, survive path (`survive_failure=True`)

```
child raises → _execute_process returns ("failed", exc)
  → execute_child:
      alpha notify NOT fired (survive_failure=True; existing behavior)
      returns ChildOutcome("failed", exc)
  → run_child returns ChildOutcome to parent
  → parent: outcome.original_exception holds the DownloadFailed instance
```

### Parallel group, multi-failure path

```
spawn._run loop (each child):
  outcome = await self._ctx.run_child(..., survive_failure=True, survive_cancel=True)
  ChildResult populated with name + original_exception
  if outcome.state in {failed, cancelled} and group's survive_* breached:
      self._failed = True
      group-level alpha notify (unchanged)

__aexit__:
  await gather (unchanged)
  if self._failed:
      raise ExceptionGroup("Parallel group failed", [ChildProcessFailed, ...])

parent:
  try:
      async with ctx.parallel_group(...) as g:
          await g.spawn(...)
          await g.spawn(...)
  except* ChildProcessFailed as eg:
      for cpf in eg.exceptions:
          if isinstance(cpf.original, DownloadFailed): ...
```

### Success and cancelled paths

Externally unchanged except for the return shape: `ChildOutcome("done", None)`
or `ChildOutcome("cancelled", None)` instead of bare state strings.

## Error handling and edge cases

**Cancellation.** A child cancelled via `cancel_flag` ends `("cancelled", None)`
— no exception object exists. `ChildOutcome.original_exception` is `None`. The
parallel-group wrapper synthesizes `RuntimeError("child cancelled")` as the
`ChildProcessFailed.original` when building the `ExceptionGroup`, so callers
get a non-`None` `original` to inspect.

**No-execute-fn early-fail** (`executor.py:165-174`). A child registered without
an execute function fails before execution starts. Currently returns
`"failed"`. New behavior: returns `("failed", None)`. `execute_child` sees
`exc=None`; when `survive_failure=False` it must still raise something — it
synthesizes `RuntimeError("No execute function found")` (matching the Mongo
`status.error` text) and passes that as `ChildProcessFailed.original`.

**Failure inside `_execute_process` catch block** (e.g., the `update_status`
write to Mongo itself throws while writing the terminal `failed` state).
Existing behavior bubbles the new exception up out of `_execute_process`,
losing the original `e`. This hazard predates the change. **Out of scope to
fix.** Document and move on.

**`BaseException` vs `Exception`.** The catch block uses `except Exception`.
`BaseException` subclasses (`KeyboardInterrupt`, `asyncio.CancelledError`)
bypass it — unchanged. `ChildProcessFailed` extends `Exception`, consistent
with the catch scope.

**`__cause__` chain.** `raise ChildProcessFailed(...) from exc` sets
`__cause__` to the original. Both `e.__cause__` and `e.original` point at the
same instance. `e.original` is the documented accessor (typed attribute, not
exception-machinery). The traceback printer will show the original below the
"The above exception was the direct cause of the following exception:" line.

**Alpha-cascade timing.** Unchanged. `execute_child` fires
`notify_parent_abnormal` based on `(end_state, survive_*)` before re-raising.
The new `raise ChildProcessFailed(...)` happens after
`asyncio.create_task(notify_parent_abnormal(...))` schedules.

**ExceptionGroup with mixed types.** Failed children may have heterogeneous
`original` types (`DownloadFailed`, `RuntimeError`, synthesized cancellation
sentinel, etc.). `except* ChildProcessFailed` matches them all; the caller
filters by `e.original` type within the handler. No uniformity constraint.

**Refused spawn** (`context.py:528-543`). When the parent's cancellation flag
is already set and `auto_cancel_children=True`, `spawn` records a "cancelled"
`ChildResult` with `error="parent cancelled"` and never starts the child.
That code path now also sets `name=name, original_exception=None`. Behaviour
otherwise unchanged — the result never participated in the raise path.

## Testing

New tests under `packages/optio-core/tests/`:

1. **`test_child_failure_raises_structured`** — child raises a custom
   `class DownloadFailed(Exception)` with fields `url`, `exit_code`,
   `stderr_tail`. Parent catches `ChildProcessFailed as e`. Assertions:
   `e.name == "<child name>"`, `e.process_id == "<child pid>"`,
   `isinstance(e.original, DownloadFailed)`, `e.original.url` etc. intact.

2. **`test_child_failure_cause_chain`** — verify `e.__cause__ is e.original`
   after `raise ... from exc`.

3. **`test_child_survive_failure_returns_outcome`** —
   `outcome = await ctx.run_child(..., survive_failure=True)`. Assert
   `outcome.state == "failed"`,
   `isinstance(outcome.original_exception, DownloadFailed)`, no exception
   raised at call site.

4. **`test_child_done_outcome`** — success path returns
   `ChildOutcome("done", None)`.

5. **`test_child_cancelled_outcome`** — cancel mid-flight returns
   `ChildOutcome("cancelled", None)`.

6. **`test_no_execute_fn_synthesizes_runtimeerror`** — no-execute path:
   `ChildProcessFailed.original` is a synthetic
   `RuntimeError("No execute function found")`.

7. **`test_parallel_group_raises_exception_group`** — two children with two
   distinct exception types. `__aexit__` raises `ExceptionGroup`.
   `except* ChildProcessFailed as eg`: both `.original` types present, names
   correct.

8. **`test_parallel_group_results_carry_originals`** — `survive_failure=True`
   at group level (no raise). For each `r in group.results` with `r.state ==
   "failed"`, `r.original_exception` is populated.

9. **`test_parallel_group_mixed_cancel_and_fail`** — one child fails with a
   real exception, one is cancelled. Resulting `ExceptionGroup` contains
   both wrappers; the cancelled one's `original` is the synthetic
   `RuntimeError("child cancelled")`.

10. **Regression sweep.** Existing alpha-cascade tests continue to pass —
    `notify_parent_abnormal` fires on the same state transitions.

## Migration of in-tree consumers

- `ParallelGroup.spawn._run` (`context.py:548-583`): rewrite to use
  `ChildOutcome`. Update `ChildResult` construction with `name=` and
  `original_exception=`.
- `optio-demo` callers (`tasks/festival.py`, `tasks/home.py`,
  `tasks/terraforming.py`): all discard the `run_child` return value.
  No changes required.
- Existing tests catching `RuntimeError("Child process ... failed")`: rewrite
  to `ChildProcessFailed`.
- Existing tests catching `RuntimeError("Parallel group failed")`: rewrite to
  `ExceptionGroup` and assert via `eg.exceptions`.

Mechanically: `grep -rn 'Child process.*failed\|Parallel group failed' packages/`
during implementation to surface call sites.

## Out of scope

- Cross-process or resume preservation of exception structure. Mongo
  `status.error` remains a free-form string.
- Changes to `optio-api`, `optio-dashboard`, or other consumers outside
  `optio-core`. This is a core-internal contract change. The Mongo schema and
  external HTTP responses are unaffected.
- Mongo schema additions for serialized exception payload.
- Replacing the catch-block-bubbles-up-and-loses-original hazard inside
  `_execute_process` itself (pre-existing, documented above).

## Files changed

- `packages/optio-core/src/optio_core/exceptions.py` — new module, defines
  `ChildProcessFailed`.
- `packages/optio-core/src/optio_core/models.py` — add `ChildOutcome` dataclass;
  extend `ChildResult` with `name` and `original_exception`.
- `packages/optio-core/src/optio_core/executor.py` — `_execute_process` return
  type and exception-path return value; `execute_child` return type and raise
  shape; root-entry tuple destructure.
- `packages/optio-core/src/optio_core/context.py` — `ProcessContext.run_child`
  return type annotation; `ParallelGroup.spawn._run` and
  `ParallelGroup.__aexit__` rewrites.
- `packages/optio-core/AGENTS.md` — document the new public types and revised
  contract.
- `packages/optio-core/tests/` — new test files per the test list above.
