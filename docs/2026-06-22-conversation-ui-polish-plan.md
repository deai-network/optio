# Conversation UI Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. This plan is **parallel-shaped with one dependency barrier**: Task CV (the shared component) must land before the two engine-view adapters that consume it. All other tasks are file-disjoint; ALL verification is deferred to the final task.

**Goal:** Deduplicate `ClaudeCodeView`/`OpencodeView` into one shared `ConversationView`, then polish that single component: opt-in light/dark theming, chat-bubble tails, status tints, a max-width reading column, a per-answer copy button, an antd composer, global Escape-to-interrupt, and a closable error alert.

**Architecture:** `ConversationView` owns all rendering + local UI state + the input bar + a thin header; the engine views become thin adapters supplying transport callbacks + a `modelSelector` node. `ConversationWidget` gains an opt-in `ownTheme` that wraps its own `ConfigProvider`. Every existing `data-testid` is preserved so the model-switch/upload/download suites keep passing.

**Tech Stack:** TypeScript + React + antd (`Input.TextArea`, `ConfigProvider`, `theme`), vitest + @testing-library/react.

## Global Constraints

- Parallel-shaped + one barrier: **CV lands first**; CCV/OCV consume its pinned interface. All other tasks concurrent. All vitest/tsc in the final task only.
- **Preserve every `data-testid`** (migration contract, §spec 6): `conversation-input-box`, `conversation-send`, `conversation-interrupt`, `model-select`, `attach-button`, `file-input`, `attach-chips`, `tool-call`, `permission-card`, `conversation-content`, `conversation-closed`, `optio-widget-loading`.
- Behavior parity: the extraction must not change what the views currently do (model switch, upload, download, permissions, scroll, mount-flash, optimistic echo) — only DRY it and add the §3 polish.
- Lifted CSS stays widget-scoped (`optio-cc-*` class-prefixed injected `<style>`), never global selectors.
- TS tooling from `packages/optio-conversation-ui`: `node_modules/.bin/{vitest,tsc}`. Never npx. Branch `csillag/opencode-frontend`, in-place. No push, no merge.

## Pinned Interfaces

```ts
// src/ConversationView.tsx
import type { ChatItem, ChatState } from './chat.js';
import type { Attachment } from './attachments.js';

export interface ConversationViewProps {
  state: ChatState;
  closed: boolean;
  busy: boolean;
  toolVerbosity: 'silent' | 'description-only' | 'verbose';
  showFileUpload: boolean;
  maxUploadBytes: number;
  fileDownload: boolean;
  onSend: (text: string, attachments: Attachment[]) => Promise<boolean>;  // returns ok
  onInterrupt: () => void;
  onPermission: (requestId: string, behavior: 'allow' | 'deny') => void;
  onFileDownload: (relpath: string, filename: string) => void;
  modelSelector?: React.ReactNode;     // engine's own <Select>, rendered in the input bar
  // theming (only set by ConversationWidget when ownTheme):
  themeMode?: 'light' | 'dark';
  onToggleTheme?: () => void;           // absent => no ☀/🌙 button
}
export function ConversationView(props: ConversationViewProps): JSX.Element

// src/ConversationWidget.tsx — gains:
//   ownTheme?: boolean   (default false = inherit host; true => own ConfigProvider + persisted pref)
```

`onSend` owns the optimistic local echo dispatch internally to each engine's reducer? **No** — the echo stays in the adapter. `ConversationView` calls `onSend(text, attachments)`; on `true` it clears the input + attachments. The adapter does the optimistic-echo dispatch (engine-specific event shape) inside its `onSend`.

## File Ownership

| File | Task | Depends on |
|---|---|---|
| `src/ConversationView.tsx` (new) | CV | — |
| `src/ConversationWidget.tsx` | TW | — (theming; concurrent with CV) |
| `src/claudecode/ClaudeCodeView.tsx` | CCV | CV |
| `src/opencode/OpencodeView.tsx` | OCV | CV |
| `src/__tests__/conversation-view.test.tsx` (new) | T1 | CV |
| `src/__tests__/theme-toggle.test.tsx` (new) | T2 | TW |

Execution: **wave 1** = CV + TW (concurrent). **barrier.** **wave 2** = CCV, OCV, T1, T2 (concurrent, against the real CV/TW). **then** V (verify + commit). The existing widget tests (`claudecode-widget`, `opencode-model-widget`, `conversation-upload`, `file-download`, `markdown`) are NOT rewritten — they must keep passing via preserved testids; V confirms.

---

### Task CV: extract `ConversationView`

