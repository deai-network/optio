# DELIVERABLE Path Relativization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `optio-opencode` ingests a `DELIVERABLE: <path>` log line whose path resolves inside `<workdir>/deliverables/`, strip the prefix so the `on_deliverable` callback and progress logs see e.g. `summary.md` instead of `/tmp/optio-opencode-abc/deliverables/summary.md`. Paths inside the workdir but outside `deliverables/` are treated as a malfunction and skipped with an info-level progress message.

**Architecture:** Add one pure helper, `relativize_deliverable_path`, in `logparse.py`. Wire it into `_tail_and_dispatch` and `_deliverable_fetch_loop` in `session.py`. The deliverable queue carries a `(absolute, display)` pair; `host.fetch_deliverable_text` keeps using the absolute path; the callback and progress messages use the display path.

**Tech Stack:** Python 3, pytest, asyncio.

---

## Spec

`docs/2026-04-29-deliverable-relpath-design.md`

## File Structure

| File | Change |
|---|---|
| `packages/optio-opencode/src/optio_opencode/logparse.py` | Add `DELIVERABLES_SUBDIR` constant + `relativize_deliverable_path` helper. |
| `packages/optio-opencode/src/optio_opencode/session.py` | Update `_tail_and_dispatch` and `_deliverable_fetch_loop`. Queue type changes from `asyncio.Queue[str]` to `asyncio.Queue[tuple[str, str]]`. |
| `packages/optio-opencode/src/optio_opencode/types.py` | Update `DeliverableCallback` docstring. |
| `packages/optio-opencode/AGENTS.md` | Update `on_deliverable` description (around line 40). |
| `packages/optio-opencode/tests/test_logparse.py` | Add unit tests for `relativize_deliverable_path`. |
| `packages/optio-opencode/tests/test_session_local.py` | Adjust existing happy-path assertion; add malfunction-skip test. |
| `packages/optio-opencode/tests/fake_opencode.py` | Add `inside_workdir_not_deliverables` scenario. |

`validate_deliverable_path` is unchanged; it remains the security boundary.

---

## Task 1: Add `relativize_deliverable_path` helper (TDD)

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/logparse.py`
- Test: `packages/optio-opencode/tests/test_logparse.py`

- [ ] **Step 1: Write failing tests**

Append to `packages/optio-opencode/tests/test_logparse.py` (after the existing `validate_deliverable_path` tests):

```python
# ---- relativize_deliverable_path ----

from optio_opencode.logparse import relativize_deliverable_path


