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

### Optio.group_cancel / Optio.group_cancel_and_wait

Cancel every active process whose metadata matches a filter. The pair offers
both fire-and-forget and wait-for-terminal variants:

```python
await optio_core.group_cancel(
    metadata_filter: ProcessMetadataFilter,    # required, non-empty
    block_new_launches: bool = False,
    *,
    persist: bool = False,                     # requires block_new_launches=True
    reason: str | None = None,                 # stored on the persistent record
) -> None  # fire-and-forget; returns once cancels are issued (and, if
           # block_new_launches=True, after the leak sweep). Safe to call
           # from inside a task whose own metadata matches the filter.

await optio_core.group_cancel_and_wait(
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool = False,
    *,
    persist: bool = False,                     # requires block_new_launches=True
    reason: str | None = None,                 # stored on the persistent record
) -> None  # blocks until every matching process is in a terminal state.
           # Raises asyncio.TimeoutError on the internal ceiling
           # (cancel_grace_seconds + 25s). Do NOT call from inside a task
           # whose metadata matches the filter — use group_cancel instead.
```

`metadata_filter` is a non-empty `dict[str, Any]` (AND-equality match against task
metadata); `{}` / `None` is rejected with `ValueError` (use `Optio.shutdown()` to
drain everything).

`block_new_launches=True` registers a `block_launches(metadata_filter)` guard for
the duration of the call (released on return or exception, even on the
`group_cancel_and_wait` `TimeoutError` path). When True, both helpers also run a
post-snapshot leak sweep (100 ms settling delay then re-list) to catch launches
that raced past `_check_launch_blocks` before the guard registered.

`persist=True` makes the installed launch block persistent — the block is
written to `{prefix}_launch_blocks` and remains in effect after the call
returns (and across restarts). `persist=True` requires `block_new_launches=True`,
otherwise `ValueError` is raised. Remove later via `unblock_launches(metadata_filter)`.

Specs: `docs/2026-04-30-group-cancel-design.md`,
`docs/2026-04-30-persistent-launch-blocks-design.md` (for `persist` / `reason`).

---

### Optio.block_launches / Optio.unblock_launches

Reject launches whose task metadata matches a filter. `block_launches` is the
async context manager primitive; `unblock_launches` is the symmetric removal
operation for the persistent variant.

```python
async with optio_core.block_launches(
    launch_filter: ProcessMetadataFilter,
    *,
    persist: bool = False,
    reason: str | None = None,
):
    ...  # while the CM is active, any launch whose task metadata matches
         # `launch_filter` raises LaunchBlocked. Multiple concurrent
         # block_launches() calls — overlapping or identical filters —
         # stack independently. An empty filter `{}` matches every task
         # metadata (blocks all launches).

await optio_core.unblock_launches(
    launch_filter: ProcessMetadataFilter,
) -> int  # remove every persistent record AND every in-memory block entry
          # whose filter equals `launch_filter` by exact dict equality.
          # Returns the number of in-memory entries removed.
```

`persist=False` (default) keeps the in-memory-only behaviour: the block is
released when the context manager exits.

`persist=True` writes a record to `{prefix}_launch_blocks` on entry and **does
not remove it on exit** — the block remains active after the CM returns and is
reloaded into `_launch_blocks` on every `init()`. `reason` is stored on the
persistent record (default `None`). Remove a persistent block with
`unblock_launches(launch_filter)`; the match is exact-dict equality (not filter
subsumption).

When a non-null `reason` is set, it is also appended to the `LaunchBlocked`
exception message raised by matching launches (`...; reason={reason}`). Set on
either transient or persistent blocks; null reason leaves the message
unchanged.

Spec: `docs/2026-04-30-persistent-launch-blocks-design.md`.

---

### Automatic lifecycle guarantees

- **Terminal states** (`done`, `failed`, `cancelled`): executor clears `widgetUpstream`
  automatically so the proxy returns 404 after the process ends.
- **Dismiss / relaunch**: both `widgetData` and `widgetUpstream` are cleared when a
  process is dismissed or re-launched.

---

### optio_core.rpc_server

```python
optio_core.rpc_server: RpcServerCore | None
```

The `RpcServerCore` constructed (a `RedisRpcServer` when `redis_url` is provided, or
whatever the caller supplied via `init(rpc_server=...)`), or `None` if no Redis is
configured. Apps register additional clamator services on this attribute before calling
`optio_core.run()`.

---

### init() — RPC server parameters (phase 2+)

Two new keyword arguments added to `optio_core.init()`:

- `rpc_server` (`RpcServerCore | None`): Pre-built clamator RPC server. Mutually
  exclusive with `redis_url`. When supplied, optio-core registers `OptioEngineService` on
  it but does not own its lifecycle.
- `redis_url` (existing): When supplied, optio-core constructs a `RedisRpcServer`
  internally, registers `OptioEngineService`, and exposes it at `optio_core.rpc_server`.

Full `init()` signature (as of phase 2):

```python
await optio_core.init(
    mongo_db: AsyncIOMotorDatabase,
    prefix: str = "optio",
    redis_url: str | None = None,
    rpc_server: RpcServerCore | None = None,
    services: dict[str, Any] | None = None,
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None,
    cancel_grace_seconds: float = 5.0,
) -> None
```
