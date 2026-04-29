# DELIVERABLE Path Relativization

**Base revision:** `4c3e37dadb1d82ad352d6843ad31f57a23c2c438` on branch `main` (as of 2026-04-29T08:07:46Z)

## Summary

In `optio-opencode`, the LLM emits `DELIVERABLE: <path>` lines into `optio.log`. The framework currently passes the resolved absolute path to the `on_deliverable` callback and into progress logs. Absolute paths are noisy and leak workdir layout into consumer code and human-facing logs. This change relativizes such paths to `<workdir>/deliverables/`, so callbacks and logs see e.g. `summary.md` instead of `/tmp/optio-opencode-abc123/deliverables/summary.md`.

The contract that all deliverables live under `./deliverables/` is already documented in the prompt; this change enforces it.

## Scope

In scope:

- Progress messages emitted by `_tail_and_dispatch` and `_deliverable_fetch_loop` in `src/optio_opencode/session.py`.
- The `path` argument passed to `on_deliverable` (`DeliverableCallback`).
- Documentation in `packages/optio-opencode/AGENTS.md` describing the callback signature.
- The `DeliverableCallback` docstring in `src/optio_opencode/types.py`.

Out of scope:

- The `deliverablesEmitted` Mongo audit field. It is currently always `[]` and has no readers; no change here.
- Internal absolute-path arguments to `host.fetch_deliverable_text`, which still need the absolute path on the remote filesystem.
- The prompt itself in `src/optio_opencode/prompt.py`. It already states the `./deliverables/` contract.
- Resume-related uses of `host.fetch_deliverable_text` in `_rotate_optio_log` (it is being used as a generic file reader for `optio.log`/`optio.log.old`, not deliverables).

## Behavior

After `validate_deliverable_path` resolves a `DELIVERABLE: <path>` line to an absolute path inside `workdir`:

| Case | Action |
|---|---|
| Resolves under `<workdir>/deliverables/` | Strip up to & including `<workdir>/deliverables/`. Display in progress log; pass to callback. |
| Resolves inside workdir but NOT under `deliverables/` | Skip. Emit info-level progress message: `deliverable <raw>: not under deliverables/, skipping (malfunction)`. No fetch, no callback. |
| Escapes workdir (existing) | Skip. Emit existing info-level progress message: `invalid deliverable path <raw>, skipping`. |

## Design

### New helper in `logparse.py`

```python
DELIVERABLES_SUBDIR = "deliverables"


def relativize_deliverable_path(absolute_path: str, workdir: str) -> str:
    """Return ``absolute_path`` made relative to ``<workdir>/deliverables/``.

    Precondition: ``absolute_path`` has already been validated to be
    inside ``workdir`` (via :func:`validate_deliverable_path`). Both
    arguments are real (already-resolved) absolute paths.

    Raises ``ValueError`` if ``absolute_path`` is not strictly under
    ``<workdir>/deliverables/`` (including the case where it equals
    the deliverables root itself).
    """
```

Implementation outline:

1. `deliverables_root = os.path.realpath(os.path.join(workdir, DELIVERABLES_SUBDIR))`.
2. `rel = os.path.relpath(absolute_path, deliverables_root)`.
3. If `rel == "."`, `rel == ".."`, or `rel.startswith(".." + os.sep)` → raise `ValueError`.
4. Otherwise return `rel`.

`validate_deliverable_path` is unchanged; it remains the security boundary.

### Call site changes in `session.py`

`_tail_and_dispatch`:

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
            f"deliverable {ev.path!r}: not under deliverables/, skipping (malfunction)",
        )
        continue
    ctx.report_progress(None, f"Deliverable: {display}")
    item = (absolute, display)
    try:
        deliverable_queue.put_nowait(item)
    except asyncio.QueueFull:
        await deliverable_queue.put(item)
```

The pre-validation `Deliverable: <raw>` log line at the top of the old branch is removed; the post-validation log line is the single source of truth.

The queue's element type changes:

- Before: `asyncio.Queue[str]` carrying the absolute path.
- After: `asyncio.Queue[tuple[str, str]]` carrying `(absolute, display)`.

`_deliverable_fetch_loop`:

```python
absolute, display = await queue.get()
try:
    text = await host.fetch_deliverable_text(absolute)   # absolute, unchanged
