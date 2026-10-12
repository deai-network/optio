# Read-only conversation view for suspended agent tasks

Design, agreed with the owner on 2026-10-12. This is step 2 of "read suspended agent tasks":
1. done: the conversation buffer left the plaintext workdir. See
   `docs/2026-10-11-conversation-buffer-encryption-design.md`. claudecode now keeps the recent part of
   a conversation as an encrypted `conversation` blob on its latest snapshot.
2. this step: show that record, read-only, for a suspended task.
3. later: the session/workdir split for the other engines, and records for the other six agents.

## What the user gets

A suspended agent task's process page shows its conversation as it stood when it was suspended, with
input disabled and a Resume control. A suspended task is `cancelled` with `supportsResume` and
`hasSavedState`. It shows the recent part, the same last ~1000 events a resume replays.

Today that page shows only the task tree and the log. Resuming is not needed to read. No agent host
starts and no seed is taken.

## Decisions (owner, 2026-10-12)

1. **Decryption stays in the engine; no keys in the API.** In Excavator the record is encrypted to the
   instance key, which only the engine container mounts. Excavator's S-9 design keeps key material out
   of the internet-facing API.
2. **No RPC: the engine serves the record over HTTP, and optio-api proxies it.** A decrypted reply over
   the Redis-stream RPC would sit in Redis (reply streams keep about 1024 entries). This path is the
   same as the live conversation widget's:
   - an aiohttp listener in the engine;
   - an inner basic-auth password;
   - the address published in MongoDB;
   - optio-api proxying after the `'widget'` access check;
   - the same `OPTIO_WIDGET_TUNNEL_BIND` / `OPTIO_WIDGET_TUNNEL_HOST` network config.

   The plaintext exists only in transit and in memory, as with the live view.
3. **claudecode first.** The read path is agent-agnostic. Other agents show a "resume to view" note
   until their listeners write records (a follow-up).

## Design

### 1. optio-core: the conversation archive listener

- **What it is:** `optio_core.conversation_archive.ConversationArchiveListener`, one aiohttp server per
  engine.
  - It starts in `Optio.run()` before the RPC server and stops in shutdown.
  - It binds `OPTIO_WIDGET_TUNNEL_BIND` (default `127.0.0.1`) on an ephemeral port.
  - It generates a random inner password (`secrets.token_urlsafe(32)`), as the conversation listeners do.
- **Publication:** it writes `{_id: "conversationArchive", url: "http://<OPTIO_WIDGET_TUNNEL_HOST
  or 127.0.0.1>:<port>/", password, updatedAt}` to `{prefix}_engine_endpoints`. It deletes the document at
  shutdown. A crashed engine leaves a stale document, and the proxy then answers 503.
- **Route:** `GET /conversation/{processId}`, where `processId` is the process document's `_id` (an
  ObjectId string):
  1. Check basic auth `optio:<password>`; otherwise 401.
  2. Resolve the process document by `_id`; if missing, 404 `not-found`.
  3. If `isSuspended` is not true (state `cancelled`, `supportsResume`, `hasSavedState`), 409
     `not-suspended`.
  4. Look up `task = executor._task_registry.get(doc.processId)`. If there is no task, or the task has no
     `read_conversation`, 404 `no-conversation-support`.
  5. Call `events = await task.read_conversation(ctx)` with a detached `ProcessContext`. Factor out the
     construction `_run_resurrect` uses today into a shared helper. If it returns `None`, 404
     `no-conversation`. Otherwise 200 `{"events": [[seq, event], ...], "capturedAt": <ISO of the latest
     snapshot, or null>}` with `Cache-Control: no-store`.

  Errors are JSON `{"reason": <code>, "message": <text>}`. A hook that raises gives 500 `read-failed`,
  logged with the process id and the exception type only, never content.
- **`TaskInstance.read_conversation`:** an optional
  `Callable[[ProcessContext], Awaitable[list | None]] | None = None`, the last field.

### 2. optio-claudecode: the read hook

`create_claudecode_task` sets `read_conversation` (only when `config.conversation_ui`) to
`_read_conversation(ctx, decrypt=config.session_blob_decrypt)`, which reads **strictly**:

- **Absent record:** returns None, so the listener answers 404 `no-conversation`.
- **Unreadable record:** a decrypt error, bad JSON, or a GridFS or Mongo error raises, so the listener
  answers 500 `read-failed` and the UI offers Retry.
- **Otherwise:** it returns `{"events": [[seq, event], ...], "capturedAt": <ISO, +00:00>}`.

Decryption, parsing and filtering run in `asyncio.to_thread`. The parser `_parse_conversation_record`
is shared with resume, which stays lenient: a bad record means an empty buffer, never a failed
launch. A snapshot whose conversation blob is missing (a dangling reference) is `read-failed`.

### 3. optio-api: `GET /processes/:id/conversation`

- **Contract:** in `optio-contracts` `processesContract`, `getConversation`:
  - path `/processes/:id/conversation`, query `InstanceQuerySchema`;
  - 200 `{events: unknown[], capturedAt: string | null}`;
  - 401 / 403 / 404 / 409 / 503 / 500 with `{reason, message}`.
