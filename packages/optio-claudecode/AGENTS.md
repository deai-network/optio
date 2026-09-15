# optio-claudecode — Agent Cheatsheet

Run Anthropic Claude Code as an optio task — local subprocess or remote
host via SSH — with the interactive TUI exposed in the dashboard via a
ttyd-served iframe.

Full design: `docs/2026-05-28-optio-claudecode-design.md`.

## Public API

```python
from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task

create_claudecode_task(
    process_id="my-task",
    name="My task",
    config=ClaudeCodeTaskConfig(
        consumer_instructions="...",
        credentials_json=...,        # opaque dict/bytes/str → ~/.claude/.credentials.json
        claude_config=...,           # dict, deep-merged into ~/.claude/settings.json
        env={"ANTHROPIC_BASE_URL": "..."},
        permission_mode=None,        # default | plan | acceptEdits | bypassPermissions
        allowed_tools=None,
        disallowed_tools=None,
        ssh=None,
        install_if_missing=True,
        install_ttyd_if_missing=True,
        claude_install_dir=None,     # default ~/.local/bin (per host)
        ttyd_install_dir=None,
        before_execute=None,
        after_execute=None,
        on_deliverable=None,
    ),
)
```

`TaskInstance` returned has `ui_widget="iframe"` and `supports_resume`
tracking the config field (defaults to `True`). Resume snapshots the
`<workdir>/home/.claude/` subtree (encryptable session blob) plus a plaintext
workdir blob; on resume the workdir is restored and `--continue` is appended to
claude's argv. See `docs/2026-05-29-optio-claudecode-resume-design.md`.

## ClaudeCodeTaskConfig field semantics

* `credentials_json` — opaque payload; planted at
  `<workdir>/home/.claude/.credentials.json` with mode 0600. dict →
  JSON-encoded; bytes → UTF-8 decoded verbatim; str → written
  verbatim.
* `claude_config` — deep-merged into
  `<workdir>/home/.claude/settings.json` via a post-seed
  read-modify-write (`host_actions.apply_claude_settings`): applied
  AFTER the seed (fresh) or restored snapshot (resume) on all paths, so
  the caller's keys win per key while every other key the seed/snapshot
  carried is preserved.
* `permission_mode` — forwarded verbatim to `claude
  --permission-mode`. Validation happens in `__post_init__`.
* `session_blob_encrypt/decrypt`, `seed_blob_encrypt/decrypt` —
  inherited from `optio_agents.config_types.BlobCryptoConfigMixin`
  (alongside the `ClaustrumConfigMixin` triad). Optional synchronous
  bytes→bytes transforms at GridFS write/read: the `session_blob` pair
  wraps this process's home/.claude session tar (ds-scoped key), the
  `seed_blob` pair wraps the shared pool-account SEED tar (pool-scoped
  key). Per pair both-or-neither (validated by `_validate_blob_crypto`
  in `__post_init__`); the seed pair falls back to the session pair
  when unset — seed ops read the transforms only through the mixin's
  `seed_encrypt` / `seed_decrypt` accessors.
* HOME isolation: every task sees `HOME=<workdir>/home` so concurrent
  tasks on one host never share `~/.claude/` state.

## Conversation mode

Full design: `docs/2026-06-10-claudecode-conversation-gate-design.md`.

