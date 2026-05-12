# Child Failure Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve structured exceptions across the `run_child` / `parallel_group` boundary in `optio-core`, so parents can `except ChildProcessFailed as e: isinstance(e.original, DownloadFailed)`.

**Architecture:** Direct return tuple from `_execute_process` carries the live exception object up to `execute_child`, which wraps it in a typed `ChildProcessFailed(name, process_id, original)` and either raises (default) or returns inside `ChildOutcome(state, original)` (`survive_failure=True`). `ParallelGroup.__aexit__` aggregates into `ExceptionGroup[ChildProcessFailed]`. No persistence change, no cross-process / resume support.

**Tech Stack:** Python 3.11+, `motor` (async Mongo), `pytest-asyncio`. Builtin `ExceptionGroup` and `except*` syntax. All work in `packages/optio-core/`.

**Reference spec:** `docs/2026-05-12-child-failure-propagation-design.md`.

**Test command (run from repo root):** `cd packages/optio-core && pytest`.
**Per-test command:** `cd packages/optio-core && pytest tests/<file>::<test_name> -v`.
**Working dir convention:** All file paths below are relative to the repo root (`/home/csillag/deai/optio`).

**Branch:** Already on `feat/optio-host-download-file`. All commits go on this branch.

---

## File Structure

**Created:**
- `packages/optio-core/src/optio_core/exceptions.py` — new module, `ChildProcessFailed` class.
- `packages/optio-core/tests/test_child_failure_structured.py` — new test file for all the new structured-failure tests in this plan.

**Modified:**
- `packages/optio-core/src/optio_core/models.py` — add `ChildOutcome` dataclass; extend `ChildResult` with `name` and `original_exception` fields.
- `packages/optio-core/src/optio_core/executor.py` — `_execute_process` return shape; `execute_child` return shape + raise type.
- `packages/optio-core/src/optio_core/context.py` — `ProcessContext.run_child` return type; `ParallelGroup.spawn._run` and `ParallelGroup.__aexit__` rewrites.
- `packages/optio-core/tests/test_executor.py` — migrate `test_child_failure_survived` to new return type.
- `packages/optio-core/tests/test_cancel_propagation.py` — migrate refusal-state assertion + stale comment.
- `packages/optio-core/AGENTS.md` — document new public types and revised contract.

---

## Task 1: Create `ChildProcessFailed` exception type

**Files:**
- Create: `packages/optio-core/src/optio_core/exceptions.py`
- Create: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the failing test**

Create file `packages/optio-core/tests/test_child_failure_structured.py`:

```python
"""Tests for structured child failure propagation."""
import pytest

from optio_core.exceptions import ChildProcessFailed


class _SampleErr(Exception):
    def __init__(self, url: str, exit_code: int):
        self.url = url
        self.exit_code = exit_code
        super().__init__(f"sample error for {url}")


def test_child_process_failed_carries_name_pid_and_original():
    original = _SampleErr("http://example.com", 42)
    cpf = ChildProcessFailed("Sample Child", "child-pid", original)
    assert cpf.name == "Sample Child"
    assert cpf.process_id == "child-pid"
    assert cpf.original is original
    assert isinstance(cpf.original, _SampleErr)
    assert cpf.original.url == "http://example.com"
    assert cpf.original.exit_code == 42


def test_child_process_failed_message_includes_repr_of_original():
    original = _SampleErr("u", 1)
    cpf = ChildProcessFailed("N", "P", original)
    # Message should include the child name, pid, and a repr of the original
    msg = str(cpf)
    assert "N" in msg
    assert "P" in msg
    assert repr(original) in msg


def test_child_process_failed_is_an_exception():
    original = _SampleErr("u", 1)
    cpf = ChildProcessFailed("N", "P", original)
    assert isinstance(cpf, Exception)
    with pytest.raises(ChildProcessFailed):
        raise cpf
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'optio_core.exceptions'`.

- [ ] **Step 3: Create the exceptions module**

Create file `packages/optio-core/src/optio_core/exceptions.py`:

```python
"""Exception types raised by optio-core to parent task code."""


class ChildProcessFailed(Exception):
    """Raised by ProcessContext.run_child when a child task fails and
    survive_failure=False. Carries the child's identifying name, its
    process_id, and the original exception raised inside the child's
    execute function.

    Parents catch this and branch on isinstance(e.original, SomeType):

        try:
            await ctx.run_child(...)
        except ChildProcessFailed as e:
            if isinstance(e.original, DownloadFailed):
                ...
    """

    def __init__(self, name: str, process_id: str, original: BaseException):
        self.name = name
        self.process_id = process_id
        self.original = original
        super().__init__(
            f"Child '{name}' (process_id={process_id}) failed: {original!r}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/exceptions.py packages/optio-core/tests/test_child_failure_structured.py
git commit -m "feat(optio-core): ChildProcessFailed exception type"
```

---

## Task 2: Add `ChildOutcome` dataclass

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py`
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_child_failure_structured.py`:

```python
from optio_core.models import ChildOutcome


def test_child_outcome_default_no_exception():
    outcome = ChildOutcome(state="done")
    assert outcome.state == "done"
    assert outcome.original_exception is None


def test_child_outcome_with_exception():
    exc = _SampleErr("u", 9)
    outcome = ChildOutcome(state="failed", original_exception=exc)
    assert outcome.state == "failed"
    assert outcome.original_exception is exc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_child_outcome_default_no_exception -v`
