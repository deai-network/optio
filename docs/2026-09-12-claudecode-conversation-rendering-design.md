# Claude Code conversation rendering: narration, tool rows, background tasks

Date: 2026-09-12. Status: design approved by the owner; not yet implemented.

Scope: optio-claudecode in `mode="conversation"` (headless `claude -p` with
stream-json in and out) and the widget that renders it
(`optio-conversation-ui`). The iframe/TUI mode is out of scope.

## Problem

Three things the operator should see in a Claude Code conversation are missing
or garbled.

1. **Narration is invisible.** Since CLI 2.1.267 thinking is on for Opus 5 (Fable
   5.1 since 2.1.259). The model then writes its between-tool narration ("I'll
   check the VPN, then run the harness") as a *thinking update*: a thinking block
   that carries text. The claudecode reducer
   (`optio-conversation-ui/src/claudecode/events.ts`) handles only `text` and
   `tool_use` blocks and only `text_delta` partials, so these blocks are dropped
   whatever `thinking_verbosity` says.
2. **Tool rows do not mean what the verbosity levels promise.** The reducer keeps
   only the in-flight tool row (`withoutTools` drops the previous one on every new
   reply, tool or permission card), never reads `tool_result`, and never sets a
   status. So every level behaves roughly the same: one transient row, always
   "running", never a result. `tool_verbosity="verbose"` does not show results or
   history.
3. **Background tasks are either invisible or raw XML.** The CLI reports
   background commands as `system` events (`task_started`, `task_updated`,
   `task_notification`, `background_tasks_changed`), which the reducer ignores
   (`default:` branch). When a completion is injected as a user turn, its
   `<task-notification>…</task-notification>` text renders as a user bubble, as if
   the operator had typed it.

## Evidence

**Narration arrives as thinking in our mode.** The excavator analysis session
(Opus 5, CLI 2.1.268/2.1.269) delivered 16 text-bearing thinking blocks, 29 empty
ones, 11 text blocks and 66 tool calls in its replay buffer; over the whole
transcript 56 text-bearing thinking blocks against 51 text blocks. The
text-bearing ones are first-person narration followed by a tool call.

**Controlled runs** (claude 2.1.269, `claude-opus-5`, launched exactly as optio
launches it: `-p --input-format stream-json --output-format stream-json
--verbose --include-partial-messages --replay-user-messages`, six-step
"tell me, then run it" task; raw data in `superego:/tmp/thinksum-test/`):

| Setting | Runs | Text-bearing thinking blocks |
|---|---|---|
| default | 3 | 1, 2, 0 (each the 2nd thinking block of a response: `T0 T+ tool`) |
| `showThinkingSummaries: true` via `--settings` | 3 | 0, 0, 0 |
| `showThinkingSummaries: true` via project settings | 2 | 0, 0 |

- With summaries on, no thinking text arrived at all: no summaries and no
  narration (0 `thinking_delta` characters).
- Streamed `content_block_start` for thinking blocks has the same keys
  (`signature`, `thinking`, `type`) in every arm; nothing marks a block as
  narration or reasoning. Signature sizes overlap (empty 1016-48472 bytes,
  text 1244-3472), so size is not a discriminator either.
- Conclusion: in this mode every text-bearing thinking block is narration, and
  reasoning summaries are not obtainable.

**Wire shapes** (same runs):

- `tool_use` block: `{type, id, name, input, caller}`; its event carries
  `timestamp` and `parent_tool_use_id`.
- Tool result: a `user` event whose content holds `{type: "tool_result",
  tool_use_id, is_error, content}` (`content` is a string or a list of blocks),
  plus a top-level `tool_use_result` and `timestamp`.
- Background task: `system/task_started {task_id, tool_use_id, description,
  is_backgrounded, task_type}`, `system/task_updated {task_id, patch: {status,
  end_time}}`, `system/task_notification {task_id, tool_use_id, status
  ("completed" | "failed"), output_file, summary}`. The analysis buffer held 7
  `task_started` and 7 `task_notification` events.
- Injected notification turns appear in the transcript as `user` records with
  `origin: {kind: "task-notification"}` and `promptSource: "sdk"`; the text is the
  `<task-notification>` XML (`task-id`, `tool-use-id`, `output-file`, `status`,
  `summary`).

Background: the AoE report on the same CLI change
(`superego:/tmp/never-mentioned.md`).

## Decisions

1. Narration renders exactly like the agent's ordinary replies, with no marker.
2. Empty thinking blocks are dropped.
3. `thinking_verbosity` has nothing to control for Claude Code in conversation
   mode; it keeps working for the other engines.
4. optio never enables `showThinkingSummaries` for Claude Code: in `-p` mode it
   removes the narration.
5. optio pins `CLAUDE_CODE_THINKING_DISPLAY_UPDATES=1` in the conversation launch
   env, so a CLI default change cannot silently drop the narration channel.
6. Claude Code tool rows become real rows: persistent, with status and a result
   preview, so the four `tool_verbosity` levels behave as documented.
7. A running tool row shows a live elapsed-time counter; a finished row keeps its
   final duration.
8. Background tasks update their Bash tool row; injected notification turns never
   render as user bubbles.
9. The fix lives in the widget's claudecode reducer. The engine keeps passing raw
   events through untouched (as `optio_claudecode/conversation.py` promises), so
   replay buffers of existing sessions render correctly after the update.

Not in scope: the other engines' reducers, the iframe/TUI mode, reasoning
summaries, a live tool-verbosity control, and excavator's own settings (tracked
separately: the analysis task moves to `tool_verbosity="description-only"`).

