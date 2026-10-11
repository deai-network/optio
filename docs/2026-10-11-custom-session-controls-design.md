# Custom session controls -- Design

2026-10-11. Designed with the owner in conversation (brainstorming); decisions recorded below.
Facts: superego `~/chat/2026-10-11-session-control-presets-facts.md`.

## Goal

A task can show its own selectors in the conversation view, next to (or instead of) the
built-in agent controls (model, effort, permission mode). Each selector is a
`OneOfSegmented` whose options each set a fixed combination of model / effort / permission
mode. The options, their labels and the default come from the task definition, the way
`show_session_controls` and the `session_controls` whitelist do today.

First use: Excavator's data chat gets the daeo app's "Fast / Extensive" switch.

## Owner decisions

1. Custom controls are independent of the built-in bar (`show_session_controls`,
   `session_controls`): a task can show only its custom controls, only the built-ins, or
   both. When both show, a custom selector highlights the option whose settings all match
   the current values, otherwise none (e.g. after a manual model change).
2. A task may define several custom controls; each setting (model, effort, permission mode)
   may be set by at most one of them.
3. Each control declares a default option, applied when a session starts; the user's pick
   survives a resume.
4. Labels and descriptions are a plain string or a per-language map (`{en, hu, de}`); the
   widget shows the UI language's entry, falling back to `en`, then to the first entry.
5. Claude Code first: the definition lives in optio's shared agent configuration (all 7
   agent configs accept it); Claude Code applies it; the other agents refuse a non-empty
   list until their translation lands (as with MCP servers).
6. The engine applies a pick, live, without restarting the agent. The same live path
   replaces today's restart for the built-in model and effort controls ("the restart notion
   is obsolete").

## Facts this rests on

- optio's controls today: `SessionControlsConfigMixin` (`show_session_controls`,
  `session_controls`); the contract in `optio_agents/session_controls.py`; Claude Code sends
  the controls in `widgetData` and full updates as `x-optio-control-update`; the widget posts
  `/control {id, value}` (one id per request); the listener refuses ids outside
  `settable_controls` (403); a model or effort change restarts claude with `--continue`;
  picks persist in `home/.claude/optio-picks.json` and are re-validated on resume.
- Claude Code 2.1.296 (binary strings, to be confirmed by the spike): control requests
  `apply_flag_settings {settings: {model?, effortLevel?, ...}}` (one request can set both),
  `set_model`, `set_permission_mode` (already used live by optio); `/model` and `/effort`
  are usable in headless mode.
- optio's known effort levels: low, medium, high, xhigh, max.
- vultus `OneOfSegmented` (vultus-antd) renders a `useOneOfField` field: choices with label,
  description tooltip, disabled reason, variant; a pending commit locks the control.
- The widget renders inside antd's `ConfigProvider`, whose locale Excavator sets per UI
  language (`en_US`, `hu_HU`, `de_DE`).

## Part A -- optio-agents: the definition (shared)

New module `optio_agents/custom_controls.py`, re-exported:

- `Text = str | Mapping[str, str]` (a plain string or a language map; map keys are language
  codes such as `en`, `hu`, `de`; values non-empty).
- `ControlSettings` (frozen): `model: str | None`, `reasoning_effort: str | None`,
  `permission_mode: str | None`; at least one set; `reasoning_effort` in the known levels,
  `permission_mode` in the known modes.
- `CustomOption` (frozen): `value: str` (`[A-Za-z0-9_-]+`), `label: Text`,
  `description: Text | None = None`, `settings: ControlSettings`.
- `CustomControl` (frozen): `id: str` (`[A-Za-z0-9_-]+`), `label: Text`,
  `description: Text | None = None`, `default: str`, `options: list[CustomOption]`
  (at least 2, unique values; `default` is one of them).
- `CustomControlsConfigMixin`: `custom_controls: list[CustomControl] | None = None`;
  `_validate_custom_controls(*, supported: bool, agent: str)`: an unsupported agent refuses
  a non-empty list (`<agent> does not support custom_controls yet`); unique control ids;
  every setting key set by the options of at most one control (decision 2).
- All 7 `<X>TaskConfig`s inherit the mixin; `test_config_uniformity.py` checks it.
- optio-agents-all: `CUSTOM_CONTROLS_AGENT_TYPES = frozenset({"claudecode"})`,
  `supports_custom_controls(agent_type)`.

