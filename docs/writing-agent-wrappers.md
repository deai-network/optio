# Writing an optio Coding-Agent Wrapper

This is the porting guide for running a new coding agent as an optio task. optio
already wraps two agents — **Claude Code** (`optio-claudecode`) and **opencode**
(`optio-opencode`) — and this guide is how you add more (codex, cursor, grok, …)
quickly and consistently.

**How to read this guide.** For each thing a wrapper must provide, the guide gives
the *goal* (what capability, and why), the *interface to implement* (the method,
callback, or type you satisfy), and a *pointer* to where the two existing wrappers
already do it. It deliberately does **not** copy implementation code. The two
reference wrappers are the living source of truth — read them as you build. Detail
lives in code so this guide does not rot.

**Parity is the target; staged is how you get there.** A finished wrapper supports
the whole capability surface in [Appendix A](#appendix-a--parity-checklist). A new
wrapper starts behind and reaches parity through the staged path in
[Part 3](#part-3--the-staged-build-path). Shipping a partial wrapper is fine — but
name what is still missing so it is a tracked gap, not a silent one.

---

## Part 0 — Orientation

### What a wrapper is

A wrapper is a **`TaskInstance` factory that bundles mode-adapters.** It exposes a
`create_<agent>_task(...)` function returning an optio-core `TaskInstance`, plus a
config dataclass. Internally it runs the agent as a managed subprocess (local or
over SSH) and binds it to one or more optio *surfaces* (an embedded UI, a live
conversation object, the dashboard chat widget).

The heavy lifting is already generic. The shared driver owns the workdir lifecycle,
the coordination-log loop, the hook context, and the seed/resume machinery. Your
wrapper supplies only the backend-specific pieces: how to install the agent, how to
launch and monitor it, how to talk to it, and how to compose its prompt.

### A wrapper is a bundle of mode-adapters

The central mental model: each **mode** binds one **agent capability** to one
**optio embedding surface**. The config `mode` field selects at task-creation time.
Both existing wrappers already ship more than one mode.

| Agent capability | optio surface | claudecode | opencode |
|---|---|---|---|
| headless / programmatic API | **conversation mode** → drives the dashboard chat UI | stdio stream-json | HTTP + SSE |
| ships its own web server | **iframe** (embed the SPA) | — (TUI has no web UI) | `opencode web` SPA |
| TUI only | **iframe via ttyd** (embed the terminal) | tmux + ttyd | — |

Conversation mode is preferred — it is the richest integration and gets the native
dashboard chat UI for free. Where the agent's headless surface is limited (the
canonical case: **login/auth that cannot run headless**), fall back to an
interactive surface for that operation and/or pre-provision identity with a
**seed**. See [Part 1](#part-1--profile-your-target-agent) and
[Part 4 — Authentication](#authentication-the-headless--remote-browser-problem).

### Package landscape

| Package | Role | Dependency |
|---|---|---|
| `optio-core` | engine, scheduler, `TaskInstance`, `ProcessContext` (`ctx`), `publish_result` / `launch_and_await_result` | base |
| `optio-host` | transport: `Host` protocol with `LocalHost` / `RemoteHost` (SSH) | → optio-core |
| `optio-agents` | **the wrapper contracts**: the log-protocol driver, the `Conversation` protocol, `HookContext`, the prompt SSOT, the seed/lease engine | → optio-host, optio-core |
| `optio-conversation-ui` | engine-neutral React chat widget; renders any wrapper that provides a reducer + adapter | frontend |
| **`optio-<agent>`** | **your wrapper** | → optio-core, optio-host, optio-agents |

Reference wrappers: `packages/optio-claudecode`, `packages/optio-opencode`.
Every part below points into them.

---

## Part 1 — Profile your target agent

Before writing anything, answer these about the target agent. The answers drive
every later decision.

1. **Headless / programmatic API?** Does it expose a way to drive a session
   non-interactively — a stream protocol over stdio, or an HTTP/SSE server? This
   determines whether you get **conversation mode** (the preferred surface).
2. **Own web server?** Does it ship a browsable web UI? If yes, you can embed it
   directly in an **iframe** (the opencode pattern).
3. **TUI-only?** If the only rich UI is a terminal UI, embed it via **ttyd** in an
   iframe (the claudecode pattern).
4. **Headless login?** Can the agent authenticate without an interactive browser?
   If not, you need one of the login strategies in [Part 4 — Authentication](#authentication-the-headless--remote-browser-problem).
5. **Resume?** Can a terminated session be relaunched and pick up its
   conversation/state? This determines how much of Stage 2 applies.
6. **Rotating credentials?** Does it use single-use refresh tokens (so a stored
   token dies after first use)? If yes, you need credential save-back (Stage 4).
7. **Model selection?** Can the model be chosen at launch, switched mid-session, or
   only via restart? This shapes the model-switching adapter (Stage 7).

### Decision: mode selection

- Pick the **primary mode**: conversation if the agent has any headless API;
  otherwise iframe-web if it has a server; otherwise iframe-ttyd.
- Pick **fallback mode(s)** for capability gaps in the primary. The usual gap is
  interactive login — solved by seeds or an interactive surface. A wrapper may ship
  several modes simultaneously and let the caller choose per task.

Reference: the `mode` field and its validation in
`optio-claudecode/…/types.py` (`ClaudeCodeTaskConfig`) and
`optio-opencode/…/types.py` (`OpencodeTaskConfig`).

---

## Part 2 — Interfaces to implement

For each interface: the goal, the interface to implement, a reference pointer, and
the key engine divergence (so you can see which parts are contract and which are
mechanism you choose).

### A. Task / log-protocol driver

**Goal.** Run the agent as a managed task with progress reporting, deliverable
handling, cancellation, and clean teardown — without hand-sequencing any of it.

**Interface to implement.** Call `run_log_protocol_session(host, ctx, …)` and
supply the backend-specific callbacks:
- `body(host, hook_ctx)` — launch the agent subprocess and stay alive while it runs.
- `prepare(host, hook_ctx)` — install the runtime and restore resume state (runs
  after the workdir wipe, before the log tail).
- `_agent_sender(text)` — push one message into the live session (engine→agent).

The driver owns everything else: `host.setup_workdir()`, the `optio.log`
tail/parse/dispatch loop, deliverable and caller-message queues, browser shims, the
`HookContext`, and the before/after-execute hooks. It enforces completion semantics
(agent `DONE` → clean return; `ERROR` or premature body exit → failure;
cancellation → clean return).

**The `optio.log` keyword channel.** The agent coordinates with optio by appending
keyword lines the driver parses: `STATUS:` (→ progress), `DELIVERABLE:` (→ fetch),
`DONE` / `ERROR` (→ completion), `BROWSER:` (→ open), `ATTENTION:` (→ need
attention), `CLIENT_MESSAGE:` / `CALLER_MESSAGE:` (opt-in side channels). Optional
keywords are feature-gated — an agent cannot trigger a facility nobody enabled.

**Reference.**
- Contract: `optio-agents/…/protocol/session.py` (`run_log_protocol_session`),
  `…/protocol/parser.py` (`parse_log_line`, the `LogEvent` union),
  `…/protocol/features.py` (`ProtocolFeatures`),
  `…/protocol/protocol.py` (`get_protocol`).
- Implementations: `optio-claudecode/…/session.py` and
  `optio-opencode/…/session.py` — each builds `protocol = get_protocol(...)`,
  defines `body`/`_prepare`/`_agent_sender`, and calls the driver.

**Divergence.** claudecode's `body` runs the agent in a detached tmux session (or
headless stdio in conversation mode); opencode's `body` launches `opencode web` and
tunnels to it. Both reduce to "launch, monitor, surface DONE/ERROR."

### B. `Conversation` protocol

**Goal.** Give a caller a live, backend-agnostic handle to one conversation:
send turns, observe events, gate permissions, interrupt, close.

**Interface to implement.** The `runtime_checkable` Protocol in
`optio-agents/…/conversation.py`:
`send`, `on_event`, `on_message`, `on_permission_request`, `is_pending`,
`interrupt`, `close`, and the `closed` property. Two-tier event model: `on_event`
fans out every raw backend event **unmodified** (live only, no replay);
`on_message` delivers one final answer per completed turn. Permission gating uses
`PermissionRequest` → `PermissionDecision` (allow/deny, optional modified input,
optional deny reason). `send`/`interrupt` raise `ConversationClosed` after the
session ends.

**Reference.**
- Contract: `optio-agents/…/conversation.py`.
- Implementations: `optio-claudecode/…/conversation.py` (`ClaudeCodeConversation`,
  over stdio NDJSON) and `optio-opencode/…/conversation.py` (`OpencodeConversation`,
  over HTTP + SSE).

**Divergence.** The transport is entirely your choice — stdio stream-json vs
HTTP/SSE — as long as the Protocol methods behave as specified. Synthetic events
(the ones optio injects, not the backend) use an `x-optio-` type prefix so
reducers can tell them apart.

### C. Conversation UI

**Goal.** Render the agent's conversation in the dashboard chat widget, engine-agnostically.

**Interface to implement.** In `optio-conversation-ui`, add three things:
1. A **pure reducer** `(state, rawEvent, seq) → ChatState` mapping the agent's
   native wire events onto the normalized `ChatItem` union (`user`, `assistant`,
   `activity`, `tool`, `permission`, `error`, `closed`). This is where *all*
   engine-specific knowledge lives; it is DOM-free and unit-tested.
2. A thin **transport-adapter view** that opens the agent's event stream, feeds the
   reducer, and wires the `ConversationViewProps` callbacks (`onSend`,
   `onInterrupt`, `onPermission`, `onFileDownload`, optional `modelSelector`) to the
   agent's endpoints. It then hands all rendering to the shared `ConversationView`.
3. A `widgetData.protocol` discriminator so `ConversationWidget` dispatches to your
   view.

Everything else — markdown/mermaid/katex, streaming caret, copy button,
auto-scroll, theme, permission cards — is provided by the shared renderer once your
reducer emits `ChatItem`s.

**Reference.**
- Contract: `optio-conversation-ui/src/chat.ts` (`ChatItem`, `ChatState`),
  `…/src/ConversationView.tsx` (`ConversationViewProps`),
  `…/src/ConversationWidget.tsx` (dispatch).
- Implementations: `…/src/claudecode/` (`events.ts` reducer, `ClaudeCodeView.tsx`)
  and `…/src/opencode/` (`events.ts` reducer, `OpencodeView.tsx`).

**Divergence.** claudecode's view is a client of a per-task optio-side listener;
opencode's view is a direct client of the spawned opencode server. Model selection,
file transfer, and permission-verb mapping differ per engine but all funnel through
the same `ConversationViewProps`.

### D. Prompt composition

**Goal.** Produce the agent's memory file (`CLAUDE.md`, `AGENTS.md`, or the
target's equivalent) that teaches it the coordination protocol and the task.

**Interface to implement.** Compose the file from: the shared keyword-protocol docs
(`build_log_channel_prompt(features)` — the single source of truth), a
resume-awareness section, the task framing, and the consumer's verbatim
instructions. Honor `host_protocol=False` (omit keyword docs, add the `System:`
message explainer instead).

**Resume awareness has two halves — ship BOTH.** Shipping only one is a silent
parity gap.
1. **Pull** — document the `resume.log` protocol in the memory file so the agent
   can detect a relaunch itself (each session start appends a line; the agent
   re-reads on a new turn).
2. **Push** — on *every* resume, send the agent a `System: you have been resumed`
   message (`RESUME_NOTICE`, prefixed with `SYSTEM_MESSAGE_PREFIX`) as its first
   turn, so it notices immediately instead of waiting to re-check `resume.log`.
   The push is per-launch-mode: a trailing positional after the agent's
   continue/resume flag for a **TUI/iframe** launch (`agent --continue '<notice>'`),
   the first stdin/API message for a **conversation** launch. Send it in every
   surface; gate it only if the agent isn't taught the `System:` convention in
   that mode.

**Reference.** `optio-agents/…/protocol/prompt.py` (`build_log_channel_prompt`,
`RESUME_NOTICE`); wrappers' `prompt.py` (`compose_agents_md`) in both packages.

---

## Part 3 — The staged build path

Dependency-ordered. Each stage: the goal, the interface/config it touches, a
reference pointer, and "done when." Ship Stage 0, then climb toward parity.

### Stage 0 — MVP
**Goal.** The agent runs as a task in one mode, reports completion, on the local
host. **Touches.** The driver call (Part 2A), a minimal `body`/`prepare`, one mode,
prompt composition (Part 2D). **Reference.** The `create_<agent>_task` +
`run_<agent>_session` skeleton in either wrapper's `session.py`. **Done when.** A
demo task launches, does work, emits `DONE`, and tears down cleanly locally.

### Stage 1 — Remote / SSH
**Goal.** The same task runs on a remote host, indistinguishable from optio's side.
**Touches.** An `ssh` config field selecting `RemoteHost` vs `LocalHost`; use only
generic `Host` primitives. **Reference.** `build_host` in either `host_actions.py`.
**Done when.** The demo runs identically over SSH; no `isinstance` branches except
the local-vs-remote bind decision.

### Stage 2 — Resume / snapshots
**Goal.** A terminated task relaunches and picks up conversation, workdir, and
state. **Touches.** `supports_resume`; snapshot capture/restore (workdir tar +
session-state blob, with retention); `workdir_exclude`; optional at-rest encryption;
`on_resume_refresh`; **both halves of resume awareness** — the `resume.log` pull doc
AND the pushed `System: you have been resumed` notice on relaunch (Part 2D), in
*every* launch mode. **Reference.** `snapshots.py` + `_capture_snapshot`/`_prepare`
in either wrapper; the resume section in `prompt.py`; `build_resume_notice_args`
(iframe positional) and the conversation-body `RESUME_NOTICE` send. **Done when.**
Relaunch by process id restores the session AND the agent receives the resume
notice; decrypt failure fails loud (never silent fresh-start).

### Stage 3 — Seeds
**Goal.** Start a *fresh* session that is already logged-in/configured — the axis
resume can't give, and the answer to headless login. **Touches.** A per-agent seed
manifest adopting the generic `optio_agents.seeds` engine; `seed_id` / `on_seed_saved`;
seed CRUD wrappers. **Reference.** `seed_manifest.py` in either wrapper;
`optio-agents/…/seeds.py`. **Done when.** A seed captured from a logged-in session
launches a new task already authenticated.

### Stage 4 — Leases + credential save-back + verify
**Goal.** Share N seeds safely across concurrent sessions, and keep rotating tokens
alive. **Touches.** The pool/lease layer (`acquire`/`renew_lease`/`release`); an
in-session credential watcher that saves rotated tokens back into the seed, plus a
`finally` backstop that fires on *every* exit path; a host-free
`verify_and_refresh_seed`.

**Two findings that bite hard with single-use rotating tokens:**
1. **Flush before you save — shut the agent down GRACEFULLY when a seed is in
   use.** The agent's write of a just-rotated `auth.json` is best-effort. If
   teardown SIGKILLs the agent (the usual aggressive-kill on *cancel*), the kill
   can beat the flush: the backstop then reads the *stale* file and persists the
   already-spent token, so the next launch of that seed demands re-auth. Gate the
   teardown aggressiveness on whether a seed is in use — SIGTERM-and-wait (let it
   flush) for a seeded session even on cancel; keep the fast kill only for
   non-seeded ones. Ref `_teardown_aggressive` + the teardown block in grok's
   `session.py`.
2. **Verify/refresh via the provider's token endpoint directly — not by launching
   the agent.** Discover the OAuth/OIDC token endpoint from the seed's stored
   issuer (`<issuer>/.well-known/openid-configuration`), run the standard
   `refresh_token` grant (public CLI clients use `client_id` only, no secret), and
   write the rotated `access_token`/`refresh_token`/expiry back into the seed. This
   is **host-free and non-billable** — no agent process, no model inference — and
   the provider owns the token format so you just diff-and-save. Make status
   **fail-closed and precise**: a 4xx `invalid_grant` marks the seed *dead*; a
   transport/discovery failure is *inconclusive* and must never retire a healthy
   seed; a still-valid token is confirmed (userinfo) and left un-rotated. Confirm
   the request shape once against a live seed. Fall back to an agent
   challenge-answer probe (billable) only when the provider exposes no usable
   endpoint.

**Reference.** `cred_watcher.py` + the lease wiring in either wrapper; the direct
endpoint refresh in `optio-claudecode/…/oauth.py` (Anthropic) and
`optio-grok/…/verify.py` (xAI OIDC discovery); `optio-agents/…/seeds.py` lease +
`overwrite_seed_member`. **Done when.** Two concurrent sessions on one owner's seed
pool don't strand each other; a rotated token is persisted back **and survives a
cancelled session** (graceful flush before the backstop); a stale seed is
verified/refreshed offline with no billable agent call.

### Stage 5 — Binary cache + HOME/XDG isolation
**Goal.** Install the agent binary into an optio-owned, evictable cache (never
snapshotted, never polluting the host `~`); give each task its own agent identity.
**Touches.** A cache dir resolved against the worker's real env; per-task
`HOME`/`XDG_*` under the workdir; install-if-missing gating. On a cache miss,
`ensure_<agent>_installed` MUST actually provision the binary. Two-tier is the
shared pattern: if a binary is already on the worker (login-shell `PATH`), seed
the cache from it (a fast local copy, no download); **otherwise** run the vendor
installer (or download the release) into the cache. A stub that does only the
first tier — re-use a binary already on the worker `PATH` — does **not** satisfy
this stage: a fresh or
remote worker with no pre-installed agent must still bootstrap itself. Install
into a persistent location **outside** the task workdir (so tearing down the
workdir never destroys the install), then symlink the cached binary into a
task-accessible launch path under the isolated home (e.g.
`<workdir>/home/.local/bin/<agent>`); `ensure_<agent>_installed` returns that
per-task path. Re-call it after any resume/restore (idempotent: cache hit → just
relink) so the launch symlink — which lives in the wiped-and-restored workdir —
is re-established. **Reference.** `_resolve_install_dir` / `_isolation_env` /
`ensure_<agent>_installed` in either `host_actions.py` (both run the vendor
installer on a miss — claudecode's `install.sh`, grok's `x.ai/cli/install.sh`).
**Done when.** Concurrent tasks/users have isolated identities; the binary is
shared and genuinely re-installable on a bare worker with no host agent present;
snapshots exclude it.

### Stage 6 — Conversation mode + conversation-ui
**Goal.** The headless live `Conversation` (Part 2B) plus the dashboard chat widget
(Part 2C). **Touches.** `mode="conversation"`, `host_protocol` toggle,
`conversation_ui`; `publish_result` of the `Conversation`; the reducer + view.
**Reference.** `conversation.py` + the conversation branch of `session.py` in either
wrapper; `optio-conversation-ui/src/<engine>/`. **Done when.** A caller drives a
live conversation programmatically, and the same task renders in the dashboard chat
UI.

### Stage 7 — Frontend parity
**Goal.** Permission gating, model switching, file upload/download, tool verbosity
in the conversation UI. **Touches.** `permission_gate`; model config
(`show_model_selector`, default model); `show_file_upload`/`max_upload_bytes`,
`file_download`/`max_download_bytes` (the `optio-file:` sentinel); `tool_verbosity`.
**Reference.** The listener/endpoints in either wrapper (claudecode's
`conversation_listener.py`; opencode's server client) and the per-engine view in
`optio-conversation-ui`. **Done when.** Each feature works in the widget for your
engine; model switch may be restart-based (claudecode) or inline (opencode) — both
valid.

### Stage 8 — Filesystem isolation
**Goal.** Sandbox the agent (and its tool subprocesses) to an explicit allowlist,
where the launch model allows. **Touches.** A Landlock sandbox wrap
(claustrum) with a grant-flag builder; `fs_isolation` / `extra_allowed_dirs` /
`delivery_type`; fail-closed. **Reference.** `fs_allowlist.py` +
`_build_claustrum_wrap` in `optio-claudecode` (currently the only implementation —
follow its pattern). **Done when.** The agent can only touch the workdir + explicit
grants; default-on, fail-closed, local and remote.

---

## Part 4 — Cross-cutting concerns

### Host abstraction discipline
Do all host I/O through the generic `optio_host.Host` primitives (`run_command`,
`launch_subprocess`, `write_text`, `establish_tunnel`, `archive_workdir`/
`restore_workdir`, `fetch_bytes_from_host`/`put_file_to_host`, …). Keep
`host_actions.py` a free-function layer over these. The **only** sanctioned
`isinstance` is the Local-vs-Remote bind-address decision. This is what makes
remote-over-SSH nearly free.

### Authentication: the headless + remote-browser problem
Every agent authenticates differently — API key, browser OAuth, device code, an
external SSO binary — and the optio environment is hostile to the browser-based
ones in **two** specific ways. Getting login right is per-agent work: you must
research the agent's actual flow, pick the matching measure, and prove it works
end-to-end. Do not assume; the flow that "just works" on a laptop usually fails
here.

**Research the flow first (empirical, like profiling the target).** Before you
choose a measure, determine, for *your* agent:
- Does it accept a non-interactive credential — an API key / token in an env var
  or config file? (If so, that is almost always the right path — no browser.)
- If it does OAuth: is it a **device-code** flow (prints a URL + short code to the
  terminal and polls — no callback), a **hosted-redirect** flow (redirects to a
  server the agent doesn't host), or a **loopback** flow (redirects to
  `http://127.0.0.1:<port>/callback` that the agent listens on locally)?
- *How* does it open the browser: by spawning a **terminal subprocess**
  (`$BROWSER`, `xdg-open`, `open`, a `webbrowser`-style crate), or from
  **client-side code** (its own web UI / embedded JS `window.open`, or an in-process
  OS call)? This decides whether you can intercept it at all (see below).
- Does it try to log in **automatically on first launch**, or only on an explicit
  `login` command? Auto-login-on-launch is the case that surprises operators.

Determine these by reading the agent's docs, probing its binary/help, and — the
only conclusive method — **watching a real first-login** in the wrapper.

**The two constraints that make login unusual.**
1. **No usable browser on the worker.** The agent runs headless; a direct browser
   launch either silently no-ops or errors. You must *intercept* the open, not let
   it run. (If you plant silent no-op browser shims, login appears to hang with no
   feedback — a real failure mode; see `browser="suppress"`.)
2. **The operator's browser is on a different machine than the agent.** In remote
   (SSH) deployments the human's browser cannot reach the agent's `127.0.0.1`. So
   any **loopback callback** the agent expects to catch locally never comes back to
   it. Local mode (worker == operator's machine) is the lucky exception where
   loopback still works.

**Login-mechanism taxonomy → the measure to take.**
| Agent's mechanism | Measure |
|---|---|
| **API key / token env var** | Inject it via the launch env / a seed. No browser at all. Prefer this whenever the agent offers it. |
| **Device-code flow** | Ideal for this environment: no callback, works remote. If the agent supports it (often a `--device-code`/`--device-auth` flag), force it, and surface the printed URL+code (terminal is visible in the iframe/ttyd surface). |
| **Hosted-redirect OAuth** | Capture the browser-open URL (`redirect` shim) and surface it to the operator; the redirect lands on a server that isn't the agent, so it completes from any machine. |
| **Loopback (`127.0.0.1`) OAuth** | Completes only in **local** mode. For remote, one of: (a) rewrite the loopback `redirect_uri` to a hosted redirect (`browser_url_rewrite`, ref `optio-claudecode/…/oauth_redirect.py`); (b) switch the agent to device-code; (c) avoid login entirely with a seed. |

**Browser-open taxonomy — can you even intercept it?**
- **Terminal / subprocess open** (`$BROWSER`, `xdg-open`, `open`, a native crate
  that shells out) — **interceptable**. optio plants fake browser shims on `PATH`
  + sets `BROWSER`; the agent's open runs the shim instead. Modes via
  `get_protocol(browser=…)`: `ignore` (no shims), `suppress` (silent no-op — the
  agent thinks it opened a browser, operator sees nothing; only for agents that
  degrade gracefully), or `redirect` (the shim writes `BROWSER: <url>` to
  `optio.log` → the tail parser surfaces it to the operator). **Default to
  `redirect` for any agent whose login opens a browser** — `suppress` hides login
  URLs and strands the operator.
- **Client-side open** (the agent's own web UI calls `window.open`, or it opens the
  browser from in-process code that doesn't shell out) — the process shim **cannot**
  catch it. Fall back to: the **iframe/web surface** where the operator sees and
  completes the flow directly, or a config flag that disables auto-open so you can
  surface the URL another way.

**The toolbox (compose as needed).**
1. **Seeds** (Stage 3) — pre-provision a logged-in identity so steady-state never
   logs in. The backbone: always back your login story with seeds.
2. **Interactive fallback mode** — let a human log in once in an iframe/ttyd/web
   surface, then capture that as a seed.
3. **OAuth-redirect rewrite** — `browser_url_rewrite` rewrites a loopback
   `/callback` redirect onto a hosted one so login completes remote/headless. Ref
   `optio-claudecode/…/oauth_redirect.py`.

**Verify for real — unit tests cannot prove login.** A fake agent never exercises
the actual OAuth/device/callback path. You MUST run the real first-login
end-to-end in the wrapper environment (local at minimum): confirm the browser-open
is intercepted, the flow completes, and a seed is captured with the expected
credential files. Test remote too if you claim remote login. This is one instance
of a general rule — see [Testing](#testing-fakes-for-logic-the-real-binary-for-truth):
no surface is "done" until the real binary has run it.

**Decision order.** API key → device-code → hosted-redirect OAuth → loopback OAuth
(local-only, or needs a rewrite). Whatever the agent forces on you, back it with
seeds so the common path is "already authenticated."

### Isolation & security posture
Default to fail-closed. Keep auth passwords off the argv (pass via file/env).
Provide multi-container tunnel-bind knobs where deployments split the worker and the
dashboard. Prefer conversation-mode + claustrum for the tightest posture.

### Testing: fakes for logic, the real binary for truth
Two layers, **both required**.

**Layer 1 — deterministic logic (necessary).** Both wrappers ship a **fake agent**
(a shim that speaks the agent's protocol without the real backend) plus a
**docker-sshd** harness for remote-mode tests. Copy this: it exercises the full
session pipeline — launch, log-protocol parse, deliverables, resume, seeds, the
conversation reducer — deterministically, local and remote, with no network or
credentials. Reference: `tests/` in either wrapper (`fake_claude.py`/`claude-shim.sh`,
`fake_opencode.py`/`opencode-shim.sh`).

**Layer 2 — real-binary end-to-end (the layer that actually proves it).** A green
fake-harness suite is **necessary but not sufficient.** A fake cannot reproduce the
real binary's process semantics — how it opens a browser, whether its sandbox needs
a controlling tty, how its OAuth callback behaves, where its installer really lands,
how its transport frames bytes. Each is a place the fake silently disagrees with
reality. **A surface is not "done" until the real agent binary has run it end-to-end
in the wrapper environment.** Declaring parity on fakes alone is exactly how
install-stub, browser-vanish, and sandbox-won't-start bugs ship "green" — the fake
passed because it doesn't do the thing the real binary does.

Run this checklist against the **real** binary before calling any surface complete:
- [ ] **iframe/ttyd** — launches, renders, accepts input, reaches `DONE`.
- [ ] **conversation** — the real `agent` transport handshakes, streams tokens,
      runs a tool, completes a turn.
- [ ] **each surface with fs-isolation ON** — the sandbox actually applies (fakes
      ignore `--sandbox`; a fail-closed sandbox silently refuses to start here).
- [ ] **first-login** end-to-end (browser-intercept / device-code / API-key) →
      credential files land → **seed captured**.
- [ ] **seed replant** — a fresh task starts already-authenticated.
- [ ] **resume** — a relaunch picks up the prior session.
- [ ] **remote (SSH)** — at least one surface end-to-end (path / tty / callback
      assumptions that hold locally routinely break remote).

**Layer 3 — the real wire against the real reducer (catches tokenization/render
bugs).** A real-binary E2E that merely "completes a turn" still hides UI bugs if you
only assert it finished. The conversation reducer is a pure function, so the
highest-value cheap test is: **capture the real agent's conversation stream once** —
a fixture of its actual protocol events for a real turn, *including the messy parts*
(interleaved reasoning, tool calls, provider-specific control frames) — and **replay
that captured wire through the real reducer**, asserting the resulting `ChatState` is
what a human should see: one coalesced answer bubble, reasoning in its own rows,
`busy` cleared at turn-end. Fakes emit idealized events; only the real wire exposes
the reducer's real failure modes — e.g. a reasoning model that interleaves
thought-deltas with answer-deltas, which fragmented the answer into one bubble *per
token* until the reducer coalesced by turn id instead of tail position. Pair it with
a **full inbound-chain check** — real agent → the `Conversation` client → the
listener → SSE → the reducer/widget — which catches wiring bugs the reducer test
can't: wrong-view routing (a task rendered by the wrong engine's reducer), dropped or
duplicated events, or a self-response flood swamping the stream and evicting real
events from the replay buffer. Capture-and-replay is a permanent regression fixture;
the full-chain check can be an opt-in real-binary test. **Every one of these was a
real bug in a wrapper that had a green fake suite** — conversation-mode that wouldn't
launch, one-bubble-per-token, and a task silently routed to the wrong reducer.

Keep as many of these as feasible as **opt-in, skip-if-no-real-binary** tests (see
`test_sandbox_enforce.py` / `test_conversation_sandbox_enforce.py` in optio-grok) so
the guard is reproducible, not a one-off manual check that rots.

---

## Part 5 — Wiring: packaging, registration, and demo tasks

A wrapper isn't done until it is installable and demonstrated end-to-end. This part
is required for every wrapper.

### Packaging & registration
- **Package.** `packages/optio-<agent>/` with its own `pyproject.toml` (setuptools,
  `src/` layout), depending on `optio-core` / `optio-host` / `optio-agents`. Mirror
  either reference wrapper's `pyproject.toml`.
- **Editable install + release lists.** Add the package to the demo's install list
  and the repo's release list so it is built and shipped. **Reference.** the
  `LOCAL_PKGS` / `install -e` lines in `packages/optio-demo/Makefile`, the
  `RELEASABLE_PY` list in the root `Makefile`, and the demo's `pyproject.toml`
  `dependencies`.

### Demo tasks (`optio-demo`)
**Goal.** Every wrapper ships demo tasks so it is exercised in the real dashboard,
not just in unit tests — and so the seed lifecycle is demonstrable. Both reference
wrappers ship the same set: one **seed-setup task** (log in / configure once, stop
to capture a reusable seed) and **two seed-pinned run tasks** — one **iframe** and
one **conversation** — that auto-appear after a seed exists and run unattended
against the captured identity. Keep this trio.

**Interface to implement.** Expose `async def get_tasks(services) -> list[TaskInstance]`
in `optio_demo/tasks/<agent>.py` (services gives `db`, `prefix`, and the framework
handle), then aggregate it in `optio_demo/tasks/__init__.py`'s
`get_task_definitions`. Build each task with your `create_<agent>_task(...)` factory.

**Reference.** `packages/optio-demo/src/optio_demo/tasks/claudecode.py` and
`…/opencode.py` (the seed-setup + seed-pinned task pair, and the
`_make_on_seed_saved` capture wiring); aggregation in `…/tasks/__init__.py`.

**Parity note.** The two seed-pinned demos exercise both surfaces from the same
captured identity: the iframe/ttyd demo ships at Stage 0, the conversation demo once
Stage 6 lands. Both reference wrappers ship this iframe + conversation pair — mirror
it.

---

## Appendix A — Parity checklist

A finished wrapper covers this surface. `req` = expected for any wrapper;
`opt` = depends on the agent / deployment. Pointer column: read the reference impl.

| # | Capability | Req/Opt | Reference (both wrappers unless noted) |
|---|---|---|---|
| 1 | iframe mode (embed agent web UI or ttyd TUI) | opt | `session.py` iframe branch; claudecode ttyd, opencode SPA |
| 2 | conversation mode (live `Conversation`) | req | `conversation.py`, `session.py` conversation branch |
| 3 | conversation-ui widget | req | `optio-conversation-ui/src/<engine>/` |
| 4 | `optio.log` keyword protocol | req | driver + `prompt.py` |
| 5 | local + remote(SSH) | req | `host_actions.build_host` |
| 6 | readiness detection + monitoring + teardown | req | `session.py` |
| 7 | resume / snapshots | opt | `snapshots.py`, `_capture_snapshot` |
| 7b | resume awareness: `resume.log` doc (pull) **+** pushed `RESUME_NOTICE` on relaunch, every mode | req if resume | `prompt.py`; `build_resume_notice_args` + conversation `RESUME_NOTICE` send |
| 8 | at-rest encryption of session blob | opt | `session_blob_encrypt`/`decrypt` |
| 9 | crash-orphan rescue | opt | claudecode `_rescue_orphan_if_present` |
| 10 | auto-resume-on-restart | opt | optio-core; `auto_resume` |
| 11 | seeds (logged-in fresh start) | req* | `seed_manifest.py`, `optio-agents/seeds.py` |
| 12 | pool / leases | opt | `optio-agents/seeds.py` lease fns |
| 13 | credential save-back (rotating tokens) | opt | `cred_watcher.py` |
| 14 | verify / refresh seed (host-free) | opt | `verify.py` / `oauth.py` |
| 15 | binary cache (evictable, unsnapshotted) + auto-install on miss + symlink into task path | req | `_resolve_install_dir` / `ensure_<agent>_installed` (vendor installer) |
| 16 | HOME/XDG per-task isolation | req | `_isolation_env` / launch env |
| 17 | hooks (before/after execute, on_deliverable, …) | req | config fields; `HookContext` |
| 18 | prompt composition from SSOT | req | `prompt.py`, `optio-agents/protocol/prompt.py` |
| 19 | permission gating | opt | `conversation.py`, `permission_gate` |
| 20 | model switching | opt | claudecode restart / opencode inline |
| 21 | file upload | opt | listener/server upload path |
| 22 | file download (`optio-file:`) | opt | listener/server download path |
| 23 | tool verbosity | opt | `tool_verbosity` → widgetData |
| 24 | session restore / rebase (scripted) | opt | claudecode `transcript.py` |
| 25 | filesystem isolation (Landlock) | opt | claudecode `fs_allowlist.py` |
| 26 | browser handling (ignore/suppress/redirect) | req | `get_protocol(browser=…)` |
| 27 | headless-login strategy | req* | seeds / interactive fallback / redirect rewrite |
| 28 | packaging + editable/release registration | req | `optio-demo/Makefile`, root `Makefile` `RELEASABLE_PY` |
| 29 | demo tasks: seed-setup + two seed-pinned (iframe & conversation) | req | `optio-demo/…/tasks/{claudecode,opencode}.py` |
| 30 | **real-binary E2E of every surface** (not just the fake harness) | req | [Testing checklist](#testing-fakes-for-logic-the-real-binary-for-truth); `test_*_sandbox_enforce.py` |

`*` req when the agent needs auth (all real agents do).

**A wrapper is not "full-featured" until row 30 passes for every surface it ships.**
A green fake-harness suite covers the other rows' *logic*; it does not prove the
real binary runs. Do not declare parity on fakes alone — that is how this project
shipped an install stub, a vanishing browser-open, and a conversation mode that
died on first real launch, all with a green suite.

## Appendix B — Interface reference (contract symbol → file)

| Symbol | File |
|---|---|
| `run_log_protocol_session` | `optio-agents/…/protocol/session.py` |
| `parse_log_line`, `LogEvent` union | `optio-agents/…/protocol/parser.py` |
| `ProtocolFeatures` | `optio-agents/…/protocol/features.py` |
| `get_protocol`, `Protocol` | `optio-agents/…/protocol/protocol.py` |
| `build_log_channel_prompt`, `RESUME_NOTICE` | `optio-agents/…/protocol/prompt.py` |
| `Conversation`, `PermissionRequest`, `PermissionDecision`, `ConversationClosed` | `optio-agents/…/conversation.py` |
| `HookContext`, `HookContextProtocol` | `optio-agents/…/context.py` |
| seed/lease engine (`capture_seed`, `plant_seed`, `merge_seed`, `refresh_seed`, `acquire`, `renew_lease`, `release`, `SeedManifest`) | `optio-agents/…/seeds.py` |
| `ChatItem`, `ChatState` | `optio-conversation-ui/src/chat.ts` |
| `ConversationViewProps` | `optio-conversation-ui/src/ConversationView.tsx` |
| protocol dispatch | `optio-conversation-ui/src/ConversationWidget.tsx` |

## Appendix C — Engine divergence table

Same capability, different mechanism — the range of valid implementations.

| Capability | claudecode | opencode |
|---|---|---|
| conversation transport | stream-json over stdio | HTTP + SSE (client of the spawned server) |
| embedded UI | tmux + ttyd (TUI) | `opencode web` SPA |
| conversation-ui data source | per-task optio-side listener | direct client of the opencode server |
| model switch | restart with `--continue` + new `--model` | inline, per prompt |
| seed consume transform | rekey `.claude.json` projects to workdir | no cwd rekey |
| resume identity | `--continue` (agent self-resolves) | export/import session db |
| filesystem isolation | claustrum / Landlock | not yet |
| browser mode | redirect (surface login URL) | suppress |

## Appendix D — Task configuration surface

Every wrapper's `TaskConfig` (a dataclass in its `types.py`) is what a caller sets
per task. Accept and handle at least the parameters below — they are the
engine-neutral contract; the reference `types.py` in either wrapper is the
canonical field list, and new wrappers should mirror the field names for
cross-engine consistency. **Validate them in `__post_init__`** (reject
out-of-range enums, and cross-field constraints — e.g. `conversation_ui` requires
`mode="conversation"`).

**Surface & task shape**
| Parameter | Meaning / gotcha |
|---|---|
| `consumer_instructions` | The task/prompt text composed into the agent's instructions file (`AGENTS.md`/`CLAUDE.md`). Empty = pure chat. |
| `mode` | `iframe` (ttyd TUI / web SPA) vs `conversation` (headless live `Conversation`). |
| `conversation_ui` | Publish the dashboard chat widget. Requires `mode="conversation"`. |
| `host_protocol` | Run the `optio.log` keyword protocol (`DONE`/`DELIVERABLE`/`BROWSER`/`ATTENTION`) alongside the surface. Off for a pure chat. |
| `auto_start` | Kick off the first turn unattended. **Default `False`** — a conversation/chat task must NOT auto-fire, or the agent runs a kickoff prompt and blocks the operator's first message (parity: claudecode/opencode default `False`). |

**Identity, resume, transport**
| Parameter | Meaning |
|---|---|
| `seed_id` | Replant a logged-in identity (str, or a lease-acquiring `SeedProvider`). |
| `supports_resume` | Allow relaunch to pick up the prior session (snapshots). |
| `ssh` | Remote host config; `None` = local. |

**Conversation-ui rendering** (only meaningful with `conversation_ui=True`)
| Parameter | Meaning |
|---|---|
| `tool_verbosity` | `silent` / `description-only` / `verbose` — tool-call detail. |
| `thinking_verbosity` | `hidden` / `visible` — reasoning/thinking traces (e.g. a reasoning model's thought stream). **Default `hidden`** (thinking is noisy); rendered as a distinct reasoning row, never the System style. Visibility is a *task* option — the UI never decides. |
| `show_model_selector` / `default_model` | Model picker in the widget + its initial value. |
| `show_file_upload` / `max_upload_bytes` | Operator → workdir file upload. |
| `file_download` / `max_download_bytes` | Agent → operator download (the `optio-file:` sentinel). |

**Permissions**
| Parameter | Meaning |
|---|---|
| `permission_gate` | Surface tool-approval to the operator (the conversation gate). |
| `permission_mode` | The agent's own permission mode (e.g. `bypassPermissions`, `acceptEdits`). |

**Model / effort**
| Parameter | Meaning |
|---|---|
| `model`, `effort` / `reasoning_effort` | Passed to the agent CLI. |

**Isolation & provisioning**
| Parameter | Meaning |
|---|---|
| `fs_isolation` | Kernel-enforced sandbox. **Default on**, fail-closed. |
| `extra_allowed_dirs` | `AllowedDir(path, "ro"\|"rw")` grants beyond the workdir. |
| `scrub_env` | Env vars to strip from the launch. |
| install-dir overrides / `install_if_missing` | Binary-cache location + auto-install toggle (Stage 5). |

**Hooks** — `before_execute` / `after_execute`, `on_deliverable`, `on_seed_saved`,
and the readiness/teardown callbacks; see `HookContext` and the config fields in
either reference `types.py`.

New rendering options (like `thinking_verbosity`) are a four-touch change: the
config field (+ validation), forward it in `set_widget_data(...)` as
`widgetData.<camelCase>`, a `ConversationViewProps` field the shared view reads,
and each engine view passing it through. Ship the config field to **all** engines
for parity even if only one currently emits the underlying events.
