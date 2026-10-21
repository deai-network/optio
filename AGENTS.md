# Optio — LLM Reference

Async process management library for Python backends with TypeScript API and React UI layers.

---

## Permissions

- **Reads**: You may always read any superpowers-related file without asking for permission.
- **Writes**: You may write design and plan files to `/docs` without asking for permission. During implementation phase, you may write freely within this repo without asking for permission. For anything else, fall back to the default permission rules.

---

## Workflow

**Every feature addition, behavior change, or bug fix MUST go through the relevant
superpowers skill before implementation — brainstorming for new features/changes, debugging
for bugs, TDD for implementation. No exceptions for "simple" tasks.** If you find yourself
thinking "this is too simple to need it," that is the exact moment you must use it. The
user does not need to mention skills explicitly — recognizing when they apply is your job.

Put all specs, plans, and other generated documentation directly under `docs/`. Do not nest
them under subdirectories like `docs/superpowers/specs/` — keep it flat.

Do not add `Co-Authored-By` or any other self-credit lines to git commits.

Do not implement any change — including bug fixes, refactors, or "obvious" improvements — without first describing what you intend to do and getting explicit confirmation from the user.

To release packages to npm / PyPI, follow **`docs/release-cookbook.md`** — the per-package / wire / all flow, dependency order, prerequisites (push `main` first, npm + PyPI auth), and how to register a new package into the release scripts.

**AGENTS.md coordination**: Each package under `packages/` has its own `AGENTS.md` with
package-specific API details. When you change a package's public API, exported symbols,
query parameters, component props, hook signatures, or contract endpoints, you MUST update
that package's `AGENTS.md` to reflect the change in the same commit. Also check whether
the root `AGENTS.md` needs a corresponding update — it contains a unified reference that
must stay consistent with the package-level files.

### Tests must survive CPU starvation — no wall-clock dependence

`make test` runs the whole suite in parallel, and the machine is routinely oversubscribed
(several agent sessions run at once). Any test that assumes code runs within a fixed
real-time window flakes when the CPU is starved. **No test may depend on wall-clock time
for correctness.** This is not optional — a wall-clock-dependent test is a latent mine.

**Never write these:**
- *Sleep-then-assert.* `await asyncio.sleep(N); assert happened` /
  `await new Promise(r => setTimeout(r, N)); expect(count)...`. Under load the awaited work
  has not run yet.
- *Read async-populated state without waiting.* Launch/await, then immediately
  `assert proc["widgetData"] == {...}` / `assert optio.get_published_result(...)` /
  `expect(cap.widgetData)...`. The async write (a mongo field, a published result, a
  captured hook) may not have landed — the read returns `None`/stale and the assert fails.
- *Too-tight positive-wait ceilings.* `asyncio.wait_for(x, 2)`, `for _ in range(200)` polls,
  a fixed `setTimeout(reject, 2000)` gating a WS `open`/`message`.
- *Count-over-wallclock.* Asserting a number of periodic events after a fixed sleep.

**Write instead:**
- Wait for the specific **condition/event**, bounded by a **generous 60s hang-ceiling** —
  the ceiling only bounds a true hang; correctness comes from the awaited condition, not the
  duration. Use the existing poll helpers (`_wait_for` / `_wait_terminal` /
  `_wait_widget_data` in Python; a local `waitUntil(pred, 60000)` in TS). Prefer event-driven
  (`asyncio.Event` raced against the producer task) over polling.
- Poll until the observable value is **present**, *then* assert its contents — insert the
  wait, never weaken the assertion.

**Leave alone (not wall-clock bugs):** poll-*interval* sleeps inside a bounded loop
(`asyncio.sleep(0.02)` as a loop step); sleeps inside fake binaries (`fake_*.py` — they
simulate the real agent's timing, so wait on the fake's *output* instead); product/behavior
timeouts passed into product code (`cancel_grace_seconds`, `ready_timeout_s`, deadlines).
Negative/intermediate-state assertions ("still pending *immediately* after cancel") are
genuinely hard — avoid writing them; if unavoidable, gate on a deterministic checkpoint, not
a time window.

**Verify under load, not once.** A single green run does not prove robustness. Before
claiming a timing-sensitive test is fixed, loop it under CPU contention and require zero
failures — e.g. `hogs=""; for i in $(seq 1 12); do python3 -c "while True: pass" & hogs="$hogs $!"; done;`
run the file 10–20×, then `kill $hogs`.

---

## Integration Levels

| Level | Package | Language | Install |
|-------|---------|----------|---------|
| 1 — Core runtime | `optio-core` | Python | `pip install optio-core` (MongoDB + APScheduler); add `[redis]` for Redis command bus |
| 2 — Remote control | `optio-core[redis]` | Python | `pip install optio-core[redis]` |
| 3 — REST API | `optio-api` | TypeScript | `npm install optio-api optio-contracts` |
| 4 — React UI | `optio-ui` | TypeScript | `npm install optio-ui optio-contracts @tanstack/react-query i18next react-i18next antd @ant-design/icons vultus-core vultus-antd` |
| 1+ — Opencode runner | `optio-opencode` | Python | workspace; runs `opencode web` as an optio task (local subprocess or remote via SSH) |
| 1+ — Claude Code runner | `optio-claudecode` | Python | workspace; runs `claude` as an optio task via ttyd-served iframe (local subprocess or remote via SSH) |
| — Filter language (core) | `filtrum-core` | TypeScript | `npm install filtrum-core` (backend-agnostic filter schema + builders + `Dialect<T>` compiler) |
| — Filter language (Mongo) | `filtrum-mongo` | TypeScript | `npm install filtrum-mongo` (Mongo dialect + `createMongoFilterTranslator`; type-only `mongodb` peer) |

Dependencies: Python requires `motor>=3.3.0`, `apscheduler>=4.0.0a6`, `mongo-quaestor>=0.1,<0.2` (the migration module; import name is still `quaestor` for backwards compatibility — note the upstream `quaestor` PyPI package is unrelated and will collide if installed), `clamator-over-redis>=0.1.9`, `clamator-protocol>=0.1.9`, `pydantic>=2.0`, `aiohttp>=3.9`. Redis support: `redis>=5.0.0` (optional extra). TypeScript API requires `mongodb`, `ioredis`, `@ts-rest/core`. UI requires React 18+, Ant Design 6+ (`antd` and `@ant-design/icons` are peers of `optio-ui`), `@tanstack/react-query` 5+, `i18next` 24+ / `react-i18next` 15+, `vultus-core` >=0.5.4 and `vultus-antd` >=0.7.2 (all peers of `optio-ui`; vultus-core: one copy, one components context, the one vultus-antd uses; vultus-antd: the task factory launch form's widgets).

---

## Debug tools

### MongoDB CLI

`mongosh` and `mongodump` may not be installed on the host. Do NOT search for
them or fail back to alternative install paths — use the tools shipped inside
the running MongoDB Docker container instead. Container name varies by
docker-compose project (e.g. `optio-demo-mongodb-1` from
`packages/optio-demo/docker-compose.yml`, or `excavator-mongodb-1` when
running against the excavator stack); resolve via `docker ps --format
'{{.Names}}' | grep mongo`.

```bash
# One-off query:
docker exec <mongo-container> mongosh --quiet <db> \
  --eval 'db.<collection>.findOne({...})'

# Interactive shell:
docker exec -it <mongo-container> mongosh <db>

# Dump:
docker exec <mongo-container> mongodump --db <db> --out /data/dump
docker cp <mongo-container>:/data/dump /tmp/dump
```

If `docker ps` does not show a mongo container, bring it up via the relevant
package's `docker-compose.yml` (`packages/optio-demo` for development).

### UI brainstorming

Render real `optio-ui` components against fixtures (visual variants, layout
explorations, etc.):

```bash
pnpm --filter optio-dashboard dev:playground
```

Then open http://localhost:5174/. Add new topics under
`packages/optio-dashboard/playground/topics/<name>/`; see the playground
`README.md` for the contract. Use this when comparing visual variants of an
`optio-ui` component before committing to a design.

---

## Python: optio-core

### Public API

All symbols are available directly from `optio_core` (module-level singleton):

```python
import optio_core

# Lifecycle
await optio_core.init(mongo_db, prefix="optio", redis_url=None, rpc_server=None, services=None, get_task_definitions=None,
                      cancel_grace_seconds=5.0, auto_resume_delay_seconds=300.0,
                      cancel_wait_ceiling_margin_seconds=25.0, shutdown_drain_margin_seconds=5.0,
                      terminal_poll_interval_seconds=0.1, force_cancel_shield_seconds=2.0)
await optio_core.run()          # blocks until shutdown; call after init()
await optio_core.shutdown(grace_seconds: float | None = None)  # graceful shutdown; None = init's cancel_grace_seconds; force-finalizes tasks that do not unwind in time

# Commands. session_id is keyword-only and required (None is valid): the session that started
# the run (optio-ui sends its browser session's id), stored as originatingSessionId, read as ctx.session_id.
await optio_core.launch(process_id: str, resume: bool = False, *, session_id: str | None, params: dict | None = None) -> LaunchOutcome  # fire-and-forget; ok=False carries a reason (not-found, not-launchable, no-resume-support, launch-blocked, shutting-down, parameters-required, not-a-factory, invalid-parameters)
await optio_core.launch_and_wait(process_id: str, resume: bool = False, *, session_id: str | None, params: dict | None = None) -> None  # blocks until done; raises LaunchBlocked (a TaskFactory's refused launch: LaunchError, but LaunchBlocked for a launch block)
await optio_core.launch_and_await_result(process_id: str, resume: bool = False, *, session_id: str | None, timeout: float | None = None, params: dict | None = None) -> Any
# Launches like launch() and returns the object the task passes to ctx.publish_result(obj) while it keeps
# running. Raises LaunchError(reason, message) on a refused launch, ResultNotPublished when the task ends without
# publishing, asyncio.TimeoutError on timeout. optio_core.get_published_result(process_id) -> Any | None
# reads a running process's published object.
# params: a TaskFactory's launch values (see "TaskFactory Fields"). A factory's record launched with them
# defines an ad-hoc child of the record with the values in ctx.params and launches it with the same
# session_id; the outcome's proc is the child's record. Refusals, in this order: shutting-down, not-found,
# no-resume-support (resume=True), parameters-required (no params), not-launchable (no synced definition),
# invalid-parameters (LaunchOutcome.message "<id>: <what>": the fields' checks, then the factory's
# validate), launch-blocked (the child's metadata). params on any other record: not-a-factory. No child is
# defined while shutting down (all three entry points). A factory's record never runs itself.
await optio_core.resurrect(process_id: str, *, session_id: str | None) -> ResurrectOutcome  # fire-and-forget
# Saves the work a failed run left on its host, then resumes. Refusals come back as
# ResurrectOutcome(ok=False, reason=...), checked in this order: shutting-down, not-found,
# no-resurrect-support (no TaskInstance.resurrect hook), resurrect-in-progress,
# not-resurrectable (state not launchable, or hasUnsavedWork false), launch-blocked.
# On ok=True a background task sets the process's resurrecting marker and runs the hook
# (ctx.resume=False) under progress "Resurrecting: saving the unsaved work…". Hook returns:
# hasUnsavedWork cleared, then launch(resume=True, same session_id); a refused launch is
# logged. Hook raises NothingToResurrect: flag cleared, reason logged, no resume. Any other
# exception: logged, flag kept (retry possible); an error escaping the background run
# (e.g. a Mongo write) is logged by its done-callback. Progress and resurrecting are cleared in
# every case, before the resume launch; init() clears a marker left by a dead engine. While
# the hook runs, launch returns not-launchable and a second resurrect
# resurrect-in-progress. shutdown() cancels a resurrect in progress and waits at most
# force_cancel_shield_seconds for it (a hook ignoring the cancel is abandoned); the flag
# stays set. Engine RPC: resurrect({ processId, sessionId }) -> { ok: true, process } |
# { ok: false, reason } (OptioEngineService.resurrect).
# The post-restart auto-resume sweep never resumes a process with hasUnsavedWork (the resume
# would wipe the work): with supportsResurrect and a registered hook it calls resurrect
# (session_id=None) instead; otherwise it skips it and logs "Auto-resume skipped <processId>:
# unsaved work on the host (use Resurrect)". The autoResumeScheduled stamp is cleared either way.
await optio_core.cancel(process_id: str) -> CancelOutcome
# Cancels the named process. Recursively cancels active direct descendants whose
# TaskInstance has auto_cancel_children=True (default). Opt-out parents handle
# their own children's shutdown. Force-cancel after grace always cascades through
# the live subtree regardless of the flag. The cancel grace deadline is shared
# across the subtree — descendants inherit the root cancel's monotonic deadline.
await optio_core.group_cancel(metadata_filter: dict, block_new_launches: bool = False, *, persist: bool = False, reason: str | None = None, purge_records: bool = False) -> int             # fire-and-forget; cancels every active process matching the (non-empty) filter, returns how many (persist=True requires block_new_launches=True; purge_records=True also deletes the matching records and their descendants)
await optio_core.group_cancel_and_wait(metadata_filter: dict, block_new_launches: bool = False, *, persist: bool = False, reason: str | None = None, purge_records: bool = False) -> int    # blocks until every matching process is terminal (persist=True requires block_new_launches=True)
async with optio_core.block_launches(launch_filter: dict, *, persist: bool = False, reason: str | None = None): ...   # async CM; rejects matching launches with LaunchBlocked. persist=True writes a Mongo record and the block survives the CM
await optio_core.unblock_launches(launch_filter: dict) -> int                                              # remove every persistent record + in-memory block whose filter equals launch_filter exactly; returns count of in-memory entries removed
await optio_core.dismiss(process_id: str) -> DismissOutcome          # reset done/failed/cancelled → idle
await optio_core.resync(clean: bool = False, metadata_filter: dict | None = None) -> dict[str, str]  # re-sync task definitions (scoped to metadata_filter if given); clean=True deletes the (in-scope) records first; returns {processId: oid_hex}

# Ad-hoc processes (not from get_task_definitions). A child's record carries the task's cancellable,
# description, special, warning and ui_widget, and the parent's ttlSeconds.
await optio_core.adhoc_define(task: TaskInstance, parent_id: ObjectId | None = None, ephemeral: bool = False) -> dict
await optio_core.adhoc_delete(process_id: str) -> None

# Queries
await optio_core.get_process(process_id: str) -> dict | None
await optio_core.list_processes(state=None, root_id=None, metadata=None) -> list[dict]
await optio_core.count_processes(*, states=None, metadata=None, roots_only=False) -> int
# Counts without loading documents. states: any of these. metadata: AND of
# metadata.{key} matches; a list value matches any of its items. roots_only:
# top-level processes only (children inherit their parent's metadata).

# RPC server (set after init(); None if no Redis configured).
# Register custom clamator services on it before run() for app-specific verbs.
optio_core.rpc_server: RpcServerCore | None

# The (db, prefix) binding init() was called with; raises before init().
optio_core.mongo_store: MongoStore
```

**`init()` parameters:**

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `mongo_db` | `AsyncIOMotorDatabase` | required | Motor async database object |
| `prefix` | `str` | `"optio"` | Namespace for collections (`{prefix}_processes`) and clamator service streams (the `{database}/{prefix}` Redis key prefix) |
| `redis_url` | `str \| None` | `None` | If None: command bus disabled; use direct method calls |
| `rpc_server` | `RpcServerCore \| None` | `None` | Pre-built clamator RPC server. Mutually exclusive with `redis_url`. When supplied, optio-core registers `OptioEngineService` on it but does not own its lifecycle. |
| `services` | `dict[str, Any] \| None` | `{}` | Passed as `ctx.services` to all task execute functions |
| `get_task_definitions` | `Callable[..., Awaitable[list[TaskInstance \| TaskFactory]]] \| None` | `None` | Async function `(services, metadata_filter) -> list[TaskInstance \| TaskFactory]`; called on init and resync. `metadata_filter` is `None` for a full sync. |
| `cancel_grace_seconds` | `float` | `5.0` | Cooperative-cancel deadline; after it the supervisor force-cancels and writes `failed`. Also the default `shutdown()` grace |
| `auto_resume_delay_seconds` | `float` | `300.0` | Post-boot delay before the auto-resume sweep re-launches stamped processes |
| `cancel_wait_ceiling_margin_seconds` | `float` | `25.0` | Budget on top of the grace before `cancel_and_wait` / `group_cancel_and_wait` give up with `TimeoutError` |
| `shutdown_drain_margin_seconds` | `float` | `5.0` | Extra time `shutdown()` waits for tasks to drain beyond the grace |
| `terminal_poll_interval_seconds` | `float` | `0.1` | Poll step while waiting for terminal states |
| `force_cancel_shield_seconds` | `float` | `2.0` | How long a force-cancel (and shutdown, for a resurrect in progress) waits for the task to unwind |

**`optio_core.rpc_server`** (`RpcServerCore | None`): The clamator `RpcServerCore`
constructed (a `RedisRpcServer` when `redis_url` is provided, or whatever the caller
supplied via `init(rpc_server=...)`). Apps register extra services here before
`optio_core.run()`. `None` if no Redis is configured.

---

### TaskInstance Fields

```python
@dataclass
class TaskInstance:
    execute: Callable[..., Awaitable[None]]  # async def execute(ctx: ProcessContext) -> None
    process_id: str
    name: str
    description: str | None = None           # optional description, shown as tooltip in UI
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None              # cron expression, e.g. "0 3 * * *"
    special: bool = False                    # hidden from default UI views when special=True
    supports_resume: bool = False            # opt-in: enable resume/checkpoint support
    resurrect: Callable[[ProcessContext], Awaitable[None]] | None = None  # saves a failed run's host work (snapshot + mark_has_saved_state) and removes host leftovers; raises NothingToResurrect when nothing is left; requires supports_resume=True (ValueError at sync)
    warning: str | None = None              # shown as confirmation prompt before launch
    cancellable: bool = True                 # whether this process can be cancelled
    ui_widget: str | None = None             # widget name registered via registerWidget() in optio-ui
    ttl_seconds: int | None = None           # record expires this long after reaching a terminal state (expireAt); children copy it
    auto_cancel_children: bool = True        # cancel(this) recursively cancels active direct descendants; set False for tasks that finalize children themselves
    auto_resume: bool = False                # a top-level process interrupted by engine shutdown that saved its state is re-launched (resume=True) after the next start, after auto_resume_delay_seconds; requires supports_resume=True (ValueError at sync)
    schedule_offset_seconds: int | None = None  # second (0-59) within each matching minute at which `schedule` fires; None = second 0
    session_crypto: SessionCrypto | None = None  # the task's at-rest cipher (encrypt/decrypt callables) for its own blob channels; set by engine-wrapper factories, read e.g. by deferred migrations
    read_conversation: Callable[[ReadOnlyProcessContext], Awaitable[list | dict | None]] | None = None  # last field; returns a suspended process's saved conversation record ([[seq, event], ...] or {events, capturedAt}; None = no record) for the engine's conversation archive listener; gets a read-only context (process_id, _db, _prefix, load_blob)
```

The engine's conversation archive listener (`optio_core.ConversationArchiveListener`, started by
`run()`, stopped by `shutdown()`) serves `read_conversation` over HTTP for the optio-api proxy:
`GET /conversation/{_id}` behind basic auth, address published as `conversationArchive` in
`{prefix}_engine_endpoints`. Details: `packages/optio-core/AGENTS.md` "Conversation archive listener".

