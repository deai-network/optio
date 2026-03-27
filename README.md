# optio

Optio is a reusable async process management library for Python. It provides a framework for defining, launching, cancelling, and monitoring long-running tasks backed by MongoDB for persistence, with optional Redis integration for multi-worker command ingestion. Processes support hierarchical parent-child relationships, progress reporting, cooperative cancellation, cron scheduling, and ad-hoc dynamic task creation.

## Integration Levels

Optio is designed as a progressive stack. Each level adds capability (and a dependency).

### Level 1: Python Core (MongoDB only)

**What you get:** Define async tasks, launch/cancel/dismiss them, track progress with percent and message, create child processes (sequential and parallel), cron scheduling, query processes. All via direct Python method calls.

**Requirements:** Python 3.11+, MongoDB

**Install:**

```bash
pip install optio-core
```

**Minimal example:**

```python
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from optio import init, launch_and_wait, get_process, TaskInstance

async def my_task(ctx):
    for i in range(10):
        if not ctx.should_continue():
            return
        ctx.report_progress(i * 10, f"Step {i + 1}/10")
        await asyncio.sleep(1)
    ctx.report_progress(100, "Done")

async def get_tasks(services):
    return [
        TaskInstance(
            execute=my_task,
            process_id="my-task",
            name="My Task",
        ),
    ]

async def main():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["myapp"]

    await init(
        mongo_db=db,
        prefix="myapp",
        get_task_definitions=get_tasks,
    )

    await launch_and_wait("my-task")
    proc = await get_process("my-task")
    print(proc["status"]["state"])  # "done"

asyncio.run(main())
```

### Level 2: + Redis

**Adds:** External command ingestion via Redis Streams, multi-worker support, custom command handlers.

**Install:**

```bash
pip install optio-core[redis]
```

**Example:**

```python
from optio import init, run, on_command

async def handle_custom(payload):
    print(f"Received: {payload}")

async def main():
    await init(
        mongo_db=db,
        prefix="myapp",
        redis_url="redis://localhost:6379",
        get_task_definitions=get_tasks,
    )

    on_command("my_custom_command", handle_custom)

    await run()  # Blocks, listens for commands on Redis stream "myapp:commands"
```

With Redis enabled, external systems can publish commands (launch, cancel, dismiss, resync, or custom) to the `{prefix}:commands` Redis stream. The `run()` method blocks and processes commands until `shutdown()` is called.

### Level 3: + REST API (optio-api)

**Adds:** HTTP endpoints for process management, SSE streams for real-time status updates.

**Install:**

```
npm install optio-api optio-contracts
```

**Example (Fastify):**

```typescript
import Fastify from "fastify";
import { registerProcessRoutes } from "optio-api/fastify";
import { registerProcessStream } from "optio-api/fastify";

const app = Fastify();

await registerProcessRoutes(app, { db, redis, prefix: "myapp" });
await registerProcessStream(app, { db, prefix: "myapp" });

await app.listen({ port: 3000 });
```

See [`packages/optio-api/README.md`](../optio-api/README.md) for the full endpoint reference.

### Level 4: + Web UI (optio-ui)

**Adds:** Pre-built React components for process monitoring: process list, tree view, progress bars, action buttons.

**Install:**

```
npm install optio-ui
```

**Example:**

```tsx
import { OptioProvider, ProcessList } from "optio-ui";

function App() {
  return (
    <OptioProvider baseUrl="/api">
      <ProcessList
        onLaunch={(id) => console.log("launch", id)}
        onCancel={(id) => console.log("cancel", id)}
      />
    </OptioProvider>
  );
}
```

See [`packages/optio-ui/README.md`](../optio-ui/README.md) for component documentation.

## Concepts

### Processes and the State Machine

Every process has a state that follows a strict state machine:

```
idle --> scheduled --> running --> done
                          |         |
                          v         v
                        failed    idle (dismiss)
                          |
                          v
                        idle (dismiss)

Cancel path:
  scheduled --> cancelled
  running --> cancel_requested --> cancelling --> cancelled
  cancelled --> idle (dismiss)
```

**State groups:**

