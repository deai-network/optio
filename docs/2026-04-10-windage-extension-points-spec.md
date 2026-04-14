# Optio Extension Points for Windage Integration

**Date:** 2026-04-10
**Status:** Draft
**Context:** Windage is an LLM-driven interactive data exploration toolkit that runs its exploration process as an optio task. It needs bidirectional communication between a running process and a custom UI widget, plus persistent state that survives page refreshes.

---

## Overview

Five extensions to optio are required:

1. **Extended process state** (append-only persistence)
2. **Extended state retrieval** (REST endpoint)
3. **Process messaging** (UI-to-process communication)
4. **Custom UI widget registry** (client-side)
5. **SSE extended state deltas** (live streaming)

These extensions are general-purpose — they are not windage-specific. Any optio process could use them.

---

## 1. Extended Process State

### Purpose

Allow a running process to persist arbitrary structured data alongside its standard status/progress/log. The data model is append-only: the process pushes individual entries to an ordered list.

### Python API (optio-core)

New method on `ProcessContext`:

```python
ctx.append_extended_state(entry: dict[str, Any]) -> None
```

- `entry` must be JSON-serializable.
- Appends to a MongoDB array field (`extendedState`) on the process document using `$push`.
- Each entry is wrapped with an auto-incrementing index and a server-assigned timestamp.
- No read-back method is needed on the process side — the process is write-only.

### MongoDB Storage

New field on the process document:

```
extendedState: [
  { _idx: 0, timestamp: "2026-04-10T14:32:01.123Z", payload: { ... } },
  { _idx: 1, timestamp: "2026-04-10T14:32:01.456Z", payload: { ... } },
  ...
]
```

- `_idx`: monotonically increasing integer, assigned by optio-core on append. Enables efficient `since` queries.
- `timestamp`: ISO 8601 datetime, assigned by optio-core on append. Provides wall-clock ordering.
- `payload`: the original entry passed by the process.

### Lifecycle

- Initialized as an empty array when the process is created or dismissed (reset to idle).
- Cleared on dismiss, same as logs and progress.
- Preserved across `done`/`failed`/`cancelled` terminal states (the data remains readable after the process finishes).
- On relaunch, the host application decides whether to clear or preserve. Default: clear (same as logs).

---

## 2. Extended State Retrieval

### Purpose

Allow the client to fetch the full extended state or a slice of it. Primary use case: loading conversation history on widget mount or after a page refresh.

### REST Endpoint (optio-api)

```
GET /api/processes/:prefix/:id/extended-state
```

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `from` | integer | — | Return entries with `_idx >= from`. |
| `to` | integer | — | Return entries with `_idx < to`. |
| `last` | integer | — | Return the last N entries (most recent). |

**Supported combinations:**

| Pattern | Use case |
|---------|----------|
| `?last=50` | Initial load — get the most recent 50 entries. |
| `?from=42` | Live catch-up — get everything from idx 42 onward. |
| `?from=10&to=30` | Back-fill — get entries in range [10, 30) for scroll-back. |
| _(no params)_ | Get all entries. |

`from` and `last` are mutually exclusive. `to` can only be used with `from`.

**Response:**

```json
{
  "entries": [
    { "_idx": 0, "timestamp": "2026-04-10T14:32:01.123Z", "payload": { ... } },
    { "_idx": 1, "timestamp": "2026-04-10T14:32:01.456Z", "payload": { ... } }
  ],
  "total": 42,
  "hasMore": true
}
```

- `total`: the full count of entries in extended state (regardless of query params).
- `hasMore`: whether there are entries before the returned range (useful for the client to know if scroll-back is possible).

### Auth

Same rules as existing endpoints — `viewer` role sufficient (read-only).

---

## 3. Process Messaging (UI to Process)

### Purpose

Allow the client to send arbitrary JSON messages to a running process. The process consumes them at its own pace.

### REST Endpoint (optio-api)

```
POST /api/processes/:prefix/:id/message
```

**Request body:**

```json
{
  "payload": { ... }
}
```

- `payload` is any JSON-serializable object. Optio does not interpret it.
- Returns `404` if the process does not exist.
- Returns `409` if the process is not in a message-accepting state (`running`, `cancel_requested`).

**Response:**

```json
{ "ok": true }
```

### Transport (API to Core)

New Redis message type on the `{prefix}:commands` stream:

```
type: "message"
payload: { "processId": "...", "payload": { ... } }
```

optio-core's `CommandConsumer` receives this and enqueues the message for the target process.

### Python API (optio-core)

