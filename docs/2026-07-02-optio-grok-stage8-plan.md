# optio-grok Stage 8 (Filesystem Isolation — native grok sandbox) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Confine the grok agent (and every tool/subprocess it spawns) to the task workdir + explicit grants, kernel-enforced, fail-closed, on local and remote hosts.

**Architecture — DECISION (diverges from the spec, deliberately):** Grok ships **native Landlock/Seatbelt sandboxing** (`--sandbox`, see `~/.grok/docs/user-guide/18-sandbox.md`) — the same OS primitive claustrom wraps, but built into grok, applied to the whole process at startup, covering `bash`/`grep`/subagents automatically. So `optio-grok` uses grok's **native custom sandbox profile** rather than porting claudecode's claustrum. This avoids the claustrum cross-compile/install machinery entirely and needs no `bubblewrap` (we avoid `deny` lists, which are the only bwrap-requiring feature; a bare Landlock profile suffices). The spec's "claustrum first, native as alt" is reversed here — flag this and update the spec.

**Fail-closed rationale:** grok's **built-in** profiles fail-OPEN ("logs a warning and continues without enforcement" if the kernel can't apply them) — unacceptable for optio. But an explicitly-requested **custom** profile fails-CLOSED ("refuses to start rather than run"). Therefore optio-grok MUST launch under a **custom** profile (named, e.g., `optio`) so isolation is fail-closed.

**Tech Stack:** Python, grok `--sandbox`, a planted `.grok/sandbox.toml`.

## Global Constraints

- Branch `csillag/optio-grok`. Reference for the config surface (`fs_isolation`, `extra_allowed_dirs`, `AllowedDir`) = `optio-claudecode/types.py`; but the mechanism is grok-native, NOT claustrum.
- **Custom profile** planted at `<workdir>/home/.grok/sandbox.toml` (global scope for that GROK_HOME) as `[profiles.optio]`: `extends = "strict"`, `read_write = [<workdir>, "/tmp", "/var/tmp"]` + caller `extra_allowed_dirs` (rw) and `read_only` for ro grants. NO `deny` list by default (keeps it Landlock-only, no bwrap). Grok auto-grants `~/.grok/` writes (session persistence).
- Launch both bodies (iframe + conversation) with `--sandbox optio` when `fs_isolation=True`.
- `fs_isolation: bool = True` (default-on, like claudecode). When True and the profile can't apply, grok refuses to start → the task fails loudly (fail-closed). Document that a custom profile requires Landlock (kernel ≥5.13) — matches the deployment already used for claudecode.
- Resume-safe: grok fixes a session's profile for its life and restores it on `--continue` — so no profile-mismatch handling needed beyond always passing the same `optio` profile.
- Config `extra_allowed_dirs: list[AllowedDir] | None`, `AllowedDir(path, mode ∈ {"ro","rw"})`; `~/` expands against the real host home.
- Every task: failing test first, minimal impl, commit.

---

### Task 1: `fs_allowlist.py` — sandbox.toml builder

**Files:** Create `src/optio_grok/fs_allowlist.py`; modify `types.py` (add `AllowedDir`, `fs_isolation`, `extra_allowed_dirs`); Test `tests/test_fs_allowlist.py`

**Interfaces:**
- `def build_sandbox_toml(*, workdir: str, extra_allowed_dirs: list[AllowedDir] | None, host_home: str) -> str` — returns the `[profiles.optio]` TOML: `extends="strict"`, `read_write=[workdir, "/tmp", "/var/tmp", *rw_extras]`, `read_only=[*ro_extras]` (with `~/` expanded against `host_home`).
- `types.py`: `AllowedDir(path: str, mode: Literal["ro","rw"])`; `fs_isolation: bool = True`; `extra_allowed_dirs: list[AllowedDir] | None = None`; `__post_init__` validates modes.

- [ ] **Step 1: Failing test** — `build_sandbox_toml(workdir="/w/task", extra_allowed_dirs=[AllowedDir("~/data","ro"), AllowedDir("/scratch","rw")], host_home="/home/u")` contains `[profiles.optio]`, `extends = "strict"`, `/w/task` and `/scratch` in `read_write`, `/home/u/data` in `read_only`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (simple TOML string builder; expand `~/`).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): native-sandbox profile builder (Stage 8)`.

---

### Task 2: Wire `--sandbox optio` into both launch paths

**Files:** Modify `src/optio_grok/session.py`, `host_actions.py`; Test `tests/test_host_actions.py` + a session test

**Interfaces:**
- `host_actions.build_grok_flags(...)` gains `fs_isolation: bool` → appends `--sandbox optio` when True.
- `session.py` `_prepare`: when `config.fs_isolation`, plant `<workdir>/home/.grok/sandbox.toml` via `build_sandbox_toml(...)` (using `host.resolve_host_home()` for `~/` expansion) before launch. Both `_grok_body` (iframe) and `_conversation_body` pass `fs_isolation` through to the flags.
- Fake grok: teach `fake_grok.py` to record whether `--sandbox optio` was in its argv (for the wiring assertion); it need not enforce anything.

- [ ] **Step 1: Failing test** — (a) unit: `build_grok_flags(..., fs_isolation=True)` includes `--sandbox optio`. (b) session: run a local iframe task with `fs_isolation=True`; assert `<workdir>/home/.grok/sandbox.toml` was planted with `[profiles.optio]` and the fake recorded `--sandbox optio`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS; full suite green. Update existing Stage-0..6 session tests that construct `GrokTaskConfig` if the new `fs_isolation=True` default changes their behavior — pass `fs_isolation=False` where the fake can't cope, OR make the fake accept the flag (preferred: fake accepts + ignores it, so default-on is exercised).
- [ ] **Step 5: Commit** `feat(optio-grok): fail-closed native sandbox on iframe + conversation launch (Stage 8)`.

---

### Task 3 (optional, env-gated): real-grok Landlock enforcement test

**Files:** `tests/test_sandbox_enforce.py`

- [ ] A `@pytest.mark.skipif` test (skips unless a real `grok` + Landlock kernel present): run a real headless `grok -p "write /etc/optio-probe"` under `--sandbox optio` and assert the write is denied (kernel-enforced). Skip-with-reason otherwise. Commit `test(optio-grok): real-grok sandbox enforcement (skip-if-no-landlock)`.

---

## Self-Review
- Spec Stage 8 (fs isolation, fail-closed, local+remote) ↔ Tasks 1-2; mechanism changed to grok-native (flag spec update).
- Fail-closed guaranteed via CUSTOM profile semantics (built-ins fail-open — not used).
- No bwrap dependency (no `deny` list) → Landlock-only, matching the deployment claudecode already targets.
- Divergence from spec documented; `AllowedDir`/`fs_isolation`/`extra_allowed_dirs` names mirror claudecode for cross-wrapper consistency.
- No placeholders; tests + doc pointer per task.