| Group | States | Description |
|-------|--------|-------------|
| Active | `scheduled`, `running`, `cancel_requested`, `cancelling` | Process is in progress |
| End | `done`, `failed`, `cancelled` | Terminal states from a run |
| Launchable | `idle`, `done`, `failed`, `cancelled` | Can be (re-)launched |
| Cancellable | `scheduled`, `running` | Can receive a cancel request |
| Dismissable | `done`, `failed`, `cancelled` | Can be reset to `idle` |

**Valid transitions:**

| From | To |
|------|----|
| `idle` | `scheduled` |
| `scheduled` | `running`, `cancel_requested` |
| `running` | `done`, `failed`, `cancel_requested` |
| `done` | `scheduled`, `idle` |
| `failed` | `scheduled`, `idle` |
| `cancel_requested` | `cancelling` |
| `cancelling` | `cancelled` |
| `cancelled` | `scheduled`, `idle` |

### Task Definitions and the Task Generator

Tasks are defined as `TaskInstance` objects. Rather than registering tasks imperatively, you provide a `get_task_definitions` async callback that returns the full list of tasks. Optio calls this function on `init()` and on every `resync()`, syncing the returned list with MongoDB: new tasks are created, removed tasks are deleted (if idle), and metadata on existing tasks is updated without disturbing runtime state.

```python
async def get_tasks(services):
    sources = await services["db"].sources.find().to_list(None)
    return [
        TaskInstance(
            execute=fetch_source,
            process_id=f"fetch-{s['_id']}",
            name=f"Fetch {s['name']}",
            params={"source_id": str(s["_id"])},
            metadata={"targetId": str(s["_id"])},
            schedule="0 */6 * * *",  # Every 6 hours
        )
        for s in sources
    ]
```

### ProcessContext

Every task `execute` function receives a single `ProcessContext` argument. This is the task's interface to optio:

| Property/Method | Description |
|----------------|-------------|
| `ctx.process_id` | The process ID string |
| `ctx.params` | The params dict from the task definition |
| `ctx.metadata` | The metadata dict from the task definition |
| `ctx.services` | The services dict passed to `init()` |
| `ctx.report_progress(percent, message)` | Update progress (see below) |
| `ctx.should_continue()` | Returns `False` if cancellation requested |
| `ctx.run_child(...)` | Run a sequential child process |
| `ctx.parallel_group(...)` | Create a parallel execution group |
| `ctx.mark_ephemeral()` | Mark this process for deletion after completion |

### Child Processes

Tasks can spawn child processes that appear as a tree in the database. Children have their own state, progress, and logs.

**Sequential children** with `run_child`:

```python
async def parent_task(ctx):
    ctx.report_progress(0, "Starting phase 1")
    state = await ctx.run_child(
        execute=phase_one,
        process_id=f"{ctx.process_id}/phase-1",
        name="Phase 1",
        params={"key": "value"},
        survive_failure=False,  # Raise if child fails (default)
        survive_cancel=False,   # Raise if child is cancelled (default)
    )
    # state is "done", "failed", or "cancelled"

    ctx.report_progress(50, "Starting phase 2")
    await ctx.run_child(
        execute=phase_two,
        process_id=f"{ctx.process_id}/phase-2",
        name="Phase 2",
    )
    ctx.report_progress(100, "Complete")
```

**Parallel children** with `parallel_group`:

```python
async def parent_task(ctx):
    async with ctx.parallel_group(
        max_concurrency=5,
        survive_failure=True,   # Continue even if some children fail
        survive_cancel=False,
    ) as group:
        for i, item in enumerate(items):
            await group.spawn(
                execute=process_item,
                process_id=f"{ctx.process_id}/item-{i}",
                name=f"Process {item['name']}",
                params={"item": item},
            )

    # group.results is a list of ChildResult after the group completes
    failed = [r for r in group.results if r.state != "done"]
    ctx.report_progress(100, f"Done, {len(failed)} failures")
```

When `survive_failure=False` (the default for `parallel_group`), a `RuntimeError` is raised when the group's async context exits if any child failed or was cancelled.

### Progress Reporting

Call `ctx.report_progress(percent, message)` from your task function:

- `percent`: `float` from 0 to 100, or `None` for indeterminate progress.
- `message`: Optional `str` describing the current step.