`NothingToResurrect` and `ResurrectOutcome` are exported from `optio_core`. The only task
that sets `resurrect` today is optio-claudecode's (`create_claudecode_task`, when
`supports_resume`); its hook is described under "Resurrect" in `packages/optio-claudecode/AGENTS.md`.

---

### TaskFactory Fields

A task launched with values. `get_task_definitions` may return `TaskFactory`s among its
`TaskInstance`s (exported from `optio_core`). Spec: `docs/2026-10-20-task-factory-design.md`.

```python
@dataclass
class TaskFactory:
    execute: Callable[..., Awaitable[None]]  # a launch's task; reads ctx.params
    process_id: str
    name: str
    parameters: list[dict[str, Any]]         # vultus-core field definitions as plain JSON (below)
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    special: bool = False
    warning: str | None = None               # shown before the launch form, as a task's
    cancellable: bool = True                 # a launch's
    ui_widget: str | None = None             # a launch's widget (the factory's record has none)
    ttl_seconds: int | None = None           # None: a launch is ephemeral (deleted when it ends); set: kept that long after it ends
    auto_cancel_children: bool = True
    validate: Callable[[dict[str, Any]], str | None] | None = None  # after the fields' checks: a returned message ("<id>: <what>") refuses the values
    name_for: Callable[[dict[str, Any]], str] | None = None        # a launch's name; default `name`
```

- **The record:** synced like a task's, plus `taskFactory: true` and `parameters` (both `$set` on
  every sync); `params` `{}`, `uiWidget` null, `supportsResume` false. It never runs: it has no
  schedule, resume, resurrect or auto-resume; `launch` without `params` refuses it
  (`parameters-required`), the scheduler never gets it, the auto-resume sweep's `resume=True`
  launch is refused (`no-resume-support`), `resurrect` answers `no-resurrect-support` (factories
  stay out of the executor's task registry), and `Executor.launch_process` skips a `taskFactory`
  record. A TaskInstance's record has neither field (unset on sync). A resync that removes the
  factory cancels its running launches (the stale records' active descendants are cancelled with
  them).
- **The parameters** (checked at sync; a bad one raises `ValueError("Task factory '<id>':
  parameters: <param id>: <what>")`; `optio_core.task_factory.check_parameters`): `kind` one of
  `boolean`, `oneOf`, `manyOf`, `numeric`, `range`, `text`; `id` (unique, an identifier:
  `^[A-Za-z_][A-Za-z0-9_]*$`); `label`; the kind's options (`choices: [{value: str, label?}]`,
  non-empty, for `oneOf`/`manyOf`; `min`, `max`, `step` and `integer` (bool) for
  `numeric`/`range`, a range needing `min` and `max`; `maxLength` for `text`); `initialValue`
  (checked like a value, may be empty); `required` (default true). Any other key is refused. A
  `label` (a field's, a choice's) is a string or a map of language code to string. A null key
  counts as absent.
- **A launch's values** (`optio_core.task_factory.check_values`, before the child exists): every
  key a parameter's id; every required parameter has a value (null, and for `text`/`manyOf` the
  empty string/list, is none); `boolean` a bool, `oneOf` one of its choices, `manyOf` a list of
  distinct choices, `numeric` a number within `min`/`max` (a whole one with `integer`), `range` a
  `[from, to]` list within them with `from <= to` (whole ends with `integer`), `text` a string
  within `maxLength`; nothing coerced (a bool is no number). Then `validate`. The first problem
  refuses: `invalid-parameters`, message `"<id>: <what>"`. Null values are then dropped: the task's
  `ctx.params`, `validate` and `name_for` see a left-out value and a null one alike.
