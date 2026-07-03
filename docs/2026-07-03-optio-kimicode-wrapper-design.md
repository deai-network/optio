# optio-kimicode — Wrapper Design

Goal: a full-featured optio wrapper around **Kimi Code CLI** (Moonshot,
`@moonshot-ai/kimi-code`), at parity with `optio-claudecode` / `optio-opencode` /
`optio-grok`, following `docs/writing-agent-wrappers.md`. Target = all 30 rows of
Appendix A, reached via the staged path (guide Part 3).

Reference source (untracked, under `.kimi-src/`): `kimi-code` (target), `kimi-cli`
(Python predecessor, auth/protocol lineage). Both cloned for direct analysis.

---

## 1. Target profile (guide Part 1 — empirically established)

| Q | Finding | Source (in `.kimi-src/kimi-code`) |
|---|---|---|
| Headless API? | **Yes** — `kimi acp` (JSON-RPC/ACP over stdio); also `-p --output-format stream-json`; also `kimi server run` REST+WS. | `apps/kimi-code/src/cli/sub/acp.ts`, `commands.ts` |
| Own web server? | **Yes** — `kimi server run` (:58627 loopback, OpenAPI) serving `apps/kimi-web` SPA; also `kimi vis` (read-only). | `apps/kimi-code/src/cli/sub/server/`, `apps/kimi-web` |
| TUI-only? | No — TUI exists but a native web UI also ships. | — |
| Headless login? | **Device-code (RFC 8628)** — prints `verification_uri_complete`+`user_code` to stderr; browser open is best-effort `xdg-open`, swallowed → works over bare SSH. API-key bypass also available. | `packages/oauth/src/oauth.ts`, `apps/kimi-code/src/cli/sub/login-flow.ts`, `utils/open-url.ts` |
| Resume? | **Yes** — `--continue` (recent by cwd), `--session <id>`, `--resume`. Local `session_<uuid>`; JSONL wire + `state.json` on disk. | `cli/commands.ts`, `agent-core/src/agent/records/persistence.ts` |
| Rotating creds? | **Yes** — single-use rotating refresh tokens; client serializes refresh w/ file lock + re-read to avoid double-spend. | `packages/oauth/src/oauth-manager.ts` |
| Model selection? | Launch `-m <alias>`; **mid-session `/model` switch** (no restart); `KIMI_MODEL_*` env. Effort `low..max`. | `cli/commands.ts`, `agent-core/src/config/env-model.ts` |

**Auth specifics (Stage 4 critical).**
- OAuth host `https://auth.kimi.com` (override `KIMI_CODE_OAUTH_HOST`).
- `client_id = 17e5f671-d194-4dfb-9706-5516cb48c098` (public CLI client, no secret).
- Device auth: `POST {host}/api/oauth/device_authorization` form `{client_id}`.
- Token/refresh: `POST {host}/api/oauth/token` form
  `{client_id, grant_type: refresh_token, refresh_token}`.
