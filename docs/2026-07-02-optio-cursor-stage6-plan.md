# optio-cursor Stage 6 (Conversation mode + Conversation UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Checkbox steps.

**Goal:** Add conversation mode to `optio-cursor`: a live `Conversation` object driving `cursor-agent acp` (ACP), plus the dashboard chat widget rendering it.

**Architecture:** Cursor's headless conversation transport is **ACP (Agent Client Protocol) — JSON-RPC 2.0 over stdio** (`cursor-agent acp`, hidden subcommand; handshake verified live on this host). This is the SAME protocol grok uses — `CursorConversation` adapts `GrokConversation` nearly verbatim, and the conversation-ui work is primarily a **refactor-the-seam**: extract grok's ACP reducer into a shared engine-neutral ACP module consumed by both grok and cursor views (single source of truth; no duplicated reducer).

**Tech Stack:** Python (asyncio subprocess, JSON-RPC), aiohttp SSE listener, TypeScript (React reducer + view).

## Global Constraints

- Branch `csillag/cursor`. Reference = `optio-grok` (`conversation.py`, `conversation_listener.py`, the conversation branch of `session.py`, conversation config fields) and `optio-conversation-ui/src/grok/` (ACP reducer + view + `ConversationWidget` dispatch).
- **ACP wire facts already verified on this host (unauthenticated):** `initialize` → `{protocolVersion:1, agentCapabilities:{loadSession:true, promptCapabilities:{image:true}, sessionCapabilities:{list:{}}}, authMethods:[{id:"cursor_login"}]}`. Methods present in the binary: `session/new`, `session/load`, `session/prompt`, `session/cancel`, `session/update`, `session/set_model`, `session/request_permission`, `authenticate`. Grok's live-pinned ACP shapes (see `optio-grok/src/optio_grok/conversation.py` header comment) are the working assumption for payload shapes — both agents implement the same public protocol.
- **Auth-gated live probe (Task 0):** a full prompt-cycle probe needs a logged-in cursor. If the host has no cursor auth (check `cursor-agent status`), SKIP the live probe, code against the grok-pinned shapes + the fake, and leave the runtime confirmation to the demo stage — record this as a tracked gap in the design doc §7. Do NOT block the stage on auth.
- `Conversation` contract (implement all): `send`, `on_event` (fan out raw ACP messages untouched), `on_message` (one final answer per turn), `on_permission_request`, `is_pending`, `interrupt`, `close`, `closed`. Synthetic events use the `x-optio-` prefix.
- Conversation mode: NO tmux/ttyd. `cursor-agent acp` is the subprocess (launched with `_isolation_env` + `--trust`-equivalent trust handling if ACP requires it — probe); `CursorConversation` wraps its stdin/stdout; `ctx.publish_result(conversation)`.
- Non-gated conversation: pass `--force` to auto-approve (cursor's analogue of grok's `--always-approve`; verify the flag is accepted by the `acp` subcommand — if not, answer `session/request_permission` allow-all client-side, which the capability seam permits).
- Tests use a **fake ACP cursor** (extend `fake_cursor.py` with an `acp` JSON-RPC mode — adapt grok's fake ACP responder) — no real binary/network.
- Every task: failing test first, minimal impl, commit (no Co-Authored-By).

---

## Group 6a — Python: ACP conversation

### Task 0: Live ACP probe (auth-gated; skip cleanly if not logged in)
- [ ] `cursor-agent status` on this host. If logged in: spawn `cursor-agent acp` (isolation env + scratch HOME seeded with the host auth.json), run `initialize` → `session/new` → `session/prompt` forcing a shell tool call; capture the exchange to a scratch file; record permission-request/cancel/turn-end/update shapes as a comment block at the top of `conversation.py`; diff them against grok's pinned shapes. If NOT logged in: copy grok's pinned-shape comment block, mark each shape `[grok-pinned, cursor runtime-unverified]`, and add the gap to the design doc §7. (No commit — research feeding Tasks 1-2.)

### Task 1: `CursorConversation` (ACP stdio client)
**Files:** Create `src/optio_cursor/conversation.py`; extend `tests/fake_cursor.py` (ACP mode); Test `tests/test_conversation.py`

**Interfaces:** `class CursorConversation` implementing `optio_agents.conversation.Conversation`, constructed with the subprocess stdio streams (+ cwd). Adapt `GrokConversation` (reader task routing notifications → `on_event` + chunk accumulation; agent→client permission request → `PermissionRequest` → handler → JSON-RPC result; `session/prompt` response = turn end → `on_message`; `send` = `session/prompt`; `interrupt` = `session/cancel`; cooperative `close`).

- [ ] **Step 1:** Failing test: drive a fake-ACP-cursor — `send("say PONG")`, assert `on_message` fires with the text; assert `on_event` saw ≥1 `agent_message_chunk`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt `GrokConversation`; keep any cursor-specific divergence found in Task 0 isolated + commented). Extend `fake_cursor.py` with the ACP responder (adapt grok's).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(optio-cursor): CursorConversation ACP stdio client (Stage 6)`.

### Task 2: Conversation mode in `session.py` + `types.py`
**Files:** Modify `src/optio_cursor/session.py`, `types.py`; Test `tests/test_session_conversation.py`

**Interfaces:**
- `types.py`: `mode` gains `"conversation"`; add `permission_gate: bool = False`, `conversation_ui: bool = False`, `tool_verbosity: ToolVerbosity = "description-only"`; loosen `host_protocol` validation (may be False only in conversation mode). Mirror grok's conversation validations.
- `session.py`: `_conversation_body` launches `cursor-agent [--model …] [--force] acp` (no tmux/ttyd; flags-before-subcommand order verified against `--help` — adjust if the acp subcommand rejects them), builds `CursorConversation`, `ctx.publish_result(conversation)`, waits on close/cancel; `body = _conversation_body if config.mode=="conversation" else _cursor_body`; permission gate wires `conversation.on_permission_request` when `permission_gate`; `create_cursor_task` ui_widget `"conversation"` when `conversation_ui` else iframe.

- [ ] **Step 1:** Failing test: create a conversation-mode task; via the published `Conversation`, `send` a prompt and assert `on_message`; assert permission gate denies when configured. (Fake ACP cursor + the same harness grok's conversation test uses.)
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement (adapt grok's conversation branch).
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(optio-cursor): conversation mode (publish live Conversation) (Stage 6)`.

---

## Group 6b — Listener + Conversation UI

### Task 3: `conversation_listener.py` (SSE) + conversation_ui wiring
**Files:** Create `src/optio_cursor/conversation_listener.py`; modify `session.py`; Test `tests/test_conversation_listener.py`

**Interfaces:** aiohttp app (adapt grok's): `GET /events` SSE with replay buffer + `Last-Event-ID`; `POST /send`, `/interrupt`, `/permission`. session.py conversation_ui branch: start listener subscribed to `conversation.on_event`, `set_widget_upstream`, `set_widget_data({"protocol":"cursor","toolVerbosity":…})`, broadcast `x-optio-permission-answered`.

- [ ] **Step 1:** Failing test: listener replays buffered events to a late SSE subscriber; `POST /send` forwards to the conversation. (Adapt grok `test_conversation_listener`.)
- [ ] **Step 2-4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `feat(optio-cursor): conversation SSE listener + widget wiring (Stage 6)`.

### Task 4: Shared ACP reducer refactor + `src/cursor/` view
**Files:** In `packages/optio-conversation-ui`: create `src/acp/events.ts` (extracted from `src/grok/events.ts` — the engine-neutral ACP reducer, parameterized only where engine-specific), refactor `src/grok/events.ts` to re-export/wrap it, create `src/cursor/events.ts` (thin wrapper) + `src/cursor/CursorView.tsx`; modify `src/ConversationWidget.tsx` (dispatch `protocol==="cursor"`); export from `src/index.ts`; Test `src/__tests__/cursor-events.test.ts` (+ grok tests must stay green unchanged).

**Interfaces:** `reduceAcpEvent(state, rawEvent, seq) → ChatState` (shared); `reduceCursorEvent` = the cursor binding. `CursorView.tsx`: SSE from `{widgetProxyUrl}events`, wire `ConversationViewProps` (`onSend`→POST /send, `onInterrupt`→/interrupt, `onPermission`→/permission), hand rendering to shared `ConversationView` — adapt `GrokView.tsx`.

- [ ] **Step 1:** Failing reducer test: feed the same ACP update sequence grok's test uses, assert equivalent `ChatItem[]` via `reduceCursorEvent`.
- [ ] **Step 2:** RED.
- [ ] **Step 3:** Extract shared `src/acp/events.ts` (pure move — grok tests must pass unmodified), add cursor binding + view + dispatch.
- [ ] **Step 4:** GREEN: `pnpm test` in the package + `node_modules/.bin/tsc --noEmit` (do NOT use npx).
- [ ] **Step 5: Commit** `feat(optio-conversation-ui): shared ACP reducer + cursor view (Stage 6)`.

---

## Self-Review
- Spec Stage 6 (live Conversation + chat widget over `cursor-agent acp`) ↔ Tasks 0-4.
- SSOT enforced: one ACP reducer serves grok + cursor (extract-don't-duplicate); grok behavior pinned by its existing tests staying green unmodified.
- Auth-gated live probe degrades gracefully to grok-pinned shapes + tracked gap — the stage is not blocked on operator login.
- Deferred to Stage 7: model switching, file up/down, richer permission verbs.
- No placeholders; tests + reference pointers per task; names consistent (`CursorConversation`, `reduceAcpEvent`, `reduceCursorEvent`, `CursorView`, `conversation_listener`).
