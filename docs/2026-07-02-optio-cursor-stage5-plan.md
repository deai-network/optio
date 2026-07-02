# optio-cursor Stage 5 (Binary Cache + HOME/XDG Isolation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Give each task its own agent identity (formalized HOME/XDG isolation) and resolve the cursor-agent binary through an optio-owned, evictable cache that never pollutes the operator's `~/.local/share/cursor-agent` and is never snapshotted.

**Architecture:** Most isolation already exists (Stage 0 sets `HOME` + `XDG_*` + `NO_OPEN_BROWSER`). This stage extracts a single `_isolation_env` helper and upgrades `ensure_cursor_installed` to a cache-backed resolver. Unlike grok (unconfirmed installer), cursor HAS a confirmed vendor installer (`curl https://cursor.com/install -fsS | bash`, installs under `$HOME/.local/{bin,share/cursor-agent}`) — so the cache can be populated vendor-first with host-copy as fallback.

**Tech Stack:** Python, `optio_host` Host primitives.

## Global Constraints

- Branch `csillag/cursor`. Reference = `optio-grok/src/optio_grok/host_actions.py` (`_isolation_env`, `_resolve_install_dir`, cache-backed `ensure_grok_installed`) and `optio-claudecode`'s vendor-installer variant of `ensure_claude_installed`.
- **The binary is not a single file:** `cursor-agent` is a symlink into a Node dist dir (`.../cursor-agent/versions/<v>/`). The cache must hold the whole version dir; the cached entrypoint is `<cache>/versions/<v>/cursor-agent` (or a `<cache>/cursor-agent` symlink to it). Copying only the symlink target's file is NOT sufficient.
- **Cache location:** resolve against the worker's REAL env (outside per-task isolation): `CURSOR_CACHE_DIR` env, else `${XDG_CACHE_HOME:-$HOME/.cache}/optio-cursor`. Overridable via `config.cursor_install_dir`. Never under the task workdir; never the operator's `~/.local/share/cursor-agent`.
- **Cache population order:** (1) cache hit → return; (2) vendor installer run with `HOME=<cache>/staging` (so it installs into the staging tree), then move `staging/.local/share/cursor-agent` → cache and fix the entry symlink; (3) fallback: copy the host install's resolved version dir into the cache; (4) else raise (or per `install_if_missing=False`, raise immediately when no cache hit). Keep it network-optional in tests (tests exercise 1 and 3 with shim binaries; 2 is covered by argv/URL construction unit test only, no network).
- **Isolation env** (`_isolation_env`): `HOME=<wd>/home`, `XDG_CONFIG_HOME=<wd>/home/.config`, `XDG_DATA_HOME=<wd>/home/.local/share`, `XDG_CACHE_HOME=<wd>/home/.cache`, `NO_OPEN_BROWSER=1`. No GROK_HOME/CLAUDE_CONFIG_DIR analogs (cursor ingests no foreign agent config — but the Stage-5 test asserts the env contains no path pointing outside `<wd>` except PATH). `_build_cursor_shell_command` must consume this helper (single source of truth) so iframe + conversation launches share identical isolation.
- Cache dir must NOT be captured by snapshots (outside the workdir — already true) and must survive eviction (re-populate on miss).
- Every task: failing test first, minimal impl, commit (no Co-Authored-By).

---

### Task 1: `_isolation_env` helper

**Files:** Modify `src/optio_cursor/host_actions.py`; Test `tests/test_host_actions.py`

**Interfaces:**
- Produces: `def _isolation_env(workdir: str) -> dict[str, str]` returning the HOME/XDG_*/NO_OPEN_BROWSER map.
- `_build_cursor_shell_command` refactored to build its env assignments from `_isolation_env(workdir)` (+ PATH + extras), behavior identical for the existing Stage-0 test.

- [ ] **Step 1: Failing test** — `env = _isolation_env("/w/task")`; assert all five keys with `/w/task/home...` values.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement helper; refactor `_build_cursor_shell_command` to consume it. Existing `test_env_isolation_and_done_error` must still pass.
- [ ] **Step 4:** Run full `tests/test_host_actions.py` → PASS.
- [ ] **Step 5: Commit** `refactor(optio-cursor): single _isolation_env helper (Stage 5)`.

---

### Task 2: Cache-backed `ensure_cursor_installed`

**Files:** Modify `src/optio_cursor/host_actions.py`; Test `tests/test_cursor_cache.py`

**Interfaces:**
- `def _resolve_install_dir(install_dir: str | None) -> str` — `install_dir` else `CURSOR_CACHE_DIR` else `${XDG_CACHE_HOME:-$HOME/.cache}/optio-cursor`, resolved against the worker's real env.
- `async def ensure_cursor_installed(hook_ctx, *, install_if_missing=True, install_dir=None) -> str` — resolve cache dir; if a cached entrypoint exists, return it; else populate per the population order above using only generic Host primitives (`run_command` for `command -v cursor-agent` / `readlink -f` / `cp -a`, vendor install via `run_command` curl|bash with HOME staged); raise clearly when nothing works.

- [ ] **Step 1: Failing test** — with a temp cache dir containing an executable `cursor-agent`, `ensure_cursor_installed(install_dir=<tmp>)` returns it (cache hit, no copy). With an empty cache but a fake `cursor-agent` on PATH (shim + fake version-dir layout: a symlink to `versions/1.0/cursor-agent`), it copies the version dir into the cache and returns the cached path resolving through the copied tree. (LocalHost + monkeypatched PATH, as existing tests do.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt grok's cache logic; add the vendor-installer population branch guarded so tests never hit the network).
- [ ] **Step 4:** Run → PASS; full suite `pytest packages/optio-cursor/tests -v` green (Stage-0 session tests still pass — they pass `cursor_install_dir=<shim>` which is now the cache dir with the shim).
- [ ] **Step 5: Commit** `feat(optio-cursor): optio-owned evictable cursor-agent binary cache (Stage 5)`.

---

## Self-Review
- Spec Stage 5 (evictable optio-owned cache, no host pollution, per-task HOME/XDG identity) ↔ Tasks 1-2.
- Cursor advantage over grok flagged: confirmed vendor installer → vendor-first population, host-copy fallback; version-dir (not single-file) copy semantics called out explicitly.
- No placeholders; tests + reference pointers per task. Names consistent: `_isolation_env`, `_resolve_install_dir`, `ensure_cursor_installed`.