## Part B -- optio-claudecode: applying presets live

- **Spike first (throwaway):** in a real conversation-mode session, send
  `apply_flag_settings {settings: {model: "sonnet", effortLevel: "medium"}}` and
  `{model: "opus", effortLevel: "xhigh"}` between turns; confirm the control response, that
  the next turn runs on the new model and effort (the CLI's status / the turn's model), and
  that `--continue` resumes with the last values. If `apply_flag_settings` does not do it,
  fall back to `set_model` + `/effort` and record which.
- **Launch:** a fresh launch takes each custom control's default option's settings over the
  config's `model` / `reasoning_effort` / `permission_mode`. A resume restores the saved
  picks (existing `picks.py`, re-validated) instead.
- **Live switching (one path for custom and built-in controls):** a method on the
  conversation that applies a `ControlSettings`-like change with one `apply_flag_settings`
  request (model and/or effortLevel) and, when needed, one `set_permission_mode` request;
  on success it updates the current values, saves the picks and emits the control update.
  Built-in `model` / `reasoning_effort` changes use it too; the restart-with-`--continue`
  path for them is removed (the model probe on resume stays).
- **widgetData / updates:** `customControls`: per control `{id, label, description,
  options: [{value, label, description}], value}`, labels unresolved (all languages);
  `value` is derived: the option whose settings all match the current model / effort /
  permission mode, else `null`. Sent whatever `show_session_controls` says; included in
  every `x-optio-control-update`.
- **Listener:** `POST /control {id: "custom:<control id>", value}` is accepted for declared
  custom controls regardless of `settable_controls`; an unknown control or option is 400
  `bad-id` / `bad-value`; a refused switch is 409 `rejected` with the CLI's message.
- **Errors:** a failed switch leaves the current values and the derived selection unchanged;
  the widget shows the error (as for built-in controls today).

## Part C -- optio-conversation-ui

- `CustomControls`: one `OneOfSegmented size="small"` per custom control, in the input
  toolbar where the built-in controls sit, before them; the control's label as the muted
  caption (hidden in compact mode, like the built-ins); option descriptions as tooltips;
  no highlighted segment when the value is `null`; disabled while a turn runs or the
  conversation is closed (as the built-ins).
- Labels: `resolveText(text, language)`: the language's entry, else `en`, else the first
  entry; the language from antd's `ConfigProvider` locale (`locale.locale`, e.g. `hu`),
  default `en`.
- Picking posts `/control {id: "custom:<id>", value}` with an optimistic update, like the
  built-ins; the engine's control update is the source of truth.
- Shown whenever `widgetData.customControls` is non-empty (independent of
  `showSessionControls`). Claude Code view only in v1.

## Part D -- Excavator: Fast / Extensive in the data chat

The data chat task (Claude Code) defines one control (labels copied from the daeo app):

- id `speed`, label `{en: "Model", hu: "Modell", de: "Modell"}`, default `extensive`
  (the daeo app's default spec uses its "Extensive" model; the owner may switch it);
- option `fast`: label "Fast", settings `{model: "sonnet", reasoning_effort: "medium"}`;
- option `extensive`: label "Extensive", settings `{model: "opus", reasoning_effort: "xhigh"}`.

`show_session_controls` stays `False` (no built-in bar in a chat).

## Testing

- optio-agents: definition validation (text maps, settings, defaults, duplicate ids, a
  setting owned by two controls, unsupported agents refuse); uniformity; capability table.
- optio-claudecode: launch takes the defaults; resume restores picks; the live switch sends
  one `apply_flag_settings` (and `set_permission_mode` when needed) and no restart; built-in
  model/effort use the same path; derived selection (match, no match); listener ids and
  errors; control updates carry `customControls`.
- optio-conversation-ui: rendering with `OneOfSegmented`, language resolution and fallback,
  no highlight for `null`, disabled while busy, the POST body.
- Excavator: the data chat task's `custom_controls` value.
- Live: in a data chat, switch Fast -> Extensive -> Fast between questions; the agent answers
  on the chosen model (the turn's model in the transcript / logs), no restart; resume keeps
  the pick.

## Out of scope

- The other six agents' translation; per-option icons; free-form (non-preset) values;
  changing settings in the middle of a running turn.
