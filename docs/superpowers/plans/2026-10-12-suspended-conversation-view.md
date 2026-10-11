# Suspended conversation view Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a suspended claudecode conversation task's process page shows its conversation read-only, with Resume. The engine decrypts and serves it over HTTP, and optio-api proxies it.

**Architecture:**
- **optio-core:** an engine-wide aiohttp archive listener publishes its address in MongoDB, then serves `GET /conversation/{_id}` through a per-agent `TaskInstance.read_conversation` hook.
- **optio-claudecode:** sets the hook.
- **optio-api:** proxies `GET /processes/:id/conversation` after `gateProcess(…, 'widget')`.
- **optio-ui:** renders a widget's registered "suspended" variant for suspended processes.
- **optio-conversation-ui:** provides that variant, replaying the record through the claudecode reducer into a read-only `ConversationView`.

**Tech Stack:** Python 3.11+ (aiohttp, motor), TypeScript (ts-rest, Fastify/Express/Next adapters, React 19, react-query, vitest).

**Spec:** `docs/2026-10-12-suspended-conversation-view-design.md`. Read it first; its sections define every name, status code and string used below.

## Global Constraints

- **Worktree:** `/home/csillag/chat/wt-suspended/optio`, branch `csillag/suspended-view`. Never push. Never touch `~/deai`, `~/wt` or excavator paths.
- **Python tests:** `cd packages/<pkg> && MONGO_URL=mongodb://localhost:27317 REDIS_URL=redis://localhost:6679 ../../.venv/bin/pytest <files> -q`. These are private containers; never use port 27017 or the `resurrect-*` containers.
- **TS tests:** `cd packages/<pkg> && ./node_modules/.bin/vitest run <files>` and `./node_modules/.bin/tsc --noEmit`. Workspace deps are built; re-run `pnpm --filter "<pkg>^..." build` at the repo root if a dependency's `dist` is missing or stale.
- **Test scope:** focused (the touched and closely related files). The controller runs the full suites once before the final review.
- **Commits:** NO Co-Authored-By and NO self-credit lines (optio AGENTS.md). Prefixes `feat(optio-core|optio-claudecode|optio-api|optio-contracts|optio-ui|optio-conversation-ui): …`.
- **Exact values from the spec:**
  - collection `{prefix}_engine_endpoints`, document `_id: "conversationArchive"`, fields `url`, `password`, `updatedAt`;
  - listener route `GET /conversation/{processId}` (the process document `_id`), basic-auth user `optio`;
  - reasons `not-found`, `not-suspended`, `no-conversation-support`, `no-conversation`, `read-failed`, `engine-unavailable`;
  - 200 body `{events, capturedAt}`; header `Cache-Control: no-store`;
  - optio-api route `GET /processes/:id/conversation` (contract key `getConversation`), access action `'widget'`, endpoint cache 30 s, upstream timeout 15 s;
  - `closedLabel` text `Suspended — resume to continue`;
  - other-protocol note `Resume to view this conversation.`;
  - no-record text `No saved conversation to show; resume to continue it.`
- **Logs:** never log conversation content or the password; only process ids, reasons and exception types.
- **Coordination:** another session's unmerged branches touch `optio-conversation-ui/src/ConversationView.tsx`. Limit edits there to the `closedLabel` prop.

## Review Focus

1. **The engine has restarted since the endpoint was published** (stale document, connection refused). optio-api answers 503 `engine-unavailable` quickly, and the next request after the engine republishes works (cache dropped on a connection error). Test in Task 3.
2. **The process resumes while the suspended view is open.** The listener returns 409 `not-suspended`, the widget renders nothing, and the live widget takes over. No crash, no stale replay. Tests in Tasks 1 and 5.
3. **A hook raises or decryption fails** (wrong key after a rotation): 500 `read-failed`, logged without content; the UI shows the message plus Retry. Tests in Tasks 1 and 5.
4. **Access:** a user without widget access to the process gets 403/404 from optio-api, before any engine call. Test in Task 3.
5. **Live behaviour unchanged** for running processes, and for terminal processes whose widget has no suspended variant (e.g. iframe widgets). Test in Task 4.

---

### Task 1: optio-core, the conversation archive listener

