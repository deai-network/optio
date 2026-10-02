# Conversation todo progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conversation-mode todo writes set the process percent and status line, and the list is restored from the workdir snapshot on resume.

**Architecture:** `optio_agents.todos` owns the list, the progress rule, the JSON file, and a `TodoTracker` that subscribes to a conversation. Each engine has an extractor that returns a `TodoUpdate` or `None`. The session creates the tracker only in conversation mode, restores after the launch milestone, and saves before the workdir tar.

**Tech Stack:** Python 3.11+, pytest, existing `ctx.report_progress`, existing `Host.write_text` / `fetch_bytes_from_host`.

**Spec:** `docs/2026-09-28-conversation-todo-progress-design.md`

## Global Constraints

- Conversation mode only. Iframe and the agent's own web UI do not attach a tracker.
- Percent is 0–100: `(100 * completed + countable // 2) // countable`. Halves round up (`1/8` -> 13, `1/2` -> 50).
- Cancelled items are stored and excluded from both counts. Any other status is stored as `pending`.
- Countable total of zero does not call `report_progress`.
- Message is `Now working on Task <n>/<total>, "<text>"` for each in-progress row, joined with ` and `. No in-progress row: the message is null and only the percent moves. An empty list calls `report_progress(None)`. ACP plan `_meta.cancelled: true` (and the spelling `canceled`) is status `cancelled`.
- File is `{workdir}/.optio-todo.json`, full list, `active` omitted when unset. Written only when `supports_resume` and a conversation tracker exists. Write failure is logged and does not fail teardown. An empty list is written.
- Restore happens after the launch line that passes percent `None`. A valid file, including an empty list, is "loaded". Report only when the countable total is above zero.
- Arm before history replay. Replay updates the list and does not append a progress line per old change. After replay, one line announces the list as it stands. When the file loaded, a replayed todo tool may only correct a matching row, and a replayed plan is ignored.
- A bad payload returns `None`. Nothing is added to the process log. Launch does not fail on a missing or invalid file.
- Tool names match case-insensitively with underscores removed. Shared set: `todowrite`, `writetodo`. Codex `update_plan` is separate.
- No wall-clock sleeps in tests. No live agent.
- Do not change the process list or the snapshot document schema. The conversation view does show the list: a standing checklist above the transcript, fed by `widgetData.todos` (added after the first cut of this plan). `ctx.set_widget_todos` is documented in the root `AGENTS.md`.
- Update `packages/optio-agents/AGENTS.md` and add one sentence to each engine cheatsheet.

## Review Focus

- A streaming fragment (Claude `input_json_delta`, ACP `rawInput` that is not an object) must not replace the stored list. Task 3 and Task 4 pin this.
- `merge: true` must keep tasks the patch does not mention. Task 1 pins this.
- A saved list must survive a history replay that also contains todo events. Task 2 pins the arm-after-restore flag; Task 7's wiring puts `arm` after `replay_history` only when `restore` returned true.
- The launch line `"Conversation UI is live"` (percent `None`) must not be the last write when a saved list has a countable total. Wiring tasks call `restore` after that line.
- A raising extractor or garbage JSON must not escape the watcher or fail launch. Task 2 pins both.

---

### Task 1: TodoProgress

**Files:**
- Create: `packages/optio-agents/src/optio_agents/todos.py`
- Test: `packages/optio-agents/tests/test_todos.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `TodoItem(id: str, text: str, status: str, active: str | None = None)`
  - `TodoUpdate(items: list[TodoItem], merge: bool)`
  - `ProgressReport(percent: int, message: str)`
  - `TodoProgress.apply(update: TodoUpdate) -> ProgressReport | None`
  - `TodoProgress.report() -> ProgressReport | None`
  - `TodoProgress.to_json() -> str`
  - `TodoProgress.from_json(raw: str) -> TodoProgress | None`
  - `assign_ids(items: list[TodoItem]) -> list[TodoItem]`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-agents/tests/test_todos.py` with tests that import `TodoItem`, `TodoUpdate`, `TodoProgress` from `optio_agents.todos`:

