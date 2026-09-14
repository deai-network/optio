# optio-conversation-ui

Engine-neutral **conversation widget** for [optio](https://github.com/deai-network/optio) tasks. One React widget renders a live chat transcript for a headless agent session and drives it (send / interrupt / approve permissions / switch model / upload + download files) through the optio widget proxy — for **both** the `claudecode` and `opencode` engines.

This package replaces the engine-specific `optio-claudecode-ui`. A single registration serves both engines; each task self-declares its wire protocol.

## Install

```sh
pnpm add optio-conversation-ui
```

Peer deps: `react >=18`, `react-dom >=18`, `antd >=5`.

## Usage

Register the widget once in your host app (alongside `optio-ui`):

```ts
import { registerConversationWidget } from 'optio-conversation-ui';

registerConversationWidget();
// or, when the host has no antd ConfigProvider of its own and you want the
// widget to own a light/dark toggle:
registerConversationWidget({ ownTheme: true });
```

It registers for `ui_widget = "conversation"`. Each task carries its engine in `widgetData.protocol` (`"claudecode"` or `"opencode"` — injected automatically by `optio-claudecode` / `optio-opencode`), and the widget dispatches to the matching view. You do **not** specify the engine at registration.

## Features

- Streamed chat transcript (replay + live) with optimistic local echo.
- Steering while the agent works (claudecode, via the view's `onSteer`): the busy input bar is `[Send when ready | Interrupt and send]` (Enter / ⌘/Ctrl-Enter) beside the red Interrupt; a message the agent has not taken yet is a dashed "Queued" bubble with **Send now**; an interrupted answer keeps its text with a jagged bottom edge, followed by one muted "⏹ Interrupted by you" row. Views without `onSteer` keep a single Send.
- Interrupt gives immediate feedback (manual-test finding, owner ruling 2026-09-14): `onInterrupt: () => Promise<boolean> | void` resolves whether the request reached the listener. While in flight the button reads "Interrupting…" and ignores further clicks; on success it returns to normal (the "⏹ Interrupted by you" row still comes from the listener's `x-optio-interrupt`, as before); on `false` or a rejection it shows "Interrupt failed — retry." through the same error slot Send/Send now use. A void-returning handler (the other engines, unchanged) is treated as immediate success.
- Tool-call and permission cards (approve / deny) when the task runs a permission gate.
- Model switching (when the task enables `show_model_selector`).
- File upload (📎) and one-click file download (agent emits `[name](optio-file:relpath)`), when the task enables `show_file_upload` / `file_download`.
- Markdown rendering with GFM, LaTeX math (KaTeX), Mermaid diagrams, and themed code blocks.
- Optional opt-in light/dark theming (`ownTheme`).

## Consumer requirements

Because the widget renders rich markdown, the host bundler must handle:

- **CSS imports** — the widget imports `katex/dist/katex.min.css`. Ensure your bundler can import CSS from `node_modules`.
- **KaTeX fonts** — served from the `katex` package; make sure they resolve at runtime.
- **Mermaid** — client-only; render the widget in the browser, not during SSR.
- **antd theme** — wrap your app (or the widget) in an antd `ConfigProvider` if you want a non-default theme, or use `registerConversationWidget({ ownTheme: true })`.

See `src/AnswerBlock.tsx` for the authoritative list.

## Exports

- `registerConversationWidget(opts?)`, `ConversationWidget` — the widget + its registration.
- `AnswerBlock` — the standalone markdown/answer renderer.
- `reduceClaudecodeEvent`, `reduceOpencodeEvent`, `historyToChatItems`, `initialChatState`, `ChatItem`, `ChatState` — the engine-neutral chat model and per-engine reducers, for embedding outside the default widget.
  - Tool items (`kind: 'tool'`) may carry `callId` (the call's wire id), `result` (its output, trimmed), `startedAt` / `endedAt` (epoch ms; the view shows a live elapsed counter until `endedAt` is set), `background` (a backgrounded shell command) and `taskId` (its background task id). All are optional and set by the claudecode reducer only.
  - A tool item's `status` may also be `'stopped'`: stopped rather than completed or failed (a background task stopped or killed, or a call still running when the session closed or a resumed run replaced it). It renders as finished, not failed.
  - `reduceClaudecodeEvent(state, ev, seq, now?)`: `now` (epoch ms, default `Date.now()`) is the clock used only until the stream has shown a user/assistant timestamp; pass it for deterministic replays and tests.
  - Steering fields (all optional, claudecode reducer only): user items `queued` (a "Send when ready" message the agent has not taken yet, pinned at the bottom of the transcript) and `queueId` (the id optio gave the message, kept once it is taken); assistant items `interrupted` (cut off by the operator); activity items `muted` (a quiet one-line note, e.g. an undelivered message, instead of the harness `System:` bubble); user/assistant/error/activity items also carry `timestamp` (see Message timestamps below). The reducer maps the listener's `x-optio-queued`, `x-optio-taken` and `x-optio-interrupt` events onto `queued`/`queueId`/`interrupted`/`muted`, plus the view's own local echo.
  - Message timestamps (owner request 2026-09-14, extended 2026-09-14 to `System:` rows — manual-test finding 3): user, assistant, error and non-muted activity items may carry an optional `timestamp` (epoch ms). The view renders it as a small grey time under the bubble (`HH:MM` for a message from today, else a short date — plus the year if not this year — then `HH:MM`; the full local date/time is the hover title), via the pure `formatMessageTime(timestamp, now)` in `src/messageTime.ts` (the view passes the current time; the reducer never reads the clock). Absent when no time is known (e.g. a still-queued bubble in a replay) — never invented. The claudecode reducer fills it from the wire: a user/assistant item gets its own event's `timestamp` (an assistant message keeps the FIRST one seen for it — a streaming delta carries none); a queued "Send when ready" shows the view's local-echo send time live, replaced by the taking echo's own wire timestamp once Claude takes it; a harness `System:` echo (e.g. "you have been resumed", a deliverable notice) becomes a non-muted activity item carrying that same echo's own wire timestamp. Muted notice rows (an operator interrupt, an undelivered message) and background-task rows never carry one. Other engines' reducers don't set it, so their items simply show no time.

## License

Apache-2.0
