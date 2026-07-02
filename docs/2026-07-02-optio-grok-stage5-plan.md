# optio-grok Stage 5 (Binary Cache + HOME/XDG Isolation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Give each task its own agent identity (formalized HOME/XDG isolation) and resolve the grok binary through an optio-owned, evictable cache that never pollutes the operator's `~/.grok` and is never snapshotted.

**Architecture:** Most isolation already exists (Stage 0 sets `HOME`/`GROK_HOME`/`CLAUDE_CONFIG_DIR`). This stage extracts a single `_isolation_env` helper (adds explicit `XDG_*` dirs) and upgrades `ensure_grok_installed` to a cache-backed resolver mirroring claudecode's version-cache-via-symlink shape.

**Tech Stack:** Python, `optio_host` Host primitives.

## Global Constraints

- Branch `csillag/optio-grok`. Reference = `optio-claudecode/src/optio_claudecode/host_actions.py` (`ensure_claude_installed`, the version-cache + per-task symlink, `_resolve_install_dir`/`_isolation_env` shapes) and `optio-opencode`'s `_isolation_env` / `_resolve_install_dir`.
- **Cache location:** resolve against the worker's REAL env (outside the per-task isolation) so it stays shared/evictable: `GROK_CACHE_DIR` env, else `${XDG_CACHE_HOME:-$HOME/.cache}/optio-grok/bin`. Overridable via `config.grok_install_dir`. Never under the task workdir; never the operator's `~/.grok`.
- **Cache population:** ~~grok's headless bootstrap-installer URL is unconfirmed, so Stage 5 seeds the cache from the resolved host `grok` binary… a real vendor-installer download is a future refinement.~~ **SUPERSEDED** — real vendor auto-install (`https://x.ai/cli/install.sh`) + task-path symlink shipped; see design doc §7 "Stage 5 binary cache" bullet. Cache miss → seed from host grok if present, else vendor-install into the persistent cache; return the per-task `<wd>/home/.local/bin/grok` symlink.
- **Isolation env** (`_isolation_env`): `HOME=<wd>/home`, `GROK_HOME=<wd>/home/.grok`, `XDG_CONFIG_HOME=<wd>/home/.config`, `XDG_DATA_HOME=<wd>/home/.local/share`, `XDG_CACHE_HOME=<wd>/home/.cache`, and `CLAUDE_CONFIG_DIR=<wd>/home/.claude` (claude-compat neutralization). `_build_grok_shell_command` must use this helper (single source of truth) so iframe + future conversation launches share identical isolation.
- Cache dir must NOT be captured by snapshots (it lives outside the workdir — already true) and must survive eviction (re-seed on miss).
- Every task: failing test first, minimal impl, commit.

---

### Task 1: `_isolation_env` helper

**Files:** Modify `src/optio_grok/host_actions.py`; Test `tests/test_host_actions.py`

**Interfaces:**
- Produces: `def _isolation_env(workdir: str) -> dict[str, str]` returning the HOME/GROK_HOME/XDG_*/CLAUDE_CONFIG_DIR map.
- `_build_grok_shell_command` refactored to build its env assignments from `_isolation_env(workdir)` (+ PATH + extras), leaving behavior identical for the existing Stage-0 test.

- [ ] **Step 1: Failing test** — `env = _isolation_env("/w/task")`; assert all six keys with the `/w/task/home...` values incl. `XDG_CONFIG_HOME=/w/task/home/.config`, `GROK_HOME=/w/task/home/.grok`, `CLAUDE_CONFIG_DIR=/w/task/home/.claude`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement helper; refactor `_build_grok_shell_command` to consume it. The existing `test_env_isolation_and_done_error` must still pass.
- [ ] **Step 4:** Run full `tests/test_host_actions.py` → PASS.
- [ ] **Step 5: Commit** `refactor(optio-grok): single _isolation_env helper + explicit XDG (Stage 5)`.

---

### Task 2: Cache-backed `ensure_grok_installed`

**Files:** Modify `src/optio_grok/host_actions.py`; Test `tests/test_grok_cache.py`

**Interfaces:**
- `def _resolve_install_dir(install_dir: str | None) -> str` — `install_dir` else `GROK_CACHE_DIR` else `${XDG_CACHE_HOME:-$HOME/.cache}/optio-grok/bin`, resolved against the worker's real env.
- `async def ensure_grok_installed(hook_ctx, *, install_if_missing=True, install_dir=None) -> str` — resolve cache dir; if `<cache>/grok` exists, return it; else if a host `grok` is on PATH, copy it into `<cache>/grok` (chmod +x) and return that; else if `not install_if_missing`, raise; else raise a clear "no grok binary to seed the cache; vendor auto-install is a future refinement" error. Uses only generic Host primitives (`run_command` for `command -v grok`, `put_file_to_host`/`run_command cp`).

- [ ] **Step 1: Failing test** — with a temp cache dir containing a `grok` executable, `ensure_grok_installed(install_dir=<tmp>)` returns `<tmp>/grok` (cache hit, no copy). With an empty cache dir but a fake `grok` on PATH, it populates and returns the cached path. (Use a LocalHost + monkeypatched PATH or a shim dir, as the existing tests do.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt claudecode `ensure_claude_installed` cache-hit/seed logic; replace vendor curl-install with host-binary seed).
- [ ] **Step 4:** Run → PASS; full suite `pytest packages/optio-grok/tests -v` green (Stage-0 session tests still pass — they pass `grok_install_dir=<shim>` which is now the cache dir with the shim `grok`).
- [ ] **Step 5: Commit** `feat(optio-grok): optio-owned evictable grok binary cache (Stage 5)`.

---

## Self-Review
- Spec Stage 5 (evictable optio-owned cache, no host-~ pollution, per-task HOME/XDG identity) ↔ Tasks 1-2. Claude-compat neutralization retained in the shared helper.
- Deviation from claudecode: cache seeded from the host binary (not vendor curl) because grok's bootstrap installer URL is unconfirmed — flagged, with `grok update`/vendor-download as future work. This keeps the cache optio-owned + evictable + unsnapshotted (the required properties) using the binary the host already has.
- No placeholders; tests + reference pointers per task. Names consistent: `_isolation_env`, `_resolve_install_dir`, `ensure_grok_installed`.
