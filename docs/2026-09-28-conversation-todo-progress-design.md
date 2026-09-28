# Conversation todo writes drive process progress (design)

Date: 2026-09-28. Status: agreed in conversation, pending spec review.

## Problem

In conversation mode the agent already keeps a todo list, through its own
tool. Claude calls `TodoWrite`. Codex calls `update_plan`. Grok, Cursor, and
Kimi speak ACP, which has a `plan` session update and may also call
`todo_write`. OpenCode and Antigravity call a tool under one of those names
when they have one.

That list never reaches the process. The process row already shows a
percent and a status line, fed by `ctx.report_progress`. Launch milestones
and `STATUS:` lines use it. A todo write does not.

Terminal mode and the agent's own web UI are out of this design. They
already show the agent's own list, and `STATUS:` remains the way those
modes set the bar.

## Outcome

A conversation-mode session watches its event stream. When the agent writes
its todo list, the process percent becomes completed-over-total, and the
status line becomes the in-progress task names. The chat transcript is
unchanged. The list is saved with the resume snapshot and restored onto
the bar when the conversation comes back.

Live runs will show where an engine's real frame differs from the payload
below. An extractor grows a clause for that envelope. The payload rule and
the progress rule stay.

## Pieces

`optio_agents.todos` holds the engine-neutral types and the progress rule.
Each wrapper owns an extractor that reads its own events and returns a
`TodoUpdate` or `None`. The session subscribes the watcher only when
`mode="conversation"`.

```
native event
  -> extractor -> TodoUpdate | None
  -> TodoProgress.apply
  -> ctx.report_progress(percent, message)
  -> existing process bar and status line
```

`TodoItem` is `id`, `text`, `status`, and optional `active`. Status is
`pending`, `in_progress`, `completed`, or `cancelled`. Any other status is
stored as `pending`.

`TodoUpdate` is the items plus `merge`. `merge` is true only when the tool
payload says so. A snapshot replaces the stored list. A merge patches by
`id`. An item in a merge that has no id is appended, with an id derived
from its text and position so a later snapshot can still tell the rows
apart.

`TodoProgress` keeps the list for the life of the conversation. `apply`
returns the percent and the message. The watcher calls
`ctx.report_progress` with both. `report_progress` stays the only writer.
Other callers (launch lines, `STATUS:`, snapshot milestones) still
overwrite the bar until the next todo update.

The conversation view is not modified. A todo tool row that the view
already draws stays. A signal the view ignores today (ACP `plan`, Codex
`update_plan`) stays ignored in the transcript and is still read by the
watcher.

## Progress rule

Cancelled items are in the saved list and out of both counts. The
countable total is everything else.

Percent is the integer nearest to `100 * completed / countable`, halves
rounding up: `(100 * completed + countable // 2) // countable`. The scale
is 0–100, the same scale a `STATUS: 50%` line uses. When the countable
total is zero, `apply` returns no percent and no message, and the watcher
does not call `report_progress`. The previous bar stays.

The message is the in-progress rows, joined with `"; "`. A row's shown
text is `active` when set, otherwise `text`. Claude's `activeForm` is
`active`. When nothing is in progress the message is `"N of M done"`.

## Resume

The list is a file in the workdir, `.optio-todo.json`:

```json
{"items":[{"id":"1","text":"Write the parser","status":"in_progress","active":"Writing the parser"}]}
```

`active` is omitted when unset. The file is the full list, not a patch.

It is written at teardown, in the same window as the other resume files,
before the workdir tar is taken, and only when `supports_resume` is set
and a conversation was started. Write failure is logged and does not fail
teardown, the same way a conversation-buffer write failure does not. An
empty list is written too, so a cleared list does not leave the previous
run's file in the next snapshot. Default workdir excludes do not match
this name.

On resume the workdir restore brings the file back. After the launch
milestones, including the `"Conversation UI is live"` line (that call
passes percent `None` and would otherwise leave the bar indeterminate):

- File present and valid: load it into `TodoProgress`. If the loaded
  list has a countable total above zero, `report_progress` once, with
  the same percent and message a live update would produce. An empty
  list loads and does not report. Subscribe the watcher after history
  replay, so a shortened replay cannot replace the saved list.
