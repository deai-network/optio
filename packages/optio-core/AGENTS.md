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

Optional field. Defaults to `False`. When `True`, task sync publishes `supportsResume=True`
into the process document (refreshed via `$set` on every sync) and the UI switches the launch
button to a `Dropdown.Button` (Resume primary / Restart menu) when `hasSavedState` is also true.
Tasks that set this should use `ProcessContext.mark_has_saved_state()` / `clear_has_saved_state()`
and the blob helpers to persist and restore checkpoint data.

---

### TaskInstance.resurrect

```python
@dataclass
class TaskInstance:
    ...
    resurrect: Callable[[ProcessContext], Awaitable[None]] | None = None
```

Optional hook that saves the work a failed run left on its host (snapshot +
`mark_has_saved_state()`) and removes the host leftovers. Run by `Optio.resurrect` outside
`execute`, followed by a resume. Raises `optio_core.NothingToResurrect` when nothing is left
to save. Requires `supports_resume=True`; task sync raises `ValueError` otherwise. Task sync
publishes `supportsResurrect` (whether the hook is set) into the process document. The task
flags unsaved host work with `ProcessContext.mark_unsaved_work()` / `clear_unsaved_work()`
(`hasUnsavedWork`); a launch clears the flag, and so does a resurrect that saved the work or
found nothing.

`optio_core.ResurrectOutcome` (`ok`, `reason`, `proc`) is the result type of `Optio.resurrect`.
`reason` is one of `not-found`, `not-resurrectable`, `no-resurrect-support`,
`resurrect-in-progress`, `launch-blocked`, `shutting-down`.

---

### TaskInstance.schedule_offset_seconds

```python
@dataclass
class TaskInstance:
    ...
    schedule_offset_seconds: int | None = None
```

Optional field, after `resurrect`. A cron `schedule` has minute resolution, so every
task scheduled `*/1 * * * *` fires at second 0 of the same minute. Setting an offset (0-59)
makes the scheduler build the same `CronTrigger` that `CronTrigger.from_crontab` would,
plus `second=offset`, so the task fires that many seconds into each matching minute.
`None` keeps second 0. Applications with many per-item schedules (e.g. one heartbeat per
item) can spread them by deriving a stable offset from the process id. An out-of-range or
non-int value is logged as a schedule error and the task is not scheduled, like a bad
crontab expression.

---

### TaskInstance.auto_cancel_children

```python
@dataclass
class TaskInstance:
    ...
    auto_cancel_children: bool = True
```

When `True` (default), `lifecycle.cancel(this_process)` recurses to active direct children and cancels each. Recursion threads the same monotonic deadline through every level so descendants share one grace budget. Internal callers pass `inherit_deadline=...`; external callers omit it.

When `False`, this task owns shutdown of its own children. After receiving cancel, the task's execute fn is responsible for cancelling/finalizing children within the parent's grace. If it does not, the force-cancel cascade catches every live descendant at grace expiry.

`executor.force_cancel(oid)` walks `list_direct_children(oid, states=ACTIVE_STATES)` and recursively force-cancels each. Unconditional — no flag check. Idempotent at every touchpoint: `task.cancel()` is idempotent, `_write_force_cancelled_state` is a conditional Mongo update on `state in ACTIVE_STATES`.

Upward propagation (alpha): abnormal child terminal dispatches by breach reason.

- **Failure breach** (child ended `failed` with `survive_failure=False`): invokes `notify_parent_failure(parent_process_id)`, wired to `Optio._cancel_active_children`. This cancels only the parent's other active direct children — sibling-only descent. It does **not** set the parent's `cancellation_flag`, does **not** change the parent's row state. The failure signal reaches the parent via `ChildProcessFailed` (single-child path) or `ExceptionGroup[ChildProcessFailed]` (parallel_group path); the parent's terminal state is then determined by whether user code re-raises (→ `failed`) or catches+returns (→ `done`).
- **Cancellation breach** (child ended `cancelled` with `survive_cancel=False`, or external cancel hits a non-surviving group): invokes `notify_parent_abnormal(parent_process_id)`, wired to `Optio.cancel`. This cascades cancellation upward — the parent's row transitions through `cancel_requested`/`cancelling`, the parent's flag is set, and the recursive descent cancels other direct children. The parent's `ctx.should_continue()` returns `False` inside any subsequent `except` handler. Terminal state: `cancelled` (parent catches+returns) or `failed` (parent re-raises).

