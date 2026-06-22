# Opencode Conversation-Mode Model Switching — Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a session-sticky model picker to the opencode conversation widget, driven by a per-task default-model config and a visibility flag — Phase 1 (opencode + UI) of the engine-parity feature in `docs/2026-06-21-opencode-conversation-model-switching-design.md`.

**Architecture:** Almost entirely client-side in `optio-conversation-ui`'s opencode adapter. The widget discovers models from opencode's `GET /config/providers` (through the existing generic widget proxy) and attaches a `model:{providerID,modelID}` object to the existing `POST /session/<id>/prompt_async`. The only Python change is two new `OpencodeTaskConfig` fields plumbed into the conversation widget's `widgetData`.

**Tech Stack:** TypeScript + React + antd `Select` (optio-conversation-ui, vitest + @testing-library/react); Python dataclass config (optio-opencode, pytest).

## Global Constraints

- opencode only this phase; `claudecode/ClaudeCodeView.tsx` is untouched (Phase 2).
- Model selection is **session-sticky**: one `currentModel` sent on every `prompt_async` until the user changes it.
- Initial `currentModel` resolution order: (1) last assistant model in history → (2) validated `defaultModel` → (3) `/config/providers` default → (4) `null`.
- The widget **always** sends `currentModel` when non-null, whether or not the picker is shown (so `default_model` works with `show_model_selector=false`).
- `default_model` format is the string `"providerID/modelID"`.
- New config fields `default_model` and `show_model_selector` both require `conversation_ui=True`.
- Use `node_modules/.bin/tsc` / `node_modules/.bin/vitest` directly (never `npx`). TS source is shipped (no build emit; `noEmit: true`).
- Run TS commands from `/home/csillag/deai/optio/packages/optio-conversation-ui`; Python from `/home/csillag/deai/optio/packages/optio-opencode` with the package's venv.

---

## File Structure

- `packages/optio-conversation-ui/src/opencode/events.ts` — add two pure exported helpers: `parseProviders`, `lastModelFromHistory`.
- `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx` — widgetData fields, `currentModel` state, providers fetch + resolution, send wiring, picker UI.
- `packages/optio-conversation-ui/src/__tests__/opencode-model.test.ts` — helper unit tests.
- `packages/optio-conversation-ui/src/__tests__/opencode-model-widget.test.tsx` — widget tests (resolution, send-carries-model, picker visibility & switching).
- `packages/optio-opencode/src/optio_opencode/types.py` — two config fields + validation.
- `packages/optio-opencode/src/optio_opencode/session.py` — two widgetData keys.
- `packages/optio-opencode/tests/fake_opencode.py` — `GET /config/providers` fixture route.
- `packages/optio-opencode/tests/test_conversation_ui_model.py` — config-validation + widgetData tests.
- `packages/optio-demo/src/optio_demo/tasks/opencode.py` — set `show_model_selector=True` on the conversation task.

---

### Task 1: Pure model helpers in the opencode adapter

**Files:**
- Modify: `packages/optio-conversation-ui/src/opencode/events.ts` (append exports at end)
- Test: `packages/optio-conversation-ui/src/__tests__/opencode-model.test.ts`

**Interfaces:**
- Consumes: nothing (pure functions over the wire JSON shapes).
- Produces:
  - `export type OpencodeModel = { providerID: string; modelID: string };`
  - `export interface ModelGroup { providerName: string; models: { providerID: string; modelID: string; label: string }[] }`
  - `export function parseProviders(json: any): { groups: ModelGroup[]; defaultModel: OpencodeModel | null }`
  - `export function lastModelFromHistory(history: any[]): OpencodeModel | null`

- [ ] **Step 1: Write the failing test**