- **The launch's child:** an ad-hoc child of the factory's record (`adhoc_define(child,
  parent_id=<record _id>, ephemeral=ttl_seconds is None)`), process id `<factory id>:<8 hex
  digits>`, name `name_for(values)` or `name`, `params` the values, metadata the factory's plus
  `taskFactory: <factory id>`, the factory's other fields, `ttlSeconds` copied from the record;
  launched with the launch's `session_id`. If that launch is refused (a launch block or a shutdown
  since), the child is deleted. Its TaskInstance lives only in memory (as any ad-hoc process's).

---

### ChildResult Fields

```python
@dataclass
class ChildResult:
    process_id: str
    state: str        # "done" | "failed" | "cancelled"
    error: str | None = None
    name: str = ""                                       # child's logical name (same string passed to spawn)
    original_exception: BaseException | None = None      # exception raised inside child's execute, if any
```

---

### ChildOutcome Fields

```python
@dataclass
class ChildOutcome:
    state: str                                           # "done" | "failed" | "cancelled"
    original_exception: BaseException | None = None      # populated on "failed" only
```

`ProcessContext.run_child` returns `ChildOutcome`. Use `.state` for the string state; on `survive_failure=True` callers, `.original_exception` carries the live exception object raised inside the child.

---

### ChildProcessFailed (optio_core.exceptions)

```python
from optio_core.exceptions import ChildProcessFailed

class ChildProcessFailed(Exception):
    name: str
    process_id: str
    original: BaseException     # __cause__ is also set to this
```

Raised by `ctx.run_child` when a child fails and `survive_failure=False`. For `parallel_group`, aggregate failure raises `ExceptionGroup("Parallel group failed", [ChildProcessFailed, ...])`; callers use `except* ChildProcessFailed`.

For cancelled children inside a parallel-group `ExceptionGroup`, `.original` is a synthetic `RuntimeError("child cancelled")` so it is never `None`.

---

### ChildProgressInfo Fields

```python
@dataclass
class ChildProgressInfo:
    process_id: str
    name: str
    state: str             # "scheduled" | "running" | "done" | "failed" | "cancelled"
    percent: float | None = None
    message: str | None = None
```

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

Passed to `ctx.set_widget_upstream()` to inject credentials into proxied widget requests.

---

### ProcessContext Methods

`ProcessContext` is the sole argument to every task execute function. Signature: `async def execute(ctx: ProcessContext) -> None`.

```python
# Progress reporting
ctx.report_progress(percent: float | None, message: str | None = None, *, level: Literal["info", "warning"] = "info") -> None
# percent=None → indeterminate; buffered and flushed every 100ms (OPTIO_PROGRESS_FLUSH_INTERVAL_MS env)
# message is also appended to process log, at `level`; warning lines are never dropped by burst coalescing

ctx.should_continue() -> bool
# Returns False when cancellation has been requested for THIS process —
# external on self/ancestor, or cancel cascade from a non-surviving
# descendant. Does NOT become False because a child of this process
# failed. Poll this in loops; also safe to read inside `except
# ExceptionGroup` / `except ChildProcessFailed` handlers to distinguish
# "I was cancelled" (False) from "a child failed" (True).

await ctx.mark_ephemeral() -> None
# Mark this process for deletion after it completes

# Sequential child process
await ctx.run_child(
    execute: Callable[..., Awaitable[None]],
    process_id: str,
    name: str,
    params: dict[str, Any] | None = None,
    survive_failure: bool = False,
    survive_cancel: bool = False,
    on_child_progress: Callable[[list[ChildProgressInfo]], None] | None = None,
    description: str | None = None,
) -> ChildOutcome
# Returns ChildOutcome; .state is the child's final state: "done" | "failed" | "cancelled"
# Blocks until child completes

# Parallel child processes
ctx.parallel_group(
    max_concurrency: int = 10,
    survive_failure: bool = False,
    survive_cancel: bool = False,
    on_child_progress: Callable[[list[ChildProgressInfo]], None] | None = None,
) -> ParallelGroup
# Use as async context manager; raises ExceptionGroup("Parallel group failed", [ChildProcessFailed, ...]) on exit if any child fails (unless survive_failure=True) or is cancelled (unless survive_cancel=True)

# Public fields (read-only use)
ctx.process_id: str
ctx.params: dict[str, Any]
ctx.metadata: dict[str, Any]
ctx.services: dict[str, Any]
ctx.resume: bool  # True when this execution was triggered with resume=True
ctx.session_id: str | None  # the launch's session_id (children inherit the parent's)

# Resume / checkpoint support
await ctx.mark_has_saved_state() -> None
# Set hasSavedState=True on the process doc. Idempotent.
# Warn-and-noop when task's supports_resume=False.

await ctx.clear_has_saved_state() -> None
# Set hasSavedState=False. Idempotent. Warn-and-noop when supports_resume=False.

await ctx.mark_unsaved_work() -> None
# Set hasUnsavedWork=True: the host workdir holds work newer than the last snapshot.
# Idempotent. Warn-and-noop when the task has no resurrect hook (supportsResurrect=False).

await ctx.clear_unsaved_work() -> None
# Set hasUnsavedWork=False (a save completed). Idempotent; silent when already clear.

# GridFS blob helpers (blobs tagged with metadata {processId, prefix, name})
# file_id pre-selects the blob's id (so it can be recorded before writing); default: GridFS generates one.
async with ctx.store_blob(name: str, file_id: ObjectId | None = None) as stream:
    stream.file_id  # assigned GridFS file_id
    await stream.write(data: bytes)

async with ctx.load_blob(file_id) as stream:
    data = await stream.read()

await ctx.delete_blob(file_id) -> None
# No-op if file_id does not exist.

# Widget proxy control
await ctx.set_widget_upstream(url: str, inner_auth: InnerAuth | None = None) -> None
# Registers upstream URL for /api/widget/:database/:prefix/:processId/* proxy. Cleared automatically on terminal state.

await ctx.clear_widget_upstream() -> None
# Proxy returns 404 for this process until set again.

await ctx.set_widget_data(data) -> None
# Overwrites widgetData (any JSON-serializable value); delivered to widget via tree SSE stream.

await ctx.set_widget_todos(items) -> None
# Sets widgetData.todos and leaves sibling keys. No-op when widgetData is
# missing, null, or not an object.

await ctx.clear_widget_data() -> None
# Sets widgetData to null.
```

**ParallelGroup usage:**

```python
async with ctx.parallel_group(max_concurrency=5) as group:
    for item in items:
        await group.spawn(execute_fn, process_id=item.id, name=item.name, description=item.desc, params={...})
# group.results: list[ChildResult] after exit
```

---

### Progress Helpers

```python
from optio_core.progress_helpers import sequential_progress, average_progress, mapped_progress

# Returns on_child_progress callback — pass to run_child() or parallel_group()

sequential_progress(ctx: ProcessContext, total_children: int)
# Maps N sequential children to equal slots of 100/N % each

average_progress(ctx: ProcessContext)
# Averages all children's percent; done/failed/cancelled → 100%

mapped_progress(ctx: ProcessContext, range_start: float, range_end: float)
# Maps last child's 0-100% into parent range_start..range_end (fractions 0.0–1.0)
# e.g. mapped_progress(ctx, 0.0, 0.25) maps child into 0–25% of parent
```

---

### Process States

All states:

| State | Group | Description |
|-------|-------|-------------|
| `idle` | LAUNCHABLE | Initial state; ready to launch |
| `scheduled` | ACTIVE, CANCELLABLE | Queued by scheduler or launch command |
| `running` | ACTIVE, CANCELLABLE | Execute function is running |
| `cancel_requested` | ACTIVE | Cancel requested, waiting for executor to acknowledge |
| `cancelling` | ACTIVE | Executor acknowledged; cleaning up |
| `done` | END, LAUNCHABLE, DISMISSABLE | Completed successfully |
| `failed` | END, LAUNCHABLE, DISMISSABLE | Execute function raised an exception |
| `cancelled` | END, LAUNCHABLE, DISMISSABLE | Cancelled successfully |

**State groups:**

```python
ACTIVE_STATES     = {"scheduled", "running", "cancel_requested", "cancelling"}
END_STATES        = {"done", "failed", "cancelled"}
LAUNCHABLE_STATES = {"idle", "done", "failed", "cancelled"}
CANCELLABLE_STATES = {"scheduled", "running"}
DISMISSABLE_STATES = {"done", "failed", "cancelled"}
```

**Transition table:**

| From | To (valid) |
|------|-----------|
| `idle` | `scheduled` |
| `scheduled` | `running`, `cancel_requested` |
| `running` | `done`, `failed`, `cancel_requested` |
| `done` | `scheduled`, `idle` |
| `failed` | `scheduled`, `idle` |
| `cancel_requested` | `cancelling` |
| `cancelling` | `cancelled` |
| `cancelled` | `scheduled`, `idle` |

**Cancel propagation:**

`cancel(p)` recursively cancels active direct descendants of `p` when `p`'s TaskInstance has `auto_cancel_children=True` (default). Setting it to False makes `p` responsible for cleaning up its own subtree within the cancel grace budget. Force-cancel (the post-grace path) always cascades through the live subtree regardless of the flag.

Upward propagation (alpha) dispatches by breach reason:

- **Failure breach** (child failed with `survive_failure=False`): invokes `notify_parent_failure(parent)` → `Optio._cancel_active_children(parent)`. Cancels only the parent's other active direct children (sibling-only descent). Parent's `cancellation_flag` and row state are NOT touched; the failure is signalled to user code via `ChildProcessFailed` / `ExceptionGroup[ChildProcessFailed]`.
- **Cancellation breach** (child cancelled with `survive_cancel=False`): invokes `notify_parent_abnormal(parent)` → `Optio.cancel(parent)`. Sets parent's flag, transitions parent's row through `cancel_requested`/`cancelling`, recurses to direct children. Cancellation cascades upward through the tree.

For `parallel_group`, `ParallelGroup` records `_breach_reason: Literal["failure", "cancel", None]` per child outcome (failure dominates over cancel). `__aexit__` dispatches to the correct callback based on the final reason and always raises `ExceptionGroup[ChildProcessFailed]`. Per-spawn `run_child` inside groups hardcodes `survive_*=True`, so the executor-level alpha path does not fire for individual group children.

Terminal-state rule: a parent whose own child *failed* never ends as `cancelled`. The `cancelled` terminal is reserved for actual cancellation — external on self/ancestor, or cancel-cascade from a non-surviving descendant.

The cancel grace deadline is shared across the subtree: descendants inherit the root cancel's monotonic deadline (via an internal `inherit_deadline` kwarg on `lifecycle.cancel`) rather than starting a fresh clock at each level.

While a process is being cancelled (its cancellation flag is set), `ctx.run_child` and `ParallelGroup.spawn` refuse to spawn new children unless the parent has `auto_cancel_children=False` (it owns shutdown timing).

---

### MongoDB Document Schema

Collection: `{prefix}_processes`

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | MongoDB document ID |
| `processId` | string | Application-defined unique identifier |
| `name` | string | Human-readable display name |
| `description` | string \| null | Optional description text |
| `params` | object | Static parameters from TaskInstance; a TaskFactory launch's values |
| `metadata` | object | Arbitrary metadata; fields can be filtered via `list_processes(metadata=...)` |
| `parentId` | ObjectId \| null | Parent process `_id`; null for root processes |
| `rootId` | ObjectId | Root process `_id`; equals `_id` for root processes |
| `depth` | int | Tree depth; 0 for root |
| `order` | int | Sort order among siblings |
| `cancellable` | bool | Whether cancel is permitted |
| `adhoc` | bool | True if created via `adhoc_define()` |
| `ephemeral` | bool | The record is deleted (with its descendants) when its run ends: any terminal state, a force-cancel, a start that finds no execute function, or an engine restart that interrupted it |
| `special` | bool | Marks administrative/special-purpose processes |
| `warning` | string \| null | Warning text shown before launch |
| `status` | object | See ProcessStatus sub-document |
| `status.state` | string | Current process state |
| `status.error` | string \| null | Error message (failed state) |
| `status.runningSince` | datetime \| null | When execution started |
| `status.doneAt` | datetime \| null | When process completed successfully |
| `status.duration` | float \| null | Execution duration in seconds |
| `status.failedAt` | datetime \| null | When process failed |
| `status.stoppedAt` | datetime \| null | When process was cancelled |
| `progress` | object | See Progress sub-document |
| `progress.percent` | float \| null | 0–100, or null for indeterminate |
| `progress.message` | string \| null | Current progress message |
| `log` | array | Log entries; see LogEntry sub-document |
| `log[].timestamp` | ISO datetime string | Entry timestamp |
| `log[].level` | string | `event` \| `info` \| `debug` \| `warning` \| `error` |
| `log[].message` | string | Log message |
| `log[].data` | object \| absent | Optional structured data |
| `uiWidget` | string \| null | Widget name; `ProcessDetailView` dispatches on this field |
| `widgetUpstream` | `{ url, innerAuth } \| null` | Server-side only — never sent to clients |
| `widgetData` | any JSON \| null | Live data delivered to the widget component via tree stream |
| `supportsResume` | bool | Task opted into resume support; refreshed via `$set` on every sync |
| `hasSavedState` | bool | Task has a valid checkpoint; `$setOnInsert: false`; mutated only by `mark/clear_has_saved_state` |
| `supportsResurrect` | bool | Task has a `resurrect` hook; refreshed via `$set` on every sync; absent reads as false |
| `hasUnsavedWork` | bool | Host workdir holds work newer than the last snapshot; `$setOnInsert: false`; set by `mark/clear_unsaved_work`, cleared by optio-core when a launch starts and when a resurrect saves the work or finds nothing; absent reads as false |
| `resurrecting` | bool | Transient: true while a resurrect's background run is in progress; set and cleared by optio-core only; `init()` clears any marker a dead engine left behind; absent reads as false |
| `autoResumeScheduled` | bool | Stamp set at shutdown on a cancelled, state-saved top-level `auto_resume` process; cleared on resume, manual launch and any transition to failed |
| `launchMode` | `"resume"` \| `"fresh"` \| absent | How the latest run started (a resume without saved state is fresh) |
| `originatingSessionId` | string \| null | The `session_id` of the launch; children inherit the parent's |
| `controlUpstream` | `{ url, innerAuth } \| null` | Server-side only — never sent to clients. The session's in-process input listener, read by optio-api's widget-control route (set/cleared by `ctx.set_control_upstream` / `clear_control_upstream`) |
| `browserOpenRequests` | array | `{requestId, url, createdAt}` records pushed by `ctx.request_browser_open`; append-only |
| `sessionEvents` | array | `{requestId, type: "attention", reason}` / `{requestId, type: "client", keyword, data}` records pushed by `ctx.need_attention` / `ctx.client_message`; append-only |
| `ttlSeconds` | int \| null | From `TaskInstance.ttl_seconds` (or `TaskFactory.ttl_seconds`); a child copies its parent's |
| `taskFactory` | bool \| absent | True on a `TaskFactory`'s record (`$set` on every sync; unset on a TaskInstance's); such a record never runs |
| `parameters` | array \| absent | A `TaskFactory`'s field definitions, as plain JSON (`$set` on every sync; unset on a TaskInstance's) |
| `expireAt` | datetime \| absent | Set at a terminal state to now + `ttlSeconds`; removed by relaunch and dismiss; the `expireAt_ttl` index evicts the record after it |
| `createdAt` | datetime | Document creation timestamp |

---

## Python: optio-opencode

Run `opencode web` as an optio task.  Public API:

```python
from optio_opencode import create_opencode_task, OpencodeTaskConfig, SSHConfig

task = create_opencode_task(
    process_id="my-task", name="My task",
    config=OpencodeTaskConfig(
        consumer_instructions="...",
        opencode_config={...},     # passthrough to opencode.json
        ssh=None,                  # None = local subprocess
        on_deliverable=cb,
        workdir_exclude=None,      # None = DEFAULT_WORKDIR_EXCLUDES; [] = capture all
    ),
)
```

The returned `TaskInstance` has `ui_widget="iframe"` and `supports_resume=True` baked in.

**`OpencodeTaskConfig.workdir_exclude`** — controls snapshot exclusions:
`None` → use `archive.DEFAULT_WORKDIR_EXCLUDES` (`.git`, `node_modules`, `__pycache__`, `.venv`, `*.pyc`, `.DS_Store`).
`[]` → capture everything. Non-empty list → verbatim (no merge with defaults).

**Snapshot collection** — `{prefix}_opencode_session_snapshots`. Key fields:
`processId`, `capturedAt`, `endState`, `sessionId`, `sessionBlobId`, `workdirBlobId`, `deliverablesEmitted`.
Compound index `{processId: 1, capturedAt: -1}`. Retention: keep latest 5; older snapshots' blobs deleted with the doc.

**Host method additions** (both `LocalHost` and `RemoteHost`):
`launch_opencode(..., env=None)`, `opencode_import(db_path, session_json)`, `opencode_export(db_path, session_id) -> bytes`,
`archive_workdir(exclude, *, on_note=None) -> AsyncIterator[bytes]`, `restore_workdir(stream)`, `remove_file(path)`.
`archive_workdir` streams `tar | pigz -1` (`gzip -1` without pigz) on both hosts: `RemoteHost` over SSH,
`LocalHost` as a local subprocess group that is killed if the capture is abandoned. Only a local host without
`tar` or `bash` falls back to in-process `tarfile` (level 1). See `docs/2026-10-03-local-archive-throughput-design.md`.
Above the workdir capture cap (`OPTIO_WORKDIR_CAPTURE_MAX_MB`, default 100 MiB, `0` = off) it leaves out the paths
listed in the workdir's `regenerable.txt`; the one-line note goes to `on_note(note, level)` (`level` is `"info"` or
`"warning"`; the engines forward it with `ctx.report_progress(None, note, level=level)`) and the `optio_host.archive` logger.
See the "Workdir capture cap" section of `packages/optio-host/AGENTS.md`.

Full details: `packages/optio-opencode/AGENTS.md`.

---

## Python: engine wrappers — shared config mixins

All 7 engine `TaskConfig` dataclasses (claudecode, opencode, codex, cursor,
grok, kimicode, antigravity) inherit their common field sets from mixins in
`optio_agents.config_types` — fields stay top-level on each config:

- **`ClaustrumConfigMixin`** — the claustrum filesystem-isolation triad
  (`fs_isolation`, `extra_allowed_dirs`, `delivery_type`).
- **`BlobCryptoConfigMixin`** — at-rest crypto for the two GridFS blob
  channels, uniform across all 7 engines: `session_blob_encrypt`/`decrypt`
  wrap this process's session snapshot (ds-scoped key);
  `seed_blob_encrypt`/`decrypt` wrap the shared pool-account SEED tar
  (pool-scoped key), falling back to the session pair when unset. Setting one
  member of a pair without the other raises `ValueError` at construction.
- **`SessionControlsConfigMixin`** — the conversation widget's controls bar:
  `show_session_controls`, plus the optional `session_controls` allowlist of
  control ids (None: every control the engine builds; setting it without the
  bar raises `ValueError`). Engines narrow every controls snapshot with
  `optio_agents.session_controls.filter_controls` and their `/control` refuses
  an id outside `settable_controls` (403 `not-allowed`; every id while the bar
  is off). opencode builds its controls in the browser, so its widget applies
  the allowlist there and it has no server-side gate.
- **`WelcomeConfigMixin`** — `welcome: WelcomeCard | None`, the card the
  conversation widget shows atop the transcript. `WelcomeCard(title,
  description=None, icon=None, min_height=None)` (exported from
  `optio_agents`): `title` (not blank) and `description` (markdown) are
  `optio_agents.Text` — a plain string or a map of language code to string
  (`{"en": ..., "hu": ...}`), checked with `check_text`; `icon` is a URL
  relative to the web app root (`"img/a.png"` → `"/img/a.png"`; `"/x.png"`
  and `https://...` stay); `min_height` is a positive int of CSS pixels.
  Every engine publishes `welcome_widget_data`
  (`{title, description, icon, minHeight}` with the texts unresolved, or
  None) as the conversation widget's `welcome` widgetData key; the widget
  resolves a map for the viewer's language (`resolve_text`: the language's
  entry, else `"en"`, else the first). Anything but a `WelcomeCard` or None
  raises `ValueError`.
