# optio-cursor Stage 8 (Filesystem Isolation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Confine the cursor agent (and every tool/subprocess it spawns) to the task workdir + explicit grants, kernel-enforced, fail-closed, on local and remote hosts.

**Architecture — decision deferred to Task 0 probe:** Cursor ships a native sandbox (`--sandbox enabled|disabled`, `CURSOR_SANDBOX`, `cli-config.json` `sandbox.{mode,networkAccess}`, and a `.cursor/sandbox.json` file referenced in the binary). Whether it is (a) allowlist-configurable per-path and (b) fail-closed is UNKNOWN — unlike grok, whose custom profiles were documented fail-closed. Task 0 probes this. Decision rule: use the native sandbox iff a custom/explicit configuration both confines to an explicit path allowlist AND refuses to run when enforcement is unavailable; otherwise port claudecode's claustrum wrap (`fs_allowlist.py` + `_build_claustrum_wrap`) exactly as the guide's Stage-8 reference prescribes.

**Tech Stack:** Python; cursor `--sandbox` / `sandbox.json`, or claustrum (Landlock CLI).

## Global Constraints

- Branch `csillag/cursor`. Config surface (`fs_isolation: bool = True`, `extra_allowed_dirs: list[AllowedDir] | None`, `AllowedDir(path, mode ∈ {"ro","rw"})`) mirrors claudecode/grok regardless of mechanism. `~/` expands against the real host home.
- Fail-closed is non-negotiable: when `fs_isolation=True` and enforcement can't be applied, the task must fail loudly, never run unconfined.
- Both bodies (iframe + conversation) launch confined.
- Every task: failing test first, minimal impl, commit (no Co-Authored-By). Fake-cursor records sandbox argv/config for wiring assertions; enforcement tests are env-gated real-binary tests.

---

### Task 0: Native-sandbox probe (pin the mechanism)
- [ ] Probe cursor's sandbox: (a) grep the binary/docs for `sandbox.json` schema + `CURSOR_SANDBOX` semantics; (b) run a real `cursor-agent -p 'write to /etc/optio-probe and to $PWD/ok.txt' --sandbox enabled --trust` in a scratch dir (auth-gated — skip the live half if not logged in, keeping the strings/docs analysis); (c) determine: path-allowlist configurability, network toggle, and fail-open vs fail-closed when Landlock/бwrap unavailable. Record findings + the DECISION (native vs claustrum) in a comment block + update the design doc §2/§7. (No commit yet — research feeding Task 1.)

### Task 1: Allowlist builder (mechanism per Task 0)
**Files:** Create `src/optio_cursor/fs_allowlist.py`; modify `types.py` (add `AllowedDir`, `fs_isolation`, `extra_allowed_dirs`); Test `tests/test_fs_allowlist.py`
- **If native:** builder emits cursor's sandbox config (sandbox.json / cli-config sandbox section) with `read_write=[workdir, /tmp, /var/tmp, *rw_extras]`, `read_only=[*ro_extras]` equivalents, planted under `<workdir>/home/.cursor/`.
- **If claustrum:** port claudecode's `fs_allowlist.py` grants builder + `_build_claustrum_wrap` (wrap the cursor argv), including the claustrum binary provisioning claudecode uses.
- [ ] RED (builder output asserted, mirroring grok's `build_sandbox_toml` test shape) → implement → GREEN → **Commit** `feat(optio-cursor): fs-isolation allowlist builder (Stage 8)`.

### Task 2: Wire isolation into both launch paths
**Files:** Modify `src/optio_cursor/session.py`, `host_actions.py`; Test `tests/test_host_actions.py` + a session test
- `fs_isolation=True` default-on; `_prepare` plants the config (native) or wraps the argv (claustrum); both `_cursor_body` and `_conversation_body` launch confined; fake-cursor records the sandbox argv/config for assertions; existing tests updated per grok's Stage-8 pattern (fake accepts + ignores the flag so default-on is exercised).
- [ ] RED → implement → GREEN; full suite green. **Commit** `feat(optio-cursor): fail-closed fs isolation on iframe + conversation launch (Stage 8)`.

### Task 3 (env-gated): real-cursor enforcement test
**Files:** `tests/test_sandbox_enforce.py`
- [ ] `@pytest.mark.skipif` unless real `cursor-agent` + auth + Landlock kernel + an opt-in env var (mirror grok's `OPTIO_GROK_REAL_SANDBOX_TEST` gating — billable): probe that a write outside the allowlist is denied and a workdir write succeeds. Skip-with-reason otherwise. **Commit** `test(optio-cursor): real-cursor sandbox enforcement (opt-in, skip-if-no-landlock)`.

---

## Self-Review
- Spec Stage 8 (fs isolation, fail-closed, local+remote, native-vs-claustrum decision at probe time) ↔ Tasks 0-3 — matches design Decision 6.
- Fail-closed rule stated as the decision criterion, not an afterthought.
- Config surface names mirror claudecode/grok for cross-wrapper consistency.
- Billable real-binary test opt-in gated (grok precedent).
- No placeholders; tests + reference pointers per task.