## Design

### 1. Narration as replies (`claudecode/events.ts`)

- A `thinking` block with non-empty `thinking` text is treated like a `text`
  block. Empty thinking blocks and `signature_delta` partials are ignored.
- `thinking_delta` partials stream into the pending bubble like `text_delta`.
- **Several text-bearing blocks in one message render in one bubble**, in order,
  separated by a blank line. Each block is one *part* of the bubble:
  - the first non-empty delta opens a part (adding the separator first if the
    bubble already has text);
  - the block's final `assistant` event replaces only the open part's text and
    closes it;
  - an `assistant` event with no open part appends a new part. This is the
    replay case: the replay buffer holds no `stream_event`s.

  A single-text-block message behaves exactly as today; live and replayed
  sessions render the same bubble.
- **`result` keeps narration.** `finalizePending` replaces the pending bubble's
  text with the result text only when the bubble does not already end with it.
  Otherwise narration earlier in the same message would be overwritten at the end
  of the turn.
- The part bookkeeping is reducer-private state on the pending assistant item
  (an optional field), not rendered.

### 2. `thinking_verbosity` and summaries

- `ClaudeCodeTaskConfig.thinking_verbosity` (`optio_claudecode/types.py`) is
  documented as having no effect in conversation mode: narration always renders as
  replies, and reasoning is not available in `-p` mode.
- A comment next to the conversation launch records that `showThinkingSummaries`
  must stay off, with the evidence above.

### 3. Env pin (`optio_claudecode/host_actions.py`)

`conversation_launch_env` adds `"CLAUDE_CODE_THINKING_DISPLAY_UPDATES": "1"`
before `**extra`, so a caller's explicit `extra_env` can still override it. The
iframe/tmux launch is unchanged.

### 4. Real tool rows (`claudecode/events.ts`, `chat.ts`)

- A `tool_use` block becomes a tool row that stays in the history. The claudecode
  reducer stops calling `withoutTools` on new replies, tool calls, permission
  requests and `x-optio-closed`. (The close-time trimming existed so a transient
  "echo DONE" row would not linger above the "conversation ended" divider; with
  real rows that is ordinary, finished history.)
- The shared tool item gains optional fields: `callId` (the `tool_use` `id`),
  `startedAt` and `endedAt` (epoch ms), and `background` (boolean).
- A `tool_result` in a later `user` event, matched by `tool_use_id`:
  - sets `status: "done"`, or `"failed"` when `is_error` is true;
  - sets `preview` to the result text (string content, or the text of its text
    blocks), trimmed to 2000 characters;
  - sets `endedAt` from the event `timestamp`.

  A `tool_result` for an unknown id is ignored. A user event that carries only
  tool results produces no user bubble (as today).
- Bubble and user-echo placement are unchanged: tool rows already count as
  "not newer content" in `isTail`, so a user echo still lands above the answer and
  its tool rows.
- Events with a non-null `parent_tool_use_id` (sub-agent calls) are handled the
  same way, without nesting.
- The generic `ConversationView` rendering already implements the levels:
  `silent` hides rows, `description-while-active` hides finished rows,
  `description-only` keeps one line per call, `verbose` shows args and result and
  collapses when finished. No change is needed there beyond section 5.

### 5. Elapsed-time counter (`ConversationView.tsx`)

