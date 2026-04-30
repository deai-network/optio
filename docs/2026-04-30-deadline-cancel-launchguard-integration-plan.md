# Deadline-Cancel × Launch-Guard Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `csillag/project-removal` (deadline-cancel work) onto `main` (with launch-guard) via linear rebase, resolve the one mechanical conflict, add two interaction tests.

**Architecture:** Linear rebase of 17 commits onto main. One conflict at commit `6d691b2` in `Executor.__init__`. Two new tests in a new `tests/test_deadline_cancel_launchguard.py` covering child-`LaunchBlocked` propagation under cancel and force-cancel-with-active-block scenarios. No new mechanism — both features remain orthogonal in code paths.

**Tech Stack:** Python 3.11+, `asyncio`, `motor`, `pytest-asyncio`. Spec: `docs/2026-04-30-deadline-cancel-launchguard-integration-design.md`.

**Base revision:** `59782ec7d7d5f5fc96e017973b684d3ac4c56504` on branch `main` (as of 2026-04-30T11:09:15Z).

---

## File Structure

**Modified during rebase:**
- `packages/optio-core/src/optio_core/executor.py` — manual conflict resolution at commit `6d691b2`.
- (Possibly) `packages/optio-core/src/optio_core/lifecycle.py` and `packages/optio-core/src/optio_core/__init__.py` — adjacent edits, expected to merge cleanly. Resolution rules captured in spec section "Conflict resolution rules" if needed.

**New files (post-rebase):**
- `packages/optio-core/tests/test_deadline_cancel_launchguard.py` — two integration tests.

---

## Task 1: Create integration feature branch

**Files:** none. Branch operation only.

- [ ] **Step 1: Verify clean working tree**

```bash
git status
```

Expected: `On branch csillag/project-removal` and "nothing to commit, working tree clean".

- [ ] **Step 2: Create feature branch in-place**

```bash
git checkout -b csillag/deadline-cancel-launchguard-merge
git status
```

Expected: `On branch csillag/deadline-cancel-launchguard-merge` with clean tree. Branch is created from `csillag/project-removal` HEAD (`76c1444` — the integration spec commit).

- [ ] **Step 3: Sanity-check the rebase target**

```bash
git rev-parse main
```

