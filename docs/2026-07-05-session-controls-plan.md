# Generic Session Controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the six wrappers' bespoke per-engine model selector with one engine-neutral `SessionControl` contract that the conversation UI renders generically (select / boolean / segmented) and channels back via a generic `set_control(id, value)` round-trip — unlocking kimi's thinking + mode controls along the way.

**Architecture:** A frozen `SessionControl` dataclass in `optio-agents` (Python) mirrored as a TS type in `optio-conversation-ui`. Each wrapper emits `controls: SessionControl[]` in its `widgetData` (seed) and folds live updates in its reducer; the shared `ConversationView` renders `state.controls`; changes call a new `onControlChange` prop that dispatches per-engine (`POST /control` to the listener, or UI-local for opencode) → `Conversation.set_control` → native mechanism (ACP inline / process restart / next-prompt).

**Tech Stack:** Python 3.11 (frozen dataclasses, `typing.Protocol`), pytest; TypeScript + React + antd ^5.29.3, vitest, pnpm.

## Global Constraints

- **antd pinned `^5.29.3`** — no v6, no new UI dependency. Use `Select`, `Switch`, `Segmented`, `Tooltip` from `antd` (already available).
- **Every antd control carries a `data-testid`** (package convention).
- **Python contract style:** `from __future__ import annotations`; `@dataclass(frozen=True)` for value objects; `Literal[...]` for closed enums; forward-ref string annotations for optional-dep types (mirror `SeedManifest` in `seeds.py`).
- **TS build:** run `node_modules/.bin/tsc` directly (never `npx tsc`); tests via `pnpm --filter optio-conversation-ui test` (vitest).
- **Python tests:** use a `.venv` inside the checkout; MongoDB-dependent tests use the `mongo_db` fixture (Docker / mongodb-memory-server) — never a local `mongod`.
- **Parallel-shaped execution (OVERRIDES the skill's per-task RED→GREEN):** every file is owned by exactly one task; file-disjoint tasks run concurrently; **ALL verification is deferred** to the final wave. Engine tasks write code + tests and commit **without** running the suite. Accept that the tree may not compile mid-execution. Task 12 runs everything and fixes fallout.
- **No `Co-Authored-By` trailer** in commits.
- Branch: `csillag/kimicode` (this feature ships with the kimi merge).

---

## Execution Model — three waves

```
Wave 1 (concurrent): T1 optio-agents contract  ∥  T2 conversation-ui shared UI
Wave 2 (concurrent): T3 kimi ∥ T4 grok ∥ T5 cursor ∥ T6 claudecode ∥ T7 codex ∥ T8 opencode
Wave 3 (sequential): T9 remove modelSelector slot → T10 remove request_model_change → T11 demo/dashboard widgetData → T12 full verification
```

Wave 2 depends on Wave 1 (contract types + shared renderer). The six Wave-2 tasks are file-disjoint (each owns its `optio-<engine>/` package + its `conversation-ui/src/<engine>/` files) → fully parallel. The shared `acp/events.ts` (grok + cursor) is owned by **T2**, not by T4/T5, to avoid a shared-file conflict.

### File-ownership map (no file appears twice)

| Task | Files owned |
|---|---|
| T1 | `optio-agents/src/optio_agents/session_controls.py` (new), `.../conversation.py`, `.../__init__.py`, `optio-agents/tests/test_session_controls.py` (new) |
| T2 | `conversation-ui/src/chat.ts`, `.../ConversationView.tsx`, `.../index.ts`, `.../acp/events.ts`, `.../__tests__/session-controls-render.test.tsx` (new), `.../__tests__/chat-controls.test.ts` (new) |
| T3 kimi | `optio-kimicode/src/optio_kimicode/{conversation.py,session.py,conversation_listener.py,models.py}`, `conversation-ui/src/kimicode/{events.ts,KimiCodeView.tsx}`, their tests |
| T4 grok | `optio-grok/src/optio_grok/{conversation.py,session.py,conversation_listener.py,models.py}`, `conversation-ui/src/grok/GrokView.tsx`, their tests |
| T5 cursor | `optio-cursor/src/optio_cursor/{conversation.py,session.py,conversation_listener.py,models.py}`, `conversation-ui/src/cursor/CursorView.tsx`, their tests |
| T6 claudecode | `optio-claudecode/src/optio_claudecode/{conversation.py,session.py,conversation_listener.py,models.py}`, `conversation-ui/src/claudecode/{events.ts,ClaudeCodeView.tsx}`, their tests |
| T7 codex | `optio-codex/src/optio_codex/{conversation.py,session.py,conversation_listener.py,models.py}`, `conversation-ui/src/codex/{events.ts,CodexView.tsx}`, their tests |
| T8 opencode | `optio-opencode/src/optio_opencode/session.py`, `conversation-ui/src/opencode/{events.ts,OpencodeView.tsx}`, their tests |
| T9 | `conversation-ui/src/ConversationView.tsx` (2nd pass — remove `modelSelector`) |
| T10 | `optio-agents/src/optio_agents/conversation.py` (2nd pass — remove `request_model_change`) |
| T11 | `optio-demo/**`, `optio-dashboard/**` widgetData references (if any) |
| T12 | none (runs suites, fixes fallout in whichever file breaks) |

> T9 re-touches `ConversationView.tsx` (owned by T2) and T10 re-touches `conversation.py` (owned by T1) — legal because Wave 3 is **sequential after** Waves 1–2; no concurrent writer.

---

## Shared Contract (pinned — every task consumes these exact shapes)

**Python `SessionControl` (defined in T1):**

```python
ControlKind = Literal["select", "boolean", "segmented"]

@dataclass(frozen=True)
class ControlOption:
    value: str
    label: str
    description: str | None = None
    disabled: bool = False
    why_disabled: str | None = None

@dataclass(frozen=True)
class SessionControl:
    id: str
    kind: ControlKind
    label: str
    value: "str | bool"
    category: str | None = None
    description: str | None = None
    options: "list[ControlOption] | None" = None   # kind == "select"
    levels: "list[str] | None" = None               # kind == "segmented"

    def to_dict(self) -> dict: ...   # camelCase keys for the UI: whyDisabled
```

**widgetData shape (each `session.py` emits):** replace `"models"`, `"currentModel"`, `"showModelSelector"` with:

```python
"showSessionControls": config.show_session_controls,
"controls": [c.to_dict() for c in controls],   # list[SessionControl] serialized
```

**TS `SessionControl` (defined in T2, `chat.ts`):**

```ts
export interface ControlOption {
  value: string; label: string; description?: string;
  disabled?: boolean; whyDisabled?: string;
}
export interface SessionControl {
  id: string;
  kind: 'select' | 'boolean' | 'segmented';
  label: string;
  value: string | boolean;
  category?: string;
  description?: string;
  options?: ControlOption[];   // kind === 'select'
  levels?: string[];            // kind === 'segmented'
}
```

**`/control` listener route (each `conversation_listener.py` except opencode):** `POST {proxy}control` with body `{ "id": string, "value": string | boolean }` → `self._conversation.set_control(id, value)`.

**View wiring (each `<Engine>View.tsx`):** seed reducer initial state `{ ...initialChatState, controls: controlsFromWidgetData }`; pass `controls={state.controls}` and `onControlChange={handleControlChange}` to `ConversationView`; drop the bespoke `modelSelector` `<Select>`.

---

## WAVE 1

### Task 1: optio-agents `SessionControl` contract

**Files:**
- Create: `packages/optio-agents/src/optio_agents/session_controls.py`
- Modify: `packages/optio-agents/src/optio_agents/conversation.py` (add `set_control` to the `Conversation` Protocol; keep `request_model_change` for now)
- Modify: `packages/optio-agents/src/optio_agents/__init__.py` (export)
- Test: `packages/optio-agents/tests/test_session_controls.py`

**Interfaces:**
- Produces: `SessionControl`, `ControlOption`, `ControlKind`, and a helper `model_control(*, models: list[dict], current: str | None, label: str = "Model") -> SessionControl` that builds the `id="model"` select from a `[{id,label,disabled?,disabledReason?}]` catalog. `SessionControl.to_dict()` emits camelCase (`whyDisabled`). Adds `Conversation.set_control(self, control_id: str, value: "str | bool") -> None` to the protocol.

- [ ] **Step 1: Write `session_controls.py`**

```python
"""Engine-neutral session-control contract.

A SessionControl is one live, UI-renderable knob a wrapper exposes for its
running session (model, thinking effort, permission/plan mode, ...). It
generalizes the former bespoke model selector: the model is just the
``id="model"`` control. Wrappers emit these (serialized) in their widgetData
and implement ``Conversation.set_control`` to push value changes to the native
transport. Mirrors the frozen-dataclass style of ``seeds.SeedManifest``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ControlKind = Literal["select", "boolean", "segmented"]


@dataclass(frozen=True)
class ControlOption:
    """One member of a ``select`` control's option list."""
    value: str
    label: str
    description: str | None = None
    disabled: bool = False
    why_disabled: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"value": self.value, "label": self.label, "disabled": self.disabled}
        if self.description is not None:
            d["description"] = self.description
        if self.why_disabled is not None:
            d["whyDisabled"] = self.why_disabled
        return d


@dataclass(frozen=True)
class SessionControl:
    """One engine-neutral session control. ``value`` is the current value;
    ``options`` applies to ``select``, ``levels`` (ordered) to ``segmented``,
    and ``boolean`` carries neither."""
    id: str
    kind: ControlKind
    label: str
    value: "str | bool"
    category: str | None = None
    description: str | None = None
    options: "list[ControlOption] | None" = None
    levels: "list[str] | None" = None

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "kind": self.kind, "label": self.label, "value": self.value}
        if self.category is not None:
            d["category"] = self.category
        if self.description is not None:
            d["description"] = self.description
        if self.options is not None:
            d["options"] = [o.to_dict() for o in self.options]
        if self.levels is not None:
            d["levels"] = list(self.levels)
        return d


def model_control(
    *, models: list[dict], current: str | None, label: str = "Model"
) -> SessionControl:
    """Build the ``id="model"`` select from a wrapper's model catalog
    (``[{id,label,disabled?,disabledReason?}]`` — the shape every wrapper's
    ``models.py`` already produces)."""
    options = [
        ControlOption(
            value=m["id"],
            label=m.get("label", m["id"]),
            disabled=bool(m.get("disabled", False)),
            why_disabled=m.get("disabledReason"),
        )
        for m in models
    ]
    return SessionControl(
        id="model", kind="select", label=label, category="model",
        value=current or "", options=options,
    )
```

- [ ] **Step 2: Add `set_control` to the `Conversation` protocol**

In `packages/optio-agents/src/optio_agents/conversation.py`, inside the `Conversation` `Protocol` (after `interrupt`), add:

```python
    async def set_control(self, control_id: str, value: "str | bool") -> None:
        """Push a session-control value change to the native transport
        (generalizes model selection). ``control_id`` matches a
        ``SessionControl.id`` the wrapper published. No-op for unknown ids."""
        ...
```

- [ ] **Step 3: Export from `__init__.py`**

Add (grouped with the other whole-module imports):

```python
from optio_agents import session_controls
from optio_agents.session_controls import ControlOption, SessionControl
```

and add `"session_controls"`, `"SessionControl"`, `"ControlOption"` to `__all__`.

- [ ] **Step 4: Write `tests/test_session_controls.py`** (do not run — Wave 3 runs it)

```python
from optio_agents.session_controls import ControlOption, SessionControl, model_control


def test_select_to_dict_camelcase_and_disabled():
    c = SessionControl(
        id="model", kind="select", label="Model", value="a", category="model",
        options=[
            ControlOption("a", "A"),
            ControlOption("b", "B", disabled=True, why_disabled="plan-gated"),
        ],
    )
    d = c.to_dict()
    assert d["id"] == "model" and d["kind"] == "select" and d["value"] == "a"
    assert d["category"] == "model"
    assert d["options"][0] == {"value": "a", "label": "A", "disabled": False}
    assert d["options"][1] == {
        "value": "b", "label": "B", "disabled": True, "whyDisabled": "plan-gated",
    }


def test_segmented_levels_and_boolean_shapes():
    seg = SessionControl(id="thinking", kind="segmented", label="Thinking",
                         value="high", levels=["low", "high", "max"])
    assert seg.to_dict()["levels"] == ["low", "high", "max"]
    assert "options" not in seg.to_dict()
    b = SessionControl(id="wide", kind="boolean", label="Wide", value=True)
    bd = b.to_dict()
    assert bd["value"] is True and "options" not in bd and "levels" not in bd


def test_model_control_helper():
    c = model_control(
        models=[{"id": "m1", "label": "M1"},
                {"id": "m2", "label": "M2", "disabled": True, "disabledReason": "no plan"}],
        current="m1",
    )
    assert c.id == "model" and c.kind == "select" and c.value == "m1"
    opts = c.to_dict()["options"]
    assert opts[1]["disabled"] is True and opts[1]["whyDisabled"] == "no plan"
```

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/session_controls.py \
        packages/optio-agents/src/optio_agents/conversation.py \
        packages/optio-agents/src/optio_agents/__init__.py \
        packages/optio-agents/tests/test_session_controls.py
git commit -m "feat(optio-agents): SessionControl contract + set_control protocol method"
```

---

### Task 2: conversation-ui shared renderer + `ChatState.controls`

**Files:**
- Modify: `packages/optio-conversation-ui/src/chat.ts` (add `SessionControl`/`ControlOption` types, `controls` field, `foldControlUpdate` helper)
- Modify: `packages/optio-conversation-ui/src/ConversationView.tsx` (generic controls renderer + `onControlChange` prop; keep `modelSelector` slot for now)
- Modify: `packages/optio-conversation-ui/src/index.ts` (export `SessionControl`, `ControlOption`)
- Modify: `packages/optio-conversation-ui/src/acp/events.ts` (fold `x-optio-control-update` for grok+cursor)
- Test: `packages/optio-conversation-ui/src/__tests__/chat-controls.test.ts`, `packages/optio-conversation-ui/src/__tests__/session-controls-render.test.tsx`

**Interfaces:**
- Produces: `SessionControl`, `ControlOption` (TS), `ChatState.controls: SessionControl[]`, `foldControlUpdate(state, update)`, and `ConversationViewProps.controls?: SessionControl[]` + `ConversationViewProps.onControlChange?: (id: string, value: string | boolean) => void`. A synthetic reducer event `{ type: 'x-optio-control-update', controls?: SessionControl[], id?: string, value?: string | boolean }`.

- [ ] **Step 1: Extend `chat.ts`**

Add the TS `ControlOption`/`SessionControl` interfaces (verbatim from the Shared Contract section above). Add `controls: SessionControl[]` to `ChatState` and `controls: []` to `initialChatState`. Add the fold helper:

```ts
// Merge a live control update into state.controls. Accepts either a full
// snapshot (controls) or a single-value patch ({id, value}); patch updates the
// matching control's value in place, leaving chat items untouched.
export function foldControlUpdate(
  state: ChatState,
  update: { controls?: SessionControl[]; id?: string; value?: string | boolean },
): ChatState {
  if (update.controls) return { ...state, controls: update.controls };
  if (update.id === undefined) return state;
  return {
    ...state,
    controls: state.controls.map((c) =>
      c.id === update.id ? { ...c, value: update.value as string | boolean } : c,
    ),
  };
}
```

- [ ] **Step 2: Add the generic controls renderer to `ConversationView.tsx`**

Import `Segmented` (add to the existing `antd` import line). Add to `ConversationViewProps`:

```ts
  controls?: SessionControl[];
  onControlChange?: (id: string, value: string | boolean) => void;
```

Add a renderer component (above `ConversationView`), rendering each control by kind, disabled greyed + `whyDisabled` tooltip on select options:

```tsx
function SessionControls({
  controls, disabled, onChange,
}: {
  controls: SessionControl[];
  disabled: boolean;
  onChange: (id: string, value: string | boolean) => void;
}) {
  if (!controls.length) return null;
  return (
    <>
      {controls.map((c) => {
        if (c.kind === 'boolean') {
          return (
            <Switch
              key={c.id}
              data-testid={`control-${c.id}`}
              size="small"
              checked={Boolean(c.value)}
              disabled={disabled}
              onChange={(v) => onChange(c.id, v)}
            />
          );
        }
        if (c.kind === 'segmented') {
          return (
            <Segmented
              key={c.id}
              data-testid={`control-${c.id}`}
              size="small"
              value={String(c.value)}
              disabled={disabled}
              options={(c.levels ?? []).map((l) => ({ label: l, value: l }))}
              onChange={(v) => onChange(c.id, String(v))}
            />
          );
        }
        // select
        return (
          <Select
            key={c.id}
            data-testid={`control-${c.id}`}
            size="small"
            style={{ minWidth: 180, alignSelf: 'center' }}
            placeholder={c.label}
            disabled={disabled}
            value={c.value ? String(c.value) : undefined}
            onChange={(v: string) => onChange(c.id, v)}
            options={(c.options ?? []).map((o) => ({
              label: o.label,
              value: o.value,
              disabled: o.disabled,
              title: o.whyDisabled,
            }))}
          />
        );
      })}
    </>
  );
}
```

In the input toolbar (Row 2, where `{modelSelector}` renders), render the generic controls **before** `{modelSelector}` (both coexist during migration; T9 removes `modelSelector`):

```tsx
  {props.controls && props.onControlChange ? (
    <SessionControls
      controls={props.controls}
      disabled={props.busy || props.state.closed}
      onChange={props.onControlChange}
    />
  ) : null}
  {modelSelector}
```

- [ ] **Step 3: Fold `x-optio-control-update` in the shared ACP reducer**

In `packages/optio-conversation-ui/src/acp/events.ts`, add a case at the top of `reduceAcpEvent`'s switch (import `foldControlUpdate` from `../chat`):

```ts
    case 'x-optio-control-update':
      return foldControlUpdate(state, ev);
```

- [ ] **Step 4: Export types from `index.ts`**

Add `SessionControl`, `ControlOption` to the `export type { ... } from './chat'` line and export `foldControlUpdate`.

- [ ] **Step 5: Write `__tests__/chat-controls.test.ts`** (reducer/helper)

```ts
import { describe, it, expect } from 'vitest';
import { initialChatState, foldControlUpdate, SessionControl } from '../chat';

const CTRLS: SessionControl[] = [
  { id: 'model', kind: 'select', label: 'Model', value: 'a',
    options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] },
  { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low',
    levels: ['low', 'high'] },
];