Progress writes are **throttled**: updates are buffered and flushed to MongoDB at most every 100ms (configurable via the `OPTIO_PROGRESS_FLUSH_INTERVAL_MS` environment variable). A final flush occurs automatically when the process completes. Messages are also appended to the process log.

**Progress helpers** for child-to-parent progress mapping (from `optio.progress_helpers`):

| Helper | Usage |
|--------|-------|
| `sequential_progress(ctx, total_children)` | Divides parent 0-100% into equal slots for N sequential children |
| `average_progress(ctx)` | Parent percent = average of all children's percent |
| `mapped_progress(ctx, range_start, range_end)` | Maps a single child's 0-100% into a sub-range of the parent (e.g., 0.0-0.25 = first 25%) |

These return callbacks suitable for the `on_child_progress` parameter of `run_child` and `parallel_group`.

### Cooperative Cancellation

Cancellation is cooperative. When a cancel request arrives:

1. The process state transitions to `cancel_requested`, then `cancelling`.
2. An internal flag is set.
3. The task function must check `ctx.should_continue()` periodically and return early if it is `False`.
4. Cancellation propagates to child processes automatically.

If a task never checks `should_continue()`, it cannot be cancelled (it will remain in `cancelling` state until it finishes naturally).

### Scheduling

Tasks with a `schedule` field are registered with APScheduler as cron jobs. The schedule is a standard cron expression (5 fields: minute, hour, day-of-month, month, day-of-week).

```python
TaskInstance(
    execute=nightly_cleanup,
    process_id="nightly-cleanup",
    name="Nightly Cleanup",
    schedule="0 2 * * *",  # 2:00 AM daily
)
```

Schedules are synced on `init()` and `resync()`. The scheduler starts when `run()` is called.

### Ad-hoc Processes

Ad-hoc processes are created at runtime rather than from the task generator. They are useful for one-off operations or dynamically spawned work.

```python
proc = await adhoc_define(
    task=TaskInstance(
        execute=one_off_task,
        process_id="one-off-123",
        name="One-off Import",
    ),
    ephemeral=True,  # Auto-delete after completion
)

await launch("one-off-123")
```

Ad-hoc processes can also be children of existing processes by passing `parent_id`. Use `adhoc_delete(process_id)` to remove an ad-hoc process and its descendants.

### MongoDB Document Schema

All processes are stored in the `{prefix}_processes` collection. Each document has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `_id` | `ObjectId` | MongoDB document ID |
| `processId` | `string` | Unique process identifier |
| `name` | `string` | Human-readable process name |
| `params` | `object` | Parameters passed to the execute function |
| `metadata` | `object` | Application-defined metadata (e.g., `targetId`) |
| `parentId` | `ObjectId \| null` | Parent process ID (`null` for root processes) |
| `rootId` | `ObjectId` | Root ancestor process ID (self for root processes) |
| `depth` | `int` | Nesting depth (0 for root) |
| `order` | `int` | Sibling order among children |
| `cancellable` | `bool` | Whether this process accepts cancel requests |
| `special` | `bool` | Application-defined flag for special display treatment |
| `warning` | `string \| null` | Warning message shown before launch |
| `adhoc` | `bool` | Whether this process was created via `adhoc_define` |
| `ephemeral` | `bool` | Whether this process is deleted after completion |
| `status` | `object` | Runtime status (see below) |
| `progress` | `object` | Current progress (see below) |
| `log` | `array` | Log entries from the current/last run |
| `createdAt` | `datetime` | Document creation timestamp |

**`status` sub-document:**

| Field | Type | Description |
|-------|------|-------------|
| `state` | `string` | Current state (see state machine) |
| `error` | `string \| null` | Error message if failed |
| `runningSince` | `datetime \| null` | When the process started running |
| `doneAt` | `datetime \| null` | When the process completed successfully |
| `duration` | `float \| null` | Run duration in seconds |
| `failedAt` | `datetime \| null` | When the process failed |
| `stoppedAt` | `datetime \| null` | When the process was cancelled |

**`progress` sub-document:**

| Field | Type | Description |
|-------|------|-------------|
| `percent` | `float \| null` | 0-100, or `null` for indeterminate |
| `message` | `string \| null` | Current progress message |

**`log` entries:**

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `string` | ISO 8601 timestamp |
| `level` | `string` | Log level (e.g., `"info"`) |
| `message` | `string` | Log message |
| `data` | `object` | Optional structured data |

