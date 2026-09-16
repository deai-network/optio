# optio-agents — Agent Cheatsheet

The agent-coordination layer shared by every engine wrapper: the optio.log
keyword protocol (parser + session driver), `HookContext`, the abstract
`Conversation` surface, claustrum provisioning, and the engine-neutral
task-config vocabulary described below.

## Shared config vocabulary (`optio_agents.config_types`)

Engine `TaskConfig` dataclasses (all `frozen=True, kw_only=True`) compose
their common field sets from mixins here instead of redeclaring them. Fields
stay top-level on each config — callers write `fs_isolation=` /
`session_blob_encrypt=` verbatim, no nesting.

* `ClaustrumConfigMixin` — the claustrum filesystem-isolation triad
  (`fs_isolation`, `extra_allowed_dirs`, `delivery_type`). Engines call
  `self._validate_claustrum()` from `__post_init__`.
* `BlobCryptoConfigMixin` — at-rest crypto for the two GridFS blob channels,
  four `Callable[[bytes], bytes] | None` fields (default None = plaintext):
  * `session_blob_encrypt` / `session_blob_decrypt` — wrap this process's
    session snapshot (the per-task home tar; ds-scoped key).
  * `seed_blob_encrypt` / `seed_blob_decrypt` — wrap the SEED tar (the shared
    pool account; pool-scoped key). Falls back to the session pair when unset,
    so single-key callers are unaffected.

  Setting one member of a pair without the other is a config error; engines
  call `self._validate_blob_crypto()` from `__post_init__`. The seed→session
  fallback lives ONLY in the `seed_encrypt` / `seed_decrypt` accessor
  properties — engine seed ops must read the transforms through those, never
  the raw `seed_blob_*` fields.

Also here: `AllowedDir`, `ConversationMode`, `ToolVerbosity` (+
`TOOL_VERBOSITIES`, the SSOT validation set), `ThinkingVerbosity`,
`SeedProvider` / `SeedUnavailableError`.

## Conversation steering (`optio_agents.steering`)

Design: `docs/2026-09-13-conversation-steering-design.md`. The scaffolding
behind every conversation listener's `POST /send`, `POST /steer` and
`POST /interrupt`.

