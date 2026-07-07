# Agents-All Spec B — Reasoning-Effort Live Control: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a live reasoning-effort `slider` `SessionControl` to the 5 effort-capable engines (codex/grok/opencode/kimi/claude), plus a per-engine `reasoning_effort` config field, mirroring the model control. cursor folds effort into the model dropdown; antigravity is excluded.

**Architecture:** A new `slider` control kind (reuses `levels`) + an `effort_control(...)` helper in `optio_agents.session_controls`; each engine declares the effort control when its current model supports graded effort (re-derived on model change) and routes `set_control("reasoning_effort", …)` to its live channel. Only claudecode restarts (reusing its existing model-relaunch loop).

**Tech Stack:** Python frozen dataclasses + pytest-xdist; TypeScript + React + antd + vitest.

## Global Constraints

- **Parallel-shaped (OVERRIDES per-task RED→GREEN):** one file per task owner; file-disjoint tasks concurrent; **verification deferred** to the final task. Engine tasks write code + tests, commit, don't run suites.
- Worktree `/home/csillag/deai/optio/.worktrees/csillag/agents-all`, branch `csillag/agents-all`, `.venv` inside it. pytest-xdist harness (new tests xdist-safe; `serial` only if spawn-heavy). TS via `node_modules/.bin/tsc` + vitest.
- **External dep:** kimi live effort needs the fork `kimi-code ≥ 0.23.1-csillag.2` (`csillag/acp-graded-thinking`) — verified present.
- No `Co-Authored-By`.
- **Model-dependent presence:** the effort control appears only when the current model supports graded effort, and is **re-derived + re-emitted on every model change** (an `x-optio-control-update` after `set_control("model")`, folded by the reducer — same reactive path kimi's thinking control uses). An unsupported model → omit the control; an always-on/single-level model → disabled+`whyDisabled` (Spec-A auto-lock).

---

## Execution model

```
Wave 1 (1 task):   T1 slider kind + effort_control helper + renderer (foundation)
Wave 2 (concurrent): T2 codex ∥ T3 grok ∥ T4 opencode ∥ T5 kimi ∥ T6 claudecode
Wave 3 (sequential): T7 full verification (xdist + tsc/vitest + grep)
```

Wave 2 depends on T1. The 5 Wave-2 tasks are file-disjoint (each owns its `packages/optio-<engine>/`). cursor + antigravity: no task.

### File-ownership map

| Task | Files owned |
|---|---|
| T1 | `optio-agents/src/optio_agents/session_controls.py`, `conversation-ui/src/chat.ts`, `conversation-ui/src/ConversationView.tsx`, + their tests (`optio-agents/tests/test_session_controls.py`, `conversation-ui/src/__tests__/session-controls-render.test.tsx`) |
| T2 codex | `packages/optio-codex/**` |
| T3 grok | `packages/optio-grok/**` |
| T4 opencode | `packages/optio-opencode/**` + `conversation-ui/src/opencode/**` |
| T5 kimi | `packages/optio-kimicode/**` |
| T6 claudecode | `packages/optio-claudecode/**` |
| T7 | none (runs suites, fixes fallout) |

> T4 owns opencode's conversation-ui files too (opencode's control is client-side in `OpencodeView.tsx`). The other engines' effort control flows through Python widgetData + the shared reducer, so they don't touch conversation-ui.

---

## WAVE 1

### Task 1: `slider` kind + `effort_control` helper + renderer

**Files:** `optio-agents/src/optio_agents/session_controls.py`; `conversation-ui/src/chat.ts`; `conversation-ui/src/ConversationView.tsx`; tests.

**Produces:** `ControlKind` incl. `"slider"`; `effort_control(*, levels, current, disabled=False, why_disabled=None, label="Effort") -> SessionControl`; TS `'slider'` kind; a `Slider` render branch.

- [ ] **Step 1 — Python contract.** In `session_controls.py`: change `ControlKind` (line 15) to `Literal["select", "boolean", "segmented", "slider"]`. Add a helper next to `model_control`:
```python
def effort_control(*, levels, current, disabled=False, why_disabled=None, label="Effort"):
    """Build the id="reasoning_effort" slider from ordered effort levels."""
    return SessionControl(
        id="reasoning_effort", kind="slider", label=label, category="thought_level",
        value=(current or (levels[0] if levels else "")), levels=list(levels),
        disabled=disabled, why_disabled=why_disabled,
    )
```
(`to_dict` needs no change — it already serializes `levels`.)

- [ ] **Step 2 — TS contract.** In `chat.ts` (line 36) add `'slider'` to the `kind` union; update the `levels` comment to note it backs `slider` too.