describe('foldControlUpdate', () => {
  it('snapshot replaces controls, keeps items', () => {
    const withItem = { ...initialChatState, items: [{ kind: 'user', text: 'hi', seq: 0 } as any] };
    const s = foldControlUpdate(withItem, { controls: CTRLS });
    expect(s.controls).toHaveLength(2);
    expect(s.items).toHaveLength(1);
  });
  it('value patch updates only the matching control', () => {
    const seeded = { ...initialChatState, controls: CTRLS };
    const s = foldControlUpdate(seeded, { id: 'thinking', value: 'high' });
    expect(s.controls.find((c) => c.id === 'thinking')!.value).toBe('high');
    expect(s.controls.find((c) => c.id === 'model')!.value).toBe('a');
  });
});
```

- [ ] **Step 6: Write `__tests__/session-controls-render.test.tsx`** (renderer)

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConversationView } from '../ConversationView';
import { initialChatState, SessionControl } from '../chat';

const controls: SessionControl[] = [
  { id: 'model', kind: 'select', label: 'Model', value: 'a',
    options: [{ value: 'a', label: 'A' },
              { value: 'b', label: 'B', disabled: true, whyDisabled: 'plan-gated' }] },
  { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low', levels: ['low', 'high'] },
  { id: 'wide', kind: 'boolean', label: 'Wide', value: false },
];

function base(onControlChange: any) {
  return {
    state: initialChatState, closed: false, busy: false,
    toolVerbosity: 'silent' as const, thinkingVerbosity: 'hidden' as const,
    showFileUpload: false, maxUploadBytes: 0, fileDownload: false,
    onSend: async () => true, onInterrupt: () => {}, onPermission: () => {},
    onFileDownload: () => {}, controls, onControlChange,
  };
}

describe('SessionControls renderer', () => {
  it('renders one control per kind with testids', () => {
    render(<ConversationView {...base(vi.fn())} />);
    expect(screen.getByTestId('control-model')).toBeTruthy();
    expect(screen.getByTestId('control-thinking')).toBeTruthy();
    expect(screen.getByTestId('control-wide')).toBeTruthy();
  });
  it('segmented change fires onControlChange(id, value)', () => {
    const cb = vi.fn();
    render(<ConversationView {...base(cb)} />);
    fireEvent.click(screen.getByText('high'));
    expect(cb).toHaveBeenCalledWith('thinking', 'high');
  });
  it('disabled select option shows whyDisabled tooltip title', async () => {
    render(<ConversationView {...base(vi.fn())} />);
    fireEvent.mouseDown(screen.getByTestId('control-model').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('B')).toBeTruthy());
    const opt = screen.getByText('B').closest('.ant-select-item');
    expect(opt?.getAttribute('title')).toBe('plan-gated');
  });
});
```

