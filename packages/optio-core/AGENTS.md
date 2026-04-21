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

---

### Automatic lifecycle guarantees

- **Terminal states** (`done`, `failed`, `cancelled`): executor clears `widgetUpstream`
  automatically so the proxy returns 404 after the process ends.
- **Dismiss / relaunch**: both `widgetData` and `widgetUpstream` are cleared when a
  process is dismissed or re-launched.