except UnicodeDecodeError:
    ctx.report_progress(
        None, f"Deliverable {display}: not valid UTF-8, skipping callback"
    )
    continue
except FileNotFoundError:
    ctx.report_progress(None, f"Deliverable {display}: not found")
    continue
except Exception as exc:  # noqa: BLE001
    ctx.report_progress(
        None, f"Deliverable {display}: fetch failed: {exc!r}, skipping"
    )
    continue

if callback is None:
    continue
await callback(hook_ctx, display, text)
```

The local variable `path` in this loop is replaced by the `(absolute, display)` pair; logs use `display`, the fetch call uses `absolute`, the callback receives `display`.

### Documentation updates

`packages/optio-opencode/AGENTS.md`, around line 40:

- Replace `(hook_ctx, remote_path, decoded_text)` with `(hook_ctx, deliverable_path, decoded_text)`, and clarify that `deliverable_path` is relative to `<workdir>/deliverables/` (e.g. `summary.md` or `sub/summary.md`).
- Note that the auto-emitted `Deliverable: <path>` progress message uses the same relative path.

`packages/optio-opencode/src/optio_opencode/types.py`:

- Update the `DeliverableCallback` docstring's "Arguments: (hook_ctx, remote_path, decoded_text)" line to say `deliverable_path` instead of `remote_path` and note it is relative to `<workdir>/deliverables/`.

The prompt in `src/optio_opencode/prompt.py` and the contract description in `AGENTS.md` § "Log-file contract" are unchanged. The `DELIVERABLE: <path>` line on the wire is still arbitrary (absolute or workdir-relative); only the callback/log surface is normalized.

## Testing

Unit tests in `tests/test_logparse.py` for `relativize_deliverable_path`:

| Case | Resolved absolute input | Expected |
|---|---|---|
| Direct child of `deliverables/` | `<wd>/deliverables/foo.md` | `"foo.md"` |
| Nested under `deliverables/` | `<wd>/deliverables/sub/foo.md` | `"sub/foo.md"` |
| Inside workdir, not under `deliverables/` | `<wd>/foo.md` | `ValueError` |
| Sibling dir starting with "deliverables" | `<wd>/deliverables_other/foo.md` | `ValueError` |
| Equal to deliverables root | `<wd>/deliverables` | `ValueError` |
| Outside workdir entirely | `/etc/passwd` | `ValueError` |

Each test uses a real `tmp_path` so `os.path.realpath` resolves cleanly (no symlink games required for these cases).

Integration coverage in `tests/test_session.py` (or wherever the existing `_tail_and_dispatch` deliverable cases live):

- A `DELIVERABLE: <abs path under deliverables/>` line causes the callback to be invoked with the relative form (e.g. `summary.md`), and the progress log shows the relative form.
- A `DELIVERABLE: <abs path inside workdir but outside deliverables/>` line skips the callback and emits a "not under deliverables/, skipping (malfunction)" progress message.
- The pre-existing escape-the-workdir case continues to emit "invalid deliverable path …, skipping" and skips the callback.

## Compatibility

This is a breaking change for any consumer whose `on_deliverable` callback assumes its `path` argument is absolute. The existing callbacks in this repo are limited; auditing them is part of the implementation plan.

The `DeliverableCallback` type signature is unchanged (still `(HookContext, str, str)`); only the semantic meaning of the second argument changes.

## Risks and mitigations

- **Symlinks pointing inside `deliverables/` from elsewhere in workdir.** `validate_deliverable_path` already calls `os.path.realpath`, so the resolved path is canonical before relativization. `relativize_deliverable_path` also resolves the deliverables root via `realpath`, so both sides are real paths.
- **`workdir` lacking a literal `deliverables/` subdirectory.** `os.path.realpath` of a non-existent path simply returns the lexically normalized path, so the helper still works (it just rejects everything as not-under-deliverables, which is correct).
- **Windows path separators.** Out of scope; the project already assumes POSIX (workdir paths in `host.py` are constructed with `/`).

## Out-of-scope follow-ups

- Wiring `deliverablesEmitted` to actually capture the run's emitted (relative) paths for the Mongo snapshot. The schema is reserved for this; populating it is a separate change.
