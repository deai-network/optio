# Custom session controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Task-defined `OneOfSegmented` selectors in the conversation view whose options switch model / effort / permission mode live; Excavator's data chat gets "Model: Fast | Extensive".

**Architecture:** optio-agents gains a shared `CustomControlsConfigMixin`; optio-claudecode applies picks (custom and built-in model/effort) live with `apply_flag_settings` instead of restarting, sends `customControls` with a derived selection, and accepts `custom:<id>` on `/control`; optio-conversation-ui renders them; Excavator's chat task defines the control.

**Tech Stack:** Python 3.13 (pytest), TypeScript/React (vitest), antd + vultus-antd `OneOfSegmented`.

**Spec:** `docs/2026-10-11-custom-session-controls-design.md` (optio)

## Where
- optio worktree `excavator:~/wt/deai/optio`, branch `csillag/custom-controls` (from `csillag/mcp-servers`).
- Excavator worktree `excavator:~/wt/deai/excavator`; Task 6 creates branch `csillag/chat-presets` from `csillag/data-chat`.
- The owner's live stack runs from these worktrees: Python edits under optio/excavator `src` restart its engine (acceptable, but finish edits in batches); TS edits hot-reload.
- Python: `source ~/wt/deai/env.sh; $PY -m pytest ...`. optio-conversation-ui tests: `./node_modules/.bin/vitest run ... --pool=threads --poolOptions.threads.maxThreads=1` in the package (never pnpm in ~/wt/deai/optio).
- No Co-Authored-By trailer (repo policy). Never push.

## Global Constraints
- Control and option ids match `^[A-Za-z0-9_-]+$`; a control has >= 2 options with unique values and a `default` among them.
- `Text = str | Mapping[str, str]`; map keys are language codes, values non-empty; resolution: language entry, else `en`, else the first entry.
- Effort levels: low, medium, high, xhigh, max. Permission modes: optio-claudecode's known modes.
- A setting key (model, reasoning_effort, permission_mode) may be set by the options of at most one control.
- Unsupported agents refuse a non-empty `custom_controls` with `<agent_type> does not support custom_controls yet`; `CUSTOM_CONTROLS_AGENT_TYPES = frozenset({"claudecode"})`.
- `/control` id for a custom control: `custom:<control id>`; accepted regardless of `settable_controls`.
- widgetData key `customControls`: `[{id, label, description, options: [{value, label, description}], value}]`, labels unresolved; `value` = the option whose settings all match the current values, else `null`.
- Data chat control: id `speed`, label `{en: "Model", hu: "Modell", de: "Modell"}`, default `extensive`; `fast` "Fast" `{model: "sonnet", reasoning_effort: "medium"}`; `extensive` "Extensive" `{model: "opus", reasoning_effort: "xhigh"}`.

## Review Focus
1. A pick while the agent is mid-turn or the conversation is closed: refused cleanly (409), selection unchanged. (Task 4)
2. Built-in model/effort and a custom control both shown: a manual built-in change makes the custom selection `null`, a custom pick updates the built-in values. (Task 4)
3. Resume after a pick: the picked model/effort come back and the derived selection matches. (Task 4)
4. A failed `apply_flag_settings` (CLI error): values and selection unchanged, error surfaced. (Task 3)
5. A language with no entry (e.g. `fr`) and a plain-string label render sensibly. (Task 5)

---