- [ ] **Step 7: Commit**

```bash
git add packages/optio-conversation-ui/src/chat.ts \
        packages/optio-conversation-ui/src/ConversationView.tsx \
        packages/optio-conversation-ui/src/index.ts \
        packages/optio-conversation-ui/src/acp/events.ts \
        packages/optio-conversation-ui/src/__tests__/chat-controls.test.ts \
        packages/optio-conversation-ui/src/__tests__/session-controls-render.test.tsx
git commit -m "feat(conversation-ui): generic SessionControls renderer + ChatState.controls"
```

---

## WAVE 2 — per-engine migration (T3–T8, concurrent)

Each Wave-2 task follows the same shape (**consumes** the pinned Shared Contract from T1/T2). Per engine:

**A. Python `conversation.py`** — implement `async def set_control(self, control_id, value)`; delegate `control_id == "model"` to the engine's existing set-model path; add engine-specific ids where they exist (kimi only). Keep the class's `request_model_change` for now (T10 removes it repo-wide is impractical — instead **each engine removes its own `request_model_change` here** and repoints its listener; T10 removes only the *protocol declaration*).

**B. Python `session.py`** — build `controls: list[SessionControl]` from the engine's model catalog via `optio_agents.session_controls.model_control(...)` (+ kimi's thinking/mode); emit `"controls"` + `"showSessionControls"` in `set_widget_data(...)`, removing `"models"`/`"currentModel"`/`"showModelSelector"`. Rename the config field `show_model_selector` → `show_session_controls` (keep `default_model`).