**File:** Create `packages/optio-conversation-ui/src/ConversationView.tsx`

This is a **directed extraction**: `OpencodeView.tsx` and `ClaudeCodeView.tsx` currently hold near-identical chrome. Build `ConversationView` by lifting that shared chrome verbatim, parameterized by `ConversationViewProps`, then apply the §3 polish. Read both current views first; the shared pieces are: `bubbleBase`, `kvCell`, `renderInputKV`, `toolSummary`, the `ensureFlashStyle` + markdown-spacing injected styles, the `renderItem` switch, the scroll container + `ResizeObserver` stick-to-bottom (with the no-reflow guard), the auto-grow composer, the attach button + chips, send/interrupt buttons, the error display, and the closed divider.

- [ ] **Move the shared chrome in**, driven by props (not engine fetch calls): `renderItem` reads `props.state.items`; the send button calls `props.onSend(text, attachments)`; interrupt → `props.onInterrupt()`; permission approve/deny → `props.onPermission(...)`; attach uses `props.showFileUpload`/`maxUploadBytes`; the `optio-file:` downloads go through `<FileDownloadContext.Provider value={props.fileDownload ? props.onFileDownload : null}>`. Render `props.modelSelector` where the `<Select>` used to be. **Keep all listed `data-testid`s identical.**

- [ ] **Add a thin header strip** above the scroll area:

```tsx
{(props.onToggleTheme || true) && (
  <div data-testid="conversation-header" style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '4px 8px' }}>
    <Button size="small" data-testid="wide-toggle" onClick={() => setWide((w) => !w)}>
      {wide ? '▭ Narrow' : '▭ Wide'}
    </Button>
    {props.onToggleTheme && (
      <Button size="small" data-testid="theme-toggle" onClick={props.onToggleTheme}>
        {props.themeMode === 'dark' ? '☀' : '🌙'}
      </Button>
    )}
  </div>
)}
```

  with `const [wide, setWide] = useState(false);`.

- [ ] **§3 polish — bubble tails + tints** in `renderItem`:

```tsx
// user bubble:
style={{ ...bubbleBase, alignSelf: 'flex-end', background: token.colorPrimaryBg,
         border: `1px solid ${token.colorPrimaryBorder}`, borderRadius: '14px 14px 4px 14px' }}
// assistant bubble:
style={{ ...bubbleBase, alignSelf: 'flex-start', background: token.colorBgContainer,
         border: `1px solid ${token.colorBorderSecondary}`, borderRadius: '14px 14px 14px 4px' }}
```

  (drop the flat `borderRadius: 8` from `bubbleBase`; set radius per role.)

- [ ] **§3 — max-width reading column**: wrap the items column so it's centered and capped unless `wide`:

```tsx
<div style={{ maxWidth: wide ? '100%' : 880, margin: '0 auto', width: '100%',
              display: 'flex', flexDirection: 'column', gap: 8 }} ref={contentRef} data-testid="conversation-content">
```

- [ ] **§3 — per-answer copy button**: in the assistant case of `renderItem`, add a hover-revealed copy control, and register a scoped style (extend the injected sheet, e.g. in `ensureFlashStyle` or a new `ensureCopyStyle`):

```tsx
// assistant bubble inner:
<div className="optio-cc-answer" style={{ position: 'relative' }}>
  <AnswerBlock text={item.text} />
  <Button
    size="small" type="text" className="optio-cc-copy" data-testid="answer-copy"
    style={{ position: 'absolute', top: 0, right: 0 }}
    onClick={() => void navigator.clipboard?.writeText(item.text)}
  >⧉</Button>
</div>
```

  scoped CSS (added to an injected `<style>`, class-prefixed):
  `.optio-cc-answer .optio-cc-copy{visibility:hidden} .optio-cc-answer:hover .optio-cc-copy{visibility:visible}`

- [ ] **§3 — composer as `Input.TextArea`**: replace the raw `<textarea>` + the manual `scrollHeight` auto-grow effect with antd:

```tsx
import { Input } from 'antd';
// ...
<Input.TextArea
  data-testid="conversation-input-box"
  className="optio-cc-flash"
  value={text}
  onChange={(e) => setText(e.target.value)}
  onKeyDown={onKeyDown}
  placeholder="Message agent…  (Enter to send, Shift+Enter for newline)"
  autoSize={{ minRows: 2, maxRows: 8 }}
  disabled={closed}
  ref={inputRef}
/>
```

  (drop the `useEffect` that set `ta.style.height`; `autoSize` handles it. Keep `onKeyDown` Enter/Shift+Enter/Escape.)