- **`CustomControlsConfigMixin`** (`optio_agents.custom_controls`) —
  `custom_controls`: task-defined selectors (`CustomControl` with
  `CustomOption`s, each setting a fixed `ControlSettings` combination of
  model / reasoning effort / permission mode; labels a string or a language
  map read by `resolve_text`), independent of the controls bar. Only
  claudecode accepts a non-empty list so far
  (`optio_agents_all.supports_custom_controls`); the others raise `ValueError`.
- **`ResumeConfigMixin`** — `smooth_resume` (default False): this task should
  be resumed without confirmation when a message arrives. A send to the
  suspended session resumes it directly; a send that discards a failed run's
  unsaved work still asks. Every engine accepts it; only claudecode's
  suspended replay acts on it so far (`widgetData.smoothResume`).

`optio-agents-all` guards this uniformity with a union-wide test; its
`create_task` passes configs through verbatim. Details:
`packages/optio-agents/AGENTS.md`, `packages/optio-agents-all/AGENTS.md`.

---

## Python: engine wrappers — conversation steering

`optio_agents.steering` is the shared scaffolding for sending while the agent
works: each wrapper declares `busy_send` (`joins-next-step` | `queues-to-end` |
`cuts-in` | `rejected` | `unsafe`, per agent with per-model overrides, no
declaration = `unsafe`), and `Steering` implements `send_when_ready`,
`interrupt_and_send` and `interrupt` over its `Conversation`, with optio's own
queue, one-at-a-time delivery onto an agent's own queue (Fix 17: at most
one optio message waits there; the next goes once the agent reports taking
it), the 15 s bounded wait after an interrupt, and the synthetic events
`x-optio-queued` (`waiting: true` when a Send when ready waits) / `x-optio-taken` / `x-optio-interrupt` /
`x-optio-requeued` (the last for messages a resumed run re-sends, Fix 19:
`docs/2026-09-15-steering-session-end-design.md`).
A wrapper's conversation listener exposes it as `POST /send` (send when
ready, returns `{id, queued}`), `POST /steer` (interrupt and send; empty
text = send what is queued; `upTo` = the queued id Send now was clicked on)
and `POST /interrupt`; Claude Code's listener holds `/send` and `/steer` until the session
has written its opening messages (kickoff prompt, resume notice, re-queued messages), so a
message sent while it starts goes after them; stage 1 wires Claude Code
(`packages/optio-claudecode/AGENTS.md`), including a uuid on every CLI stdin
message (the steering id, for messages Steering sends) and a trailing blank
line on a busy native-queue send. `Steering.reset_transport()` (Fix 25)
re-writes an in-flight message rather than dropping it when a wrapper
relaunches its transport mid-turn (Claude Code did for a model/effort change;
it now switches them live, without a relaunch), so the queue behind it keeps
draining and the UI does not stay stuck busy.
`begin_transport_reset()` (Fix 29) latches that in-flight message from the
moment the relaunch begins, so the dying process's own last events cannot
clear it first and leave nothing to rescue.
Details: `packages/optio-agents/AGENTS.md`.
Design: `docs/2026-09-13-conversation-steering-design.md` and
`docs/2026-09-15-steering-individual-delivery-design.md`.

---

## TypeScript: optio-contracts

Package: `optio-contracts`

### Schemas

**`ProcessSchema`** — full process document:
- `_id`: ObjectId (24-char hex string)
- `processId`: string
- `name`: string
- `description`: string (nullable, optional)
- `params`: `Record<string, unknown>` (optional)
- `metadata`: `Record<string, unknown>` (optional)
- `parentId`: ObjectId (optional)
- `rootId`: ObjectId
- `depth`: int ≥ 0
- `order`: int ≥ 0
- `cancellable`: boolean
- `special`: boolean (optional)
- `warning`: string (optional)
- `status`: `{ state, error?, runningSince?, doneAt?, duration?, failedAt?, stoppedAt? }`
- `progress`: `{ percent: number | null (0–100), message?: string }`
- `log`: `LogEntry[]`
- `uiWidget`: string (nullable, optional) — widget name for `ProcessDetailView` dispatch
- `widgetData`: unknown (optional) — live data delivered to the widget component
- `taskFactory`: boolean (optional) — the record is a `TaskFactory` (missing = false)
- `parameters`: unknown[] (optional) — a factory's field definitions as data (optio-ui types them with vultus-core)
- `supportsResume`: boolean (optional) — task opted into resume support
- `hasSavedState`: boolean (optional) — task has a valid checkpoint ready to restore
- `supportsResurrect`: boolean (optional) — task has a resurrect hook (missing = false)
- `hasUnsavedWork`: boolean (optional) — the host workdir holds work newer than the last snapshot (missing = false)
- `resurrecting`: boolean (optional) — transient, true while a resurrect saves the unsaved work (missing = false)
- `autoResumeScheduled`: boolean (optional) — stamped for automatic resume after an engine restart
- `browserOpenRequests`: `{ requestId, url, createdAt? }[]` (optional)
- `sessionEvents`: `({ requestId, type: 'attention', reason } | { requestId, type: 'client', keyword, data })[]` (optional)
- `originatingSessionId`: string (nullable, optional)
- `createdAt`: Date

**`ProcessStateSchema`** — enum:
`'idle' | 'scheduled' | 'running' | 'done' | 'failed' | 'cancel_requested' | 'cancelling' | 'cancelled'`

**`LogEntrySchema`**:
- `timestamp`: Date
- `level`: `'event' | 'info' | 'debug' | 'warning' | 'error'`
- `message`: string
- `data`: `Record<string, unknown>` (optional)

**Common schemas:**
- `ObjectIdSchema`: string matching `/^[a-f\d]{24}$/i`
- `PaginationQuerySchema`: `{ cursor?: string, limit: number (1–100, default 20) }`
- `PaginatedResponseSchema<T>`: `{ items: T[], nextCursor: string | null, totalCount: number }`
- `ErrorSchema`: `{ message: string }`
- `DateSchema`: coerced Date

### Types

```typescript
type Process = z.infer<typeof ProcessSchema>;
type ProcessState = z.infer<typeof ProcessStateSchema>;
type LogEntry = z.infer<typeof LogEntrySchema>;
```

### Contract Endpoints

`processesContract` — ts-rest router. All paths prefixed with `/processes`. There is no `:prefix` path segment: every route takes the optio instance as the optional query parameters `database` and `prefix` (`?prefix=&database=`; `prefix` defaults to `optio`; `database` is required in multi-db mode and ignored in single-db mode, see `resolveDb`). `:id` is an ObjectId hex or a `processId` string.

| Name | Method | Path | Path Params | Query | Body | Responses |
|------|--------|------|-------------|-------|------|-----------|
| `list` | GET | `/processes` | — | `database?, prefix?, cursor?, limit, rootId?, state?, metadataFilter?` (URL-encoded JSON; legacy `?metadata.<key>=<value>` form returns 400) | — | `200: PaginatedResponse<Process>` |
| `get` | GET | `/processes/:id` | `id` | `database?, prefix?` | — | `200: Process`, `404: Error` |
| `getTree` | GET | `/processes/:id/tree` | `id` | `database?, prefix?, maxDepth?: number` | — | `200: ProcessTreeNode`, `404: Error` |
| `getLog` | GET | `/processes/:id/log` | `id` | `database?, prefix?, cursor?, limit` | — | `200: PaginatedResponse<LogEntry>`, `404: Error` |
| `getTreeLog` | GET | `/processes/:id/tree/log` | `id` | `database?, prefix?, cursor?, limit, maxDepth?` | — | `200: PaginatedResponse<LogEntry & {processId, processLabel}>`, `404: Error` |
| `getConversation` | GET | `/processes/:id/conversation` | `id` | `database?, prefix?` | — | `200: { events: unknown[], capturedAt: string \| null }` (a suspended process's saved conversation record, proxied from the engine's conversation archive; `Cache-Control: no-store`), `401: { message }`, `403`/`404`/`409`/`500`/`503`: `{ reason: ConversationFailureReason, message }` |
| `launch` | POST | `/processes/:id/launch` | `id` | `database?, prefix?` | `{ resume?: boolean; sessionId?: string \| null; params?: Record<string, unknown> }` (optional; body may be omitted entirely; `params` are a task factory's values) | `200: Process` (a factory's launch: the child's record), `400`/`404`/`409`: `{ reason: LaunchFailureReason, message }` (`parameters-required` and `invalid-parameters` are 400, `not-found` 404, the others 409; an `invalid-parameters` message is optio-core's `"<id>: <what>"`, unchanged) |
| `cancel` | POST | `/processes/:id/cancel` | `id` | `database?, prefix?` | (none) | `200: Process`, `404`/`409`: `{ reason: CancelFailureReason, message }` |
| `dismiss` | POST | `/processes/:id/dismiss` | `id` | `database?, prefix?` | (none) | `200: Process`, `404`/`409`: `{ reason: DismissFailureReason, message }` |
| `resurrect` | POST | `/processes/:id/resurrect` | `id` | `database?, prefix?` | `{ sessionId?: string \| null }` (optional; body may be omitted entirely) | `200: Process`, `404`/`409`: `{ reason: ResurrectFailureReason, message }` |
| `resync` | POST | `/processes/resync` | — | `database?, prefix?` | `{ clean?: boolean; metadataFilter?: ProcessMetadataFilter }` (send `{}` for full sync) | `202: { message: string }` |

`discoveryContract` has one route, `instances`: GET `/optio/instances` → `200: { instances: { database, prefix, live }[] }`.

Note: The adapters mount the contract under `/api` (`pathPrefix: '/api'`), so effective paths are `/api/processes/...?prefix=&database=`.

### Engine RPC contract

`optioEngineContract` (clamator, service `optio-engine`): methods `launch`, `cancel`, `dismiss`,
`resurrect`, `groupCancel`, `groupCancelAndWait`, `blockLaunches`, `unblockLaunches`,
`materializeUpload`; notification `resync`. `resurrect({ processId, sessionId: string | null })`
returns `{ ok: true, process } | { ok: false, reason: ResurrectFailureReason }`.
`launch({ processId, resume?, sessionId: string | null, params? })` returns
`{ ok: true, process } | { ok: false, reason: LaunchFailureReason, message? }` (`message` is the
`"<id>: <what>"` text of an `invalid-parameters` refusal); the engine answers every reason
optio-core's `launch` can return.

Failure-reason enums (zod enum + type of the same name, browser-safe, exported from the package
root): `LaunchFailureReason` (`not-found`, `not-launchable`, `no-resume-support`, `launch-blocked`,
`shutting-down`, `parameters-required`, `not-a-factory`, `invalid-parameters`), `CancelFailureReason`, `DismissFailureReason`,
`ResurrectFailureReason` (`not-found`, `not-resurrectable`, `no-resurrect-support`,
`resurrect-in-progress`, `launch-blocked`, `shutting-down`), `GroupCancelFailureReason`,
`BlockLaunchesFailureReason`.