## Python API Reference

### Lifecycle

#### `init()`

```python
async def init(
    mongo_db: AsyncIOMotorDatabase,
    prefix: str,
    redis_url: str | None = None,
    services: dict[str, Any] | None = None,
    get_task_definitions: Callable[..., Awaitable[list[TaskInstance]]] | None = None,
) -> None
```

Initialize optio. Must be called before any other function.

| Parameter | Description |
|-----------|-------------|
| `mongo_db` | Motor async MongoDB database instance |
| `prefix` | Namespace prefix for MongoDB collections (`{prefix}_processes`) and Redis streams (`{prefix}:commands`) |
| `redis_url` | Redis connection URL. If `None`, Redis features are disabled and processes are managed via direct method calls only |
| `services` | Dict of application services passed through to task execute functions via `ctx.services` |
| `get_task_definitions` | Async callback `(services) -> list[TaskInstance]` that returns the current set of task definitions. Called on init and resync |

Runs database migrations automatically on first call.

#### `run()`

```python
async def run() -> None
```

Start the main event loop. Blocks until `shutdown()` is called. Starts the cron scheduler and, if Redis is configured, begins consuming commands from the Redis stream. Installs signal handlers for `SIGTERM` and `SIGINT` that trigger graceful shutdown.

Without Redis, `run()` simply blocks until `shutdown()` is called (useful for scheduler-only deployments).

#### `shutdown()`

```python
async def shutdown() -> None
```

Initiate graceful shutdown. Stops the command consumer and scheduler, sets cancellation flags on all running processes, waits up to 5 seconds for them to exit, and closes the Redis connection.

### Process Management

#### `launch()`

```python
async def launch(process_id: str) -> None
```

Fire-and-forget launch. The process begins execution in a background task. Returns immediately. The process must be in a launchable state (`idle`, `done`, `failed`, or `cancelled`).

#### `launch_and_wait()`

```python
async def launch_and_wait(process_id: str) -> None
```

Launch a process and wait for it to complete. Blocks until the process reaches a terminal state (`done`, `failed`, or `cancelled`). Useful for scripting and tests.

#### `cancel()`

```python
async def cancel(process_id: str) -> None
```

Cancel a running or scheduled process. If the process is `scheduled`, it transitions directly to `cancelled`. If `running`, it transitions through `cancel_requested` and `cancelling`, and the cancellation flag is set for cooperative cancellation.

#### `dismiss()`

```python
async def dismiss(process_id: str) -> None
```

Reset a completed process back to `idle`. Only works on processes in a dismissable state (`done`, `failed`, or `cancelled`). Clears previous run's result fields (status timestamps, progress, logs) and deletes all descendant child processes.

#### `resync()`

```python
async def resync(clean: bool = False) -> None
```

Re-run the task generator and sync definitions with the database.

| Parameter | Description |
|-----------|-------------|
| `clean` | If `True`, delete all process records before re-syncing (nuclear option) |

### Querying

#### `get_process()`

```python
async def get_process(process_id: str) -> dict | None
```

Get a single process document by its `processId` string. Returns the full MongoDB document or `None` if not found.

#### `list_processes()`

```python
async def list_processes(
    state: str | None = None,
    root_id: str | None = None,
    type: str | None = None,
    target_id: str | None = None,
) -> list[dict]
```

List processes with optional filters. Results are sorted by `depth`, `order`, then `_id`.

| Parameter | Description |
|-----------|-------------|
| `state` | Filter by `status.state` (e.g., `"running"`, `"done"`) |
| `root_id` | Filter by `rootId` (as a string; converted to ObjectId internally) |
| `type` | Filter by `type` field |
| `target_id` | Filter by `metadata.targetId` |

### Commands (requires Redis)

#### `on_command()`

```python
def on_command(command_type: str, handler: Callable[..., Awaitable]) -> None
```

Register a custom command handler. The handler receives the command payload dict. Must be called after `init()` but before `run()`. Raises `RuntimeError` if Redis is not configured.

Built-in commands (`launch`, `cancel`, `dismiss`, `resync`) are registered automatically.

### Ad-hoc Processes

#### `adhoc_define()`

