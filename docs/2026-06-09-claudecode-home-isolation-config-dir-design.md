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

Root cause (per Claude Code docs, confirmed via the claude-code guide): Claude
Code resolves its config directory as **`CLAUDE_CONFIG_DIR` if set, otherwise
`~/.claude`** — and the `~` for the global-memory read resolves to the OS user's
home (passwd / `getpwuid`), **not** `$HOME`. We set `HOME` but never set
`CLAUDE_CONFIG_DIR` (zero occurrences in the codebase), so the global `CLAUDE.md`
(and potentially other user-scope config) is read from the host user's real home.

This is an isolation-boundary defect: host operator config influencing
sandboxed task execution.

## Goal

Make the per-task claude session read **none** of the host user's global Claude
config — its user-level `CLAUDE.md`, `settings.json`, and other config-dir state
must come only from the per-task planted `<workdir>/home/.claude`. Close the
observed `CLAUDE.md` leak, and guard the one existing mechanism that could
regress (resume/archive, which depends on where claude stores its state).

## Approach

Set **`CLAUDE_CONFIG_DIR=<workdir>/home/.claude`** in the claude launch
environment. Per the docs, `CLAUDE_CONFIG_DIR` is the single authoritative
override for the entire config directory — global `CLAUDE.md`, `settings.json`,
credentials, projects/transcripts, user-scope skills and MCP servers — and
"bypasses everything under `~/.claude`". Pointing it at the already-planted
per-task dir forces all config-dir resolution into the isolated location,
independent of how `~` resolves. `HOME` stays as-is (still needed for the claude
binary at `<home>/.local/bin/claude` and the version cache under `$HOME/.cache`).

The value equals the existing planted/archived path (`<workdir>/home/.claude`),
so plant (`plant_home_files`), resume-restore (`_extract_home_claude`), archive
(`_archive_home_claude` → `tar home/.claude`), and `.claude.json` rekey all stay
consistent — **provided** claude continues to store `.claude.json`/`projects`
where those mechanisms expect them once the var is set. That is the one
interaction to verify (see "Resume interaction").

### Why not the alternatives

- **`HOME` only (status quo):** the defect — claude's global config read ignores
  `$HOME`.
- **Symlink/bind the real `~/.claude` to the planted dir:** fragile, host-mutating,
  and doesn't generalize to remote/SSH hosts.
- **A "no global config" flag:** none exists. Per the docs there is no CLI flag to
  disable the global `CLAUDE.md`; `CLAUDE_CONFIG_DIR` pointed at a clean dir is the
  documented mechanism.

## Components

### Fix — `build_claude_shell_command` (host_actions.py:~421)

Add one env assignment to the launch env:

```
CLAUDE_CONFIG_DIR=<workdir>/home/.claude
```

placed alongside the existing `HOME` / `PATH` assignments. This is the only
production code change. The install step (host_actions.py:199, `env HOME=…`) and
the resume-detect/rekey helpers do not invoke claude to read memory and need no
change.

### Resume interaction (verify, adjust only if needed)

The resume path depends on claude's on-disk layout:
- `_archive_home_claude` tars `<workdir>/home/.claude` (session.py:544).
- `.claude.json` is rekeyed on resume (session.py:216).
- transcript discovery reads `<workdir>/home/.claude/projects` (session.py:537).

The implementation must **empirically confirm with a real claude binary** (the
worker version cache has 2.1.165/168/169) where claude writes `.claude.json` and
`projects` once `CLAUDE_CONFIG_DIR=<home>/.claude` is set:
- If they remain under `<home>/.claude` (and `.claude.json` under the config dir
  or at `<home>/.claude.json` as today) → archive/rekey/discovery already capture
  them; no change.
- If `CLAUDE_CONFIG_DIR` relocates any of them → adjust the archive scope /
  rekey path / discovery path so resume still round-trips.

This empirical confirmation is a required implementation step, not an assumption.

## Out of scope / accepted

- **Managed-policy `CLAUDE.md`** (`/etc/claude-code/CLAUDE.md` on Linux) is loaded
  by claude regardless of `CLAUDE_CONFIG_DIR` and cannot be bypassed. None exists
  on the current host. In a locked-down engine image such a file would be a
  deliberate org policy, not a leak — explicitly out of scope.
- macOS Keychain credentials (system-wide) — irrelevant to the Linux engine.
- Broader per-vector audit of settings/skills/MCP leakage (option 2 in
  brainstorming) — deferred; `CLAUDE_CONFIG_DIR` covers them by construction, and
  the regression test below exercises the representative `CLAUDE.md` vector.

## Testing

Two layers (per the chosen scope: cheap always-on guard + authoritative proof):

1. **Unit (always-on).** Assert that `build_claude_shell_command(...)` emits
   `CLAUDE_CONFIG_DIR=<workdir>/home/.claude` in its env assignments. Cheap, runs
   everywhere, guards against the var being dropped. Proves we *set* it — not that
   claude honors it.

2. **Real-claude regression (skip if no real claude binary, like
   `test_tmux_persistence` skips without tmux).** The authoritative proof that the
   leak is closed. Run the real claude under the isolation env
   (`HOME=<workdir>/home`, `CLAUDE_CONFIG_DIR=<workdir>/home/.claude`) with
   `--debug`/`--debug-file` and capture the resolved memory/config paths claude
   logs at startup (auth-free — path resolution happens before any API call).
   Assert:
   - the resolved config/memory paths are under `<workdir>/home/.claude`, and
   - **no** path under the OS user's real home (`/home/<realuser>/.claude`) appears.

   The host's real `~/.claude/CLAUDE.md` (which exists on the dev host) serves as
   the live sentinel: the test proves it is *not* among the loaded paths. Note the
   leak originates from the passwd home (`getpwuid`), which a test cannot redirect;
   so the test asserts against the real OS-home path rather than a synthesized one.

   **Implementation must empirically confirm** that `--debug` surfaces the
   resolved memory/config paths without authentication. If it does not, fall back
   to: (a) a behavioral `--print` test gated on real credentials (a sentinel
   instruction in a host `CLAUDE.md`, assert its marker is absent from output), or
   (b) the unit guard plus a documented manual verification. The plan picks the
   working channel after the empirical check.

## Affected packages

`optio-claudecode` only (host_actions.py + tests; possibly session.py if the
resume-interaction check requires a path adjustment). No other package, no
release-order considerations beyond a patch release of optio-claudecode.
