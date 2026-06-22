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

## License

Apache-2.0
