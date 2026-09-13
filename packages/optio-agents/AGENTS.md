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
  turn_end_timeout_s=15.0, new_id=None)` over any `Conversation`
  (`send`, `interrupt`, `is_pending`, `on_event`, optional `closed`):
  * `await send_when_ready(text) -> SendOutcome(id, queued)` — idle: plain
    send. Busy + `joins-next-step`/`queues-to-end`: emits
    `{"type":"x-optio-queued","id","text"}`, then sends (the agent holds it).
    Busy otherwise: emits x-optio-queued and holds it in optio's queue; at
    the turn end (`is_turn_end(event)`) everything held goes as ONE prompt
    joined by a blank line, announced by one `{"type":"x-optio-taken","ids"}`.
  * `await interrupt_and_send(text) -> id | None` — busy: emits
    `{"type":"x-optio-interrupt","by":"user"}`; `cuts-in` then sends
    natively; others `interrupt()` and wait for the turn end (at most
    `turn_end_timeout_s`, then send anyway and log a warning). Then held
    messages + `text` go as one prompt. Empty `text` = Send now (returns None).
  * `await interrupt()` — stop only; emits x-optio-interrupt while busy.
  * `await settle()`, `held_ids`, `close()` (unsubscribes).
* `emit` must put the event into the wrapper's own event stream (in order
  with native events) so the conversation listener buffers and persists it.

## Dependency direction

Depends on `optio-host` and `optio-core`; consumed by every engine wrapper
(`optio-claudecode`, `optio-opencode`, `optio-codex`, `optio-cursor`,
`optio-grok`, `optio-kimicode`, `optio-antigravity`) and by
`optio-agents-all`.
