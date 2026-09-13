# Conversation steering: send when ready, interrupt and send, interrupts (design)

Date: 2026-09-13. Status: approved by the owner section by section (1-5).

This merges two designs: the steer UI agreed on 2026-07-11 (per-agent steer mode,
"Send when ready / Interrupt & send", ⌘/Ctrl-Enter, a combined `/steer` endpoint,
an antigravity send queue; never written down, never built; its only trace is the
"Out of Scope (follow-up plan)" paragraph in excavator
`docs/superpowers/plans/2026-07-11-vultus-extraction-markdown-unify.md`) and the
rendering design worked out on 2026-09-13 from a real mis-rendered session.

## Problem

- **A steer splits and repeats the answer (Claude Code).** A message sent while an
  answer streams makes the view close the half-streamed bubble and open a new one;
  the block's final event then writes the whole answer into the new bubble.
- **Replay puts the steer in the wrong place.** The CLI echoes a mid-turn message
  only when it absorbs it (after the next tool result), and the reducer pulls that
  echo above the answer it interrupted.
- **Interrupts render as noise.** Claude Code's `[Request interrupted by user]` text
  becomes a user bubble (often above the partial answer), its `error_during_execution`
  result becomes a red "The agent reported an error.", and a killed tool shows ✗
  failed or a mislabeled background job.
- **Busy sends differ per agent and the UI doesn't say.** Some agents take the
  message at the next step, some after the turn, one cancels the turn, one rejects
  it, one starts a second process in parallel. The UI offers a plain Send and an
  Interrupt that sends nothing.

## Measurements

What a native send does while the agent works, and how an interrupt shows up.
Capabilities are recorded per **agent + model**; every recording's header carries
the agent version and model.

| agent + model | busy send (native) | interrupt signal | tool left running | source |
|---|---|---|---|---|
| Claude Code CLI 2.1.270 | taken at the next tool result, same turn; echoed as a `user` event (`isReplay`) whose timestamp is the send time | `user` text `[Request interrupted by user]` / `… for tool use]`, then `result` `error_during_execution` with `terminal_reason` `aborted_streaming` / `aborted_tools` | tool gets an `is_error` result "The user doesn't want to proceed…"; a foreground task also gets `task_notification status=stopped` | 2026-09-13 synthetic runs |
| codex 0.154.0 + gpt-5.6-luna | `turn/start` while busy is merged into the running turn (same turn id); delivered as a `userMessage` item at the next tool result | `turn/completed status=interrupted` | command item never gets `item/completed` | 2026-09-13, spark |
| grok 0.2.93 + grok-4.6 | own queue: `_x.ai/queue/changed` carries the entry text; runs after the whole turn; echoed live as `user_message_chunk` | `turn_completed` / `prompt_complete` / prompt response with `stop_reason` `cancelled` | tool stays `in_progress` | 2026-09-13, spark, through optio's own `GrokConversation` |
| cursor | cancels the running turn (`stopReason: cancelled`) and starts a new one | `stopReason: cancelled` | — | 2026-07-11 |
| kimicode | rejected while busy (`Cannot launch a new turn`); optio serializes client-side today | `stopReason: cancelled` | — | 2026-07-11 |
| opencode | not measured | `session.error`, then `session.status idle` | — | 2026-07-11 (interrupt only) |
| antigravity | not measured; optio today starts a second `agy -p` in parallel | none (optio kills the process) | — | 2026-07-11 (model overloaded) |

The July tiers classified Claude Code as "plain send interrupts". That was wrong:
it queues until the next tool result. "Interrupt, then send" preserves the
conversation context (proven in July for grok, opencode and kimicode).

Recordings: `superego:/tmp/steer-interrupt-test/` (Claude Code synthetic runs,
`codex-2026-09-13/`, `grok-2026-09-13/`, `july-probe/`); originals of July on
`spark:/tmp/steer-*.jsonl`. They live in `/tmp`; the implementation preserves the
parts it uses as test fixtures.

## Decisions

1. **Uniform UI.** Captions and options never depend on the agent. While the agent
   is busy the operator always gets both actions: **Send when ready** and
   **Interrupt and send**. What depends on the agent is the scaffolding optio adds
   around it.
