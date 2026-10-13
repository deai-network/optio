# optio-agents — Agent Cheatsheet

The agent-coordination layer shared by every engine wrapper: the optio.log
keyword protocol (parser + session driver), `HookContext`, the abstract
`Conversation` surface, claustrum provisioning, and the engine-neutral
task-config vocabulary described below.

## Conversation todos (`optio_agents.todos`)

Design: `docs/2026-09-28-conversation-todo-progress-design.md`.

Conversation mode watches the agent's own todo tool and calls
`ctx.report_progress`. Percent is completed-over-total on the 0–100 scale
(`(100 * completed + countable // 2) // countable`, halves up). Cancelled
items are stored and left out of both counts. While a row is in progress
the status line is `Now working on ` plus `Task <n>/<total>, "<text>"`
for each such row, joined with ` and `. `n` is that row's 1-based place
among the rows that are not cancelled, and `total` is how many those
are. The quoted text is the task text. When nothing is in progress the
message is null, including when the last in-progress row finishes.
The percent still moves. A null message clears the previous sentence
on the bar and does not append a log line. The same sentence is logged
once. Grok sends a todo change as a
tool call, again as the tool update, then as a plan. A changed percent
with the same sentence still moves the bar and does not add a line.
A merge whose id is not already in the list updates the row with the
same text instead of appending a second copy. An ACP plan row with
`_meta.cancelled: true` is `cancelled` even when its status field is
`completed` (the plan enum has no cancelled value). `canceled` is the
same state. An empty list calls `report_progress(None)`: the bar goes
indeterminate and the stale sentence is cleared. A list that still has
rows but nothing left to count does not move the bar.

The list is `{workdir}/.optio-todo.json`, written at teardown when
`supports_resume` is set. On resume it is loaded after the launch
milestone. The watcher is armed before history replay. Replay updates the list
and does not append a progress line for each old change. When replay
finishes, one line announces the list as it stands. A replayed todo
tool call updates the status of a matching row and does not add, drop,
or reorder rows, so a shortened replay cannot replace the saved list.
A replayed plan is ignored. A plan does not mark a row completed while
the todo tool's last status for that text is still `in_progress`. That
last tool status is stored on the item as an optional `tool` field, so
a resume whose history replay never arrives still blocks the plan.
Grok's wire title for this tool call is "Updating plan"; the tool name
is `_meta["x.ai/tool"].name` or `rawInput.variant`.

Claude Code's TaskCreate, TaskUpdate, and TaskList are patches, removals,
and a list snapshot. A partial update leaves an omitted field as it was,
including status and description. `deleted` removes the row, and TaskList
replaces membership (`No tasks found` clears it) while keeping a
description the list line does not carry. On resume, a transcript that
accepted one of those results rebuilds the list from the transcript
instead of `.optio-todo.json`.

The same list is written to `widgetData.todos` via `ctx.set_widget_todos`,
which leaves every other widgetData key in place and does nothing when
widgetData is not already an object. A failed write is logged and does
not undo the progress report. Writes are serialized, so an earlier todo
event cannot land on top of a later one. The conversation view renders
that list in a column to the right of the transcript. The column starts
open; its chevron collapses the column, and the width eases as it
moves. A completed row shows a green check. A label ending in
`(failed)`, in any case, shows a red ×, and that suffix is left off
the name on the row. A non-empty description marks the row with an info icon. Hovering
the info icon says "Click for details". Clicking it opens a column to the right
of the list, and that column renders the description with the same markdown
as an assistant reply. The column eases open and shut like the list.
Collapsing the list hides the column and keeps the selection; the column's
close control closes only the column. The open row is highlighted. An empty
list clears it. Resume
publishes the restored list before the next todo write. Launch
milestones do not clear the checklist.

