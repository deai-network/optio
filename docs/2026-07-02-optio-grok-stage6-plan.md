# optio-grok Stage 6 (Conversation mode + Conversation UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Add conversation mode to `optio-grok`: a live `Conversation` object driving `grok agent stdio` (ACP), plus the dashboard chat widget rendering it.

**Architecture:** Grok's headless conversation transport is **ACP (Agent Client Protocol) — JSON-RPC 2.0 over stdio** (`grok agent stdio`). `GrokConversation` is a minimal ACP client implementing the `optio_agents.conversation.Conversation` protocol (structurally like `ClaudeCodeConversation`, but ACP framing instead of claude stream-json). The UI mirrors claudecode's pattern: a per-task optio-side SSE listener exposing the raw ACP `session/update` stream + a grok reducer (`optio-conversation-ui/src/grok/`) mapping ACP updates → the normalized `ChatItem` model.

**Tech Stack:** Python (asyncio subprocess, JSON-RPC), aiohttp SSE listener, TypeScript (React reducer + view).

## Global Constraints

- Branch `csillag/optio-grok`. Reference = `optio-claudecode` (`conversation.py`, `conversation_listener.py`, the `_conversation_body`/publish_result branch of `session.py`, its conversation config fields) and `optio-conversation-ui/src/claudecode/` (reducer + view + `ConversationWidget` dispatch). Grok's ACP details from `~/.grok/docs/user-guide/15-agent-mode.md`.
- **ACP wire facts (pin by LIVE PROBE before coding — see Task 0):** requests `initialize`, `session/new {cwd, mcpServers:[]}` → `{sessionId}`, `session/prompt {sessionId, prompt:[{type:"text", text}]}`. Notifications `session/update` with `update.sessionUpdate` ∈ {`agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `tool_call_update`, `plan`}. Permission = an agent→client JSON-RPC **request** (confirm exact method, likely `session/request_permission`) the client must answer. Cancel/interrupt method (confirm, likely `session/cancel`). Options go before the mode: `grok agent --model <m> [--always-approve] stdio`.
- `Conversation` contract (implement all): `send`, `on_event` (fan out raw ACP messages untouched), `on_message` (one final answer text per turn), `on_permission_request`, `is_pending`, `interrupt`, `close`, `closed`. Synthetic events use the `x-optio-` type prefix.
- Conversation mode: NO tmux/ttyd. `grok agent stdio` is the subprocess; `GrokConversation` wraps its stdin/stdout; `ctx.publish_result(conversation)`.
- `--no-leader`/isolation env still applies (reuse `_isolation_env`). `host_protocol` toggle governs the optio.log keyword channel (a conversation task may set it False).
- Tests use a **fake ACP grok** (extend `fake_grok.py` with an `agent stdio` JSON-RPC mode) — no real binary/network. But Task 0's live probe (one real `grok agent stdio` session) is required to pin the wire.
- Every task: failing test first, minimal impl, commit.

---

## Group 6a — Python: ACP conversation

### Task 0: Live ACP probe (pin the wire)
- [ ] Spawn `grok agent stdio` (with the isolation env + a scratch GROK_HOME already authed on this host), send `initialize` → `session/new` → a `session/prompt` whose prompt forces a tool that needs approval (e.g. "run `echo hi` in the shell"). Capture the full JSON-RPC exchange to a scratch file. Record: exact permission request method + params shape + how to answer (allow/deny result), the cancel method, the `end`/turn-completion signal (does `session/prompt` return a result at turn end?), and the `session/update` payload shapes. Write these findings as a comment block at the top of `conversation.py`. (No commit — this is research feeding Tasks 1-2.)

### Task 1: `GrokConversation` (ACP stdio client)
**Files:** Create `src/optio_grok/conversation.py`; extend `tests/fake_grok.py` (ACP mode); Test `tests/test_conversation.py`

**Interfaces:** `class GrokConversation` implementing `optio_agents.conversation.Conversation`, constructed with the subprocess stdio streams (+ cwd). A `run_reader()` task consumes stdout JSON-RPC lines and routes: notifications → `on_event` + accumulate `agent_message_chunk` per turn; agent→client permission request → `_answer_permission` (build `PermissionRequest`, await handler → JSON-RPC result); turn end → `on_message`. `send()` writes `session/prompt`. `interrupt()` writes the cancel method. `close()` cooperative.

- [ ] **Step 1:** Failing test: drive a fake-ACP-grok — `send("say PONG")`, assert `on_message` fires with the text; assert `on_event` saw ≥1 `agent_message_chunk`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt `ClaudeCodeConversation` structure; ACP JSON-RPC framing). Extend `fake_grok.py` with an `agent stdio` ACP responder (initialize/session.new/session.prompt→chunks+end; a tool+permission scenario).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-grok): GrokConversation ACP stdio client (Stage 6)`.

### Task 2: Conversation mode in `session.py` + `types.py`
**Files:** Modify `src/optio_grok/session.py`, `types.py`; Test `tests/test_session_conversation.py`

**Interfaces:**
- `types.py`: `mode` gains `"conversation"`; add `permission_gate: bool = False`, `conversation_ui: bool = False`, `tool_verbosity: ToolVerbosity = "description-only"`; loosen `host_protocol` validation (may be False only in conversation mode). Mirror claudecode `__post_init__` conversation validations.
- `session.py`: `_conversation_body` launches `grok agent --model … stdio` (no tmux/ttyd), builds `GrokConversation`, `ctx.publish_result(conversation)`, waits on close/cancel; `body = _conversation_body if config.mode=="conversation" else _grok_body`; permission gate wires `conversation.on_permission_request` when `permission_gate`; `create_grok_task` ui_widget `"conversation"` when `conversation_ui` else per iframe.

- [ ] **Step 1:** Failing test: create a conversation-mode task; via the published `Conversation`, `send` a prompt and assert `on_message`; assert permission gate denies when configured. (Use fake ACP grok + `launch_and_await_result`/publish_result harness as claudecode's conversation test does.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt claudecode conversation branch; drop session-restore/model-restart — later).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-grok): conversation mode (publish live Conversation) (Stage 6)`.

---

## Group 6b — Listener + Conversation UI

### Task 3: `conversation_listener.py` (SSE) + conversation_ui wiring
**Files:** Create `src/optio_grok/conversation_listener.py`; modify `session.py`; Test `tests/test_conversation_listener.py`

**Interfaces:** aiohttp app (adapt claudecode): `GET /events` SSE with replay buffer + `Last-Event-ID`; `POST /send`, `/interrupt`, `/permission`. session.py conversation_ui branch: start listener subscribed to `conversation.on_event`, `set_widget_upstream`, `set_widget_data({"protocol":"grok","toolVerbosity":…, …})`, broadcast `x-optio-permission-answered`.

- [ ] **Step 1:** Failing test: listener replays buffered events to a late SSE subscriber; `POST /send` forwards to the conversation. (Adapt claudecode `test_conversation_listener`.)
- [ ] **Step 2-4:** RED → implement (adapt claudecode `conversation_listener.py`) → GREEN.
- [ ] **Step 5: Commit** `feat(optio-grok): conversation SSE listener + widget wiring (Stage 6)`.

### Task 4: `optio-conversation-ui/src/grok/` reducer + view
**Files:** Create `packages/optio-conversation-ui/src/grok/events.ts`, `src/grok/GrokView.tsx`; modify `src/ConversationWidget.tsx` (dispatch `protocol==="grok"`); export from `src/index.ts`; Test `src/__tests__/grok-events.test.ts`

**Interfaces:** `reduceGrokEvent(state, rawEvent, seq) → ChatState` mapping ACP `session/update`:
- `agent_message_chunk` → `{kind:'assistant', text (cumulative), pending}` (msgId per turn)
- `agent_thought_chunk` → muted `{kind:'activity'}` (or fold into assistant-pending; match claudecode's thinking treatment) 
- `tool_call` → `{kind:'tool', name:title, input}`; `tool_call_update` → update/replace
- permission request → `{kind:'permission', requestId, toolName, input, answered:null}`; `x-optio-permission-answered` → flip `answered`
- turn end → clear `busy`; `x-optio-closed` → `{kind:'closed'}`; error → `{kind:'error'}`
- synthetic `x-optio-local-user` → `{kind:'user', local:true}`
`GrokView.tsx`: SSE from `{widgetProxyUrl}events`, `useReducer(reduceGrokEvent)`, wire `ConversationViewProps` (`onSend`→POST /send, `onInterrupt`→/interrupt, `onPermission`→/permission), hand render to shared `ConversationView`.

- [ ] **Step 1:** Failing reducer test (Vitest/Jest per the package): feed a captured ACP update sequence, assert the resulting `ChatItem[]`.
- [ ] **Step 2-4:** RED → implement reducer + view + dispatch → GREEN. Typecheck with `node_modules/.bin/tsc --noEmit` (do NOT use npx).
- [ ] **Step 5: Commit** `feat(optio-conversation-ui): grok ACP reducer + view (Stage 6)`.

---

## Self-Review
- Spec Stage 6 (live Conversation + chat widget, `grok agent stdio`, `src/grok/` reducer+view, `--no-leader`) ↔ Tasks 0-4.
- Third transport (ACP JSON-RPC) correctly identified; `GrokConversation` structural-mirrors claudecode but frames ACP. Parity note (conversation-ui): grok joins claudecode/opencode as a third `protocol` — renderer + `ChatItem` model untouched, only a reducer+view added.
- Deferred to Stage 7: model switching, file up/down, richer permission verbs. Deferred: `agent serve`/WebSocket (stdio first), session-restore.
- Live-probe (Task 0) pins the ACP permission/cancel wire before coding — no guessing.
- No placeholders; tests + reference pointers per task; names consistent (`GrokConversation`, `reduceGrokEvent`, `GrokView`, `conversation_listener`).