---

## TypeScript: optio-api

Package: `optio-api`. Framework-agnostic handlers + Fastify adapter.

### Role: read-only DB exposure + RPC client

The optio-api server has two roles only:

1. **Expose process data for reading.** Direct Mongo reads via the layered Engine-access primitives (`resolveDb`, ts-rest contract routes).
2. **Expose the optio engine's capabilities for mutation.** All mutations go via RPC into optio-core via `OptioEngineClient`. The API contains no business logic, generates no IDs/timestamps/defaults, and does not write to MongoDB.

API handlers are pure pass-through: validate (auto via ts-rest/zod) → inject auth-derived fields → `engine.launch(...)` / `engine.cancel(...)` / etc. → map failure reason to HTTP status → return RPC's response body. No `??` defaulting, no `new Date()`, no `new ObjectId()` in write paths.

Direct DB writes (`insertOne`, `updateOne`, `deleteOne`, `findOneAndUpdate`, etc.) under `packages/optio-api/src/` are violations of this rule. Tests may write directly (under `__tests__/`); production code never does. `make lint-no-direct-writes` enforces this via a grep guardrail; `packages/optio-api/eslint.config.mjs` surfaces violations inline in editors via `no-restricted-syntax`.

### Don't copy code between projects

When N projects need the same code — especially interface, contract, or wire-format code that must stay in sync — build a shared library. Do not duplicate. Code copies drift; bugs get fixed in one and not the others; schema mismatches cause runtime breakage. Cost of a shared lib is one-time; cost of N copies is linear in maintenance.

### Entry Points

```typescript
// Handlers (framework-agnostic)
export { listProcesses, getProcess, getProcessTree, getProcessLog, getProcessTreeLog,
         getProcessConversation,
         launchProcess, cancelProcess, dismissProcess, resurrectProcess,
         resyncProcesses } from 'optio-api';
export type { ListQuery, PaginationQuery, TreeLogQuery,
              LaunchCommandResult, CancelCommandResult, DismissCommandResult,
              ResurrectCommandResult, ResyncCommandResult, ForbiddenResult,
              ConversationResult } from 'optio-api';

// Authentication + per-request access (for custom adapters). checkAuth(req, authenticate, isWrite)
// -> null | { status: 401 | 403, body }; accessFor / resolveAccess build the request's Access from
// the scope / visibility / authorize hooks; UNRESTRICTED is the no-hooks Access.
export { checkAuth, accessFor, resolveAccess, hasAccessHooks, UNRESTRICTED,
         type OptioRole, type AuthCallback, type AuthResult, type Access, type AccessHooks,
         type ScopeCallback, type ScopeFilter, type VisibilityCallback, type AuthorizeCallback,
         type AuthorizeInput, type OptioAction, type ProcessRef } from 'optio-api';

// Conversation archive proxy (read a suspended process's record; for custom adapters)
export { resolveConversationArchive, forgetConversationArchive, fetchConversation,
         CONVERSATION_ARCHIVE_CACHE_TTL_MS, CONVERSATION_ARCHIVE_TIMEOUT_MS,
         CONVERSATION_ARCHIVE_MAX_BODY_BYTES,
         type ConversationArchiveEndpoint, type ConversationFetchResult } from 'optio-api';

// Scope helpers (for custom adapters). findActionTarget(reads, id): the document a
// single-process action targets, by _id for a 24-hex id, else by processId (a
// duplicated processId resolves to the newest document, as the engine does).
// findReadableProcess(reads, id, access): the same lookup, additionally null when
// the document fails `access.visibility`; used by getProcess, getProcessLog,
// getProcessTree and getProcessTreeLog in place of findScopedProcess.
// findScopedProcess/findReadableProcess/findActionTarget/inScope/gateProcess all
// accept a `ProcessReads` (below) or, for custom adapters and compatibility, a
// plain `Collection`.
export { findActionTarget, findReadableProcess, findScopedProcess, gateProcess, inScope,
         andScope, scopeToMongo, gateInstances, scopedResyncFilter, toProcessRef,
         type GateResult, type ScopedFilterResult } from 'optio-api';

// Visibility helpers (for custom adapters). normalizeVisibility(raw): validate a
// visibility hook's return value (null/undefined/{} -> null, else parsed with
// ProcessMetadataFilterSchema, throwing on an invalid predicate); visibilityToMongo /
// andVisibility: the predicate's Mongo filter fragment, ANDed into a base filter like
// andScope; isVisible(col, doc, visibility): whether one document satisfies the
// predicate, judged in Mongo; pruneOrphans(docs, keep?): drop tree children whose
// parent chain doesn't resolve to a kept root. Wired into AccessHooks (the `visibility`
// hook) and gateProcess (the single-process action gate, via an explicit `isVisible`
// check on an unfiltered read). Every process read that can reach a response --
// listProcesses (and its countDocuments), getProcess/getProcessLog/getProcessTree/
// getProcessTreeLog (404 for a hidden process by either id form; getProcessTree
// fetches only visible children at each level; getProcessTreeLog prunes orphans,
// only when visibility is set, before collecting log lines) -- goes through the one
// choke point below, `processReads`, so no handler ANDs the condition itself.
// The streams and all four adapters honour it too (see the visibility(req)
// comment and "Stream Poller" below).
export { normalizeVisibility, visibilityToMongo, andVisibility, isVisible,
         pruneOrphans } from 'optio-api';

// The one choke point for process reads (packages/optio-api/AGENTS.md "One choke
// point"): processCollection(db, prefix) is the only place allowed to name the
// `{prefix}_processes` collection outside a short allowlist (a change stream, instance
// discovery, and the two upstream lookups that run only after gateProcess admitted the
// process). processReads(db, prefix, visibility) / processReadsOf(col, visibility)
// return a `ProcessReads`, whose find/findOne/countDocuments AND the visibility
// condition into every query, passing a null-visibility filter through unchanged (the
// same object, not a copy). The handlers build one `ProcessReads` per request and
// read through it exclusively; a guard test (process-reads-guard.test.ts) fails the
// build if a new read path names the collection, calls processCollection outside its
// allowlist, or reaches `.unfiltered` outside access-scope.ts. The guard is a
// tripwire for the common bypasses (forgetting processReads), not a proof: it is a
// text scan, so a string-concatenated collection name, an aliased import of
// processCollection/processReads/processReadsOf, or a destructured `unfiltered`
// used later all slip past it; review still has to catch those.
export { processCollection, processReads, processReadsOf, type ProcessReads } from 'optio-api';

// Predicate builders (for writing a visibility hook), re-exported from optio-contracts
export { and, or, not, leaf, eq, ne, isIn, notIn, exists, gt, gte, lt, lte } from 'optio-api';

// Layered RPC access
//   Layer 1 (Transport cache): createOptioTransports / OptioTransports
//   Layer 2 (Optio context):   createOptioContext / OptioContext / resolveOptioEngine
//   Generated:                 OptioEngineClient (wraps Layer 0 RpcClient)
export { OptioEngineClient,
         createOptioTransports, type OptioTransports,
         createOptioContext, type OptioContext,
         resolveDb, resolveOptioEngine,
         type DbOptions, type SingleDbOptions, type MultiDbOptions } from 'optio-api';

// Stream poller (for custom SSE adapters)
export { createListPoller, createTreePoller, createMultiTreePoller, createSessionEventsPoller } from 'optio-api';
export type { StreamPollerOptions, TreePollerOptions, MultiTreePollerOptions, MultiTreeRoot,
              SessionEventsPollerOptions, ListPollerHandle } from 'optio-api';

// Instance discovery and metadata-filter helpers
export { discoverInstances,              // (dbOpts, redis?, prefix?) -> { database, prefix, live }[]
         parseMetadataFilterQuery, metadataFilterToMongo,
         detectLegacyMetadataParams, formatLegacyMetadataMessage,
         type ParseResult, type ProcessMetadataFilter } from 'optio-api';
```

### OptioApiOptions (two-form)

```typescript
import type { AuthCallback, OptioRole, OptioContext } from 'optio-api';

// Sugar form: adapter constructs OptioContext internally, returns it.
interface SugarOptioApiOptions {
  db?: Db;                                    // single-db mode
  mongoClient?: MongoClient;                  // multi-db mode (alternative to db)
  redis: Redis;
  prefix?: string;                            // optional; limits instance discovery to this prefix
  authenticate: AuthCallback<TRequest>;
  scope?: ScopeCallback<TRequest>;            // optional tenant boundary (see below)
  authorize?: AuthorizeCallback<TRequest>;    // optional per-action decision (see below)
  visibility?: VisibilityCallback<TRequest>;  // optional per-process hide (see below)
  verbose?: boolean;                          // Fastify only
}

// Explicit form: caller supplies a pre-built OptioContext. Returns void.
interface ExplicitOptioApiOptions {
  ctx: OptioContext;
  prefix?: string;
  authenticate: AuthCallback<TRequest>;
  scope?: ScopeCallback<TRequest>;
  authorize?: AuthorizeCallback<TRequest>;
  visibility?: VisibilityCallback<TRequest>;
}

// AuthCallback returns 'viewer' (read-only) | 'operator' (read+write) | null (deny).
// Enforced on every request to every route across all four adapters: REST,
// SSE streams, /api/optio/instances discovery, and the /api/widget/* proxy.
// Reads (GET/HEAD/OPTIONS) require viewer or operator; writes require operator.
// Fastify: authenticate runs in an onRequest hook added to the instance passed to
// registerOptioApi, so it gates every route of the host app too, not only optio's;
// let the host's own routes through in the callback (optio-dashboard returns
// 'operator' for URLs outside /api/ and for /api/auth/). Express mounts it with
// app.use('/api', ...): a narrower reach, but host /api/* routes added after it pass through it too.
//
// scope(req) -> ScopeFilter | null: a flat exact-match metadata map
// ({ customerId: 3 }) confining the request to processes whose metadata
// matches it (a child lacking the keys is judged by its root). Applied to
// every process lookup: lists and list/session-events streams (ANDed into the
// query), single-process reads and tree streams, launch/cancel/dismiss/resurrect,
// the widget proxy, widget-control and widget-upload. Outside the scope a process
// behaves as not found (404; missing in multi-tree resolution). A scoped
// resync runs with the client's flat filter plus the scope keys; a filter
// contradicting the scope is 403, a predicate tree 400. Resync is unaffected
// by visibility (not per-process; bounded by scope alone): a clean resync
// (clean: true) deletes every root record its scope filter matches, hidden
// ones included -- authorize('resync', { clean }) decides who may run one. A
// plain resync only regenerates, and prunes records whose task no longer exists.
//
// authorize(req, { role, action, process?, metadataFilter?, clean? }) -> boolean:
// called only after a process is confirmed in scope and (where checked) visible —
// a process the request cannot see is a 404 before authorize is asked, not a 403.
// Runs for launch/cancel/dismiss/resurrect/resync, the three widget routes and
// instance discovery; false gives 403 { message: 'Forbidden' }. action 'resurrect'
// is a single-process action like 'launch' (input.process set). OptioAction has no
// 'read' member: it was declared but never passed to authorize.
//
// Without scope/visibility/authorize nothing changes (no extra lookups).
//
// visibility(req) -> ProcessMetadataFilter | null (a per-request metadata predicate,
// `VisibilityCallback<TRequest>`; see packages/optio-api/AGENTS.md "Access scope
// and authorization"): declared once at init next to scope/authorize -- clients
// never send it, no route takes it as a parameter. A rule per group of processes
// is a clause of the one predicate (typically one per `kind`, plus a catch-all for
// everything else), e.g. or(and(eq('kind','data-chat'), eq('ownerId', me(req))),
// not(isIn('kind', ['data-chat']))). eq(key, null) matches a *missing* key too
// (MongoDB semantics), so a null/undefined user id must never reach a leaf this
// way; an `undefined` value anywhere in the predicate is rejected outright
// (normalizeVisibility throws, failing the request), never treated as "no
// restriction" the way the top-level null/undefined/{} is. `Access` carries it,
// and gateProcess (the single-process action gate) checks it before authorize,
// 404ing a hidden process
// the same as an out-of-scope one. The read handlers (list, single reads, the tree
// and the tree log) honour it too: a hidden process is a 404/omitted, exactly like
// a missing one, and the tree log's orphans are pruned once visibility can create
// them -- a child whose own metadata diverges from its inherited copy can stay
// visible under a hidden parent on a single read or the list, but appears in no
// tree (no ancestor walk; documented, not compensated for). The
// streams (stream-poller.ts) honour it too: each poller takes a `visibility`
// option alongside `scope`, reads through processReads, and -- for the tree and
// multi-tree streams -- prunes orphans and re-reads on any metadata change (not
// only a member's), so a process gaining or losing visibility appears or
// disappears mid-stream like a create or delete does today. All four adapters
// accept it next to scope/authorize and pass it through to every handler call
// and every poller they create; a hidden id in the multi-tree stream's
// `resolveOne` is reported in the `resolution` event's `missing` list, exactly
// like a nonexistent or out-of-scope one.
```

### Fastify Adapter