An engine whose checklist tool has been tested names it on
`AgentPromptProfile.todo_tool`. A bare name is wrapped as "the `<name>`
tool". A value that contains a comma is the text after "via", so an
engine with several tools can name all of them. The instructions then
tell the agent to keep that list with the named tool or tools, and to
append `(failed)` to a completed label when a step failed. When the tool
has a description or details field, the note also tells the agent to
record what it plans to do or what happened, including why a step failed,
because that text is shown to the person supervising the process. Grok names
`todo_write`. Claude Code names `TaskCreate`, `TaskGet`, `TaskList`,
and `TaskUpdate`. The other engines leave the field unset, so their
instructions say nothing about it.

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
* `SessionControlsConfigMixin` — the conversation widget's controls bar:
  `show_session_controls: bool = False` and `session_controls: list[str] |
  None = None` (allowlist of control ids; None offers every control). Engines
  call `self._validate_session_controls()` from `__post_init__` (the allowlist
  must be a list of ids and needs the bar on). `settable_controls` is what a
  `/control` endpoint may change: `[]` while the bar is off, else the
  allowlist.
* `WelcomeConfigMixin` — the conversation widget's welcome card, shown at the
  top of the transcript: `welcome: WelcomeCard | None = None`.
  `WelcomeCard(title, description=None, icon=None, min_height=None)`
  (frozen): `title` and `description` (markdown) are `Text` (see below),
  checked with `check_text` (a map is stored as a plain dict copy); `title`
  must also not be blank. `icon` is a URL relative to the web app root — a
  value with no leading "/" and no "://" gets one (`"img/a.png"` →
  `"/img/a.png"`), `"/x.png"` and absolute URLs stay as given.
  `min_height` is the card's minimum height in CSS pixels (a positive int;
  bool, 0, negatives and non-ints raise `ValueError`). `to_widget_data()`
  returns `{"title", "description", "icon", "minHeight"}`: texts unresolved
  (the widget picks the viewer's language), icon normalized, None kept. The
  mixin's `welcome_widget_data` is that dict or None; every engine publishes
  it as the conversation widget's `welcome` widgetData key (iframe / TUI
  widgets ignore it). Engines call `self._validate_welcome()` from
  `__post_init__` (anything but a `WelcomeCard` or None raises
  `ValueError`). Both names are exported from `optio_agents`.
* `ResumeConfigMixin` — how a suspended session resumes:
  `smooth_resume: bool = False`, "this task should be resumed without
  confirmation when a message arrives". Set, a message sent to the suspended
  session resumes it directly; unset, the widget asks first. A send that
  would discard a failed run's unsaved work (a resurrect) still asks. No
  validation and no refusal: every engine accepts it. Only claudecode has a
  suspended view whose send resumes the session so far (it publishes the flag
  as `widgetData.smoothResume`); on the other engines it has no effect until
  they get one.
* `McpConfigMixin` (in `optio_agents.mcp`, with the rest of the MCP
  vocabulary) — `mcp_servers`: None (no MCP servers, the default), a static
  list of `McpServer`, or a `McpServersProvider` (an async callable awaited
  with the process id, like `SeedProvider`, so per-launch credentials stay
  fresh). `McpServer(name, url, headers)` is one remote streamable-HTTP server
  (name of letters, digits, `_`, `-`; an http(s) URL; header values are
  credentials, never shown in a `repr` or an error). Engines call
  `self._validate_mcp(supported=..., agent=...)` from `__post_init__` (an
  engine without a translation refuses any non-None value; a static list is
  checked) and `resolve_mcp_servers(config, process_id)` on every launch,
  fresh and resume (a provider's result is checked; its exception fails the
  launch). Only claudecode translates it so far: ask
  `optio_agents_all.supports_mcp(agent_type)`.
* `CustomControlsConfigMixin` (in `optio_agents.custom_controls`) —
  `custom_controls: list[CustomControl] | None`: task-defined selectors shown
  in the conversation view, independent of `show_session_controls` /
  `session_controls`. Engines call
  `self._validate_custom_controls(supported=..., agent=...)` from
  `__post_init__`: an engine that cannot apply them refuses a non-empty list
  (`<agent> does not support custom_controls yet`); otherwise control ids are
  unique and each setting (model, reasoning effort, permission mode) is set by
  the options of at most one control. Only claudecode applies them so far: ask
  `optio_agents_all.supports_custom_controls(agent_type)`. See the next
  section.

## Custom session controls (`optio_agents.custom_controls`)

