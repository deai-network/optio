# Conversation UI Polish — Design

**Base:** branch `csillag/opencode-frontend`, 2026-06-22. Grounded in
`docs/2026-06-22-conversation-ui-polish-research-notes.md` (compares
`optio-conversation-ui` against conversation-scripter's chat UI). The fourth and
final pre-merge feature of this branch.

## Summary

Two things, in order: (1) **deduplicate** — `ClaudeCodeView` and `OpencodeView`
share ~250 lines of identical chrome; extract a shared `ConversationView` so the
two engine views become thin transport adapters; (2) **polish** the one extracted
component — opt-in light/dark theming, chat-bubble tails, status-tinted bubbles, a
max-width reading column, a per-answer copy button, an antd composer, global
Escape-to-interrupt, and a closable error alert. Doing (1) first means every
polish item lands once and the two engines cannot drift.

## Decisions (settled in brainstorming)

1. **Extract `ConversationView` first**, then polish once. The duplication is a
   structural smell worth removing on its own merits, independent of polish.
2. **Theming is opt-in.** An engine-neutral widget must not hijack an embedding
   host's theme; a prop (default off) gates a self-contained `ConfigProvider` +
   toggle for standalone use.
3. **Visual bundle (7 items)** applied to the extracted component. Scripter's
   app-level chrome (sidebar, script editing, runs, export, hash routing) is out
   of scope; lifted CSS stays widget-scoped.

## 1. Shared `ConversationView` (the dedup)

New `packages/optio-conversation-ui/src/ConversationView.tsx`. It owns everything
the two views currently duplicate:

- Render helpers: `bubbleBase`, `kvCell`, `renderInputKV`, `toolSummary`,
  `renderItem` (all `ChatItem` kinds: user/assistant/activity/tool/permission/closed).
- The scroll area + `ResizeObserver` stick-to-bottom auto-scroll (incl. the
  no-reflow-loop guard), the mount-flash + focus, the auto-growing composer.
- The input bar: composer, attach button + chips, send/interrupt buttons, error
  display, the model-picker slot.
- The `FileDownloadContext.Provider` wrapping, and a thin **header strip** holding
  the toggles (§2, §3).

**Props (the seam):**

```ts
interface ConversationViewProps {
  state: ChatState;                 // reduced transcript (engine view feeds it)
  closed: boolean;
  busy: boolean;
  toolVerbosity: 'silent' | 'description-only' | 'verbose';
  showFileUpload: boolean;
  maxUploadBytes: number;
  fileDownload: boolean;
  onSend: (text: string, attachments: Attachment[]) => Promise<boolean>;
  onInterrupt: () => void;
  onPermission: (requestId: string, behavior: 'allow' | 'deny') => void;
  onFileDownload: (relpath: string, filename: string) => void;
  modelSelector?: React.ReactNode;  // engine renders its own <Select>, passed in
}
```

**Why a `modelSelector` slot, not a shared picker:** opencode uses grouped
`{providerID, modelID}` options; claude uses flat string ids with stream-derived
`currentModel` + the `[variant]` strip. Normalizing them is more coupling than
it's worth; each engine view keeps its own `Select` and passes it as a node. All
*other* chrome is shared.

**The engine views shrink** to: SSE subscription + reducer (engine-specific
events), the engine-specific transport implementations (`onSend` builds the
`prompt_async` file-parts/model body for opencode, the `/send` + System: upload
lines for claude; `onFileDownload` decodes `FileContent` vs blobs `/download`;
`onPermission`/`onInterrupt` hit the right routes), `currentModel` resolution, and
the `<Select>` they hand to `modelSelector`. Then they render
`<ConversationView … />`. Net: ~250 duplicated lines collapse to one component +
two ~80-line adapters.

## 2. Opt-in theming

`ConversationWidget` gains `ownTheme?: boolean` (default **false**). When false
(embedded, e.g. optio-dashboard) behavior is unchanged — the widget inherits the
host's `ConfigProvider`. When true (standalone/demo):

- Wrap the view in the widget's **own** antd `ConfigProvider` whose `algorithm` is
  `theme.darkAlgorithm` / `defaultAlgorithm` from a persisted pref.
- Persist the pref in `localStorage` under `optio-conversation:theme` — **only**
  in `ownTheme` mode (never write storage when embedded).
- The `ConversationView` header shows a ☀/🌙 button flipping the pref live.
- Provider wraps, token consumers are children (the `ScripterWidget → ThemedApp`
  hook ordering) so `theme.useToken()` reads the right algorithm.

The toggle button renders only when `ownTheme` is on (the view receives an
optional `onToggleTheme?: () => void` + `themeMode?: 'light'|'dark'`; absent →
no button).

## 3. Visual polish (in `ConversationView`)

- **Bubble tails + tinted roles.** User: `borderRadius:'14px 14px 4px 14px'`,
  `background: colorPrimaryBg`, `border: 1px solid colorPrimaryBorder`. Assistant:
  `'14px 14px 14px 4px'`, `colorBgContainer` + `colorBorderSecondary`.
- **Status-tinted bubbles.** Error item → `colorErrorBg` / `colorErrorBorder`;
  the closed/stale state → dashed border + `opacity: 0.75`.
- **Max-width reading column + "wide" toggle.** The content column is
  `maxWidth: 880; margin: 0 auto`; a header toggle (state, persisted with the
  theme pref when `ownTheme`, else component-local) removes the cap.
- **Per-answer copy button.** A small hover-revealed button on the assistant
  bubble (`navigator.clipboard.writeText(item.text)`); a `<style>` rule
  `.optio-cc-answer .optio-cc-copy { visibility: hidden }` →
  `.optio-cc-answer:hover .optio-cc-copy { visibility: visible }` (scoped class,
  added to the existing injected sheets — never a global selector).
- **Composer → antd `Input.TextArea`** with `autoSize={{ minRows: 2, maxRows: 8 }}`,
  replacing the raw `<textarea>` + the manual `scrollHeight` auto-grow effect.
  Keep the existing Enter-to-send / Shift+Enter-newline / Escape handlers and the
  `optio-cc-flash` mount-flash class.
- **Global Escape-to-interrupt.** A `window` keydown listener (added in a
  `useEffect`, removed on unmount) that calls `onInterrupt` when `busy && !closed`
  — so Escape works from anywhere in the widget, not only the focused textarea.
  Keep the textarea-local handler too (harmless overlap).
- **Error → closable `Alert`.** Replace the inline `<span colorError>` with an
  antd `Alert type="error" closable` at the top of the input bar; clearing it
  resets the error state.

## 4. Scope / non-goals

- **Do NOT port** scripter's sidebar / conversation list / "Runs", script editing
  (edit/reroll/move/delete/insert/"stale"/autopilot/"Run all"), model-switch
  `window.confirm`, export dropdown, URL-hash conversation restore.
- Any lifted CSS stays **widget-scoped** (`optio-cc-*` class-prefixed, like the
  existing injected sheets) — never global selectors (`body`, `.ant-input`).
- Markdown/Mermaid/KaTeX stack is **untouched** — B is the canonical newer copy;
  nothing to port from scripter there.
- No timestamps/avatars/roles-beyond-kind (not present in scripter either; YAGNI).
- Session/connection status pill and the full edit/reroll hover toolbar are
  deliberately excluded (only the copy button is kept from the toolbar pattern).

## 5. Testing

- **ConversationView render:** each `ChatItem` kind renders; user/assistant
  bubbles carry the tail radii + tint; an error item gets the error tint; the copy
  button copies `item.text` (mock `navigator.clipboard`); the closable `Alert`
  shows on error and clears on close.
- **Interaction:** a window-level Escape calls `onInterrupt` only when busy;
  send/interrupt/permission/upload/download callbacks fire with the right args;
  the `modelSelector` node renders in the bar.
- **Theming:** `ownTheme` off → no provider, no toggle, inherits host (a
  `theme.useToken()` probe matches the host algorithm); `ownTheme` on → toggle
  flips `darkAlgorithm`/`defaultAlgorithm` and persists to
  `localStorage['optio-conversation:theme']`; embedded mode never writes storage.
- **Adapters unchanged behavior:** the existing `ClaudeCodeView`/`OpencodeView`
  widget tests (model picker, attach, send, download) still pass after the
  extraction — they now assert against `ConversationView`'s rendered output via
  the same `data-testid`s (kept stable).

## 6. Migration note

The extraction must preserve every existing `data-testid`
(`conversation-input-box`, `conversation-send`, `conversation-interrupt`,
`model-select`, `attach-button`, `file-input`, `attach-chips`, `tool-call`,
`permission-card`, `conversation-content`, `conversation-closed`) so the model-
switch, upload, and download test suites keep passing without rewrites.