```typescript
import { registerOptioApi } from 'optio-api/fastify';

registerOptioApi(app: FastifyInstance, opts: OptioApiOptions): OptioContext | void
// Sugar form: returns the constructed OptioContext; fastify onClose wired to ctx.closeAll().
// Explicit form (caller passed ctx): returns void; caller owns ctx lifecycle.
// Single entry point for the Fastify integration. Registers:
//   - The authenticate onRequest hook (app-wide, see OptioApiOptions above)
//   - REST endpoints from processesContract under /api/processes/... (?database=&prefix=)
//   - SSE endpoints GET /api/processes/:id/tree/stream, GET /api/processes/stream,
//     GET /api/processes/tree/multi/stream and GET /api/session-events/stream
//   - Instance discovery GET /api/optio/instances (only instances of opts.prefix when set)
//   - POST /api/widget-control/:database/:prefix/:processId (body { text } or { key }) and
//     POST /api/widget-upload/:database/:prefix/:processId (multipart, staged in GridFS,
//     handed to the engine's materializeUpload)
//   - Widget reverse-proxy at /api/widget/:database/:prefix/:processId/*
//     proxies HTTP, SSE, and WebSocket to the upstream URL stored in widgetUpstream.
//     404 when widgetUpstream is absent; 502 on upstream errors.
//     Inner auth (Basic/Header via headers, Query via URL) injected per-request.
//     widgetUpstream is TTL-cached (5 s) per (database, prefix, processId) to reduce
//     MongoDB round-trips.
```

### Handlers (all signatures)

```typescript
interface ListQuery {
  cursor?: string; limit: number; rootId?: string;
  state?: string;
  metadataFilter?: ProcessMetadataFilter; // exact-match map { key: value }; URL-encoded JSON form is for the wire
}
interface PaginationQuery { cursor?: string; limit: number; }
interface TreeLogQuery extends PaginationQuery { maxDepth?: number; }

// Every handler takes the OptioContext, the instance query { database?, prefix? } (resolved by
// resolveDb) and a trailing `access: Access = UNRESTRICTED`. Command results:
//   { status: 200; body: Process } | { status: 404 | 409; body: { reason, message } } | ForbiddenResult (403)
type InstanceQuery = { database?: string; prefix?: string };

async function listProcesses(ctx: OptioContext, query: ListQuery & InstanceQuery, access?: Access): Promise<PaginatedResponse>
async function getProcess(ctx: OptioContext, query: InstanceQuery, id: string, access?: Access): Promise<Process | null>
async function getProcessTree(ctx: OptioContext, query: InstanceQuery & { maxDepth?: number }, id: string, access?: Access): Promise<ProcessTreeNode | null>
async function getProcessLog(ctx: OptioContext, query: PaginationQuery & InstanceQuery, id: string, access?: Access): Promise<PaginatedResponse<LogEntry> | null>
async function getProcessTreeLog(ctx: OptioContext, query: TreeLogQuery & InstanceQuery, id: string, access?: Access): Promise<PaginatedResponse | null>
async function getProcessConversation(ctx: OptioContext, query: InstanceQuery, id: string, access?: Access): Promise<ConversationResult>
async function launchProcess(ctx: OptioContext, query: InstanceQuery, id: string, resume = false,
                             sessionId: string | null = null, access?: Access,
                             params?: Record<string, unknown>): Promise<LaunchCommandResult>
// 409 { reason: 'no-resume-support', message: "This task does not support resume" } when resume=true and supportsResume=false.
// `params` (a task factory's values) go to the engine unchanged. 400 for parameters-required and
// invalid-parameters (its message is the engine's "<id>: <what>", unchanged), 409 for not-a-factory
// and shutting-down.
async function cancelProcess(ctx: OptioContext, query: InstanceQuery, id: string, access?: Access): Promise<CancelCommandResult>
async function dismissProcess(ctx: OptioContext, query: InstanceQuery, id: string, access?: Access): Promise<DismissCommandResult>
async function resurrectProcess(ctx: OptioContext, query: InstanceQuery, id: string,
                                sessionId: string | null = null, access?: Access): Promise<ResurrectCommandResult>
// POST /processes/:id/resurrect. 200: Process; 404/409: { reason: ResurrectFailureReason, message }
// (not-found is 404, every other reason 409); 403 when authorize refuses.
async function resyncProcesses(ctx: OptioContext, query: InstanceQuery, clean = false,
                               metadataFilter?: ProcessMetadataFilter, access?: Access): Promise<ResyncCommandResult>
// 202 { message }; 400/403 { message } when the filter is a predicate tree under a scope / contradicts it, or authorize refuses.
```

### In-process triggers

In-process domain code that needs to launch / cancel / dismiss / resurrect / resync calls the
`Optio` methods directly (`optio_core.launch` / `cancel` / `dismiss` / `resurrect` / `resync`);
no wire involved. External services use the clamator RPC channel — see "Control-plane
convergence" above for the architectural rule, and the optio-core README's
"Remote Control via Clamator RPC" section for the client-construction details.

### Stream Poller

Used internally by SSE endpoints. Each stream reads MongoDB once at start, then only when the shared change stream of its `(database, prefix)` reports a change relevant to it, at most once a second; where change streams are unavailable (standalone MongoDB) or with `OPTIO_API_CHANGE_STREAMS=off`, once a second. Sends change events only (snapshot diffing). Details: `packages/optio-api/AGENTS.md` "Stream Poller"; spec `docs/2026-10-09-api-change-streams-design.md`.

```typescript
interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  metadataFilter?: ProcessMetadataFilter;      // list poller
  scope?: ScopeFilter | null;                 // list/session-events pollers only
  visibility: ProcessMetadataFilter | null;    // required on every poller (null = no restriction); read through processReads
  hub?: ProcessChangeHub;                     // default: the shared change hub of (db, prefix)
}

interface TreePollerOptions extends Omit<StreamPollerOptions, 'metadataFilter' | 'scope'> {
  rootId: string;
  baseDepth: number;
  maxDepth?: number;
}

interface ListPollerHandle { start(): void; stop(): void; }

function createListPoller(opts: StreamPollerOptions): ListPollerHandle
// Sends: { type: 'update', processes: ProcessListItem[] }

function createTreePoller(opts: TreePollerOptions): ListPollerHandle
// Sends: { type: 'update', processes: ProcessTreeItem[] }
//        { type: 'log', entries: LogEntry[] }
//        { type: 'log-clear' }

// createMultiTreePoller / createSessionEventsPoller take the same `visibility`
// option (MultiTreePollerOptions / SessionEventsPollerOptions); details:
// packages/optio-api/AGENTS.md "Stream Poller".
```

With `visibility` set, the tree and multi-tree pollers prune orphans out of
each read and re-read on any metadata change (not only a member's), so a
process gaining or losing visibility appears or disappears mid-stream the
same way a create or delete does today; an entry point losing visibility
leaves the stream, as for a deletion -- literally empty (`processes: []`)
only when the entry point is the tree's root, since the tree poller reads
the whole tree by `rootId` and prunes by walking down from a root
`pruneOrphans` can see; a non-root entry losing visibility only drops its
own subtree. A *visible* non-root entry under a *hidden* root also empties
the stream and the tree log (the root itself fails the read, breaking
every orphan chain), even though `GET /processes/:id/tree` (REST, which
walks down from the entry by `parentId`, never through the root) still
returns that entry's subtree -- a documented, not compensated-for,
diverged-metadata edge case; details: packages/optio-api/AGENTS.md
"Stream Poller".

**List stream process shape:**
```typescript
{ _id, processId, name, status, progress, cancellable, special, warning, metadata, depth,
  supportsResume, hasSavedState, autoResumeScheduled, browserOpenRequests, taskFactory, parameters }
```

**Tree stream process shape:**
```typescript
{ _id, parentId: string | null, rootId, processId, launchMode, name, status, progress, cancellable,
  depth, order, widgetData, uiWidget, metadata, warning,
  supportsResume, hasSavedState, autoResumeScheduled, browserOpenRequests, taskFactory, parameters }
```

Both streams, and the multi-tree stream (`GET /api/processes/tree/multi/stream`), also carry
`supportsResurrect`, `hasUnsavedWork` and `resurrecting` (`false` when absent), tracked in the
snapshot fingerprint. Neither carries `description`.

All three also carry a task factory's `taskFactory` (`false` when absent) and `parameters` (absent
on other records), and the tree streams `warning` (the list stream always did): optio-ui opens a
factory's launch form from them. The list stream reads them (`LIST_PROJECTION`); `warning`,
`taskFactory` and `parameters` are in every stream's snapshot fingerprint, so a resync that changes
only a factory's definition is sent again.

`widgetData` is included in tree-stream payloads and tracked in the snapshot fingerprint so
worker-side mutations trigger SSE events. The list stream does **not** include `widgetData`.
`widgetUpstream` and `controlUpstream` are never present in any client-facing payload.

---

## TypeScript: optio-ui

Package: `optio-ui`. React components and hooks. Requires `OptioProvider` at root.

**Spinner slot.** An app can lend its loading spinner through vultus-core
(`<VultusComponentsContext.Provider value={{ Spinner }}>`, or vultus-antd's
`VultusProvider components={{ Spinner }}`); optio-ui then shows it in place of antd's spinners
in `ProcessList` (key `optio:process-list`, size `'default'`) and the `LaunchControls`
Resurrecting indicator (`optio:process:<id>:resurrecting`), and before the "Loading…" text in
`ProcessDetailView` (`optio:process:<id>:detail`) and the iframe widget
(`optio:process:<id>:widget`); size `'small'` except `ProcessList`. A vultus-core `SpinnerScope` prefixes the keys; without a lent spinner nothing
changes. Details: `packages/optio-ui/AGENTS.md` "Spinner slot".

### OptioProvider Props

```typescript
interface OptioProviderProps {
  prefix?: string;    // omitted: that of the instance discovery finds (GET /api/optio/instances) when it finds exactly one, else 'optio'
  database?: string;  // omitted: the discovered instance's database
  live?: boolean;     // omitted: the discovered instance's liveness, else false
  baseUrl?: string;   // default: '' (same origin)
  onAttention?: (processId: string, reason: string) => void;                     // ctx.need_attention events of processes this tab's session launched
  onClientMessage?: (processId: string, keyword: string, data: unknown) => void; // ctx.client_message events, likewise
  onBrowserOpen?: BrowserOpenHandler;  // overrides the default handler for agent "open this URL" requests (window.open + notification fallback)
  children: ReactNode;
}
```

Wrap your application (or subtree):

```tsx
<QueryClientProvider client={queryClient}>
  <OptioProvider prefix="myapp">
    <App />
  </OptioProvider>
</QueryClientProvider>
```

---

### Components

**`LaunchControls`** (`packages/optio-ui/src/components/LaunchControls.tsx`)

```typescript
interface LaunchControlsProps {
  process: any;
  onLaunch?: (id: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;  // a task factory's launch form launched it: the child it made
  onResurrect?: (id: string) => void;  // save a failed run's host work, then resume
  size?: ButtonProps['size'];
  iconFontSize?: number;               // pixel size of the buttons' icons
  denyReason?: string | null;          // set + non-empty: launch button disabled, this as its tooltip
}
```

Smart launch button. Renders nothing when not in a launchable state or `onLaunch` is absent.
While `isResurrecting(process)` (`resurrecting` true) it renders a single disabled "Resurrecting" indicator
(spinning icon, tooltip "Resurrecting: saving the unsaved work…") and nothing clickable.
For a task factory's record (`isTaskFactory(process)`: `taskFactory: true`; exported from `optio-ui`) it
renders a single play button that opens the factory's `TaskFactoryLaunchModal` (below) instead of calling
`onLaunch`, which only allows it; no `Popconfirm` (the form shows the `warning`); the form's launch hands
the child to `onLaunched`.
When `onResurrect` is given and `isResurrectable(process)` (launchable, not resurrecting, and `supportsResurrect`
and `hasUnsavedWork` both true; `isResurrectable` and `isResurrecting` are exported from `optio-ui`),
renders a split button: primary = Resurrect (calls `onResurrect(id)`), menu = Resume from last snapshot (only
when resumable) and Restart; both menu items first confirm "This discards the unsaved work left by the failed
run." (OK = "Discard and continue", via `Modal.useModal()`) before calling `onLaunch`. Otherwise:
Renders a single play button when `supportsResume=false` or `hasSavedState=false`.
Renders an Ant Design `Dropdown.Button` (primary = Resume, menu = Restart) when both flags are true.
Used by `ProcessList` and `ProcessDetailView`.

**`ProcessList`**

```typescript
interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;  // forwarded to each ProcessItem
  onResurrect?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}
```

Ant Design `List` of `ProcessItem`. Shows name (with description tooltip if set), status badge, progress bar, launch/cancel buttons. Launch button rendered via `LaunchControls` (`onResurrect` and `onLaunched` forwarded to it). `FilteredProcessList` takes the same callbacks, including `onResurrect` and `onLaunched`. optio-dashboard passes `useProcessActions()`'s `launch`, `resurrect` and `cancel` to its `FilteredProcessList` as `onLaunch` / `onResurrect` / `onCancel` when the selected instance is live (none for an offline one).

**`ProcessItem`**

```typescript
interface ProcessItemProps {
  process: any;
  onLaunch?: (id: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;  // forwarded to LaunchControls (a task factory's launch)
  onResurrect?: (id: string) => void;  // forwarded to LaunchControls
  onCancel?: (id: string) => void;
  readonly?: boolean;
  onProcessClick?: (id: string) => void;
  size?: ProcessItemSize;
  inline?: boolean;                    // name / progress / actions on one row
  inactiveContent?: ReactNode;         // shown where the progress bar would be while not active
  launchDenyReason?: string | null;    // forwarded to LaunchControls as denyReason
}
```

