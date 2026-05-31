# Claude Runtime Provisioning via a Shared Version Cache

This spec was written against the following baseline:

**Base revision:** `25b8188b595f53105aeff39e73834513eb8eb00d` on branch `main` (as of 2026-05-31T13:39:02Z)

## Summary

optio-claudecode currently installs the `claude` binary into the **worker's real
home** (`~/.local/bin/claude` + `~/.local/share/claude/versions/`) and only
isolates the *session state* (`HOME=<workdir>/home`) at launch. That pollutes the
host user's environment, never updates (install is skipped whenever a binary is
already present), and defeats claude's native autoupdater (it writes new versions
into the ephemeral per-task home).

This spec replaces that with a **shared, optio-owned version cache** plus a
per-task **symlink** trick: each task's `home/.local/share/claude/versions` is a
symlink to the cache. claude's installer and autoupdater write version binaries
*through* the symlink into the cache, so the cache self-maintains, tasks stay
current, and the host home is never touched. The binary lives in the cache
(outside any workdir) so it is never captured in resume snapshots. The cache is
treated as evictable — repopulated on demand.

A spike (see "Validation") confirmed the load-bearing behavior: `claude install`
into a HOME whose `versions` is a symlink writes the new binary **into the cache**
and leaves the symlink intact.

## Motivation

Three defects in the current model, all rooted in installing to the real home:

1. **Host-home pollution.** The engine writing `~/.local/bin/claude` and
   `~/.local/share/claude/versions/` into the worker user's home is unacceptable —
   the runtime should be a self-contained environment independent of where it runs.
2. **Stale version, never updates.** `ensure_claude_installed` installs only when
   the binary is *absent*; once present it is reused forever at the first-installed
   version. Tasks run an old claude.
3. **Native autoupdate is wasted.** Under `HOME=<workdir>/home`, claude's
   autoupdater writes new version binaries into the per-task home
   (`home/.local/share/claude/versions`), which is wiped on teardown — a ~230 MB
   download that lands nowhere durable. (This is why the snapshot code currently
   does `rm -rf home/.local/share/claude` before archiving.)

Insight from investigation: a claude "version" is **just the executable** (a
single ~240 MB file named by version under `versions/`); the only *state* is
`~/.claude/` + `~/.claude.json`, which is purely `$HOME`-relative. So the binary
and the state can be separated cleanly: keep state per-task via `HOME` (as today),
and relocate only the binary into a shared cache.

## Goals

- The engine **never** writes into the worker's real `~/.local` or `~/.claude`.
- The claude binary lives in an **optio-owned cache**, shared across tasks on a
  worker, never inside a `<workdir>` and never captured by resume snapshots.
- Tasks stay **current**: claude's native autoupdater maintains the cache; a
  cache miss installs `latest`.
- The cache is **evictable**: if gone at launch, it is repopulated by reinstall.
- Per-task **state isolation is unchanged** (`HOME=<workdir>/home`), so seeds and
  snapshots keep working exactly as today.

## Non-goals (v1)

- Version pinning / reproducible-version config. v1 always tracks `latest` (cache
  miss installs latest; autoupdate keeps it current). A `claude_version` pin can
  be added later if a consumer needs it.
- Disabling claude's autoupdater. We **keep it on** — it is now useful (it
  maintains the cache).
- Cache garbage-collection / pruning of old version files. Claude accumulates
  version files in the cache; pruning is a follow-up if disk growth matters.
- Cross-worker shared cache. The cache is per-worker (a worker-local path).
- opencode (separate isolation work, tracked elsewhere).

## Validation (spike performed against base revision)

Setup: a temp `HOME` whose `…/.local/share/claude/versions` is a symlink to an
empty cache dir, then `HOME=<temp> claude install latest`.

Results:
- `versions` **remained a symlink** afterward — the installer did **not** unlink
  or recreate it.
- The new binary landed **in the cache** (`<cache>/2.1.158`, ~240 MB) — the
  symlink was **followed**.