- `test_all_pending_is_zero`: three pending -> percent 0, message null.
- `test_mixed_uses_active_text`: one completed, one in_progress with `active="Writing"`, one pending -> percent 33, message `"Writing"`.
- `test_several_in_progress_join`: two in_progress -> message `"A; B"`.
- `test_all_complete`: two completed -> percent 100, message null.
- `test_cancelled_excluded`: one completed, one cancelled -> percent 100, message null. The cancelled row is still in `progress.items`.
- `test_unknown_status_counts_as_pending`: status `"bogus"` stored as `"pending"`, percent 0, message null.
- `test_half_rounds_up`: 1 completed and 7 pending -> 13. 1 completed and 1 pending -> 50.
- `test_zero_countable_returns_none`: one cancelled only -> `apply` returns `None`.
- `test_replace_drops_old_ids`: apply a snapshot of one item, then a snapshot of a different id. Only the new id remains.
- `test_merge_patches_and_appends`: snapshot `[{id:a, pending}, {id:b, pending}]`, then merge `[{id:a, completed}, {text:"C", status:pending}]` with empty id. `a` is completed in place, `b` remains, `C` is appended.
- `test_json_round_trip`: items with and without `active`. `from_json(to_json())` equals the items. `active` key absent in the JSON when unset.
- `test_empty_json_loads`: `{"items":[]}` loads a progress whose `report()` is `None`.
- `test_garbage_json_is_none`: `"{"` and `{"items":[{}]}` and `"[]"` all return `None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && python -m pytest tests/test_todos.py -q`
Expected: FAIL, `optio_agents.todos` cannot be imported.

- [ ] **Step 3: Write minimal implementation**

`todos.py` implements the types and `TodoProgress` as specified. `assign_ids`: a blank id becomes `text`; a second row in the same update whose id (after that fill-in) is already used becomes `f"{id}#{index}"` with `index` the zero-based position in that update. `apply` on `merge` False replaces `self.items` with `assign_ids(update.items)`. On `merge` True, each incoming item updates the row with the same id in place, or is appended. A merge item with a blank id is appended with an id that does not collide (`text`, then `text#n`). `report` implements the progress rule. `to_json` emits `{"items":[...]}` with `active` omitted when `None`. `from_json` returns `None` unless the value is an object with an `items` list of objects that each have string `text`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && python -m pytest tests/test_todos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/todos.py packages/optio-agents/tests/test_todos.py
git commit -m "feat(optio-agents): todo list progress rule"
```

### Task 2: Tracker, payload parser, ACP extractor

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/todos.py`
- Modify: `packages/optio-agents/tests/test_todos.py`

**Interfaces:**
- Consumes: Task 1 types
- Produces:
  - `TODO_FILENAME = ".optio-todo.json"`
  - `TODO_TOOL_NAMES = frozenset({"todowrite", "writetodo"})`
  - `normalize_tool_name(name: str) -> str`
  - `todo_update_from_payload(payload: dict) -> TodoUpdate | None`
  - `extract_acp_todo(event: dict) -> TodoUpdate | None`
  - `TodoTracker(extract, ctx)` with `restore(host) -> bool`, `arm(conversation)`, `save(host)`, `observe(event)`

- [ ] **Step 1: Write the failing tests**

Append to `test_todos.py`:

- `test_payload_todos_replace`: `{todos:[{content,status,activeForm}], merge:false}` -> one item, `active` set, `merge` false.
- `test_payload_plan_array`: `{plan:[{step,status:in_progress}]}` -> text is the step, merge false.
- `test_payload_merge_flag`: `merge: true` is preserved.
- `test_payload_not_a_list`: `{todos:"nope"}` returns `None`.
- `test_normalize_name`: `TodoWrite`, `todo_write`, `write_todo` normalize into `TODO_TOOL_NAMES`. `update_plan` does not.
- `test_acp_plan_update`: a `session/update` notification whose `params.update.sessionUpdate` is `"plan"` and `entries` is `[{content,status,priority:"high"}]` returns a replace update. `priority` is not stored.
- `test_acp_tool_call_when_raw_input_object`: `sessionUpdate:"tool_call"`, `title:"todo_write"`, `rawInput:{todos:[...], merge:true}` returns a merge update.
- `test_acp_tool_call_streaming_raw_input`: `rawInput` a string or missing returns `None`.
- `test_acp_unrelated_event`: an `agent_message_chunk` returns `None`.
- `test_tracker_observe_reports`: fake ctx records `report_progress` calls. `observe` of an ACP plan event with one in_progress entry whose content is `"Write"` calls `report_progress(0, "Write")`.
- `test_tracker_none_extract_does_not_report`: unrelated event, no call.
- `test_tracker_raising_extract_does_not_escape`: extract raises `RuntimeError`. `observe` returns and does not call `report_progress`.
- `test_tracker_restore_reports_then_save_round_trip`: fake host. `restore` on a file with one in_progress item calls `report_progress` and returns `True`. `save` writes `TODO_FILENAME` via `write_text`. A second tracker `restore`s that text and does not depend on the first object.
- `test_tracker_missing_file`: `fetch_bytes_from_host` raises `FileNotFoundError`. `restore` returns `False` and does not report.
- `test_tracker_garbage_file`: file body `"{}"`. `restore` returns `False` and does not report.
- `test_tracker_empty_file_loads_without_report`: body `{"items":[]}`. `restore` returns `True` and does not report.
- `test_tracker_arm_is_idempotent`: `on_event` called once across two `arm` calls. The unsubscribe removes the handler.