Single process row. Launch button shows `Popconfirm` when `process.warning` is set (a task
factory's opens its launch form instead, which shows the warning). The progress
message is shown while the process is active or resurrecting (`isResurrecting`).

**`ProcessStatusBadge`**

```typescript
interface ProcessStatusBadgeProps {
  state: string;
  error?: string;
  runningSince?: string | null;
  size?: ProcessStatusBadgeSize;     // default 'small'
  autoResumeScheduled?: boolean;     // shows a clock icon ("Scheduled for auto-restart")
  className?: string;
  supportsResume?: boolean;          // cancel_requested / cancelling read "Suspending" (blue)
  hasSavedState?: boolean;           // with supportsResume: cancelled reads "Suspended" (blue)
}
```

Ant Design `Tag` with state-based color. Active states show elapsed time (live ticker). Failed state shows error tooltip icon. A resumable process being stopped reads "Suspending", and a cancelled one with saved state "Suspended", both blue.

State → color mapping:
- `idle` → `default`
- `scheduled` → `cyan`
- `running` → `blue`
- `done` → `green`
- `failed` → `red`
- `cancel_requested` → `orange`
- `cancelling` → `orange`
- `cancelled` → `orange`

**`ProcessTreeView`**

```typescript
interface ProcessTreeViewProps {
  treeData: ProcessNode | null;
  sseState: { connected: boolean };
  onCancel?: (processId: string) => void;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;      // forwarded to each node's LaunchControls
  onResurrect?: (processId: string) => void;  // forwarded to each node's LaunchControls
}
```

Ant Design `Tree` rendering process hierarchy with status badges, progress bars, cancel buttons, and (when `onLaunch` is given) `LaunchControls`. A node's progress message is shown while it is active or resurrecting (`isResurrecting`). Has built-in "Hide finished sub-tasks" toggle (default: on).

`ProcessNode` shape expected by this component:
```typescript
interface ProcessNode {
  _id: string;
  name: string;
  description?: string | null;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  warning?: string;
  supportsResume?: boolean;     // these flags are read by LaunchControls
  hasSavedState?: boolean;
  supportsResurrect?: boolean;
  hasUnsavedWork?: boolean;
  resurrecting?: boolean;
  autoResumeScheduled?: boolean;
  taskFactory?: boolean;        // a task factory's record: LaunchControls opens its launch form
  parameters?: unknown[];       // the factory's parameters, for that form
  children?: ProcessNode[];
}
```

**`ProcessLogPanel`**

```typescript
interface ProcessLogPanelProps {
  logs: LogEntry[];  // { timestamp: string; level: string; message: string; data?; processId: string; processLabel: string }
  tree: ProcessTreeNode | null;  // gives each process's lines a stable color and indent; null: no bar
  fillParent?: boolean;          // fill the parent's height instead of max-height 400px
}
```

Scrollable log viewer (max-height 400px, monospace). Auto-scrolls to bottom unless user has scrolled up. Level → color: `event`→cyan, `info`→blue, `debug`→default, `warning`→gold, `error`→red.

**`ProcessFilters`**

```typescript
function ProcessFilters(props?: { className?: string; style?: CSSProperties }): JSX.Element
// Search box + filter-group select + "show details" / "show special" checkboxes. Holds no state
// of its own: reads and sets it through useProcessFilter(), so it must sit inside
// <WithFilteredProcesses>, next to the FilteredProcessList it filters.

function WithFilteredProcesses(props: { children: ReactNode }): JSX.Element  // provides the filter state
function useProcessFilter(): { filterGroup, setFilterGroup, showDetails, setShowDetails,
                               showSpecial, setShowSpecial, filterFn: (processes: any[]) => any[] }

type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';
```

**`ProcessDetailView`**

```typescript
interface ProcessDetailViewProps {
  processId: string | null | undefined;
  readOnly?: boolean;  // default false; true hides the launch / resurrect / cancel affordances
  showLogs?: boolean;  // default true; false renders only the widget, at full height
  waitForWidget?: boolean;  // default false; true shows a big spinner instead of "Loading…" and the process view until the widget shows
  widgetPlaceholder?: ReactNode;  // shown instead of that spinner; implies waitForWidget
  onLaunched?: (child: Process) => void;  // a task factory's launch form (its play button in the tree) launched it
}
```

Self-fetching detail panel (uses `useProcessStream` internally). Renders a placeholder when
`processId` is falsy, a loading state while the SSE stream connects, a named widget when
`tree.uiWidget` is a registered name for the process's mode (live or suspended — see
`useProcessWidgetState` below), or the default `ProcessTreeView` + `ProcessLogPanel` layout
otherwise. Warns to `console.warn` and falls back to default when `uiWidget` is set but no
live widget is registered; falls back silently (no warning) when the process `isSuspended`
and no suspended variant is registered. In suspended mode, also renders a `LaunchControls`
(Resume) row above the widget unless `readOnly`, wired to `launch` and `resurrect` like the
default layout (a process left with `hasUnsavedWork` gets Resurrect, and Resume asks before it
discards the unsaved work). The default layout wires `ProcessTreeView`'s
`onLaunch`, `onResurrect` and `onCancel` to `useProcessActions()` itself (none when
`readOnly`), so the host passes no action callbacks to it; `onLaunched` is passed on, for a task
factory's record (the root of its launches), whose launch shows under it in the tree.

**`TaskFactoryLaunchModal`** (`packages/optio-ui/src/components/TaskFactoryLaunchModal.tsx`)

```typescript
interface TaskFactoryLaunchModalProps {
  process: { _id: string; name: string; warning?: string | null; parameters?: unknown[] | null };  // TaskFactoryRecord
  open: boolean;
  onClose: () => void;                    // Cancel, the close icon, Escape, and after a launch
  onLaunched?: (child: Process) => void;  // the launched child; the modal then closes
}
```

A task factory's launch form (spec `docs/2026-10-20-task-factory-design.md` §3), opened by
`LaunchControls` for a factory's record (`taskFactory` and `parameters`: the REST reads and every
optio-api stream carry them, see "List stream process shape"). A vultus-antd `FormPanel`: the
factory's `warning` on top, one antd `Form.Item` (name = the parameter's id) per parameter holding a
vultus-antd `<Field>`, then Cancel and Launch (vultus actions). The definitions get their labels (and
their choices') resolved for the UI language (that language, its base language, else `en`, else the
first; no label: the id); `required` and a text's `maxLength`, which vultus-core has no option for, are
left out and checked by the form (a required text or manyOf left empty, a text over `maxLength`: nothing
is launched). Initial values: `initialValue`, else the kind's default. Launch calls
`useProcessActions().launchAsync(_id, { params })` with every value (`errorRoute: 'processes.launch'`,
`optioErrorRoutes`); success calls `onLaunched(child)` and closes. A refusal keeps the modal open with
the values: `invalid-parameters` whose message `"<id>: <what>"` names a field shows `<what>` on it (and
the reason's text in the form's error); one naming none shows its message in the form's error; the other
reasons their text (`errors.processes.launch.<reason>`). Each opening starts from the initial values.
Exported from `optio-ui` (with `TaskFactoryLaunchModalProps`, `TaskFactoryRecord`,
`TaskFactoryParameter`, `ParameterChoice`, `ParameterLabel`).

**`optioErrorRoutes`** (`packages/optio-ui/src/error-routes.ts`) — vultus-core's
`ErrorRoutesRegistry<'processes.launch'>` (`OptioErrorRouteId`): every `LaunchFailureReason` →
`{ i18nKey: 'errors.processes.launch.<reason>' }`, typed exhaustively (a reason added to
optio-contracts fails the typecheck until it has an entry). Its texts are the app's (no default).

---

### Widget System

```typescript
interface WidgetProps {
  process: any;            // full process tree root (includes widgetData, uiWidget)
  apiBaseUrl: string;      // optio API base URL
  widgetProxyUrl: string;  // ${apiBaseUrl}/api/widget/${database}/${prefix}/${process._id}/ (URI-encoded) — trailing slash load-bearing
  prefix: string;
  database?: string;
  readOnly?: boolean;      // the host shows the process read-only (ProcessDetailView readOnly): do not launch or resume it
  hideEmptyRuns?: boolean; // conversation widgets: leave out resumed runs where nothing happened
  placeholder?: ReactNode; // what the host shows while the process waits for its widget
  onReady?: () => void;    // given to a live widget taking over from the suspended variant; call once it shows the process
}

type WidgetComponent = ComponentType<WidgetProps>;

interface RegisterWidgetOptions {
  suspended?: WidgetComponent;   // shown instead of `component` while the process isSuspended
}

function registerWidget(name: string, component: WidgetComponent, options?: RegisterWidgetOptions): void
function getSuspendedWidget(name: string): WidgetComponent | undefined
```

Call `registerWidget` at module load time to make a widget available to `ProcessDetailView`.
`options.suspended` registers a second component shown while the process `isSuspended`
(cancelled, `supportsResume`, `hasSavedState`) rather than widget-live; same `WidgetProps`
either way. Omitting `options` (or `options.suspended`) leaves any suspended variant already
registered for that name untouched — every pre-existing two-argument call site keeps working
unchanged. `useProcessWidgetState` (below) is what actually dispatches between the two maps;
its `mode: 'live' | 'suspended'` tells the caller which one it got.

**Built-in `'iframe'` widget** — registered automatically when anything from `optio-ui` is
imported. Reads `process.widgetData` for config (`iframeSrc`, `localStorageOverrides`,
`sandbox`, `allow`, `title`). Sets localStorage entries on mount and removes them on
unmount. Shows a dismissible "Session ended." banner on terminal states. Has no suspended
variant, so a suspended process using it falls back to the default tree/log layout.

**Built-in `'iframe-input'` widget** (`IframeInputWidget`) — the `'iframe'` widget plus a text box
under it that POSTs `{ text }` (or, with the box empty, navigation keys as `{ key }`) to
`/api/widget-control/:database/:prefix/:processId` (Fastify adapter only).

**`useProcessWidget(tree, options?)`** (`packages/optio-ui/src/components/ProcessWidget.tsx`) —
**unchanged in shape**: still `ReactNode | null`, exactly as before suspended-widget support
existed. It now also yields the suspended variant while the process `isSuspended` (same
readiness gate as `useProcessWidgetState` below), so an existing caller that renders its result
directly — e.g. `const widget = useProcessWidget(tree); ...{widget}` — gets the suspended view
automatically, with no code change and no risk of a non-element value (a `{widget, mode}`
object would throw "Objects are not valid as a React child") reaching its render tree.
Implemented as `useProcessWidgetState(tree, options)?.widget ?? null`. `options.readOnly`
(`ProcessDetailView` passes its own) reaches the widget as `WidgetProps.readOnly`.

**`useProcessWidgetState(tree, options?)`** — the layout-free primitive, for callers that need to know
*which* mode produced the element (`ProcessDetailView` uses it to decide whether to show a
Resume control). Returns `{ widget: ReactNode; mode: 'live' | 'suspended' | 'resuming' } | null`; `'resuming'`: the hook showed the process suspended and it is being launched, but its live widget is not ready yet (a relaunch clears `widgetData`), so the suspended variant stays on screen. `null`
when `tree` or `tree.uiWidget` is missing, when the process is neither widget-live nor
suspended, when `tree.widgetData` hasn't arrived, when the database is unknown, or when no
component is registered for the applicable mode. `widget` is the registered component invoked
with the same `WidgetProps` in both modes.

---

### Hooks

**`useProcessActions(options?)`**

```typescript
interface ProcessActionsOptions {
  onResyncSuccess?: (clean: boolean) => void;
}

useProcessActions(options?: ProcessActionsOptions): {
  launch: (processId: string, opts?: { resume?: boolean; params?: Record<string, unknown> }) => void;
  launchAsync: (processId: string, opts?: { resume?: boolean; params?: Record<string, unknown> }) => Promise<Process>;
  cancel: (processId: string) => void;
  dismiss: (processId: string) => void;
  resurrect: (processId: string) => void;
  resync: (metadataFilter?: ProcessMetadataFilter) => void;
  resyncClean: (metadataFilter?: ProcessMetadataFilter) => void;
  isResyncing: boolean;
}
// Every call sends the provider's ?database=&prefix=.
// launch sends body: { sessionId: getSessionId() }, plus resume: true when opts.resume is true and
// params when opts.params is given (a task factory's values: the API makes and launches its child).
// launchAsync sends the same, for an action that shows the outcome: it resolves with the launched
// record (a factory's: the child) and rejects with ts-rest's { status, body: { reason, message } }.
// resurrect POSTs /processes/:id/resurrect with body: { sessionId: getSessionId() }.
```

Note: `processId` arguments are MongoDB `_id` strings (ObjectId hex), not `processId` strings.

**`useProcessConversation(processId, opts?)`**

```typescript
useProcessConversation(
  processId: string,
  opts?: { enabled?: boolean; stoppedAt?: string | null },
): UseQueryResult  // ts-rest react-query result, unmodified (loosely typed `any` — OptioClient
                    // is `any`-typed — but correct in shape at runtime)
```

Reads a suspended process's saved conversation record: `GET /processes/:id/conversation`
(`api.processes.getConversation.useQuery`), proxied by optio-api from the engine's
conversation archive. A 200 appears as `data` (`data.body` is
`{ events: unknown[]; capturedAt: string | null }`); any non-2xx (401/403/404/409/500/503) is
thrown by ts-rest and surfaces as `error` (`error.status`; `error.body` is
`{ reason, message }` except for 401, which is `{ message }`). Never retries a 4xx (transient
errors get up to 2). `opts.enabled: false` fetches nothing at all (neither the conversation nor
the process doc below). `staleTime: Infinity`: a record is fixed for a given suspend (whose
`stoppedAt` is in the key), so a window refocus or a remount never refetches it; an explicit
`refetch()` still does.

