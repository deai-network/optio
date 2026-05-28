# Distinguish child-failure from external cancel in parent's lifecycle signals

This spec was written against the following baseline:

**Base revision:** `24c10af46dd4a900c31cc5c6e7b2bb201d0e22eb` on branch `main` (as of 2026-05-28T13:40:31Z)

## Summary

When a child process inside `ctx.parallel_group(survive_failure=False)` fails, the
optio executor's current alpha-cascade mechanism calls `Optio.cancel(parent_id)` on
the parent to fan an abort out to siblings. As a side effect, the parent's
`cancellation_flag` is set and its Mongo row transitions through
`cancel_requested → cancelling`. This collapses two semantically distinct events
("a child of mine failed" vs "I was externally cancelled") onto the same signal,
and corrupts the parent's terminal state when the parent's user code catches the
resulting `ExceptionGroup` and returns normally (parent ends `cancelled` instead of
`failed`).

The fix replaces the parent-targeted cancel with sibling-only cancellation: when a
child fails or breaches a group, the executor cancels the parent's other still-active
direct children directly, without touching the parent's flag or row. The parent's
`should_continue()` and terminal state then accurately reflect its own situation.

A secondary distinction is preserved: when the breach reason is external child
cancellation (not failure), the cancellation chain still cascades upward through
the parent's flag — because that case really is cancellation.

## Problem

### Observed symptom

In a real Excavator deployment (consumer of optio-core), a sync task running a
producer + consumer under `ctx.parallel_group(survive_failure=False)` saw the
consumer fail. The expected outcome was for the parent sync task to be classified
as a failure. The actual outcome:

- The consumer process row ended `failed` (correct).
- The producer process row ended `cancelled` (correct — induced cancel from sibling
  failure).
- The parent sync task row ended `cancelled` (**incorrect** — no external cancel
  occurred; a child of the parent failed).

The bug is observable in optio's own process tree, not only in application-level
collections.

### Root cause

When a `parallel_group` breach occurs (child failed or cancelled while the group's
`survive_*=False`), `ParallelGroup.__aexit__` calls
`executor._notify_parent_abnormal(parent_id)` — wired in `lifecycle.py:175` to
`Optio.cancel`. This call:

1. Atomically transitions the parent's Mongo row from a `CANCELLABLE_STATES` value
   (`running`) to `cancel_requested`.
2. Calls `request_cancel_with_deadline(parent_oid)` — sets the parent's
   `cancellation_flag` Event and records a force-cancel deadline.
3. Conditionally transitions the parent's row to `cancelling`.
4. Recursively cancels the parent's active direct children with a shared deadline.

The fourth step is the only step that the alpha-cascade actually needs in order to
abort siblings. Steps 1-3 are side effects that:

- Poison `ctx.should_continue()` — it now returns `False` inside the parent's
  `except ExceptionGroup` handler, even though no external cancel occurred.
- Race the parent's `execute_fn` finalization: if the parent catches the
  `ExceptionGroup` and returns normally, the executor reads
  `cancel_flag.is_set() == True` and writes the terminal state as `cancelled`
  (`executor.py:199-200, 287-300`). This is how the parent ends up `cancelled`
  in Mongo even when the underlying cause was a child failure.
- Violate the documented state machine: today's path
  `running → cancel_requested → cancelling → failed` (when `execute_fn`
  re-raises) crosses `cancelling → failed`, which is not a valid transition per
  `state_machine.py:6, 10`. The raw `$set` in `update_status` bypasses validation.

A second call site in `executor.py:357-359` (non-group `run_child` with an abnormal
child terminal) has the same conflation.

Both call sites use `asyncio.create_task(...)` — fire-and-forget — which makes the
parent's flag observation timing-dependent and adds flakiness to any test that
attempts to assert it.

### Why existing tests do not catch this

Two-layer coverage gap:

1. No test asserts the value of `ctx.should_continue()` or
   `ctx.cancellation_flag.is_set()` inside the parent's `except` handler. Existing
   tests only check terminal DB state and exception contents at user-code
   boundaries.