Create `packages/optio-conversation-ui/src/__tests__/opencode-model.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { parseProviders, lastModelFromHistory } from '../opencode/events.js';

// Shape verified against opencode 1.17.3-csillag.2 GET /config/providers:
// { providers: [{ id, name, models: { <modelId>: { id, providerID, name } } }],
//   default: { <providerID>: <modelId> } }
const PROVIDERS = {
  providers: [
    {
      id: 'opencode',
      name: 'OpenCode Zen',
      models: {
        'deepseek-v4-flash': { id: 'deepseek-v4-flash', providerID: 'opencode', name: 'DeepSeek V4 Flash' },
        'big-pickle': { id: 'big-pickle', providerID: 'opencode', name: 'Big Pickle' },
      },
    },
    {
      id: 'xai',
      name: 'xAI',
      models: { 'grok-5': { id: 'grok-5', providerID: 'xai', name: 'Grok 5' } },
    },
  ],
  default: { opencode: 'big-pickle', xai: 'grok-5' },
};

describe('parseProviders', () => {
  it('groups models by provider with id/name', () => {
    const { groups } = parseProviders(PROVIDERS);
    expect(groups.map((g) => g.providerName)).toEqual(['OpenCode Zen', 'xAI']);
    expect(groups[0].models).toEqual([
      { providerID: 'opencode', modelID: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
      { providerID: 'opencode', modelID: 'big-pickle', label: 'Big Pickle' },
    ]);
  });

  it('derives the default model from the first provider', () => {
    const { defaultModel } = parseProviders(PROVIDERS);
    expect(defaultModel).toEqual({ providerID: 'opencode', modelID: 'big-pickle' });
  });

  it('returns empty groups and null default for malformed input', () => {
    expect(parseProviders({})).toEqual({ groups: [], defaultModel: null });
    expect(parseProviders(null)).toEqual({ groups: [], defaultModel: null });
  });
});

describe('lastModelFromHistory', () => {
  it('returns the last assistant message model', () => {
    const history = [
      { info: { role: 'user' }, parts: [] },
      { info: { role: 'assistant', providerID: 'opencode', modelID: 'deepseek-v4-flash' }, parts: [] },
      { info: { role: 'assistant', providerID: 'xai', modelID: 'grok-5' }, parts: [] },
    ];
    expect(lastModelFromHistory(history)).toEqual({ providerID: 'xai', modelID: 'grok-5' });
  });

  it('returns null when no assistant message carries a model', () => {
    expect(lastModelFromHistory([{ info: { role: 'user' }, parts: [] }])).toBeNull();
    expect(lastModelFromHistory([])).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/opencode-model.test.ts`
Expected: FAIL — `parseProviders`/`lastModelFromHistory` are not exported from `events.ts`.

- [ ] **Step 3: Implement the helpers**

Append to the end of `packages/optio-conversation-ui/src/opencode/events.ts`:

```typescript
/** A concrete opencode model selection, as accepted by prompt_async's `model`. */
export type OpencodeModel = { providerID: string; modelID: string };

/** Provider-grouped option model for the picker. */
export interface ModelGroup {
  providerName: string;
  models: { providerID: string; modelID: string; label: string }[];
}

/** Parse GET /config/providers into grouped options + a fallback default.
 *  Response shape (opencode 1.17.3-csillag.2):
 *    { providers: [{ id, name, models: { <modelId>: { id, providerID, name } } }],
 *      default: { <providerID>: <modelId> } }
 *  The default field maps each provider to its default model; we take the
 *  first provider's default as the widget-level fallback. */
export function parseProviders(json: any): { groups: ModelGroup[]; defaultModel: OpencodeModel | null } {
  const providers = Array.isArray(json?.providers) ? json.providers : [];
  const groups: ModelGroup[] = providers.map((p: any) => ({
    providerName: String(p?.name ?? p?.id ?? ''),
    models: Object.values(p?.models ?? {}).map((m: any) => ({
      providerID: String(m?.providerID ?? p?.id ?? ''),
      modelID: String(m?.id ?? ''),
      label: String(m?.name ?? m?.id ?? ''),
    })),
  }));
  let defaultModel: OpencodeModel | null = null;
  const first = providers[0];
  const def = json?.default;
  if (first && def && typeof def === 'object' && typeof def[first.id] === 'string') {
    defaultModel = { providerID: String(first.id), modelID: String(def[first.id]) };
  }
  return { groups, defaultModel };
}

/** The model of the last assistant message in GET /session/:id/message history,
 *  or null. Assistant `info` carries providerID/modelID (the datum the engine's
 *  _resolve_session_model_sync reads). */
export function lastModelFromHistory(history: any[]): OpencodeModel | null {
  let model: OpencodeModel | null = null;
  for (const entry of history ?? []) {
    const info = entry?.info ?? {};
    if (info.role === 'assistant' && info.providerID && info.modelID) {
      model = { providerID: String(info.providerID), modelID: String(info.modelID) };
    }
  }
  return model;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/opencode-model.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Typecheck**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/tsc --noEmit`