Expected: `59782ec7d7d5f5fc96e017973b684d3ac4c56504` (matches the spec's base revision).

(No commit at the end of this task. Branch creation only.)

---

## Task 2: Verify pre-rebase test suite

**Files:** none. Verification only.

- [ ] **Step 1: Run full optio-core suite on the feature branch (should still match the merged state)**

```bash
cd packages/optio-core && python -m pytest tests/ -q
```

Expected: `163 passed`. (This is the post-deadline-cancel-merge state.)

- [ ] **Step 2: Note any failures**

If any test fails, STOP and report — the rebase should not start from a broken state. Otherwise proceed.

(No commit.)

---

## Task 3: Begin rebase, expect conflict at 6d691b2

**Files:** none yet. Git operation.

- [ ] **Step 1: Start the rebase**

```bash
cd /home/csillag/deai/optio
git rebase main
```

Expected: rebase progresses through several commits, then halts with:

```
Auto-merging packages/optio-core/src/optio_core/executor.py
CONFLICT (content): Merge conflict in packages/optio-core/src/optio_core/executor.py
error: could not apply 6d691b2... feat(optio-core): track running asyncio tasks and per-process cancel entries
```

`git status` shows `executor.py` as `both modified`.

- [ ] **Step 2: Verify the conflict region matches the spec's expected location**

Run:

```bash
grep -nE '^(<<<<<<<|=======|>>>>>>>)' packages/optio-core/src/optio_core/executor.py
```

Expected three lines, located inside `Executor.__init__`, around line 49 of the merge state.

If the conflict markers appear in unexpected locations or in any file other than `executor.py`, STOP. Run `git rebase --abort` and report — the conflict surface has changed since the spec was written.

(No commit.)

---

## Task 4: Resolve the `Executor.__init__` conflict

**Files:**
- Modify: `packages/optio-core/src/optio_core/executor.py` (the conflict region around `Executor.__init__`)

- [ ] **Step 1: Open the file and locate the conflict**

The file currently contains a region like:

```python
class Executor:
    """Executes task functions with lifecycle management."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        prefix: str,
        services: dict[str, Any],
        optio: "Optio | None" = None,
    ):
        self._db = db
        self._prefix = prefix
        self._services = services
<<<<<<< HEAD
        self._optio = optio
        self._cancellation_flags: dict[ObjectId, asyncio.Event] = {}
=======
        self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
        self._running_tasks: dict[ObjectId, asyncio.Task] = {}
>>>>>>> 6d691b2 (feat(optio-core): track running asyncio tasks and per-process cancel entries)
        self._task_registry: dict[str, TaskInstance] = {}
```

(The constructor's signature already shows the 4-arg form — git merged the signature cleanly because that line did not collide. The conflict is the body region.)

- [ ] **Step 2: Apply the resolution**

Replace the entire `<<<<<<< HEAD ... =======  ... >>>>>>> 6d691b2` block with:

```python
        self._optio = optio
        self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
        self._running_tasks: dict[ObjectId, asyncio.Task] = {}
```

Final region after resolution:

```python
class Executor:
    """Executes task functions with lifecycle management."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        prefix: str,
        services: dict[str, Any],
        optio: "Optio | None" = None,
    ):
        self._db = db
        self._prefix = prefix
        self._services = services
        self._optio = optio
        self._cancellation_flags: dict[ObjectId, _CancelEntry] = {}
        self._running_tasks: dict[ObjectId, asyncio.Task] = {}
        self._task_registry: dict[str, TaskInstance] = {}
```

- [ ] **Step 3: Verify no leftover conflict markers**

```bash
grep -nE '^(<<<<<<<|=======|>>>>>>>)' packages/optio-core/src/optio_core/executor.py
```

Expected: no output.

- [ ] **Step 4: Verify `_CancelEntry` is imported / declared**

```bash
grep -n "_CancelEntry" packages/optio-core/src/optio_core/executor.py | head -5
```

Expected: at minimum a `class _CancelEntry` or `@dataclass` declaration plus the type annotation just resolved. The `_CancelEntry` definition was added by the same commit (`6d691b2`); confirm it's also present at the top of the file.

- [ ] **Step 5: Stage the resolved file and continue the rebase**

```bash
git add packages/optio-core/src/optio_core/executor.py
git rebase --continue
```

Expected: rebase resumes. May halt on subsequent commits if those commits also touched the conflict region (they shouldn't — `_CancelEntry` only appears in `6d691b2`). If another conflict surfaces, STOP and report.

If the editor opens for the rebase commit message, save without changes — keep the original commit message intact (`feat(optio-core): track running asyncio tasks and per-process cancel entries`).

- [ ] **Step 6: Verify rebase completed**

```bash
git status
git log --oneline -5
```

Expected: `On branch csillag/deadline-cancel-launchguard-merge` clean; the last 5 commits on the feature branch have rebase-rewritten hashes (new SHAs) but the same commit messages and (originally) `59782ec` should be in the immediate ancestry.

```bash
git log --oneline main..HEAD | wc -l
```

Expected: `17` (16 deadline-cancel commits + the integration spec commit `76c1444` (rebased) — confirm via `git log --oneline main..HEAD`).

(No separate commit — `git rebase --continue` produces the rebased commit using the original message.)

---

## Task 5: Verify post-rebase test suite

**Files:** none.

- [ ] **Step 1: Run the full optio-core test suite on the rebased feature branch**

```bash
cd packages/optio-core && python -m pytest tests/ -q
```

Expected: ALL PASS. The exact count = 163 (deadline-cancel) + N (launch-guard) where N is the number of tests in `tests/test_launch_guard.py` that came over from main. Confirm by counting:

```bash
grep -cE '^async def test_' packages/optio-core/tests/test_launch_guard.py
```

Total expected: `163 + N`. If the count is below this, identify failures and report. If a deadline-cancel test fails, the rebase resolution may have lost code; if a launch-guard test fails, the rebase may have regressed launch-guard behaviour.

- [ ] **Step 2: If failures occur, diagnose**

The most likely failure modes:
- `executor.py` resolution dropped a line (re-read the file against the spec's "Conflict resolution rules" section).
- `lifecycle.py` adjacent edits collided at runtime (e.g., the `Executor(...)` call lost `optio=self`). Run:

  ```bash
  grep -n "Executor(" packages/optio-core/src/optio_core/lifecycle.py
  ```

  Expected: a single call site reading `Executor(mongo_db, prefix, services, optio=self)`. If `optio=self` is missing, fix it inline and stage:

  ```bash
  git add packages/optio-core/src/optio_core/lifecycle.py
  git commit --amend --no-edit
  ```

- `Optio.__init__` lost one of `_supervisor_task` or `_launch_blocks`. Confirm both:

  ```bash
  grep -n "_supervisor_task\|_launch_blocks" packages/optio-core/src/optio_core/lifecycle.py
  ```

  Expected: both attributes present in `__init__`.

If failures persist after these checks, STOP and report. Do not paper over with skips.

- [ ] **Step 3: If everything passes, no commit needed for this task**

The rebase produced its own commits; verification just confirms them.

---

## Task 6: Add `test_child_launchblocked_propagates_and_parent_cancellable`

**Files:**
- Create: `packages/optio-core/tests/test_deadline_cancel_launchguard.py`

- [ ] **Step 1: Create the file with module-level test scaffold**

Create `packages/optio-core/tests/test_deadline_cancel_launchguard.py` with:

```python
"""Integration tests for deadline-cancel × launch-guard.

Spec: docs/2026-04-30-deadline-cancel-launchguard-integration-design.md
"""
import asyncio

import pytest

from optio_core.lifecycle import Optio
from optio_core.models import LaunchBlocked, TaskInstance


pytestmark = pytest.mark.asyncio
```

- [ ] **Step 2: Append the failing test**

Append to the same file:

```python
async def test_child_launchblocked_propagates_and_parent_cancellable(mongo_db):
    """A parent that handles LaunchBlocked from a child remains cancellable.

    Verifies:
    - LaunchBlocked raises out of run_child as a normal exception
    - The blocked child never enters _cancellation_flags / _running_tasks
    - cancel/cancel_and_wait on the parent reaches a clean terminal state
    - _launch_blocks is empty after the test
    """
    prefix = "intg1"

    parent_started = asyncio.Event()
    child_block_observed = asyncio.Event()
    parent_done = asyncio.Event()

    async def child(ctx):  # noqa: ARG001
        return  # never reached when blocked

    async def parent(ctx):
        parent_started.set()
        try:
            await ctx.run_child(
                execute=child,
                process_id="p.child",
                name="Child",
                params={},
            )
        except LaunchBlocked:
            child_block_observed.set()

        # Cooperate with cancel after the block has been observed.
        for _ in range(100):
            if ctx.cancellation_flag.is_set():
                parent_done.set()
                return
            await asyncio.sleep(0.05)
        parent_done.set()

    parent_task = TaskInstance(
        process_id="p.parent", name="Parent", params={},
        execute=parent, metadata={"kind": "parent"},
    )

    async def gen(_s, _f):
        return [parent_task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=2.0,
    )
    run_task = asyncio.create_task(optio.run())
    try:
        # Block any launch whose metadata matches kind=child.
        async with optio.block_launches({"kind": "child"}):
            await optio.launch("p.parent")
            await parent_started.wait()
            # Inside the parent, run_child triggers the block.
            # We can't directly assert the run_child happened, so we
            # wait for child_block_observed via cancel-driven flush.

            # Issue cancel; this also nudges the cooperative loop.
            state = await optio.cancel_and_wait("p.parent")
            assert state == "cancelled"

        # After the block context exits, _launch_blocks must be empty.
        assert optio._launch_blocks == {}

        # Registries should be empty for parent (cooperative cancel) and
        # the would-be child should never have appeared.
        assert optio._executor._cancellation_flags == {}
        assert optio._executor._running_tasks == {}
        # Parent observed the LaunchBlocked exception.
        assert child_block_observed.is_set()
    finally:
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
```

- [ ] **Step 3: Run the test**

```bash
cd packages/optio-core && python -m pytest tests/test_deadline_cancel_launchguard.py::test_child_launchblocked_propagates_and_parent_cancellable -v
```

Expected: PASS.

If FAIL, the most likely root cause is a runtime ordering issue with `child_block_observed`. Diagnose by inspecting parent state at failure point. Do not weaken the assertions.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-core/tests/test_deadline_cancel_launchguard.py
git commit -m "test(optio-core): integration test for child LaunchBlocked under cancel"
```

---

## Task 7: Add `test_force_cancel_with_active_block_does_not_leak`

**Files:**
- Modify: `packages/optio-core/tests/test_deadline_cancel_launchguard.py` (append second test)

- [ ] **Step 1: Append the failing test**

Append to `packages/optio-core/tests/test_deadline_cancel_launchguard.py`:

```python
async def test_force_cancel_with_active_block_does_not_leak(mongo_db):
    """Stubborn task force-cancelled while a block_launches context is active.

    Verifies:
    - The stubborn task ends 'failed' with the canonical error
    - The async with block_launches body exits cleanly (block popped)
    - _launch_blocks is empty after teardown
    """
    prefix = "intg2"

    started = asyncio.Event()
    block_active = asyncio.Event()
    block_holder_done = asyncio.Event()

    async def stubborn(ctx):  # noqa: ARG001
        started.set()
        while True:
            await asyncio.sleep(0.05)

    stub_task = TaskInstance(
        process_id="p.stub", name="Stub", params={}, execute=stubborn,
    )

    async def gen(_s, _f):
        return [stub_task]

    optio = Optio()
    await optio.init(
        mongo_db=mongo_db, prefix=prefix,
        get_task_definitions=gen, cancel_grace_seconds=0.3,
    )
    run_task = asyncio.create_task(optio.run())

    async def hold_block_until_cancelled():
        try:
            async with optio.block_launches({"some": "filter"}):
                block_active.set()
                # Hold indefinitely until cancelled
                while True:
                    await asyncio.sleep(0.1)
        finally:
            block_holder_done.set()

    holder = asyncio.create_task(hold_block_until_cancelled())

    try:
        await block_active.wait()
        assert len(optio._launch_blocks) == 1

        await optio.launch("p.stub")
        await started.wait()

        state = await optio.cancel_and_wait("p.stub")
        assert state == "failed"
        proc = await optio.get_process("p.stub")
        assert "Task did not unwind within cancellation grace period" in proc["status"]["error"]

        # Cancel the block-holder so the async with body exits.
        holder.cancel()
        try:
            await holder
        except asyncio.CancelledError:
            pass
        await block_holder_done.wait()

        # _launch_blocks must be empty.
        assert optio._launch_blocks == {}
    finally:
        if not holder.done():
            holder.cancel()
            try:
                await holder
            except (asyncio.CancelledError, Exception):
                pass
        await optio.shutdown()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
```

- [ ] **Step 2: Run the test**

```bash
cd packages/optio-core && python -m pytest tests/test_deadline_cancel_launchguard.py::test_force_cancel_with_active_block_does_not_leak -v
```

Expected: PASS.

If FAIL on `assert state == "failed"`: the supervisor may not have force-cancelled in time. Increase `cancel_grace_seconds` slightly (e.g. 0.4) and re-run. Do not skip the canonical-error assertion.

If FAIL on `_launch_blocks == {}`: the `async with` exit didn't pop. Inspect `Optio.block_launches` (`packages/optio-core/src/optio_core/lifecycle.py`) — its `finally` clause must call `self._launch_blocks.pop(token, None)`. If it does, there's a leftover token from elsewhere; don't paper over.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-core/tests/test_deadline_cancel_launchguard.py
git commit -m "test(optio-core): integration test for force-cancel with active launch-block"
```

---

## Task 8: Full suite verification

**Files:** none.

- [ ] **Step 1: Run the full optio-core suite**

```bash
cd packages/optio-core && python -m pytest tests/ -q
```

Expected: `163 + N + 2` tests pass (where N = launch-guard test count from main). If anything fails, debug before proceeding.

- [ ] **Step 2: Confirm no orphan references remain**

```bash
grep -rn "_force_finalize_stuck_processes\|Task did not exit within shutdown grace period\|request_cancel\b" packages/optio-core/src packages/optio-core/tests
```

Expected: only `request_cancel_with_deadline` matches (the renamed method). No bare `request_cancel(` and no `_force_finalize_stuck_processes`. (These were removed in the deadline-cancel work; verify they didn't get reintroduced by the rebase.)

- [ ] **Step 3: Confirm `LaunchBlocked` is still exported**

```bash
grep -n "LaunchBlocked\|block_launches" packages/optio-core/src/optio_core/__init__.py
```

Expected: both `LaunchBlocked` and `block_launches` exported. (Confirms launch-guard's public surface survived the rebase.)

- [ ] **Step 4: Confirm `cancel_and_wait` and `block_launches` coexist on Optio**

```bash
grep -n "async def cancel_and_wait\|async def block_launches\|def _check_launch_blocks" packages/optio-core/src/optio_core/lifecycle.py
```

Expected: all three present.

- [ ] **Step 5: No commit. The branch is ready for merge to main.**

---

## Self-Review Checklist

- ✅ **Spec coverage:** Conflict resolution (Task 4), interaction docs (already in spec), interaction tests (Tasks 6 + 7), full-suite verification (Tasks 5 + 8). All spec sections covered.
- ✅ **No placeholders:** Every step has concrete commands or code.
- ✅ **Type consistency:** `_CancelEntry`, `_running_tasks`, `_optio`, `_launch_blocks`, `block_launches`, `cancel_and_wait`, `LaunchBlocked` — all names match across tasks and the spec.
- ✅ **Base revision header present.**
- ✅ **TDD discipline:** Tasks 6 and 7 each start with the test before any source changes (in this case, no source changes are needed at all — the rebase already brought all production code over).
- ✅ **Frequent commits:** Tasks 6 and 7 each end with a focused commit. Tasks 1–5 are git/rebase operations that produce their own commits via `git rebase --continue`.