2. **Send when ready** is delivered at the latest when the current turn ends, earlier
   if the agent can take it between steps (Claude Code, codex).
3. **Interrupt and send** (and **Send now** on a queued bubble) stops the running
   step and delivers everything queued plus the new message, in the order written.
4. **No edit or cancel** of a queued message, on any agent.
5. The busy input bar uses vultus's multi-action button. Its "selection sticks"
   behaviour becomes optional (on by default); the conversation UI opts out with
   `keepOriginalDefault`, so the main half always returns to Send when ready.

## 1. What the operator sees (every agent)

- **Idle:** one **Send** button; Enter sends.
- **Busy:** vultus multi-action button, fixed width: main half **Send when ready**
  (Enter), menu **Interrupt and send** (⌘/Ctrl-Enter). The red **Interrupt** stays:
  it stops and sends nothing. The placeholder names both keys.
- **Send when ready:** until the agent takes it, the message is a muted queued
  bubble at the bottom, "Queued — the agent reads it when ready", with a
  **Send now** link. When taken, it becomes a normal bubble at the point where the
  agent took it (after the tool row it came with, or at the start of the next turn).
  Identical live and after a reload.
- **Interrupt and send / Send now:** the running step stops; everything queued plus
  the new message goes now, in order.
- **Any interrupt:** the cut-off answer keeps its text and gets a jagged bottom
  edge; running tool rows show ⏹ stopped; one muted "⏹ Interrupted by you" row; no
  error item.

## 2. Scaffolding

### Capability

Each wrapper declares `busy_send` per agent, with per-model overrides:

- `joins-next-step` — taken at the next tool result, same turn (Claude Code, codex)
- `queues-to-end` — the agent's own queue, runs after the turn (grok)
- `cuts-in` — cancels the running turn and starts a new one (cursor)
- `rejected` — refused while busy (kimicode)
- `unsafe` — no defined behaviour (antigravity: parallel processes)

A model without its own measurement inherits its agent's value (measured with the
model named in the table); an unmeasured agent gets `unsafe`: always correct, only
slower. Today that covers antigravity and opencode. A model override is added only
when a recording shows that model behaving differently.

### Shared scaffolding (new module in optio-agents, used by every wrapper)

- `send_when_ready(text)`
  - idle → plain send
  - `joins-next-step` / `queues-to-end` → plain send (the agent holds it)
  - `cuts-in` / `rejected` / `unsafe` → optio's own queue, flushed in order when the
    turn ends
- `interrupt_and_send(text)`
  - `cuts-in` → plain send (native), carrying optio's held messages with it
  - otherwise → interrupt, then wait for the turn-end signal (at most 15 s; after
    that, send anyway and log it); the agent's own queue delivers first, then optio's
    held messages and the new text go
- `interrupt()` → stop only (unchanged)

When optio delivers messages it holds (a turn-end flush, or an interrupt and send),
it joins them into **one prompt**, in the order written, separated by a blank line,
and reports all their ids in one `x-optio-taken`. Sending them one by one would make
a `cuts-in` agent cancel each previous one.

It replaces kimicode's private send queue and gives antigravity the queue it lacks.

### Endpoints (each wrapper's conversation listener)

- `POST /send` → `send_when_ready` (route unchanged), returns `{id, queued}`
- `POST /steer` → `interrupt_and_send` (new, atomic), returns `{id}`; empty text
  means "deliver what is queued" (Send now)
- `POST /interrupt` → stop only (unchanged)

## 3. Events

### Synthetic events (written by the scaffolding into the wrapper's event stream; buffered, so a reload replays them)

- `x-optio-queued {id, text}` — a Send when ready that arrived while the agent was
  busy, whether the agent or optio holds it. A reload in the middle of a queue still
  shows the queued bubble.
- `x-optio-taken {ids}` — optio dispatched messages it held itself (one combined
  prompt). Needed because the ACP agents (cursor, kimicode) do not echo live prompts.
- `x-optio-interrupt {by: "user"}` — optio sent an interrupt (Interrupt, Interrupt
  and send, Send now). The single source of the "Interrupted by you" row and the
  jagged edge, including antigravity, whose process kill leaves no native marker.

