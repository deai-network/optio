# Child Result Channel (D5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parent tasks can launch a child and receive the child's `publish_result` object while the child keeps running, via `ctx.run_child_with_result` / `ctx.run_child_task_with_result` returning a `ChildHandle`.

**Architecture:** Pure parent-side wrapper over the existing `run_child` machinery — pre-register the result future (`ensure_result_future`), spawn `run_child` as an asyncio task, await first-of {future, child task, timeout}. No executor changes; children already flow through `_execute_process`, which handles publish/registry/cleanup.

**Tech Stack:** Python 3.11+, asyncio, pytest + pytest-asyncio, MongoDB via Docker (existing `mongo_db` fixture).

**Spec:** `docs/2026-06-10-child-result-channel-design.md`

**Working directory:** repo root of the `csillag/convo-scripter` worktree. Test commands run from `packages/optio-core`; the venv is at repo root (`../../.venv/bin/pytest`).

---

### Task 1: `ChildHandle` model + `ResultNotPublished.state`

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py` (after `ChildOutcome`, ~line 104)
- Modify: `packages/optio-core/src/optio_core/exceptions.py:39-46` (`ResultNotPublished`)
- Test: `packages/optio-core/tests/test_child_result_channel.py` (new)

- [ ] **Step 1.1: Write the failing test**

Create `packages/optio-core/tests/test_child_result_channel.py`:

```python
"""run_child_with_result / ChildHandle matrix.

Spec: docs/2026-06-10-child-result-channel-design.md
"""
import asyncio

import pytest

from optio_core.exceptions import ChildProcessFailed, ResultNotPublished
from optio_core.lifecycle import Optio
from optio_core.models import ChildHandle, ChildOutcome, TaskInstance, TaskInstanceCore


async def test_childhandle_outcome_awaitable_repeatedly():
    """outcome() awaits the wrapped task; repeat awaits return the same value."""
    async def body() -> ChildOutcome:
        return ChildOutcome(state="done")

    task = asyncio.ensure_future(body())
    handle = ChildHandle(result={"x": 1}, task=task)
    assert handle.result == {"x": 1}
    out1 = await handle.outcome()
    out2 = await handle.outcome()
    assert out1.state == "done"
    assert out2 is out1


def test_result_not_published_carries_state():
    e = ResultNotPublished("pid-1", state="cancelled")
    assert e.process_id == "pid-1"
    assert e.state == "cancelled"
    # Old single-arg form still works (used by executor.py).
    e2 = ResultNotPublished("pid-2")
    assert e2.state is None
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run (from `packages/optio-core`): `../../.venv/bin/pytest tests/test_child_result_channel.py -v`
Expected: FAIL — `ImportError: cannot import name 'ChildHandle'`.

- [ ] **Step 1.3: Implement `ChildHandle` and extend `ResultNotPublished`**

In `packages/optio-core/src/optio_core/models.py`, add directly after the `ChildOutcome` dataclass (~line 104). `models.py` does not currently import asyncio; add `import asyncio` to its imports:

```python
class ChildHandle:
    """Returned by ProcessContext.run_child_with_result /
    run_child_task_with_result: the child's published result object,
    available while the child keeps running, plus access to the child's
    eventual outcome.

    ``outcome()`` awaits the underlying child execution; it returns the
    ChildOutcome or raises ChildProcessFailed exactly as a plain
    ``run_child`` call would have. It may be awaited multiple times.
    """

    def __init__(self, result: Any, task: "asyncio.Task[ChildOutcome]"):
        self.result = result
        self._task = task

    async def outcome(self) -> "ChildOutcome":
        return await self._task
```

In `packages/optio-core/src/optio_core/exceptions.py`, replace the `ResultNotPublished.__init__` (keep the class docstring):

```python
    def __init__(self, process_id: str, state: str | None = None):
        self.process_id = process_id
        self.state = state
        suffix = f" (terminal state: {state})" if state else ""
        super().__init__(
            f"process '{process_id}' ended without publishing a result{suffix}"
        )
```

In `packages/optio-core/src/optio_core/__init__.py`, add `ChildHandle` to the `from optio_core.models import (...)` block and to `__all__` (next to the other model exports).

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `../../.venv/bin/pytest tests/test_child_result_channel.py -v`
Expected: 2 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py \
        packages/optio-core/src/optio_core/exceptions.py \
        packages/optio-core/src/optio_core/__init__.py \
        packages/optio-core/tests/test_child_result_channel.py