2. Existing tests that observe the parent's terminal state after a child failure
   permit *either* `failed` or `cancelled` (`test_parallel_group_fail_fast_under_alpha:409`,
   `test_parallel_group_cancel_propagates_to_siblings:362`). The accompanying
   comment on lines 358-361 of the latter test explicitly accepts both: "Either
   terminal is acceptable." The author noticed the ambiguity but did not investigate
   its cause; the bug was codified as tolerated behavior.

Both layers fed each other — author probably thought "the terminal state is allowed
to vary" because the in-flight signal made user-side classification non-deterministic.

## Contract after the fix

### Terminal-state rules

| Cause | Parent terminal state |
|-------|-----------------------|
| External cancel on parent (or cancel of an ancestor cascades down) | `cancelled` |
| Child cancelled externally + parent's group does not `survive_cancel` (cancellation cascades up) | `cancelled` |
| Child failed + parent's group does not `survive_failure` + parent's `execute_fn` re-raises or does not catch | `failed` |
| Child failed + parent's `execute_fn` catches `ExceptionGroup` and returns normally | `done` (parent took explicit responsibility) |
| Parent's own code raised an exception | `failed` |
| Normal completion, no cancel flag, no exception | `done` |

A parent whose own child *failed* never ends as `cancelled`. The `cancelled`
terminal state is reserved for actual cancellation — on self, on an ancestor, or on
a descendant whose cancellation has cascaded up through a non-surviving group.

Tests that currently allow `state in {failed, cancelled}` must be tightened to
assert one exact terminal value per scenario.

### In-flight signal contract

`ctx.should_continue() == False` (equivalently, `ctx.cancellation_flag.is_set()`
returns `True`) when and only when:

- The parent itself has been externally cancelled, OR
- An ancestor's cancellation has propagated down to the parent, OR
- A descendant of the parent has been externally cancelled and the cancellation has
  cascaded up through a non-surviving group boundary.

It does **not** become `True` because a child of the parent failed. Code inside
`except ExceptionGroup` / `except ChildProcessFailed` handlers may read
`should_continue()` to distinguish "I'm winding down because I was cancelled" from
"I'm winding down because something below me failed."

### Sibling-cancellation behavior

When a child in a `parallel_group` fails or is cancelled and the group's
`survive_*` is not satisfied, the still-running siblings of that child are
cancelled cooperatively (each gets a normal `Optio.cancel`, with the shared
force-cancel deadline). Each cancelled sibling's row goes
`running → cancel_requested → cancelling → cancelled`, matching today's behavior
for siblings.

When a non-group `run_child` ends abnormally and the parent has other concurrent
direct children (e.g., via a separate group or a manual gather elsewhere in the
parent's code), those concurrent children are cancelled cooperatively.

## Design

### Mechanism

Two changes:

1. The callback wired into `Executor` via `notify_parent_abnormal` is augmented
   with a sibling-only variant. Today it points at `Optio.cancel`; after the
   change there are two callbacks — one that calls `Optio.cancel(parent_id)` (used
   when the breach is a cancellation cascade) and one that cancels only the
   parent's active direct children, leaving the parent untouched (used when the
   breach is a child failure).

2. `ParallelGroup` tracks the breach **reason** (failure vs cancellation) so the
   appropriate callback can be invoked.

For the non-group `run_child` path, the existing line 365-366 flag-set is kept for
the cancelled-child case; the `notify_parent_abnormal` call is replaced with the
sibling-only variant for the failed-child case.

Concretely (sketch, not final API):

```python
# lifecycle.py — new helper, sibling-only descent
async def _cancel_active_children(
    self,
    parent_process_id: str,
    *,
    inherit_deadline: float | None = None,
) -> None:
    """Cancel the active direct children of `parent_process_id` without
    cancelling the parent itself. Used by the alpha-cascade callback when the
    breach reason is a child *failure* (cancellation cascades up should still
    go through Optio.cancel(parent) so the parent's row transitions correctly).
    Shares the existing deadline-budget convention with Optio.cancel."""
    proc = await self._resolve(parent_process_id)
    if proc is None:
        return
    task = self._executor._task_registry.get(proc["processId"])
    auto = task.auto_cancel_children if task is not None else True
    if not auto:
        return
    effective_deadline = (
        inherit_deadline
        if inherit_deadline is not None
        else time.monotonic() + self._config.cancel_grace_seconds
    )
    from optio_core.store import list_direct_children
    children = await list_direct_children(
        self._config.mongo_db, self._config.prefix,
        proc["_id"], states=ACTIVE_STATES,
    )
    if not children:
        return
    await asyncio.gather(
        *(
            self.cancel(str(c["_id"]), inherit_deadline=effective_deadline)
            for c in children
        ),
        return_exceptions=True,
    )

# wiring — Executor now takes both callbacks
self._executor = Executor(
    ...,
    notify_parent_failure=self._cancel_active_children,   # child failed → siblings only
    notify_parent_cancel_cascade=self.cancel,             # child cancelled → cascade up
)
```