* `Text` — a plain string or a map of language code (`en`, `hu`, `de`, ...)
  to a non-empty string; `resolve_text(text, language)` gives the language's
  entry, else `en`, else the first entry (a plain string as is).
* `ControlSettings(model, reasoning_effort, permission_mode)` — what an option
  sets; None leaves a setting alone, at least one is set. `reasoning_effort` is
  one of `KNOWN_REASONING_EFFORTS` (low, medium, high, xhigh, max) and
  `permission_mode` one of `KNOWN_PERMISSION_MODES` (Claude Code's wire values:
  `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions`;
  `manual` is spelled `default`). Kept here because optio-agents imports no
  engine; optio-agents-all's tests keep them equal to optio-claudecode's.
  `to_dict()` holds only the set values.
* `CustomOption(value, label, settings, description=None)` — `value` of
  letters, digits, `_`, `-`; `label` / `description` are `Text`.
* `CustomControl(id, label, default, options, description=None)` — `id` of
  letters, digits, `_`, `-` (its `/control` id is `custom:<id>`); `label` is
  `Text` or None (no caption); at least 2 options with unique values;
  `default` is one of them, applied when a session starts.

All are frozen and validated on construction (`ValueError`); language maps
and the options list are copied.

## Localized text (`optio_agents.text`)

* `Text = str | Mapping[str, str]` — a plain string, or a map of language code
  (`"en"`, `"hu"`, `"de"`, ...) to a non-empty string. Configuration keeps it
  unresolved and publishes it as is (a map as a plain dict); the viewer picks
  the language.
* `check_text(value, *, what)` — returns a non-empty string as is, or a
  non-empty map with non-empty string keys and values as a plain dict copy;
  anything else raises `ValueError` naming `what`.
* `resolve_text(text, language)` — a string as is; from a map, the language's
  entry, else `"en"`, else the first entry.

All three are exported from `optio_agents`.

## Session controls (`optio_agents.session_controls`)

* `SessionControl` / `ControlOption` — one engine-neutral live control and a
  select's option (`description`, `disabled` + `why_disabled`, `variant`
  primary / danger, `confirm`: a question asked before switching), serialized with
  `to_dict()` for widgetData and `x-optio-control-update` snapshots.
  `model_control` / `effort_control` build the common two.
* `filter_controls(controls, allowed)` — narrows a serialized snapshot to the
  allowlist (None: all); every emitted snapshot goes through it.
* `control_allowed(control_id, allowed)` — the `/control` gate.
* `validate_session_controls(...)` — the config check behind the mixin.

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
    `{"type":"x-optio-queued","id","text","waiting":true}` (ORIGINAL text)
    and appends the message to the pending list; it is written at once only
    if nothing is in flight. A send that finds the agent idle while earlier
    messages are still pending queues behind them instead of overtaking them
    (also `waiting: true`). Busy otherwise: emits x-optio-queued (`waiting:
    true`) and holds it in optio's queue; at the
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
    `{"type":"x-optio-queued","id","text"}` (no `waiting`: it arrives after the interrupted turn ended, so the widget must not pin it; the same id this call
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
  * `begin_transport_reset()` (Fix 29/W1, wave-2 re-review of Fix 25): call
    from the moment a relaunch begins, BEFORE the old process is killed —
    alongside the wrapper's own restart marker (Claude Code:
    `ClaudeCodeConversation.begin_restart()`). Latches `in_flight_id`
    against the dying process's own last events: an abort `result` +
    trailing idle, or a shutdown sweep's `command_lifecycle`
    `cancelled`/`discarded`/`refused`, no longer clear it (they would
    otherwise make `reset_transport()` below find nothing to rescue — the
    exact failure Fix 25 exists to prevent, one step earlier). A genuine
    `started`/`completed` observed during the window is still honoured, so
    `reset_transport()` knows not to write that message a second time.
    Review of fix 29, finding 3: the latch also covers `send_when_ready`,
    `interrupt_and_send` and `requeue`'s own "the agent is idle, so
    `in_flight_id` must be stale" reading — each suspends it while a
    relaunch is in progress, so a call landing in the window (the W2 drain
    can leave `is_pending()` False for a few seconds before
    `reset_transport()` runs) queues behind the latched message instead of
    clearing it and writing straight to the dying transport.
  * `await reset_transport()` (Fix 25, final-review-2 I3 / ledger line 399):
    called once the caller has re-attached a fresh transport underneath this
    Steering (a model/effort relaunch SIGTERMs the CLI mid-turn and
    relaunches it). The dead process took the in-flight message with it, so
    neither its `command_lifecycle` nor the turn's own `result` will ever
    arrive; without this, `in_flight_id` would stay set forever and the
    native queue would never advance again. Puts that message back at the
    front of `pending_ids` (rewritten, not dropped — unless
    `begin_transport_reset()`'s window already saw it taken) and writes the
    next deliverable message to the newly attached transport; everything
    else queued is left exactly as it was, in order. Clears the
    `begin_transport_reset()` latch as its first act. Review of fix 29,
    finding 1: if the window instead saw a `cancelled`/`discarded`/
    `refused` for it, the dying process's own raw event already reached the
    UI too (the W2 drain does not suppress it), and the reducer has already
    turned that message's queued bubble into a muted "Not delivered" note —
    rewriting it under the SAME id would strand that note next to a
    duplicate, freshly-delivered bubble. This case instead gets a NEW id
    and one `x-optio-requeued` announcing the swap, the same event Fix 19's
    own `requeue()` emits, which the reducer already knows how to turn a
    "Not delivered" note back into a queued bubble with.
  * `await settle()` (waits for a turn-end flush or a native-queue write in
    progress), `held_ids`, `pending_ids`, `in_flight_id`, `close()`
    (unsubscribes).
* `emit` must put the event into the wrapper's own event stream (in order
  with native events) so the conversation listener buffers and persists it.

