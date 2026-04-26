# optio-core — LLM Reference

See the [monorepo AGENTS.md](../../AGENTS.md) for the complete reference covering all packages.

---

## Widget Extensions

### TaskInstance.ui_widget

```python
@dataclass
class TaskInstance:
    ...
    ui_widget: str | None = None  # widget name registered via registerWidget() in optio-ui
```

Optional field added after `cancellable`. Stored in MongoDB as `uiWidget`. When set,
`ProcessDetailView` in optio-ui dispatches to the named widget component instead of the
default tree+log view.

---

### TaskInstance.supports_resume

```python
@dataclass
class TaskInstance:
    ...
    supports_resume: bool = False  # opt-in to resume/checkpoint support
```

Optional field. Defaults to `False`. When `True`, the executor publishes `supportsResume=True`
into the process document (refreshed via `$set` on every sync) and the UI switches the launch
button to a `Dropdown.Button` (Resume primary / Restart menu) when `hasSavedState` is also true.
Tasks that set this should use `ProcessContext.mark_has_saved_state()` / `clear_has_saved_state()`
and the blob helpers to persist and restore checkpoint data.

---

### InnerAuth (optio_core.models)

```python
from optio_core.models import BasicAuth, QueryAuth, HeaderAuth, InnerAuth

@dataclass
class BasicAuth:
    username: str
    password: str
    def to_dict(self) -> dict: ...  # {"kind": "basic", "username": ..., "password": ...}

@dataclass
class QueryAuth:
    name: str
    value: str
    def to_dict(self) -> dict: ...  # {"kind": "query", "name": ..., "value": ...}

@dataclass
class HeaderAuth:
    name: str
    value: str
    def to_dict(self) -> dict: ...  # {"kind": "header", "name": ..., "value": ...}

InnerAuth = Union[BasicAuth, QueryAuth, HeaderAuth]
```

Used to inject credentials into proxied widget requests. `BasicAuth` adds an
`Authorization: Basic ...` header. `QueryAuth` appends a query parameter to the upstream
URL. `HeaderAuth` adds an arbitrary request header.

---

### ProcessContext resume methods

```python
# Read-only attribute — True when this execution was triggered with resume=True
ctx.resume: bool

# Mark that the process has a valid checkpoint saved.
# Idempotent; warn-and-noop when the task's supports_resume=False.
await ctx.mark_has_saved_state() -> None

# Clear the saved-state flag (call after the task finishes consuming its checkpoint).
# Idempotent; warn-and-noop when supports_resume=False.
await ctx.clear_has_saved_state() -> None
```

---

### ProcessContext GridFS blob helpers

```python
# Store a blob.  Returns an async context manager; the yielded stream has a .file_id attribute.
# All blobs are tagged with metadata {processId, prefix, name}.
async with ctx.store_blob(name: str) as stream:
    stream.file_id  # GridFS file_id assigned to this blob
    await stream.write(data: bytes)  # write content

# Load a previously stored blob by file_id.
# Returns an async context manager yielding an async byte-stream.
async with ctx.load_blob(file_id) as stream:
    data = await stream.read()

# Delete a blob by file_id.  No-op if the file_id does not exist.
await ctx.delete_blob(file_id) -> None
```

---

### ProcessContext widget methods

```python
await ctx.set_widget_upstream(url: str, inner_auth: InnerAuth | None = None) -> None
# Registers the upstream URL for the widget proxy. The proxy will forward all
# /api/widget/:processId/* requests to this URL. inner_auth is injected per-request.

await ctx.clear_widget_upstream() -> None
# Removes widgetUpstream so the proxy returns 404 for this process.

await ctx.set_widget_data(data) -> None
# Overwrites widgetData with any JSON-serializable value. The tree stream delivers
# this to the widget component via the SSE update event.

await ctx.clear_widget_data() -> None
# Sets widgetData to null.
```

---

### MongoDB document schema additions

| Field | Type | Description |
|-------|------|-------------|
| `uiWidget` | `string \| null` | Widget name; `ProcessDetailView` dispatches on this field |
| `widgetUpstream` | `{ url: string, innerAuth: object \| null } \| null` | Server-side only — never sent to clients |
| `widgetData` | `<any JSON> \| null` | Live data delivered to the widget component via tree stream |
| `supportsResume` | `bool` | Whether the task opted into resume support; refreshed via `$set` on every sync |
| `hasSavedState` | `bool` | Whether the task has a valid checkpoint; `$setOnInsert: false`; mutated only by `mark/clear_has_saved_state` |

`hasSavedState` is backfilled to `false` on all existing documents by migration `m003_backfill_has_saved_state`
(runs on startup; depends on m002).

---

### Optio.launch / Optio.launch_and_wait

Both methods gain a `resume` keyword argument (default `False`):

```python
await optio_core.launch(process_id: str, resume: bool = False) -> None
await optio_core.launch_and_wait(process_id: str, resume: bool = False) -> None
```

When `resume=True`, the value is forwarded through the Redis command payload so the
executor sets `ctx.resume = True` when the task starts.

---

### Automatic lifecycle guarantees

- **Terminal states** (`done`, `failed`, `cancelled`): executor clears `widgetUpstream`
  automatically so the proxy returns 404 after the process ends.
- **Dismiss / relaunch**: both `widgetData` and `widgetUpstream` are cleared when a
  process is dismissed or re-launched.
