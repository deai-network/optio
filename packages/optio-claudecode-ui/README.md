# optio-claudecode-ui

React chat widget for [optio-claudecode](../optio-claudecode) conversation-mode
tasks. It renders a live conversation with a running Claude Code session — chat
bubbles, tool-activity rows, streaming partial text, interactive permission
(Approve / Deny) cards, and an interrupt button — all driven by the task's raw
`stream-json` event stream, delivered through the optio-api widget proxy.

All Claude-specific event interpretation lives client-side in this package
(`src/events.ts`, a pure reducer); the listener on the task side only forwards
raw events.

## Install

```bash
npm install optio-claudecode-ui
```

Peer dependencies: `react` >=18, `react-dom` >=18, `antd` >=5 (same policy as
`optio-ui`).

## Registration

Register the widget once at app startup (e.g. in your dashboard's entry module),
next to your other optio-ui setup:

```typescript
import { registerClaudeCodeConversationWidget } from 'optio-claudecode-ui';

registerClaudeCodeConversationWidget();
```

This calls optio-ui's `registerWidget('claudecode-conversation', ...)`; any task
that publishes `ui_widget="claudecode-conversation"` with a widget upstream then
renders the conversation UI automatically.

## Task-side requirements

The Python task must opt in to the conversation UI:

```python
ClaudeCodeTaskConfig(
    mode="conversation",
    conversation_ui=True,
    # permission_gate=True to exercise the Approve/Deny cards
)
```

`conversation_ui=True` requires `mode="conversation"`; the task then starts a
per-task listener (SSE event replay + live tail, plus `send` / `interrupt` /
`permission` POST endpoints) and registers it as the widget upstream.

## Design

See the Phase II design doc:
[docs/2026-06-10-claudecode-conversation-ui-design.md](../../docs/2026-06-10-claudecode-conversation-ui-design.md)
(and, for the underlying Conversation object and `x-optio-*` event conventions,
the Phase I doc
[docs/2026-06-10-claudecode-conversation-gate-design.md](../../docs/2026-06-10-claudecode-conversation-gate-design.md)).
