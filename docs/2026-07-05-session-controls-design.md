# Generic Session Controls — Design

**Date:** 2026-07-05
**Status:** Approved design (brainstorming output); implementation plan to follow.
**Scope:** All six agent wrappers (kimicode, cursor, grok, opencode, claudecode,
codex), `optio-agents`, and `optio-conversation-ui`.

## Problem

In conversation mode, `optio-conversation-ui` renders exactly one bespoke
session control — the model dropdown — wired through a special-cased
`widgetData` path (`models` / `currentModel`) plus an `onModelChange` callback.
Agents expose more than a model, though. Kimi Code (K2.7) offers a **thinking**
effort level and a **mode** (permission/plan) via its ACP `configOptions`
channel; the other ACP engines (cursor, grok) plausibly expose the same once
read; opencode and claudecode have their own native knobs. None of these reach
the UI today.

We want a single engine-neutral contract: a wrapper **declares** whatever
session-level controls its native transport exposes, `optio-conversation-ui`
**renders** them generically (dropdown / toggle / segmented), and value changes
**channel back** to the agent through one generic mechanism — the same
round-trip the model selector uses today, generalized so the model is simply one
control among many.

## Goals

- One `SessionControl` contract, engine-neutral, owned by `optio-agents` and
  mirrored in `optio-conversation-ui`.
- The model selector becomes an ordinary control (`id: "model"`), no longer
  special-cased.
- **Live-updating:** the agent owns the control set and pushes a fresh snapshot
  whenever it changes; the UI reacts.
- Control kinds: `select`, `boolean`, `segmented`.
- **Full migration** of all six engines — no compatibility shim in the end
  state.
- Each engine exposes **every** control its native transport offers, not just
  the model (kimi's thinking/mode being the first case; cursor/grok/opencode/
  claudecode surveyed and mapped during implementation).

## Non-Goals

- No new agent-side *capabilities* — we surface controls that already exist in
  each engine's native transport; we do not invent thinking/mode support where
  the engine has none.
- No per-user persistence of control values (the agent/session is the source of
  truth for current values).
- No `appliesOnRestart` UI warning in v1 (see Restart-based controls).

## The Contract

A single engine-neutral `SessionControl`, declared in **optio-agents** (Python —
the shared contract, alongside `seeds` / `claustrum`) and mirrored in
**optio-conversation-ui** (TypeScript).

```
SessionControl:
  id:          str                    # "model" | "thinking" | "mode" | ...
  kind:        "select" | "boolean" | "segmented"
  label:       str                    # display name
  category?:   str                    # optional grouping/icon hint (model / thought_level / mode)
  value:       str | bool             # current value
  description?: str
  disabled:    bool = false           # whole control unchangeable (e.g. collapsed to one option)
  whyDisabled?: str                   # if present, shown as a hover tooltip on the disabled control
  # kind-specific:
  options?:    [ControlOption]        # kind = select
  levels?:     [str]                  # kind = segmented (ordered, e.g. off -> low -> high -> max)

ControlOption:                        # members of a select control
  value:        str
  label:        str
  description?: str
  disabled:     bool = false          # greyed-out, unselectable
  whyDisabled?: str                   # if present, shown as a tooltip on hover over the disabled option
```

Notes:

- The agent owns and pushes the **full current list** of controls (a live
  snapshot) whenever anything changes. The model becomes the `id: "model"`
  entry — no longer special-cased.
- `disabled` / `whyDisabled` exist at **two levels**, both generalizing the
  existing "decision/reason" pattern (cursor's model probe greys plan-gated
  models with a reason): on a `ControlOption` (one unselectable choice) and on
  the whole `SessionControl` (an unchangeable control). Engines auto-mark a
  select/segmented that collapses to ≤1 option as disabled with a reason
  (`SINGLE_OPTION_REASON`, or a control-specific one — e.g. kimi's always
  -thinking model: "This model always thinks; thinking can't be turned off").
  The UI greys the control and shows `whyDisabled` on hover.