Three config fields control it (all defaults preserve today's behavior):

* `mode: Literal["iframe", "conversation"] = "iframe"` —
  `"conversation"` runs claude headlessly (no tmux, no ttyd, no iframe
  widget) over its bidirectional stream-json stdio protocol and
  publishes a live `Conversation` object via `ctx.publish_result()`.
* `host_protocol: bool = True` — opt-out for the optio.log keyword
  channel (STATUS / DELIVERABLE / DONE / ERROR / …). With `False` the
  keyword docs are also omitted from the composed CLAUDE.md.
  `mode="iframe"` **requires** `host_protocol=True` (it is the only
  completion signal there); validated in `__post_init__`.
* `permission_gate: bool = False` — only valid with
  `mode="conversation"`. Routes claude's `can_use_tool` control
  requests to the caller's `on_permission_request` handler instead of
  pre-deciding via `--permission-mode` / `--allowed-tools`. With
  `permission_gate=False` in conversation mode, `permission_mode` must
  be one of `{"acceptEdits", "bypassPermissions", "dontAsk"}` or
  `allowed_tools` must be non-empty (headless claude cannot show a
  permission dialog).

The `Conversation` surface (abstract Protocol in
`optio_agents.conversation`; concrete `ClaudeCodeConversation` here):

* `await send(text)` — queue one user message (claude queues stdin
  messages natively); raises `ConversationClosed` after session end.
* `on_event(handler) -> unsubscribe` — transparent passthrough of every
  stdout NDJSON object as a dict, unmodified. Live events only.
  Synthetic `{"type": "x-optio-unparseable", ...}` /
  `{"type": "x-optio-closed", ...}` events use the `x-optio-` prefix.
  The conversation listener keeps `x-optio-closed` out of the persisted
  replay buffer and marks a resume with its own `x-optio-resumed` instead
  (see Replay-buffer semantics).
* `on_message(handler) -> unsubscribe` — simplified tier: one final
  answer text per completed turn (the `result` event's `result` field).
* `on_permission_request(handler) -> unsubscribe` — the permission
  gate; at most one handler, second registration replaces the first.
  Requests arriving before registration are queued (the turn blocks),
  so register promptly when `permission_gate=True`. With the gate off,
  a stray `can_use_tool` is answered with a defensive deny. Note:
  sandbox-safe commands (e.g. a bare `echo`) execute without consulting
  the gate at all — the handler only sees non-sandboxable calls.
* `is_pending()` — `True` while a sent message has no `result` yet. A
  message sent mid-turn joins that turn, so two sends can share one
  `result`. `system/session_state_changed` resyncs it rather than
  zeroing it unconditionally: `idle` sets it to the number of sends
  written since the last `result` (0 for a genuinely finished turn, but
  not for a stale idle that races a send), and `running` raises it to at
  least 1. **These resyncs depend on the CLI actually emitting
  `session_state_changed`, which since CLI 2.1.270 only happens with
  `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`** (set by
  `conversation_launch_env`, below). Without it a merged turn leaves
  `_pending` stuck above 0 forever. `_route` logs one warning per
  conversation (on the first `result` seen with no prior state event)
  naming the variable, rather than drifting silently.
* `emit_event(event)` — put a synthetic `x-optio-*` event into the event
  stream, in order with native events (the steering scaffold's hook).
* `await interrupt()` — abort the current turn; no-op when idle.
  Verified live: the CLI acks with a `control_response` and ends the
  turn with `result` subtype `error_during_execution` (`result: null`,
  so no `on_message` fires for the aborted turn). Messages queued
  behind the interrupted turn are processed normally afterwards.
  `interrupt(cancel_queued=True)` (Fix 19) makes the CLI drop them
  instead: each gets a `command_lifecycle` `cancelled` and no `started`.
* `begin_session_end()` / `await interrupt_for_session_end()` (Fix 19,
  session end; see "Session end" below): the first makes `send()` raise
  `ConversationClosed` and `closed` read `True`; the second interrupts
  the running turn with `cancel_queued` and returns once its `result`
  was dispatched (a no-op when idle). The session bounds it at 3 s.
* `await close()` — cooperative shutdown of the whole task; idempotent.
* `closed` (property) — `True` once the session has ended, or is ending
  (`begin_session_end`).

Caller-side usage (publish/await via optio-core):

```python
conv = await optio.launch_and_await_result("my-task", session_id=None)
conv.on_message(lambda text: print("claude:", text))
await conv.send("hello")
...
await conv.close()
```

Task completion semantics in conversation mode:

| Trigger | Task outcome |
|---|---|
| Caller `close()` | cooperative shutdown → **done** |
| optio cancel | existing route → **cancelled** (aggressive teardown) |
| Claude exits on its own before `close()` | **failed** ("claude exited unexpectedly (exit N)") |
| `host_protocol=True` alongside (legal combo) | optio.log DONE/ERROR terminate exactly as today; a caller `close()` emits a harness-side DONE so the task still resolves **done** |

### Conversation UI (`conversation_ui`)

Full design: `docs/2026-06-10-claudecode-conversation-ui-design.md`.
Browser widget: the engine-neutral `optio-conversation-ui` package
(`packages/optio-conversation-ui`) — register it in the host app via
`registerConversationWidget()` (serves both claudecode and opencode;
each task self-declares its engine via `widgetData.protocol`).

* `conversation_ui: bool = False` — strictly opt-in; requires
  `mode="conversation"` (validated in `__post_init__`). The published
  `Conversation` object stays the default gate; this is a deliberate
  parallel path for dashboard monitoring/control.

When `True`:

* Two extra argv flags are appended to the conversation argv:
  `--include-partial-messages` (live partial text on the stream) and
  `--replay-user-messages` (user turns echoed back on stdout — without
  it the stream carries only the assistant side). Gated on the flag so
  in-process-only consumers don't pay for events nobody reads.
* `create_claudecode_task` sets `ui_widget="claudecode-conversation"`
  (instead of `None` in plain conversation mode).
* After `ctx.publish_result(conversation)` the task body starts a
  per-task `ConversationListener` (aiohttp, sibling of
  `input_listener.py`, OS-assigned port, `OPTIO_WIDGET_TUNNEL_BIND`
  interface logic), registers it via
  `ctx.set_widget_upstream(url, inner_auth)` with a per-task random
  basic-auth credential, and calls `ctx.set_widget_data({})`. The
  listener is shut down in the session teardown bracket.

Endpoints (reached through the optio-api widget proxy, which injects
the inner basic-auth credential; GET = viewer role, POST = operator):

| Endpoint | Behavior |
|---|---|
| `GET /events` | SSE. On connect: replay buffer contents, then live tail. Each event's SSE `id:` is its monotonic `seq`; `Last-Event-ID` honored, so reconnects resume without duplicates. |
| `POST /send` | `{text}` → Send when ready (`Steering.send_when_ready`). Returns `{ok, id, queued}`; `queued` is true when a turn was running: the message waits its turn and reaches Claude as its own user message (one at a time, Fix 17: see Steering events below). 409 when closed. |
| `POST /steer` | `{text, upTo?}` → Interrupt and send (`Steering.interrupt_and_send`): queue `text` behind anything already queued, interrupt the running turn and wait for its `result` (at most 15 s). Claude then runs the message in flight, and the rest follow one at a time, so `text` arrives last, as its own message. Empty `text` = Send now: only interrupt. `upTo` is the queued id Send now was clicked on; since Fix 17 it needs nothing beyond the interrupt (no cancel, no re-send). Returns `{ok, id}` (`id` null for empty text). 409 when closed. |
| `POST /interrupt` | `{}` → `Steering.interrupt()`: stop only; emits `x-optio-interrupt` while a turn runs. No-op when idle. |
| `POST /permission` | `{request_id, behavior: "allow"\|"deny", updated_input?, message?}` → resolves the pending permission future. 404 for unknown/already-answered request_id. |

Replay-buffer semantics:

* `collections.deque(maxlen=1000)` of raw events, stamped with a
  monotonic `seq`. Session-persistent only — nothing goes to Mongo;
  after the task ends the conversation view is gone.
* Mechanical type filter, not interpretation: events of type
  `stream_event` (the partial-message deltas) are forwarded live but
  never buffered. Everything else — `system`, `user`, `assistant`,
  `result`, `control_request`, `x-optio-*` — is buffered.
* The engine channels raw stream-json events through untouched; all
  interpretation happens client-side in `optio-conversation-ui`. The
  listener adds synthetic events of its own:
  `{"type": "x-optio-permission-answered", "request_id": ..., "behavior": ...}`,
  broadcast (and buffered) when a permission is answered so every
  viewer sees the card resolve; `{"type": "x-optio-resumed"}` (below);
  `{"type": "x-optio-message-start", "id", "ts"}` (Fix 12); and, at a
  session end mid-turn (Fix 19, below), `{"type": "x-optio-partial", "id",
  "text"}` and `{"type": "x-optio-interrupt", "by": "session"}`.
* `x-optio-message-start` (Fix 12, owner ruling 2026-09-14: an assistant
  message's time label becomes a "HH:MM - HH:MM" interval when streaming
  crosses a minute, so the UI needs an exact start). `stream_event` frames
  carry no time and are never buffered (above), so a streamed message's true
  start would otherwise only exist live, at the moment `message_start`
  passes through, and be lost to replay. The listener stamps it instead: for
  every `stream_event` whose inner event is `message_start`, it broadcasts
  `{"type": "x-optio-message-start", "id": <message.id>, "ts": <epoch ms,
  server clock at receipt>}` — buffered and persisted like a native event —
  immediately BEFORE forwarding that `stream_event` (still unbuffered, as
  today). `id` matches the assistant event's own `message.id`. The clock is
  injectable (`ConversationListener(..., clock=...)`) for deterministic
  tests; only the listener ever stamps this, so live and a later replay of
  the persisted buffer show the exact same value.
* Steering events (`optio_claudecode.steering`, `busy_send` =
  `joins-next-step` via `BUSY_SEND`): `{"type": "x-optio-queued", "id",
  "text"}` for a Send when ready (or an Interrupt and send's text) that
  arrived mid-turn, emitted when it is sent, not when it reaches Claude,
  and `{"type": "x-optio-interrupt", "by": "user"}` before every interrupt
  optio sends while a turn runs. Both enter through `emit_event`, so they
  are buffered, replayed and persisted like native events. `{"type":
  "x-optio-requeued", "id", "new_id"}` is no longer emitted for Fix 13a's
  `upTo` re-send (Fix 17); since Fix 19 it announces each message a
  resumed run re-sends (see Resume below). Buffers recorded before Fix 17
  still contain the old kind, and the UI handles both.
* Fix 13a (owner rulings from manual testing, 2026-09-15): every stdin
  message `ClaudeCodeConversation.send(text, *, uuid=None)` writes carries a
  `uuid` (never a `priority`) — the ONLY thing that turns on the CLI's
  `command_lifecycle` events for that message. A message through Steering
  gets its own steering id as that uuid (bubble id, response id and CLI
  uuid are one value); a call outside Steering (session.py's agent
  feedback, the first prompt) omits it and gets a fresh `uuid4` minted
  inside `send()`. A message written directly onto Claude's own native
  queue (the busy path of `send_when_ready` and `interrupt_and_send`) gets
  a trailing blank line unless it already ends with one: several messages
  queued together otherwise reach the model run together with no
  separator, because the CLI folds them into one turn with one text block
  each (`x-optio-queued` itself still carries the ORIGINAL text). Idle
  sends are unchanged. `ClaudeCodeConversation.cancel_async_message(uuid)`
  sends `cancel_async_message` and returns the reply's `cancelled` bool (a
  transport capability; Steering no longer uses it since Fix 17).
  `optio_claudecode.steering.command_lifecycle(event)` extracts
  `(command_uuid, state)` from a `command_lifecycle` event, and
  `make_steering` always wires it into `Steering` (it is a pure function).
* One at a time (Fix 17, owner ruling 2026-09-15;
  `docs/2026-09-15-steering-individual-delivery-design.md`). Claude Code
  folds every prompt waiting in its stdin queue into ONE user message when
  it starts a turn, but takes queued prompts one by one at tool
  boundaries, and no `priority` value changes that (`now` interrupts the
  turn and jumps the queue; `later` is never taken mid-turn and is still
  folded). So `Steering` keeps at most ONE optio message in the CLI queue:
  the others wait in its pending list, and the next is written when the
  CLI reports the one in flight `started`, or `completed` / `cancelled` /
  `discarded` / `refused` (each can arrive without `started`). With tool
  calls, each message joins the running turn at the next tool boundary; in
  a text-only turn, each becomes its own turn after the current one (N
  messages = N turns, a cost the owner accepted). After an interrupt the
  CLI runs the message in flight first (the interrupt reply lists it under
  `still_queued`), and the model reads the CLI's "[Request interrupted by
  user]" marker in that same user turn. A CLI that emits no lifecycle
  still advances: a conversation that is idle (`is_pending()` False) holds
  nothing in its queue, so the next message goes when the turn ends idle.
  Messages still pending when the session ends are never written.
* Session end (Fix 19, owner rulings 2026-09-15;
  `docs/2026-09-15-steering-session-end-design.md`). A cooperative cancel
  (stop, suspend, engine shutdown) while a turn runs
  (`is_pending()`): the body's cancel path calls
  `session._end_turn_gracefully`, which (1) `begin_session_end()`, so
  neither Steering nor `POST /send` writes anything more; (2)
  `listener.announce_session_end()`, which puts `{"type":
  "x-optio-interrupt", "by": "session"}` into the event stream after
  every event already read; (3) `interrupt_for_session_end()`, bounded
  by `GRACEFUL_INTERRUPT_TIMEOUT_S` (3 s), while the reader still runs:
  the CLI records the partial answer in its transcript, and its final
  partial `assistant` event, "[Request interrupted by user]", the
  `cancelled` of the message in flight and the aborted `result` are all
  buffered. Then the reader stops. Just before `x-optio-closed` passes,
  the listener adds `{"type": "x-optio-partial", "id", "text"}` if the
  open block of the message being streamed has text whose final
  `assistant` event never came (the listener accumulates `text_delta` and
  `thinking_delta` text per block, reset at `message_start` and at each
  final `assistant` event), and the marker if it was not announced and a
  turn runs (`running` or a `message_start` seen without its
  `result`/`idle`, or `is_pending()`). Both are in `export_buffer()`.
  claude is then SIGKILLed and the snapshot taken as before. Idle: nothing
  happens. The wait loop of the body cancels its waiter tasks when it is
  cancelled (no "Task was destroyed but it is pending!").
* Resume: `export_buffer()` persists the buffer with the snapshot, minus
  the terminal `x-optio-closed` (replayed, it would close the live resumed
  session in the UI). When a resumed run re-primes the listener from it,
  the listener appends exactly one `{"type": "x-optio-resumed"}` after the
  restored history. It marks where the earlier run ended, since nothing
  else does: the widget stops the rows that run left running (a background
  task, a call with no result) as `stopped`, at the latest wire timestamp.
  The marker is persisted like any event, so a later resume replays it in
  place and appends its own. Fix 19 (owner ruling 2026-09-15, finding 6
  #2): `optio_claudecode.steering.undelivered_queued(events)` computes,
  from the restored buffer, what the LAST run left queued and undelivered
  (an `x-optio-queued` with no `command_lifecycle` `started`/`completed`
  and no confirming echo; `x-optio-requeued` renames it; `discarded` and a
  `cancelled` without `started` leave it undelivered; an earlier
  `x-optio-resumed` drops what older runs left). The marker then carries
  `"requeued": [ids]`, and right after the "you have been resumed" notice
  the session calls `listener.requeue_undelivered()`: `Steering.requeue`
  re-sends them in order, each under a new uuid with one `{"type":
  "x-optio-requeued", "id", "new_id"}`, one at a time (Fix 17). Nothing
  the CLI already started is re-sent.

**Handler-slot rule**: `conversation_ui=True` occupies the single
`on_permission_request` slot (the listener registers the handler and
resolves it from `POST /permission`). Consumers that want programmatic
permission gating must not enable `conversation_ui` — or must accept
that the UI is the gate.

When `False` (default): behavior is byte-identical to plain
conversation mode (`ui_widget=None`, no listener, no extra argv flags).

## Hooks

`before_execute(hook_ctx)`, `after_execute(hook_ctx)`,
`on_deliverable(hook_ctx, relative_path, decoded_text)`. Identical
signatures and failure semantics to optio-opencode.

`before_execute` fires **after** AGENTS.md and HOME files are planted
and **before** ttyd launches.

`after_execute` fires after claude exits (or after cancellation), on
both success and ERROR paths.

## Log-file contract

Same as opencode. AGENTS.md tells claude to append to `./optio.log`:

- `STATUS: [N%] <msg>`
- `DELIVERABLE: <workdir-relative-or-absolute-path>` (must resolve
  under `<workdir>/deliverables/`)
- `DONE[: summary]`
- `ERROR[: message]`

DONE / ERROR terminate the session.

## Binary install

* claude — provisioned from a shared, optio-owned version cache
  (`${OPTIO_CLAUDECODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-claudecode/versions}`);
  the per-task home gets `home/.local/share/claude/versions` symlinked at the
  cache and `home/.local/bin/claude` pointed at the newest cached version.
  Cache miss → vendor `curl -fsSL https://claude.ai/install.sh | bash`
  (unconfined, `HOME=<workdir>/home`) writes through the symlink into the
  cache. Cache hit → launch-time freshness check against the upstream
  `latest` endpoint install.sh consults
  (`https://downloads.claude.ai/claude-code-releases/latest`); upstream newer
  → same install.sh path refreshes the cache; probe failure keeps the cached
  binary (best-effort). Sessions run with `DISABLE_AUTOUPDATER=1` — under
  claustrum the cache is `--rox`, so in-session self-update can only EACCES;
  freshness is owned by this unconfined provisioning path. See the addendum
  in `docs/2026-05-31-optio-claudecode-runtime-cache-design.md`.
  Conversation mode also pins `CLAUDE_CODE_THINKING_DISPLAY_UPDATES=1`
  (`conversation_launch_env`, set before `extra_env`, which can still
  override it). Since CLI 2.1.267 the model's between-tool narration arrives
  as text-bearing thinking blocks ("thinking updates"), which the widget
  renders as ordinary replies; the pin keeps a CLI default change from
  silently dropping that channel. See
  `docs/2026-09-12-claudecode-conversation-rendering-design.md`.
  Conversation mode also pins `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`
  (same function): since CLI 2.1.270 the CLI only emits
  `system/session_state_changed` (idle/running) with this set, and
  conversation steering's `is_pending()` resync (see above) depends on
  those events. Owner ruling, 2026-09-14 manual test finding (conversation
  steering stage 1, fix 3).
* ttyd — downloaded from `tsl0922/ttyd` GitHub Releases (pinned
  version). Linux x86_64/aarch64/armv7l only in v1.

Override install locations via `claude_install_dir` /
`ttyd_install_dir` (absolute paths).

## Testing

```
pytest packages/optio-claudecode/tests/
```

Needs MongoDB via Docker for the integration tests.

Fake binaries (`claude-shim.sh`, `ttyd-shim.sh`, `fake_claude.py`) live
in `tests/` and substitute the real ones during integration tests. The
ttyd shim prints a fake "Listening on http://127.0.0.1:N/" banner so
the framework's port discovery completes without opening a real socket.
The claude shim resolves its own symlink (via `readlink -f`) before
locating `fake_claude.py`, since the framework symlinks the shim into
a tmpdir.

Remote SSH automated tests are deferred to a follow-up plan. See the
design doc's "Open follow-ups" section.
