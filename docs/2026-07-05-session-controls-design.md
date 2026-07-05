# Generic Session Controls — Design

**Date:** 2026-07-05
**Status:** Approved design (brainstorming output); implementation plan to follow.
**Scope:** All five agent wrappers (kimicode, cursor, grok, opencode, claudecode),
`optio-agents`, and `optio-conversation-ui`.

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
- **Full migration** of all five engines — no compatibility shim in the end
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
- `disabled` / `whyDisabled` generalize the existing "decision/reason" pattern
  already on main (cursor's model probe greys out plan-gated models with a
  reason). Default `disabled = false`; `whyDisabled` is rendered as a hover
  tooltip on the disabled option only.
- `boolean` carries no `options` / `levels`; `value` is the bool.
- `segmented` uses `levels` (ordered); `value` is the current level string.

## Data Flow (live round-trip)

### Inbound (agent → UI), live

Controls ride the **event stream**, not static `widgetData` — they are live and
the conversation reducer already consumes that stream.

1. The wrapper's `Conversation` receives native control updates:
   - **kimi (ACP):** `session/new` `configOptions` + `config_option_update`.
   - **cursor / grok (ACP):** the same `configOptions` once adopted (they read
     the older `models` block today).
   - **opencode / claudecode:** their native events.
2. The wrapper maps them to `SessionControl[]` and surfaces the snapshot.
3. The engine's **reducer** folds that snapshot into a new
   `controls: SessionControl[]` field on `ChatState` (alongside chat items).
4. The shared `ConversationView` renders `state.controls` — model dropdown,
   thinking segmented, mode select, toggles — replacing the special-cased model
   selector.

The reducer is the single source of truth for `state.controls`; a live update
must not drop or reorder chat items.

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

Each wrapper does two small things: map its native control state →
`SessionControl[]` (fed into its reducer → `state.controls`), and implement
`set_control(id, value)` → native.

| Engine | Inbound map | `set_control` → | Notes |
|---|---|---|---|
| kimi (ACP) | `configOptions` (model / thinking / mode) → controls | `session/set_config_option` | Already receives them; least work |
| cursor (ACP) | adopt `configOptions` if present, else model-only from `models` block | `set_config_option` / `set_model` | Survey may unlock thinking/mode |
| grok (ACP) | same as cursor | same | same |
| opencode (HTTP/SSE) | model (+ survey native extras) → controls | native set | inline |
| claudecode (stream-json) | model (+ survey thinking/permission) → controls | native | **restart-based** model switch |

**Survey obligation.** For cursor, grok, opencode, and claudecode, the
implementer must inspect the native transport for controls beyond model
(thinking / reasoning-effort, plan / permission mode) and map every one found.
The table's "model-only" entries are floors, not ceilings.

### Restart-based controls (claudecode `--model`)

Some controls apply inline (ACP `set_config_option`); claudecode's model change
needs a session restart. The contract is unchanged — `set_control` does whatever
the engine needs; the wrapper handles the restart, and `state.controls` reflects
the new value once it settles. No contract change; it is a per-wrapper
implementation detail. (A future `appliesOnRestart` hint could let the UI warn —
YAGNI for v1.)

## Migration (the "full" part)

- Shared `ConversationView`: **replace** the bespoke model selector with a
  generic controls renderer over `state.controls` (dropdown / segmented / toggle
  by `kind`; disabled option greyed + `whyDisabled` tooltip).
- Each engine's reducer folds its control snapshot into `state.controls` (model
  at minimum).
- `ConversationViewProps`: `onModelChange` → generic
  `onControlChange(id, value)`. The old `models` / `currentModel` `widgetData`
  path is removed.
- Task-config surface: `show_model_selector` → `show_session_controls` (master
  on/off, default on); `default_model` stays (sets the initial model-control
  value). Ship these field renames to all engines for parity even where only
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
2. **Migrate engines one at a time**, kimi first (richest, already has
   `configOptions`) → cursor → grok → opencode → claudecode. Each flips from
   model-`widgetData` to `state.controls`.
3. **Remove the old path** once all five emit `state.controls`: switch
   `ConversationView` to render solely from it, delete `models` / `currentModel`
   / `onModelChange`, and the `show_model_selector` → `show_session_controls`
   rename. No compat left.

Version chain at release time (per the changed-deps-first order):
optio-agents → optio-conversation-ui → the five engine packages → demo /
dashboard.
