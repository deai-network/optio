# Conversation UI Polish — Research Notes (2026-06-22)

Read-only comparison of two React chat UIs, to seed a future brainstorming session.
**No code was modified.** All Project A pointers are into a *private* repo
(`~/deai/conversation-scripter`); cite them by path for later lookup, do not copy
file contents wholesale.

---

## 1. Current state of each UI

### Project A — `conversation-scripter` / `packages/scripter-ui`
A full chat *application shell*, not just a message list. It wraps the conversation
in an antd `Layout` with a **sidebar** (conversation list + collapsible "Runs"),
a **header** (title editor, model select, autopilot/wide toggles, export dropdown,
session pill, **theme toggle**), a centered/max-width **script column**, and a
**composer**. Notable traits:

- **Real light/dark theming**: its own `ConfigProvider` whose `algorithm` follows
  a persisted `prefs.theme` (`localStorage`, key `scripter:theme`). A ☀/🌙 button
  in the header flips it live.
- **Message bubbles via antd tokens** with asymmetric border-radius "tails"
  (`14px 14px 4px 14px` for questions, `…14px 4px` for answers) and tinted
  primary/error backgrounds.
- **CSS-file-driven** layout (`styles.css`, `shell.css`) rather than the
  all-inline-style approach B uses. Hover-revealed toolbars, insert-between-turns
  gaps, a "stale" divider, a "wide" layout mode.