New method on `ProcessContext`:

```python
ctx.get_messages() -> list[dict[str, Any]]
```

- Returns all queued messages and removes them from the queue (drain semantics).
- Returns an empty list if no messages are pending.
- Non-blocking — never waits for messages.

Optionally, for processes that want to wait:

```python
ctx.wait_for_message(timeout: float | None = None) -> dict[str, Any] | None
```

- Blocks (async) until a message arrives, cancellation is requested, or timeout expires.
- Returns `None` on timeout or cancellation. The process should then check `ctx.should_continue()` to distinguish between the two.
- This preserves optio's cooperative cancellation contract — cancellation never raises, the process always decides how to respond.

**Usage guidance:** `wait_for_message()` is a convenience for processes whose only external input is user messages (e.g., an interactive chat). Processes that need to multiplex between user messages and other event sources (e.g., database change streams, external APIs) should use the non-blocking `get_messages()` and manage their own `asyncio` event loop.

### Internal Queue

Messages are stored in an in-memory `asyncio.Queue` per running process, keyed by process ObjectId. Messages from Redis are routed to the correct queue by the command consumer. The queue is created when the process starts and discarded when it finishes.

---

## 4. Custom UI Widget Registry

### Purpose

Allow processes to specify a preferred UI widget, and allow the host application to register custom widget implementations.

### Process Definition (optio-core)

New optional field on `TaskInstance`:

```python
TaskInstance(
    ...
    ui_widget: str | None = None,  # e.g., "windage-chat"
)
```

Stored on the process document in MongoDB as `uiWidget`.

### Widget Registry (optio-ui)

New client-side registry:

```typescript
import { registerWidget, type WidgetProps } from '@optio/ui';

registerWidget('windage-chat', WindageChatWidget);
```

**WidgetProps interface:**

```typescript
interface WidgetProps {
  process: Process;            // The full process document
  prefix: string;              // The optio prefix
  apiBaseUrl: string;          // Base URL for optio-api
  sseConnection: EventSource;  // The existing SSE connection for this process tree
}
```

### Rendering Behavior

When optio-ui renders a process detail view:

1. Check if the process has a `uiWidget` field.
2. If yes, look up the widget in the registry.
3. If found, render the registered component instead of the default progress/log view.
4. If not found, fall back to the default view (with a console warning).
5. If no `uiWidget` field, render the default view as before.

---

## 5. SSE Extended State Deltas

### Purpose

Stream new extended state entries to the client in real time, so the custom widget receives live updates without polling.

### Changes to Existing SSE Stream

The existing `GET /api/processes/:prefix/:id/tree/stream` SSE endpoint gains a new event type:

```
event: extended-state
data: { "processId": "...", "entries": [{ "_idx": 5, "timestamp": "...", "payload": { ... } }, { "_idx": 6, "timestamp": "...", "payload": { ... } }] }
```

### Polling Mechanism (Server-Side)

The existing `createTreePoller` in optio-api polls MongoDB at ~1s intervals. Extend it to:

1. Track the last seen `_idx` for each process in the tree.
2. On each poll, query for entries with `_idx > lastSeen`.
3. If new entries exist, emit an `extended-state` SSE event.
4. Update `lastSeen`.

### Client-Side

The existing `useProcessStream` hook is extended to parse `extended-state` events and expose them via a callback or state update. The custom widget subscribes to these updates.

### Delivery Guarantees

Unlike progress updates (which are rate-limited and only store the latest value), extended state deltas are:

- **Guaranteed delivery** — backed by MongoDB, not ephemeral.
- **Ordered** — entries have monotonic `_idx` values.
- **Resumable** — if the SSE connection drops, the client can fetch missed entries via the REST endpoint using `since=lastSeenIdx`, then resume streaming.

---

## Implementation Order

Suggested implementation sequence:

1. **Extended process state** (optio-core) — the foundation; everything else depends on it.
2. **Extended state retrieval** (optio-api) — enables testing without SSE.
3. **Process messaging** (optio-api + optio-core) — enables bidirectional communication.
4. **SSE extended state deltas** (optio-api) — enables real-time updates.
5. **Custom UI widget registry** (optio-ui) — enables custom rendering.

Steps 2-4 can be parallelized once step 1 is complete.

---

## Out of Scope

- The windage-specific widget implementation (that lives in windage-ui).
- The windage exploration process implementation (that lives in windage-core).
- Schema or format of the extended state entries — optio treats them as opaque JSON.
- Schema or format of process messages — optio treats them as opaque JSON.