**C. Python `conversation_listener.py`** — replace `_handle_model` / the `/model` route with `_handle_control` / `/control` (`body: {id, value}` → `self._conversation.set_control(id, value)`). (opencode: no listener — skip.)

**D. TS `<Engine>View.tsx`** — seed reducer initial state with `controls` from `widgetData.controls`; drop the bespoke `modelSelector` `<Select>`; pass `controls={state.controls}` + `onControlChange`. The `onControlChange` handler `POST`s `{proxy}control` with `{id, value}` (opencode: UI-local, see T8).

**E. TS `<engine>/events.ts`** — fold live control updates into `state.controls` (engine-specific event → `foldControlUpdate`). grok/cursor inherit this from T2's shared `acp/events.ts` and need no reducer edit.

Each task ends with a commit; **no test run** (deferred to T12).

---

### Task 3: kimi (template — the only engine gaining new controls)

**Files:** `optio-kimicode/src/optio_kimicode/{conversation.py,session.py,conversation_listener.py,models.py}`; `conversation-ui/src/kimicode/{events.ts,KimiCodeView.tsx}`; tests `optio-kimicode/tests/test_conversation_controls.py`, `conversation-ui/src/__tests__/kimicode-controls.test.tsx`.

**Interfaces:**
- Consumes: `optio_agents.session_controls.{SessionControl, ControlOption, model_control}`; TS `SessionControl`, `foldControlUpdate`, `ConversationView` `controls`/`onControlChange`.
- Produces: kimi's `set_control` mapping (`model` → `session/set_model`; `thinking`/`mode` → `session/set_config_option`); `parse_all_controls(session_config_options, default_model)` in `models.py`.

