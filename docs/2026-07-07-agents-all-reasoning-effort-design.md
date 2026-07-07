# Agents-All — Spec B: Reasoning-Effort as a Live Control

**Date:** 2026-07-07
**Branch:** `csillag/agents-all` (worktree)
**Status:** Approved design (brainstorming output); implementation plan to follow.
**Program:** Spec **B** of three (see `2026-07-07-agents-all-config-harmonization-design.md`).
A = config-surface harmonization (done). **B = this doc.** C = the `optio-agents-all`
meta-factory + discriminated union.

## Goal

Expose a graded **reasoning-effort** control across the engines that support it,
as a live `SessionControl` **slider**, mirroring exactly how the existing model
control works. Add a harmonized `reasoning_effort` config field (the initial
value, like `model`) typed per-engine so Spec C's discriminated union selects the
enum by `agent_type`.

## Reachability (verified this session)

Graded reasoning-effort is reachable on **5 engines** as a live control
(codex/grok/opencode/kimi/claude). **cursor** exposes effort **through the model
id** (variant ids like `…-thinking-high`), so it needs no separate control — it
is picked via the model dropdown. **antigravity** has no lever (agy bakes the
level into the model server-side).

**No new restart machinery is needed** (an earlier read suggested kimi/grok would
need a relaunch; deeper probing disproved it):
- **codex / grok / opencode / kimi** apply effort **live** (no restart), through
  each engine's existing runtime channel.
- **claudecode** is the only restart-backed engine, and it **reuses its existing**
  `model_change_requested` relaunch loop (the same path its model control already
  uses) — no new machinery.

**External dependency:** kimi's live graded effort requires the fork's
`csillag/acp-graded-thinking` change, shipped in **`kimi-code ≥ 0.23.1-csillag.2`**
(the ACP adapter now accepts a graded `session/set_config_option {configId:"thinking"}`
and advertises the graded levels). Verified present in that release this session.

## Section 1 — Contract extension

### New `SessionControl` kind: `slider`
- Python `ControlKind` gains `"slider"`; TS `SessionControl.kind` union gains
  `'slider'`. Reuses the existing `levels: list[str]` (ordered) — the slider is a
  segmented control rendered as a discrete antd `Slider` snapped to labeled marks.
- No new round-trip plumbing: `set_control(id, value)`, `disabled`/`whyDisabled`,
  and the `show_session_controls` gate all apply unchanged.

### Config field: `reasoning_effort` (per-engine enum)
- Added to the **5** effort-control engines' `TaskConfig` (codex/grok/opencode/
  kimi/claude) as the **initial** reasoning effort (mirrors `model`; applied at
  launch), **typed as each engine's own `Literal` enum** and validated in
  `__post_init__`. Spec C's discriminated union selects the enum by `agent_type`
  — same field name, different value type per variant. (cursor and antigravity
  get no such field.)
- It is **not** part of the harmonized common core (5/7 coverage) and stays
  **out** of the Spec-A parity guard's `CORE` set.

### Live control
- `id: "reasoning_effort"`, `kind: "slider"`, `levels` = the **current model's**
  supported effort levels (model-dependent, see Section 3), `value` = current.
- `set_control("reasoning_effort", <level>)` → live or silent-restart per engine
  (Section 2).

## Section 2 — Per-engine wiring

The effort control is added to **5 engines** (codex/grok/opencode/kimi/claude).
cursor is folded into the model control; antigravity is excluded.

| Engine | `reasoning_effort` levels | `set_control("reasoning_effort", …)` | Present when |
|---|---|---|---|
| **codex** | `none/minimal/low/medium/high/xhigh` | **live** — stash `_requested_effort` → attach to next `turn/start.effort` (clones the inline model seam) | model's `supported_reasoning_efforts` (app-server list) is non-empty |
| **grok** | `low/medium/high/xhigh` | **live** — re-send `session/set_model` for the **current** model with the effort in the request `_meta` (`reasoningEffort`) — the TUI `/effort` path; **no restart** | model `_meta.supportsReasoningEffort` (advertised in the ACP model block, currently discarded) |
| **opencode** | live levels = the current model's `variants` keys (config enum `none/minimal/low/medium/high/xhigh/max` superset) | **live** — attach the chosen `variant` to the next prompt body (client-side, like the model) | model's `variants` map is non-empty |
| **kimi** | `off/low/medium/high/xhigh/max` (always-thinking model drops `off`) | **live** — `session/set_config_option {configId:"thinking", value:<level>}` (fork ≥0.23.1-csillag.2); **replaces** kimi's current live thinking on/off control | model `thinkingSupported` / advertised graded levels in `configOptions` |
| **claudecode** | `low/medium/high/xhigh/max` | **restart** — relaunch `--effort <level> --continue` via the **existing** `model_change_requested` loop (add an effort arm) | model advertises the `effort` capability |

Notes:
- **grok** — effort rides `session/set_model` (the same request the wrapper already
  sends live for model), with the level in the request `_meta`. The exact request
  `_meta` key/nesting needs a one-off **live probe** (candidates:
  `_meta:{reasoningEffort:"high"}`, then top-level) — the method + no-restart are
  certain. grok's general `--effort` stays a grok-native launch field, untouched.