## Instructions file (`optio_agents.prompt`)

`compose_instructions_file` (and the `render_resume_section` it calls for the
resume section) gains the `./regenerable.txt` paragraph and resume step from
the workdir capture cap design (`docs/2026-10-13-workdir-capture-cap-design.md`).

* `render_resume_section(profile, workdir_exclude, check_resume_log_every_message=True, *, capture_max_bytes=None)`
  and `compose_instructions_file(..., capture_max_bytes=None)`: `None` (the
  default) reads `optio_host.capture_cap.workdir_capture_max_bytes()` once,
  inside `render_resume_section`, so a bad `OPTIO_WORKDIR_CAPTURE_MAX_MB`
  surfaces at agent launch rather than only when a capture runs. Passing an
  explicit byte count (tests, or a caller that already resolved it) skips that
  read.
* The paragraph renders, right after the section's existing "preserved / not
  preserved" text, when the task supports resume (the resume section exists
  only then — exactly when captures happen) AND the cap is not `0`:

  > **Keep `./regenerable.txt` current.** List in it anything big in the
  > workdir that you could regenerate quickly and completely: dependency
  > installs, build outputs, caches, data you can download again. One path
  > per line, relative to the workdir (exact paths, no wildcards); a `#` note
  > after a path is fine (e.g. `frontend/node_modules  # npm ci`). When the
  > workdir is over `<C>` MB at snapshot time, these paths are left out of
  > the snapshot. Never list anything holding work you could not regenerate.

  `<C>` is the configured cap in whole MiB.
* Under the same condition, `_resume_steps` (threaded through both
  `_detect_by_polling` and `_detect_by_notice` via a `regenerable` flag) gains
  one more step:

  > - If paths listed in `./regenerable.txt` are missing, regenerate them
  >   before you continue.

* A cap of `0` (`OPTIO_WORKDIR_CAPTURE_MAX_MB=0`, the feature off) or
  `supports_resume=False` renders neither text. No wrapper passes
  `capture_max_bytes` itself — each inherits the environment-driven default
  through `compose_instructions_file`, so `test_wrapper_compose_signature_is_uniform`
  (optio-agents-all) still holds.

## Dependency direction

Depends on `optio-host` and `optio-core`; consumed by every engine wrapper
(`optio-claudecode`, `optio-opencode`, `optio-codex`, `optio-cursor`,
`optio-grok`, `optio-kimicode`, `optio-antigravity`) and by
`optio-agents-all`.