- [ ] **Step 1 — `models.py`: project ALL config options to controls.** Add:

```python
def parse_all_controls(session_config_options, default_model=None):
    """Project kimi's ACP configOptions into SessionControl[]. The 'model'
    option -> select; 'thinking' -> segmented (effort levels); 'mode' -> select.
    Unknown option ids are surfaced as generic select/boolean by their ACP type.
    """
    from optio_agents.session_controls import ControlOption, SessionControl
    controls = []
    for opt in (session_config_options or []):
        if not isinstance(opt, dict):
            continue
        oid = opt.get("id")
        options = [ControlOption(value=o.get("value"), label=o.get("name", o.get("value")),
                                 description=o.get("description"))
                   for o in (opt.get("options") or []) if isinstance(o, dict)]
        cur = opt.get("currentValue")
        if oid == "model":
            controls.append(SessionControl(id="model", kind="select", label="Model",
                                           category="model",
                                           value=(default_model or cur or ""), options=options))
        elif oid == "thinking":
            levels = [o.value for o in options]
            controls.append(SessionControl(id="thinking", kind="segmented", label="Thinking",
                                           category="thought_level",
                                           value=(cur or (levels[0] if levels else "")),
                                           levels=levels))
        elif oid == "mode":
            controls.append(SessionControl(id="mode", kind="select", label="Mode",
                                           category="mode", value=(cur or ""), options=options))
        elif opt.get("type") == "boolean":
            controls.append(SessionControl(id=oid, kind="boolean", label=oid.title(),
                                           value=bool(cur)))
        else:
            controls.append(SessionControl(id=oid, kind="select", label=(oid or "").title(),
                                           value=(cur or ""), options=options))
    return controls
```

- [ ] **Step 2 — `conversation.py`: `set_control`.** Add (and remove `request_model_change`/`_set_model`, folding model into `set_control`):

```python
    async def set_control(self, control_id: str, value) -> None:
        if control_id == "model":
            self.current_model_id = value
            await self._request("session/set_model",
                                {"sessionId": self._session_id, "modelId": value})
            return
        # thinking / mode / other configOptions -> ACP set_config_option.
        # NOTE: verify the exact param names against kimi-code
        # packages/acp-adapter/src/config-options.ts before relying on this;
        # mirror the verified session/set_model shape.
        await self._request("session/set_config_option",
                            {"sessionId": self._session_id, "optionId": control_id, "value": value})
```

> **Verification note (real unknown, not a placeholder):** `session/set_model {sessionId, modelId}` is live-verified. `session/set_config_option`'s param names (`optionId`/`value` vs `id`) are **not** yet confirmed — the implementer must read the fork's `packages/acp-adapter/src/config-options.ts` (`session/set_config_option` handler) and adjust before T12. This is the kimi-specific new surface; treat like the grok `session/set_model` pinning note in `optio-grok/src/optio_grok/models.py`.

- [ ] **Step 3 — `conversation.py`: fold `config_option_update` → emit a control patch.** kimi already passes `config_option_update` to `on_event`. In the ACP notification handler, on `config_option_update`, additionally surface `{ type: 'x-optio-control-update', id: <optionId>, value: <newValue> }` to `on_event` so the reducer patches `state.controls`. (Keep the raw passthrough too.)

- [ ] **Step 4 — `session.py`: emit `controls` widgetData.** Replace the `"models"`/`"currentModel"`/`"showModelSelector"` block (session.py:463-478) with:

```python
from optio_agents.session_controls import model_control  # if only model needed
from optio_kimicode import models as kimi_models
controls = kimi_models.parse_all_controls(
    conversation.session_config_options, default_model=config.default_model)
await ctx.set_widget_data({
    "protocol": "kimicode",
    "showSessionControls": config.show_session_controls,
    "controls": [c.to_dict() for c in controls],
    # ...unchanged keys (toolVerbosity, thinkingVerbosity, showFileUpload, ...)
})
```

Rename `config.show_model_selector` → `config.show_session_controls` in the kimi config dataclass/types (`types.py` / wherever `show_model_selector` is defined) and its default (True).

- [ ] **Step 5 — `conversation_listener.py`: `/control` route.** Replace `_handle_model` (lines ~248-262) with:

```python
    async def _handle_control(self, request):
        body = await request.json()
        cid, value = body.get("id"), body.get("value")
        if cid is None:
            return web.json_response({"error": "missing id"}, status=400)
        await self._conversation.set_control(cid, value)
        return web.json_response({"ok": True})
```