- `boolean` carries no `options` / `levels`; `value` is the bool.
- `segmented` uses `levels` (ordered); `value` is the current level string.

## Data Flow (live round-trip)

### Inbound (agent → UI): widgetData seeds, reducer updates

`state.controls` is the **render source of truth**, but it is *seeded* from
`widgetData` (where the model catalog already lives) and *updated live* by the
reducer. This matches the existing architecture — model options reach the UI via
`ctx.set_widget_data(...)` today, not the reduced event stream, and opencode has
no Python model surface at all (the UI fetches `config/providers` itself).

1. **Seed (mount):** each wrapper emits `controls: SessionControl[]` in its
   `set_widget_data({...})` payload — replacing the bespoke `models` /
   `currentModel` / `showModelSelector` keys. For opencode, whose catalog is
   UI-fetched, the view assembles the initial model control from its
   `config/providers` fetch as it does today. The engine view reads
   `widgetData.controls` into the initial `state.controls`.
2. **Live update:** the engine **reducer** folds live control events into
   `state.controls` without dropping or reordering chat items — kimi's
   `config_option_update` (currently a no-op) → the changed control's value;
   claudecode's `system.init` model sniff → the model control's value; etc.
3. **Render:** the shared `ConversationView` renders `state.controls` — model
   dropdown, thinking segmented, mode select, toggles — replacing the special
   -cased `modelSelector` React node.

So: **catalog seeded via widgetData (as today); live value-changes via the
reducer.** `state.controls` is what the view renders; the reducer owns live
mutation of it.

### Outbound (UI → agent)

1. User changes a control → `onControlChange(id, value)` (a
   `ConversationViewProps` callback).
2. The per-task listener/adapter calls the wrapper's
   `Conversation.set_control(id, value)`.
3. The wrapper maps to native: ACP `session/set_config_option` (kimi) /
   `set_config_option` or `set_model` (cursor/grok) / native (opencode,
   claudecode).
4. The native echo (`config_option_update` or equivalent) flows back inbound →
   reducer updates → re-render.

This is exactly today's model-change round-trip, generalized to any control and
driven by the reducer instead of a bespoke `widgetData` path. Optional
optimistic update for snappiness; the native echo is the source of truth.

## Per-Engine Mapping