- `home/.local/bin/claude` → `…/versions/2.1.158`, resolving through the symlink
  to the cache binary.

Tar / snapshot safety, both host transports:
- **LocalHost** (`optio_host.archive._build_archive_bytes`, Python `tarfile` +
  `os.walk`): `os.walk` defaults to `followlinks=False` and the archive loop adds
  only files (not directories/symlinks), so a symlinked `versions` is neither
  followed nor stored — the cache is not captured; the symlink is recreated each
  launch by prep.
- **RemoteHost** (shells out to `tar`): default `tar` stores the symlink as a
  symlink (verified), not its target.

These confirm the design's two assumptions: install/autoupdate write *through* the
symlink into the cache, and snapshots never carry the binary.

## Architecture

### Cache

- **Location (resolved on the worker):** `OPTIO_CLAUDECODE_CACHE_DIR` if set,
  otherwise `${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions`, where
  `$HOME`/`$XDG_CACHE_HOME` are the worker's (so RemoteHost uses the remote
  `~/.cache`). This is optio-owned scratch, never claude's `~/.local`/`~/.claude`.
- **Contents:** claude version executables (e.g. `2.1.158`), written by claude's
  own installer/updater through the per-task symlink.
- **Lifetime:** evictable. Absence is handled by reinstall, not an error.

### Per-task prep (replaces the real-home install in `ensure_claude_installed`)

Runs before launch on every start (fresh and resume), against the isolated home:

1. `mkdir -p <workdir>/home/.local/share/claude`, `<workdir>/home/.local/bin`,
   and the resolved cache dir.
2. `ln -sfn <cache> <workdir>/home/.local/share/claude/versions` (idempotent —
   recreates/repairs the symlink each launch).
3. Decide install vs reuse:
   - **No usable version in the cache** (empty, or no executable version file):
     run the vendor installer (`curl -fsSL https://claude.ai/install.sh | bash`)
     with `HOME=<workdir>/home`. It writes through the symlink into the cache and
     creates `home/.local/bin/claude` → newest version.
   - **Cache populated but `home/.local/bin/claude` absent/stale** (fresh task
     reusing an existing cache): point `home/.local/bin/claude` at the newest
     version file in the cache, without reinstalling.
4. Return `claude_path = <workdir>/home/.local/bin/claude` (resolves through
   `versions` → cache).

"Newest version in the cache" = the highest-semver version file name present.

The `_resolve_install_dir` / `_DEFAULT_INSTALL_SUBDIR` / `resolve_host_home`-based
install path is removed. The existing `claude_install_dir` config field is
repurposed as an explicit **cache-dir override** (engine-side absolute path); when
unset, the worker-resolved default above is used. (Naming: keep the field name
`claude_install_dir` to avoid a breaking config change, but document it as "the
claude version cache directory"; or introduce `cache_dir` and deprecate
`claude_install_dir` — the plan picks one and stays consistent.)

### Launch

`HOME=<workdir>/home` (unchanged — all state, `.claude` and `.claude.json`,
per-task isolated; seeds/snapshots unaffected). Exec `home/.local/bin/claude` with
`home/.local/bin` prepended to PATH (as today). The real-home symlink step
currently in `build_ttyd_argv` (introduced to silence the `~/.local/bin` warning
by linking to the real-home binary) collapses: the `bin/claude` link is now owned
by prep and already points into the cache, so `build_ttyd_argv` keeps only the
PATH-prepend (the self-link guard makes the existing `ln -sf` a no-op).

### Autoupdate

Left **on**. Claude's autoupdater writes new version files through the symlink
into the cache and repoints the per-task `home/.local/bin/claude`. Each task's bin
pointer is per-task (in its isolated home), so a running task is never swapped
mid-flight; new tasks pick up the newest cached version. Concurrent tasks fetching
the same new version write the same cache file; claude's installer writes
atomically, so the worst case is a harmless duplicate fetch, not corruption.

### Snapshot / resume