and route `POST /control` → `_handle_control` (drop the `/model` route).

- [ ] **Step 6 — `kimicode/events.ts`: fold control updates.** Replace the `config_option_update` no-op (events.ts:192-194) — and add a case — so the reducer folds:

```ts
    case 'x-optio-control-update':
      return foldControlUpdate(state, ev);
```

(import `foldControlUpdate` from `../chat`).

- [ ] **Step 7 — `KimiCodeView.tsx`: seed controls + drop `<Select>`.** Seed the reducer initial state with `controls` from `widgetData.controls`; delete the bespoke model `<Select>` (KimiCodeView.tsx:151-156 + the `modelSelector={...}` prop); pass to `ConversationView`:

```tsx
  controls={state.controls}
  onControlChange={(id, value) => {
    // optimistic + POST /control
    setState((s) => foldControlUpdate(s, { id, value }));
    void post('control', { id, value });
  }}
```

Read initial controls: `const initialControls = ((props.process.widgetData as any)?.controls ?? []) as SessionControl[];` and use `{ ...initialChatState, controls: initialControls }` as the reducer's initial state.

- [ ] **Step 8 — Tests** (write, don't run):

`optio-kimicode/tests/test_conversation_controls.py` — a fake transport asserting `set_control("model","x")` issues `session/set_model {sessionId,modelId:"x"}` and `set_control("thinking","high")` issues `session/set_config_option` with the option id + value; and `parse_all_controls` yields model(select)+thinking(segmented)+mode(select) from a sample configOptions list.

`conversation-ui/src/__tests__/kimicode-controls.test.tsx` — render `KimiCodeView` with `widgetData.controls` = [model, thinking, mode]; assert `control-model`/`control-thinking`/`control-mode` present; selecting a model POSTs `/control {id:'model', value}`.

- [ ] **Step 9 — Commit**

```bash
git add packages/optio-kimicode/ packages/optio-conversation-ui/src/kimicode/ \
        packages/optio-conversation-ui/src/__tests__/kimicode-controls.test.tsx
git commit -m "feat(optio-kimicode): migrate to SessionControls (model + thinking + mode)"
```

---

### Task 4: grok (model-only, ACP inline)

**Files:** `optio-grok/src/optio_grok/{conversation.py,session.py,conversation_listener.py,models.py}`; `conversation-ui/src/grok/GrokView.tsx`; tests `optio-grok/tests/test_conversation_controls.py`, `conversation-ui/src/__tests__/grok-controls.test.tsx`.

**Interfaces:** Consumes T1/T2 contract + T2's shared `acp/events.ts` fold (grok's reducer is a re-export — **no `grok/events.ts` edit**).

- [ ] **Step 1 — `conversation.py`: `set_control`** (replace `request_model_change`/`_set_model`):

```python
    async def set_control(self, control_id: str, value) -> None:
        if control_id != "model":
            return  # grok exposes only the model control
        self.current_model_id = value
        await self._request("session/set_model",
                            {"sessionId": self._session_id, "modelId": value})
```

- [ ] **Step 2 — `session.py`: emit `controls`.** Replace the `models`/`currentModel`/`showModelSelector` block (session.py:428-440) with:

```python
from optio_agents.session_controls import model_control
model_list = grok_models.fetch_available_models(conversation.session_models, host=..., grok_path=...)
current_model = config.default_model or model_list.get("default")
control = model_control(models=model_list["models"], current=current_model)
await ctx.set_widget_data({
    "protocol": "grok",
    "showSessionControls": config.show_session_controls,
    "controls": [control.to_dict()],
    # ...unchanged keys...
})
```

Rename `show_model_selector` → `show_session_controls` in grok's config.

- [ ] **Step 3 — `conversation_listener.py`: `/control` route** — same `_handle_control` body as Task 3 Step 5 (grok listener line ~259); drop `/model`.

- [ ] **Step 4 — `GrokView.tsx`: seed controls + drop `<Select>`** — same wiring as Task 3 Step 7 (GrokView.tsx:150-155), `post('control', {id, value})`.