git commit -m "feat(core): ChildHandle model; ResultNotPublished carries terminal state"
```

---

### Task 2: `run_child_with_result` happy path + sugar

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py` (insert after `run_child_task`, ~line 480)
- Test: `packages/optio-core/tests/test_child_result_channel.py`

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/test_child_result_channel.py` (harness helpers mirror
`tests/test_publish_result.py`):

```python
import time as _time


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _define(optio: Optio, process_id: str, execute) -> None:
    await optio.adhoc_define(
        TaskInstance(execute=execute, process_id=process_id, name=process_id),
    )


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 5.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in {"done", "failed", "cancelled"}:
            return proc
        await asyncio.sleep(0.02)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def test_child_publish_then_await(mongo_db):
    """Child publishes immediately (before parent awaits); object delivered;
    child keeps running until released; outcome() returns done."""
    release = asyncio.Event()
    seen: dict = {}

    async def child_exec(ctx):
        ctx.publish_result({"conv": 42})
        await release.wait()

    async def parent_exec(ctx):
        handle = await ctx.run_child_with_result(child_exec, "child-pub-1", "Child")
        seen["result"] = handle.result
        seen["live"] = True
        release.set()
        out = await handle.outcome()
        seen["state"] = out.state

    optio = await _make_optio(mongo_db, "chres1")
    await _define(optio, "parent-1", parent_exec)
    await optio.launch_and_wait("parent-1", session_id=None)
    assert seen["result"] == {"conv": 42}
    assert seen["state"] == "done"
    await optio.shutdown(grace_seconds=0.5)


async def test_child_await_then_publish(mongo_db):
    """Parent awaits first; child publishes after a delay."""
    seen: dict = {}

    async def child_exec(ctx):
        await asyncio.sleep(0.2)
        ctx.publish_result("late")

    async def parent_exec(ctx):
        handle = await ctx.run_child_with_result(child_exec, "child-pub-2", "Child")
        seen["result"] = handle.result
        out = await handle.outcome()
        seen["state"] = out.state

    optio = await _make_optio(mongo_db, "chres2")
    await _define(optio, "parent-2", parent_exec)
    await optio.launch_and_wait("parent-2", session_id=None)
    assert seen["result"] == "late"
    assert seen["state"] == "done"
    await optio.shutdown(grace_seconds=0.5)


async def test_run_child_task_with_result_sugar(mongo_db):
    """The TaskInstanceCore variant unpacks and delegates."""
    seen: dict = {}

    async def child_exec(ctx):
        ctx.publish_result(ctx.params.get("tag"))

    async def parent_exec(ctx):
        task = TaskInstanceCore(
            execute=child_exec, process_id="child-sugar-1",
            name="Sugar child", params={"tag": "via-task"},
        )
        handle = await ctx.run_child_task_with_result(task)
        seen["result"] = handle.result
        await handle.outcome()

    optio = await _make_optio(mongo_db, "chres3")
    await _define(optio, "parent-3", parent_exec)
    await optio.launch_and_wait("parent-3", session_id=None)
    assert seen["result"] == "via-task"
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `../../.venv/bin/pytest tests/test_child_result_channel.py -v`
Expected: the three new tests FAIL — `AttributeError: 'ProcessContext' object has no attribute 'run_child_with_result'` (surfaced inside the parent task as a failed process / `ChildProcessFailed`-style failure of `launch_and_wait`; the two Task-1 tests still PASS).

- [ ] **Step 2.3: Implement the wrapper methods**

