# Claude Code home isolation: close the config-dir leak (`CLAUDE_CONFIG_DIR`)

This spec was written against the following baseline:

**Base revision:** `34329320f364d886104644a975084ac099006d3c` on branch `main` (as of 2026-06-09T02:42:04Z)

## Problem

`optio-claudecode` isolates the per-task claude session by launching it with
`HOME=<workdir>/home` and planting per-task credentials/settings under
`<workdir>/home/.claude` (`build_claude_shell_command`, host_actions.py:414–423;
`plant_home_files`, host_actions.py:345). The assumption was that Claude Code,
honoring `$HOME`, would read all of its user-level config from the isolated home.

That assumption is wrong. Observed in a live session: claude quoted rules from
the **real** user-global memory file `/home/<realuser>/.claude/CLAUDE.md` despite
`HOME` pointing into the workdir. The operator's personal global `CLAUDE.md`
leaked into task behavior.

Root cause (per Claude Code docs, confirmed via the claude-code guide and an
empirical probe with a real claude binary): Claude Code resolves its config
directory as **`CLAUDE_CONFIG_DIR` if set, otherwise `~/.claude`** — and the `~`
for the global-memory read resolves to the OS user's home (passwd / `getpwuid`),
**not** `$HOME`. We set `HOME` but never set `CLAUDE_CONFIG_DIR` (zero
occurrences in the codebase), so the global `CLAUDE.md` (and any other user-scope
config) is read from the host user's real home.

This is an isolation-boundary defect: host operator config influencing sandboxed
task execution.

## Goal

Make the per-task claude session read **none** of the host user's global Claude
config — its user-level `CLAUDE.md`, `settings.json`, and other config-dir state
must come only from the per-task planted `<workdir>/home/.claude`. Close the
observed `CLAUDE.md` leak; keep the seed/credential and resume machinery working,
including for seeds captured before this change.

## Approach

Set **`CLAUDE_CONFIG_DIR=<workdir>/home/.claude`** in the claude launch
environment. Per the docs, `CLAUDE_CONFIG_DIR` is the single authoritative
override for the entire config directory, and "bypasses everything under
`~/.claude`". Pointing it at the already-planted per-task dir forces all
config-dir resolution into the isolated location, independent of how `~`
resolves. `HOME` stays (still needed for the claude binary at
`<home>/.local/bin/claude` and the version cache under `$HOME/.cache`).

### Why `<home>/.claude` and not `<home>` (empirically determined)

`CLAUDE_CONFIG_DIR` is the **literal** config dir — claude flattens everything
into it (it does **not** append a `.claude` subdir). Probed with a real binary:

| state | optio currently expects | `=<home>/.claude` | `=<home>` |
|---|---|---|---|
| `.credentials.json` | `<home>/.claude/.credentials.json` | unchanged | ❌ `<home>/.credentials.json` |
| `settings.json` | `<home>/.claude/settings.json` | unchanged | ❌ `<home>/settings.json` |
| `projects/` (transcripts) | `<home>/.claude/projects` | unchanged | ❌ `<home>/projects` |
| `sessions/` | inside `.claude/` | unchanged | ❌ `<home>/sessions` |
| `.claude.json` | `<home>/.claude.json` | ❌ → `<home>/.claude/.claude.json` | unchanged |

`=<home>` would move credentials, settings, projects, and sessions **out** of
`.claude/`, breaking auth (planted creds unread), `cred_watcher` (watches
`.claude/.credentials.json`), the resume archive (`tar home/.claude` would no
longer contain projects/sessions → resume loses transcripts), and transcript
discovery. `=<home>/.claude` is the **minimal-ripple** value: everything optio
cares about stays put; **only `.claude.json` moves** — into the `.claude/` dir
that is already planted and archived.

### The one relocation: `.claude.json`

With `CLAUDE_CONFIG_DIR=<home>/.claude`, claude reads/writes `.claude.json` at
`<home>/.claude/.claude.json` instead of the current `<home>/.claude.json`. This
file holds project-trust state; the consume-time rekey collapses it to a single
trusted entry so autonomous tasks don't die on claude's "trust this folder?"
prompt (which `--permission-mode bypassPermissions` does **not** suppress). Three
sites reference the old path and must follow the file:

- `seed_manifest.py:45` — `_rekey_claude_json_projects` reads `home/.claude.json`.
- `seed_manifest.py:87` — the seed capture/restore manifest lists `.claude.json`.
- `oauth.py:242` — the seed-diff noise filter checks `.claude.json`.

The resume archive (`tar home/.claude`, session.py:550) **now also captures
`.claude.json`** because it sits inside `.claude/`. That is a benign behavioral
improvement (project trust now persists across resume); no archive code change.

### Seed backward-compatibility — consume-time, not migration