### Task 1: Spike -- live model/effort switching in Claude Code 2.1.296 (throwaway)
Determine with a REAL conversation-mode Claude Code session on excavator (use a Claude Code seed of the dev DB the way optio-claudecode materializes one -- lease it with a holder id and release it after; never print credentials) whether `apply_flag_settings {settings: {model, effortLevel}}` switches model and effort between turns without restart (control response ok; the next turn's model and effort observable in stream-json output), and how `--continue` resumes afterwards. If it does not work, try `set_model` plus `/effort <level>` as user input. Report the exact request/response JSON, what changed, and the recommendation. Nothing is committed; scratch only under /tmp.

### Task 2: optio-agents -- the definition and the shared mixin
**Files:** create `optio-agents/src/optio_agents/custom_controls.py` (+ exports); modify the 7 `types.py` (mixin + `_validate_custom_controls(supported=..., agent=self.agent_type)` in `__post_init__`, supported only in claudecode); `optio-agents-all` capability exports; tests `optio-agents/tests/test_custom_controls.py`, `optio-agents-all/tests/test_config_uniformity.py`, `test_capabilities.py`.
**Produces:** `Text`, `ControlSettings(model, reasoning_effort, permission_mode)`, `CustomOption(value, label, settings, description=None)`, `CustomControl(id, label, default, options, description=None)`, `CustomControlsConfigMixin.custom_controls`, `_validate_custom_controls`, `resolve_text(text, language) -> str`, `CUSTOM_CONTROLS_AGENT_TYPES`, `supports_custom_controls`.
- [ ] Tests first (validation per Global Constraints; text resolution incl. fallback; unsupported agents refuse; uniformity; capability), RED, implement, GREEN, suites, commit `feat(optio-agents): custom session controls in the shared agent configuration`.

### Task 3: optio-claudecode -- live switching for model and effort
**Consumes:** Task 1's result (the request that works).
**Files:** `optio-claudecode/src/optio_claudecode/{conversation,session,controls,picks}.py` and tests.
**Produces:** `ClaudeCodeConversation.apply_settings(model: str | None, effort: str | None, permission_mode: str | None) -> None` (raises a clear error on refusal), used by the built-in `model` / `reasoning_effort` controls instead of the restart-with-`--continue` path; picks saved and the control update emitted on success.
- [ ] Tests first (one apply request carrying both values; no relaunch; failure leaves values unchanged and surfaces the error; permission mode via `set_permission_mode`; picks saved), RED, implement, GREEN, package suite (known baseline failure `test_resume_refresh_tags_resume_log` only), commit `feat(optio-claudecode): switch model and effort live`.

### Task 4: optio-claudecode -- custom controls
**Consumes:** Tasks 2-3.
**Files:** `session.py`, `controls.py`, `conversation_listener.py`, tests.
- [ ] Tests first: fresh launch takes each control's default settings over the config's model/effort/permission; resume restores picks; `widgetData.customControls` (labels unresolved, derived `value`); control updates carry it; `POST /control {id: "custom:speed", value}` applies via `apply_settings` (one request), 400 for unknown control/option, 409 when refused/closed/busy (Review Focus 1); derived selection after a built-in change is `null` and after a custom pick the built-ins reflect it (Review Focus 2); resume keeps the pick (Review Focus 3). RED, implement, GREEN, suite, commit `feat(optio-claudecode): custom session controls`.

### Task 5: optio-conversation-ui -- the selectors
**Files:** `optio-conversation-ui/src/{ConversationView,chat}.tsx/.ts`, `claudecode/ClaudeCodeView.tsx`, `claudecode/events.ts`, tests.
- [ ] Tests first: `CustomControls` renders one `OneOfSegmented size="small"` per control in the session toolbar before the built-ins, captions hidden in compact mode, descriptions as tooltips, no highlight for `null`, disabled while busy/closed; labels resolved from antd ConfigProvider locale (hu map entry; `fr` falls back to `en`; plain strings) (Review Focus 5); picking posts `{id: "custom:<id>", value}` with an optimistic update; control updates fold `customControls`; shown independent of `showSessionControls`. RED, implement, GREEN, package suite, commit `feat(optio-conversation-ui): custom session controls`.

### Task 6: Excavator -- Fast / Extensive in the data chat
**Files:** create branch `csillag/chat-presets` from `csillag/data-chat` in `~/wt/deai/excavator`; `packages/engine/src/engine/data_chat/task.py`, `tests/test_data_chat_task.py`.
- [ ] Test first: the chat task's `custom_controls` equals the Global Constraints value and `show_session_controls` stays False; RED, implement, GREEN, engine suite, commit `feat(engine): Fast / Extensive model switch in data chats`.

### Task 7: Live check (controller, with the owner)
In a data chat on the worktree stack: switch Fast -> Extensive -> Fast between questions; no restart (engine log); the turn uses the chosen model; resume keeps the pick; labels in en/hu/de.
