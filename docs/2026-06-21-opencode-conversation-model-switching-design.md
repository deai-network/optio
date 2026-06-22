# Opencode Conversation-Mode Model Switching — Design

**Base:** branch `csillag/opencode-frontend`, 2026-06-21.

## Summary

Conversation mode for opencode lacks model switching — an unwanted asymmetry
with iframe mode, where opencode's embedded SPA carries its own model picker.
This adds a model picker to the conversation widget (`OpencodeView`) and two
task-config knobs to control a per-task default model and the picker's
visibility.

The feature is almost entirely client-side in `optio-conversation-ui`. opencode
already exposes everything needed over its REST API, which the existing generic
widget proxy forwards untouched; no optio-api, optio-core, or optio-host change.
The only Python change is plumbing two new `OpencodeTaskConfig` fields into the
conversation widget's `widgetData`.

## Verified API facts (opencode 1.17.3-csillag.2)

- **Discovery:** `GET /config/providers` → `{ providers: [...], default: {...} }`.
  Each provider: `{ id, name, models: { <modelId>: { id, providerID, name,
  family, capabilities, cost, limit, status, ... } } }`. `default` names the
  fallback provider/model. The list reflects exactly what the seed has
  configured and authenticated.
- **Switching:** `POST /session/<id>/prompt_async` accepts a `model:
  { providerID, modelID }` object (both required) alongside the existing
  `parts`. Omitting it preserves today's behavior (opencode uses its own
  default).
- Both routes ride the generic widget proxy
  (`/api/widget/<db>/<prefix>/<pid>/<subpath…>`), reachable client-side with the
  same `?directory=` query and inner-auth the widget already uses.

## Decisions (settled during brainstorming)

1. **Source of the model list:** discovered live from `GET /config/providers`.
   No curated/static list, no drift from what the seed actually exposes.
2. **Selection granularity:** session-sticky. A single "current model" is sent
   on every `prompt_async` until the user changes it; not per-message.
3. **Picker placement:** anchored at the bottom beside the send input (matching
   native opencode), grouped by provider, listing all models (no filtering).
4. **No engine logic.** opencode is the single authority for what models exist
   and which ran; the widget is the single authority for what model is sent.
5. **Scope:** opencode only. `ClaudeCodeView` is untouched. No `agent`
   switching. No persistence beyond what opencode's own history already records.

## 1. Task config surface (optio-opencode)

New fields on `OpencodeTaskConfig` (`types.py`), plumbed through
`create_opencode_task`:

```python
# Default model for a fresh conversation session, "providerID/modelID".
# Applied only at the start of a non-resumed session and only if present in
# the live model list; otherwise ignored. Effective regardless of whether the
# picker is shown.
default_model: str | None = None
# Show the model picker in the conversation widget.
show_model_selector: bool = False
```

**Validation (`__post_init__`, additive):** both fields require
`conversation_ui=True` (which already requires `mode="conversation"`). They are
conversation-widget concepts; without the widget they have no effect, so
setting either without `conversation_ui` is a config error.

Both values are forwarded into the conversation widget's `widgetData` in
`session.py` (the `conversation_ui` branch that already sets `protocol`,
`sessionID`, `directory`):

```python
"showModelSelector": config.show_model_selector,
"defaultModel": config.default_model,   # may be None
```

No other Python behavior changes. In particular, opencode's own config is never
rewritten for this feature — the default is enforced by the widget (§3), so
there is nothing to "apply once" on the Python side and nothing to clobber on
resume.

## 2. Widget — model picker (OpencodeView)

`OpencodeView` gains:

- A providers fetch on bootstrap: `GET config/providers?directory=…` through
  the proxy, alongside the existing history fetch. Result is reduced to a
  grouped option model: `[{ providerName, models: [{ providerID, modelID,
  label }] }]`.
- A picker rendered **only when `widgetData.showModelSelector` is true**,
  placed at the bottom next to the send input. Grouped by provider (antd
  `Select` with `OptGroup`, label = `model.name`). Selecting sets the sticky
  `currentModel`. Disabled while a prompt is in flight.

## 3. Widget — state and initial model resolution

New state: `currentModel: { providerID, modelID } | null`.

Resolved **once** on bootstrap, in order:

1. **History-last** — the last assistant message in the fetched history that
   carries `providerID/modelID` (the same datum
   `_resolve_session_model_sync` reads, computed client-side over history).
2. **`defaultModel`** — used only when history yields nothing (a fresh,
   non-resumed session) **and** the value is present in the discovered model
   list. Invalid/absent → skipped.
3. **`/config/providers` `default`**.
4. `null`.

This yields the required "apply the default once at session start, only when not
a resume" semantics without an explicit resume flag: a resumed session restores
history, so step 1 wins and the user's previously selected model carries over; a
fresh session has empty history, so step 2 applies `defaultModel`. A user's
mid-session switch is recorded by opencode in the message history, so it is what
step 1 finds on the next resume.

`currentModel` is React state only — never persisted by the widget.

## 4. Widget — send wiring

`send()` includes `model: currentModel` in the `prompt_async` body whenever
`currentModel` is non-null, omitting it otherwise. The widget **always** sends
the resolved `currentModel`, whether or not the picker is rendered — this is
what makes `default_model` effective independent of `show_model_selector`.

```ts
const body = { parts: [{ type: 'text', text }],
               ...(currentModel ? { model: currentModel } : {}) };
```

## 5. Edge cases and errors

- `config/providers` fetch fails or returns no providers → picker (if shown)
  renders disabled with an explanatory tooltip; sending still works with no
  `model` field (opencode falls back to its own default). `currentModel`
  resolution stops at step 1 (history) or null. Non-fatal; never blocks chat.
- The providers fetch runs concurrently with the history bootstrap; its failure
  does not block history or messaging.
- `defaultModel` set but not in the list → ignored (logged client-side at
  debug), resolution falls through to providers `default`.

## 6. Testing

- `fake_opencode.py`: add `GET /config/providers` returning a small
  two-provider, multi-model fixture; extend the `prompt_async` journal to
  capture the `model` field.
- optio-opencode: a config test that `show_model_selector`/`default_model`
  without `conversation_ui` raises; a session test that both values land in
  `widgetData`.
- optio-conversation-ui (`OpencodeView`): picker hidden by default; shown when
  `showModelSelector`; renders grouped options from a providers fixture;
  selecting a model makes the next `prompt_async` body carry
  `model:{providerID,modelID}`; initial `currentModel` derives history-last →
  `defaultModel` → providers `default`; widget sends `defaultModel` even with
  the picker hidden.

## 7. Scope / non-goals

- opencode only; `ClaudeCodeView` untouched.
- No `agent` selection (model only).
- No new optio-api / optio-core / optio-host code; no opencode-config rewrite.
- Demo: set `show_model_selector=True` on the conversation task only
  (`opencode-conversation-seed-<id>`); the three iframe-mode opencode tasks keep
  opencode's native SPA picker. `default_model` is not exercised in the demo.

## 8. Phasing

**Phase 1 (this spec):** widget picker + opencode adapter + the two config
fields + demo wiring. Implemented now.

**Phase 2 (deferred — separate paired spec):** Claude Code parity, per the
engine-parity rule for this package. Same user-facing capability, different
mechanism — design verified empirically this session, captured here so Phase 2
starts from facts:

- **Switch mechanism:** kill the `claude` subprocess and relaunch in the same
  workdir with `--continue` + new `--model`. Verified: `--model` *does* override
  on resume (haiku→opus, transcript preserved). The in-place `/model` slash
  command is **refused** in headless stream-json mode ("not available in this
  environment"), so restart is the only path. Cost is a single uncached turn —
  inherent to any model change (cache is per-model), not extra.
- **Model list:** Claude Code exposes no programmatic list; fetch the
  account-available set from the Anthropic Models API (`GET /v1/models`).
- **Lifecycle (must preserve on the restart):** pasta/netns is **not** in the
  conversation path (iframe-only). Landlock (claustrum) is **per-process** — the
  relaunch must go through the normal launch path so the argv wrap is re-applied
  (a bare re-exec runs unconfined). Task-scoped infra survives untouched
  (`Host`, `ClaudeCodeConversation`, `ConversationListener` + SSE, replay buffer,
  permission gate, `<workdir>/home/.claude`), so the browser stays attached; the
  restart loop generalizes the conversation body's existing per-process
  attach/reader/cred-watcher choreography. Restart-loop template:
  `csillag/restart-on-demand`.