* `BusySend` — what the agent does with a message sent while a turn runs:
  `joins-next-step` (taken at the next tool result, same turn),
  `queues-to-end` (the agent's own queue, after the turn), `cuts-in`
  (cancels the turn, starts a new one), `rejected`, `unsafe` (undefined).
* `BusySendDeclaration(agent, models={})` — a wrapper's measured value;
  `.for_model(model)` returns the model override or the agent value (a
  `[variant]` suffix is ignored). `resolve_busy_send(None, model)` is
  `unsafe`: an unmeasured agent is always correct, only slower. Add a model
  override only when a recording shows that model behaving differently.
* `Steering(conversation, *, busy_send, emit, is_turn_end,
  turn_end_timeout_s=15.0, new_id=None, command_lifecycle=None)` over any
  `Conversation` (`send(text, *, uuid=None)`, `interrupt`, `is_pending`,
  `on_event`, optional `closed`). `new_id` defaults to a dashed
  `uuid.uuid4()` string, not `.hex`: for a native-queue wrapper this id
  doubles as the transport's own message identity (Fix 13a), which for
  Claude Code's CLI must be a schema-valid uuid. `command_lifecycle(event)`
  returns `(command_uuid, state)` for the transport's per-message lifecycle
  event, else `None`; a native-queue wrapper whose transport reports one
  (Claude Code) passes it.
  * One at a time onto a native queue (Fix 17, owner ruling 2026-09-15;
    `docs/2026-09-15-steering-individual-delivery-design.md`). An agent in
    `NATIVE_QUEUE` (`joins-next-step`, `queues-to-end`) may fold every
    prompt waiting in its own queue into ONE user message when it starts a
    turn, so `Steering` never lets more than one of its messages wait
    there. Messages sent while the agent is busy form an ordered pending
    list (`pending_ids`); at most one is in flight, i.e. written to the
    agent and not taken yet (`in_flight_id`). The next pending message is
    written when the in-flight one's `command_lifecycle` state is `started`
    or any final state (`completed`, `cancelled`, `discarded`, `refused`;
    a final state can come without `started`), or, as a safety net, when a
    turn ends or the agent is idle with nothing in flight. An idle agent
    holds nothing in its queue, so `is_pending()` going False also clears
    the in-flight mark: that keeps delivery going (one message per turn)
    for a transport that reports no lifecycle at all. Each written message
    carries its id as its uuid and a trailing blank line. The write runs as
    its own task, never inside the wrapper's event dispatch.
  * `await send_when_ready(text) -> SendOutcome(id, queued)` — idle: plain
    send, `conversation.send(text, uuid=id)`. Busy + `NATIVE_QUEUE`: emits
    `{"type":"x-optio-queued","id","text"}` (ORIGINAL text) and appends the
    message to the pending list; it is written at once only if nothing is
    in flight. A send that finds the agent idle while earlier messages are
    still pending queues behind them instead of overtaking them. Busy
    otherwise: emits x-optio-queued and holds it in optio's queue; at the
    turn end (`is_turn_end(event)`) everything held goes as ONE prompt
    joined by a blank line, announced by one
    `{"type":"x-optio-taken","ids"}`.
  * `await interrupt_and_send(text, *, up_to=None) -> id | None` — busy:
    emits `{"type":"x-optio-interrupt","by":"user"}`. `NATIVE_QUEUE`:
    non-empty `text` then gets its x-optio-queued and is appended to the
    pending list WITHOUT being written, then comes `interrupt()` and the
    wait for the turn end; after it (or the deadline) the next pending
    message is written if nothing is in flight. The agent keeps the
    in-flight message through the interrupt and runs it next, and the rest
    follow one at a time, so `text` arrives last, as its own message.
    `cuts-in` sends natively; the other agents `interrupt()`, wait for the
    turn end, then send what optio holds plus `text` as one prompt. One
    `turn_end_timeout_s` deadline covers both the `interrupt()` call and
    the wait, so a live but unresponsive agent cannot hold `Steering` (and
    every later send/steer behind it) forever; on expiry it goes on anyway
    and logs a warning. Non-empty `text` gets its own
    `{"type":"x-optio-queued","id","text"}` (the same id this call
    returns), emitted after the interrupt marker (if any), so the reducer
    can dedupe the steer's own local echo against the wire by id. Idle,
    `text` is a plain send (on a native queue, unless earlier messages are
    still pending: then it queues behind them). Empty `text` is Send now
    (returns `None`, no queued event): on a native queue it only
    interrupts, and delivery continues in order; with nothing held and the
    agent not in `NATIVE_QUEUE`, it is a no-op — it neither interrupts nor
    sends, so it never draws an interrupted-row on a turn that is still
    streaming.
  * `up_to` (the queued id Send now was clicked on) is accepted and needs
    nothing beyond the interrupt: a native queue never holds more than the
    one in-flight message, so messages after `up_to` are only written once
    their predecessors started, and are taken when the agent is ready.
    Fix 13a's `cancel_async_message` + re-send path is gone (Fix 17);
    `{"type":"x-optio-requeued","id","new_id"}` now comes only from
    `requeue` (Fix 19, below).
  * `await interrupt()` — stop only; emits x-optio-interrupt while busy.
    Messages still pending on a native queue keep going one at a time
    afterwards.
  * At most one `x-optio-interrupt` (and one underlying `interrupt()` call)
    per turn, shared between `interrupt()` and `interrupt_and_send()`: a
    second Interrupt click, or one pressed while `interrupt_and_send` still
    waits for the turn end, emits no second marker and sends no second
    control request; `interrupt_and_send` in an already-interrupted turn
    still waits for the turn end and delivers. The flag clears on
    `is_turn_end(event)`, and — checked again on every later event — also
    once `conversation.is_pending()` has gone False. The second check
    matters for a merged turn (Send when ready taken mid-turn): the result
    that ends it can leave `is_pending()` True for a few more ms, until the
    wrapper's own idle event catches up. Without it, an Interrupt landing in
    that gap sets the flag again right after `is_turn_end` cleared it, and
    nothing would ever clear it again — silently swallowing the next turn's
    first Interrupt (no marker, no underlying `interrupt()` call).
  * `await requeue(messages) -> [new ids]` (Fix 19, owner ruling
    2026-09-15, finding 6 #2): deliver again messages a previous run left
    queued and undelivered, given as `(old id, ORIGINAL text)` in their
    original order. Each gets a NEW id and one
    `{"type":"x-optio-requeued","id":<old>,"new_id":<new>}` (never a
    second x-optio-queued), emitted before anything is written.
    `NATIVE_QUEUE`: they go to the FRONT of the pending list, ahead of
    messages sent since the resume, written one at a time as above (the
    first at once if nothing is in flight). Other agents: the front of
    optio's queue, delivered at the turn end, or at once when idle.
    Raises `ConversationClosed` when closed.
  * Session end: pending native-queue messages are never written; a write
    attempted after the close logs a warning. Their x-optio-queued, never
    followed by a `started`, is what a resuming wrapper re-queues (Claude
    Code: `ConversationListener.requeue_undelivered`, Fix 19).
  * `await reset_transport()` (Fix 25, final-review-2 I3 / ledger line 399):
    called once the caller has re-attached a fresh transport underneath this
    Steering (a model/effort relaunch SIGTERMs the CLI mid-turn and
    relaunches it). The dead process took the in-flight message with it, so
    neither its `command_lifecycle` nor the turn's own `result` will ever
    arrive; without this, `in_flight_id` would stay set forever and the
    native queue would never advance again. Puts that message back at the
    front of `pending_ids` (rewritten, not dropped) and writes the next
    deliverable message to the newly attached transport; everything else
    queued is left exactly as it was, in order.
  * `await settle()` (waits for a turn-end flush or a native-queue write in
    progress), `held_ids`, `pending_ids`, `in_flight_id`, `close()`
    (unsubscribes).
* `emit` must put the event into the wrapper's own event stream (in order
  with native events) so the conversation listener buffers and persists it.

## Dependency direction

Depends on `optio-host` and `optio-core`; consumed by every engine wrapper
(`optio-claudecode`, `optio-opencode`, `optio-codex`, `optio-cursor`,
`optio-grok`, `optio-kimicode`, `optio-antigravity`) and by
`optio-agents-all`.
