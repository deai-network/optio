# optio-kimicode Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: execute via **Workflow** multi-agent
> orchestration (project convention). Steps use checkbox (`- [ ]`) syntax.
> This is a **port**: each task mirrors a named reference module from
> `packages/optio-grok` (or `optio-claudecode` where noted) into
> `packages/optio-kimicode`, applying the kimi-specific deltas profiled in the spec.

**Goal:** Ship a full-parity optio wrapper around Kimi Code CLI (`@moonshot-ai/kimi-code`).

**Architecture:** `TaskInstance` factory bundling mode-adapters over the shared
`optio_agents` log-protocol driver; ACP-stdio conversation transport, `kimi web`
iframe surface, seed-based auth with direct host-free token refresh, claustrum
fs-isolation. Mirrors `optio-grok` module-for-module.

**Tech Stack:** Python 3 (setuptools/src layout), `optio-core`/`optio-host`/
`optio-agents`; TypeScript React for `optio-conversation-ui`. Target binary: kimi
native SEA. Tests: pytest + fake-kimi shim + docker-sshd; vitest for the reducer.

**Reference (untracked):** `.kimi-src/kimi-code` (target CLI), `.kimi-src/kimi-cli`
(lineage). Design SSOT: `docs/2026-07-03-optio-kimicode-wrapper-design.md`.
Porting guide: `docs/writing-agent-wrappers.md`.

## Global Constraints (verbatim from spec)

- Package `packages/optio-kimicode`, module root `src/optio_kimicode/`, mirror
  `optio-grok`'s 13-module layout exactly.
- OAuth: host `https://auth.kimi.com`, `client_id=17e5f671-d194-4dfb-9706-5516cb48c098`
  (public, no secret); device auth `POST /api/oauth/device_authorization {client_id}`;
  token/refresh `POST /api/oauth/token {client_id, grant_type, [device_code|refresh_token]}`.
  **No `.well-known`** — hardcode endpoints. Refresh when life `< max(300s, 0.5*expires_in)`.
- Creds: `$KIMI_CODE_HOME/credentials/kimi-code.json` (0600), fields
  `access_token/refresh_token/expires_at/scope/token_type/expires_in`, no issuer.
  Rotating single-use refresh tokens — serialize refresh; persist rotated RT.
- API-key bypass: `KIMI_MODEL_NAME`+`KIMI_MODEL_API_KEY` (or `KIMI_API_KEY`).
- Isolation env: per-task `KIMI_CODE_HOME` under `<workdir>/home`.
- Install: two-tier — reuse worker `kimi` on PATH else vendor installer
  `https://code.kimi.com/kimi-code/install.sh`; symlink to `<workdir>/home/.local/bin/kimi`.
- Session dir `$KIMI_CODE_HOME/sessions/<workDirKey>/<sessionId>/`; `workDirKey`
  hashes abs workdir path → restore under identical path; keep `session_index.jsonl` consistent.
- `auto_start` default False; `thinking_verbosity` default hidden; `fs_isolation`
  default on, fail-closed. Effort enum `low|medium|high|xhigh|max`. Models are
  aliases, not raw ids.
- No `Co-Authored-By` in commits. Use `.venv` inside the worktree, never global pip -e.
- Real-binary E2E (Appendix A row 30) gates any "done" claim per surface.

---

## Plan group 1 — Scaffold + MVP + SSH (Stages 0–1)

**Deliverable:** `packages/optio-kimicode` installs; `create_kimicode_task` launches
`kimi web` iframe locally and over SSH, emits `DONE`, tears down.

### Task 1.1 — Package scaffold
**Files:** Create `packages/optio-kimicode/pyproject.toml`, `src/optio_kimicode/__init__.py`,
`tests/`. **Port from:** `packages/optio-grok/pyproject.toml` (rename, adjust deps).
- [ ] Mirror grok's `pyproject.toml`; deps `optio-core`, `optio-host`, `optio-agents`.
- [ ] `.venv` in worktree; `pip install -e` all four; `python -c "import optio_kimicode"`.
- [ ] Commit.