Each wrapper does two small things: emit its controls as `SessionControl[]` in
`widgetData` (seed) + fold live updates in its reducer, and implement
`set_control(id, value)` → native (generalizing today's `request_model_change`).

| Engine | Model set-mechanism (today) | Controls beyond model | `set_control("model")` → |
|---|---|---|---|
| kimi (ACP) | `session/set_model` INLINE | **thinking + mode** (from `configOptions`, received today but IGNORED) | `session/set_model`; thinking/mode → `session/set_config_option` |
| grok (ACP) | `session/set_model` INLINE (live-pinned) | none (reads `models` block, no `configOptions`) | `session/set_model` |
| cursor (ACP) | `session/set_model` INLINE (unverified; restart fallback) | none | `session/set_model` |
| opencode (HTTP/SSE) | inline per-prompt `prompt_async.model` (UI-local) | none (`config/providers` UI-fetched) | UI-local, applied next prompt |
| claudecode (stream-json) | **RESTART** `claude --model <m> --continue` (`model_change_requested` Event) | none (permission is per-tool `can_use_tool`, not a mode) | restart |
| codex (app-server) | **RESTART** (`model_change_requested`, mirrors claudecode) | none | restart |

**Kimi is the only engine gaining new controls in v1** — thinking (segmented
effort) + mode (select), already arriving on kimi's ACP `configOptions` and
currently discarded. The other five surface exactly one control (model) through
the generic contract; their bespoke `<Select>` is replaced, mechanism unchanged.

**Survey obligation.** For grok, cursor, opencode, claudecode, codex, the
implementer confirms (from the native transport) whether any control beyond
model exists; the audit above found none, so "model-only" stands unless the
implementer disproves it. Not speculative work — a confirm step.

### Set-mechanism variants (inline / restart / UI-local)

`set_control` hides three native mechanisms behind one method: ACP inline
(`session/set_model`, `session/set_config_option` — kimi/grok/cursor), process
restart (`--model --continue` — claudecode/codex, via the existing
`model_change_requested` Event), and UI-local next-prompt (opencode). The
contract is unchanged across all three; `state.controls` reflects the new value
once it settles. (A future `appliesOnRestart` hint could let the UI warn on
restart controls — YAGNI for v1.)

## Migration (the "full" part)

- Shared `ConversationView`: **replace** the bespoke `modelSelector` React node
  with a generic controls renderer over `state.controls` (dropdown / segmented /
  toggle by `kind`; disabled option greyed + `whyDisabled` tooltip), plus a new
  `onControlChange(id, value)` prop. There is no `onModelChange` today — the old
  surface is the opaque `modelSelector?: React.ReactNode` slot.
- Each engine view seeds `state.controls` from `widgetData.controls` and passes
  its own `onControlChange` handler (dispatching per-engine: `POST /control` to
  the listener for kimi/grok/cursor/claudecode/codex; UI-local for opencode).
- Each engine reducer folds live control updates into `state.controls`.
- Python: `widgetData` carries `controls: SessionControl[]` in place of
  `models` / `currentModel`; the `Conversation` protocol gains
  `set_control(id, value)` (generalizing `request_model_change`); each
  `conversation_listener.py` gains a `/control` route → `set_control` (opencode
  has no listener — UI-local).
- Task-config surface: `show_model_selector` → `show_session_controls` (master
  on/off, default on); `default_model` stays (sets the initial model-control
  value). Ship these field renames to all six engines for parity even where only
  some controls exist.

## Testing

- **Contract (optio-agents):** `SessionControl` shape + serialization; each
  `kind` validates its own fields (select → options, segmented → levels,
  boolean → no options / levels).
- **Reducer (per engine):** native control snapshot → `state.controls`; a live
  `config_option_update` (kimi) / native update folds into `state.controls`
  without dropping or reordering chat items.
- **Round-trip:** `set_control(id, value)` asserts the mapped native call (mock
  transport): kimi → `session/set_config_option`; cursor/grok →
  `set_config_option` / `set_model`; opencode/claudecode → native. Echo →
  `state.controls` reflects the new value.
- **conversation-ui renderer:** each `kind` renders (dropdown / segmented /
  toggle); disabled option greyed + `whyDisabled` tooltip on hover;
  `onControlChange(id, value)` fires with the right args.
- **Migration regression (per engine):** model still switchable end-to-end
  through the new generic path (proves nothing lost vs the old selector).
- **claudecode restart control:** `set_control("model", …)` restarts and the
  session returns with the new model in `state.controls`.

## Rollout

Incremental internally, clean end-state:

1. **Land the contract** — `SessionControl` in optio-agents + TS mirror; generic
   renderer in `ConversationView` (tolerates empty `controls`); add
   `onControlChange`. The old model path is still present.
2. **Migrate engines**, kimi first (richest, already has `configOptions`) →
   grok → cursor → claudecode → codex → opencode. Each flips its `widgetData`
   from `models`/`currentModel` to `controls[]`, seeds `state.controls`, and
   passes `onControlChange`. File-disjoint across packages → parallelizable.
3. **Remove the old path** once all six emit `state.controls`: delete the
   `modelSelector` node construction + `models`/`currentModel`/
   `showModelSelector` widgetData keys, and land the `show_model_selector` →
   `show_session_controls` rename. No compat left.

Version chain at release time (per the changed-deps-first order):
optio-agents → optio-conversation-ui → the six engine packages → demo /
dashboard.