Query key includes the process's current `status.stoppedAt` alongside the id, so a
resume-then-resuspend cycle gets a fresh cache entry instead of momentarily showing the
previous suspend's conversation. Pass `opts.stoppedAt` (even `null`) when the caller already
has it — e.g. a suspended widget's own `process.status.stoppedAt` — to skip this hook's
internal `useProcess(processId)` fetch and 5 s poll entirely; omit it to fall back to that
internal lookup.

**`useProcessList(options?)`**

```typescript
useProcessList(options?: {
  refetchInterval?: number | false;
  metadataFilter?: ProcessMetadataFilter;
}): {
  processes: Process[];
  totalCount: number;
  isLoading: boolean;
}
// Default refetchInterval: 5000ms. Fetches limit=50.
```

**`useProcess(id, options?)`**

```typescript
useProcess(id: string | undefined, options?: { refetchInterval?: number | false }): {
  process: Process | null;
  isLoading: boolean;
}
// id is MongoDB _id. Default refetchInterval: 5000ms.
```

**`useProcessTree(id, options?)`**

```typescript
useProcessTree(id: string | undefined, options?: { refetchInterval?: number | false }): ProcessTreeNode | null
// Returns full tree with children nested. Default refetchInterval: 5000ms.
```

**`useProcessTreeLog(id, options?)`**

```typescript
useProcessTreeLog(
  id: string | undefined,
  options?: { refetchInterval?: number | false; limit?: number }
): LogEntry[]
// Default refetchInterval: 5000ms, limit: 100.
```

**`useProcessStream(processId, maxDepth?)`**

```typescript
useProcessStream(processId: string | undefined, maxDepth?: number): {
  processes: ProcessUpdate[];
  connected: boolean;
  tree: ProcessTreeNode | null;
  rootProcess: ProcessUpdate | null;
  logs: LogEntry[];
  processNotFound: boolean;  // the pre-flight GET returned 404; terminal, no retry
  error: Error | null;       // other API errors; the retry loop keeps running
}
// SSE connection to /api/processes/:id/tree/stream?prefix=&maxDepth=&database=
// (inside a MultiProcessStreamProvider it reads that provider's shared stream instead)
// Reconnects automatically after 3s on error.
// maxDepth default: 10
```

**`useProcessListStream(options?)`**

```typescript
useProcessListStream(options?: {
  metadataFilter?: ProcessMetadataFilter;
}): {
  processes: any[];
  connected: boolean;
}
// SSE connection to /api/processes/stream?prefix=&database=&metadataFilter= (module-level singleton — one
// EventSource per app; it reconnects when baseUrl, database, prefix or the filter changes)
// Reconnects automatically after 3s on error. Only one filter active at a time.
```

---

### Types

```typescript
// From optio-ui
export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';

export interface ProcessTreeNode {   // ProcessUpdate (a tree-stream item) + children
  _id: string;
  processId?: string;
  launchMode?: 'resume' | 'fresh';
  parentId: string | null;
  name: string;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable: boolean;
  depth: number;
  order: number;
  metadata?: Record<string, unknown>;
  browserOpenRequests?: { requestId: string; url: string }[];
  autoResumeScheduled?: boolean;
  supportsResume?: boolean;
  hasSavedState?: boolean;
  warning?: string;
  taskFactory?: boolean;     // a task factory's record: its launch opens its launch form
  parameters?: unknown[];    // a task factory's parameters, for that form
  children: ProcessTreeNode[];
}
```

---

### i18n Keys

All components use `react-i18next`. Required keys:

| Key | Used by |
|-----|---------|
| `processes.launch` | ProcessItem (launch button tooltip) |
| `processes.resume` | LaunchControls (primary button label when hasSavedState=true; default "Resume") |
| `processes.restart` | LaunchControls (dropdown menu item; default "Restart (discard saved state)") |
| `processes.resurrect` | LaunchControls (Resurrect button aria-label; default "Resurrect") |
| `processes.resurrectHint` | LaunchControls (Resurrect button tooltip; default "Save the work left by the failed run, then resume") |
| `processes.resumeFromSnapshot` | LaunchControls (Resurrect menu item + confirmation title; default "Resume from last snapshot") |
| `processes.restartDiscarding` | LaunchControls (Resurrect menu item + confirmation title; default "Restart") |
| `processes.discardUnsavedWork` | LaunchControls (confirmation body; default "This discards the unsaved work left by the failed run.") |
| `processes.discardAndContinue` | LaunchControls (confirmation OK button; default "Discard and continue") |
| `processes.resurrecting` | LaunchControls (Resurrecting indicator tooltip; default "Resurrecting: saving the unsaved work…") |
| `processes.resurrectingLabel` | LaunchControls (Resurrecting indicator aria-label; default "Resurrecting") |
| `processes.cancel` | ProcessItem, ProcessTreeView (cancel button tooltip) |
| `processes.suspend` | ProcessItem, ProcessTreeView (stop button tooltip for a resumable process; default "Suspend") |
| `processes.moreOptions` | LaunchControls (split-button menu tooltip; default "More options") |
| `processes.search` | ProcessFilters (search box placeholder) |
| `processes.filterAll` | ProcessFilters |
| `processes.filterActive` | ProcessFilters |
| `processes.filterHideCompleted` | ProcessFilters |
| `processes.filterErrors` | ProcessFilters |
| `processes.showDetails` | ProcessFilters |
| `processes.showSpecial` | ProcessFilters |
| `status.idle` | ProcessStatusBadge |
| `status.scheduled` | ProcessStatusBadge |
| `status.running` | ProcessStatusBadge |
| `status.done` | ProcessStatusBadge |
| `status.failed` | ProcessStatusBadge |
| `status.cancel_requested` | ProcessStatusBadge |
| `status.cancelling` | ProcessStatusBadge |
| `status.cancelled` | ProcessStatusBadge |
| `status.suspending` / `status.suspended` | ProcessStatusBadge (resumable process stopping / stopped with saved state; defaults "Suspending" / "Suspended") |
| `status.autoResumeScheduled` | ProcessStatusBadge (auto-resume clock icon label; default "Scheduled for auto-restart") |
| `common.noData` | ProcessLogPanel (empty state) |
| `processes.factoryTitle` | TaskFactoryLaunchModal (title, `{{name}}` the factory's; default "Launch “{{name}}”") |
| `processes.factoryLaunch` / `processes.factoryCancel` | TaskFactoryLaunchModal (buttons; defaults "Launch" / "Cancel") |
| `processes.factoryRequired` / `processes.factoryMaxLength` | TaskFactoryLaunchModal (a required value missing, a text over `maxLength` with `{{count}}`; defaults "Required" / "At most {{count}} characters") |
| `errors.processes.launch.<reason>` | TaskFactoryLaunchModal via `optioErrorRoutes`, one per `LaunchFailureReason` (`not-found`, `not-launchable`, `no-resume-support`, `launch-blocked`, `shutting-down`, `parameters-required`, `not-a-factory`, `invalid-parameters`); no default (vultus-core's `routeApiError` reads `t(key)`) |

---

## Architecture Notes

- **Authority rule.** `optio-core` is the sole writer to MongoDB. `optio-api` reads MongoDB directly for queries (REST GETs, SSE, widget proxy, discovery) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. Engine is single source of truth for what commands are allowed and what state results. Full statement: top-level README "Authority and data flow".
- **Engine RPC.** clamator over-redis. Engine hosts a `RedisRpcServer` constructed by `optio_core.init()` with `key_prefix=f"{database}/{prefix}"`, registering the `optio-engine` service defined in `optio-contracts/src/optio-engine-to-api.ts`. API uses a `RedisRpcClient` per `(database, prefix)` provided by `createOptioTransports` / `OptioContext.transports`. Apps can register additional services on `optio_core.rpc_server` before calling `optio_core.run()`; consumers (e.g. Excavator) wrap any clamator contract on the cached transport.
- **registerOptioApi return shape.** Sugar form (no `ctx` supplied) returns the constructed `OptioContext`; the host uses `ctx.transports`, `ctx.closeAll()`, `resolveOptioEngine(ctx, query)` as needed. Explicit form (`{ ctx, authenticate }`) returns `void` and leaves lifecycle to the caller. nextjs-app returns `{ GET, POST, ctx? }`; nextjs-pages returns `{ handler, ctx? }`. See `packages/optio-api/README.md`.
- **optio-api internal layer rules.** The `optio-api` package has binding internal layer rules (adapter / handler / context); see `packages/optio-api/AGENTS.md` "Layer rules (binding)".
- **Collection name**: `{prefix}_processes` (MongoDB)
- **No Redis mode**: `init()` with `redis_url=None` and no `rpc_server` disables the command surface; use direct Python API calls (`optio.launch()`, etc.) instead.
- **Progress flushing**: buffered every 100ms; override with `OPTIO_PROGRESS_FLUSH_INTERVAL_MS` env var
- **Child processes**: stored as MongoDB documents with `parentId`/`rootId`; automatically deleted on parent re-launch and dismiss (`delete_descendants`); each carries its parent's `ttlSeconds`
- **Ephemeral processes**: deleted from DB (with their descendants) when their run ends: after reaching an end state, after a force-cancel (once its children are force-cancelled), when a launch finds no execute function, and on `init()` when a restart interrupted them (instead of being reconciled to `failed`)
- **Migrations**: run automatically on `init()` via `mongo-quaestor` (PyPI name; legacy `quaestor` import name); migrations live in `packages/optio-core/src/optio_core/migrations/`
- **Scheduler**: APScheduler-backed; cron schedules defined on `TaskInstance.schedule` are synced on init and resync
- **Process state reconciliation**: on `init()`, any process still in an active state (`scheduled`, `running`, `cancel_requested`, `cancelling`) from a previous session is reset to `failed` with `error="Process was interrupted by server restart"` (an ephemeral one is deleted instead, with its descendants), and every `resurrecting` marker is cleared (no resurrect survives an engine restart). On `shutdown(grace_seconds=None)` (None = `cancel_grace_seconds`, default 5.0), tasks that do not unwind within the grace period are force-finalized to `failed` with `error="Task did not unwind within cancellation grace period"` (an ephemeral one is then deleted). Spec: `docs/2026-04-22-process-reconciliation-design.md`
- **Lost final-state writes**: if MongoDB is unreachable when a process ends, the executor keeps the final state in memory and the supervisor loop retries it (backing off from 1 s to 30 s, no attempt limit) until it lands; the row gets a `"State recorded late: <state> (database was unavailable)"` log entry. `cancel()`, `cancel_and_wait()` and `group_cancel_and_wait()` settle an active row that no task in this engine owns instead of waiting on it (to the queued final state, else `failed` with `error="Process had no running task in this engine; its final state was never recorded"`). Spec: `docs/2026-10-03-lost-final-state-writes-design.md`
- **Process writes**: each lifecycle step of a process is one update on its document -- relaunch (reset, `scheduled` and its log line), start (`running`, its log line and `originatingSessionId`), a progress message and its log line, the end (final status, `expireAt`, its log or error line and the `widgetUpstream` clear), dismiss. Log entries keep their own timestamps and order; nothing is buffered. `store.update_status(..., log=, set_fields=)`, `store.update_progress(..., log=)` and `store.relaunch_reset` carry the combined writes. Spec: `docs/2026-10-09-fewer-process-writes-design.md`
- **Process indexes**: every `init()` ensures six indexes on `{prefix}_processes` (`store.ensure_process_indexes`): `processId`+`_id` desc, `parentId`+`order`, `rootId`+`depth`+`order`, `status.state`, `originatingSessionId`, and the `expireAt` TTL index (`expireAt_ttl`, as migration m004 creates it). An index that conflicts with an existing one is logged and skipped. A relaunch or dismiss removes `expireAt` (a terminal state sets it again), and a child copies its parent's `ttlSeconds`, so a tree expires with its root. Spec: `docs/2026-10-09-process-indexes-design.md`
- **Persistent launch blocks**: `block_launches(..., persist=True)` and `group_cancel*(..., block_new_launches=True, persist=True)` write a record to `{prefix}_launch_blocks` (Mongo); records are reloaded into the in-memory block list on every `init()` so the block survives restarts. Removed only by `unblock_launches(launch_filter)` (exact filter match). When set, `reason` is stored on the record and appended to the `LaunchBlocked` exception message (`...; reason={reason}`). Spec: `docs/2026-04-30-persistent-launch-blocks-design.md`

### Control-plane convergence

Every control verb (launch, cancel, dismiss, resync, group_cancel, …) has **exactly one implementation**: a method on `Optio` in `lifecycle.py`. All external entry points — Python callers, RPC adapters, schedulers, future channels — funnel through that public method.

Adapters do two things and only two things:
1. Translate inbound wire shape to a `(process_id, …)` tuple.
2. Translate the public method's return / raised exception to the adapter's wire result.

State-machine logic, side effects, and authority decisions live on `Optio`, never in adapters. If an adapter needs to short-circuit with a typed reason (e.g. RPC's `not-found` / `not-cancellable`), it pre-flights against shared constants (`CANCELLABLE_STATES`, etc.) and only then delegates — it does not duplicate the state transition.

When you add a new channel, you call existing `Optio` methods. When you add a new verb, you add one method to `Optio` and one thin adapter per existing channel. Never duplicate verb logic across layers.