Seeds are stored as encrypted tar.gz blobs in GridFS, encrypted with
**caller-provided crypto** (`capture_seed(encrypt=…)` / `consume_seed(decrypt=…)`
— in claudecode, `config.session_blob_encrypt/decrypt`). Optio holds no key, so
an **offline migration over stored blobs is impossible within optio**. It is also
unnecessary: consume already decrypts (via the caller callback), extracts, then
runs `consume_transform` on the **decrypted, restored tree** (seeds.py:322–328).
The relocation is handled there:

- **Manifest `include` lists both** `.claude.json` and `.claude/.claude.json`.
  Capture archives only paths that exist (seeds.py:178), so new seeds capture the
  new path; the extra entry lets a pre-existing seed's root `.claude.json` member
  still be extracted on restore (`_extract_seed` matches members against the
  include list, seeds.py:227).
- **`_rekey_claude_json_projects` normalizes then rekeys:** the effective path is
  `<home>/.claude/.claude.json`; if it is absent but the old `<home>/.claude.json`
  exists (an old seed just restored), move it into `.claude/` first, then apply
  the existing trust-collapse rekey at the new path.

Result: old seeds keep working, new seeds use the new layout — zero migration,
zero re-encryption, no key access optio lacks. `CLAUDE_SEED_MANIFEST_VERSION` need
not change (consume is back-compatible across both layouts).

### Why not the alternatives

- **`HOME` only (status quo):** the defect — claude's global config read ignores
  `$HOME`.
- **`CLAUDE_CONFIG_DIR=<home>`:** larger, auth-breaking ripple (see table).
- **Symlink/bind the real `~/.claude`:** fragile, host-mutating, doesn't
  generalize to remote/SSH hosts.
- **A "no global config" flag:** none exists; `CLAUDE_CONFIG_DIR` at a clean dir
  is the documented mechanism.

## Components

### Fix — `build_claude_shell_command` (host_actions.py:~421)

Add `CLAUDE_CONFIG_DIR=<workdir>/home/.claude` to the launch env, alongside
`HOME`/`PATH`. Only production env change. The install step (host_actions.py:199)
does not read memory and needs no change.

### Seed/oauth path follow (the `.claude.json` move)

- `seed_manifest.py`: add `.claude/.claude.json` to the manifest `include`
  (keep `.claude.json` for back-compat); update `_rekey_claude_json_projects` to
  normalize old→new then rekey the new path.
- `oauth.py:242`: accept both `.claude.json` and `.claude/.claude.json` in the
  noise filter.

## Out of scope / accepted

- **Managed-policy `CLAUDE.md`** (`/etc/claude-code/CLAUDE.md`) loads regardless of
  `CLAUDE_CONFIG_DIR` and cannot be bypassed; none exists on the host. In a
  locked-down engine image it would be deliberate org policy, not a leak.
- macOS Keychain credentials — irrelevant to the Linux engine.
- Broader per-vector audit of settings/skills/MCP leakage — deferred;
  `CLAUDE_CONFIG_DIR` covers them by construction, and the regression test
  exercises the representative `CLAUDE.md`/config vector.

## Testing

1. **Unit (always-on).** Assert `build_claude_shell_command(...)` emits
   `CLAUDE_CONFIG_DIR=<workdir>/home/.claude` in its env assignments. Cheap, runs
   everywhere, guards against the var being dropped. Proves we *set* it.

2. **Real-claude regression (skip if no real claude binary, like
   `test_tmux_persistence` skips without tmux).** The authoritative proof. Run the
   real claude under the isolation env (`HOME=<workdir>/home`,
   `CLAUDE_CONFIG_DIR=<workdir>/home/.claude`) with `--debug-file <path> --print x`
   (exits non-zero on no-login — fine; **config-path resolution is logged to the
   debug file at startup, before any API call** — empirically confirmed). Assert
   the debug file's resolved config/settings paths are under `<workdir>/home/.claude`
   and that **no** path under the OS user's real home (`/home/<realuser>/.claude`)
   appears. The host's real `~/.claude` is the live sentinel.

3. **Seed back-compat (unit, mongodb-memory-server + fake host).** Capture a seed
   from a tree with `.claude/.claude.json` (new layout) and confirm it round-trips
   on consume. Separately, simulate an **old** seed (a tar with `.claude.json` at
   the home root), consume it, and assert `_rekey_claude_json_projects` normalizes
   it to `<home>/.claude/.claude.json` and the trust-collapse still applies. This
   proves old seeds are not invalidated.

## Affected packages

`optio-claudecode` (host_actions.py, seed_manifest.py, oauth.py, tests).
`optio-agents` is **not** changed — the seed engine's include/extract already
supports listing both paths; only the claudecode manifest/transform change. Patch
release of optio-claudecode only.