### Task 1.2 — `types.py` (`KimiCodeTaskConfig`)
**Files:** Create `src/optio_kimicode/types.py`, `tests/test_types.py`.
**Port from:** `optio-grok/.../types.py`. **Deltas:** mode `iframe|conversation`;
effort enum `low..max`; model = alias; default base `api.kimi.com/coding/v1`.
- [ ] Test: `__post_init__` rejects bad enum + `conversation_ui` without conversation mode.
- [ ] Port dataclass + validation (spec §7 field list). Test passes. Commit.

### Task 1.3 — `host_actions.py` core (`build_host`, isolation env)
**Files:** Create `src/optio_kimicode/host_actions.py`, `tests/test_host_actions.py`.
**Port from:** `optio-grok/.../host_actions.py`. **Deltas:** `_isolation_env` sets
`KIMI_CODE_HOME=<workdir>/home`; install-dir resolution (full two-tier deferred to grp 4).
- [ ] Test `build_host` local/remote select; `_isolation_env` sets `KIMI_CODE_HOME`. Commit.

### Task 1.4 — `prompt.py` (`compose_agents_md`)
**Files:** Create `src/optio_kimicode/prompt.py`, `tests/test_prompt.py`.
**Port from:** grok `prompt.py`. **Deltas:** writes `AGENTS.md` (kimi's memory file);
`build_log_channel_prompt(features)` SSOT + resume section + `RESUME_NOTICE`; honor
`host_protocol=False`.
- [ ] Test: composed file contains keyword docs when features on; System explainer when off. Commit.

### Task 1.5 — `session.py` iframe branch (Stage 0 MVP)
**Files:** Create `src/optio_kimicode/session.py`, `tests/test_session_iframe.py` +
`tests/fake_kimi.py`, `tests/kimi-shim.sh`. **Port from:** grok `session.py` iframe
branch + grok's fake. **Deltas:** `body` launches `kimi server run`/`kimi web`,
tunnels, injects `#token=`; readiness = server port up.
- [ ] Build fake-kimi shim (serves a stub web page, speaks `optio.log`).
- [ ] Test: task launches iframe, reaches `DONE`, tears down (fake). Commit.

### Task 1.6 — SSH parity (Stage 1)
**Files:** Modify `host_actions.py`, `session.py`; `tests/test_session_remote.py` +
docker-sshd harness. **Port from:** grok remote test + `build_host`.
- [ ] Test (docker-sshd): iframe task runs over `RemoteHost`; only bind `isinstance`. Commit.

---

## Plan group 2 — Resume + snapshots (Stage 2)

**Deliverable:** a relaunched task restores the kimi session and receives the resume notice.

### Task 2.1 — `snapshots.py`
**Port from:** grok `snapshots.py`. **Deltas:** capture/restore
`$KIMI_CODE_HOME/sessions/<workDirKey>/<sessionId>/` + the `session_index.jsonl`
line; **restore under identical workdir path** (workDirKey hashes abs path);
retention; optional at-rest encryption (fail-loud on decrypt error).
- [ ] Test: capture→wipe→restore round-trips the session dir + index line. Commit.

### Task 2.2 — resume wiring in `session.py` + `prompt.py` (+ correct group-1 iframe)
**CORRECTION (post group-1):** kimi's iframe is `kimi server run`/`kimi web` — a pure
web server with NO `--continue`, NO positional prompt (grok's iframe PUSH shape does not
apply). The parity target is **opencode**: it pre-creates a session, points the iframe at
it, and PUSHes via `POST /sessions/{id}/prompts`. Group 1 shipped iframe `_agent_sender`
as `NotImplementedError` — this task replaces it with the REST session-POST.
**Deltas (mirror opencode `session.py` lines ~380-446 + `_post_opencode_prompt`):**
- Pre-create a kimi session (`POST /sessions`), capture `session_id`, point the iframe
  `iframeSrc` at that session (verify SPA URL form against `.kimi-src/.../apps/kimi-web`).
- Implement iframe `_agent_sender(text)` = `POST /sessions/{session_id}/prompts` (bearer token, loopback).
- Snapshot restore in `_prepare` (on `ctx.resume` + latest snapshot), capture on teardown
  (gated `supports_resume`).
- PULL half: `resume.log` doc already in `prompt.py` (group 1); port `_append_resume_log_entry`
  + `_rotate_optio_log` to `host_actions`, call in `_prepare` (append every start; rotate on resume).
- PUSH half: on resume, `POST` `SYSTEM_MESSAGE_PREFIX+RESUME_NOTICE` to the session (fires
  in BOTH iframe and conversation, like opencode line 429). auto_start POSTs kickoff on fresh launch.
- [ ] Test: relaunch restores the kimi session store AND a resume-notice prompt is POSTed
  to the session AND `resume.log` appended AND `optio.log` rotated (fake kimi server). Commit.

---

## Plan group 3 — Seeds + leases + cred save-back + verify (Stages 3–4)

**Deliverable:** a captured logged-in seed launches an already-authenticated task;
rotated tokens persist and survive cancellation; stale seeds refresh offline.

### Task 3.1 — `seed_manifest.py`
**Port from:** grok `seed_manifest.py`. **Deltas:** creds member =
`$KIMI_CODE_HOME/credentials/kimi-code.json`; adopt `optio_agents.seeds`.
- [ ] Test: capture seed from a creds dir; replant into fresh `KIMI_CODE_HOME`. Commit.

### Task 3.2 — `verify.py` (host-free refresh)
**Port from:** grok `verify.py`, but **hardcode** the two kimi endpoints (no OIDC
discovery). Direct `POST auth.kimi.com/api/oauth/token` refresh grant; write rotated
`access_token/refresh_token/expires_at` back. Status: 4xx `invalid_grant`→dead;
transport/discovery fail→inconclusive (never retire healthy); valid→confirmed.
- [ ] Test (mocked HTTP): each status branch; rotated RT persisted. Commit.
- [ ] **Real-seed confirm** (opt-in): one live refresh proves request shape.

### Task 3.3 — `cred_watcher.py` + leases + graceful-flush teardown
**Port from:** grok `cred_watcher.py` + lease wiring + `_teardown_aggressive`.
**Deltas:** watch `kimi-code.json`; `finally` backstop; **SIGTERM-and-wait for
seeded sessions even on cancel** (single-use RT — SIGKILL races the flush); fast
kill only non-seeded.
- [ ] Test: two concurrent sessions on one pool don't strand; cancelled seeded
  session flushes rotated RT before backstop reads it. Commit.

---

## Plan group 4 — Binary cache + HOME/XDG isolation (Stage 5)

**Deliverable:** a bare worker (no kimi on PATH) bootstraps the binary; snapshots exclude it.

### Task 4.1 — two-tier install in `host_actions.py`
**Port from:** grok/claudecode `ensure_<agent>_installed`, `_resolve_install_dir`.
**Deltas:** tier-1 copy worker `kimi` if on login-shell PATH; **tier-2 run vendor
installer** `code.kimi.com/kimi-code/install.sh` into evictable cache outside
workdir; symlink → `<workdir>/home/.local/bin/kimi`; re-link after resume (idempotent).
- [ ] Test: cache-miss on a no-kimi env provisions the binary; hit relinks only;
  snapshot excludes cache. Commit.
- [ ] **Real-binary** (opt-in): vendor install lands a runnable `kimi`.

---

## Plan group 5 — Conversation + UI + frontend parity + fs-isolation (Stages 6–8)

**Deliverable:** live ACP conversation drives the dashboard chat widget with
permission gating, model switch, file transfer, thinking/tool verbosity; agent
sandboxed by claustrum.

### Task 5.1 — `conversation.py` (ACP over stdio)
**Port from:** grok/cursor `conversation.py` (both ACP). **Deltas:** spawn
`kimi acp`; map ACP JSON-RPC to the `Conversation` protocol (`send`/`on_event`/
`on_message`/`on_permission_request`/`interrupt`/`close`/`closed`); `x-optio-`
synthetic prefix.
- [ ] Test (fake ACP): turn send→events→one final message; permission gate; close. Commit.

### Task 5.2 — `session.py` conversation branch + `conversation_listener.py`
**Port from:** grok conversation branch + claudecode `conversation_listener.py`.
- [ ] Test: conversation task publishes `Conversation`; listener bridges to SSE. Commit.

### Task 5.3 — conversation-ui `kimicode/`
**Files:** Create `packages/optio-conversation-ui/src/kimicode/{events.ts,KimiCodeView.tsx}`;
modify `ConversationWidget.tsx` (add `protocol="kimicode"`). **Port from:** grok/opencode `src/<engine>/`.
- [ ] vitest: pure reducer ACP wire→`ChatItem` (coalesced answer, reasoning rows,
  busy cleared). **Layer-3 fixture:** capture one real kimi ACP turn, replay. Commit.

### Task 5.4 — `models.py` + frontend parity
**Deltas:** model alias catalog; **inline `/model` switch** (opencode-style); effort
`low..max`; file up/download (`optio-file:`); tool + `thinking_verbosity` widgetData
four-touch (config→set_widget_data→ConversationViewProps→view).
- [ ] Tests per feature in the widget. Commit.

### Task 5.5 — `fs_allowlist.py` + claustrum wrap (Stage 8)
**Port from:** claudecode `fs_allowlist.py` + `_build_claustrum_wrap`.
- [ ] Test: agent confined to workdir + `extra_allowed_dirs`; fail-closed; local+remote. Commit.

---

## Plan group 6 — Wiring + demos + real-binary E2E (guide Part 5 + row 30)

### Task 6.1 — packaging + registration
**Files:** Modify `packages/optio-demo/Makefile`, demo `pyproject.toml`, root
`Makefile` `RELEASABLE_PY`. **Port from:** the grok lines.
- [ ] `-e` install list + release list include optio-kimicode. Commit.

### Task 6.2 — demo trio
**Files:** Create `packages/optio-demo/src/optio_demo/tasks/kimicode.py`; modify
`tasks/__init__.py`. **Port from:** `tasks/claudecode.py` (seed-setup + iframe +
conversation trio + `_make_on_seed_saved`).
- [ ] Tasks appear in dashboard; seed lifecycle demonstrable. Commit.

### Task 6.3 — real-binary E2E gate (row 30)
Opt-in, skip-if-no-binary (mirror optio-grok `test_*_sandbox_enforce.py`). Checklist:
iframe launch/render/DONE; conversation handshake/stream/tool/turn; each surface
fs-isolation ON; first-login device-code→seed captured; seed replant; resume;
remote SSH one surface.
- [ ] Each real-binary check passes or is explicitly documented as a tracked gap. Commit.

---

## Self-review

- **Spec coverage:** every spec §1–7 finding maps to a task (auth→3.2/3.3, install→4.1,
  resume→2.x, transports→1.5/5.1, config→1.2, UI→5.3/5.4, fs-iso→5.5, wiring→6.x). ✓
- **Parity rows:** Appendix A rows 1–30 covered across groups 1–6; row 30 = Task 6.3.
- **Execution:** groups are dependency-ordered; within a group tasks fan out where
  independent (per parallel-shaped-plans). Group 1 unblocks all; 2/3/4 parallel after 1;
  5 after 1 (needs 3 for seeded conversation demo); 6 last.