### Native signals each reducer maps

| | taken echo | turn cancelled | tool left running |
|---|---|---|---|
| Claude Code | `user` echo after a `tool_result` (`isReplay`) | `[Request interrupted…]` text + `terminal_reason` | freeze it as stopped |
| codex | `userMessage` item | `turn/completed status=interrupted` | command item never completes |
| grok | `user_message_chunk` (live) | `stop_reason: cancelled` | tool stays `in_progress` |
| cursor, kimicode | none → `x-optio-taken` | `stopReason: cancelled` | — |
| antigravity, opencode | `x-optio-taken` | from `x-optio-interrupt` | — |

### Matching

A queued bubble becomes taken by id (`x-optio-taken`) or, for native queues, by the
first matching echo (FIFO by text, as today's optimistic-echo confirmation does).
The view keeps its instant local echo and learns the message's id from the `/send`
response; the listener's `x-optio-queued` with the same id then supersedes the local
echo instead of adding a second bubble.

## 4. Rendering

### Shared model (additive; engines that never set a field are unchanged)

- user item: `queued?`, `queueId?`
- assistant item: `interrupted?` → jagged bottom edge
- tool item: status `stopped` (exists)
- activity row: "⏹ Interrupted by you"

### Placement (the same rule live and on reload)

Queued bubbles stay pinned at the bottom and do not count as newer content, so a
streaming answer never splits or repeats. When taken, the bubble moves to the take
point: after the tool row it came with (mid-turn), or at the start of the new turn.
The Claude Code reducer stops pulling mid-turn echoes above the in-flight answer.

### On `x-optio-interrupt`

Close the in-flight answer keeping its text and mark it interrupted; running tool
rows become stopped; add one "Interrupted by you" row; swallow the agent's own
cancel artefacts for that turn (Claude Code's `[Request interrupted…]` text and its
error result, opencode's `session.error`, cancelled stop reasons). An interrupt the
operator did not cause still shows as an error.

### View

- queued bubble: user colours, dashed border, muted, "Queued — the agent reads it
  when ready", **Send now** (`POST /steer` with no new text)
- interrupted answer: jagged bottom edge
- busy input bar: vultus multi-action button `[Send when ready | Interrupt and send]`
  with `keepOriginalDefault`, Enter / ⌘/Ctrl-Enter, plus the red Interrupt
- idle: single Send

## 5. Dependencies, stages, tests, docs

### Dependency first

unitas vultus-antd: `CombinedActionButton` gets `keepOriginalDefault` (selection
sticks unless set). Folded into the unpublished 0.1.1; optio-conversation-ui then
depends on `vultus-antd ^0.1.1`.

### Stages (each usable on its own; the branch starts from optio main)

1. Shared model, view, scaffolding and `/steer`, wired for Claude Code (fixes the
   split/repeat bug).
2. codex and grok (native queues; manual interrupt), plus one cheap codex check: is
   a joined-but-not-yet-taken message kept or dropped when the turn is interrupted?
3. cursor (optio queue), kimicode (the shared queue replaces its private one),
   antigravity (optio queue + kill), opencode (conservative `unsafe`; its native
   cancel error swallowed after `x-optio-interrupt`).

Later, when measured: antigravity (needs `agy` signed in on spark; free tier, so the
cheapest model and few requests) and opencode natives, which may relax their
capability.

### Tests

- reducer fixtures trimmed from the real recordings (Claude Code, codex, grok from
  2026-09-13; cursor, kimicode, opencode, antigravity from July), each asserting
  that live and replay placement are equal
- scaffolding unit tests per capability, with fake conversations
- listener `/steer` tests
- view tests: queued bubble, Send now, jagged edge, the button and its keys
- no wall-clock dependence (optio AGENTS.md)

### Docs

The capability declaration in `docs/writing-agent-wrappers.md`; AGENTS.md for
optio-agents (new module), the listeners' `/steer` route, and optio-conversation-ui.

## Out of scope

Editing or cancelling queued messages; per-agent captions; measuring opencode's
native busy send (postponed by the owner).