def test_relativize_direct_child_of_deliverables(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables", "foo.md")
    assert relativize_deliverable_path(abs_path, tmp_workdir) == "foo.md"


def test_relativize_nested_under_deliverables(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables", "sub", "foo.md")
    expected = os.path.join("sub", "foo.md")
    assert relativize_deliverable_path(abs_path, tmp_workdir) == expected


def test_relativize_inside_workdir_but_not_deliverables_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "foo.md")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_sibling_dir_with_deliverables_prefix_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables_other", "foo.md")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_deliverables_root_itself_rejected(tmp_workdir):
    import os
    abs_path = os.path.join(tmp_workdir, "deliverables")
    with pytest.raises(ValueError):
        relativize_deliverable_path(abs_path, tmp_workdir)


def test_relativize_outside_workdir_rejected(tmp_workdir):
    with pytest.raises(ValueError):
        relativize_deliverable_path("/etc/passwd", tmp_workdir)
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `packages/optio-opencode/`:

```
pytest tests/test_logparse.py -v -k relativize
```

Expected: `ImportError` / collection error — `relativize_deliverable_path` does not exist yet.

- [ ] **Step 3: Implement helper**

Edit `packages/optio-opencode/src/optio_opencode/logparse.py`. Append after `validate_deliverable_path`:

```python
DELIVERABLES_SUBDIR = "deliverables"


def relativize_deliverable_path(absolute_path: str, workdir: str) -> str:
    """Return ``absolute_path`` made relative to ``<workdir>/deliverables/``.

    Precondition: ``absolute_path`` has already been validated to be
    inside ``workdir`` (via :func:`validate_deliverable_path`). Both
    arguments may be any absolute paths; this function realpaths them
    internally before relativizing.

    Raises ``ValueError`` if ``absolute_path`` is not strictly under
    ``<workdir>/deliverables/`` (including when it equals the
    deliverables root itself or escapes outside it).
    """
    deliverables_root = os.path.realpath(
        os.path.join(workdir, DELIVERABLES_SUBDIR)
    )
    target = os.path.realpath(absolute_path)
    rel = os.path.relpath(target, deliverables_root)
    if rel == "." or rel == ".." or rel.startswith(".." + os.sep):
        raise ValueError(
            f"deliverable path is not under <workdir>/{DELIVERABLES_SUBDIR}/: "
            f"{absolute_path!r} (workdir={workdir!r})"
        )
    return rel
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```
pytest tests/test_logparse.py -v -k relativize
```

Expected: 6 passed.

Then run the full logparse test file to ensure no regression:

```
pytest tests/test_logparse.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```
git add packages/optio-opencode/src/optio_opencode/logparse.py \
        packages/optio-opencode/tests/test_logparse.py
git commit -m "feat(optio-opencode): add relativize_deliverable_path helper"
```

---

## Task 2: Wire helper into session loops (TDD)

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Modify: `packages/optio-opencode/tests/test_session_local.py:130-144` (existing `test_happy_path`)
- Modify: `packages/optio-opencode/tests/fake_opencode.py:96-136` (add new scenario)
- Test: `packages/optio-opencode/tests/test_session_local.py` (add new malfunction test)

- [ ] **Step 1: Update existing happy-path expectation (RED)**

Edit `packages/optio-opencode/tests/test_session_local.py:141-144`:

```python
    assert len(received) == 1
    p, text = received[0]
    assert p == "out.txt"
    assert text == "hello 42 blue"
```

The previous assertion (`"deliverables/out.txt" in p`) accepted the absolute path; the new assertion requires the relativized form.

- [ ] **Step 2: Add malfunction scenario in fake_opencode**

Edit `packages/optio-opencode/tests/fake_opencode.py`. Add new entry inside `SCENARIOS` (after `escape_path`, line 121):

```python
    "inside_workdir_not_deliverables": [
        ("write", "stray.txt", "hi"),
        ("log", "DELIVERABLE: ./stray.txt"),
        ("sleep", 0.05),
        ("log", "DONE"),
    ],
```

This writes a file at the workdir root (not under `deliverables/`) and emits a `DELIVERABLE` line pointing to it. The framework should treat it as a malfunction and skip.

- [ ] **Step 3: Add malfunction test in test_session_local**

Add to `packages/optio-opencode/tests/test_session_local.py` (after `test_invalid_deliverable_path_is_skipped`, around line 183):

```python
async def test_deliverable_outside_deliverables_dir_is_skipped(
    ctx_and_captures, _supply_scenario,
):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "inside_workdir_not_deliverables"

    received: list = []
    async def on_d(hook_ctx, path, text):
        received.append((path, text))

    await run_opencode_session(
        ctx,
        _config("inside_workdir_not_deliverables", deliverable_cb=on_d),
    )
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("not under deliverables/" in m for m in messages)
```

- [ ] **Step 4: Run tests to verify they fail**

Run from `packages/optio-opencode/`:

```
pytest tests/test_session_local.py::test_happy_path \
       tests/test_session_local.py::test_deliverable_outside_deliverables_dir_is_skipped \
       -v
```

Expected:
- `test_happy_path` fails: `assert p == "out.txt"` — receives the absolute path instead.
- `test_deliverable_outside_deliverables_dir_is_skipped` fails: callback IS invoked (malfunction not yet detected) and the expected progress message is absent.

- [ ] **Step 5: Update `_tail_and_dispatch`**

Edit `packages/optio-opencode/src/optio_opencode/session.py:460-472`. Replace the entire `elif isinstance(ev, DeliverableEvent):` block:

```python
        elif isinstance(ev, DeliverableEvent):
            try:
                absolute = validate_deliverable_path(ev.path, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None, f"invalid deliverable path {ev.path!r}, skipping"
                )
                continue
            try:
                display = relativize_deliverable_path(absolute, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None,
                    f"deliverable {ev.path!r}: not under deliverables/, "
                    f"skipping (malfunction)",
                )
                continue
            ctx.report_progress(None, f"Deliverable: {display}")
            item = (absolute, display)
            try:
                deliverable_queue.put_nowait(item)
            except asyncio.QueueFull:
                await deliverable_queue.put(item)
```

Notes:
- The pre-validation `Deliverable: <ev.path>` log line that used to fire on line 461 is gone; the post-validation `Deliverable: <display>` line is the single source of truth.
- The queue now carries `tuple[str, str]`.

- [ ] **Step 6: Update the import in `session.py`**

Edit `packages/optio-opencode/src/optio_opencode/session.py:34`. Add `relativize_deliverable_path` to the existing import from `optio_opencode.logparse`:

```python
from optio_opencode.logparse import (
    DeliverableEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    relativize_deliverable_path,
    validate_deliverable_path,
)
```

(Adjust if the existing import block uses a different shape; insert `relativize_deliverable_path` alphabetically.)

- [ ] **Step 7: Update `_deliverable_fetch_loop`**

Edit `packages/optio-opencode/src/optio_opencode/session.py:494-525` (the body of `_deliverable_fetch_loop`). Replace the inner loop body:

```python
    while True:
        absolute, display = await queue.get()
        try:
            try:
                text = await host.fetch_deliverable_text(absolute)
            except UnicodeDecodeError:
                ctx.report_progress(
                    None,
                    f"Deliverable {display}: not valid UTF-8, skipping callback",
                )
                continue
            except FileNotFoundError:
                ctx.report_progress(None, f"Deliverable {display}: not found")
                continue
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None,
                    f"Deliverable {display}: fetch failed: {exc!r}, skipping",
                )
                continue

            if callback is None:
                continue
            try:
                await callback(hook_ctx, display, text)
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None,
                    f"Deliverable {display}: on_deliverable callback raised "
                    f"{exc!r}, continuing",
                )
        finally:
            queue.task_done()
```

Notes:
- The local variable rename from `path` to `(absolute, display)` is the only structural change.
- The `try`/`finally` with `queue.task_done()` and the existing callback exception handler must be preserved verbatim from the existing implementation. Read lines 494–525 as you edit; do not remove unrelated logic. (Run `grep -n 'task_done\|callback raised' packages/optio-opencode/src/optio_opencode/session.py` first to locate the existing shape.)

- [ ] **Step 8: Update the queue type annotation**

Edit `packages/optio-opencode/src/optio_opencode/session.py:259-260`:

```python
        deliverable_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
            maxsize=DELIVERABLE_QUEUE_BOUND
        )
```

Also update the queue parameter annotation on `_tail_and_dispatch` (line 451) and `_deliverable_fetch_loop` (line 490) to `asyncio.Queue[tuple[str, str]]`.

- [ ] **Step 9: Run the two target tests to verify they pass**

```
pytest tests/test_session_local.py::test_happy_path \
       tests/test_session_local.py::test_deliverable_outside_deliverables_dir_is_skipped \
       -v
```

Expected: both pass.

- [ ] **Step 10: Run the full session_local + logparse suites for regression**

```
pytest tests/test_session_local.py tests/test_logparse.py -v
```

Expected: all green. In particular `test_invalid_deliverable_path_is_skipped`, `test_non_utf8_deliverable_is_skipped`, and `test_callback_raises_does_not_fail_task` must still pass — they already fix raw-path semantics that survive this change unchanged.

- [ ] **Step 11: Commit**

```
git add packages/optio-opencode/src/optio_opencode/session.py \
        packages/optio-opencode/tests/test_session_local.py \
        packages/optio-opencode/tests/fake_opencode.py
git commit -m "feat(optio-opencode): relativize DELIVERABLE paths to workdir/deliverables/"
```

---

## Task 3: Update consumer-facing documentation

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py:10-17`
- Modify: `packages/optio-opencode/AGENTS.md:40-47`

No tests; documentation-only.

- [ ] **Step 1: Update `DeliverableCallback` docstring**

Edit `packages/optio-opencode/src/optio_opencode/types.py:10-17`. Replace the comment + `DeliverableCallback` definition with:

```python
# DeliverableCallback receives the same HookContext as before/after_execute,
# so callbacks no longer need to close over ctx. Breaking change vs. the
# pre-hooks signature `Callable[[str, str], Awaitable[None]]`.
DeliverableCallback = Callable[["HookContext", str, str], Awaitable[None]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: ``(hook_ctx, deliverable_path, decoded_text)``.

``deliverable_path`` is the path of the deliverable file relative to
``<workdir>/deliverables/`` (e.g. ``"summary.md"`` or
``"sub/summary.md"``). It is the same value that appears in the
auto-emitted ``"Deliverable: <path>"`` progress message.
"""
```

- [ ] **Step 2: Update `AGENTS.md` callback description**

Edit `packages/optio-opencode/AGENTS.md:40-47`. Replace the bullet for `on_deliverable` with:

```markdown
- `on_deliverable: Callable[[HookContext, str, str], Awaitable[None]] | None`
  — invoked once per fetched DELIVERABLE with `(hook_ctx,
  deliverable_path, decoded_text)`. `deliverable_path` is relative
  to `<workdir>/deliverables/` (e.g. `summary.md` or
  `sub/summary.md`). The framework already auto-emits a
  `"Deliverable: <path>"` progress message (using the same relative
  path) before the callback fires, so callbacks only need to add
  behavior beyond that (e.g. parsing the body, fetching a related
  file via `hook_ctx.read_text_from_host`, etc.). **Breaking change**:
  prior to the hooks feature, this callback received `(path, text)`
  only; prior to this change, the path was absolute on the remote
  host.
```

- [ ] **Step 3: Sanity-check the docs render**

Run a quick read-back to make sure the file isn't garbled:

```
grep -n "deliverable_path" packages/optio-opencode/src/optio_opencode/types.py \
                          packages/optio-opencode/AGENTS.md
```

Expected: matches in both files; no stray edits elsewhere.

- [ ] **Step 4: Run full optio-opencode test suite as a final regression gate**

```
pytest packages/optio-opencode/tests -v
```

Expected: all green. (The remote/SSH-dependent tests may be skipped depending on environment; that's fine — the local suite covers this change.)

- [ ] **Step 5: Commit**

```
git add packages/optio-opencode/src/optio_opencode/types.py \
        packages/optio-opencode/AGENTS.md
git commit -m "docs(optio-opencode): document relativized deliverable_path in callback"
```

---

## Self-review checklist

- **Spec coverage:** Every section of `docs/2026-04-29-deliverable-relpath-design.md` is covered:
  - Behavior table → Task 1 (helper) + Task 2 (call sites + scenarios).
  - Code structure (helper signature) → Task 1 step 3.
  - Call site changes (`_tail_and_dispatch`, `_deliverable_fetch_loop`, queue type) → Task 2 steps 5–8.
  - Documentation updates (`AGENTS.md`, `types.py`) → Task 3.
  - Testing matrix (6 unit cases for helper) → Task 1 step 1.
  - Integration coverage (callback gets short path; malfunction skip; existing escape skip) → Task 2 (happy-path update + new malfunction test; existing escape test left alone).
- **Placeholders:** None.
- **Type consistency:** `relativize_deliverable_path` signature, queue element type `tuple[str, str]`, and the variable names `(absolute, display)` are used identically across all tasks.