Naming is illustrative. Final names should make the asymmetry self-documenting.

### Group's breach-reason tracking

`ParallelGroup` today carries a single `self._failed: bool` that conflates the two
breach reasons (`failed` outcome vs `cancelled` outcome). It is replaced with
explicit tracking so the post-aggregation behavior can differ:

```python
self._breach_reason: Literal["failure", "cancel", None] = None
# set in the per-child completion handler:
#   outcome.state == "failed"   and not survive_failure → "failure"
#   outcome.state == "cancelled" and not survive_cancel → "cancel"
# Failure dominates: if any child failed (not survived), the group's breach is
# classified as "failure", regardless of any concurrently-breaching cancel. A
# concrete failure is a stronger signal than a cancellation, and the parent
# should not be auto-cancelled when one of its children actually failed.
```

In `__aexit__`, after the `gather`:

- If `self._breach_reason == "failure"`: invoke the sibling-only callback (so
  remaining siblings are cancelled cooperatively). Do **not** touch parent's
  flag or row. Raise `ExceptionGroup`.
- If `self._breach_reason == "cancel"`: invoke `Optio.cancel(parent_id)` (today's
  mechanism — propagates the cancellation up to the parent, including the
  row-state transitions and the recursive descent to other direct children).
  Raise `ExceptionGroup`.
- Otherwise: return False (no breach).

In the cancel-cascade case the parent's row goes `running → cancel_requested →
cancelling`, which matches today's behavior and is correct: the parent really is
being cancelled. The parent's terminal state is then determined naturally — if
`execute_fn` returns, the executor writes `cancelled` (cancelling → cancelled,
valid); if `execute_fn` re-raises, the executor writes `failed` (cancelling →
failed, a state-machine violation that exists today and is out of scope for this
fix).

### Non-group `run_child` path

`execute_child` at `executor.py:345-366` is split into two cases by breach reason
(same asymmetry as the group case):

```python
# Current (collapsed):
abnormal = (
    (end_state == "cancelled" and not survive_cancel)
    or (end_state == "failed" and not survive_failure)
)
if abnormal and self._notify_parent_abnormal is not None:
    asyncio.create_task(self._notify_parent_abnormal(parent_ctx.process_id))

if end_state == "failed" and not survive_failure:
    ...
    raise ChildProcessFailed(...)
if end_state == "cancelled" and not survive_cancel:
    parent_ctx._cancellation_flag.set()

# After fix:
if end_state == "failed" and not survive_failure:
    # Failure breach: cancel parent's other concurrent children only.
    # Do NOT touch parent's flag or row. ChildProcessFailed raise
    # communicates the failure to parent's user code.
    if self._notify_parent_failure is not None:
        asyncio.create_task(self._notify_parent_failure(parent_ctx.process_id))
    if exc is None:
        exc = RuntimeError(...)
    raise ChildProcessFailed(name, process_id, exc) from exc

if end_state == "cancelled" and not survive_cancel:
    # Cancellation breach: today's full Optio.cancel(parent) cascade is
    # preserved — the parent really is being cancelled. The line 366
    # flag-set is retained so the user code that holds parent_ctx
    # observes should_continue() == False without waiting for the row
    # transitions.
    if self._notify_parent_cancel_cascade is not None:
        asyncio.create_task(
            self._notify_parent_cancel_cascade(parent_ctx.process_id),
        )
    parent_ctx._cancellation_flag.set()