Expected: no output (exit 0).

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-conversation-ui/src/opencode/events.ts packages/optio-conversation-ui/src/__tests__/opencode-model.test.ts
git commit -m "feat(optio-conversation-ui): opencode model parse/resolve helpers"
```

---

### Task 2: Widget resolves and sends the model

Add `currentModel` state to `OpencodeChat`, fetch providers alongside history, resolve the initial model, and attach it to `prompt_async`. No picker UI yet (Task 3).

**Files:**
- Modify: `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx`
- Test: `packages/optio-conversation-ui/src/__tests__/opencode-model-widget.test.tsx`

**Interfaces:**
- Consumes: `OpencodeModel`, `parseProviders`, `lastModelFromHistory` from Task 1; `ModelGroup` (used in Task 3).
- Produces: `OpencodeWidgetData` gains `showModelSelector?: boolean; defaultModel?: string`. Component-internal state `groups: ModelGroup[]`, `currentModel: OpencodeModel | null` (consumed by Task 3).

- [ ] **Step 1: Write the failing test**

Create `packages/optio-conversation-ui/src/__tests__/opencode-model-widget.test.tsx`:

```typescript
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OpencodeView } from '../opencode/OpencodeView.js';

class MockEventSource {
  static last: MockEventSource | null = null;
  url: string; onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) { this.url = url; MockEventSource.last = this; }
  close() {}
}

const PROVIDERS = {
  providers: [{
    id: 'opencode', name: 'OpenCode Zen',
    models: {
      'deepseek-v4-flash': { id: 'deepseek-v4-flash', providerID: 'opencode', name: 'DeepSeek V4 Flash' },
      'big-pickle': { id: 'big-pickle', providerID: 'opencode', name: 'Big Pickle' },
    },
  }],
  default: { opencode: 'big-pickle' },
};

// fetch router: history (empty unless overridden), providers, POST capture.
function installFetch(opts: { history?: any[]; posts: { url: string; body: any }[] }) {
  const fn = vi.fn(async (url: string, init?: any) => {
    if (init?.method === 'POST') {
      opts.posts.push({ url, body: JSON.parse(init.body) });
      return { ok: true, json: async () => ({}) } as any;
    }
    if (url.includes('/config/providers')) return { ok: true, json: async () => PROVIDERS } as any;
    if (url.includes('/message')) return { ok: true, json: async () => (opts.history ?? []) } as any;
    return { ok: true, json: async () => ({}) } as any;
  });
  (globalThis as any).fetch = fn;
  return fn;
}

function makeProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    widgetProxyUrl: '/api/widget/db/gm/p1/',
  } as any;
}

beforeEach(() => {
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
});

