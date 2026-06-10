# Session-Restore Config-Dir Delivery — Design

This spec was written against the following baseline:

**Base revision:** `3497251ef99cccefdc7de04f51da0901c8ee189d` on branch `csillag/claudecode-config-dir-isolation` (as of 2026-06-10T12:03:11Z)

## Summary

Main's explicit session-restore feature (`session_restore_from` / `rebase_session_blob` / `on_session_saved`) is a **new source of resume data** that lands a `home/.claude` session blob into the workdir before launch. It was written without awareness of this branch's `CLAUDE_CONFIG_DIR` setup, and it leaves the restored `.claude.json`'s `projects` entries keyed to the session's **original** workdir.

`rebase_session_blob` rekeys the transcript **directory slug** (`home/.claude/projects/<old-slug>` → `<new-slug>`) but does **not** touch `.claude.json`'s `projects` dict. So after restore, `.claude.json` still says the *original* workdir is the (only) trusted project. claude, now running in the *new* workdir under `CLAUDE_CONFIG_DIR`, sees the new workdir as untrusted → the folder-trust prompt ("Is this a project you trust?") fires → which `--permission-mode bypassPermissions` does **not** suppress → the session exits in tmux.

The optio-level snapshot-resume path already handles this: it calls `await _rekey_claude_json_projects(host)` after `_extract_home_claude`, which collapses `.claude.json` `projects` to a single trusted entry keyed at the launch workdir. The `session_restore_from` path does **not**. This spec closes that asymmetry by applying the same call on the `session_restore_from` restore path.

(As a harmless secondary effect, `_rekey_claude_json_projects` also relocates an old-root `home/.claude.json` into `.claude/` if one is present — but on this path it never is, since the restore blob is a `home/.claude` tar that cannot contain a root-level file. The operative effect here is the projects-rekey-to-new-workdir + trust.)

## Background

- `session_restore_from` is **conversation-mode, fresh-run only** (validated in `types.py`). On a fresh run with it set, `_prepare` fetches the GridFS blob, decrypts, calls `rebase_session_blob` (rekeys the `home/.claude/projects/<slug>` dir to the new workdir's slug + optional transcript truncation), then `_extract_home_claude`, then `pass_continue = _has_transcript(host)`.
- claude is resumed with **`--continue`** (picks the most-recent transcript in the cwd's project dir), not `--resume <id>`.
- `_rekey_claude_json_projects` (in `seed_manifest.py`) reads `home/.claude/.claude.json` first and only falls back to + moves `home/.claude.json` (root) if present — a no-op for new-layout blobs, a fix for old-layout ones.

## Change

In `session.py`, in the `elif config.session_restore_from is not None:` block of `_prepare`, after `await _extract_home_claude(host, plain)` and before `pass_continue = await _has_transcript(host)`, add:

```python
            await _rekey_claude_json_projects(host)
```

This mirrors the optio-resume branch directly above it. No other production change.

## Why correct and safe

- The restored `.claude.json` (at `home/.claude/.claude.json`) carries the *original* session's `projects` keys. `_rekey_claude_json_projects` reuses the chosen entry's value (preserving trust flags / allowedTools / MCP enablement), forces `hasTrustDialogAccepted: true`, and rekeys it to `{<new workdir>: ...}` — so claude trusts the new workdir and skips the folder-trust prompt.
- It is a no-op when `.claude.json` is missing or malformed (left as-is), so it never breaks a blob that lacks one.
- Same well-tested function the optio-resume and seed paths use; symmetric, minimal.

## Out of scope

- **Capture side** (`on_session_saved` → `_archive_home_claude` tars `home/.claude`): already config-dir-correct on this branch; no change.
- **Projects-slug rekey** (`rebase_session_blob`): verified consistent — both launch paths run claude with `cwd=host.workdir`, `rebase_session_blob` uses `new_workdir=host.workdir`, and headless claude's `/`→`-` and `.`→`-` slug rule matches `slugify_workdir` (confirmed live against a `.`-containing cwd). No change.
- **Real-claude `--continue` resume behavior**: with `--continue` (a lower bar than `--resume <id>`) and the transcript landing at the right slug, claude should resume the restored conversation. This is a **live-testing verification item**, not a presupposed code change.

## Testing

A focused integration test driving the `session_restore_from` path, asserting the trust-rekey (using the existing conversation/restore harness in `test_session_restore.py`):

- Build a restore blob whose `home/.claude/.claude.json` has `projects` keyed to a **foreign/original** workdir (e.g. `{"/old/cwd": {"hasTrustDialogAccepted": true}}`), alongside a transcript under `home/.claude/projects/<slug>/`.
- Restore it via `session_restore_from` into a new workdir (fake_claude harness), capturing the planted tree in a `before_execute` hook (which runs after `_prepare`, where the rekey happens).
- Assert after restore: `home/.claude/.claude.json` `projects` has exactly one key — the **new** workdir — with `hasTrustDialogAccepted: true`; the foreign key is gone.

This mirrors the existing `test_resume_relocates_old_root_claude_json` (optio-resume path) but asserts the projects-rekey effect that matters on this path. Real-claude `--continue` resume remains a live-testing item.