- **kimi** — its reasoning is ONE system (enable + grade). The fork now accepts a
  graded `session/set_config_option {configId:"thinking"}` and advertises the
  graded levels, so we expose a single `off…max` slider **live** and **remove**
  the old off/on thinking control from `parse_all_controls`. For an
  `always_thinking` model the fork drops the `off` level → the slider omits `off`
  (locked-on, matching the existing `ALWAYS_THINKING_REASON` pattern).
- **claudecode** — the only restart-backed engine; it has no live `set_effort`
  control-request, so effort reuses the model relaunch loop (add an
  `effort_task`/`requested_effort` arm alongside `model_change_requested`).
- **cursor (no separate control)** — cursor lists effort-expanded model ids
  (`gpt-5.3-codex-high`, `claude-opus-4-8-thinking-high`) and the `[effort=]`
  bracket is `--model`-CLI-only (unverified over ACP). Effort is therefore chosen
  via the existing **model** dropdown; adding a separate slider would duplicate
  it. No `reasoning_effort` field for cursor.

## Section 3 — Model-dependent presence

The effort control is **re-derived on every model change** (the same reactive
path kimi's thinking control already uses — it appears/vanishes as the model
switches). On session start and on each model switch, the engine determines: does
the current model support graded reasoning effort, and what are its levels? Then
it declares or omits the `reasoning_effort` control accordingly (a single-level or
unsupported model → the control is disabled/locked with a `whyDisabled`, per the
Spec-A single-option auto-lock, or omitted entirely).

Per-engine capability source (all resolved this session):
- **codex** — the app-server model list's `supported_reasoning_efforts` (non-empty
  ⇒ supported) + `default_reasoning_effort`; populate the slider levels directly
  from it.
- **grok** — the ACP model block `_meta` (`supportsReasoningEffort` +
  `reasoningEfforts`), currently read as only `{modelId,name}` — extend
  `parse_acp_models` to capture the `_meta`.
- **opencode** — the model's `variants` map (present ⇒ reasoning tiers); extend
  `parse_model_ids` to read `variants` (currently discarded).
- **claudecode** — model `capabilities` (`["effort", …]`) + `default_effort` from
  the model catalog (`models.py` already fetches models; extend to capture it).
- **kimi** — the fork's `configOptions` now advertises the graded levels for
  thinking-capable models (and `always_thinking` collapses per the fork);
  `parse_all_controls` reads them directly.

## Section 4 — UI

New `slider` branch in the shared `SessionControls` renderer
(`ConversationView.tsx`): antd `Slider`, discrete, `marks` from the ordered
`levels`, `value` = the index of the current level, `onChange` →
`onControlChange(id, levels[index])`. Honors the label prefix and
`disabled`/`whyDisabled` (locked with a reason when single-level / unsupported).
No other UI change.

## Section 5 — Testing

- **Contract:** `slider`-kind serialization (Python `to_dict` + TS type); a
  slider with `levels` and a `value`.
- **Renderer:** slider renders the right marks; dragging fires
  `onControlChange(id, level)`; single-level → disabled + tooltip.
- **Per-engine `set_control` round-trip:**
  - live (codex/grok/opencode/kimi) — asserts the next `turn/start` / prompt-body
    `variant` / `set_model _meta` / `set_config_option` carries the effort (mock
    transport).
  - restart (claudecode) — asserts a relaunch is triggered with the new `--effort`.
  - **kimi** — `parse_all_controls` emits the `reasoning_effort` slider **instead
    of** the thinking on/off control; `set_control` issues
    `session/set_config_option {configId:"thinking", value:<level>}`.
- **Model-dependent presence:** the control appears when the model supports effort
  and vanishes (or locks) on switch to a non-reasoning model — per engine, with
  fakes.
- **Config field:** each engine's `reasoning_effort` `Literal` enum validates in
  `__post_init__`.
- Standard: `.venv` in the worktree; pytest-xdist harness (new tests xdist-safe,
  `serial` only if spawn-heavy); conversation-ui via `tsc --noEmit` + vitest.

## Research items — RESOLVED this session

1. **grok** — levels `low/medium/high/xhigh` (TUI picker set); capability +
   levels advertised per-model in the ACP model `_meta`
   (`supportsReasoningEffort`/`reasoningEfforts`). Live via `session/set_model`
   `_meta`. **One residual live-probe:** the exact request-side `_meta` key for
   setting effort (candidates in the plan) — method + no-restart are certain.
2. **cursor** — effort is a model-id variant (folded into the model control; no
   separate control). Bracket-over-ACP unverified → not used.
3. **codex** — levels `none/minimal/low/medium/high/xhigh`; capability =
   app-server `supported_reasoning_efforts` (non-empty).
4. **kimi** — the fork (`≥0.23.1-csillag.2`, `csillag/acp-graded-thinking`)
   accepts + advertises graded thinking over ACP → **live**, no restart, no
   config.toml/relaunch. Verified in the release this session.
5. **opencode** — `variant` keys are per-model (e.g. OpenAI
   `none/minimal/low/medium/high/xhigh`, Anthropic `low…max`); the wrapper reads
   the current model's `variants` keys for the live levels; config enum = the
   union superset.

## Dependencies & rollout

Depends on **Spec A** (harmonized config surfaces — done) and the shipped
session-controls system (`optio_agents.session_controls`, the conversation-ui
renderer + `set_control` round-trip). Parallel-shaped rollout: contract + slider
renderer foundation first, then per-engine wiring (file-disjoint by engine),
verification deferred to the end. Antigravity is untouched (no effort control).