Fake host:

```python
class FakeHost:
    def __init__(self, body=None):
        self.workdir = "/work"
        self.body = body
        self.written = None
    async def fetch_bytes_from_host(self, path):
        if self.body is None:
            raise FileNotFoundError(path)
        return self.body.encode()
    async def write_text(self, relpath, content):
        self.written = (relpath, content)
```

Fake conversation stores the handler passed to `on_event` and returns a lambda that clears it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && python -m pytest tests/test_todos.py -q -k "payload or acp or tracker or normalize"`
Expected: FAIL, names are not defined.

- [ ] **Step 3: Write minimal implementation**

`todo_update_from_payload` reads `todos` (objects with `content` or `text`, `status`, optional `id`, optional `activeForm` or `active`) or, if `todos` is absent, `plan` (objects with `step` or `text`, `status`). `merge` is true only when the payload's `merge` is `True`. Anything else returns `None`.

`extract_acp_todo` accepts a JSON-RPC `session/update` whose `params.update.sessionUpdate` is `"plan"` (entries via `todo_update_from_payload({"todos": entries})` after mapping `content` -> the parser's content field; pass entries as `todos` with `content` already the key the parser accepts) or `"tool_call"` / `"tool_call_update"` whose `title` or `kind` normalizes into `TODO_TOOL_NAMES` and whose `rawInput` is a dict. A string `rawInput` returns `None`.

`TodoTracker.observe` catches any exception from `extract` or `apply`, logs it, and does not report. `arm` subscribes once. `restore` uses `fetch_bytes_from_host(f"{workdir}/{TODO_FILENAME}")`. `FileNotFoundError` and a `from_json` of `None` return `False`. A loaded progress replaces `self.progress` and reports when `report()` is not `None`, then returns `True`. `save` calls `write_text(TODO_FILENAME, to_json())` and logs exceptions.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && python -m pytest tests/test_todos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/todos.py packages/optio-agents/tests/test_todos.py
git commit -m "feat(optio-agents): track conversation todos onto progress"
```

