# optio-grok Stage 1+2 (Remote/SSH + Resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `optio-grok` (a) run identically over SSH, and (b) resume a terminated iframe session — restoring workdir + grok session state and picking up the conversation.

**Architecture:** Adapt `optio-claudecode`'s resume machinery. SSH is already free via the generic `optio_host` Host; Stage 1 is proving it with a remote test. Resume adds a Mongo snapshot store + a restore branch in `_prepare` + a `resume.log` + a prompt resume section, gated on `supports_resume`.

**Tech Stack:** Python, pytest, MongoDB (GridFS blobs), Grok `--continue` / `export` / `import`, tmux+ttyd.

## Global Constraints

- Continue on branch `csillag/optio-grok`. Reference = `optio-claudecode` (`snapshots.py`, the resume branch of `session.py`/`_prepare`, `prompt.py` resume section, `host_actions` archive/restore).
- Grok resume mechanics: `grok --continue` resumes the most recent session for the cwd (grok stores sessions in `<GROK_HOME>/sessions/`, which lives under the preserved `<workdir>/home/.grok`). So restoring the workdir tar (incl. `home/.grok`) + passing `-c` is the core mechanism — simpler than claude's transcript rekey. VERIFY against a real `grok --continue` probe during the task; if grok needs an explicit session id, fall back to `export`/`import` like opencode.
- Snapshot = one workdir GridFS blob (Stage 2 keeps it single-blob: `home/.grok` sessions live inside the workdir tar; no separate encrypted session blob unless a probe shows grok state lives outside the workdir). Retention 5.
- Decrypt/restore failure on a present snapshot must fail LOUD (no silent fresh-start) — mirror claudecode.
- Flip `GrokTaskConfig.supports_resume` default to `True`. Add `workdir_exclude: list[str] | None = None`.
- Every task: failing test first, minimal impl, commit.

---

### Task 1: Stage 1 — remote/SSH proof

**Files:** Test `tests/test_session_remote.py` (adapt claudecode's remote test + its docker-sshd fixture / compose if present).

**Interfaces:** No production code change expected — `_build_host` already returns `RemoteHost` when `config.ssh` is set.

- [ ] **Step 1:** Read claudecode's `tests/test_session_remote.py` + any `docker-compose`/sshd fixture in claudecode `tests/`. Mirror the fixture for grok (reuse the same sshd image; the fake-grok shim must be present on the "remote").
- [ ] **Step 2:** Write a remote deliverable test: same as `test_local_deliverable_callback_fired` but with `ssh=SSHConfig(...)` pointing at the docker sshd. Expect PASS. If the docker-sshd harness is unavailable in this env, mark the test `skip` with a clear reason (as claudecode does) — do NOT weaken it.
- [ ] **Step 3:** Run `pytest tests/test_session_remote.py -v`. PASS or skipped-with-reason.
- [ ] **Step 4: Commit** `test(optio-grok): remote/SSH iframe session (Stage 1)`.

---

### Task 2: `snapshots.py` — Mongo snapshot store

**Files:** Create `src/optio_grok/snapshots.py`; Test `tests/test_snapshots.py`

**Interfaces:**
- Produces (mirror claudecode signatures, collection `{prefix}_grok_session_snapshots`):
  - `async def insert_snapshot(db, prefix, *, process_id, end_state, workdir_blob_id) -> None`
  - `async def load_latest_snapshot(db, prefix, process_id) -> dict | None`
  - `async def prune_snapshots(db, prefix, process_id, *, retention=5) -> list` (returns stale GridFS blob ids for caller deletion)
- Consumed by: Task 3.

- [ ] **Step 1: Failing test** — insert two snapshots, assert `load_latest_snapshot` returns the newer; insert 7, assert `prune_snapshots` retains 5 and returns 2 stale ids. (Adapt claudecode `tests/test_snapshots.py`; uses `mongo_db` fixture.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting claudecode `snapshots.py` (drop the separate session-blob field; keep `workdir_blob_id` only unless a grok probe proves state lives outside the workdir).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): Mongo session-snapshot store (Stage 2)`.

---

### Task 3: Resume wiring in `session.py` + `types.py` + `prompt.py`

**Files:** Modify `src/optio_grok/session.py`, `types.py`, `prompt.py`, `host_actions.py`; Test `tests/test_session_resume.py`

**Interfaces:**
- Consumes: Task 2.
- `types.py`: flip `supports_resume` default `True`; add `workdir_exclude: list[str] | None = None`.
- `host_actions.py`: add `_append_resume_log_entry(host, *, refreshed=None)` writing one ISO-8601 line (+ optional `REFRESHED:<files>`) to `<workdir>/resume.log`; add `_rotate_optio_log(host)` (append restored optio.log → optio.log.old, truncate). Adapt from claudecode.
- `prompt.py`: add a resume-awareness section (reuse `optio_agents` `RESUME_NOTICE` / the shared resume template) describing preserved workdir (incl. `home/.grok`), the `workdir_exclude` list, and the `resume.log` protocol. Compose it into AGENTS.md.
- `session.py` `_prepare`: when `ctx.resume`, `load_latest_snapshot`; if present, restore the workdir tar (`host_actions.restore_workdir`), rotate optio.log, set a `pass_continue` flag → add `-c` to grok flags (Task from `build_grok_flags(resuming=pass_continue)`); on restore failure with a present snapshot, raise (loud). Append a resume.log entry each launch.
- `session.py` teardown `finally`: before cleanup, if `supports_resume`, capture a snapshot — archive workdir (honoring `workdir_exclude`) → GridFS, `insert_snapshot`, `prune_snapshots` (+ delete stale blobs), `ctx.mark_has_saved_state()`.

- [ ] **Step 1: Failing test** (`test_session_resume.py`): run a `happy` fake-grok task with `supports_resume=True`; assert a snapshot row exists after. Then run a second task with the same `process_id` and `ctx.resume=True`; assert the workdir was restored (a marker file the fake wrote survives) and `-c` was passed (fake-grok records its argv). Adapt claudecode's resume test + extend `fake_grok.py` with a `seed`/`resume` scenario that drops a marker + records argv.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the resume wiring above (adapt claudecode; grok uses workdir-tar + `-c`, no transcript rekey).
- [ ] **Step 4:** Run → PASS; re-run full suite `pytest packages/optio-grok/tests -v`.
- [ ] **Step 5: Commit** `feat(optio-grok): resume — restore workdir + --continue + snapshot capture (Stage 2)`.

---

## Self-Review
- Spec Stage 1 (SSH) ↔ Task 1; Stage 2 (resume/snapshots, resume.log, workdir_exclude, loud-fail) ↔ Tasks 2-3.
- Grok-specific simplification (workdir-tar carries `home/.grok` sessions; `-c` resumes) is flagged for runtime verification with a fallback to `export`/`import`.
- No placeholders; each task has test + concrete deltas + claudecode source pointer.
- Type consistency: `insert_snapshot`/`load_latest_snapshot`/`prune_snapshots`, `_append_resume_log_entry`, `build_grok_flags(resuming=…)`, `workdir_exclude`, `supports_resume` consistent across tasks.