- Consumes `AnswerBlock` from the **published** `optio-claudecode-ui@^0.1.1`
  (older than B's local source). So its markdown/mermaid/katex machinery is a
  *subset* of B's — **not** a source of rendering improvements.
- It is an editable, re-runnable *script* (edit/reroll/move/delete turns), which
  is domain-specific and mostly out of scope for an engine-neutral widget.

### Project B — `optio-conversation-ui` (branch `csillag/opencode-frontend`)
The engine-neutral conversation widget. `ConversationWidget` dispatches by
`widgetData.protocol` to `ClaudeCodeView` or `OpencodeView`; both render the shared
`ChatState` (`chat.ts`) with near-identical JSX. Traits:

- **antd theme tokens everywhere**, but **no `ConfigProvider` of its own** — it
  inherits whatever the host app provides, so it has *no* self-contained
  light/dark switch. Colors are token-correct; there's just no toggle here.
- **All-inline styles** plus three injected `<style>` blocks: the mount "flash"
  keyframes (`optio-cc-flash-style`), the markdown spacing sheet
  (`optio-cc-md-style`), and katex's CSS import.
- Already has the **mature** markdown stack (`Markdown.tsx`): GFM tables, KaTeX
  math, streaming-safe Mermaid with an SVG cache, stable component map, themed
  code blocks / blockquotes / tables / hr. **B is the canonical, newer copy** of
  this machinery; A's is older.
- Solid streaming UX: ResizeObserver auto-scroll with stick-to-bottom + the
  af42252 no-reflow-loop guard, auto-growing textarea, optimistic local echo,
  mount-flash + focus, Escape-to-interrupt, permission cards, tool-verbosity modes.
- `ChatState` is minimal (`kind/text/seq` + a few fields) — **no timestamps, no
  roles beyond kind, no avatars**.

**Bottom line:** A is *not* ahead on content rendering (it's behind). A is ahead on
**theming control, bubble polish, and the surrounding application chrome**. The
portable wins are theming + a handful of CSS/visual touches. Most of A's chrome
(sidebar, script editing, runs) is app-level and does not belong in B.

---

## 2. Ranked table of portable improvements

Effort: S < ~1h, M ~half-day, L ~1–2 days. "A file:line" points into the private repo.

| # | Improvement | What A does | What B does today | Eff | Risk | A pointer |
|---|---|---|---|---|---|---|
| 1 | **Self-contained light/dark theme toggle** | Own `ConfigProvider` whose `algorithm` follows persisted `prefs.theme`; ☀/🌙 button flips it; `localStorage` persists. `theme.useToken()` runs in a child of the provider. | Inherits host `ConfigProvider`; no toggle, no persistence, no dark mode of its own. | M | Med | `ScripterWidget.tsx:30-42` (ThemedApp), `Header.tsx:66-74` (toggle), `state/store.tsx:41-51,83-86` (persist) |
| 2 | **Bubble "tails" (asymmetric radius) + tinted role colors** | Question `borderRadius:'14px 14px 4px 14px'` on `colorPrimaryBg`+`colorPrimaryBorder`; answer `'14px 14px 14px 4px'` on `colorBgContainer`. Reads as a chat thread. | Flat `borderRadius: 8`, user bubble `colorPrimaryBg` (no border), assistant `colorBgContainer`+border. Less "chatty". | S | Low | `Cell.tsx:19-27`, `Answer.tsx:36-41` |
| 3 | **Distinct error/stale bubble styling** | Error → `colorErrorBg`/`colorErrorBorder`; stale → dashed border + `opacity:.75`. Status is visible at a glance. | No per-status bubble styling; errors are a separate `<span colorError>` line. | S | Low | `Cell.tsx:20-26`, `Answer.tsx:38` |
| 4 | **Centered, max-width reading column** | `.script-column { max-width:880px; margin:0 auto }` caps line length for readability; optional "wide" mode drops the cap. | Scroll area is full-width `padding:8`; long answers run edge-to-edge. | S | Low | `styles.css:21`, `:39-43` (wide) |
| 5 | **Hover-revealed per-message toolbar** | `.toolbar { visibility:hidden }` → visible on `.cell:hover`; holds edit/reroll/move/delete. Clean until hovered. | No per-message affordances at all (no copy, no retry on a message). | M | Low | `styles.css:46-49`, `Cell.tsx:38-91`, `Answer.tsx:51-62` |
| 6 | **Per-answer copy button** *(B-appropriate subset of #5)* | A's toolbar pattern is the vehicle; a copy-to-clipboard button is the engine-neutral piece worth lifting (A itself doesn't ship copy, but the hover-toolbar is the host). | None. | S | Low | pattern: `Answer.tsx:46-63` |
| 7 | **Session/connection status pill** | `SessionPill` (`Tag`) shows reconnecting / live·model / idle in the header — one glanceable connection+turn indicator. | "working…" spinner only; no connection/reconnect indicator. | S | Low | `Header.tsx:35-50` |
| 8 | **Composer as a styled antd `Input.TextArea`** | Uses antd `Input.TextArea autoSize={{minRows:2,maxRows:8}}` — theme-correct border/focus, free auto-grow, no manual height JS. | Raw `<textarea>` with manual `scrollHeight` auto-grow effect + hand-rolled token border. | S | Low | `Composer.tsx:18-24` |
| 9 | **Widget-level Escape-to-interrupt (global)** | `InterruptHotkey` listens on `window` so Escape interrupts from anywhere, not only when the textarea is focused. | Escape-to-interrupt only fires from inside the textarea `onKeyDown`. | S | Low | `ScripterWidget.tsx:13-25` |
| 10 | **Error surfaced as a closable antd `Alert`** | Top-of-composer `Alert type="error" closable` banner. | Inline `<span colorError>` next to the Send button; easy to miss, not dismissable. | S | Low | `ScripterWidget.tsx:68-77` |
| 11 | **Tool-line as a typed `<style>`-class, not inline** *(structural)* | A renders the activity/tool feed via CSS classes (`.activity`, `.tool-line`) — easier to theme/scrollbar-style globally. | Each tool/activity line is inline-styled per render. | M | Med | `styles.css:58`, `Answer.tsx:6-27` |

### Theming — explicit call-out
B's biggest gap is **#1**. B is fully token-correct but has **no light/dark control
of its own** — it relies on the host. A demonstrates the clean pattern: a private
`ConfigProvider` switching `darkAlgorithm`/`defaultAlgorithm` from a persisted pref,
with `theme.useToken()` consumers as *children* of that provider
(`ScripterWidget.tsx:30-54`). Porting this makes B demoable standalone with real
dark mode. The decision point for brainstorming: should an *engine-neutral widget*
own a theme toggle, or should that stay a host-app responsibility? (A is an app, so
it legitimately owns it; B is a widget. Recommend an **opt-in** prop, default
inherit-from-host.)

### "Fancy CSS" worth lifting
- **Bubble tails** (`Cell.tsx:19-27`, `Answer.tsx:36-41`) — the single highest
  visual-polish-per-effort change. Pure token CSS, engine-neutral.
- **Hover-reveal toolbars** (`styles.css:46-49`) — clean, discoverable affordances
  with zero idle clutter.
- **Status-tinted bubbles** (error/stale) (`Cell.tsx:20-26`).
- **Centered reading column + wide toggle** (`styles.css:21,39-43`).
- A has **no** custom scrollbar styling, transitions/animations beyond what antd
  gives, avatars, or timestamps — so there is nothing *fancier* than B there on
  those axes. (B's mount-flash keyframe is already fancier than anything in A.)

---

## 3. Component-structure notes
- A's split — `ScripterWidget` (theme provider) → `AppFrame` (token consumer) — is
  the **correct hook ordering** for self-contained theming and is the pattern to
  copy if #1 lands. B currently has no equivalent because it never owns a provider.
- A factors message rendering into `Cell` (question) + `Answer` (answer) components;
  B inlines everything in one `renderItem` switch duplicated across the two views.
  A's componentization is cleaner, **but** B's duplication is the real structural
  smell: `ClaudeCodeView` and `OpencodeView` share ~250 lines of identical JSX
  (`bubbleBase`, `kvCell`, `renderInputKV`, `toolSummary`, `renderItem`, the whole
  scroll/textarea/composer block). That shared chrome could be extracted into one
  `ConversationView` taking transport callbacks — independent of A, but A's
  cleaner separation is evidence it's worth doing.

## 4. Markdown / content rendering
**B is ahead, not behind.** B's `Markdown.tsx`/`Mermaid.tsx` is the canonical newer
copy; A consumes an *older published* `AnswerBlock` (`optio-claudecode-ui@^0.1.1`).
B already has GFM tables, KaTeX, streaming-safe cached Mermaid, themed code blocks,
stable component map. **Nothing to port from A here.** If anything, A is downstream
and benefits when B republishes.

---

## 5. Do NOT port (A-isms that don't fit B's engine-neutral, token-based design)
- **Sidebar / conversation list / "Runs" collapse** (`Sidebar.tsx`) — app-level
  multi-conversation management; B is a single-session widget.
- **Script editing model** — edit/reroll/move/delete/insert turns, "stale" divergence,
  "Run all", autopilot (`Cell.tsx`, `ScriptColumn.tsx:7-55`, `Header.tsx:86-148`).
  This is conversation-scripter's whole domain; B renders a *live* transcript, not an
  editable script.
- **Model-switch `window.confirm`** (`Header.tsx:99-108`) — A's "invalidates all
  answers" semantics don't exist in B; B's model select just changes the next send.
- **Export dropdown / `exportUrl`/`scriptUrl`** (`Header.tsx:121-144`) — scripter-API
  endpoints with no B equivalent.
- **URL-hash conversation restore** (`store.tsx:106-120`) — multi-conversation
  routing; N/A to a single-session widget.
- **`localStorage` keys namespaced `scripter:*`** — if #1 is ported, B must pick its
  own namespace (e.g. `optio-conversation:*`) and **only** when running standalone,
  not when embedded under a host that already controls the theme.
- A's `styles.css` global selectors (`.ant-input`, bare `body`, `#root`) leak past a
  widget boundary — B must keep any lifted CSS **scoped** (class-prefixed like the
  existing `optio-cc-*` sheets), not global.