- **Handler:** `getProcessConversation(ctx, query, id, access)` in `handlers.ts`:
  1. `gateProcess(col, id, access, 'widget')`;
  2. resolve the endpoint from `{prefix}_engine_endpoints`, keyed by database and prefix. Cache it for
     30 s, and drop the cache entry on a connection error;
  3. `fetch(url + "conversation/" + id)` with the basic auth and a 15 s timeout;
  4. pass the status and body through. A missing endpoint or a connection failure gives 503
     `engine-unavailable`. Always send `Cache-Control: no-store`.
- **Adapters:** wire it into all four adapters (fastify, express, nextjs-app, nextjs-pages). Export it,
  and update the AGENTS.md / README route lists.
- **Writes:** none. optio-api only reads MongoDB, as today.

### 4. optio-ui

- **`isSuspended(process)`** in `process-state.ts`: `cancelled && supportsResume && hasSavedState`, with
  a test. AGENTS.md forbids inline state checks.
- **Widget registry:** `registerWidget(name, component, options?: { suspended?: ComponentType<WidgetProps> })`
  and `getSuspendedWidget(name)`. Existing registrations are unchanged.
- **`useProcessWidget`:** when the process is not widget-live, `isSuspended`, `widgetData` is set and a
  suspended variant is registered, it returns that variant with the same props. It also returns
  `mode: 'live' | 'suspended'`.
- **`useProcessConversation(processId, { enabled })`:** a react-query `useQuery` on
  `api.processes.getConversation`. It is keyed on the process id plus the latest status timestamp, so a
  new suspend refetches, and it never retries on 4xx.
- **`ProcessDetailView`:** in suspended mode it renders the suspended widget in the widget slot, with
  `LaunchControls` (Resume) visible. After a resume the process turns live, and the live widget takes
  over once `widgetData` is set again.

### 5. optio-conversation-ui

- **`ConversationView`:** a new optional `closedLabel?: string` prop replaces the hard-coded "conversation
  ended" divider text. The default is unchanged.
- **`SuspendedConversationWidget`** (`WidgetProps`):
  - it calls `useProcessConversation`;
  - it dispatches on `widgetData.protocol`, which survives the suspend: claudecode gets
    `ClaudeCodeReplayView`; other protocols get a short note, "Resume to view this conversation."
  - Loading uses the provider-aware spinner (the spinner slot) and the "Loading…" text.
  - Errors:
    - `no-conversation` / `no-conversation-support`: "No saved conversation to show; resume to continue
      it." (from i18n texts if the package has them, else English);
    - `not-suspended`: render nothing (a race: the process just resumed);
    - others: the message plus Retry.
- **`ClaudeCodeReplayView`** (`process`, `widgetData`, `events`):
  - Initial state is a lazy fold:
    `events.reduce((s, [seq, ev]) => reduceEvent(s, ev, seq, fixedNow), {...initialChatState})`, then a
    synthetic `{type: "x-optio-closed", reason: "suspended"}` at `maxSeq + 1`. `fixedNow` is the
    record's `capturedAt` (or `Date.now()` once), so relative times stay stable.
  - It renders `ConversationView` with:
    - `closed` and `closedLabel="Suspended — resume to continue"`;
    - no-op callbacks;
    - `showFileUpload=false`, `fileDownload=false`, no controls;
    - the verbosity settings from `widgetData` as the live view uses them.
  - It wraps itself in `SpinnerScope` like the live agent views.
- **Registration:** the conversation widget registers `{ suspended: SuspendedConversationWidget }`.
  Export both components for apps that mount the widget directly.

## Out of scope

- Records for grok, cursor, kimicode, codex, antigravity and opencode.
- Excavator's data-chat page, which is app-side and can mount `SuspendedConversationWidget`.
- Paging through a long record; the recent part is enough (owner).

## Tests

- **optio-core:**
  - the listener binds and publishes its endpoint document, and removes it at shutdown;
  - 401 without auth;
  - 404 for an unknown id;
  - 409 when not suspended;
  - 404 `no-conversation-support` when there is no task or no hook;
  - 404 `no-conversation` when the hook returns None;
  - 200 with events and `no-store`;
  - 500 `read-failed` when the hook raises, with no content in the logs;
  - the detached-ctx helper is shared with resurrect.
- **optio-claudecode:** the hook is set only in conversation mode; it returns the decrypted entries for
  an encrypted record; None without a record.
- **optio-api:**
  - 403 or 404 from `gateProcess`;
  - 503 when no endpoint exists or the connection is refused;
  - status and body passthrough;
  - basic auth sent;
  - `no-store` set;
  - each adapter wires the route (one test per adapter, as for other routes).
- **optio-ui:**
  - `isSuspended`;
  - `useProcessWidget` returns the suspended variant only when suspended, `widgetData` is set and a
    variant is registered;
  - live behaviour unchanged;
  - `ProcessDetailView` shows the suspended widget plus Resume.
- **optio-conversation-ui:**
  - the replay of a fixture record renders the messages read-only with the suspended label;
  - input is disabled;
  - other protocols show the note;
  - the error states;
  - `closedLabel` defaults unchanged.
- **Live:** on the test stack (DB copy `excavator_turks`), a suspended test2 analyzer conversation shows
  its history read-only, and Resume opens it live.