- [ ] **Step 3 — Renderer.** In `ConversationView.tsx`: add `Slider` to the antd import (line 2). Add an `else if (c.kind === 'slider')` branch between the `segmented` branch (ends ~185) and the `else`/select branch (~186):
```tsx
} else if (c.kind === 'slider') {
  const levels = c.levels ?? [];
  const idx = Math.max(0, levels.indexOf(String(c.value)));
  node = (
    <Slider
      data-testid={`control-${c.id}`}
      style={{ minWidth: 160, alignSelf: 'center' }}
      min={0} max={Math.max(0, levels.length - 1)} step={null}
      marks={Object.fromEntries(levels.map((l, i) => [i, capitalize(l)]))}
      value={idx} disabled={dis}
      onChange={(v: number) => onChange(c.id, levels[v])}
    />
  );
}
```
(reuses the same `dis`/`labeled`/`Tooltip` machinery as the other kinds — no other change.)

- [ ] **Step 4 — Tests** (xdist-safe): `test_session_controls.py` — `effort_control` builds a `kind="slider"` control whose `to_dict()` carries `levels` + `value`; `session-controls-render.test.tsx` — a slider renders `control-reasoning_effort`, dragging fires `onControlChange('reasoning_effort', <level>)`, single-level/disabled → locked.

- [ ] **Step 5 — Commit** `feat(session-controls): slider control kind + effort_control helper`.

---

## WAVE 2 — per-engine (T2–T6, concurrent)

**Shared per-engine checklist** (each applies with its own mechanism):
- **A. Config field** — add `reasoning_effort: <EngineLiteral> | None = None` to the engine's `TaskConfig` (its own `Literal` enum) + `__post_init__` validation. Applied at launch (initial effort), like `model`.
- **B. Capability + levels** — extend the engine's model catalog to capture which models support graded effort + their levels (per Section 3 sources).
- **C. Declare the control** — in the engine's `session.py` controls-build, append an `effort_control(levels=…, current=…)` **when the current model supports it** (else omit; single-level/always-on → `disabled`+`why_disabled`).
- **D. `set_control` branch** — add a `reasoning_effort` branch to the engine's `Conversation.set_control` (its live channel).
- **E. Re-emit on model change** — after `set_control("model", …)`, re-derive the control set for the new model and emit an `x-optio-control-update` (so effort presence/levels follow the model). Reuse the engine's existing control-emit path.

Each task writes tests (xdist-safe) + commits; no suite run (deferred to T7).

### Task 2: codex (live, `turn/start.effort`)
- [ ] A: `reasoning_effort: Literal["none","minimal","low","medium","high","xhigh"] | None = None`.
- [ ] B: extend codex's model catalog to read the app-server model list's `supported_reasoning_efforts` (non-empty ⇒ supported) + `default_reasoning_effort`.
- [ ] C: `session.py:458-469` controls build — append `effort_control(levels=<model's supported efforts>, current=config.reasoning_effort or default)` when non-empty.
- [ ] D: `conversation.py:548-557` `set_control` — add `elif control_id == "reasoning_effort": self._requested_effort = value` (mirror the model inline-override); attach `params["effort"] = self._requested_effort` at the next `turn/start` (beside the model attach, ~487-491).
- [ ] E: re-emit controls on model change.
- [ ] Tests: `set_control("reasoning_effort","high")` → next `turn/start` carries `effort:"high"` (mock); control present only for effort-capable models. Commit.