describe('OpencodeView model send', () => {
  it('sends the providers-default model on prompt_async when history is empty', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);

    // Wait for bootstrap (providers + history fetched, currentModel resolved).
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    const sent = posts.find((p) => p.url.includes('/prompt_async'))!;
    expect(sent.body.model).toEqual({ providerID: 'opencode', modelID: 'big-pickle' });
    expect(sent.body.parts).toEqual([{ type: 'text', text: 'hi' }]);
  });

  it('prefers the last-assistant model from history over the providers default', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({
      history: [{ info: { role: 'assistant', providerID: 'opencode', modelID: 'deepseek-v4-flash' }, parts: [] }],
      posts,
    });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.model)
      .toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
  });

  it('uses defaultModel (validated) on a fresh session even with the picker hidden', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({
      sessionID: 'fake-session-id', directory: '/wd',
      defaultModel: 'opencode/deepseek-v4-flash', // valid, not the providers default
    })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.model)
      .toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/opencode-model-widget.test.tsx`
Expected: FAIL — `body.model` is `undefined` (the view doesn't send a model yet).

- [ ] **Step 3: Add widgetData fields, imports, state, resolution, and send wiring**

In `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx`:

3a. Extend the import on line 8 to pull in the new helpers and type:

```typescript
import {
  historyToChatItems, reduceOpencodeEvent,
  parseProviders, lastModelFromHistory,
  type OpencodeModel, type ModelGroup,
} from './events.js';
```

3b. Extend `OpencodeWidgetData` (currently lines 16-20) to:

```typescript
interface OpencodeWidgetData {
  sessionID?: string;
  directory?: string;
  toolVerbosity?: 'silent' | 'description-only' | 'verbose';
  showModelSelector?: boolean;
  defaultModel?: string; // "providerID/modelID"
}
```

3c. Pass the two new fields through `OpencodeView` (the gate component, currently line 114). Replace that return with:

```typescript
  return (
    <OpencodeChat
      {...props}
      sessionID={widgetData.sessionID}
      directory={widgetData.directory ?? ''}
      showModelSelector={widgetData.showModelSelector ?? false}
      defaultModel={widgetData.defaultModel}
    />
  );
```

3d. Update the `OpencodeChat` signature (line 117) and destructure (line 119):

```typescript
function OpencodeChat(
  props: WidgetProps & { sessionID: string; directory: string; showModelSelector: boolean; defaultModel?: string },
) {
  const { token } = theme.useToken();
  const { sessionID, directory, widgetProxyUrl, showModelSelector, defaultModel } = props;
```

3e. Add model state next to the other `useState`s (after line 140, `const [error, ...]`):

```typescript
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [currentModel, setCurrentModel] = useState<OpencodeModel | null>(null);
```

3f. Add a providers-fetch + resolution effect immediately AFTER the bootstrap effect (after line 180). It re-uses the history fetch result for the history-last branch, so do the resolution inside one effect:

```typescript
  // Discover models and resolve the initial sticky model once. Runs alongside
  // the bootstrap; its failure never blocks the chat (groups stays empty,
  // currentModel may stay null → sends omit `model`, opencode uses its default).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let parsed: { groups: ModelGroup[]; defaultModel: OpencodeModel | null } = { groups: [], defaultModel: null };
      let history: any[] = [];
      try {
        const r = await fetch(`${widgetProxyUrl}config/providers${q}`);
        if (r.ok) parsed = parseProviders(await r.json());
      } catch { /* non-fatal */ }
      try {
        const r = await fetch(`${widgetProxyUrl}session/${sessionID}/message${q}`);
        if (r.ok) history = await r.json();
      } catch { /* non-fatal */ }
      if (cancelled) return;
      setGroups(parsed.groups);
      const inList = (m: OpencodeModel) =>
        parsed.groups.some((g) => g.models.some((x) => x.providerID === m.providerID && x.modelID === m.modelID));
      // (1) history-last → (2) validated defaultModel → (3) providers default → (4) null
      const fromHistory = lastModelFromHistory(history);
      let resolved: OpencodeModel | null = fromHistory;
      if (!resolved && defaultModel) {
        const [providerID, modelID] = defaultModel.split('/');
        const cand = { providerID, modelID };
        if (providerID && modelID && inList(cand)) resolved = cand;
      }
      if (!resolved) resolved = parsed.defaultModel;
      setCurrentModel(resolved);
    })();
    return () => { cancelled = true; };
  }, [widgetProxyUrl, sessionID]);
```

3g. Wire the model into `send()` (line 261). Replace the `post(...)` call with:

```typescript
    const promptBody: { parts: { type: 'text'; text: string }[]; model?: OpencodeModel } = {
      parts: [{ type: 'text', text: body }],
    };
    if (currentModel) promptBody.model = currentModel;
    const ok = await post(`session/${sessionID}/prompt_async${q}`, promptBody);
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/opencode-model-widget.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full package suite + typecheck (no regressions)**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`
Expected: all tests PASS; tsc no output.

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-conversation-ui/src/opencode/OpencodeView.tsx packages/optio-conversation-ui/src/__tests__/opencode-model-widget.test.tsx
git commit -m "feat(optio-conversation-ui): opencode widget resolves + sends sticky model"
```