```python
async def adhoc_define(
    task: TaskInstance,
    parent_id: ObjectId | None = None,
    ephemeral: bool = False,
) -> dict
```

Create an ad-hoc process at runtime. Returns the MongoDB process document.

| Parameter | Description |
|-----------|-------------|
| `task` | `TaskInstance` with execute function, process_id, name, etc. |
| `parent_id` | If set, creates the process as a child of the given parent (by MongoDB `_id`) |
| `ephemeral` | If `True`, the process is automatically deleted after it reaches a terminal state |

The process starts in `idle` state. Use `launch()` or `launch_and_wait()` to start it.

#### `adhoc_delete()`

```python
async def adhoc_delete(process_id: str) -> None
```

Delete an ad-hoc process and all its descendants from MongoDB. Also removes it from the internal task registry. No-op if the process does not exist.

### Data Types

#### `TaskInstance`

```python
@dataclass
class TaskInstance:
    execute: Callable[..., Awaitable[None]]
    process_id: str
    name: str
    params: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    schedule: str | None = None
    special: bool = False
    warning: str | None = None
    cancellation: CancellationConfig = CancellationConfig()
```

| Field | Description |
|-------|-------------|
| `execute` | Async function `(ctx: ProcessContext) -> None` that implements the task |
| `process_id` | Unique string identifier for this process |
| `name` | Human-readable display name |
| `params` | Parameters passed to the execute function via `ctx.params` |
| `metadata` | Application metadata stored on the process document |
| `schedule` | Cron expression (5 fields) for automatic scheduling, or `None` |
| `special` | Flag for application-defined special display treatment |
| `warning` | Warning message shown to users before launch confirmation |
| `cancellation` | Cancellation behavior configuration |

#### `CancellationConfig`

```python
@dataclass
class CancellationConfig:
    cancellable: bool = True
    propagation: str = "down"  # "down", "up", "both", "none"
```

| Field | Description |
|-------|-------------|
| `cancellable` | Whether this process can be cancelled |
| `propagation` | Direction of cancellation propagation: `"down"` (to children), `"up"` (to parent), `"both"`, or `"none"` |

#### `ChildResult`

```python
@dataclass
class ChildResult:
    process_id: str
    state: str       # "done", "failed", or "cancelled"
    error: str | None = None
```

Returned in `ParallelGroup.results` after all children complete.

#### `ProcessContext`

The context object passed to every task execute function. See the [ProcessContext section](#processcontext) for the full method/property table.

**`run_child()` signature:**

```python
async def run_child(
    execute: Callable[..., Awaitable[None]],
    process_id: str,
    name: str,
    params: dict[str, Any] | None = None,
    survive_failure: bool = False,
    survive_cancel: bool = False,
    on_child_progress: Callable | None = None,
) -> str  # returns terminal state: "done", "failed", or "cancelled"
```

**`parallel_group()` signature:**

```python
def parallel_group(
    max_concurrency: int = 10,
    survive_failure: bool = False,
    survive_cancel: bool = False,
    on_child_progress: Callable | None = None,
) -> ParallelGroup
```

Returns an async context manager. Inside the context, call `await group.spawn(execute, process_id, name, params)` to add children. Children run concurrently up to `max_concurrency`. After the context exits, `group.results` contains a `list[ChildResult]`.

## TypeScript Packages

Optio's TypeScript layer is split into three packages:

| Package | Purpose | Key Exports |
|---------|---------|-------------|
| **optio-contracts** | Shared API contract (ts-rest + Zod schemas) | Route definitions, Zod schemas for process documents, SSE event types |
| **optio-api** | Server-side HTTP endpoints and SSE streams | `registerProcessRoutes`, `registerProcessStream` (Fastify adapter) |
| **optio-ui** | React components for process monitoring | `OptioProvider`, `ProcessList`, `ProcessTreeView`, progress/status components |

**When do you need which?**

- Building a backend that exposes process management over HTTP: `optio-contracts` + `optio-api`
- Building a frontend that displays process state: `optio-contracts` + `optio-ui`
- Full stack: all three

See each package's README for detailed documentation:
- [`packages/optio-contracts/README.md`](../optio-contracts/README.md)
- [`packages/optio-api/README.md`](../optio-api/README.md)
- [`packages/optio-ui/README.md`](../optio-ui/README.md)