### Task 3: grok (live, `session/set_model` `_meta`)
- [ ] A: `reasoning_effort: Literal["low","medium","high","xhigh"] | None = None`.
- [ ] B: extend `parse_acp_models` (`models.py`) to capture each model's `_meta.supportsReasoningEffort` + `_meta.reasoningEfforts` (currently only `{modelId,name}` read).
- [ ] C: `session.py:520-531` — append `effort_control(levels=<model reasoningEfforts>, current=…)` when `supportsReasoningEffort`.
- [ ] D: `conversation.py:417-435` `set_control` — add `reasoning_effort` branch: re-send `session/set_model` for the **current** modelId with the effort in the request `_meta`. **Live-probe the exact `_meta` shape** (like the original set_model probe): try `{sessionId, modelId, _meta:{reasoningEffort:<level>}}` first, then top-level `reasoningEffort`; document the confirmed shape. Fallback if neither: relaunch `--reasoning-effort` (grok's launch flag) — but the `_meta` path is expected to work.
- [ ] E: re-emit controls on model change.
- [ ] Tests: `set_control("reasoning_effort","high")` → `session/set_model` with current model + `_meta` effort (mock). grok's general `--effort` field untouched. Commit.

### Task 4: opencode (live, per-prompt `variant`, client-side)
- [ ] A: `reasoning_effort: Literal["none","minimal","low","medium","high","xhigh","max"] | None = None` (superset).
- [ ] B: extend `parse_model_ids` (`model_probe.py`) to also read each model's `variants` map keys (currently discarded).
- [ ] C+D+E are **client-side** (opencode's model control is UI-local): in `OpencodeView.tsx`, build the effort slider from the current model's variant keys, attach the chosen `variant` to the next `prompt_async` body (beside `body.model`, `conversation.py:305-308` server-side is a no-op; the attach is client-side like the model). The control appears only when the current model has variants; re-derived when the model changes (client-side reactive).
- [ ] `session.py` widgetData: surface the per-model variants so the view can build the slider levels.
- [ ] Tests: `opencode-controls.test.tsx` — effort slider renders for a variant-capable model; selecting a level attaches `variant` to the next `prompt_async`. Commit.

### Task 5: kimi (live, `session/set_config_option` graded — fork ≥0.23.1-csillag.2)
- [ ] A: `reasoning_effort: Literal["off","low","medium","high","xhigh","max"] | None = None`.
- [ ] B/C: in `models.py` `parse_all_controls`, **replace** the current `thinking` off/on segmented branch (lines 169-182) with a `reasoning_effort` **slider** built from the fork's now-graded `configOptions` thinking option (`['off', <levels>]`, or `<levels>` without `off` for always-thinking → disabled+`ALWAYS_THINKING_REASON`). Remove the old `id="thinking"` control.
- [ ] D: `conversation.py:473-508` `set_control` — the generic fall-through already sends `session/set_config_option {sessionId, configId:<id>, value}`; ensure `control_id == "reasoning_effort"` maps to `configId:"thinking"` with the graded `value` (add an explicit branch mapping the id, since the control id is `reasoning_effort` but the ACP configId is `thinking`).
- [ ] E: kimi re-emits controls via `config_option_update` already (the fork emits updated configOptions on change) — confirm the reducer folds it.
- [ ] Note the fork floor in `host_actions.py` / docs (smart-install already pins the fork).
- [ ] Tests: `parse_all_controls` emits a `reasoning_effort` slider (not `thinking`) from a graded configOptions fixture; always-thinking → disabled; `set_control("reasoning_effort","high")` → `session/set_config_option {configId:"thinking", value:"high"}`. Commit.

### Task 6: claudecode (restart, reuse existing loop)
- [ ] A: `reasoning_effort: Literal["low","medium","high","xhigh","max"] | None = None`.
- [ ] B: extend claude's `models.py` to capture per-model `effort` capability + `default_effort`.
- [ ] C: `session.py:544-553` — append `effort_control(levels=…, current=…)` when the model advertises `effort`.
- [ ] D: `conversation.py:234-245` `set_control` — add a `reasoning_effort` branch: store `self.requested_effort = value` and fire a change Event (add `effort_change_requested` beside `model_change_requested`, or reuse the model Event with a combined "relaunch requested" flag).
- [ ] E: extend the relaunch loop (`session.py:582-619`): add an `effort_task` arm alongside `model_task`; on fire, `begin_restart()` + relaunch `_spawn(current_model, do_continue=True)` with the new `--effort` in the argv builder (`build_conversation_argv`/`build_claude_flags`). Controls re-emit on relaunch (effort follows the model).
- [ ] Tests: `set_control("reasoning_effort","high")` fires the restart Event + the relaunch argv carries `--effort high`; control present only for effort-capable models. Commit.

---

## WAVE 3

### Task 7: full verification
- [ ] **Python:** `make test` (or per-package xdist `-m "not serial"` + `-m serial`; optio-core serial) over optio-agents + codex/grok/opencode/kimicode/claudecode + demo. Fix fallout.
- [ ] **TS:** `cd packages/optio-conversation-ui && ./node_modules/.bin/tsc --noEmit && pnpm test`.
- [ ] **Grep:** `grep -rn "kind: 'slider'\|ControlKind" packages/optio-agents/src packages/optio-conversation-ui/src` shows the new kind wired in both contracts.
- [ ] **Live-probe note (not automated):** the grok `_meta` effort key (Task 3) and a kimi authed `session/set_config_option` graded round-trip are real-binary live-test items (need authed seeds) — record in the parity/real-E2E ledger, don't block on them.
- [ ] Commit any fixes.

## Self-review notes
- **Spec coverage:** slider kind + effort_control (T1) · reasoning_effort field + control + set_control + presence per engine (T2-T6) · cursor folded/no-task, antigravity excluded (design) · testing (per task + T7). Mapped.
- **Parallel shape:** file-disjoint by engine; T1 foundation first; verification in T7.
- **Known live-probes:** grok `_meta` key, kimi authed round-trip (real-binary, documented fallback / ledger).