- [ ] **§3 — global Escape-to-interrupt**:

```tsx
useEffect(() => {
  const h = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && props.busy && !props.closed) { e.preventDefault(); props.onInterrupt(); }
  };
  window.addEventListener('keydown', h);
  return () => window.removeEventListener('keydown', h);
}, [props.busy, props.closed]);
```

- [ ] **§3 — error as closable `Alert`**: the view keeps local `error` state set by `onSend` failure; render at the input-bar top:

```tsx
{error && <Alert type="error" closable message={error} onClose={() => setError(null)}
  data-testid="conversation-error" style={{ marginBottom: 4 }} />}
```

  (`onSend` returning `false` sets `error`; remove the old inline `<span>`.)

- [ ] Commit: `git add packages/optio-conversation-ui/src/ConversationView.tsx && git commit -m "feat(optio-conversation-ui): shared ConversationView (dedup + visual polish)"`

---

### Task TW: opt-in theming on `ConversationWidget`

**File:** Modify `packages/optio-conversation-ui/src/ConversationWidget.tsx`

- [ ] Add an `ownTheme?: boolean` prop. When false/absent, render exactly as today (inherit host). When true, wrap the dispatched view in the widget's own provider with a persisted pref:

```tsx
import { ConfigProvider, theme as antdTheme } from 'antd';
const THEME_KEY = 'optio-conversation:theme';
// inside the component, only when ownTheme:
const [mode, setMode] = useState<'light' | 'dark'>(
  () => (typeof localStorage !== 'undefined' && localStorage.getItem(THEME_KEY) === 'dark') ? 'dark' : 'light',
);
const toggle = () => setMode((m) => {
  const next = m === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
  return next;
});
// render:
//  ownTheme:
//    <ConfigProvider theme={{ algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm }}>
//      {dispatchedView({ themeMode: mode, onToggleTheme: toggle })}
//    </ConfigProvider>
//  else: dispatchedView({}) unchanged.
```

  Pass `themeMode`/`onToggleTheme` through to the engine view → `ConversationView`. (The engine view forwards them as props to `ConversationView`; add `themeMode?`/`onToggleTheme?` to each engine view's prop pass-through — this is done in CCV/OCV, against the pinned interface.) **localStorage is touched only in `ownTheme` mode.**

- [ ] Commit: `git add packages/optio-conversation-ui/src/ConversationWidget.tsx && git commit -m "feat(optio-conversation-ui): opt-in ownTheme with persisted light/dark"`

---

### Task CCV: ClaudeCodeView → thin adapter  *(depends on CV)*

**File:** Modify `packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx`

- [ ] Strip the chrome now living in `ConversationView`; keep only: the SSE subscription + reducer, `currentModel` resolution (incl. the `[variant]` strip + stream/init derivation), and the engine-specific transport functions. Render:

```tsx
return (
  <ConversationView
    state={state}
    closed={closed}
    busy={busy}
    toolVerbosity={toolVerbosity}
    showFileUpload={showFileUpload}
    maxUploadBytes={maxUploadBytes}
    fileDownload={fileDownload}
    onSend={async (text, attachments) => {
      // existing claude send: upload attachments via /upload, prepend System: lines, post('send', {text});
      // do the optimistic x-optio-local-user dispatch here; return ok.
    }}
    onInterrupt={() => void post('interrupt', {})}
    onPermission={(requestId, behavior) => void post('permission', { request_id: requestId, behavior })}
    onFileDownload={onFileDownload}
    modelSelector={showModelSelector ? (
      <Select data-testid="model-select" /* …existing claude flat-string picker… */ />
    ) : undefined}
    themeMode={(props as any).themeMode}
    onToggleTheme={(props as any).onToggleTheme}
  />
);
```

  Move the existing `send`/upload/optimistic-echo logic into the `onSend` callback; keep `post`, the EventSource effect, `currentModel` state + the stream-derive effect, and the `<Select>`.
- [ ] Commit: `git add packages/optio-conversation-ui/src/claudecode/ClaudeCodeView.tsx && git commit -m "refactor(optio-conversation-ui): ClaudeCodeView is a thin ConversationView adapter"`

---

### Task OCV: OpencodeView → thin adapter  *(depends on CV)*

**File:** Modify `packages/optio-conversation-ui/src/opencode/OpencodeView.tsx`

- [ ] Same shape as CCV, with opencode transport: `onSend` builds the `prompt_async` body (file parts as data URLs + text part + `model` when set) and does the opencode optimistic echo; `onInterrupt` → `post('session/<id>/abort'+q, {})`; `onPermission` → `post('permission/<id>/reply'+q, …)`; `onFileDownload` decodes `file/content`; `modelSelector` is the existing grouped `<Select>`. Keep the EventSource bootstrap, providers fetch, `currentModel` resolution. Render `<ConversationView … />` with the same prop wiring (including `themeMode`/`onToggleTheme` pass-through).
- [ ] Commit: `git add packages/optio-conversation-ui/src/opencode/OpencodeView.tsx && git commit -m "refactor(optio-conversation-ui): OpencodeView is a thin ConversationView adapter"`

---

### Task T1: ConversationView tests  *(depends on CV)*

**File:** Create `packages/optio-conversation-ui/src/__tests__/conversation-view.test.tsx`

Render `<ConversationView …>` with a stub `state` and spy callbacks. Cover: each `ChatItem` kind renders (user/assistant/activity/tool/permission/closed); user bubble has the `14px 14px 4px 14px` radius and assistant `14px 14px 14px 4px` (assert inline style); an error `ChatItem`/error state → error tint; the copy button calls `navigator.clipboard.writeText` with the answer text (mock clipboard); a window-level `Escape` calls `onInterrupt` only when `busy` (and not when idle); send button calls `onSend(text, [])` and clears on `true`; the closable `Alert` shows when `onSend` returns false and clears on close; `modelSelector` node renders; the ☀/🌙 button shows only when `onToggleTheme` is provided and calls it. Commit: `test(optio-conversation-ui): ConversationView render + interaction`.

---

### Task T2: theme-toggle tests  *(depends on TW)*

**File:** Create `packages/optio-conversation-ui/src/__tests__/theme-toggle.test.tsx`

Cover: `ownTheme` absent → no own `ConfigProvider`, no `theme-toggle` button, no `localStorage` write; `ownTheme` → `theme-toggle` present, clicking flips and writes `localStorage['optio-conversation:theme']`, and the initial mode reads from `localStorage`. (Use a stubbed `widgetData.protocol` to pick a view, or test the provider wrap directly.) Commit: `test(optio-conversation-ui): ownTheme toggle + persistence`.

---

### Task V: verify + commit-done sweep  *(after all)*

- [ ] `cd packages/optio-conversation-ui && node_modules/.bin/tsc --noEmit` — fix interface mismatches between CV and the adapters in the owning file until clean.
- [ ] `node_modules/.bin/vitest run` — **all** suites, including the un-rewritten `claudecode-widget`, `opencode-model-widget`, `conversation-upload`, `file-download`, `markdown` (they must still pass via preserved `data-testid`s). Fix regressions in the owning file.
- [ ] Confirm no behavior drift: model switch (`model-select` → POST), attach (`attach-button`/`file-input`), send, interrupt, permission, download (`optio-file:` click) all still wired through the adapters.
- [ ] Boundary: `git grep -n "renderItem\|bubbleBase\|kvCell" packages/optio-conversation-ui/src/{opencode,claudecode}` → expect no matches (chrome fully moved to `ConversationView`).
- [ ] Manual: rebuild dashboard, eyeball bubble tails / copy button / wide toggle / (standalone) theme toggle. Note as not-run if no rebuild.

---

## Self-Review

**Spec coverage:** §1 extraction → CV + CCV/OCV adapters; §2 theming → TW (+ pass-through in CCV/OCV); §3 all 7 items → CV (tails, tints, max-width+wide, copy, Input.TextArea, global Escape, Alert); §4 scope (testids preserved, scoped CSS) → Global Constraints + V boundary; §5 tests → T1/T2 + the preserved existing suites; §6 migration (testids) → Global Constraints + V. ✓

**Placeholder scan:** the CV/CCV/OCV tasks are a *directed extraction* — the `onSend`/transport bodies say "the existing X logic" because that code already exists in the current views and is moved verbatim; the new (polish) code is given in full. This is extraction guidance, not a placeholder for unwritten logic.

**Type consistency:** `ConversationViewProps` identical across CV (def), CCV/OCV (consume), T1 (test). `ownTheme`/`themeMode`/`onToggleTheme` consistent across TW → CCV/OCV → CV. `localStorage` key `optio-conversation:theme` identical in TW + T2. All `data-testid`s listed once in Global Constraints and reused.

**Parallel-shape + barrier:** CV/TW wave 1; CCV/OCV/T1/T2 wave 2 (consume the real CV/TW); V last. File-disjoint within each wave. All test runs in V.