---

### Task 3: Model picker UI

Render a grouped antd `Select` at the bottom input bar, gated on `showModelSelector`; selecting updates `currentModel`.

**Files:**
- Modify: `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx`
- Test: `packages/optio-conversation-ui/src/__tests__/opencode-model-widget.test.tsx` (add a describe block)

**Interfaces:**
- Consumes: `groups`, `currentModel`, `setCurrentModel`, `showModelSelector`, `busy`, `closed` (all in scope from Task 2).
- Produces: a `data-testid="model-select"` control (consumed by tests only).

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-conversation-ui/src/__tests__/opencode-model-widget.test.tsx`:

```typescript
describe('OpencodeView model picker', () => {
  it('is hidden when showModelSelector is false', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    expect(screen.queryByTestId('model-select')).toBeNull();
  });

  it('is shown when showModelSelector is true', async () => {
    installFetch({ history: [], posts: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showModelSelector: true })} />);
    await waitFor(() => expect(screen.getByTestId('model-select')).toBeTruthy());
  });

  it('selecting a model changes the model sent on the next prompt', async () => {
    const posts: { url: string; body: any }[] = [];
    installFetch({ history: [], posts });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showModelSelector: true })} />);
    await waitFor(() => expect(screen.getByTestId('model-select')).toBeTruthy());

    // antd Select renders a hidden native <select> in test env when we pass a
    // plain options model; drive it via the combobox role. Open + pick the
    // option labelled "DeepSeek V4 Flash" (value "opencode/deepseek-v4-flash").
    fireEvent.mouseDown(screen.getByTestId('model-select').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('DeepSeek V4 Flash')).toBeTruthy());
    fireEvent.click(screen.getByText('DeepSeek V4 Flash'));

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hi' } });
    fireEvent.click(screen.getByTestId('conversation-send'));
    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    expect(posts.find((p) => p.url.includes('/prompt_async'))!.body.model)
      .toEqual({ providerID: 'opencode', modelID: 'deepseek-v4-flash' });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/opencode-model-widget.test.tsx -t "model picker"`
Expected: FAIL — no `model-select` element rendered.

- [ ] **Step 3: Add the picker import and render it in the input bar**

3a. Add `Select` to the antd import (line 2):

```typescript
import { Button, Select, Spin, theme } from 'antd';
```

3b. Add a `value`/`onChange` helper and the control. Insert the `<Select>` as the FIRST child of the input bar `<div>` (the bar that starts at line 458, before the `<textarea>`). Insert:

```typescript
        {showModelSelector && (
          <Select
            data-testid="model-select"
            size="small"
            style={{ minWidth: 180, alignSelf: 'center' }}
            placeholder="Model"
            disabled={busy || closed}
            value={currentModel ? `${currentModel.providerID}/${currentModel.modelID}` : undefined}
            onChange={(v: string) => {
              const [providerID, modelID] = v.split('/');
              setCurrentModel({ providerID, modelID });
            }}
            options={groups.map((g) => ({
              label: g.providerName,
              options: g.models.map((m) => ({
                label: m.label,
                value: `${m.providerID}/${m.modelID}`,
              })),
            }))}
          />
        )}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run src/__tests__/opencode-model-widget.test.tsx`
Expected: PASS (all model-widget tests, including the 3 picker tests).

- [ ] **Step 5: Full suite + typecheck**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`
Expected: all PASS; tsc clean.

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-conversation-ui/src/opencode/OpencodeView.tsx packages/optio-conversation-ui/src/__tests__/opencode-model-widget.test.tsx
git commit -m "feat(optio-conversation-ui): grouped model picker in opencode conversation widget"
```

---

### Task 4: `OpencodeTaskConfig` fields + validation

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/types.py` (fields near line 107; validation near line 133)
- Test: `packages/optio-opencode/tests/test_conversation_ui_model.py`