- [ ] **Step 5 — Tests** (write, don't run): `test_conversation_controls.py` asserts `set_control("model","x")` → `session/set_model {sessionId,modelId:"x"}` and `set_control("thinking",...)` is a no-op; `grok-controls.test.tsx` renders `GrokView` with one model control, selecting POSTs `/control`.

- [ ] **Step 6 — Commit** `feat(optio-grok): migrate model selector to SessionControls`.

---

### Task 5: cursor (model-only, ACP inline + probe)

**Files:** `optio-cursor/src/optio_cursor/{conversation.py,session.py,conversation_listener.py,models.py}`; `conversation-ui/src/cursor/CursorView.tsx`; tests.

**Interfaces:** Consumes T1/T2 + shared `acp/events.ts` fold (cursor reducer is a re-export — **no `cursor/events.ts` edit**). Cursor keeps `set_active_model` (awaited probe helper) — do **not** remove it; only fold `request_model_change` into `set_control`.

- [ ] **Step 1 — `conversation.py`: `set_control`** (mirror Task 4 Step 1; keep `set_active_model` + `reset_session` intact — the model probe uses them):

```python
    async def set_control(self, control_id: str, value) -> None:
        if control_id != "model":
            return
        await self.set_active_model(value)   # reuse the awaited session/set_model helper
```

- [ ] **Step 2 — `session.py`: emit `controls`.** Replace session.py:531-570's `models`/`currentModel` block; cursor greys plan-gated models via `_probe_or_cached_models()` → those become `ControlOption.disabled=True` + `why_disabled`. `model_control(models=<probed list with disabled/disabledReason>, current=...)` already carries them through. Emit `controls` + `showSessionControls`; rename config field.

- [ ] **Step 3 — `conversation_listener.py`: `/control` route** (cursor listener line ~259) — same `_handle_control`.

- [ ] **Step 4 — `CursorView.tsx`: seed controls + drop `<Select>`** (CursorView.tsx:152-157) — same wiring, `post('control', {id, value})`.

- [ ] **Step 5 — Tests** (write, don't run): assert `set_control("model","x")` calls `set_active_model("x")` (which issues `session/set_model`); a disabled/plan-gated model surfaces as `ControlOption.disabled` with `whyDisabled`; `cursor-controls.test.tsx` renders the control incl. a disabled option tooltip.

- [ ] **Step 6 — Commit** `feat(optio-cursor): migrate model selector to SessionControls`.

---

### Task 6: claudecode (model-only, RESTART)

**Files:** `optio-claudecode/src/optio_claudecode/{conversation.py,session.py,conversation_listener.py,models.py}`; `conversation-ui/src/claudecode/{events.ts,ClaudeCodeView.tsx}`; tests.

**Interfaces:** Consumes T1/T2. Model change is restart-based via the existing `model_change_requested` Event — `set_control("model", v)` sets it; the session.py restart loop is unchanged.

- [ ] **Step 1 — `conversation.py`: `set_control`** (fold `request_model_change`, lines 234-240):

```python
    async def set_control(self, control_id: str, value) -> None:
        if control_id != "model":
            return
        self.requested_model = value
        self.model_change_requested.set()   # session.py restart loop consumes it
```

(`begin_restart`/`_finish`/the session.py restart loop at session.py:578-608 stay as-is.)

- [ ] **Step 2 — `session.py`: emit `controls`** (replace session.py:536-545 `models`/`currentModel`). Catalog from `claudecode_models.fetch_available_models(host, home_dir=...)`; `model_control(models=..., current=current_model)`. Emit `controls` + `showSessionControls`; rename config field.

- [ ] **Step 3 — `conversation_listener.py`: `/control` route** (claudecode listener line ~223) — same `_handle_control`; drop `/model`.

- [ ] **Step 4 — `claudecode/events.ts`: fold model-sniff → control patch.** The view currently sniffs the model from `system.init`/`message.model` (ClaudeCodeView.tsx:55-64). Move/duplicate that into the reducer: on `system`/`init` with a `model`, emit `foldControlUpdate(state, { id: 'model', value: strippedModel })` (strip any `[variant]` suffix). Add the `x-optio-control-update` case too.

- [ ] **Step 5 — `ClaudeCodeView.tsx`: seed controls + drop `<Select>`** (lines 26-31 widgetData reads + 159-175 `<Select>`). Seed `state.controls` from `widgetData.controls`; `onControlChange` → optimistic `foldControlUpdate` + `post('control', {id, value})` (engine relaunches).

- [ ] **Step 6 — Tests** (write, don't run): `set_control("model","x")` sets `requested_model` + fires `model_change_requested`; reducer folds a `system.init` model into `state.controls`; view renders + POSTs `/control`.

- [ ] **Step 7 — Commit** `feat(optio-claudecode): migrate model selector to SessionControls (restart)`.

---

### Task 7: codex (model-only, RESTART — mirrors claudecode)

**Files:** `optio-codex/src/optio_codex/{conversation.py,session.py,conversation_listener.py,models.py}`; `conversation-ui/src/codex/{events.ts,CodexView.tsx}`; tests.

**Interfaces:** Consumes T1/T2. Codex uses the same restart mechanism as claudecode (`model_change_requested` Event, `conversation.py:485` `request_model_change`; reducer `reduceCodexEvent` at `codex/events.ts:104`).

- [ ] **Step 1 — `conversation.py`: `set_control`** — mirror Task 6 Step 1 (set `requested_model` + `model_change_requested.set()`). Read the codex `conversation.py` restart fields first; adapt names if they differ from claudecode.
- [ ] **Step 2 — `session.py`: emit `controls`** — mirror Task 6 Step 2 with codex's `models.py` catalog + widgetData block; rename config field.
- [ ] **Step 3 — `conversation_listener.py`: `/control` route** (codex listener line ~260) — same `_handle_control`.
- [ ] **Step 4 — `codex/events.ts`: fold model updates** — add the `x-optio-control-update` case; if codex sniffs model from its stream like claudecode, fold it into `state.controls`.
- [ ] **Step 5 — `CodexView.tsx`: seed controls + drop `<Select>`** — mirror Task 6 Step 5.
- [ ] **Step 6 — Tests** (write, don't run): mirror Task 6 tests for codex.
- [ ] **Step 7 — Commit** `feat(optio-codex): migrate model selector to SessionControls (restart)`.

---

### Task 8: opencode (model-only, UI-local — no Python model surface)

**Files:** `optio-opencode/src/optio_opencode/session.py`; `conversation-ui/src/opencode/{events.ts,OpencodeView.tsx}`; tests `conversation-ui/src/__tests__/opencode-controls.test.tsx`.

**Interfaces:** Consumes T1/T2. opencode has **no** `conversation.py` model method, **no** `conversation_listener.py`; the model catalog is UI-fetched (`config/providers`) and applied inline per prompt. `set_control` does **not** apply here — the model control is fully UI-local.

- [ ] **Step 1 — `OpencodeView.tsx`: build the model control from the provider fetch.** Keep the existing `parseProviders`/`lastModelFromHistory` resolution (OpencodeView.tsx:121-150). Instead of the bespoke `<Select>` (lines 223-243), assemble a `SessionControl` (`id:"model", kind:"select"`, options from the provider groups flattened to `{value:"providerID/modelID", label}`, value = current `providerID/modelID`) and drive `state.controls` from it. `onControlChange('model', v)` sets local `currentModel = {providerID, modelID}` (split `v`) — applied inline on next `prompt_async` (unchanged, OpencodeView.tsx:171-173). No POST.

> opencode's provider options are **grouped** (per provider). v1 flattens to a single-level select (label prefixed by provider name if needed) to fit the generic `options[]`. Grouped `optgroup` support is out of scope (YAGNI); note the flattening in a code comment.

- [ ] **Step 2 — `opencode/events.ts`: fold model updates.** Add the `x-optio-control-update` case → `foldControlUpdate`. When the resolved model changes (history/effect), emit/fold `{ id:'model', value }` so `state.controls` stays in sync. Keep `parseProviders`/`lastModelFromHistory` exports.

- [ ] **Step 3 — `session.py`: widgetData rename.** opencode's `conversation_widget_data` (session.py:79-93) carries `showModelSelector` + `defaultModel`. Rename `showModelSelector` → `showSessionControls`; keep `defaultModel` (the view still resolves it). Do **not** add a Python `controls` list (catalog is UI-fetched) — the view builds controls itself. Keep `_write_seed_model_config`/`_resolve_session_model` (session.py:990-1071) unchanged.

- [ ] **Step 4 — Tests** (write, don't run): adapt the existing `opencode-model-widget.test.tsx` pattern → `opencode-controls.test.tsx`: render `OpencodeView` with mocked `config/providers`; assert `control-model` renders; selecting a model attaches `{providerID, modelID}` to the next `prompt_async` body (same assertion as the current model-widget test, retargeted to `control-model`).

- [ ] **Step 5 — Commit** `feat(optio-opencode): migrate model selector to SessionControls (UI-local)`.

---

## WAVE 3 — cleanup + verification (sequential)

### Task 9: remove the `modelSelector` slot from `ConversationView`

**Files:** `packages/optio-conversation-ui/src/ConversationView.tsx`

- [ ] **Step 1:** Remove `modelSelector?: React.ReactNode` from `ConversationViewProps` and the `{modelSelector}` render in Row 2 (all six views now pass `controls`/`onControlChange` instead). The `SessionControls` renderer stays.
- [ ] **Step 2:** Grep the package for `modelSelector=` — expect zero hits (`grep -rn "modelSelector" packages/optio-conversation-ui/src`). If any remain, a Wave-2 view missed the drop; fix in that view's file.
- [ ] **Step 3:** Commit `refactor(conversation-ui): drop the bespoke modelSelector slot`.

### Task 10: remove `request_model_change` from the protocol

**Files:** `packages/optio-agents/src/optio_agents/conversation.py`

- [ ] **Step 1:** Remove the `request_model_change` stub from the `Conversation` Protocol (all six wrappers now implement `set_control`; none is called via `request_model_change` anymore).
- [ ] **Step 2:** Grep the repo: `grep -rn "request_model_change" packages/` — expect zero hits. If a wrapper still defines it, that wrapper's Wave-2 task left it; remove there.
- [ ] **Step 3:** Commit `refactor(optio-agents): drop request_model_change (superseded by set_control)`.

### Task 11: demo / dashboard widgetData references

**Files:** `packages/optio-demo/**`, `packages/optio-dashboard/**` (only if they read `models`/`currentModel`/`showModelSelector`)

- [ ] **Step 1:** `grep -rn "showModelSelector\|currentModel\|\"models\"\|show_model_selector" packages/optio-demo packages/optio-dashboard`. For each hit, migrate to `showSessionControls`/`controls`. If zero hits, skip — no commit.
- [ ] **Step 2 (if changes):** Commit `chore(demo,dashboard): follow session-controls widgetData rename`.

### Task 12: full verification

**Files:** none up front — fix fallout wherever it surfaces.

- [ ] **Step 1 — Python suites.** In a `.venv`, run per package (install each in editable mode first):
  ```bash
  pytest packages/optio-agents/tests -q
  pytest packages/optio-kimicode/tests -q
  pytest packages/optio-grok/tests -q
  pytest packages/optio-cursor/tests -q
  pytest packages/optio-claudecode/tests -q
  pytest packages/optio-codex/tests -q
  pytest packages/optio-opencode/tests -q
  ```
  Expected: all pass. Fix any red (usually a leftover `models=`/`show_model_selector` reference or a `set_control` signature mismatch).
- [ ] **Step 2 — TS typecheck + tests.**
  ```bash
  cd packages/optio-conversation-ui && ./node_modules/.bin/tsc --noEmit && pnpm test
  ```
  Expected: no type errors; all vitest pass. Fix any red.
- [ ] **Step 3 — grep for orphaned old surface** (must be empty):
  ```bash
  grep -rn "onModelChange\|modelSelector\|request_model_change" packages/*/src || echo CLEAN
  grep -rn "showModelSelector\|\"currentModel\"" packages/optio-*/src || echo CLEAN
  ```
- [ ] **Step 4 — Commit any fixes** `fix(session-controls): resolve verification fallout`.
- [ ] **Step 5 — Manual live-test note (not automated).** Launch a kimi conversation task, confirm: model dropdown + thinking segmented + mode select render; changing thinking mid-session takes effect (agent reasoning depth changes) and the segmented value persists (echo folds). Confirm each other engine still switches model. Report results to the user; do not merge without explicit approval.

---

## Self-review notes

- **Spec coverage:** contract (T1) · TS mirror + renderer (T2) · six-engine migration incl. kimi thinking/mode (T3–T8) · `set_control` round-trip (each engine + listener) · widgetData-seed data flow (each `session.py` + view) · full migration / old-path removal (T9–T11) · testing (per task + T12). All spec sections mapped.
- **Parallel shape:** every file owned once (see map); Wave-2 tasks file-disjoint; shared `acp/events.ts` owned by T2 only; all verification in T12.
- **Known real unknowns (flagged, not placeholders):** kimi `session/set_config_option` param names (T3 Step 2 verification note); cursor `session/set_model` runtime-unverified (existing restart fallback documented in `optio-cursor/.../models.py`); codex restart-field names (T7 Step 1 says read first).