Expected: FAIL with `ImportError: cannot import name 'ChildOutcome' from 'optio_core.models'`.

- [ ] **Step 3: Add `ChildOutcome` to models.py**

Edit `packages/optio-core/src/optio_core/models.py`. After the existing `ChildResult` dataclass (around line 51-55), add:

```python
@dataclass
class ChildOutcome:
    """Return value of ProcessContext.run_child.

    state: "done" | "failed" | "cancelled".
    original_exception: the exception object raised inside the child's
        execute function, if any. None for state in {"done", "cancelled"}
        and for "failed" only when the child failed via the no-execute-fn
        early-fail path (no real exception was raised).
    """
    state: str
    original_exception: BaseException | None = None
```

(`@dataclass` is already imported in this file; if not, add `from dataclasses import dataclass` at the top.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py -v`
Expected: All tests pass (5 now).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py packages/optio-core/tests/test_child_failure_structured.py
git commit -m "feat(optio-core): ChildOutcome dataclass"
```

---

## Task 3: Extend `ChildResult` with `name` and `original_exception`

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py:50-55`
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_child_failure_structured.py`:

```python
from optio_core.models import ChildResult


def test_child_result_defaults():
    r = ChildResult(process_id="p", state="done")
    assert r.process_id == "p"
    assert r.state == "done"
    assert r.error is None
    assert r.name == ""
    assert r.original_exception is None


def test_child_result_carries_name_and_original():
    exc = _SampleErr("u", 1)
    r = ChildResult(
        process_id="p", state="failed", error="Child failed",
        name="Sample", original_exception=exc,
    )
    assert r.name == "Sample"
    assert r.original_exception is exc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_child_result_defaults -v`
Expected: FAIL — `AssertionError: assert ... == ""` (no `name` attribute) or `TypeError: ChildResult.__init__() got an unexpected keyword argument 'name'`.

- [ ] **Step 3: Extend `ChildResult` dataclass**

Edit `packages/optio-core/src/optio_core/models.py` lines 50-55. Change:

```python
@dataclass
class ChildResult:
    """Result of a child process execution."""
    process_id: str
    state: str  # "done", "failed", "cancelled"
    error: str | None = None
```

to:

```python
@dataclass
class ChildResult:
    """Result of a child process execution."""
    process_id: str
    state: str  # "done", "failed", "cancelled"
    error: str | None = None
    name: str = ""
    original_exception: BaseException | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py -v`
Expected: All tests pass (7 now).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/models.py packages/optio-core/tests/test_child_failure_structured.py
git commit -m "feat(optio-core): extend ChildResult with name + original_exception"
```

---

## Task 4: Refactor `_execute_process` to return `(state, exc | None)` tuple

This is a pure plumbing change. The exception path begins to capture the live `e`. Callers (`execute()` root entry, `execute_child`) unpack the tuple. No new behavior — existing tests must still pass.

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:109` (root entry destructure)
- Modify: `packages/optio-core/src/optio_core/executor.py:165-225` (return-shape changes inside `_execute_process`)
- Modify: `packages/optio-core/src/optio_core/executor.py:261` (`execute_child` callsite destructure — temporary; reverted next task)

- [ ] **Step 1: Change `_execute_process` return values**

Edit `packages/optio-core/src/optio_core/executor.py`.

At line 174 (no-execute-fn early-fail), change:

```python
return "failed"
```

to:

```python
return ("failed", None)
```

At line 196 (exception-path return at the end of the `except Exception as e:` block), change:

```python
return "failed"
```

to:

```python
return ("failed", e)
```

At line 225 (success / cancelled return after end-of-function flush+update), change:

```python
return end_state
```

to:

```python
return (end_state, None)
```

(No other changes inside `_execute_process`. The catch already binds `e` via `except Exception as e:` — no rewrite of DB writes inside the block.)

- [ ] **Step 2: Update the root-entry caller (`execute()`)**

Edit `packages/optio-core/src/optio_core/executor.py:109`. Change:

```python
return await self._execute_process(
    proc, execute_fn, parent_ctx=None, resume=resume,
)
```

to:

```python
state, _ = await self._execute_process(
    proc, execute_fn, parent_ctx=None, resume=resume,
)
return state
```

(Line numbers may shift; locate the `_execute_process` call inside the public `execute()` method of `Executor` — it is the one with `parent_ctx=None`.)

- [ ] **Step 3: Update `execute_child` caller (temporary unpack)**

Edit `packages/optio-core/src/optio_core/executor.py:261`. Change:

```python
end_state = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)
```

to:

```python
end_state, _ = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)
```

(The exception object is discarded for now — captured properly in Task 5.)

- [ ] **Step 4: Run the full test suite**

Run: `cd packages/optio-core && pytest`
Expected: All existing tests pass. No new failures (pure refactor).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py
git commit -m "refactor(optio-core): _execute_process returns (state, exc) tuple"
```

---

## Task 5: Refactor `execute_child` and `run_child` to return `ChildOutcome`

Still raises generic `RuntimeError` on failure (changed in Task 6). This task migrates the two existing tests that capture and assert on the bare-string return value.

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py:230-280` (`execute_child` return type + capture exc)
- Modify: `packages/optio-core/src/optio_core/context.py:295-339` (`run_child` return type + refused-path return)
- Modify: `packages/optio-core/src/optio_core/context.py:546-583` (`ParallelGroup.spawn._run`: use `outcome.state`, populate `ChildResult.name` + `.original_exception`)
- Modify: `packages/optio-core/tests/test_executor.py:122-141` (`test_child_failure_survived` migration)
- Modify: `packages/optio-core/tests/test_cancel_propagation.py:283-304` (refusal-state assertion migration)

- [ ] **Step 1: Update `execute_child` to capture exc + return `ChildOutcome`**

Edit `packages/optio-core/src/optio_core/executor.py:261`. Change:

```python
end_state, _ = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)
```

to:

```python
end_state, exc = await self._execute_process(child_doc, execute, parent_ctx=parent_ctx)
```

Then locate the existing tail of `execute_child` (lines 278-279):

```python
if end_state == "failed" and not survive_failure:
    raise RuntimeError(f"Child process '{name}' failed")
```

Replace with:

```python
if end_state == "failed" and not survive_failure:
    raise RuntimeError(f"Child process '{name}' failed")
return ChildOutcome(end_state, exc if end_state == "failed" else None)
```

Change the function's return type annotation from `-> str` to `-> ChildOutcome` (signature at executor.py:230-240).

Add the import at the top of `executor.py` if not present:

```python
from optio_core.models import ChildOutcome
```

(Other models like `ProcessStatus` are likely already imported from this path — extend the existing import line.)

- [ ] **Step 2: Update `ProcessContext.run_child` signature + refused-path return**

Edit `packages/optio-core/src/optio_core/context.py:295-339`.

Change the return type annotation from `-> str` to `-> ChildOutcome` on line 305.

Change the refused-path return at line 327 from:

```python
return "cancelled"
```

to:

```python
return ChildOutcome(state="cancelled", original_exception=None)
```

Add `ChildOutcome` to the import on line 13:

```python
from optio_core.models import Progress, ChildResult, ChildProgressInfo, InnerAuth, ChildOutcome
```

- [ ] **Step 3: Update `ParallelGroup.spawn._run`**

Edit `packages/optio-core/src/optio_core/context.py:548-561`. The current block:

```python
state = await self._ctx.run_child(
    execute=execute,
    process_id=process_id,
    name=name,
    params=params,
    survive_failure=True,
    survive_cancel=True,
    description=description,
)
self._results.append(ChildResult(
    process_id=process_id,
    state=state,
    error=None if state == "done" else f"Child {state}",
))
```

becomes:

```python
outcome = await self._ctx.run_child(
    execute=execute,
    process_id=process_id,
    name=name,
    params=params,
    survive_failure=True,
    survive_cancel=True,
    description=description,
)
self._results.append(ChildResult(
    process_id=process_id,
    state=outcome.state,
    error=None if outcome.state == "done" else f"Child {outcome.state}",
    name=name,
    original_exception=outcome.original_exception,
))
```

Then update the breach detection a few lines below — replace `if state == "failed"` with `if outcome.state == "failed"` and `if state == "cancelled"` with `if outcome.state == "cancelled"`.

Also update the refused-spawn `ChildResult` at lines 539-542:

```python
self._results.append(ChildResult(
    process_id=process_id, state="cancelled",
    error="parent cancelled",
))
```

becomes:

```python
self._results.append(ChildResult(
    process_id=process_id, state="cancelled",
    error="parent cancelled",
    name=name, original_exception=None,
))
```

- [ ] **Step 4: Migrate `test_child_failure_survived`**

Edit `packages/optio-core/tests/test_executor.py` lines 127-132. Change:

```python
result = await ctx.run_child(
    failing_child, "bad_child2", "Bad Child",
    survive_failure=True,
)
assert result == "failed"
ctx.report_progress(100, "Parent survived")
```

to:

```python
result = await ctx.run_child(
    failing_child, "bad_child2", "Bad Child",
    survive_failure=True,
)
assert result.state == "failed"
ctx.report_progress(100, "Parent survived")
```

- [ ] **Step 5: Migrate refusal assertion in `test_cancel_propagation.py`**

Edit `packages/optio-core/tests/test_cancel_propagation.py` lines 284-304.

Line 284 currently:

```python
state = await ctx.run_child(
    execute=short_child, process_id="late_child", name="Late",
)
refusal_result["state"] = state
```

Change to:

```python
outcome = await ctx.run_child(
    execute=short_child, process_id="late_child", name="Late",
)
refusal_result["state"] = outcome.state
```

Line 304 assertion stays the same (`assert refusal_result["state"] == "cancelled"`) — it now compares the unwrapped `.state` string captured above.

Also update the docstring at lines 270-271 from:

```
is set, ctx.run_child returns 'cancelled' immediately without creating
a child doc.
```

to:

```
is set, ctx.run_child returns ChildOutcome(state="cancelled") immediately
without creating a child doc.
```

- [ ] **Step 6: Run the full test suite**

Run: `cd packages/optio-core && pytest`
Expected: All tests pass, including the two migrated ones. No new failures.

- [ ] **Step 7: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/src/optio_core/context.py packages/optio-core/tests/test_executor.py packages/optio-core/tests/test_cancel_propagation.py
git commit -m "refactor(optio-core): run_child + execute_child return ChildOutcome"
```

---

## Task 6: TDD — `execute_child` raises `ChildProcessFailed`

Switch the raise type. RED test asserts the new behavior; GREEN flips the raise.

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`
- Modify: `packages/optio-core/src/optio_core/executor.py:278-279`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-core/tests/test_child_failure_structured.py`:

```python
import asyncio
import pytest
from optio_core.executor import Executor
from optio_core.models import TaskInstance
from optio_core.exceptions import ChildProcessFailed
from optio_core.store import upsert_process


@pytest.mark.asyncio
async def test_run_child_raises_child_process_failed_with_original(mongo_db):
    """Child raises a structured exception; parent catches ChildProcessFailed
    and recovers the original via .original."""
    caught = {}

    async def failing_child(ctx):
        raise _SampleErr("http://example.com/bin", 42)

    async def parent_task(ctx):
        try:
            await ctx.run_child(
                failing_child,
                process_id="failing-child-1",
                name="Failing Child",
            )
        except ChildProcessFailed as e:
            caught["name"] = e.name
            caught["process_id"] = e.process_id
            caught["original"] = e.original
            # Suppress so the parent ends 'done' for assertion simplicity.

    parent_inst = TaskInstance(
        execute=parent_task, process_id="parent-cpf", name="Parent CPF",
    )
    child_inst = TaskInstance(
        execute=failing_child, process_id="failing-child-1", name="Failing Child",
    )

    await upsert_process(mongo_db, "test_cpf", parent_inst)
    executor = Executor(mongo_db, "test_cpf", {})
    executor.register_tasks([parent_inst, child_inst])

    result = await executor.launch_process("parent-cpf")
    assert result == "done"
    assert caught["name"] == "Failing Child"
    assert caught["process_id"] == "failing-child-1"
    assert isinstance(caught["original"], _SampleErr)
    assert caught["original"].url == "http://example.com/bin"
    assert caught["original"].exit_code == 42
```

The `mongo_db` fixture is provided by `packages/optio-core/tests/conftest.py` — look there to confirm the fixture name and any required marker setup. `@pytest.mark.asyncio` is the convention used by surrounding tests (see e.g. `tests/test_executor.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_run_child_raises_child_process_failed_with_original -v`
Expected: FAIL — the parent's `except ChildProcessFailed` does not match the currently-raised `RuntimeError`, so the exception escapes the parent's execute body, the parent ends `"failed"` instead of `"done"`, and `result == "done"` fails. (Or `KeyError: 'name'` because `caught` is never populated.)

- [ ] **Step 3: Switch the raise type in `execute_child`**

Edit `packages/optio-core/src/optio_core/executor.py:278-279`. Change:

```python
if end_state == "failed" and not survive_failure:
    raise RuntimeError(f"Child process '{name}' failed")
```

to:

```python
if end_state == "failed" and not survive_failure:
    if exc is None:
        exc = RuntimeError(f"Child process '{name}' failed")
    raise ChildProcessFailed(name, process_id, exc) from exc
```

The `if exc is None` branch handles the no-execute-fn early-fail path (`_execute_process` returned `("failed", None)`); the synthesized `RuntimeError` then becomes the `.original`.

Add `ChildProcessFailed` to imports at the top of `executor.py`:

```python
from optio_core.exceptions import ChildProcessFailed
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_run_child_raises_child_process_failed_with_original -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to verify no regressions**

Run: `cd packages/optio-core && pytest`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/executor.py packages/optio-core/tests/test_child_failure_structured.py
git commit -m "feat(optio-core): execute_child raises ChildProcessFailed with original"
```

---

## Task 7: TDD — `__cause__` chain

Verify that `raise ChildProcessFailed(...) from exc` sets the cause chain correctly.

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the test**

Append to `packages/optio-core/tests/test_child_failure_structured.py`:

```python
@pytest.mark.asyncio
async def test_child_process_failed_cause_chain_is_original(mongo_db):
    """ChildProcessFailed.__cause__ is the original exception instance."""
    caught_cpf = {}

    async def failing_child(ctx):
        raise _SampleErr("u", 7)

    async def parent_task(ctx):
        try:
            await ctx.run_child(
                failing_child, process_id="cc1", name="CC1",
            )
        except ChildProcessFailed as e:
            caught_cpf["e"] = e

    parent_inst = TaskInstance(
        execute=parent_task, process_id="parent-cc", name="Parent CC",
    )
    child_inst = TaskInstance(
        execute=failing_child, process_id="cc1", name="CC1",
    )
    await upsert_process(mongo_db, "test_cc", parent_inst)
    executor = Executor(mongo_db, "test_cc", {})
    executor.register_tasks([parent_inst, child_inst])
    await executor.launch_process("parent-cc")

    e = caught_cpf["e"]
    assert e.__cause__ is e.original
    assert isinstance(e.__cause__, _SampleErr)
```

- [ ] **Step 2: Run the test**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_child_process_failed_cause_chain_is_original -v`
Expected: PASS (Task 6's `raise ... from exc` already sets the chain).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_child_failure_structured.py
git commit -m "test(optio-core): __cause__ chain on ChildProcessFailed"
```

---

## Task 8: TDD — `survive_failure=True` returns `ChildOutcome` with original

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the test**

Append:

```python
@pytest.mark.asyncio
async def test_run_child_survive_failure_returns_outcome_with_original(mongo_db):
    """survive_failure=True suppresses the raise; caller gets ChildOutcome
    with state='failed' and original_exception populated."""
    outcomes = {}

    async def failing_child(ctx):
        raise _SampleErr("u", 11)

    async def parent_task(ctx):
        outcome = await ctx.run_child(
            failing_child, process_id="sf1", name="SF1",
            survive_failure=True,
        )
        outcomes["o"] = outcome

    parent_inst = TaskInstance(
        execute=parent_task, process_id="parent-sf", name="Parent SF",
    )
    child_inst = TaskInstance(
        execute=failing_child, process_id="sf1", name="SF1",
    )
    await upsert_process(mongo_db, "test_sf", parent_inst)
    executor = Executor(mongo_db, "test_sf", {})
    executor.register_tasks([parent_inst, child_inst])
    result = await executor.launch_process("parent-sf")

    assert result == "done"
    o = outcomes["o"]
    assert o.state == "failed"
    assert isinstance(o.original_exception, _SampleErr)
    assert o.original_exception.exit_code == 11
```

- [ ] **Step 2: Run the test**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_run_child_survive_failure_returns_outcome_with_original -v`
Expected: PASS (Task 5's plumbing already routes `exc` into `ChildOutcome`).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_child_failure_structured.py
git commit -m "test(optio-core): survive_failure surfaces original exception"
```

---

## Task 9: TDD — done and cancelled outcomes carry no exception

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the test**

Append:

```python
@pytest.mark.asyncio
async def test_run_child_done_outcome_is_none(mongo_db):
    """Successful child yields ChildOutcome('done', None)."""
    outcomes = {}

    async def ok_child(ctx):
        ctx.report_progress(100)

    async def parent_task(ctx):
        outcome = await ctx.run_child(ok_child, process_id="ok1", name="OK1")
        outcomes["o"] = outcome

    parent_inst = TaskInstance(execute=parent_task, process_id="parent-ok", name="Parent OK")
    child_inst = TaskInstance(execute=ok_child, process_id="ok1", name="OK1")
    await upsert_process(mongo_db, "test_ok", parent_inst)
    executor = Executor(mongo_db, "test_ok", {})
    executor.register_tasks([parent_inst, child_inst])
    await executor.launch_process("parent-ok")

    o = outcomes["o"]
    assert o.state == "done"
    assert o.original_exception is None


@pytest.mark.asyncio
async def test_run_child_refused_outcome_is_cancelled_no_exception(mongo_db):
    """Parent's cancel flag is set with auto_cancel_children -> run_child
    returns ChildOutcome('cancelled', None) without spawning."""
    outcomes = {}

    async def short_child(ctx):
        ctx.report_progress(100)

    async def parent_task(ctx):
        # Pre-set the cancellation flag to trigger the refused-spawn path
        # (the parent's TaskInstance has auto_cancel_children=True by default).
        ctx._cancellation_flag.set()
        outcome = await ctx.run_child(short_child, process_id="ref1", name="REF1")
        outcomes["o"] = outcome
        # Clear so the parent itself ends 'done' (not cancelled) for clean assertions.
        ctx._cancellation_flag.clear()

    parent_inst = TaskInstance(execute=parent_task, process_id="parent-ref", name="Parent REF")
    child_inst = TaskInstance(execute=short_child, process_id="ref1", name="REF1")
    await upsert_process(mongo_db, "test_ref", parent_inst)
    executor = Executor(mongo_db, "test_ref", {})
    executor.register_tasks([parent_inst, child_inst])
    await executor.launch_process("parent-ref")

    o = outcomes["o"]
    assert o.state == "cancelled"
    assert o.original_exception is None
```

- [ ] **Step 2: Run the test**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_run_child_done_outcome_is_none tests/test_child_failure_structured.py::test_run_child_refused_outcome_is_cancelled_no_exception -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_child_failure_structured.py
git commit -m "test(optio-core): ChildOutcome shape for done and cancelled"
```

---

## Task 10: TDD — no-execute-fn synthesizes `RuntimeError` as `.original`

When a child is run but the executor cannot find a registered execute function, `_execute_process` returns `("failed", None)`. `execute_child` synthesizes `RuntimeError("Child process '<name>' failed")` so `ChildProcessFailed.original` is never `None`.

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the test**

Append:

```python
@pytest.mark.asyncio
async def test_no_execute_fn_synthesizes_runtimeerror_as_original(mongo_db):
    """When _execute_process receives execute_fn=None it hits the
    no-execute-fn early-fail path (executor.py:165-174), returning
    ('failed', None). execute_child must still raise ChildProcessFailed and
    synthesize a RuntimeError as .original so .original is never None.

    Forced by passing execute=None to run_child; _execute_process forwards
    that as execute_fn=None and hits the early-fail branch before any
    user code runs."""
    caught = {}

    async def parent_task(ctx):
        try:
            await ctx.run_child(
                execute=None,                  # type: ignore[arg-type]
                process_id="missing-child",
                name="Missing",
            )
        except ChildProcessFailed as e:
            caught["e"] = e

    parent_inst = TaskInstance(execute=parent_task, process_id="p-miss", name="P Miss")
    await upsert_process(mongo_db, "test_miss", parent_inst)
    executor = Executor(mongo_db, "test_miss", {})
    executor.register_tasks([parent_inst])
    await executor.launch_process("p-miss")

    e = caught["e"]
    assert e.name == "Missing"
    assert isinstance(e.original, RuntimeError)
    # The synthesized RuntimeError carries the message from execute_child's
    # `f"Child process '{name}' failed"` fallback.
    assert "Missing" in str(e.original) or "failed" in str(e.original).lower()
```

- [ ] **Step 2: Run the test to verify it currently passes**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_no_execute_fn_synthesizes_runtimeerror_as_original -v`
Expected: PASS (Task 6's `if exc is None: exc = RuntimeError(...)` branch already covers this).

If it FAILS, fix the synthesis in `execute_child` so `.original` becomes a `RuntimeError` matching the message used in the Mongo `status.error` text (`"No execute function found"` if the early-fail path is the one being hit; or the generic `f"Child process '{name}' failed"` synthesized in Task 6). Pick the message that matches what `_execute_process` actually wrote to Mongo, so debugging gives consistent strings.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_child_failure_structured.py
# Possibly include executor.py if synthesis message had to be adjusted.
git commit -m "test(optio-core): no-execute-fn synthesizes RuntimeError as original"
```

---

## Task 11: TDD — `ParallelGroup` `ChildResult` carries `original_exception`

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the test**

Append:

```python
@pytest.mark.asyncio
async def test_parallel_group_results_carry_originals(mongo_db):
    """survive_failure=True at group level. group.results[i].original_exception
    is populated for failed children."""
    captured_results = {}

    async def fail_a(ctx):
        raise _SampleErr("ua", 1)

    async def fail_b(ctx):
        raise _SampleErr("ub", 2)

    async def parent_task(ctx):
        async with ctx.parallel_group(survive_failure=True) as g:
            await g.spawn(execute=fail_a, process_id="pa", name="PA")
            await g.spawn(execute=fail_b, process_id="pb", name="PB")
        captured_results["r"] = list(g.results)

    parent_inst = TaskInstance(execute=parent_task, process_id="pgr", name="PGR")
    a_inst = TaskInstance(execute=fail_a, process_id="pa", name="PA")
    b_inst = TaskInstance(execute=fail_b, process_id="pb", name="PB")
    await upsert_process(mongo_db, "test_pgr", parent_inst)
    executor = Executor(mongo_db, "test_pgr", {})
    executor.register_tasks([parent_inst, a_inst, b_inst])
    await executor.launch_process("pgr")

    results = captured_results["r"]
    assert len(results) == 2
    by_pid = {r.process_id: r for r in results}
    assert by_pid["pa"].state == "failed"
    assert isinstance(by_pid["pa"].original_exception, _SampleErr)
    assert by_pid["pa"].original_exception.url == "ua"
    assert by_pid["pa"].name == "PA"
    assert by_pid["pb"].state == "failed"
    assert isinstance(by_pid["pb"].original_exception, _SampleErr)
    assert by_pid["pb"].original_exception.url == "ub"
    assert by_pid["pb"].name == "PB"
```

- [ ] **Step 2: Run the test**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_parallel_group_results_carry_originals -v`
Expected: PASS (Task 5 already populates `name` and `original_exception` on `ChildResult`).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_child_failure_structured.py
git commit -m "test(optio-core): parallel_group results carry original_exception"
```

---

## Task 12: TDD — `ParallelGroup.__aexit__` raises `ExceptionGroup`

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`
- Modify: `packages/optio-core/src/optio_core/context.py:590-598`

- [ ] **Step 1: Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_parallel_group_raises_exception_group_with_per_child_wrappers(mongo_db):
    """On aggregate breach, __aexit__ raises ExceptionGroup[ChildProcessFailed]."""
    caught_eg = {}

    async def fail_a(ctx):
        raise _SampleErr("ua", 1)

    async def fail_b(ctx):
        raise _SampleErr("ub", 2)

    async def parent_task(ctx):
        try:
            async with ctx.parallel_group(survive_failure=False) as g:
                await g.spawn(execute=fail_a, process_id="ea", name="EA")
                await g.spawn(execute=fail_b, process_id="eb", name="EB")
        except* ChildProcessFailed as eg:
            caught_eg["matched"] = list(eg.exceptions)

    parent_inst = TaskInstance(execute=parent_task, process_id="peg", name="PEG")
    a_inst = TaskInstance(execute=fail_a, process_id="ea", name="EA")
    b_inst = TaskInstance(execute=fail_b, process_id="eb", name="EB")
    await upsert_process(mongo_db, "test_peg", parent_inst)
    executor = Executor(mongo_db, "test_peg", {})
    executor.register_tasks([parent_inst, a_inst, b_inst])
    await executor.launch_process("peg")

    matched = caught_eg.get("matched", [])
    assert len(matched) == 2
    by_name = {cpf.name: cpf for cpf in matched}
    assert "EA" in by_name and "EB" in by_name
    assert isinstance(by_name["EA"].original, _SampleErr)
    assert by_name["EA"].original.url == "ua"
    assert isinstance(by_name["EB"].original, _SampleErr)
    assert by_name["EB"].original.url == "ub"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_parallel_group_raises_exception_group_with_per_child_wrappers -v`
Expected: FAIL — the parent's `except*` doesn't match `RuntimeError("Parallel group failed: ...")`, so the exception escapes, the parent ends `"failed"`, and `caught_eg["matched"]` is empty.

- [ ] **Step 3: Rewrite `ParallelGroup.__aexit__`**

Edit `packages/optio-core/src/optio_core/context.py:590-598`. Replace:

```python
async def __aexit__(self, exc_type, exc_val, exc_tb):
    if self._tasks:
        await asyncio.gather(*self._tasks, return_exceptions=True)
    if self._failed:
        failed = [r for r in self._results if r.state != "done"]
        raise RuntimeError(
            f"Parallel group failed: {len(failed)} children did not complete successfully"
        )
    return False
```

with:

```python
async def __aexit__(self, exc_type, exc_val, exc_tb):
    if self._tasks:
        await asyncio.gather(*self._tasks, return_exceptions=True)
    if self._failed:
        failed = [r for r in self._results if r.state != "done"]
        failures = [
            ChildProcessFailed(
                r.name,
                r.process_id,
                r.original_exception
                if r.original_exception is not None
                else RuntimeError(f"child {r.state}"),
            )
            for r in failed
        ]
        raise ExceptionGroup("Parallel group failed", failures)
    return False
```

Add to imports at the top of `context.py`:

```python
from optio_core.exceptions import ChildProcessFailed
```

(`ExceptionGroup` is a builtin in Python 3.11+, no import needed.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_parallel_group_raises_exception_group_with_per_child_wrappers -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to verify no regressions**

Run: `cd packages/optio-core && pytest`
Expected: All tests pass. If a test that previously caught `RuntimeError("Parallel group failed")` now sees `ExceptionGroup`, migrate it with the smallest possible change (catch `ExceptionGroup` or use `except*`); the existing tests in `test_cancel_propagation.py` only check Mongo terminal states, not the raised exception type, so they should keep passing — but verify.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-core/src/optio_core/context.py packages/optio-core/tests/test_child_failure_structured.py
git commit -m "feat(optio-core): parallel_group raises ExceptionGroup[ChildProcessFailed]"
```

---

## Task 13: TDD — mixed cancel+fail synthesizes `RuntimeError("child cancelled")`

**Files:**
- Modify: `packages/optio-core/tests/test_child_failure_structured.py`

- [ ] **Step 1: Write the test**

Append:

```python
@pytest.mark.asyncio
async def test_parallel_group_mixed_cancel_and_fail_synthesizes_for_cancelled(mongo_db):
    """One child fails with a real exception, one is cancelled mid-flight.
    ExceptionGroup contains two ChildProcessFailed wrappers. The cancelled
    child's wrapper has a synthetic RuntimeError('child cancelled') as
    .original (no real exception was raised for cancellation)."""
    caught_eg = {}

    async def fail_a(ctx):
        # Take a beat so the parent's auto-cancel of sibling has time
        # to register before this raises.
        await asyncio.sleep(0.05)
        raise _SampleErr("ua", 1)

    async def slow_child(ctx):
        while ctx.should_continue():
            await asyncio.sleep(0.01)

    async def parent_task(ctx):
        try:
            async with ctx.parallel_group(
                survive_failure=False, survive_cancel=False,
            ) as g:
                await g.spawn(execute=fail_a, process_id="mfa", name="MFA")
                await g.spawn(execute=slow_child, process_id="mfb", name="MFB")
        except* ChildProcessFailed as eg:
            caught_eg["matched"] = list(eg.exceptions)

    parent_inst = TaskInstance(execute=parent_task, process_id="pmfx", name="PMFX")
    a_inst = TaskInstance(execute=fail_a, process_id="mfa", name="MFA")
    b_inst = TaskInstance(execute=slow_child, process_id="mfb", name="MFB")
    await upsert_process(mongo_db, "test_pmfx", parent_inst)
    executor = Executor(mongo_db, "test_pmfx", {})
    executor.register_tasks([parent_inst, a_inst, b_inst])
    await executor.launch_process("pmfx")

    matched = caught_eg.get("matched", [])
    # MFA failed; MFB was cancelled by the alpha-cascade.
    by_name = {cpf.name: cpf for cpf in matched}
    assert "MFA" in by_name
    assert isinstance(by_name["MFA"].original, _SampleErr)
    if "MFB" in by_name:
        # MFB may or may not have entered the results — alpha-cascade timing
        # can sometimes refuse the spawn outright. If present, it must have a
        # synthetic RuntimeError as .original.
        assert isinstance(by_name["MFB"].original, RuntimeError)
        assert "cancelled" in str(by_name["MFB"].original).lower()
```

The "MFB may or may not have entered the results" wording tolerates the existing alpha-cascade race; the substantive assertion is that **if** the cancelled child is in the group, its `.original` is a non-None synthetic `RuntimeError`. If you can deterministically force MFB into the cancelled-result state (e.g. by adjusting spawn timing or by deliberately not relying on alpha and instead cancelling MFB through the explicit `ctx.cancel` path), tighten the test to assert MFB is always present.

- [ ] **Step 2: Run the test**

Run: `cd packages/optio-core && pytest tests/test_child_failure_structured.py::test_parallel_group_mixed_cancel_and_fail_synthesizes_for_cancelled -v`
Expected: PASS (Task 12's synthesis branch covers cancelled→RuntimeError).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_child_failure_structured.py
git commit -m "test(optio-core): parallel_group synthesizes RuntimeError for cancelled child"
```

---

## Task 14: Update `optio-core` `AGENTS.md`

Document the new public types and revised contract so downstream agents know how to use them.

**Files:**
- Modify: `packages/optio-core/AGENTS.md`

- [ ] **Step 1: Read the existing `AGENTS.md`**

Run: `cat packages/optio-core/AGENTS.md` (or read via the file tool). Look for the section that documents `run_child`, `parallel_group`, and `ChildResult`. If no such section exists, create one near the existing public-API documentation.

- [ ] **Step 2: Add new-type documentation**

Add or update the following points in the appropriate section(s):

- `ChildProcessFailed` (new): raised by `ProcessContext.run_child` when a child fails and `survive_failure=False`. Carries `name: str`, `process_id: str`, `original: BaseException`. `__cause__` is set to `original`. Import: `from optio_core.exceptions import ChildProcessFailed`.
- `ChildOutcome` (new): return type of `ProcessContext.run_child`. Fields: `state: str` (`"done" | "failed" | "cancelled"`) and `original_exception: BaseException | None`. Import: `from optio_core.models import ChildOutcome`. **Breaking:** `run_child` previously returned a bare state `str`; existing callers that captured the return value must now access `.state`.
- `ChildResult` (modified): now has additional fields `name: str` and `original_exception: BaseException | None`, populated by `ParallelGroup.spawn`. Existing fields unchanged.
- `ParallelGroup.__aexit__` raise type (changed): was `RuntimeError("Parallel group failed: ...")`, now `ExceptionGroup("Parallel group failed", [ChildProcessFailed, ...])`. Callers use `except* ChildProcessFailed` (Python 3.11+ syntax).

Include a short usage example for each:

```python
# Catching a single child failure
try:
    await ctx.run_child(...)
except ChildProcessFailed as e:
    if isinstance(e.original, DownloadFailed):
        ...

# Survive path
outcome = await ctx.run_child(..., survive_failure=True)
if outcome.state == "failed":
    handle(outcome.original_exception)

# Parallel group
try:
    async with ctx.parallel_group(survive_failure=False) as g:
        await g.spawn(...)
        await g.spawn(...)
except* ChildProcessFailed as eg:
    for cpf in eg.exceptions:
        ...
```

- [ ] **Step 3: Check the root `AGENTS.md`**

Run: `grep -n "run_child\|parallel_group\|ChildResult\|ChildOutcome\|ChildProcessFailed" AGENTS.md`. If the root `AGENTS.md` has a unified reference for these symbols (per the project's coordination convention), update it identically.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/AGENTS.md AGENTS.md
git commit -m "docs(optio-core): document ChildProcessFailed, ChildOutcome, ChildResult extension"
```

---

## Task 15: Final regression sweep

Catch anything missed: stale comments, leftover test assertions tied to the old `RuntimeError`, anywhere else in the repo that captured `run_child`'s string return value or caught the old parallel-group `RuntimeError`.

**Files:** Depend on what the sweep finds.

- [ ] **Step 1: Search the whole repo for stale references**

Run from repo root:

```bash
grep -rn "Child process.*failed" packages/ --include="*.py"
grep -rn "Parallel group failed" packages/ --include="*.py"
grep -rn "RuntimeError.*Child process\|RuntimeError.*Parallel group" packages/ --include="*.py"
```

For each hit:
- Code site referencing the old strings → consider whether the comment/docstring is now stale. If so, update it.
- Test site catching the old `RuntimeError` → migrate to `ChildProcessFailed` or `ExceptionGroup` per the design.

In particular: `packages/optio-core/tests/test_cancel_propagation.py:358-361` has a comment "raises RuntimeError when any child cancels; the exception overwrites the prior cancel_requested state." Update the wording to reference `ExceptionGroup` instead of `RuntimeError`.

- [ ] **Step 2: Search for callers that captured the bare string return**

Run:

```bash
grep -rn "= await .*run_child\|state = await .*run_child\|result = await .*run_child" packages/ --include="*.py"
```

For each hit, verify the call site treats the return as `ChildOutcome` (accesses `.state` or `.original_exception`). Migrate any remaining sites.

- [ ] **Step 3: Run the full test suite**

Run: `cd packages/optio-core && pytest`
Expected: All tests pass.

Then run the top-level test command (TS + Python):

```bash
cd /home/csillag/deai/optio && make test
```

Expected: All tests pass.

- [ ] **Step 4: Commit any sweep fixes (if any)**

```bash
git add <files-touched-during-sweep>
git commit -m "chore(optio-core): post-migration regression sweep"
```

If nothing needed fixing, skip this step.

- [ ] **Step 5: Verify branch state**

Run:

```bash
git log --oneline 'origin/main..HEAD' -- packages/optio-core docs/2026-05-12-child-failure-propagation-*
```

Expected: a clean linear sequence of commits, one per task above, plus the spec commit `3091b98` already on the branch.

---

## Notes for the implementer

- **TDD discipline:** Each TDD task pairs one RED test with a minimal GREEN change. Do not bundle changes. If a GREEN step accidentally fixes a later task's RED test before you've written it, that is fine — just write the later test next and verify it passes.
- **No new behavior in plumbing tasks (4, 5):** if existing tests start failing in those tasks, you've over-stepped. Rollback and split the change.
- **Cancellation race in Task 13:** the alpha-cascade timing in `parallel_group` may sometimes refuse the slow child's spawn outright (no `ChildResult` entry) or let it enter the cancelled state. Task 13 tolerates both — the substantive assertion is on the failed child plus the synthesis behavior **if** the cancelled child is present.
- **`mongo_db` fixture:** all integration tests in this plan rely on it; it is defined in `packages/optio-core/tests/conftest.py`. If it requires Docker-backed Mongo, follow the existing project conventions (per repo memory, MongoDB runs only via Docker or `mongodb-memory-server`; no local `mongod`).
- **Do not add `Co-Authored-By:` lines** to any commit (per repo convention in root `AGENTS.md` and user preference).