### Task 3: Claude extractor

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/todos.py`
- Test: `packages/optio-claudecode/tests/test_todos.py`

**Interfaces:**
- Consumes: `todo_update_from_payload`, `normalize_tool_name`, `TODO_TOOL_NAMES`
- Produces: `extract_claude_todo(event: dict) -> TodoUpdate | None`

- [ ] **Step 1: Write the failing test**

- Matching: `{"type":"assistant","message":{"content":[{"type":"tool_use","name":"TodoWrite","input":{"todos":[{"content":"Write","status":"in_progress","activeForm":"Writing"}]}}]}}` -> one item, text `Write`, active `Writing`, merge false.
- Unrelated: `{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}` -> `None`.
- Delta: `{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{"}}}` -> `None`.
- Malformed: tool_use named `TodoWrite` whose `input` is a list -> `None`.
- Other tool: `tool_use` named `Bash` -> `None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_todos.py -q`
Expected: FAIL, module missing.

- [ ] **Step 3: Write minimal implementation**

Only `type=="assistant"`. Walk `message.content`. A `tool_use` whose name normalizes into `TODO_TOOL_NAMES` and whose `input` is a dict is parsed with `todo_update_from_payload`. The first such block wins. Any other event returns `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-claudecode && python -m pytest tests/test_todos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/todos.py packages/optio-claudecode/tests/test_todos.py
git commit -m "feat(optio-claudecode): extract TodoWrite into a todo update"
```

### Task 4: Codex extractor

**Files:**
- Create: `packages/optio-codex/src/optio_codex/todos.py`
- Test: `packages/optio-codex/tests/test_todos.py`

**Interfaces:**
- Consumes: `todo_update_from_payload`
- Produces: `extract_codex_todo(event: dict) -> TodoUpdate | None`

- [ ] **Step 1: Write the failing test**

- `item/completed` whose `params.item` is `{"type":"update_plan","arguments":{"plan":[{"step":"Write","status":"in_progress"}]}}` -> text `Write`, merge false. Also accept `arguments` as a JSON string of that object.
- A function-call shaped item `{"type":"function_call","name":"update_plan","arguments":{...same plan...}}` -> the same update.
- `item/completed` of `commandExecution` -> `None`.
- `arguments` of `"{"` -> `None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-codex && python -m pytest tests/test_todos.py -q`
Expected: FAIL, module missing.

- [ ] **Step 3: Write minimal implementation**

Look at `params.item` when `method` is `item/completed`, otherwise the event itself if it is the item. Recognize name or type `update_plan`. Parse `arguments` from a dict or a JSON string. Hand `{"plan": ...}` to `todo_update_from_payload`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-codex && python -m pytest tests/test_todos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-codex/src/optio_codex/todos.py packages/optio-codex/tests/test_todos.py
git commit -m "feat(optio-codex): extract update_plan into a todo update"
```

### Task 5: OpenCode extractor

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/todos.py`
- Test: `packages/optio-opencode/tests/test_todos.py`

**Interfaces:**
- Consumes: `todo_update_from_payload`, `normalize_tool_name`, `TODO_TOOL_NAMES`
- Produces: `extract_opencode_todo(event: dict) -> TodoUpdate | None`

- [ ] **Step 1: Write the failing test**

Event shape is the unwrapped payload `{"id","type","properties"}`.

- `message.part.updated` with `properties.part.type=="tool"`, `part.tool=="todowrite"`, `part.state.input` a dict of `todos` -> the update.
- Same with `state.input` `{}` or missing -> `None`.
- A `message.part.updated` text part -> `None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-opencode && python -m pytest tests/test_todos.py -q`
Expected: FAIL, module missing.

- [ ] **Step 3: Write minimal implementation**

Require `type=="message.part.updated"`, `part.type=="tool"`, normalized tool name in `TODO_TOOL_NAMES`, and a non-empty dict `state.input`. Parse that dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-opencode && python -m pytest tests/test_todos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/todos.py packages/optio-opencode/tests/test_todos.py
git commit -m "feat(optio-opencode): extract todo tool parts into a todo update"
```

### Task 6: Antigravity extractor

**Files:**
- Create: `packages/optio-antigravity/src/optio_antigravity/todos.py`
- Test: `packages/optio-antigravity/tests/test_todos.py`

**Interfaces:**
- Consumes: `todo_update_from_payload`, `normalize_tool_name`, `TODO_TOOL_NAMES`
- Produces: `extract_antigravity_todo(event: dict) -> TodoUpdate | None`

- [ ] **Step 1: Write the failing test**

- `{"type":"PLANNER_RESPONSE","tool_calls":[{"name":"todo_write","args":{"todos":[{"content":"Write","status":"pending"}]}}]}` -> one pending item.
- `args` a JSON string of that object is accepted.
- A `tool_calls` entry named `list_dir` -> `None` when it is the only call.
- `{"type":"USER_INPUT"}` -> `None`.
- Two calls, the second a todo tool: the todo call wins.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-antigravity && python -m pytest tests/test_todos.py -q`
Expected: FAIL, module missing.

- [ ] **Step 3: Write minimal implementation**

Only `type=="PLANNER_RESPONSE"`. Walk `tool_calls`. A name in `TODO_TOOL_NAMES` whose `args` is a dict or a JSON object string is parsed. The first match wins.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-antigravity && python -m pytest tests/test_todos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/optio-antigravity/src/optio_antigravity/todos.py packages/optio-antigravity/tests/test_todos.py
git commit -m "feat(optio-antigravity): extract todo tool calls into a todo update"
```

### Task 7: Wire the seven conversation sessions

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`
- Modify: `packages/optio-codex/src/optio_codex/session.py`
- Modify: `packages/optio-grok/src/optio_grok/session.py`
- Modify: `packages/optio-cursor/src/optio_cursor/session.py`
- Modify: `packages/optio-kimicode/src/optio_kimicode/session.py`
- Modify: `packages/optio-opencode/src/optio_opencode/session.py`
- Modify: `packages/optio-antigravity/src/optio_antigravity/session.py`

**Interfaces:**
- Consumes: `TodoTracker`, each `extract_*_todo`. Grok, Cursor, and Kimi use `extract_acp_todo`.
- Produces: conversation mode calls `restore` / `arm` / `save` as below. No new public symbol.

- [ ] **Step 1: Write the failing test**

There is no new test module. The gate is the existing unit tests plus a grep that each session file contains `TodoTracker(` and `todo_tracker.save`.

- [ ] **Step 2: Confirm the gate is red**

Run: `grep -l TodoTracker packages/optio-*/src/*/session.py || true`
Expected: no matches.

- [ ] **Step 3: Wire each session**

Declare `todo_tracker = None` in the function that both starts the conversation and later calls `_capture_snapshot`. If those are different functions (opencode), declare it in the outer function and assign it from the nested body with `nonlocal`.

Immediately after the conversation-mode progress line that means the UI is up (`"Conversation UI is live"` or, for opencode, `f"{AGENT_INFO.name} conversation is live"`), and only in that branch:

```python
todo_tracker = TodoTracker(EXTRACT, ctx)
loaded = await todo_tracker.restore(host) if resuming else False
if not loaded:
    todo_tracker.arm(conversation)
```

Engines that replay history through `on_event` (codex `replay_history()`, grok, cursor, kimi, antigravity) keep their existing replay block. After that block:

```python
if loaded:
    todo_tracker.arm(conversation)
```

Claude and opencode do not replay through this watcher. They arm in the `if not loaded` branch and have no second arm. On a resume where `restore` returns true they still need the tracker armed for live events, so those two arm unconditionally after restore:

```python
todo_tracker = TodoTracker(EXTRACT, ctx)
if resuming:
    await todo_tracker.restore(host)
todo_tracker.arm(conversation)
```

In the finally that calls `_capture_snapshot`, before that call, when `config.supports_resume` and `todo_tracker is not None`:

```python
await todo_tracker.save(host)
```

Extractor imports:

| Package | Extractor |
|---|---|
| optio-claudecode | `extract_claude_todo` |
| optio-codex | `extract_codex_todo` |
| optio-grok, optio-cursor, optio-kimicode | `extract_acp_todo` |
| optio-opencode | `extract_opencode_todo` |
| optio-antigravity | `extract_antigravity_todo` |

Do not attach a tracker in iframe mode.

- [ ] **Step 4: Verify**

Run the four extractor test files and `packages/optio-agents/tests/test_todos.py`.
Run: `grep -l TodoTracker packages/optio-claudecode/src/optio_claudecode/session.py packages/optio-codex/src/optio_codex/session.py packages/optio-grok/src/optio_grok/session.py packages/optio-cursor/src/optio_cursor/session.py packages/optio-kimicode/src/optio_kimicode/session.py packages/optio-opencode/src/optio_opencode/session.py packages/optio-antigravity/src/optio_antigravity/session.py`
Expected: all seven paths, and the pytest runs PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py packages/optio-codex/src/optio_codex/session.py packages/optio-grok/src/optio_grok/session.py packages/optio-cursor/src/optio_cursor/session.py packages/optio-kimicode/src/optio_kimicode/session.py packages/optio-opencode/src/optio_opencode/session.py packages/optio-antigravity/src/optio_antigravity/session.py
git commit -m "feat: drive process progress from conversation todo writes"
```

### Task 8: Cheatsheets

**Files:**
- Modify: `packages/optio-agents/AGENTS.md`
- Modify: `packages/optio-claudecode/AGENTS.md`
- Modify: `packages/optio-codex/AGENTS.md`
- Modify: `packages/optio-grok/AGENTS.md`
- Modify: `packages/optio-cursor/AGENTS.md`
- Modify: `packages/optio-kimicode/AGENTS.md`
- Modify: `packages/optio-opencode/AGENTS.md`
- Modify: `packages/optio-antigravity/AGENTS.md`

**Interfaces:**
- Consumes: the behavior from Tasks 1–7
- Produces: documentation only

- [ ] **Step 1: Add the optio-agents section**

A short section `Conversation todos (optio_agents.todos)` stating: conversation mode feeds `ctx.report_progress` from the agent's todo tool; the rule (percent, message, cancelled); the file `.optio-todo.json`; resume loads it after the launch milestone and arms after history replay when the file was present. Point at the spec.

- [ ] **Step 2: One sentence per engine cheatsheet**

Name the event that engine recognizes (Claude `TodoWrite`, Codex `update_plan`, Grok/Cursor/Kimi ACP `plan` and `todo_write`, OpenCode tool part, Antigravity `PLANNER_RESPONSE` tool call).

- [ ] **Step 3: Commit**

```bash
git add packages/optio-agents/AGENTS.md packages/optio-claudecode/AGENTS.md packages/optio-codex/AGENTS.md packages/optio-grok/AGENTS.md packages/optio-cursor/AGENTS.md packages/optio-kimicode/AGENTS.md packages/optio-opencode/AGENTS.md packages/optio-antigravity/AGENTS.md
git commit -m "docs: note conversation todo progress in the cheatsheets"
```
