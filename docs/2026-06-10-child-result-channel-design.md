# optio-core Child Result Channel — Design

This spec was written against the following baseline:

**Base revision:** `a7695052b3424949a1a7284573b8f34d2765532b` on branch `csillag/convo-scripter` (as of 2026-06-10T06:27:16Z)

## Summary

Extend the conversation-gate Phase I result channel (`ctx.publish_result` /
`launch_and_await_result`) to **child processes**: a parent task launches a child via
the existing `run_child` machinery and receives the child's published result object
while the child keeps running, plus a handle to await the child's eventual outcome.

Primary consumer: optio-conversation-scripter (work item D5 in
`2026-06-10-conversation-scripter-design.md`), which launches conversation-mode
optio-claudecode children and needs the live `Conversation` object out of them.

**Verified before design**: children execute through the same `_execute_process` as
top-level tasks (`executor.py` — `execute_child` delegates to it), so the result
machinery already works for children end to end: context-side `publish_result`,
pid-keyed registry and future resolution, and the terminal `finally` that pops the
registry entry and fails a still-pending future with `ResultNotPublished`. D5 is
therefore a parent-side awaiting wrapper only — **no executor changes**.

## API

Two new `ProcessContext` methods in `optio_core/context.py`, mirroring the existing
`run_child` / `run_child_task` core-plus-sugar layering:

```python
async def run_child_with_result(
    self, execute, process_id, name,
    params=None, survive_failure=False, survive_cancel=False,
    on_child_progress=None, description=None,
    *, timeout: float | None = None,
) -> ChildHandle

async def run_child_task_with_result(
    self, task: TaskInstanceCore, *,
    survive_failure=False, survive_cancel=False,
    on_child_progress=None, timeout: float | None = None,
) -> ChildHandle
```

The task variant unpacks the `TaskInstanceCore` fields (execute / process_id / name /
description / params) and delegates to the core variant, exactly like
`run_child_task` → `run_child`.

`ChildHandle` is a small class in `optio_core/models.py`:

```python
class ChildHandle:
    result: Any                                # the published object
    async def outcome(self) -> ChildOutcome    # awaits child completion
```

`outcome()` awaits the internal child task; it returns the `ChildOutcome` or raises
`ChildProcessFailed`, exactly as a plain `run_child` call would have. It may be
awaited multiple times.

## Semantics

Mirrors `launch_and_await_result`, adapted to child execution:

1. **Pre-register the waiter**: `ensure_result_future(process_id)` before spawning,
   so a child that publishes instantly cannot race the waiter (same pattern as
   `launch_and_await_result`, `lifecycle.py`).
2. **Spawn**: `asyncio.create_task(self.run_child(...))` — all existing child
   semantics (tree representation, cancel cascade up and down, failure breach
   notification, `on_child_progress`) are inherited unchanged.
3. **Await first of {future, child task}**:
   - **Future resolves** → return `ChildHandle(result, child_task)`; the child keeps
     running.
   - **Child task finishes first** (refused spawn, instant failure, or
     done-without-publish): if it raised `ChildProcessFailed`, re-raise it (richer
     than a bare `ResultNotPublished`); otherwise raise `ResultNotPublished`
     carrying the process_id and outcome state. When a process doc existed, the
     pending future was already failed/cleaned by `_execute_process`'s `finally`;
     on the refused-spawn path (parent already cancelled → no process doc, no
     `_execute_process` run) the wrapper pops the future itself.
   - **Timeout** (`timeout` set) → `asyncio.TimeoutError`; the child keeps running
     and the future stays registered — the caller may still obtain the object later
     via `get_published_result(process_id)`.
4. **No unretrieved-exception noise**: a done-callback on the internal task
   retrieves its exception when the caller never awaits `.outcome()`. Nothing is
   silently lost — child failure already propagated through `execute_child`'s
   parent-notification cascade.

## Constraints

- **Unique process_ids for concurrent result-bearing children**: the result registry
  and future map are pid-keyed, so concurrent children sharing a process_id would
  collide. Documented caller contract (Excavator already mints per-call pids;
  the conversation scripter will too). Sequential reuse of a pid is fine — the
  terminal `finally` clears the slot.
- Backward compatible by construction: purely additive methods; no changes to
  `run_child`, `run_child_task`, the executor, or the wire/storage formats.

## Error handling summary

| Situation | Behavior |
|---|---|
| Child publishes, keeps running | `ChildHandle` returned with the object |
| Child ends without publishing (done or cancelled) | `ResultNotPublished` |
| Child fails before publishing | `ChildProcessFailed` (from the wrapper call) |
| Spawn refused (parent already cancelled) | `ResultNotPublished`; wrapper cleans its own future |
| Timeout expires | `asyncio.TimeoutError`; child keeps running; `get_published_result` still works |
| Child fails after publishing | `ChildHandle` already returned; `.outcome()` raises `ChildProcessFailed`; parent-notification cascade unchanged |
| `.outcome()` never awaited | Done-callback retrieves the exception; no asyncio warning |
| `publish_result` called twice in one child run | `RuntimeError` in the child (existing behavior) |

## Testing

New tests in `packages/optio-core/tests/` on the existing harness (plain fake tasks,
MongoDB via Docker):

- publish-then-await and await-then-publish orderings through a child
- done-without-publish → `ResultNotPublished`
- failing child → `ChildProcessFailed` raised by the wrapper
- refused spawn (parent already cancelled) → prompt raise, no hang, future cleaned
- timeout → `asyncio.TimeoutError`, then late publish retrievable via
  `get_published_result`
- `.outcome()` returns terminal `ChildOutcome` after `close`/completion; repeat await
- two parallel children with distinct pids, both results delivered
- `run_child_task_with_result` sugar delegates correctly (one happy-path test)

## File map

| File | Change |
|---|---|
| `optio-core/src/optio_core/context.py` | `run_child_with_result`, `run_child_task_with_result` |
| `optio-core/src/optio_core/models.py` | `ChildHandle` |
| `optio-core/tests/test_child_result_channel.py` | new test module |