**Files:**
- Create: `packages/optio-core/src/optio_core/conversation_archive.py`
- Modify:
  - `packages/optio-core/src/optio_core/models.py` (`TaskInstance.read_conversation`, last field);
  - `packages/optio-core/src/optio_core/lifecycle.py`:
    - factor the detached-ctx construction out of `_run_resurrect` (~590-604) into `_detached_context(proc) -> ProcessContext`;
    - start the listener in `run()` before the RPC server, stop it in shutdown;
  - `packages/optio-core/src/optio_core/__init__.py` (export `ConversationArchiveListener` if useful to apps' tests);
  - `packages/optio-core/AGENTS.md`.
- Test: `packages/optio-core/tests/test_conversation_archive.py`

**Interfaces:**
- Produces:
  - `TaskInstance.read_conversation: Callable[[ProcessContext], Awaitable[list | None]] | None = None`;
  - `ConversationArchiveListener(*, db, prefix, resolve_task: Callable[[str], TaskInstance | None], make_context: Callable[[dict], ProcessContext])` with `async start() -> None`, which binds and publishes, and `async stop() -> None`, which unpublishes and closes. Properties `url` and `password`.
  - The endpoint document and route per the spec §1.

- [ ] **Step 1: Write failing tests** in `tests/test_conversation_archive.py`. Use a real aiohttp server on the listener and real Mongo; craft process docs directly; use `aiohttp.ClientSession` with and without auth:
  - `test_publishes_and_unpublishes_endpoint`: after `start()`, `{prefix}_engine_endpoints` has `{_id:"conversationArchive", url, password, updatedAt}`, and the url answers. After `stop()` the doc is gone.
  - `test_requires_basic_auth`: 401 without credentials and with a wrong password.
  - `test_unknown_process_404`: `reason == "not-found"`.
  - `test_not_suspended_409`, parametrized: running; cancelled without `hasSavedState`; done with saved state. Each gives `reason == "not-suspended"`.
  - `test_no_task_or_hook_404`: `no-conversation-support` for no registry entry, and for a task whose `read_conversation is None`.
  - `test_hook_none_404`: the hook returns None, giving `no-conversation`.
  - `test_returns_events_no_store`: the hook returns `[[1, {"type": "user"}]]`. The response is 200 `{"events": [[1, {"type": "user"}]], "capturedAt": ...}`, and the header is `Cache-Control: no-store`.
  - `test_hook_raises_500_without_content_in_logs`: the hook raises `ValueError("SECRET-CONTENT")`. The response is 500 `read-failed`, and caplog contains the process id but not `SECRET-CONTENT`.
  - `test_detached_context_shared_with_resurrect`: `Optio._detached_context` exists, and `_run_resurrect` uses it (a call spy, or assert the resurrect tests still pass unchanged).
  - `test_run_starts_and_shutdown_stops_listener`: a minimal Optio `init` + `run` lifecycle (follow the existing lifecycle tests) publishes the endpoint, and shutdown removes it.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement** per spec §1. `capturedAt`: the hook returns events only, so read the latest snapshot's `capturedAt` through an optional second hook result. Simplest is to let the hook return `{"events": [...], "capturedAt": iso | None}` OR a bare list. Implement: if the hook returns a dict, use its `events` and `capturedAt`; if a list, `capturedAt = None`. Document this in the field's docstring.
- [ ] **Step 4: Run the new file plus `tests/test_resurrect*.py` and `tests/test_deferred_migrations.py`.**
- [ ] **Step 5: Commit** `feat(optio-core): engine-wide conversation archive listener (read a suspended task's record over HTTP)`.

### Task 2: optio-claudecode, the read hook

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py` (a `_read_conversation` helper next to `_load_conversation_buffer`; `create_claudecode_task` sets `read_conversation` only when `config.conversation_ui`).
- Modify: `packages/optio-claudecode/AGENTS.md` (one line).
- Test: `packages/optio-claudecode/tests/test_conversation_record.py` (extend).

**Interfaces:**
- Consumes: `TaskInstance.read_conversation` (Task 1); `_load_conversation_buffer(ctx, *, decrypt)`; `load_latest_snapshot`.
- Produces: `async def _read_conversation(ctx, *, decrypt) -> dict | None`, returning `{"events": [[seq, event], ...], "capturedAt": <iso of the latest snapshot>}`, or None when there is no record.

- [ ] **Step 1: Write failing tests:**
  - `test_read_hook_set_only_in_conversation_mode`;
  - `test_read_hook_returns_decrypted_events_and_captured_at`: an encrypted record; the entries come back as lists, and `capturedAt` equals the latest snapshot's;
  - `test_read_hook_none_without_record`.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_conversation_record.py`.
- [ ] **Step 5: Commit** `feat(optio-claudecode): read_conversation hook for suspended-view reads`.

### Task 3: optio-contracts and optio-api, the proxy route

**Files:**
- Modify:
  - `packages/optio-contracts/src/api-to-frontend.ts` (`getConversation`, per spec §3; reuse the `{reason, message}` error schema pattern);
  - `packages/optio-api/src/handlers.ts` (`getProcessConversation`);
  - a new `packages/optio-api/src/conversation-archive.ts` (endpoint resolution + cache + fetch);
  - `packages/optio-api/src/adapters/{fastify,express,nextjs-app,nextjs-pages}.ts`;
  - `packages/optio-api/src/index.ts`;
  - `packages/optio-api/AGENTS.md`, README route list.
- Test: `packages/optio-api/src/__tests__/conversation-archive.test.ts` (new), plus adapter wiring tests where other routes have them.

**Interfaces:**
- Consumes: the endpoint document and listener contract (Task 1).
- Produces:
  - `getProcessConversation(ctx, query, id, access): Promise<{status, body}>`;
  - `resolveConversationArchive(db, prefix): Promise<{url, password} | null>` (30 s positive cache, invalidated on a connection error);
  - typed client route `api.processes.getConversation`.

- [ ] **Step 1: Write failing tests.** Use a local HTTP server as a fake listener and a real or in-memory Mongo, as the existing optio-api tests do:
  - `gates with 'widget' before any upstream call`: an authorize returning false gives 403, and the fake upstream is not hit.
  - `503 engine-unavailable when no endpoint doc`.
  - `503 on connection refused; next call re-resolves`: drop the cache, republish a new port, and the call succeeds (Review Focus 1).
  - `passes status and body through and sends basic auth`, for each of 200/404/409/500.
  - `sets Cache-Control: no-store`.
  - Adapter wiring for each of the 4 adapters: a request reaches the handler.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.** Use `fetch` with `AbortSignal.timeout(15000)`. Never log the password or the body.
- [ ] **Step 4: Run** the new test file, the adapter tests and `tsc --noEmit` for optio-contracts and optio-api.
- [ ] **Step 5: Commit** `feat(optio-api): GET /processes/:id/conversation proxies the engine's conversation archive`.

### Task 4: optio-ui, suspended widgets

**Files:**
- Modify:
  - `packages/optio-ui/src/process-state.ts` (`isSuspended`);
  - `src/widgets/registry.ts` (an `options.suspended` variant, `getSuspendedWidget`);
  - `src/components/ProcessWidget.tsx` (`useProcessWidget` returns the suspended variant and `mode`);
  - `src/components/ProcessDetailView.tsx` (suspended layout with `LaunchControls`);
  - a new hook `src/hooks/useProcessConversation.ts`;
  - `src/index.ts`, README, AGENTS.md.
- Test: the corresponding `__tests__` files (new or extended).

**Interfaces:**
- Consumes: `api.processes.getConversation` (Task 3).
- Produces:
  - `isSuspended(p): boolean`;
  - `registerWidget(name, component, options?: { suspended?: ComponentType<WidgetProps> })`;
  - `getSuspendedWidget(name)`;
  - `useProcessWidget(...)` returning `{ ..., mode: 'live' | 'suspended' }` (same props for both modes);
  - `useProcessConversation(processId: string, opts?: { enabled?: boolean })` returning the react-query result (data `{events, capturedAt}`; error with `reason`).

- [ ] **Step 1: Write failing tests:**
  - `isSuspended` truth table;
  - `useProcessWidget` returns the suspended variant only when not live, suspended, `widgetData` is set and a variant is registered;
  - it returns null for terminal processes without a variant (Review Focus 5);
  - live behaviour is unchanged;
  - `ProcessDetailView` shows the suspended widget and the Resume control for a suspended process;
  - `useProcessConversation` does not retry on 4xx.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the touched test files + `tsc --noEmit`.
- [ ] **Step 5: Commit** `feat(optio-ui): suspended widget variants and useProcessConversation`.

### Task 5: optio-conversation-ui, the suspended conversation widget

**Files:**
- Create: `packages/optio-conversation-ui/src/SuspendedConversationWidget.tsx`, `src/claudecode/ClaudeCodeReplayView.tsx`.
- Modify:
  - `src/ConversationView.tsx` (the `closedLabel?: string` prop only);
  - `src/ConversationWidget.tsx` (or wherever `registerWidget('conversation', …)` lives: add `{ suspended: SuspendedConversationWidget }`);
  - `src/index.ts`, README.
- Test: `src/__tests__/suspendedConversation.test.tsx` (new).

**Interfaces:**
- Consumes: `useProcessConversation`, `WidgetProps`, `registerWidget` options (Task 4); `reduceEvent`, `initialChatState` (claudecode).
- Produces: exported `SuspendedConversationWidget(props: WidgetProps)` and `ClaudeCodeReplayView({ process, widgetData, events, capturedAt })`.

- [ ] **Step 1: Write failing tests.** Mock `useProcessConversation`:
  - a fixture record (user message, assistant text, a finished tool row) renders those messages; the divider reads `Suspended — resume to continue`; the composer is disabled; no EventSource is created.
  - `closedLabel` absent: the default "conversation ended" text is unchanged.
  - protocol `grok` shows `Resume to view this conversation.`
  - `no-conversation` shows `No saved conversation to show; resume to continue it.`
  - `not-suspended` renders nothing (Review Focus 2).
  - A 500 shows the message and a Retry that refetches (Review Focus 3).
  - Loading shows the spinner slot / "Loading…".
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.** Fold with a fixed `now` (from `capturedAt`), then the synthetic `x-optio-closed` at `maxSeq+1`. Pass no-op callbacks, no uploads/downloads/controls, and the verbosity settings from `widgetData`. Wrap in `SpinnerScope name={"optio:process:" + process._id}`.
- [ ] **Step 4: Run** the new test file, the existing conversation-ui tests that touch `ConversationView` (`closedLabel` default) and `tsc --noEmit`.
- [ ] **Step 5: Commit** `feat(optio-conversation-ui): read-only replay of a suspended conversation`.
