# Conversation Tool-Usage Verbosity — Design

This spec was written against the following baseline:

**Base revision:** `4925a66837a5cb3176978609d981bd5a6754d568` on branch `csillag/claudecode-config-dir-isolation` (as of 2026-06-10T15:24:40Z)

## Summary

The claudecode conversation UI currently always renders each tool call in full: a `running <name>:` header plus a key→value table of the tool's input. This spec adds three verbosity levels, configurable per task and carried to the UI:

- **`verbose`** — current behavior (header + full k-v table).
- **`description-only`** (new default) — one line: `running <name>: <summary>`, no table.
- **`silent`** — render nothing for tool calls.

The setting rides the **existing** `widgetData` channel — no widget-channel widening is required.

## Background

- The widget channel already carries arbitrary JSON config: `widgetData` is `z.unknown()` in the process schema (`optio-contracts/src/schemas/process.ts`), persisted in Mongo, streamed end-to-end (`optio-api/src/stream-poller.ts` → SSE → `optio-ui` `useProcessWidget`), and passed to the widget as `props.process.widgetData`. The conversation-UI path already calls `ctx.set_widget_data({})` (empty today); the iframe widget already consumes `widgetData` (`{iframeSrc}`). So a props JSON already travels beside the widget name (`uiWidget`).
- Tool events: the conversation reducer (`optio-claudecode-ui/src/events.ts`) builds a `{kind: 'tool', name, input, seq}` item from each `tool_use` content block. `input` is the raw tool-args map; there is **no** synthesized `description`. Rendering lives in `ClaudeCodeConversationWidget.tsx` (`renderInputKV` + the `'tool'` case of `renderItem`).
- Tool usage is only shown by the **conversation** widget. The iframe/ttyd widget shows the raw terminal, so verbosity applies only to the conversation-UI path.

## Components

### 1. Config — `ClaudeCodeTaskConfig` (`types.py`)

Add:
```python
ToolVerbosity = Literal["silent", "description-only", "verbose"]
...
    # Conversation-UI tool-call rendering: "verbose" = full k-v table,
    # "description-only" = one summary line, "silent" = nothing. Carried to the
    # widget via widgetData; only affects mode="conversation" with conversation_ui.
    tool_verbosity: ToolVerbosity = "description-only"
```
`__post_init__` validates `tool_verbosity in ("silent", "description-only", "verbose")`.

### 2. Engine — `session.py` (conversation-UI path)

Where the conversation-UI path currently does `await ctx.set_widget_data({})`, change to:
```python
await ctx.set_widget_data({"toolVerbosity": config.tool_verbosity})
```
No other engine change. (The setting is opaque to optio; the widget interprets it.)

### 3. UI — `ClaudeCodeConversationWidget.tsx`

- Read the level once from props: `const toolVerbosity = (props.process.widgetData?.toolVerbosity ?? 'description-only') as ToolVerbosity;`
- A pure helper for the summary line:
  ```typescript
  const SALIENT_KEYS = ['description', 'command', 'file_path', 'path', 'pattern', 'query', 'url', 'prompt', 'title'];
  function toolSummary(input: unknown): string {
    if (input && typeof input === 'object' && !Array.isArray(input)) {
      const obj = input as Record<string, unknown>;
      for (const k of SALIENT_KEYS) {
        const v = obj[k];
        if (typeof v === 'string' && v.trim()) {
          const s = v.trim();
          return s.length > 120 ? s.slice(0, 117) + '…' : s;
        }
      }
    }
    return '';
  }
  ```
- The `'tool'` case of `renderItem`:
  - `silent` → `return null`.
  - `description-only` → `running <name>` plus `: <summary>` when `toolSummary(input)` is non-empty; no table.
  - `verbose` → unchanged (header + `renderInputKV`).

The reducer (`events.ts`) is unchanged — it still builds the full `tool` item; only render-time differs.

## Data flow

`ClaudeCodeTaskConfig.tool_verbosity` → `ctx.set_widget_data({toolVerbosity})` → Mongo `widgetData` → stream-poller → SSE → `useProcessWidget` → `props.process.widgetData.toolVerbosity` → `renderItem`.

## Testing

- **Config (pytest):** `tool_verbosity` defaults to `"description-only"`; an invalid value raises `ValueError`.
- **Engine (pytest):** a `conversation_ui=True` session sets `widgetData` to `{"toolVerbosity": <configured>}` (extend the existing conversation-UI session test, asserting via the process doc / a stub of `set_widget_data`).
- **UI (vitest):** `renderItem`/the widget for a `tool` item:
  - `silent` → nothing rendered for the tool.
  - `description-only` → the summary line; covers (a) `input.description` present, (b) no `description` but a salient key (e.g. `file_path`), (c) no salient field → just `running <name>`; and asserts the k-v table is absent.
  - `verbose` → the table is present.

## Out of scope

- No widget-channel/schema change (`widgetData` already exists and is typed `unknown`).
- No event-stream/reducer change.
- No expand/collapse UI (a future enhancement; YAGNI here).
- Verbosity does not affect the iframe/ttyd widget.