```

The two callbacks share a callsite shape (fire-and-forget on the
parent's `process_id`). They differ only in what `Optio` does on receipt.

### Behavior change: parent flag no longer auto-set on child failure

Today, after a non-group `run_child` with `survive_failure=False`, the parent's
flag becomes set (via the racy `notify_parent_abnormal` → `cancel(parent)`). User
code catching `ChildProcessFailed` and attempting subsequent `run_child` calls is
refused at `context.py:617-633` because the parent's flag is set.

After the fix, the parent's flag is **not** set on a child failure. User code that
catches `ChildProcessFailed` can continue to spawn further work. This is a breaking
behavior change. Argument for: the user opted into handling the exception
explicitly; refusing further spawns was paternalistic and non-obvious. Argument
against: some users may rely on the implicit guard.

Decision: accept the change. It is the right semantic. Document in the changelog
and the spec's "Breaking changes" section. Users who want the old guard can set
their own flag in the except handler.

### State-machine cleanliness

After the fix, a parent whose child failed transitions `running → failed`
(valid per `state_machine.py:6`). Today's path `running → cancel_requested →
cancelling → failed` is eliminated. No code change to `state_machine.py` is
required; the validation gap in `update_status` is a separate concern.

### Excavator workaround

Excavator's `drive_sync` currently catches `ExceptionGroup` and inspects
`group.results` for any `state == "failed"`. After the fix, that workaround is no
longer needed: `ctx.should_continue() == True` inside the except handler reliably
indicates "a child failed (and I was not externally cancelled)." Removal of the
workaround happens in a separate Excavator-side change after this fix is released.
This spec does not modify Excavator.

## Test plan

### New tests (positive contract)

1. `test_parent_should_continue_true_inside_group_failure_except`: parent runs a
   `parallel_group(survive_failure=False)` with one failing child and one slow
   child. Parent's `except ExceptionGroup` handler captures
   `ctx.should_continue()`. Assert: `True`.

2. `test_parent_should_continue_false_when_externally_cancelled`: parent runs a
   group, external cancel hits the parent. Parent's `except` (or post-await)
   captures `ctx.should_continue()`. Assert: `False`.

3. `test_parent_should_continue_false_when_child_cancelled_externally_no_survive`:
   parent runs `parallel_group(survive_cancel=False)`. External cancel of a child.
   Parent's `except ExceptionGroup` captures `ctx.should_continue()`. Assert:
   `False` (cancel cascades up).

4. `test_parent_terminal_failed_when_child_fails_in_group_and_parent_returns`:
   parent catches `ExceptionGroup` from a child-failure breach and **returns**
   normally. Assert parent terminal state == `done` (parent's choice — they
   caught and returned). Sibling terminal == `cancelled`. Failing child
   terminal == `failed`.

5. `test_parent_terminal_failed_when_child_fails_in_group_and_parent_reraises`:
   parent does not catch (or catches and re-raises). Assert parent terminal ==
   `failed`. Sibling == `cancelled`. Failing child == `failed`.

6. `test_parent_terminal_cancelled_when_child_cancel_cascades_up`: parent runs
   `parallel_group(survive_cancel=False)`. External cancel of a child. Parent
   catches `ExceptionGroup` and returns. Assert parent terminal == `cancelled`.

7. `test_parent_terminal_failed_when_child_cancel_cascade_and_parent_reraises`:
   same as (6) but parent re-raises. Assert parent terminal == `failed` (parent's
   choice).

8. `test_nongroup_run_child_failure_does_not_set_parent_flag`: parent calls
   `run_child(survive_failure=False)`. Child fails. Parent's `except
   ChildProcessFailed` captures `ctx.should_continue()`. Assert `True`. Parent
   can continue spawning further work after the exception.

9. `test_nongroup_run_child_cancel_sets_parent_flag`: parent calls
   `run_child(survive_cancel=False)`. Child is externally cancelled. Parent's
   `except` (or post-await) captures `ctx.should_continue()`. Assert `False`
   (cancel cascade preserved).

10. `test_mixed_breach_failure_dominates_cancel`: parent runs a group with one
    failing child and one externally-cancelled child. Both breach the same group.
    Assert: parent terminal == `failed` (or `done` if parent catches and returns)
    — failure dominates over cancel for breach classification. Parent flag is
    **not** set during the breach. `ctx.should_continue()` inside except returns
    `True`.

### Tightening existing tests

`test_parallel_group_fail_fast_under_alpha:409`: change
`assert a_proc["status"]["state"] in {"failed", "cancelled"}` to
`assert a_proc["status"]["state"] == "failed"`. Update the test's parent task to
not catch the exception (or to catch and re-raise) so the contract is
deterministic.

`test_parallel_group_cancel_propagates_to_siblings:362`: change
`assert a_proc["status"]["state"] in {"failed", "cancelled"}` to
`assert a_proc["status"]["state"] == "cancelled"` (this scenario is a
cancellation-cascade case; the parent should end `cancelled`). Update the parent
task to catch and return (or remove the implicit re-raise) so the contract is
deterministic.

`test_parallel_group_mixed_cancel_and_fail_synthesizes_for_cancelled`: verify the
test still passes; tighten the parent terminal assertion if one is present.

`test_cancel_child_does_not_leave_parent_stuck_at_cancelling`: this test is a
regression guard against a race that the fix eliminates entirely (because
`Optio.cancel(parent)` is no longer called by the alpha cascade). The test should
continue to pass, and the race it guards against is no longer reachable. Keep
the test as a defensive regression guard.

### Removed paths

No tests are removed.

## Breaking changes

1. The parent's `cancellation_flag` is no longer set automatically when a child
   process fails (group or non-group, `survive_failure=False`). User code that
   relied on this implicit guard to prevent further spawns after a child failure
   must set its own flag.

2. The parent's Mongo row no longer transitions through `cancel_requested →
   cancelling` on a child failure. It stays `running` until either the parent's
   `execute_fn` returns (executor writes `done`) or raises (executor writes
   `failed`). Observers (UIs, dashboards) will see fewer transient states. Tools
   that polled for `cancelling` as an intermediate signal during a child-failure
   scenario will need to update.

3. Parent terminal state on a child failure is now strictly `failed` (re-raise) or
   `done` (catch-and-return). It is **never** `cancelled` unless an external
   cancellation actually occurred somewhere in the subtree.

## Out of scope

- **Proposal 2** (`cancel_source` tag on `_CancelEntry` or `ProcessContext`):
  unnecessary after the mechanism fix — `should_continue()` becomes a reliable
  discriminator.

- **Proposal 3** (stabilize `ExceptionGroup.exceptions` typing): independent
  contract polish. Could be done separately. Not required to fix this bug.

- **Excavator workaround removal**: handled in a separate Excavator-side PR.

- **`update_status` state-machine validation**: today `update_status` is a raw
  Mongo `$set` and does not validate transitions. This is a pre-existing concern.
  Not addressed here.

## References

### Code

- `packages/optio-core/src/optio_core/context.py:594-672` — `ParallelGroup`,
  `_run`, breach detection.
- `packages/optio-core/src/optio_core/context.py:682-698` — `__aexit__` raises
  `ExceptionGroup`.
- `packages/optio-core/src/optio_core/executor.py:38-46` — `_CancelEntry`.
- `packages/optio-core/src/optio_core/executor.py:140-160` — cancellation flag
  setup per process.
- `packages/optio-core/src/optio_core/executor.py:197-268` — `_execute_process`
  try/except, terminal-state writes.
- `packages/optio-core/src/optio_core/executor.py:345-366` — `execute_child`
  abnormal-child handling.
- `packages/optio-core/src/optio_core/executor.py:373-396` —
  `request_cancel_with_deadline`.
- `packages/optio-core/src/optio_core/lifecycle.py:172-176` — wiring of
  `notify_parent_abnormal` to `Optio.cancel`.
- `packages/optio-core/src/optio_core/lifecycle.py:413-536` — `Optio.cancel`,
  recursive descent.
- `packages/optio-core/src/optio_core/state_machine.py` — valid transitions
  (informational; not enforced by `update_status`).

### Tests to modify

- `packages/optio-core/tests/test_cancel_propagation.py` —
  `test_parallel_group_cancel_propagates_to_siblings`,
  `test_parallel_group_fail_fast_under_alpha`.
- `packages/optio-core/tests/test_child_failure_structured.py` —
  `test_parallel_group_mixed_cancel_and_fail_synthesizes_for_cancelled`.

### External report

`/tmp/optio-report.md` — Excavator-side reproduction report (2026-05-26). The
report's claim that "the parent process row ends `failed`" is correct only when
the parent's `execute_fn` re-raises; in Excavator's actual implementation the
parent catches and returns, producing the observed `cancelled` row in the optio
process tree.