- A tool row with `startedAt` shows its elapsed time after the summary, e.g.
  `⟳ Bash: ./harness.sh --lean · 1m 12s`, re-rendered once per second while any
  running row is visible (one interval for the view, cleared when none runs).
- `startedAt` comes from the `tool_use` event's `timestamp`, falling back to the
  arrival time; elapsed is clamped at 0 against clock skew between worker and
  browser.
- The counter stops, and the row keeps the final duration
  (`✓ Bash: … · 3m 04s`), when:
  - the row gets its result or background completion (`endedAt`);
  - the turn ends (`result`): running rows that are not `background` freeze at
    the result's timestamp but keep their glyph. Background rows keep counting:
    their task usually outlives the turn that started it;
  - the conversation closes (`x-optio-closed`): every running row freezes,
    background rows included.
- Duration format: `Ns` under a minute, `Mm SSs` under an hour, `Hh MMm` above.
- Only claudecode rows get `startedAt` in this change; other engines show no
  counter until their reducers provide one.

### 6. Background tasks (`claudecode/events.ts`)

- `system/task_started` with `is_backgrounded` marks the row whose `callId`
  equals `tool_use_id` as `background: true`. For a background row, the immediate
  `tool_result` ("running in background with ID …") does not finish it: status
  stays running and the counter keeps going.
- `system/task_notification` finishes that row: `status` "completed" -> `done`,
  "failed" -> `failed`; `preview` is the summary; `endedAt` is the event
  timestamp, or the arrival time when absent.
- A `user` event whose text is a `<task-notification>` element, or whose
  `origin.kind` is `"task-notification"`, never becomes a user bubble. The reducer
  parses `task-id`, `tool-use-id`, `status` and `summary` from it and applies them
  exactly like `system/task_notification`.
- Each `task_id` is applied once, whichever route delivers it first. The reducer
  keeps the set of finished task ids.
- When no matching row exists (verbosity `silent`, or a replay that lacks the
  Bash call), a muted activity row is added instead:
  `✓ Background task finished: <summary>` (or `✗ … failed: …`). With `silent`,
  that row is still shown: a finished background job is an event, not tool noise.

## Edge cases

- Missing `timestamp` on any event: fall back to arrival time.
- Duplicate `assistant` events for the same block (live final event after
  deltas): handled by the open-part replace.
- A message whose only thinking block is empty and whose next block is a
  `tool_use`: no bubble, just the tool row.
- A stale pending bubble from a buffer captured mid-turn keeps today's handling.

## Testing

Written first, in the existing suites.

- `src/__tests__/claudecode-events.test.ts`:
  - text-bearing thinking becomes an assistant bubble; empty thinking does not;
  - `thinking_delta` streams into the bubble and the final thinking event does
    not duplicate it;
  - narration + text in one message: one bubble with both parts, live and replay;
  - `result` keeps narration that precedes the result text in the same message;
  - tool rows persist across later text and tool calls;
  - a `tool_result` sets `done`/`failed`, `preview` and `endedAt`; an unknown
    `tool_use_id` is ignored;
  - `startedAt` from the event timestamp, fallback when absent;
  - background: `task_started` + immediate `tool_result` stays running;
    `system/task_notification` finishes it with the summary; an injected
    `<task-notification>` user turn does the same and creates no user bubble;
    both routes for one task apply once; a failed status; no matching row yields
    the activity row;
  - `result` freezes running non-background rows but not background ones;
    `x-optio-closed` freezes all running rows and keeps every tool row;
  - every existing case still passes.
- `src/__tests__/claudecode-widget.test.tsx` / `conversation-view.test.tsx`:
  - narration shows with `thinkingVerbosity="hidden"`;
  - the four tool verbosity levels render as documented for claudecode rows;
  - the counter ticks while running (fake timers), stops at `endedAt`, and a
    finished row shows its duration.
- optio-claudecode (pytest): `conversation_launch_env` sets
  `CLAUDE_CODE_THINKING_DISPLAY_UPDATES=1`, and `extra_env` can override it.

## Rollout

- Implemented and tested in `superego:~/deai/optio`; nothing is copied to the
  excavator host by hand.
- Publishing is a separate step: `optio-conversation-ui` (npm) and
  `optio-claudecode` (PyPI) per `docs/release-cookbook.md`. Dev setups that
  cross-link a sibling optio checkout pick the change up after a pull and a build.
- Existing sessions benefit on their next render: their replay buffers hold the
  raw events.