For `parallel_group`, `ParallelGroup` records `_breach_reason: Literal["failure", "cancel", None]` per child outcome (failure dominates over cancel — any non-survived failed child pins the group's breach to "failure", regardless of any concurrent cancellation). `__aexit__` dispatches to the right callback based on the final reason. Per-child `run_child` inside groups hardcodes `survive_*=True`, so the executor-level alpha path does not fire for individual group children.

`ctx.should_continue()` is a reliable in-flight discriminator: it returns `False` if and only if the process itself has been cancelled (external on self/ancestor, or cancel cascade from a non-surviving descendant). It does **not** become `False` because a child of this process failed.

`ctx.run_child` and `ParallelGroup.spawn` refuse to spawn new children when the parent's cancellation flag is set and `auto_cancel_children=True` — `run_child` returns `ChildOutcome(state="cancelled")` and `spawn` records a `ChildResult(state="cancelled", error="parent cancelled")` in `group.results`, without inserting a child doc.

Spec: `docs/2026-05-11-cancel-propagation-design.md`. Plan: `docs/2026-05-11-cancel-propagation-plan.md`.

---

### Structured child-failure propagation

When a child task fails, the exception object raised inside its `execute` function is preserved across the `run_child` / `parallel_group` boundary so parents can branch on the original type and inspect its fields.

```python
from optio_core.exceptions import ChildProcessFailed
from optio_core.models import ChildOutcome

# Single child — failure raises ChildProcessFailed, .original is the live exception
try:
    await ctx.run_child(execute=..., process_id=..., name="Downloader")
except ChildProcessFailed as e:
    if isinstance(e.original, DownloadFailed):
        log.warning(f"download failed: url={e.original.url} code={e.original.exit_code}")

# survive_failure=True — no raise, ChildOutcome carries the original instead
outcome = await ctx.run_child(..., survive_failure=True)
if outcome.state == "failed":
    handle(outcome.original_exception)
elif outcome.state == "cancelled":
    ...  # outcome.original_exception is None

# Parallel group — aggregate breach raises ExceptionGroup[ChildProcessFailed]
try:
    async with ctx.parallel_group(survive_failure=False) as g:
        await g.spawn(execute=..., process_id="a", name="A")
        await g.spawn(execute=..., process_id="b", name="B")
except* ChildProcessFailed as eg:
    for cpf in eg.exceptions:
        log.warning(f"{cpf.name}: {cpf.original!r}")
```

**Types:**

- `ChildProcessFailed(Exception)` — `name: str`, `process_id: str`, `original: BaseException`. `__cause__` is set to `original` so the traceback shows the chain. Import: `from optio_core.exceptions import ChildProcessFailed`.
- `ChildOutcome` — return type of `ProcessContext.run_child`. Fields: `state: str` (`"done" | "failed" | "cancelled"`) and `original_exception: BaseException | None`. Import: `from optio_core.models import ChildOutcome`. **Breaking:** `run_child` previously returned a bare state `str`; callers that captured the return must now read `.state`.
- `ChildResult` (extended) — `ParallelGroup.results[i]` now also carries `name: str` and `original_exception: BaseException | None`. Existing fields (`process_id`, `state`, `error`) unchanged.

**`ParallelGroup.__aexit__` raise type changed:** was `RuntimeError("Parallel group failed: ...")`, now `ExceptionGroup("Parallel group failed", [ChildProcessFailed, ...])`. Each entry in the group is a `ChildProcessFailed`; for children that were cancelled (not raised), `.original` is a synthetic `RuntimeError("child cancelled")` so it is never `None`. Callers use `except* ChildProcessFailed` (Python 3.11+).

**No-execute-fn early-fail.** When `_execute_process` hits the no-execute-fn branch (`execute_fn is None`), it returns `("failed", None)`. `execute_child` synthesizes a `RuntimeError(f"Child process '{name}' failed")` as `ChildProcessFailed.original` so callers always see a non-`None` `.original`.

Persistence is unaffected: Mongo `status.error` remains the `str(e)` text of the original exception. The structured propagation is in-process only — resumed processes lose the live exception object (consistent with the rest of the resume model).

Spec: `docs/2026-05-12-child-failure-propagation-design.md`. Plan: `docs/2026-05-12-child-failure-propagation-plan.md`.

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

# Flag that the host workdir holds work newer than the last snapshot (hasUnsavedWork=True).
# Idempotent; warn-and-noop when the task has no resurrect hook (supportsResurrect=False).
await ctx.mark_unsaved_work() -> None

# Clear hasUnsavedWork (a save completed). Idempotent; silent when already clear.
await ctx.clear_unsaved_work() -> None
```

---

### ProcessContext GridFS blob helpers

```python
# Store a blob.  Returns an async context manager; the yielded stream has a .file_id attribute.
# All blobs are tagged with metadata {processId, prefix, name}.
# file_id pre-selects the blob's id (so it can be recorded before writing); default: GridFS generates one.
async with ctx.store_blob(name: str, file_id: ObjectId | None = None) as stream:
    stream.file_id  # GridFS file_id assigned to this blob
    await stream.write(data: bytes)  # write content

# Load a previously stored blob by file_id.
# Returns an async context manager yielding an async byte-stream.
async with ctx.load_blob(file_id) as stream:
    data = await stream.read()

# Delete a blob by file_id.  No-op if the file_id does not exist.
await ctx.delete_blob(file_id) -> None

# Store this run's result file, which optio-api serves at GET /processes/:id/result.
# One GridFS file (the default bucket), tagged {processId, prefix, name: filename, kind: "result"}.
# On the block's clean exit the record gets result = {fileId, filename, contentType, size}
# (size in bytes; fileId is server-side only). An exception inside the block deletes the
# partial file and leaves no result. One result per run (keyed on the run's token, as
# publish_result): a second store_result raises RuntimeError; a failed block does not count.
async with ctx.store_result(filename: str, content_type: str) as stream:
    await stream.write(data: bytes)
```

The result file lives as long as its record: `store.relaunch_reset` (relaunch, dismiss) removes
`result` and then deletes the file; `store.delete_process`, `store.delete_descendants` and
`store.delete_ephemeral_processes` delete the files of the records they delete, after deleting
them (`store.delete_result_files(db, prefix, process_oids) -> int`). The files of records removed
otherwise (`purge_processes`' matched roots, `remove_stale_processes`, `resync(clean=True)`, the
TTL monitor, a record deleted while its task wrote the file) are deleted by
`store.sweep_orphan_results(db, prefix) -> int`: this prefix's `kind: "result"` files whose
`metadata.processId` is no record's `_id`, checked in batches. `Optio` runs it at `init()` (after
`_delete_ephemeral_leftovers`) and every `lifecycle.RESULT_SWEEP_SECONDS` (600) from `run()`; a
failed sweep is logged, never raised. Chunks without a file document (a write that crashed before
closing) carry no process id and stay. Spec: `docs/2026-10-25-factory-results-design.md` §1

---

### ProcessContext widget methods

```python
await ctx.set_widget_upstream(url: str, inner_auth: InnerAuth | None = None) -> None
# Registers the upstream URL for the widget proxy. The proxy will forward all
# /api/widget/:database/:prefix/:processId/* requests to this URL (:processId is the
# process document's _id, as hex). inner_auth is injected per-request.

await ctx.clear_widget_upstream() -> None
# Removes widgetUpstream so the proxy returns 404 for this process.

await ctx.set_widget_data(data) -> None
# Overwrites widgetData with any JSON-serializable value. The tree stream delivers
# this to the widget component via the SSE update event.

await ctx.set_widget_todos(items) -> None
# Sets widgetData.todos and leaves sibling keys. No-op when widgetData is
# missing, null, or not an object (a dotted $set against null raises in Mongo).

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
| `supportsResume` | `bool` | Whether the task opted into resume support; refreshed via `$set` on every sync; an ad-hoc child's (e.g. a TaskFactory's launch's) is its task's, written at its definition |
| `hasSavedState` | `bool` | Whether the task has a valid checkpoint; `$setOnInsert: false`; mutated only by `mark/clear_has_saved_state` |
| `supportsResurrect` | `bool` | Whether the task has a `resurrect` hook; refreshed via `$set` on every sync |
| `hasUnsavedWork` | `bool` | The host workdir holds work newer than the last snapshot; `$setOnInsert: false`; set by `mark/clear_unsaved_work`, cleared by optio-core when a launch starts and when a resurrect saves the work or finds nothing |
| `resurrecting` | `bool` | Transient: true while a resurrect's background run is in progress; set and cleared by optio-core only (see `Optio.resurrect`); `init()` clears any marker a dead engine left behind |
| `taskFactory` | `bool \| absent` | True on a `TaskFactory`'s record (see "TaskFactory"); `$set` on every sync, unset on a TaskInstance's; such a record never runs |
| `parameters` | `array \| absent` | A `TaskFactory`'s field definitions as plain JSON; `$set` on every sync, unset on a TaskInstance's |
| `result` | `{ fileId, filename, contentType, size } \| absent` | The run's result file, set by `ctx.store_result` on its block's clean exit; removed by the record's reset. `fileId` (the GridFS id) is server-side only |

`hasSavedState` is backfilled to `false` on all existing documents by migration `m003_backfill_has_saved_state`
(runs on startup; depends on m002). `supportsResurrect`, `hasUnsavedWork` and `resurrecting` have
no backfill; an absent field reads as `false`. All three are wire keys: the engine RPC's process
payloads (`launch`, `cancel`, `dismiss`, `resurrect` results) carry them, and `taskFactory` and
`parameters` too (`_engine_service._PROCESS_WIRE_KEYS`; absent on a TaskInstance's record). So is
`result`, without its `fileId`: `_engine_service._to_process_dict` strips it (the generated
`Result` model is `extra="forbid"`).

---

### Optio.launch / Optio.launch_and_wait

Both methods gain a `resume` keyword argument (default `False`), and `params`, `ephemeral` and
`metadata` (a TaskFactory's launch values, whether its launch's record is deleted when its run
ends, and the launch's stamp; see "TaskFactory"):

```python
await optio_core.launch(process_id: str, resume: bool = False, *, session_id: str | None, params: dict | None = None, ephemeral: bool | None = None, metadata: Mapping[str, str | int | float | bool | None] | None = None) -> LaunchOutcome
await optio_core.launch_and_wait(process_id: str, resume: bool = False, *, session_id: str | None, params: dict | None = None, ephemeral: bool | None = None, metadata: Mapping[str, str | int | float | bool | None] | None = None) -> None
await optio_core.launch_and_await_result(process_id: str, resume: bool = False, *, session_id: str | None, timeout: float | None = None, params: dict | None = None, ephemeral: bool | None = None, metadata: Mapping[str, str | int | float | bool | None] | None = None) -> Any
```

When `resume=True`, the executor sets `ctx.resume = True` when the task starts. `launch`
refuses it with `no-resume-support` when the process has `supportsResume` false, and
launches fresh (`resume=False`) when it has no saved state (`hasSavedState` false);
`launch_and_wait` does neither check. `session_id` is required (keyword-only; `None` for
an unattended launch).

While a resurrect of the process is in progress, `launch` returns `not-launchable`; so it does for
a TaskFactory's launch, which runs once, unless `resume=True` resumes a kept one (see "TaskFactory").

`LaunchOutcome` (`ok`, `reason`, `proc`, `message`): `reason` is one of `not-found`,
`not-launchable`, `launch-blocked`, `no-resume-support`, `shutting-down`, `parameters-required`,
`not-a-factory`, `invalid-parameters`, `invalid-metadata`; `message` is set for
`invalid-parameters` and `invalid-metadata` only (the engine RPC's launch answers it unchanged). On
`ok=True`, `proc` is the record re-read after the launch, as `cancel`'s `CancelOutcome.proc` is
after the cancel; an ephemeral record that its run has already deleted by then (a run that
ended at once, or on the cancel's flag) is answered as read before the action. The engine RPC
answers an ok outcome without a record (none comes) `not-found`.
`LaunchError` (`process_id`, `reason`, `message`) is what `launch_and_await_result` raises on a
refusal, and `launch_and_wait` on a TaskFactory's refused launch and on a launch of a
TaskFactory's launch (`not-launchable`: it runs once; a refused resume of one: its reason).

---

### TaskFactory

```python
from optio_core import TaskFactory

@dataclass
class TaskFactory:
    process_id: str
    name: str
    parameters: list[dict[str, Any]]         # vultus-core field definitions as plain JSON
    execute: Callable[..., Awaitable[None]] | None = None  # a launch's task; reads ctx.params (exactly one of execute / task_for)
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    special: bool = False
    warning: str | None = None               # shown before the launch form
    cancellable: bool = True
    supports_resume: bool = False            # with execute: a launch's task supports resume; with task_for the instance's rules (True refused at sync)
    auto_resume: bool = False                # with execute: a launch's task is auto-resumed (needs supports_resume); with task_for the instance's rules (True refused at sync)
    ui_widget: str | None = None             # a launch's widget
    ttl_seconds: int | None = None           # None: a launch is ephemeral; set: kept that long after it ends
    auto_cancel_children: bool = True
    validate: Callable[[dict[str, Any]], str | None] | None = None  # a returned message refuses the values
    name_for: Callable[[dict[str, Any]], str] | None = None        # a launch's name; default `name`
    task_for: Callable[[dict[str, Any], str], TaskInstance] | None = None  # instead of execute: a launch's whole task
```

A task launched with values. `get_task_definitions` may return factories among its
TaskInstances. Spec: `docs/2026-10-20-task-factory-design.md`.

**`execute` or `task_for`.** A factory has exactly one (sync raises `ValueError("Task factory
'<id>': exactly one of execute and task_for must be set")` for both or neither). With `execute`,
every launch runs it, the values in `ctx.params` (mild differences). With `task_for(values,
process_id)`, each launch gets the `TaskInstance` it returns (a different `execute`, `ui_widget`,
`supports_resume`, ... per launch); it is called after the values are checked, with the
no-values left out, and the launch's process id (`<factory id>_<8 hex>`, which an agent task
needs for its directory). optio overrides on the returned instance: `process_id`, `params` = the
values, `metadata` (the instance's, the factory's over it, then `taskFactory`), `name` =
`name_for(values)` if the factory has one, else the instance's, and `ttl_seconds` = the factory's;
the rest is the instance's. An exception from `task_for` is not caught: `launch` raises it.
The factory's `supports_resume` and `auto_resume` are an `execute` factory's launches' (their
task's); with `task_for` the instance's rule, so sync refuses a factory with `task_for` that sets
either (`ValueError("Task factory '<id>': supports_resume is the task_for instance's; it cannot be
set on a factory with task_for")`, the same for `auto_resume`). `auto_resume` needs
`supports_resume`, as a task's does (`ValueError("Task factory '<id>': auto_resume=True requires
supports_resume=True")`). With `task_for`, the factory's fields that apply are its own record's
(`name`, `description`, `parameters`, `metadata`, `special`, `warning`) and what optio sets on
every launch (`metadata`, `ttl_seconds`, `name_for`), with `validate`; its `ui_widget`,
`cancellable` and `auto_cancel_children` are not used (the instance's are).
`child_task(factory, values, process_id=None)` is the single builder (a given `process_id` is
reused, for rebuilding a kept launch). Spec: `docs/2026-10-22-task-factory-launch-options-design.md` 1.4.

**The record.** Synced like a task's, plus `taskFactory: true` and `parameters` (both `$set` on
every sync); `params` is `{}`, `uiWidget` null (the widget is the launches'), `supportsResume`
false. A TaskInstance's record has neither field (`$unset` on sync, for a process id that
switched kind). Factories are kept in `Optio._factories`, apart from the executor's task
registry, so nothing that resolves a task by process id (resurrect, the conversation archive,
deferred migrations) finds one, and the scheduler never gets one.

**It never runs.** `launch` without `params` answers `parameters-required`, with `resume=True`
`no-resume-support` (so the auto-resume sweep skips it), `resurrect` answers
`no-resurrect-support`, and `Executor.launch_process` returns None for a `taskFactory` record
(any direct path). Its own state never changes. A resync that removes the factory cancels its
running launches, the active records under a stale root (see "Optio.resync").

**Checks** (`optio_core.task_factory`):

- `check_factory(factory: TaskFactory) -> None` (also exported from `optio_core`) is the sync's
  whole check of a factory, which the sync runs for each one and whose `ValueError` it raises: in
  order, exactly one of `execute` and `task_for`, neither `supports_resume` nor `auto_resume` with
  `task_for`, `auto_resume` only with `supports_resume` (the messages above), then
  `check_parameters`. It is public so that a host whose definitions come from several parts (a
  plugin's) can check one factory first and leave out what it refuses, instead of failing the
  whole sync.
- `check_parameters(parameters: list[dict]) -> None` runs at sync (in `check_factory`) and raises
  `ValueError("<id>: <what>")` (a definition without a usable id: `"parameters[<i>]: ..."`);
  `check_factory` re-raises it as `ValueError("Task factory '<process id>': parameters: <id>:
  <what>")`. A
  definition has `kind` (`boolean`, `oneOf`, `manyOf`, `numeric`, `range`, `text`), `id` (unique,
  an identifier, `^[A-Za-z_][A-Za-z0-9_]*$`, so the `"<id>: "` prefix is unambiguous and the id a
  plain Mongo key), `label`, the kind's options (`choices: [{value: str, label?}]`, non-empty,
  distinct values; `min`, `max`, `step` (> 0), `min <= max`, a range needing both, and vultus's
  `integer` (bool); `maxLength`, a positive int), `initialValue` (checked like a value, may be
  empty) and `required` (bool, default true); any other key is refused. A `label` is a string or
  a non-empty map of language code to string. A null key counts as absent.
- `check_values(parameters, values) -> str | None` runs at launch, before the child exists, and
  returns the first refusal `"<id>: <what>"` or None: an unknown key (`"<key>: not a
  parameter"`), a required parameter without a value (`"<id>: required"`; null is no value, nor is
  the empty string of a `text` or the empty list of a `manyOf`), then each value's kind: `boolean`
  a bool; `oneOf` one of its choices' values; `manyOf` a list of distinct choice values; `numeric`
  an int or finite float (never a bool) within `min`/`max`, a whole one with `integer` (`2.0`
  counts); `range` a 2-element list of numbers, `from <= to`, within `min`/`max`, whole ends with
  `integer`; `text` a string within `maxLength`. Nothing is coerced. Then the factory's
  `validate(values)`, whose message also refuses. A refusal is
  `LaunchOutcome(ok=False, reason="invalid-parameters", message=...)`.
  `check_factory_values(factory, values) -> str | None` is both, the fields' checks then
  `validate` (with the no-values left out): a launch's check, and a kept launch's at its rebuild.
- `without_no_values(parameters, values)`: the values without the ones that are no value, as the
  required check counts it (null; the empty string of a `text`; the empty list of a `manyOf`).
  `validate`, `name_for` and the task's `ctx.params` get these, so a left-out value, a null one and
  an empty one are one shape, whether a form (optio-ui sends `''` and `[]`) or a program launched
  it. `False` and `0` are values and stay.

**A launch** (`launch(factory_id, session_id=..., params=values, ephemeral=None, metadata=None)`), refusals in this order:
`shutting-down`, `not-found`, `no-resume-support`, `parameters-required`, `not-launchable` (no
definition synced for the record), `invalid-parameters`, `invalid-metadata` (the stamp, below),
`launch-blocked` (a block matching the child's metadata). No child is defined while shutting
down, whichever entry point. Then
`task_factory.child_task(factory, values)` is the child's TaskInstance (with `task_for`, the one
it returns, see above): the factory's `execute`
and fields, `params` = the values without the no-values (`without_no_values`), metadata the
factory's plus `taskFactory: <factory id>` (and the launch's stamp, below), process id
`f"{factory.process_id}:{secrets.token_hex(4)}"`, name `name_for(values)` or `name`.
It is defined with `adhoc_define(child, parent_id=<factory record _id>,
ephemeral=<see below>)` (its record copies the factory record's `ttlSeconds`) and
launched with the same `session_id` (`Optio._launch_child`, its only launch);
`LaunchOutcome(ok=True, proc=<the child's record>)`. A child whose launch is then refused (a block
or a shutdown since its definition) is deleted. `params` or `ephemeral` on a record that is no
factory's: `not-a-factory`. `ephemeral` (None, True, False) says whether the launch's record is
deleted when its run ends: None (the default) follows the factory (`factory.ttl_seconds is None`),
True or False overrides it (a TTL'd factory's launch may be ephemeral, a TTL-less one's kept; a
kept one without a TTL stays until a dismiss). `ephemeral` is a reserved parameter id:
`check_parameters` refuses it (`parameters[i]: id 'ephemeral' is reserved for the launch's own
flag`). `launch_and_wait` and `launch_and_await_result` take `params`, `ephemeral` and `metadata` the same way;
their refusals raise `LaunchError` (a launch block in `launch_and_wait`: `LaunchBlocked`), and
`launch_and_await_result`'s result future is keyed by the child's process id before the child
starts.

**The launch stamp** (`metadata=`; factory results spec §2). A map of scalars (str, int, float,
bool, None) that server-side code stamps on one launch, e.g. `{"user": <id>}` from optio-api's
`launchMetadata` hook; the engine RPC's `launch` forwards `LaunchParams.metadata` (the generated
model parses a JSON number as a float: `_engine_service._launch_stamp` turns a whole one back
into an int, `42.0 -> 42`, `1.5` stays). `_factory_child` merges it into the metadata
`child_task` composed, before the child is defined: the child's metadata is the factory's, the
stamp and `taskFactory`, which `ctx.metadata` reads and the records under the launch inherit (so
an app's `visibility`/`authorize` can match it). A stamp key the composed metadata has already
(the factory's own, with `task_for` the instance's, or the reserved `taskFactory`) never
overrides it: the launch is refused before anything is defined,
`LaunchOutcome(ok=False, reason="invalid-metadata", message="kind, taskFactory: already set by
the factory (its metadata, or the reserved taskFactory)")` (the colliding keys in the stamp's
order; `LaunchError` from `launch_and_wait` and `launch_and_await_result`). A launch that is no
factory's (a task's run, a kept launch's resume) makes no new record to stamp: `metadata` is
ignored there.

**A launch runs once** (spec §1.2). A record whose metadata carries `taskFactory`
(`task_factory.is_factory_launch`: a launch, e.g. one its factory's `ttl_seconds` keeps, and the
records under it, which inherit its metadata) is never launched again: `launch` answers
`not-launchable` (`launch_and_wait` and `launch_and_await_result` raise
`LaunchError("not-launchable")`) before it looks for an execute function, whatever the task
registry holds (the launch's TaskInstance, which a sync keeps while the factory is synced and
the launch can still run, or nothing once the factory is gone). Nor is it reset by a dismiss, which would leave an idle record that never runs and
never expires (it removes `expireAt`): `dismiss` of an ended launch (done, failed, cancelled)
deletes it with its descendants and its task (the deletion an ephemeral launch's end takes, by
its `_id`: resolving its process id again could find another record) and answers `ok=True` with
`proc=None`; a running one is `not-dismissable`. Only the launch itself is deleted
(`task_factory.is_the_launch_itself`: `is_factory_launch` at depth 1; its factory's record need not
exist, as a sync that removes a factory deletes that record only and its kept launches stay, to
be dismissed): a record under it (a `run_child` sub-task, depth 2 or more, which inherits its
metadata, and may share its process id with another launch's) is `not-dismissable`, ended or not.
optio-ui shows it no play button (`isTaskFactoryLaunch`), and the remove button on the launch
itself only (`isTaskFactoryLaunchItself`).

**Resuming a kept launch** (launch options spec §1.5). A launch's task supports resume when it
is an `execute` factory's whose `supports_resume` is set, or a `task_for` instance with
`supports_resume=True`; its record carries `supportsResume` (an ad-hoc child's is its task's), so
`ctx.mark_has_saved_state` works in it. `launch(<launch>, resume=True)` (and `launch_and_wait`,
`launch_and_await_result`) resumes a kept one as a task's resume does (`ctx.resume=True`, its
stored `params`); refused (`Optio._relaunch_refusal`) with `no-resume-support` when its record
has `supportsResume` false, and `not-launchable` without saved state (that resume would be a
fresh run) or without a registered task. A sync keeps the tasks of a factory's launches
registered while the factory is synced and they can still run (`_launch_tasks_that_cannot_run`:
their record exists, and either their run has not ended, one defined and about to run included,
or they support resume); the task of an ended launch that cannot resume goes. A sync that removes the
factory drops its launches' tasks, and clears `supportsResume` and `autoResumeScheduled` on its
launches' records that are not running (`_end_resume_of_launches`: no Resume the engine would
refuse). A factory changed by a re-sync keeps its launches' existing tasks until the next
restart, which rebuilds them from the new definition. A launch counts as top-level
for auto-resume (depth 1, its parent the factory's record, which never runs): its task's
`auto_resume` (an `execute` factory's `auto_resume`, a `task_for` instance's) stamps it at
shutdown, and the post-restart sweep resumes it. At `init()`, after
the sync and before the deferred migrations, `Optio._rebuild_kept_launches` rebuilds the task of
every kept launch that can resume (`metadata.taskFactory`, `supportsResume` and `hasSavedState`
true, in a launchable state: a crashed run is `failed` by then, a stopped one `cancelled`, a
`done` one too) from its factory and stored values as at its launch (`check_factory_values`, then
`child_task(factory, params, <its process id>)`: `task_for` gets the stored values and the same
process id) and registers it in the executor's task registry, where resume, auto-resume, the
conversation archive and the deferred migrations find it; its record's `supportsResume` and
`uiWidget` are then the rebuilt task's (written when they differ: a factory whose new definition
no longer supports resume is neither offered a Resume nor resumed). One that cannot be rebuilt
(`'<factory id>' is no longer a task factory`, `its values are refused: <id>: <what>`, `its task
could not be rebuilt: <exception>`) no longer resumes, nor is judged again: `supportsResume` and
`autoResumeScheduled` set false. If its run was interrupted (spec §1.5: the states optio resumes
or auto-resumes a task from: a record the reconcile settled at this start, or one a graceful
shutdown stopped and stamped, `cancelled` with `autoResumeScheduled`) it is settled `failed`, as
an orphan is: its error and a log line `Cannot resume after the engine restart: <why>`,
`expireAt` set by its TTL. One whose run ended (`done`, `failed`, `cancelled` without the stamp,
e.g. a user's cancel) keeps its state, error, timestamps, log and `expireAt` as they were, and
the reason goes to the engine's log as a warning (`Kept launch <id> (<state>) can no longer
resume: <why>`). An engine without `get_task_definitions`
rebuilds and settles nothing. Ephemeral launches are still deleted at `init()`, before.

**The reserved key.** `metadata.taskFactory` is optio's: only a launch's definition writes it
(`_factory_child`, through the internal `Optio._adhoc_define`, which keeps it; a launch stamp
with the key is `invalid-metadata`). The public
`adhoc_define` drops a `taskFactory` key from the task's metadata, for a root and for a child, so
a process a launch's task defines with its `ctx.metadata` (or `{**ctx.metadata, ...}`) is no
launch of a factory, and launches. A `run_child` child inherits its parent's metadata as given
(it never goes through `launch`).

**adhoc_define, a child:** its record now carries the task's `cancellable`, `description`,
`special`, `warning` and `ui_widget` (`uiWidget`, written only when set), as a root's does.

---

### Optio.resurrect

```python
await optio_core.resurrect(process_id: str, *, session_id: str | None) -> ResurrectOutcome
```

Saves the work a failed run left on its host, then resumes. Fire-and-forget like `launch`:
preconditions are answered at once, checked in this order, as `ResurrectOutcome(ok=False,
reason=...)`: `shutting-down`, `not-found`, `no-resurrect-support` (the task has no
`resurrect` hook), `resurrect-in-progress`, `not-resurrectable` (state not launchable, or
`hasUnsavedWork` false), `launch-blocked` (it ends in a launch, so launch blocks apply).

On `ok=True` (`proc` is the process document at that moment) a background task logs
"Resurrect requested", sets `resurrecting` to true, sets progress "Resurrecting: saving the
unsaved work…" and runs the task's `resurrect` hook with a `ProcessContext` for the process
(`resume=False`). Then:

- hook returns: `hasUnsavedWork` is cleared, then the process is launched with
  `resume=True` and the same `session_id`. If that launch is refused (e.g. a launch block
  appeared meanwhile), the reason is logged ("Resume after resurrect not started: ...").
- hook raises `NothingToResurrect`: `hasUnsavedWork` is cleared and the reason logged; no
  resume.
- hook raises anything else: the error is logged at `error` level and `hasUnsavedWork` stays
  set, so the resurrect can be retried.

An error escaping the background run itself (e.g. a failed Mongo write after the hook) is
logged by the task's done-callback on the `optio_core_core` logger ("resurrect of
<processId>: background run failed", with the exception); a cancel is not logged.

Progress is cleared and `resurrecting` set back to false in every case, before the follow-up
resume is launched; the process state is not changed by the resurrect itself. A marker left
set by an engine that died mid-resurrect is cleared by the next `init()` (with the reset of
interrupted processes). While the hook runs, `launch` on the process returns `not-launchable` and a second
`resurrect` returns `resurrect-in-progress`. `shutdown()` cancels a resurrect in progress
(after cancelling running tasks) and waits at most `force_cancel_shield_seconds` (an `init()`
parameter) for it to unwind; a hook that ignores the cancel is abandoned, not waited on.
`hasUnsavedWork` stays set. Spec:
`docs/2026-10-07-resurrect-failed-session-design.md`.

Auto-resume: the post-restart sweep (tasks with `auto_resume`) does not resume a stamped
process whose `hasUnsavedWork` is set, since a resume rebuilds the workdir and would wipe that
work. When the process has `supportsResurrect` and its task a registered `resurrect` hook, the
sweep calls `resurrect(process_id, session_id=None)` instead (save, then resume); otherwise it
skips the process and logs "Auto-resume skipped <processId>: unsaved work on the host (use
Resurrect)". The `autoResumeScheduled` stamp is cleared in both cases; a refused resurrect is
logged like a refused launch.

Engine RPC: `resurrect({ processId, sessionId })` (`OptioEngineService.resurrect`) calls
`Optio.resurrect` and returns `{ ok: true, process }` or `{ ok: false, reason }` with the
reasons above.

---

### Optio.count_processes

```python
await optio_core.count_processes(
    *,
    states: Collection[str] | None = None,       # any of these states
    metadata: dict[str, str | list[str]] | None = None,  # AND; list value = any of
    roots_only: bool = False,                    # top-level processes only
) -> int
```

Counts process documents with a `count_documents` query; nothing is loaded. Meant
for frequent checks, e.g. a scheduler counting a source's active syncs before
launching another: `count_processes(states=ACTIVE_STATES, metadata={"sourceId": s,
"kind": ["sync", "sync-dry"]}, roots_only=True)`. Child processes inherit their
parent's metadata, so `roots_only=True` is what counts each sync once. Store-level
function: `optio_core.store.count_processes`.

---

### Optio.resync

```python
await optio_core.resync(clean: bool = False, metadata_filter: dict | None = None) -> dict[str, str]
```

Re-runs `get_task_definitions` and syncs the records (`_sync_definitions`); with a
`metadata_filter`, only the matching definitions and records. `clean=True` deletes the
(in-scope) records first. The root records of definitions no longer generated (stale roots) are
deleted, but first what still runs there is asked to stop: the stale roots in an active state,
and the active records under every stale root (`store.find_active_descendants`) — not only a
removed TaskFactory's launches but also a stale task's children, those under a parent with
`auto_cancel_children=False` included, since each gets its cancellation flag directly
(`Executor.request_cancel_with_deadline`). The resync waits for all of them to reach a terminal
state (or disappear) within the same `cancel_grace_seconds`; a record that no task of this engine
runs never does, so it makes the resync wait the whole grace, after which the stale roots are
deleted anyway (logged as a warning). Returns `{processId: oid_hex}` of the definitions synced.
The executor's task registry then holds the synced tasks, plus the tasks of the launches of
every factory still synced that can still run (see "Resuming a kept launch").

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
    purge_records: bool = False,               # then delete matching records + descendants
) -> int  # fire-and-forget; returns once cancels are issued (and, if
          # block_new_launches=True, after the leak sweep). Safe to call
          # from inside a task whose own metadata matches the filter.
          # Returns the number of processes cancellation was issued for.

await optio_core.group_cancel_and_wait(
    metadata_filter: ProcessMetadataFilter,
    block_new_launches: bool = False,
    *,
    persist: bool = False,                     # requires block_new_launches=True
    reason: str | None = None,                 # stored on the persistent record
    purge_records: bool = False,               # then delete matching records + descendants
) -> int  # blocks until every matching process is in a terminal state;
          # returns the number of processes cancellation was issued for.
          # Raises asyncio.TimeoutError on the internal ceiling
          # (cancel_grace_seconds + cancel_wait_ceiling_margin_seconds,
          # default 25s). Do NOT call from inside a task
          # whose metadata matches the filter — use group_cancel instead.
```

A matching row in `running`, `cancel_requested` or `cancelling` that no task in
this engine owns has lost its final-state write. Such a row is settled instead of
waited on: to the final state the executor still has queued for it, if any, or else
to `failed` with `"Process had no running task in this engine; its final state was
never recorded"`; an ephemeral row is then deleted, with its descendants (its run has
ended), its active children force-cancelled first (`Executor._force_cancel_children`, as a
force-cancel cascades), so that none runs on against a deleted record. `cancel()` and
`cancel_and_wait()` do the same (`cancel_and_wait()` then returns
`None`: the process no longer exists). This assumes one engine per (database, prefix).
Spec: `docs/2026-10-03-lost-final-state-writes-design.md`.

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
  process is dismissed or re-launched, and its result file goes (`result` unset, then the
  GridFS file deleted).
- **Record deletion**: `store.delete_process`, `store.delete_descendants` and
  `store.delete_ephemeral_processes` delete the result files of the records they delete. The
  result files of records removed by any other path (bulk deletes, the TTL monitor) are deleted
  by the sweep (`store.sweep_orphan_results`) at `init()` and every `RESULT_SWEEP_SECONDS` from
  `run()`; `shutdown()` stops that loop as it stops the supervisor.
- **Ephemeral processes** (`adhoc_define(..., ephemeral=True)`, `ctx.mark_ephemeral()`, a
  TaskFactory launch without `ttl_seconds`): the record and its descendants are deleted (and the
  ad-hoc task unregistered) whenever the run ends: at any terminal state the executor writes; after
  a force-cancel (`Executor.force_cancel`, once it has force-cancelled the active children); when a
  launch finds no execute function; and when a lost final write is settled
  (`Optio._settle_if_orphaned`, Rule 4, once it has force-cancelled the active children). On
  `init()`, before the reconcile of interrupted
  processes, `Optio._delete_ephemeral_leftovers` (`store.delete_ephemeral_processes`) deletes
  every ephemeral record of the engine's (database, prefix), whatever its state: one the restart
  interrupted, one whose end did not delete it (e.g. a Mongo failure in a force-cancel), one
  defined and never launched (a crash between `adhoc_define` and its launch). Its TaskInstance
  lived in the memory of the engine that defined it, so it can never run again. Records of
  another prefix are not touched.

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

Full `init()` signature:

```python
await optio_core.init(
    mongo_db: AsyncIOMotorDatabase,
    prefix: str = "optio",
    redis_url: str | None = None,
    rpc_server: RpcServerCore | None = None,
    services: dict[str, Any] | None = None,
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None,
    cancel_grace_seconds: float = 5.0,
    auto_resume_delay_seconds: float = 300.0,
    cancel_wait_ceiling_margin_seconds: float = 25.0,
    shutdown_drain_margin_seconds: float = 5.0,
    terminal_poll_interval_seconds: float = 0.1,
    force_cancel_shield_seconds: float = 2.0,
) -> None
```

The last five are timing knobs (stored on `OptioConfig`); the README's `init()` table
describes each.

### Process collection indexes and TTL

`Optio.init` calls `store.ensure_process_indexes(db, prefix)` on every start. It
creates, idempotently, `processId_1__id_-1`, `parentId_1_order_1`,
`rootId_1_depth_1_order_1`, `status.state_1`, `originatingSessionId_1` and the
`expireAt_ttl` TTL index (`expireAfterSeconds=0`, as migration m004) on
`{prefix}_processes`; the list is `store.PROCESS_INDEXES`. An index that conflicts
with an existing one (MongoDB codes 85/86) is logged as a warning and skipped; any
other error fails `init`. Beside it, `store.ensure_result_file_index(db)` creates
`store.RESULT_FILE_INDEX` on the default GridFS bucket's `fs.files` (one per
database): `metadata.kind_1_metadata.prefix_1_metadata.processId_1`, which
`store.delete_result_files` and `store.sweep_orphan_results` query, with the same
conflict rule.

`TaskInstance.ttl_seconds` is stored as `ttlSeconds`; a terminal state sets
`expireAt` = now + `ttlSeconds`; a record the TTL monitor removes leaves its result
file to `store.sweep_orphan_results`. `store.relaunch_reset` (relaunch and dismiss)
removes `expireAt`, so a running or dismissed record is never evicted. In the same
update it removes `result`, and then deletes the previous run's result file
(`store.delete_result_files`).
`store.create_child_process(..., ttl_seconds=)` stores a child's `ttlSeconds`; the
executor and `adhoc_define` pass the parent's. Spec:
`docs/2026-10-09-process-indexes-design.md`

---

### TaskInstance.session_crypto

```python
@dataclass
class TaskInstance:
    ...
    session_crypto: SessionCrypto | None = None
```

```python
@dataclass(frozen=True)
class SessionCrypto:
    encrypt: Callable[[bytes], bytes] | None = None
    decrypt: Callable[[bytes], bytes] | None = None
```

Optional field, after `schedule_offset_seconds`. A task's at-rest cipher for its own encrypted blob channels
(e.g. the session blob). Filled by an engine wrapper's task factory from its config
(e.g. optio-claudecode's `session_blob_encrypt`/`session_blob_decrypt`); other
engines may fill it later. `None` cipher members mean "no encryption configured" —
the blob is plaintext. Read back by code that resolves a process's `TaskInstance`
outside a running task — the engine resolves the task the same way
`Optio.resurrect` does, via the executor's task registry keyed by process id — most
notably a deferred migration (below), which needs the cipher to transform a blob it
is rewriting. Both exported from `optio_core`. Spec:
`docs/2026-10-11-conversation-buffer-encryption-design.md` §4.

---

### Deferred migrations

`optio_core.deferred_migrations` is a second migration registry, parallel to
`optio_core.migrations.fw_migrations`, for migrations that need task definitions —
most commonly a task's `session_crypto`. `fw_migrations` runs before the initial
sync and gets only the database; `deferred_migrations` runs right after it, inside
`Optio.init`, before the scheduler starts and before any launch/resume is possible,
and start blocks until it finishes.

```python
from optio_core import deferred_migrations, DeferredMigrationContext

@deferred_migrations.register("my_migration", depends_on=["other_migration"])
async def my_migration(ctx: DeferredMigrationContext) -> None:
    task = ctx.task_for(process_id)   # TaskInstance | None, via the executor's
                                       # task registry (same resolution as resurrect)
    ...
```

`DeferredMigrationContext` carries `db`, `prefix` (the app's plain prefix) and
`task_for(process_id) -> TaskInstance | None`. Applied migrations are tracked in
`{prefix}_fw_deferred_migrations`, independently of `fw_migrations`' own tracking
collection. A migration that raises fails engine start, like a failing
`fw_migrations` entry, and is retried on the next start — migrations must be
idempotent per item. Packages register at import time, as with `fw_migrations`
(e.g. optio-claudecode registering its migration in its package `__init__`, so any
app that builds claudecode tasks has it). Spec:
`docs/2026-10-11-conversation-buffer-encryption-design.md` §5.

---

### TaskInstance.read_conversation

```python
@dataclass
class TaskInstance:
    ...
    read_conversation: Callable[[ReadOnlyProcessContext], Awaitable[list | dict | None]] | None = None
```

Optional, last field. Returns the conversation record a suspended process saved, for a
read-only view without resuming it. The engine's conversation archive listener (below)
calls it only for a suspended process (`status.state == "cancelled"`, `supportsResume`,
`hasSavedState`), with a `ReadOnlyProcessContext` (exported from `optio_core`): a view of
the detached `ProcessContext` that `Optio._detached_context` builds (the same one
`Optio.resurrect` uses: own never-set cancellation flag, `resume=False`), exposing only
`process_id`, `_db`, `_prefix` and `load_blob`. Any other attribute (the write methods
`set_widget_data`, `mark_has_saved_state`, `store_blob`, `report_progress`, ...) raises
`AttributeError`, and so does setting one; the read then fails with `read-failed`.
It returns either a list of `[seq, event]` entries (`capturedAt` is then null), or a dict
`{"events": [[seq, event], ...], "capturedAt": <ISO string or datetime, or None>}` (a
naive datetime is taken as UTC). `None`, or a dict whose `events` is `None`, means "no
record". It runs inside the engine, so it may decrypt with the task's own cipher; it
must not write. optio-claudecode sets it when `conversation_ui` is on. Spec:
`docs/2026-10-12-suspended-conversation-view-design.md` §1.

---

### Conversation archive listener

`optio_core.conversation_archive.ConversationArchiveListener` (exported from
`optio_core`): one aiohttp server per engine, serving suspended tasks' conversation
records to the optio-api proxy (`GET /processes/:id/conversation`). Decryption stays in
the engine; the plaintext exists only in memory and in transit.

```python
listener = ConversationArchiveListener(
    db=db, prefix=prefix,
    resolve_task=lambda process_id: ...,        # TaskInstance | None
    make_context=lambda proc_doc: ...,          # ProcessContext
)
await listener.start()   # bind + publish; listener.url, listener.password
await listener.stop()    # unpublish (only its own document) + close; idempotent
```

- **Hook calls:** each read wraps `make_context(proc_doc)` in a `ReadOnlyProcessContext`;
  at most `READ_CONCURRENCY` (4) hooks run at once (each holds a decrypted record), and
  the response body is serialized off the event loop (`asyncio.to_thread`). A hook
  running longer than `READ_TIMEOUT_S` (60 s, counted once it holds a read slot) is
  cancelled and the read answered 500 `read-failed`, logged with the process id(s) and
  "timeout" only.
- **Lifecycle:** `Optio.run()` starts it before the RPC server (resolving tasks through
  the executor's task registry, contexts through `Optio._detached_context`);
  `Optio.shutdown()` stops it right after the RPC server. A failure to start is logged
  and the engine runs on without it (the proxy then answers 503). If a later startup
  step of `run()` raises (`rpc_server.start()`, the scheduler, `_sync_definitions()`),
  `run()`'s `finally` still stops it and deletes its endpoint document.
- **Network:** binds `OPTIO_WIDGET_TUNNEL_BIND` (default `127.0.0.1`) on an ephemeral
  port; advertises `OPTIO_WIDGET_TUNNEL_HOST` (default `127.0.0.1`), as the per-task
  conversation listeners do.
- **Publication:** `{_id: "conversationArchive", url: "http://<host>:<port>/", password,
  updatedAt}` in `{prefix}_engine_endpoints`, replaced on every start. `stop()` deletes
  it only while it still holds this listener's password, so an overlapping newer
  engine's document survives the old engine's shutdown. A crashed engine leaves a stale
  document.
- **Route:** `GET /conversation/{processId}`, `processId` being the process document's
  `_id` (ObjectId hex). Basic auth `optio:<password>` (random
  `secrets.token_urlsafe(32)` per start). Every response carries
  `Cache-Control: no-store`; errors are `{"reason", "message"}`:

  | Status | `reason` | When |
  |---|---|---|
  | 200 | — | `{"events": [[seq, event], ...], "capturedAt": <ISO or null>}` |
  | 401 | `unauthorized` | missing or wrong basic auth |
  | 404 | `not-found` | no process document with that `_id` |
  | 409 | `not-suspended` | not `cancelled` + `supportsResume` + `hasSavedState` |
  | 404 | `no-conversation-support` | no registered task, or its `read_conversation` is `None` |
  | 404 | `no-conversation` | the hook returned `None` |
  | 500 | `read-failed` | the hook raised, returned something unusable or timed out (`READ_TIMEOUT_S`), or anything else failed (e.g. MongoDB) |

  A middleware turns any uncaught exception into the JSON `read-failed` reply (never
  aiohttp's plain-text 500); unknown routes and methods keep aiohttp's own 404 / 405. A
  failed read is logged with the process id(s) and the exception type only, never the
  exception message (it may carry content). Nothing logs the password.
