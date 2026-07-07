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

## Reachability (from this session's probes)

Graded reasoning-effort is reachable on **6 of 7** engines (not antigravity —
agy bakes the thinking level into the model id, no lever). Two apply the change
**live** (no restart); four are **restart-backed** (relaunch with the new
setting, seamless via the engine's continue/resume — identical UX to switching
the model on claudecode/antigravity today).

## Section 1 — Contract extension

### New `SessionControl` kind: `slider`
- Python `ControlKind` gains `"slider"`; TS `SessionControl.kind` union gains
  `'slider'`. Reuses the existing `levels: list[str]` (ordered) — the slider is a
  segmented control rendered as a discrete antd `Slider` snapped to labeled marks.
- No new round-trip plumbing: `set_control(id, value)`, `disabled`/`whyDisabled`,
  and the `show_session_controls` gate all apply unchanged.

### Config field: `reasoning_effort` (per-engine enum)
- Added to the 6 reachable engines' `TaskConfig` as the **initial** reasoning
  effort (mirrors `model`; applied at launch), **typed as each engine's own
  `Literal` enum** and validated in `__post_init__`. Spec C's discriminated union
  selects the enum by `agent_type` — same field name, different value type per
  variant.
- It is **not** part of the harmonized common core (6/7 coverage; antigravity has
  no such field) and stays **out** of the Spec-A parity guard's `CORE` set.

### Live control
- `id: "reasoning_effort"`, `kind: "slider"`, `levels` = the **current model's**
  supported effort levels (model-dependent, see Section 3), `value` = current.
- `set_control("reasoning_effort", <level>)` → live or silent-restart per engine
  (Section 2).

## Section 2 — Per-engine wiring

| Engine | `reasoning_effort` levels | `set_control` mechanism | Present when |
|---|---|---|---|
| **codex** | `none/minimal/low/medium/high/xhigh` | **live** — stash `_requested_effort` → attach to next `turn/start.effort` (clone the existing inline model seam) | current model supports effort |
| **opencode** | config enum `none/low/high/xhigh/max`; **live levels from the model's `variants`** | **live** — attach the chosen `variant` to the next prompt body (like `current_model_id`) | current model exposes reasoning `variants` |
| **claudecode** | `low/medium/high/xhigh/max` | **restart** — relaunch `--effort <level> --continue` via the existing `model_change_requested` restart path | model advertises the `effort` capability |
| **kimi** | `off/low/medium/high/xhigh/max` | **restart** — rewrite `config.toml [thinking]` (enabled + effort) + relaunch; **REPLACES kimi's current live thinking on/off control** | model `thinkingSupported` |
| **grok** | `low…max` *(verify grok `--reasoning-effort` set in the plan)* | **restart** — relaunch with `--reasoning-effort <level>` | reasoning model (heuristic from the models block) |
| **cursor** | *(verify cursor bracket effort set in the plan)* | **live** — rewrite the model-id `[effort=<level>]` bracket → ACP `session/set_model` | model is parameterized / reasoning |

Notes:
- **kimi** — its reasoning is ONE system with two facets (`thinking` enable-toggle
  live over ACP; grade `config.toml [thinking].effort` restart). We unify them
  into a single `off…max` slider (restart-backed), and the current live thinking
  on/off `SessionControl` in `parse_all_controls` is **removed** in favour of it.
  Plan must verify `config.toml [thinking]` can set thinking *off* (not just
  grade); if it cannot, fall back to keeping the live on/off toggle for the `off`
  transition (hybrid) — but the design target is the unified restart-backed
  slider.
- **grok** — the harmonized field maps to grok's `--reasoning-effort` (reasoning
  depth). grok's general `--effort` stays a grok-native field, untouched.
- **cursor** — effort is encoded in the model id (`sonnet-4-thinking` /
  `model[effort=high]`); `set_control("reasoning_effort", …)` rewrites the bracket
  on the current model id and re-sends `session/set_model`. Model and effort
  controls both touch the model id — the effort control reads the current bracket
  and writes a new one.
- **restart-backed engines reuse the model-swap relaunch** (the operator already
  experiences this when switching model on claude/kimi/grok); no new restart
  machinery.

## Section 3 — Model-dependent presence

The effort control is **re-derived on every model change** (the same reactive
path kimi's thinking control already uses — it appears/vanishes as the model
switches). On session start and on each model switch, the engine determines: does
the current model support graded reasoning effort, and what are its levels? Then
it declares or omits the `reasoning_effort` control accordingly (a single-level or
unsupported model → the control is disabled/locked with a `whyDisabled`, per the
Spec-A single-option auto-lock, or omitted entirely).

Per-engine capability source:
- **opencode** — the model's `variants` map (clean; present ⇒ reasoning tiers).
- **claudecode** — model `capabilities` (`["effort", …]`) + `default_effort` from
  the model catalog (`models.py` already fetches models; extend to capture it).
- **kimi** — `thinkingSupported` from ACP `configOptions` (kimi already knows).
- **codex / grok / cursor** — RESEARCH-GATED (plan phase): confirm the capability
  source (codex model list / config, grok models block, cursor model list) and
  the exact level enum. Where a clean capability signal is unavailable, fall back
  to a reasoning-model heuristic (id-based) and document it.

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
  - live (codex/opencode/cursor) — asserts the next `turn/start`/prompt-body/
    `set_model` carries the effort (mock transport).
  - restart (claudecode/kimi/grok) — asserts a restart is triggered and the
    `--effort`/`config.toml`/`--reasoning-effort` value is rewritten.
  - **kimi** — `parse_all_controls` emits the `reasoning_effort` slider **instead
    of** the thinking on/off control; `set_control` writes `config.toml [thinking]`.
- **Model-dependent presence:** the control appears when the model supports effort
  and vanishes (or locks) on switch to a non-reasoning model — per engine, with
  fakes.
- **Config field:** each engine's `reasoning_effort` `Literal` enum validates in
  `__post_init__`.
- Standard: `.venv` in the worktree; pytest-xdist harness (new tests xdist-safe,
  `serial` only if spawn-heavy); conversation-ui via `tsc --noEmit` + vitest.

## Research items (resolve in the plan phase)

1. **grok** `--reasoning-effort` accepted level set + reasoning-model detection.
2. **cursor** model-id `[effort=…]` accepted level set + parameterized/reasoning
   model detection (and confirm live `set_model` accepts a bracketed id).
3. **codex** reasoning-effort capability source (which models support `effort`).
4. **kimi** — confirm `config.toml [thinking]` can set thinking **off** (not just
   grade), to validate the unified-slider (#1) design vs the hybrid fallback.
5. **opencode** — the exact `variant` → reasoning-tier mapping per model (for the
   live `levels`), and the fixed config-enum superset.

## Dependencies & rollout

Depends on **Spec A** (harmonized config surfaces — done) and the shipped
session-controls system (`optio_agents.session_controls`, the conversation-ui
renderer + `set_control` round-trip). Parallel-shaped rollout: contract + slider
renderer foundation first, then per-engine wiring (file-disjoint by engine),
verification deferred to the end. Antigravity is untouched (no effort control).