- **No `.well-known/openid-configuration`** → hardcode the two endpoints (unlike
  grok's OIDC discovery; otherwise identical direct-refresh pattern).
- Creds at `$KIMI_CODE_HOME/credentials/kimi-code.json` (default `~/.kimi-code`),
  0600, atomic write. Fields: `access_token`, `refresh_token`, `expires_at`,
  `scope`, `token_type`, `expires_in`. **No issuer stored** (host is a constant).
- Refresh threshold: remaining life `< max(300s, 0.5*expires_in)`.
- API-key bypass: `KIMI_MODEL_NAME` + `KIMI_MODEL_API_KEY` (or `KIMI_API_KEY`),
  base `https://api.moonshot.ai/v1`. OAuth-managed base = `https://api.kimi.com/coding/v1`.

**Install specifics (Stage 5).**
- Native SEA binary (no Node needed by end user). Vendor installer
  `curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash`. Also npm
  `@moonshot-ai/kimi-code` (`bin: kimi -> dist/main.mjs`, node ≥22.19 for npm path).
- Two-tier: reuse a worker `kimi` on login-shell PATH (fast copy) else run the
  vendor installer into the evictable cache; symlink into
  `<workdir>/home/.local/bin/kimi`.

**Resume specifics (Stage 2).**
- Session dir: `$KIMI_CODE_HOME/sessions/<workDirKey>/<sessionId>/` with
  `state.json` + `agents/main/wire.jsonl` (append-only source of truth) + subdirs.
- `session_index.jsonl` (one `{sessionId, sessionDir, workDir}` record/line) must
  stay consistent for `--continue`/list.
- **workDirKey caveat:** `wd_<slug>_<sha256(absWorkDir)[:12]>` — hashes the
  absolute workdir path. Snapshot/restore MUST restore under the identical workdir
  path (optio already fixes the workdir), else the bucket key drifts and resume
  misses. Snapshot = tar the session dir + the matching index line. `kimi export`
  exists but we use optio's generic snapshot machinery.

---

## 2. Architecture

A `TaskInstance` factory (`create_kimicode_task(...)`) + a `KimiCodeTaskConfig`
dataclass, bundling mode-adapters. Runs `kimi` as a managed subprocess (local or
SSH) via the generic `optio_host.Host`. Supplies the backend-specific callbacks to
the shared `run_log_protocol_session` driver; everything generic (workdir lifecycle,
`optio.log` loop, hooks, seed/lease, snapshots) is inherited.

**Module layout** — mirror `optio-grok` exactly (`packages/optio-kimicode/src/optio_kimicode/`):

| Module | Responsibility |
|---|---|
| `types.py` | `KimiCodeTaskConfig` + validation (Appendix D surface) |
| `session.py` | `create_kimicode_task` + `run_kimicode_session`; `body`/`_prepare`/`_agent_sender`; iframe & conversation branches; teardown (graceful-flush gate) |
| `host_actions.py` | `build_host`, `_resolve_install_dir`, `_isolation_env`, `ensure_kimicode_installed` (two-tier install), `_build_claustrum_wrap` |
| `conversation.py` | `KimiCodeConversation` implementing the `Conversation` protocol over ACP stdio (JSON-RPC) |
| `conversation_listener.py` | per-task optio-side listener bridging the conversation to the UI (claudecode-pattern) |
| `prompt.py` | `compose_agents_md` (writes `AGENTS.md`) from `build_log_channel_prompt` SSOT + resume section + `RESUME_NOTICE` |
| `snapshots.py` | `_capture_snapshot`/restore of the session dir; retention; optional at-rest encryption |
| `seed_manifest.py` | kimi seed manifest over `optio_agents.seeds` (creds dir = `$KIMI_CODE_HOME/credentials`) |
| `cred_watcher.py` | in-session watcher persisting rotated `kimi-code.json` back into the seed; `finally` backstop |
| `verify.py` | host-free `verify_and_refresh_seed` — direct `POST auth.kimi.com/api/oauth/token` refresh; fail-closed status |
| `models.py` | model alias catalog / effort surface for the widget selector |
| `fs_allowlist.py` | claustrum/Landlock grant-flag builder |
| `__init__.py` | public exports |

**Conversation-ui** — add `optio-conversation-ui/src/kimicode/`: `events.ts`
(pure reducer ACP wire → `ChatItem`), `KimiCodeView.tsx` (transport adapter), and a
`widgetData.protocol = "kimicode"` discriminator in `ConversationWidget.tsx`.

---

## 3. Mode / transport decisions

| Concern | Decision | Rationale |
|---|---|---|
| Conversation mode (req) | `kimi acp` JSON-RPC over stdio | native ACP; multi-turn; real permission gating; grok/cursor are direct references |
| Iframe mode | **`kimi web`** (`kimi server run`), token via `#token=`, loopback. Pre-create a session (`POST /sessions`), point the iframe at it, and drive the agent via `POST /sessions/{id}/prompts` (opencode-parity — this is `_agent_sender`, resume PUSH, and auto_start). NOT grok's positional/keystroke (kimi web is a pure server, has no `--continue`/positional). | native driving SPA; opencode-parity. `kimi vis` = later observe-only option |
| Auth | seed-only + direct host-free refresh; API-key bypass alt | endpoints known; RFC 8628 works headless |
| Model switch | inline `/model` (opencode-style) + `-m` launch; effort low..max | kimi supports live switch |
| Browser handling | `redirect` protocol mode | device-code URL surfaced; no interception needed |
| fs-isolation | claustrum/Landlock, fail-closed, default-on | kimi spawns tool subprocesses; mirror claudecode |

**Decided forks:** iframe = `kimi web` (driving); v1 depth = full parity (all 8
stages); execution = decompose into workflow plans, run via Workflow multi-agent.

---

## 4. Staged build path (guide Part 3) — done-criteria per stage

- **Stage 0 — MVP.** `create_kimicode_task` + minimal `body`/`prepare`, iframe
  mode (`kimi web`), prompt composition. Done: demo task launches, works, emits
  `DONE`, tears down clean, local.
- **Stage 1 — Remote/SSH.** `ssh` config selects `RemoteHost`. Done: demo runs
  identically over SSH; only the local-vs-remote bind `isinstance`.
- **Stage 2 — Resume/snapshots.** session-dir tar snapshot; `--continue`; both
  resume-awareness halves (`resume.log` pull doc + pushed `RESUME_NOTICE`, every
  mode); workDir-path pinning; optional at-rest encryption fail-loud. Done:
  relaunch restores session AND agent gets the notice.
- **Stage 3 — Seeds.** kimi seed manifest; `seed_id`/`on_seed_saved`. Done: a
  captured logged-in seed launches a new task already authenticated.
- **Stage 4 — Leases + cred save-back + verify.** pool/lease; `cred_watcher` for
  rotated tokens + `finally` backstop; **graceful-flush teardown gated on seed-in-use**
  (SIGTERM-and-wait for seeded sessions even on cancel — kimi rotates single-use
  tokens, a SIGKILL races the flush); host-free `verify_and_refresh_seed` (direct
  token endpoint; 4xx invalid_grant→dead, transport fail→inconclusive, valid→confirmed).
  Done: two concurrent sessions on one pool don't strand; rotated token survives a
  cancelled session; stale seed refreshed offline, non-billable.
- **Stage 5 — Binary cache + HOME/XDG isolation.** evictable cache outside workdir;
  two-tier install (reuse-or-vendor-install); symlink into task path; per-task
  `KIMI_CODE_HOME`; re-link after resume. Done: bare worker bootstraps kimi;
  snapshots exclude the binary; identities isolated.
- **Stage 6 — Conversation mode + conversation-ui.** `KimiCodeConversation` over
  ACP; reducer + view; `publish_result`. Done: caller drives a live conversation
  and it renders in the dashboard chat UI.
- **Stage 7 — Frontend parity.** permission gate; model switch (inline);
  file up/download (`optio-file:`); tool + thinking verbosity. Done: each works in
  the widget.
- **Stage 8 — Filesystem isolation.** claustrum Landlock wrap; `fs_isolation`/
  `extra_allowed_dirs`; fail-closed, local + remote. Done: agent confined to
  workdir + grants.

---

## 5. Testing (guide Part 4 — both layers required)

- **Layer 1 — fakes.** `tests/fake_kimi.py` + `kimi-shim.sh` speaking ACP/stream
  + the `optio.log` protocol without the real backend; docker-sshd harness for
  remote. Exercises session pipeline, log-parse, deliverables, resume, seeds,
  reducer — deterministic, no network/creds.
- **Layer 2 — real-binary E2E** (opt-in, skip-if-no-binary). The checklist:
  iframe launch/render/DONE; conversation handshake/stream/tool/turn; each surface
  with fs-isolation ON; first-login device-code → creds land → seed captured; seed
  replant; resume; remote SSH one surface. **Row 30 — not "done" until real kimi
  runs every surface.**
- **Layer 3 — real wire → real reducer.** Capture one real ACP turn (incl.
  interleaved reasoning + tool calls) as a fixture; replay through `events.ts`,
  assert coalesced answer bubble, reasoning rows, busy cleared at turn-end. Plus a
  full inbound-chain check (real kimi → Conversation → listener → SSE → reducer).

---

## 6. Wiring (guide Part 5)

- `packages/optio-kimicode/pyproject.toml` (setuptools, src layout; deps
  optio-core/optio-host/optio-agents), mirror grok.
- Register in `packages/optio-demo/Makefile` (`LOCAL_PKGS`/`install -e`) + demo
  `pyproject.toml` deps; add to root `Makefile` `RELEASABLE_PY`.
- Demo trio in `optio_demo/tasks/kimicode.py`: one **seed-setup** (device-code
  login, stop to capture seed) + two **seed-pinned** runs (**iframe** + **conversation**),
  aggregated in `tasks/__init__.py`. `_make_on_seed_saved` capture wiring.

---

## 7. Config surface (`KimiCodeTaskConfig`, Appendix D)

Mirror the reference `types.py` field names for cross-engine consistency, validate
in `__post_init__`: `consumer_instructions`, `mode` (iframe|conversation),
`conversation_ui` (requires conversation), `host_protocol`, `auto_start` (default
**False**), `seed_id`, `supports_resume`, `ssh`, `tool_verbosity`,
`thinking_verbosity` (default hidden), `show_model_selector`/`default_model`,
`show_file_upload`/`max_upload_bytes`, `file_download`/`max_download_bytes`,
`permission_gate`, `permission_mode`, `model`, `effort`, `fs_isolation` (default
on), `extra_allowed_dirs`, `scrub_env`, install-dir overrides/`install_if_missing`,
hooks. kimi-specific: model aliases (not raw ids), effort `low..max`.

---

## 8. Execution plan (post-approval)

Spec → `writing-plans` → decompose into ~5 dependency-ordered plans (roughly:
[0–1 scaffold+MVP+SSH], [2 resume], [3–4 seeds+leases+cred+verify],
[5 install/isolation], [6–8 conversation+ui+frontend+fs-iso]) → execute each via
Workflow multi-agent, real-binary E2E gating parity claims.
