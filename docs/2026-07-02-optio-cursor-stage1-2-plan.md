# optio-cursor Stage 1+2 (Remote/SSH + Resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `optio-cursor` (a) run identically over SSH, and (b) resume a terminated iframe session — restoring workdir + cursor chat state and picking up the conversation.

**Architecture:** Adapt `optio-grok`'s resume machinery (itself the simplified claudecode pattern). SSH is already free via the generic `optio_host` Host; Stage 1 is proving it with a remote test. Resume adds a Mongo snapshot store + a restore branch in `_prepare` + a `resume.log` + a prompt resume section, gated on `supports_resume`.

**Tech Stack:** Python, pytest, MongoDB (GridFS blobs), Cursor `--continue` / `--resume [chatId]`, tmux+ttyd.

## Global Constraints

- Continue on branch `csillag/cursor`. Reference = `optio-grok` (`snapshots.py`, the resume branch of `session.py`/`_prepare`, `prompt.py` resume section, `host_actions` archive/restore helpers).
- Cursor resume mechanics: `cursor-agent --continue` continues the previous session; chat state lives under `$HOME` (per-task `<workdir>/home/.cursor/...`), which the workdir tar preserves. So restoring the tar + passing `--continue` is the core mechanism — same simplification grok used (no transcript rekey). **VERIFY** the chat-store location against a live run during the task (probe-point 2 of the design); if `--continue` can't find the session from a restored `$HOME`, fall back to recording the chat id (`cursor-agent create-chat` / `ls`) in the snapshot doc and resuming with `--resume <chatId>`.
- Snapshot = one workdir GridFS blob (single-blob: `home/.cursor` chat state lives inside the workdir tar). Retention 5. Collection `{prefix}_cursor_session_snapshots`.
- Decrypt/restore failure on a present snapshot must fail LOUD (no silent fresh-start) — mirror grok.
- Flip `CursorTaskConfig.supports_resume` default to `True`. Add `workdir_exclude: list[str] | None = None`.
- Every task: failing test first, minimal impl, commit (no Co-Authored-By).

---

### Task 1: Stage 1 — remote/SSH proof

**Files:** Test `tests/test_session_remote.py` (adapt grok's remote test + its docker-sshd fixture: `docker-compose.sshd.yml`, `Dockerfile.sshd`).

**Interfaces:** No production code change expected — the host builder already returns `RemoteHost` when `config.ssh` is set.

- [ ] **Step 1:** Read grok's `tests/test_session_remote.py` + `docker-compose.sshd.yml` + `Dockerfile.sshd`. Mirror the fixture for cursor (reuse the same sshd image approach; the fake-cursor shim must be present on the "remote" under the name `cursor-agent`).
- [ ] **Step 2:** Write a remote deliverable test: same as `test_local_deliverable_callback_fired` but with `ssh=SSHConfig(...)` pointing at the docker sshd. Expect PASS. If the docker-sshd harness is unavailable in this env, mark the test `skip` with a clear reason (as grok does) — do NOT weaken it.
- [ ] **Step 3:** Run `pytest tests/test_session_remote.py -v`. PASS or skipped-with-reason.
- [ ] **Step 4: Commit** `test(optio-cursor): remote/SSH iframe session (Stage 1)`.

---

### Task 2: `snapshots.py` — Mongo snapshot store

**Files:** Create `src/optio_cursor/snapshots.py`; Test `tests/test_snapshots.py`

**Interfaces:**
- Produces (mirror grok signatures, collection `{prefix}_cursor_session_snapshots`):
  - `async def insert_snapshot(db, prefix, *, process_id, end_state, workdir_blob_id) -> None`
  - `async def load_latest_snapshot(db, prefix, process_id) -> dict | None`
  - `async def prune_snapshots(db, prefix, process_id, *, retention=5) -> list` (returns stale GridFS blob ids for caller deletion)
- Consumed by: Task 3.

- [ ] **Step 1: Failing test** — insert two snapshots, assert `load_latest_snapshot` returns the newer; insert 7, assert `prune_snapshots` retains 5 and returns 2 stale ids. (Adapt grok `tests/test_snapshots.py`; uses `mongo_db` fixture.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement adapting grok `snapshots.py` verbatim (rename grok→cursor).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-cursor): Mongo session-snapshot store (Stage 2)`.

---

### Task 3: Resume wiring in `session.py` + `types.py` + `prompt.py`

**Files:** Modify `src/optio_cursor/session.py`, `types.py`, `prompt.py`, `host_actions.py`; Test `tests/test_session_resume.py`

**Interfaces:**
- Consumes: Task 2.
- `types.py`: flip `supports_resume` default `True`; add `workdir_exclude: list[str] | None = None`.
- `host_actions.py`: add `_append_resume_log_entry(host, *, refreshed=None)` writing one ISO-8601 line (+ optional `REFRESHED:<files>`) to `<workdir>/resume.log`; add `_rotate_optio_log(host)` (append restored optio.log → optio.log.old, truncate). Adapt from grok.
- `prompt.py`: add a resume-awareness section (reuse `optio_agents` `RESUME_NOTICE` / the shared resume template) describing preserved workdir (incl. `home/.cursor`), the `workdir_exclude` list, and the `resume.log` protocol. Compose it into AGENTS.md.
- `session.py` `_prepare`: when `ctx.resume`, `load_latest_snapshot`; if present, restore the workdir tar (grok's restore helper), rotate optio.log, set a `pass_continue` flag → `build_cursor_flags(resuming=pass_continue)` emits `--continue`; on restore failure with a present snapshot, raise (loud). Append a resume.log entry each launch.
- `session.py` teardown `finally`: before cleanup, if `supports_resume`, capture a snapshot — archive workdir (honoring `workdir_exclude`) → GridFS, `insert_snapshot`, `prune_snapshots` (+ delete stale blobs), `ctx.mark_has_saved_state()`.

- [ ] **Step 1: Failing test** (`test_session_resume.py`): run a `happy` fake-cursor task with `supports_resume=True`; assert a snapshot row exists after. Then run a second task with the same `process_id` and `ctx.resume=True`; assert the workdir was restored (a marker file the fake wrote survives) and `--continue` was passed (fake-cursor records its argv). Adapt grok's resume test + extend `fake_cursor.py` with the marker + argv-recording scenario.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the resume wiring above (adapt grok; workdir-tar + `--continue`, no transcript rekey).
- [ ] **Step 4:** Run → PASS; re-run full suite `pytest packages/optio-cursor/tests -v`.
- [ ] **Step 5: Commit** `feat(optio-cursor): resume — restore workdir + --continue + snapshot capture (Stage 2)`.

---

## Self-Review
- Spec Stage 1 (SSH) ↔ Task 1; Stage 2 (resume/snapshots, resume.log, workdir_exclude, loud-fail) ↔ Tasks 2-3.
- Cursor-specific simplification (workdir-tar carries `home/.cursor` chat state; `--continue` resumes) is flagged for runtime verification with a fallback to `--resume <chatId>` + recorded chat id.
- No placeholders; each task has test + concrete deltas + grok source pointer.
- Type consistency: `insert_snapshot`/`load_latest_snapshot`/`prune_snapshots`, `_append_resume_log_entry`, `build_cursor_flags(resuming=…)`, `workdir_exclude`, `supports_resume` consistent across tasks.