**Interfaces:**
- Consumes: existing `OpencodeTaskConfig`, `conversation_ui`, `mode`.
- Produces: `OpencodeTaskConfig.default_model: str | None = None`, `OpencodeTaskConfig.show_model_selector: bool = False`.

- [ ] **Step 1: Write the failing test**

Create `packages/optio-opencode/tests/test_conversation_ui_model.py`:

```python
import pytest

from optio_opencode.types import OpencodeTaskConfig


def test_defaults_are_off():
    cfg = OpencodeTaskConfig(mode="conversation", conversation_ui=True)
    assert cfg.default_model is None
    assert cfg.show_model_selector is False


def test_fields_accepted_in_conversation_ui():
    cfg = OpencodeTaskConfig(
        mode="conversation",
        conversation_ui=True,
        default_model="opencode/big-pickle",
        show_model_selector=True,
    )
    assert cfg.default_model == "opencode/big-pickle"
    assert cfg.show_model_selector is True


def test_show_model_selector_requires_conversation_ui():
    with pytest.raises(ValueError, match="conversation_ui=True"):
        OpencodeTaskConfig(mode="conversation", conversation_ui=False, show_model_selector=True)


def test_default_model_requires_conversation_ui():
    with pytest.raises(ValueError, match="conversation_ui=True"):
        OpencodeTaskConfig(mode="conversation", conversation_ui=False, default_model="opencode/big-pickle")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && .venv/bin/python -m pytest tests/test_conversation_ui_model.py -q`
Expected: FAIL — `OpencodeTaskConfig` has no `default_model`/`show_model_selector`.

- [ ] **Step 3: Add the fields**

In `types.py`, immediately after the `tool_verbosity` field (line 107), add:

```python
    # Default model for a fresh conversation session, "providerID/modelID".
    # Forwarded to the widget, which applies it once at the start of a non-
    # resumed session (history empty) and only if present in the live model
    # list. Effective regardless of show_model_selector. Requires
    # conversation_ui=True.
    default_model: str | None = None
    # Show the model picker in the conversation widget. Requires
    # conversation_ui=True.
    show_model_selector: bool = False
```

- [ ] **Step 4: Add validation**

In `__post_init__`, after the `conversation_ui` check (line 133, before the `tool_verbosity` check), add:

```python
        if self.show_model_selector and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: show_model_selector=True requires "
                "conversation_ui=True."
            )
        if self.default_model is not None and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: default_model requires conversation_ui=True."
            )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && .venv/bin/python -m pytest tests/test_conversation_ui_model.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-opencode/src/optio_opencode/types.py packages/optio-opencode/tests/test_conversation_ui_model.py
git commit -m "feat(optio-opencode): default_model + show_model_selector config"
```

---

### Task 5: Plumb the fields into widgetData

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (the `set_widget_data` call in the `conversation_ui` branch, lines 378-386)
- Test: `packages/optio-opencode/tests/test_conversation_ui_model.py` (add a unit test on a small helper)

**Interfaces:**
- Consumes: `config.default_model`, `config.show_model_selector`.
- Produces: widgetData keys `showModelSelector: bool`, `defaultModel: str | None` (consumed by the widget, Task 2).

To keep the test fast and isolated (the full session needs a fake server), factor the widgetData dict into a pure module-level helper and assert on it.

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-opencode/tests/test_conversation_ui_model.py`:

```python
from optio_opencode.session import conversation_widget_data


def test_widget_data_carries_model_fields():
    cfg = OpencodeTaskConfig(
        mode="conversation",
        conversation_ui=True,
        default_model="opencode/big-pickle",
        show_model_selector=True,
        tool_verbosity="verbose",
    )
    wd = conversation_widget_data(cfg, session_id="s1", directory="/wd")
    assert wd == {
        "protocol": "opencode",
        "sessionID": "s1",
        "directory": "/wd",
        "toolVerbosity": "verbose",
        "showModelSelector": True,
        "defaultModel": "opencode/big-pickle",
    }