In `packages/optio-core/src/optio_core/context.py`, insert after `run_child_task`
(~line 480). `ChildHandle` must be added to the existing
`from optio_core.models import (...)` block at the top of the file;
`ResultNotPublished` is imported lazily inside the method (matching the
executor's local-import idiom and avoiding import-cycle surprises):

```python
    async def run_child_with_result(
        self,
        execute: Callable[..., Awaitable[None]],
        process_id: str,
        name: str,
        params: dict[str, Any] | None = None,
        survive_failure: bool = False,
        survive_cancel: bool = False,
        on_child_progress: Callable | None = None,
        description: str | None = None,
        *,
        timeout: float | None = None,
    ) -> ChildHandle:
        """Launch a child like ``run_child`` and wait for it to call
        ``ctx.publish_result(obj)``; return a ChildHandle carrying that
        object while the child keeps running.

        Raises ResultNotPublished when the child ends (or refuses to spawn)
        without publishing, ChildProcessFailed when the child fails before
        publishing (survive_failure semantics unchanged), and
        asyncio.TimeoutError on ``timeout`` expiry (the child keeps running;
        the object remains obtainable via Optio.get_published_result).
        """
        from optio_core.exceptions import ResultNotPublished

        if self._executor is None:
            raise RuntimeError("Executor not set on context")
        fut = self._executor.ensure_result_future(process_id)
        child = asyncio.create_task(self.run_child(
            execute, process_id, name, params=params,
            survive_failure=survive_failure, survive_cancel=survive_cancel,
            on_child_progress=on_child_progress, description=description,
        ))
        # If the caller never awaits .outcome(), retrieve the exception so
        # asyncio doesn't log "exception was never retrieved". The failure
        # already propagated through execute_child's parent notification.
        child.add_done_callback(
            lambda t: None if t.cancelled() else t.exception()
        )
        done, _pending = await asyncio.wait(
            {fut, child}, timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise asyncio.TimeoutError(
                f"child '{process_id}' did not publish a result "
                f"within {timeout}s"
            )
        if fut.done() and not fut.cancelled() and fut.exception() is None:
            return ChildHandle(result=fut.result(), task=child)
        # No result is coming: the child ended (or is ending) without
        # publishing. Await it — a failed child raises ChildProcessFailed
        # here (richer than a bare ResultNotPublished). Note fut.exception()
        # above also marked any future exception as retrieved.
        outcome = await child
        # Refused-spawn path: no process doc was created, so the executor's
        # terminal cleanup never ran — drop the pre-registered future.
        self._executor._result_futures.pop(process_id, None)
        raise ResultNotPublished(process_id, state=outcome.state)

    async def run_child_task_with_result(
        self,
        task: TaskInstanceCore,
        *,
        survive_failure: bool = False,
        survive_cancel: bool = False,
        on_child_progress: Callable | None = None,
        timeout: float | None = None,
    ) -> ChildHandle:
        """Run a TaskInstance(Core) as a child and await its published
        result. Convenience over ``run_child_with_result``, unpacking the
        same fields as ``run_child_task``.
        """
        return await self.run_child_with_result(
            execute=task.execute,
            process_id=task.process_id,
            name=task.name,
            params=task.params,
            survive_failure=survive_failure,
            survive_cancel=survive_cancel,
            on_child_progress=on_child_progress,
            description=task.description,
            timeout=timeout,
        )
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `../../.venv/bin/pytest tests/test_child_result_channel.py -v`
Expected: 5 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add packages/optio-core/src/optio_core/context.py \
        packages/optio-core/tests/test_child_result_channel.py
git commit -m "feat(core): run_child_with_result / run_child_task_with_result"
```

---

### Task 3: Error paths — no-publish, failure, refused spawn, timeout

**Files:**
- Test: `packages/optio-core/tests/test_child_result_channel.py`
- Modify (only if a test exposes a gap): `packages/optio-core/src/optio_core/context.py`

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/test_child_result_channel.py`:

```python
async def test_child_done_without_publish(mongo_db):
    seen: dict = {}

    async def child_exec(ctx):
        return  # ends without publishing

    async def parent_exec(ctx):
        try:
            await ctx.run_child_with_result(child_exec, "child-np-1", "Child")
        except ResultNotPublished as e:
            seen["exc"] = e

    optio = await _make_optio(mongo_db, "chres4")
    await _define(optio, "parent-4", parent_exec)
    await optio.launch_and_wait("parent-4", session_id=None)
    assert seen["exc"].process_id == "child-np-1"
    assert seen["exc"].state == "done"
    await optio.shutdown(grace_seconds=0.5)


async def test_child_fails_before_publish(mongo_db):
    seen: dict = {}

    async def child_exec(ctx):
        raise ValueError("boom")

    async def parent_exec(ctx):
        try:
            await ctx.run_child_with_result(child_exec, "child-fail-1", "Child")
        except ChildProcessFailed as e:
            seen["exc"] = e

    optio = await _make_optio(mongo_db, "chres5")
    await _define(optio, "parent-5", parent_exec)
    await optio.launch_and_wait("parent-5", session_id=None)
    assert isinstance(seen["exc"].original, ValueError)
    await optio.shutdown(grace_seconds=0.5)


async def test_refused_spawn_when_parent_cancelled(mongo_db):
    """Parent's cancellation flag is set before spawning: run_child refuses
    (no process doc), and the wrapper raises ResultNotPublished promptly
    with state='cancelled' and cleans up its pre-registered future."""
    seen: dict = {}

    async def child_exec(ctx):
        ctx.publish_result("never")

    async def parent_exec(ctx):
        ctx._cancellation_flag.set()  # simulate cancel arriving first
        try:
            await asyncio.wait_for(
                ctx.run_child_with_result(child_exec, "child-ref-1", "Child"),
                timeout=5,
            )
        except ResultNotPublished as e:
            seen["exc"] = e
        seen["future_cleaned"] = (
            "child-ref-1" not in ctx._executor._result_futures
        )

    optio = await _make_optio(mongo_db, "chres6")
    await _define(optio, "parent-6", parent_exec)
    await optio.launch_and_wait("parent-6", session_id=None)
    assert seen["exc"].state == "cancelled"
    assert seen["future_cleaned"] is True
    await optio.shutdown(grace_seconds=0.5)


async def test_timeout_keeps_child_running(mongo_db):
    seen: dict = {}

    async def child_exec(ctx):
        await asyncio.sleep(0.5)
        ctx.publish_result("eventually")
        await asyncio.sleep(0.2)

    async def parent_exec(ctx):
        try:
            await ctx.run_child_with_result(
                child_exec, "child-to-1", "Child", timeout=0.1,
            )
        except asyncio.TimeoutError:
            seen["timed_out"] = True
        # The child keeps running; the object is retrievable once published.
        for _ in range(100):
            obj = ctx._executor.get_published_result("child-to-1")
            if obj is not None:
                seen["late"] = obj
                break
            await asyncio.sleep(0.02)

    optio = await _make_optio(mongo_db, "chres7")
    await _define(optio, "parent-7", parent_exec)
    await optio.launch_and_wait("parent-7", session_id=None)
    assert seen["timed_out"] is True
    assert seen["late"] == "eventually"
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 3.2: Run the tests**

Run: `../../.venv/bin/pytest tests/test_child_result_channel.py -v`
Expected: all PASS if Task 2's implementation is complete. If any of the four
fails, fix `run_child_with_result` (the likeliest gap: the refused-spawn
future cleanup, or `asyncio.wait` set construction) — do not weaken the tests.

Note for `test_child_fails_before_publish`: the parent catches
`ChildProcessFailed`, so `launch_and_wait("parent-5", ...)` ends with the
parent in state `done` — the failure-breach machinery only schedules
parent-failure notification, which the parent here survives by catching.
If the run instead shows the parent `failed`, the wrapper is re-raising
something other than `ChildProcessFailed` — fix the implementation.

- [ ] **Step 3.3: Commit**

```bash
git add packages/optio-core/tests/test_child_result_channel.py
git commit -m "test(core): child result channel error paths"
```

---

### Task 4: Parallel children + full-suite verification

**Files:**
- Test: `packages/optio-core/tests/test_child_result_channel.py`

- [ ] **Step 4.1: Write the parallel-children test**

Append to `tests/test_child_result_channel.py`:

```python
async def test_parallel_children_distinct_pids(mongo_db):
    """Two concurrent result-bearing children with distinct process_ids:
    both results delivered, no registry collision."""
    release = asyncio.Event()
    seen: dict = {}

    def make_child(tag):
        async def child_exec(ctx):
            ctx.publish_result(tag)
            await release.wait()
        return child_exec

    async def parent_exec(ctx):
        h1, h2 = await asyncio.gather(
            ctx.run_child_with_result(make_child("a"), "child-par-a", "A"),
            ctx.run_child_with_result(make_child("b"), "child-par-b", "B"),
        )
        seen["results"] = {h1.result, h2.result}
        release.set()
        o1 = await h1.outcome()
        o2 = await h2.outcome()
        seen["states"] = {o1.state, o2.state}

    optio = await _make_optio(mongo_db, "chres8")
    await _define(optio, "parent-8", parent_exec)
    await optio.launch_and_wait("parent-8", session_id=None)
    assert seen["results"] == {"a", "b"}
    assert seen["states"] == {"done"}
    await optio.shutdown(grace_seconds=0.5)
```

- [ ] **Step 4.2: Run the new test**

Run: `../../.venv/bin/pytest tests/test_child_result_channel.py -v`
Expected: all 10 PASS.

- [ ] **Step 4.3: Run the full optio-core suite (regression gate)**

Run (from `packages/optio-core`): `../../.venv/bin/pytest -q`
Expected: all pass (baseline was 381 passed; now 391). Any failure outside
`test_child_result_channel.py` means a regression — investigate before
proceeding (the implementation is additive; regressions are most plausible in
executor/cancel tests if the wrapper mishandles task scheduling).

- [ ] **Step 4.4: Commit**

```bash
git add packages/optio-core/tests/test_child_result_channel.py
git commit -m "test(core): parallel result-bearing children"
```