Remove the `rm -rf home/.local/share/claude` pre-snapshot step in
`_capture_snapshot` (it exists only to drop the 230 MB binary that the old model
let accumulate in the isolated home). With `versions` a symlink to the cache, the
binary is never in the workdir, so nothing large is captured. On resume, prep
re-establishes the symlink and reinstalls if the cache was evicted.

## Configuration surface

- `OPTIO_CLAUDECODE_CACHE_DIR` (env, worker-side absolute path) — overrides the
  default cache location.
- `ClaudeCodeTaskConfig.claude_install_dir` (existing) — explicit cache-dir
  override at the config layer; takes precedence over the env/default. Documented
  as the claude version cache directory.
- `install_if_missing` (existing) — unchanged semantics: when False and the cache
  has no usable version, raise instead of installing.

## Testing

**Unit (no real download — reuse the shim infra / point the cache at a fake):**
- Cache-path resolution: env override wins; default is
  `${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions` on the worker.
- Prep logic against a mocked/local host: creates the `versions` symlink; when the
  cache has a version, sets `home/.local/bin/claude` → newest **without**
  reinstalling; when empty, invokes the installer (mocked) with `HOME=<workdir>/home`.
- `build_ttyd_argv`: still PATH-prepends `home/.local/bin`; no real-home reference;
  exec target is `home/.local/bin/claude`.

**Integration (LocalHost + shim claude/ttyd, as existing session tests do):**
- Fresh session with an empty cache → installer runs once, populates the cache,
  session launches.
- Second session (different `process_id`) with the cache populated → **no**
  reinstall; `bin/claude` points into the cache; launches.
- Evicted cache (delete it) → next session reinstalls.
- Snapshot of a session does **not** contain the ~240 MB binary (assert the
  workdir blob has no `versions/<v>` file content — only the symlink or nothing).
- Host home is untouched (assert nothing written under the worker's real
  `~/.local`/`~/.claude` — mirrors the existing "never touch real ~/.claude" test).

## File structure

- `packages/optio-claudecode/src/optio_claudecode/host_actions.py`
  - Replace `_resolve_install_dir` / `_DEFAULT_INSTALL_SUBDIR` with cache-dir
    resolution (`OPTIO_CLAUDECODE_CACHE_DIR` / `claude_install_dir` /
    worker-default) + a `_prepare_claude_runtime` (symlink + install-or-reuse)
    that supersedes the present-check-and-skip body of `ensure_claude_installed`.
  - Simplify `build_ttyd_argv` (drop the real-home claude symlink; keep
    PATH-prepend; exec `home/.local/bin/claude`).
- `packages/optio-claudecode/src/optio_claudecode/session.py`
  - Remove the `rm -rf home/.local/share/claude` step in `_capture_snapshot`.
  - Adjust the launch/prep wiring to the new `_prepare_claude_runtime` return.
- `packages/optio-claudecode/src/optio_claudecode/types.py`
  - Document `claude_install_dir` as the cache-dir override (or introduce
    `cache_dir`; the plan decides).
- `packages/optio-claudecode/tests/` — update `test_host_actions.py`
  (build_ttyd_argv, install/prep), add cache-prep + reuse + eviction +
  snapshot-excludes-binary tests; adjust `conftest`/shim as needed so no real
  download occurs.

## Edge cases

- **Cache evicted mid-life:** dangling `versions` symlink → prep detects no usable
  version → reinstall through the symlink.
- **`install_if_missing=False` + empty cache:** raise (consistent with today).
- **Concurrent installs of the same new version:** atomic write in claude's
  installer → harmless duplicate; no corruption.
- **Cache populated by an older version + a fresh task:** task uses the newest
  cached version; claude autoupdate may add a newer one through the symlink.
- **`bin/claude` self-symlink:** prep owns the bin link; `build_ttyd_argv`'s
  residual `ln -sf` (if kept) is guarded against the same-path case.

## Out of scope (deferred)

- Version pinning (`claude_version`) for reproducible runs.
- Cache GC / pruning of accumulated version files.
- Cross-worker / shared-network cache.
- opencode HOME/XDG isolation (separate effort).