def test_widget_data_defaults():
    cfg = OpencodeTaskConfig(mode="conversation", conversation_ui=True)
    wd = conversation_widget_data(cfg, session_id="s1", directory="/wd")
    assert wd["showModelSelector"] is False
    assert wd["defaultModel"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && .venv/bin/python -m pytest tests/test_conversation_ui_model.py -q -k widget_data`
Expected: FAIL — `conversation_widget_data` does not exist.

- [ ] **Step 3: Add the helper and use it**

3a. Add this module-level function in `session.py` (place it just above the function that contains the `conversation_ui` branch — search for `async def` enclosing line 370; put the helper directly before it):

```python
def conversation_widget_data(config: "OpencodeTaskConfig", *, session_id: str, directory: str) -> dict:
    """The widgetData published for a conversation_ui task. Pure so it can be
    unit-tested without a live session."""
    return {
        "protocol": "opencode",
        "sessionID": session_id,
        "directory": directory,
        "toolVerbosity": config.tool_verbosity,
        "showModelSelector": config.show_model_selector,
        "defaultModel": config.default_model,
    }
```

3b. Replace the inline dict in the `conversation_ui` branch (lines 378-386) with:

```python
            await ctx.set_widget_data(
                conversation_widget_data(config, session_id=session_id, directory=host.workdir)
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && .venv/bin/python -m pytest tests/test_conversation_ui_model.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_conversation_ui_model.py
git commit -m "feat(optio-opencode): plumb model fields into conversation widgetData"
```

---

### Task 6: Fake opencode serves `GET /config/providers`

So fake-server integration tests (and manual fake runs) can exercise the providers route through the proxy. The `prompt_async` journal already records the full body (model included) — no journal change needed.

**Files:**
- Modify: `packages/optio-opencode/tests/fake_opencode.py` (the request router, after the `/global/event` branch, ~line 336)

**Interfaces:**
- Consumes: nothing new.
- Produces: `GET /config/providers` → a two-provider fixture JSON.

- [ ] **Step 1: Add the route**

In `fake_opencode.py`, add this branch right after the `/global/event` handler's loop block and before `is_session_post = (...)` (around line 336):

```python
            if method == "GET" and path == "/config/providers":
                providers = {
                    "providers": [
                        {
                            "id": "opencode",
                            "name": "OpenCode Zen",
                            "models": {
                                "deepseek-v4-flash": {"id": "deepseek-v4-flash", "providerID": "opencode", "name": "DeepSeek V4 Flash"},
                                "big-pickle": {"id": "big-pickle", "providerID": "opencode", "name": "Big Pickle"},
                            },
                        },
                        {
                            "id": "xai",
                            "name": "xAI",
                            "models": {
                                "grok-5": {"id": "grok-5", "providerID": "xai", "name": "Grok 5"},
                            },
                        },
                    ],
                    "default": {"opencode": "big-pickle", "xai": "grok-5"},
                }
                body = json.dumps(providers).encode()
                ctype = b"application/json"
                await _respond(loop, conn, body, ctype)
                continue
```

NOTE: match the exact response-sending idiom used by the neighbouring branches. If the surrounding code sets `body`/`ctype` and falls through to a shared responder at the bottom (rather than an inline `_respond(...)`), set `body`/`ctype` here the same way and do NOT add the `await _respond(...)`/`continue` lines. Read lines 336-380 first and mirror the existing control flow.

- [ ] **Step 2: Sanity-check the fake server still imports/parses**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && .venv/bin/python -c "import ast; ast.parse(open('tests/fake_opencode.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Run the opencode conversation test suite (no regressions)**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && OPTIO_SKIP_PREFLIGHT_TESTS=1 .venv/bin/python -m pytest tests/ -q -k "conversation or fake"`
Expected: PASS (pre-existing conversation tests unaffected).

- [ ] **Step 4: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-opencode/tests/fake_opencode.py
git commit -m "test(optio-opencode): fake server serves /config/providers"
```

---

### Task 7: Enable the picker on the demo conversation task

**Files:**
- Modify: `packages/optio-demo/src/optio_demo/tasks/opencode.py` (the `opencode-conversation-seed-<id>` task config, lines 248-256)

**Interfaces:**
- Consumes: `OpencodeTaskConfig` with the new `show_model_selector` field.
- Produces: nothing (demo wiring).

- [ ] **Step 1: Add the flag**

In `opencode.py`, in the `config=OpencodeTaskConfig(...)` for `process_id=f"opencode-conversation-seed-{seed_id}"` (the block at lines 248-256), add `show_model_selector=True,` alongside the existing `conversation_ui=True`:

```python
                config=OpencodeTaskConfig(
                    consumer_instructions="",   # defaulted conversation prompt
                    mode="conversation",
                    conversation_ui=True,
                    show_model_selector=True,
                    host_protocol=False,
                    ssh=ssh,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
```

Leave the three iframe-mode opencode tasks (`opencode-demo`, `opencode-seed-setup`, `opencode-demo-seed-<id>`) unchanged — they have no conversation widget and would fail validation.

- [ ] **Step 2: Verify the module imports (validation passes)**

Run: `cd /home/csillag/deai/optio/packages/optio-demo && .venv/bin/python -c "import optio_demo.tasks.opencode; print('ok')"`
Expected: `ok` (no `ValueError` from `__post_init__`).

- [ ] **Step 3: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-demo/src/optio_demo/tasks/opencode.py
git commit -m "feat(optio-demo): show model selector on opencode conversation task"
```

---

### Task 8: Full verification sweep

**Files:** none (verification only).

- [ ] **Step 1: optio-conversation-ui full suite + typecheck**

Run: `cd /home/csillag/deai/optio/packages/optio-conversation-ui && node_modules/.bin/vitest run && node_modules/.bin/tsc --noEmit`
Expected: all tests PASS; tsc no output.

- [ ] **Step 2: optio-opencode model + conversation tests**

Run: `cd /home/csillag/deai/optio/packages/optio-opencode && OPTIO_SKIP_PREFLIGHT_TESTS=1 .venv/bin/python -m pytest tests/test_conversation_ui_model.py tests/ -q -k "model or conversation or fake"`
Expected: PASS.

- [ ] **Step 3: optio-demo import check**

Run: `cd /home/csillag/deai/optio/packages/optio-demo && .venv/bin/python -c "import optio_demo.tasks.opencode; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Confirm Phase-1 scope boundary**

Run: `cd /home/csillag/deai/optio && git grep -n "showModelSelector\|show_model_selector\|currentModel" packages/optio-conversation-ui/src/claudecode`
Expected: no matches (claudecode adapter untouched, per the engine-parity phasing).

---

## Self-Review

**Spec coverage:**
- §1 config fields + validation → Task 4. ✓
- §1 widgetData plumbing → Task 5. ✓
- §2 picker (bottom, grouped, all models, disabled while busy) → Task 3. ✓
- §2 providers fetch on bootstrap → Task 2 (step 3f). ✓
- §3 resolution order (history-last → defaultModel → providers default → null) → Task 2 (step 3f) + tests. ✓
- §4 send wiring (model when non-null; omit otherwise) → Task 2 (step 3g). ✓
- §4 "always sends regardless of picker visibility" → Task 2 test "uses defaultModel … even with the picker hidden". ✓
- §5 errors non-fatal (providers fetch failure doesn't block chat) → Task 2 (step 3f try/catch). ✓
- §6 fake `/config/providers` + model captured in journal → Task 6 (journal already records body). ✓
- §6 widget tests (picker visibility, grouped options, send carries model, resolution) → Tasks 2-3. ✓
- §7 demo: selector on the conversation task only → Task 7. ✓
- §7 claudecode untouched → Task 8 step 4. ✓

**Placeholder scan:** none — every code step carries full content.

**Type consistency:** `OpencodeModel {providerID, modelID}` used identically across helpers (Task 1), widget state/send (Task 2), and picker value encoding `"providerID/modelID"` (Task 3, demo string format Task 4). `ModelGroup {providerName, models:[{providerID, modelID, label}]}` consistent between `parseProviders` (Task 1) and the picker `options` mapping (Task 3). `conversation_widget_data` signature matches its test (Task 5).

**Phase boundary:** Claude Code parity is explicitly deferred to a separate paired spec (design §8); no claude-code files appear in any task.