- File absent, unreadable, or not valid JSON: start from an empty list
  and do not fail the launch. Subscribe the watcher before history
  replay, so a replayed todo write can still set the bar. Claude does
  not replay through this watcher, so an older Claude snapshot waits
  for the next write.

A snapshot taken before this change has no file. That is the absent case.

## Extractors

Names match case-insensitively with underscores removed, so `TodoWrite`
and `todo_write` are the same name. The shared set is `todowrite` and
`writetodo`. Codex `update_plan` is not in that set. Its payload uses
`step` rather than `content`, and the Codex extractor handles it alone.

An id missing from a snapshot row is the row's text. A later row in the
same update that reuses that text gets `#` plus its zero-based index.

A payload that is not a list, or whose entries are not objects, returns
`None`. The bar stays. Nothing is added to the process log.

| Engine | Event | Payload | Update |
|---|---|---|---|
| Claude Code | Finished assistant `tool_use` named `TodoWrite`. Streaming `input_json_delta` fragments return `None`. | `todos[]` of `{content, status, activeForm?}` | Replace |
| Codex | Raw event the conversation already fans out: an `item/completed` whose item is `update_plan`, or a function-call item of that name. | `plan[]` of `{step, status}`. `step` is `text`. | Replace |
| Grok, Cursor, Kimi | ACP `session/update` with `sessionUpdate: "plan"`. | `entries[]` of `{content, status}`. `priority` is ignored. | Replace |
| Grok, Cursor, Kimi | `tool_call` or `tool_call_update` whose name is in the shared set, once `rawInput` is an object. A call still streaming arguments as text returns `None`. | `todos[]` of `{id, content, status}`, plus `merge` | Replace, or patch by id when `merge` is true |
| OpenCode | Tool part whose name is in the shared set, once `state.input` is an object. | That `todos` payload, or `plan[]` of `{step, status}` | Same merge rule |
| Antigravity | `PLANNER_RESPONSE` tool call whose name is in the shared set. | `args` carrying that payload | Same merge rule |

Shell background tasks (`task_backgrounded`, `background_tasks`) are not
this list.

No captured `update_plan`, OpenCode todo, or Antigravity todo frame lives
in the repo. Those three extractors are pinned by a synthetic payload of
the shape in the table. A live frame with a different envelope gets a
clause in that engine's extractor.

Grok, Cursor, and Kimi share one ACP extractor in `optio_agents.todos`.
Claude, Codex, OpenCode, and Antigravity each keep an extractor next to
their conversation module. A shared parser for a `todos` array or a
`plan` array lives in `optio_agents.todos`. Claude (for `activeForm`),
the ACP tool-call path, OpenCode, and Antigravity call it. Codex does
not: `update_plan` is its own shape.

## What stays as it is

- Iframe mode and the agent's own web UI.
- The `STATUS:` keyword and every existing `report_progress` call.
- The conversation UI reducers, the process list, and the process detail
  view. They already render percent and message.
- The snapshot document schema. The file rides inside the workdir tar.
- `supports_resume` false. The bar still tracks todos during the run.
  The file is not written.

## Tests

Unit tests, no wall-clock waits, no live agent.

- Progress rule: all pending, mixed, several in progress, all complete,
  cancelled excluded, active text preferred, zero countable returns no
  report, half-percent rounds up (`1/8` -> 13, `1/2` -> 50).
- Merge: replace drops old ids; merge patches by id and appends an item
  with no id.
- Each extractor: one matching event, one unrelated event, one malformed
  payload. Claude: an `input_json_delta` returns `None`. ACP: a
  `tool_call` whose `rawInput` is not yet an object returns `None`.
- Dump and load of `.optio-todo.json`, including an empty list. Garbage
  JSON loads as no list.
- Watcher: a fake conversation and a fake `report_progress`. A matching
  event reports. A `None` extract does not. Restoring a list reports
  once before any event. A missing file does not report.

`packages/optio-agents/AGENTS.md` gains a short section for the module,
the progress rule, and `.optio-todo.json`. Engine cheatsheets gain one
sentence each naming the event they recognize. The root `AGENTS.md` does
not change: the process API is unchanged.

## Out of scope

A checklist inside the conversation transcript. Scraping the terminal or
the agent's web UI. Teaching the model a new `optio.log` keyword.
Changing percent from the browser. Hiding the todo tool row. Rebuilding
the list by scanning a resumed transcript when the file is present.
