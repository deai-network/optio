# Optio — LLM Reference

Async process management library for Python backends with TypeScript API and React UI layers.

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

---

## Integration Levels

| Level | Package | Language | Install |
|-------|---------|----------|---------|
| 1 — Core runtime | `optio-core` | Python | `pip install optio-core` (MongoDB + APScheduler); add `[redis]` for Redis command bus |
| 2 — Remote control | `optio-core[redis]` | Python | `pip install optio-core[redis]` |
| 3 — REST API | `optio-api` | TypeScript | `npm install optio-api optio-contracts` |
| 4 — React UI | `optio-ui` | TypeScript | `npm install optio-ui optio-contracts @tanstack/react-query react-i18next antd` |

Dependencies: Python requires `motor>=3.3.0`, `apscheduler>=4.0.0a5`, `quaestor`. Redis support: `redis>=5.0.0` (optional extra). TypeScript API requires `mongodb`, `ioredis`, `@ts-rest/core`. UI requires React 19+, Ant Design 5+.

---

## Python: optio-core

### Public API

All symbols are available directly from `optio_core` (module-level singleton):

```python
import optio_core

# Lifecycle
await optio_core.init(mongo_db, prefix, redis_url=None, services=None, get_task_definitions=None)
await optio_core.run()          # blocks until shutdown; call after init()
await optio_core.shutdown()     # graceful shutdown; cancels running processes

# Commands
await optio_core.launch(process_id: str) -> None           # fire-and-forget
await optio_core.launch_and_wait(process_id: str) -> None  # blocks until done
await optio_core.cancel(process_id: str) -> None
await optio_core.dismiss(process_id: str) -> None          # reset done/failed/cancelled → idle
await optio_core.resync(clean: bool = False) -> None       # re-sync task definitions; clean=True nukes all records first

# Ad-hoc processes (not from get_task_definitions)
await optio_core.adhoc_define(task: TaskInstance, parent_id: ObjectId | None = None, ephemeral: bool = False) -> dict
await optio_core.adhoc_delete(process_id: str) -> None

# Queries
await optio_core.get_process(process_id: str) -> dict | None
await optio_core.list_processes(state=None, root_id=None, type=None, target_id=None) -> list[dict]

# Custom Redis command handler (call before run())
optio_core.on_command(command_type: str, handler: Callable[..., Awaitable]) -> None
```

**`init()` parameters:**

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `mongo_db` | `AsyncIOMotorDatabase` | required | Motor async database object |
| `prefix` | `str` | required | Namespace for collections (`{prefix}_processes`) and Redis streams (`{prefix}:commands`) |
| `redis_url` | `str \| None` | `None` | If None: command bus disabled; use direct method calls |
| `services` | `dict[str, Any] \| None` | `{}` | Passed as `ctx.services` to all task execute functions |
| `get_task_definitions` | `Callable[..., Awaitable[list[TaskInstance]]] \| None` | `None` | Async function returning task list; called on init and resync |

---

### TaskInstance Fields

```python
@dataclass
class TaskInstance:
    execute: Callable[..., Awaitable[None]]  # async def execute(ctx: ProcessContext) -> None
    process_id: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None              # cron expression, e.g. "0 3 * * *"
    special: bool = False                    # hidden from default UI views when special=True
    warning: str | None = None              # shown as confirmation prompt before launch
    cancellation: CancellationConfig = field(default_factory=CancellationConfig)
```

---

### CancellationConfig Fields

```python
@dataclass
class CancellationConfig:
    cancellable: bool = True
    propagation: str = "down"  # "down" | "up" | "both" | "none"
```

---

### ChildResult Fields

```python
@dataclass
class ChildResult:
    process_id: str
    state: str        # "done" | "failed" | "cancelled"
    error: str | None = None
```

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

### ProcessContext Methods

`ProcessContext` is the sole argument to every task execute function. Signature: `async def execute(ctx: ProcessContext) -> None`.

```python
# Progress reporting
ctx.report_progress(percent: float | None, message: str | None = None) -> None
# percent=None → indeterminate; buffered and flushed every 100ms (OPTIO_PROGRESS_FLUSH_INTERVAL_MS env)
# message is also appended to process log

ctx.should_continue() -> bool
# Returns False when cancellation has been requested; poll this in loops

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
) -> str
# Returns child's final state: "done" | "failed" | "cancelled"
# Blocks until child completes

# Parallel child processes
ctx.parallel_group(
    max_concurrency: int = 10,
    survive_failure: bool = False,
    survive_cancel: bool = False,
    on_child_progress: Callable[[list[ChildProgressInfo]], None] | None = None,
) -> ParallelGroup
# Use as async context manager; raises RuntimeError if any child fails (unless survive_failure=True)

# Public fields (read-only use)
ctx.process_id: str
ctx.params: dict[str, Any]
ctx.metadata: dict[str, Any]
ctx.services: dict[str, Any]
```

**ParallelGroup usage:**

```python
async with ctx.parallel_group(max_concurrency=5) as group:
    for item in items:
        await group.spawn(execute_fn, process_id=item.id, name=item.name, params={...})
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

---

### MongoDB Document Schema

Collection: `{prefix}_processes`

| Field | Type | Description |
|-------|------|-------------|
| `_id` | ObjectId | MongoDB document ID |
| `processId` | string | Application-defined unique identifier |
| `name` | string | Human-readable display name |
| `params` | object | Static parameters from TaskInstance |
| `metadata` | object | Arbitrary metadata; `metadata.targetId` used by `list_processes(target_id=...)` filter |
| `parentId` | ObjectId \| null | Parent process `_id`; null for root processes |
| `rootId` | ObjectId | Root process `_id`; equals `_id` for root processes |
| `depth` | int | Tree depth; 0 for root |
| `order` | int | Sort order among siblings |
| `cancellable` | bool | Whether cancel is permitted |
| `adhoc` | bool | True if created via `adhoc_define()` |
| `ephemeral` | bool | True if process should be deleted after completion |
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
| `createdAt` | datetime | Document creation timestamp |

---

## TypeScript: optio-contracts

Package: `optio-contracts`

### Schemas

**`ProcessSchema`** — full process document:
- `_id`: ObjectId (24-char hex string)
- `processId`: string
- `name`: string
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

`processesContract` — ts-rest router. All paths prefixed with `/processes`.

| Name | Method | Path | Path Params | Query | Body | Responses |
|------|--------|------|-------------|-------|------|-----------|
| `list` | GET | `/processes/:prefix` | `prefix` | `cursor?, limit, rootId?, type?, state?, targetId?` | — | `200: PaginatedResponse<Process>` |
| `get` | GET | `/processes/:prefix/:id` | `prefix, id` | — | — | `200: Process`, `404: Error` |
| `getTree` | GET | `/processes/:prefix/:id/tree` | `prefix, id` | `maxDepth?: number` | — | `200: ProcessTreeNode`, `404: Error` |
| `getLog` | GET | `/processes/:prefix/:id/log` | `prefix, id` | `cursor?, limit` | — | `200: PaginatedResponse<LogEntry>`, `404: Error` |
| `getTreeLog` | GET | `/processes/:prefix/:id/tree/log` | `prefix, id` | `cursor?, limit, maxDepth?` | — | `200: PaginatedResponse<LogEntry & {processId, processLabel}>`, `404: Error` |
| `launch` | POST | `/processes/:prefix/:id/launch` | `prefix, id` | — | (none) | `200: Process`, `404: Error`, `409: Error` |
| `cancel` | POST | `/processes/:prefix/:id/cancel` | `prefix, id` | — | (none) | `200: Process`, `404: Error`, `409: Error` |
| `dismiss` | POST | `/processes/:prefix/:id/dismiss` | `prefix, id` | — | (none) | `200: Process`, `404: Error`, `409: Error` |
| `resync` | POST | `/processes/:prefix/resync` | `prefix` | — | `{ clean?: boolean }` | `200: { message: string }` |

Note: The Fastify adapter mounts the entire contract under `/api`, so effective paths are `/api/processes/:prefix/...`.

---

## TypeScript: optio-api

Package: `optio-api`. Framework-agnostic handlers + Fastify adapter.

### Entry Points

```typescript
// Handlers (framework-agnostic)
export { listProcesses, getProcess, getProcessTree, getProcessLog, getProcessTreeLog,
         launchProcess, cancelProcess, dismissProcess, resyncProcesses } from 'optio-api';
export type { ListQuery, PaginationQuery, TreeLogQuery, CommandResult } from 'optio-api';

// Publishers (for domain code to trigger commands via Redis)
export { publishLaunch, publishResync } from 'optio-api';

// Stream poller (for custom SSE adapters)
export { createListPoller, createTreePoller } from 'optio-api';
export type { StreamPollerOptions, TreePollerOptions, ListPollerHandle } from 'optio-api';
```

### OptioApiOptions

```typescript
interface OptioApiOptions {
  db: Db;       // mongodb Db instance
  redis: Redis; // ioredis Redis instance
  prefix: string;
}
```

### Fastify Adapter

```typescript
import { registerProcessRoutes, registerProcessStream } from 'optio-api/adapters/fastify';

registerProcessRoutes(app: FastifyInstance, opts: OptioApiOptions): void
// Registers all REST endpoints from processesContract under /api/processes/...

registerProcessStream(app: FastifyInstance, opts: OptioApiOptions): void
// Registers two SSE endpoints:
//   GET /api/processes/:prefix/:id/tree/stream?maxDepth=N
//   GET /api/processes/:prefix/stream
```

### Handlers (all signatures)

```typescript
interface ListQuery {
  cursor?: string; limit: number; rootId?: string; type?: string;
  state?: string; targetId?: string;
}
interface PaginationQuery { cursor?: string; limit: number; }
interface TreeLogQuery extends PaginationQuery { maxDepth?: number; }

type CommandResult =
  | { status: 200; body: any }
  | { status: 404; body: { message: string } }
  | { status: 409; body: { message: string } };

async function listProcesses(db: Db, prefix: string, query: ListQuery): Promise<PaginatedResponse>
async function getProcess(db: Db, prefix: string, id: string): Promise<Process | null>
async function getProcessTree(db: Db, prefix: string, id: string, maxDepth?: number): Promise<ProcessTreeNode | null>
async function getProcessLog(db: Db, prefix: string, id: string, query: PaginationQuery): Promise<PaginatedResponse<LogEntry> | null>
async function getProcessTreeLog(db: Db, prefix: string, id: string, query: TreeLogQuery): Promise<PaginatedResponse | null>
async function launchProcess(db: Db, redis: Redis, prefix: string, id: string): Promise<CommandResult>
async function cancelProcess(db: Db, redis: Redis, prefix: string, id: string): Promise<CommandResult>
async function dismissProcess(db: Db, redis: Redis, prefix: string, id: string): Promise<CommandResult>
async function resyncProcesses(redis: Redis, prefix: string, clean?: boolean): Promise<{ message: string }>
```

### Publishers

Write commands to Redis stream `{prefix}:commands`. Used by domain code that needs to trigger processes without HTTP.

```typescript
async function publishLaunch(redis: Redis, prefix: string, processId: string): Promise<void>
async function publishResync(redis: Redis, prefix: string, clean?: boolean): Promise<void>
```

Note: `publishCancel` and `publishDismiss` exist in the source but are not re-exported from the package entry point. Use the REST API or handler functions for cancel/dismiss.

### Stream Poller

Used internally by SSE endpoints. Poll interval: 1000ms. Sends change events only (snapshot diffing).

```typescript
interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
}

interface TreePollerOptions extends StreamPollerOptions {
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
```

**List stream process shape:**
```typescript
{ _id, processId, name, status, progress, cancellable, special, warning, metadata, depth }
```

**Tree stream process shape:**
```typescript
{ _id, parentId: string | null, name, status, progress, cancellable, depth, order }
```

---

## TypeScript: optio-ui

Package: `optio-ui`. React components and hooks. Requires `OptioProvider` at root.

### OptioProvider Props

```typescript
interface OptioProviderProps {
  prefix: string;
  baseUrl?: string;  // default: '' (same origin)
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

**`ProcessList`**

```typescript
interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}
```

Ant Design `List` of `ProcessItem`. Shows name, status badge, progress bar, launch/cancel buttons.

**`ProcessItem`**

```typescript
interface ProcessItemProps {
  process: any;
  onLaunch?: (id: string) => void;
  onCancel?: (id: string) => void;
  readonly?: boolean;
  onProcessClick?: (id: string) => void;
}
```

Single process row. Launch button shows `Popconfirm` when `process.warning` is set.

**`ProcessStatusBadge`**

```typescript
interface ProcessStatusBadgeProps {
  state: string;
  error?: string;
  runningSince?: string | null;
}
```

Ant Design `Tag` with state-based color. Active states show elapsed time (live ticker). Failed state shows error tooltip icon.

State → color mapping:
- `idle` → `default`
- `scheduled` → `cyan`
- `running` → `blue`
- `done` → `green`
- `failed` → `red`
- `cancel_requested` → `orange`
- `cancelling` → `orange`
- `cancelled` → `default`

**`ProcessTreeView`**

```typescript
interface ProcessTreeViewProps {
  treeData: ProcessNode | null;
  sseState: { connected: boolean };
  onCancel?: (processId: string) => void;
}
```

Ant Design `Tree` rendering process hierarchy with status badges, progress bars, and cancel buttons. Has built-in "Hide finished sub-tasks" toggle (default: on).

`ProcessNode` shape expected by this component:
```typescript
interface ProcessNode {
  _id: string;
  name: string;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  children?: ProcessNode[];
}
```

**`ProcessLogPanel`**

```typescript
interface ProcessLogPanelProps {
  logs: LogEntry[];  // { timestamp: string; level: string; message: string; processName?: string }
}
```

Scrollable log viewer (max-height 400px, monospace). Auto-scrolls to bottom unless user has scrolled up. Level → color: `event`→cyan, `info`→blue, `debug`→default, `warning`→gold, `error`→red.

**`ProcessFilters`**

```typescript
interface ProcessFiltersProps {
  filterGroup: FilterGroup;
  onFilterChange: (group: FilterGroup) => void;
  showDetails: boolean;
  onShowDetailsChange: (show: boolean) => void;
  showSpecial: boolean;
  onShowSpecialChange: (show: boolean) => void;
}

type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';
```

---

### Hooks

**`useProcessActions(options?)`**

```typescript
interface ProcessActionsOptions {
  onResyncSuccess?: (clean: boolean) => void;
}

useProcessActions(options?: ProcessActionsOptions): {
  launch: (processId: string) => void;
  cancel: (processId: string) => void;
  dismiss: (processId: string) => void;
  resync: () => void;
  resyncClean: () => void;
  isResyncing: boolean;
}
```

Note: `processId` arguments are MongoDB `_id` strings (ObjectId hex), not `processId` strings.

**`useProcessList(options?)`**

```typescript
useProcessList(options?: { refetchInterval?: number | false }): {
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

**`useSourceProcesses(sourceId, options?)`**

```typescript
useSourceProcesses(sourceId: string, options?: { refetchInterval?: number | false }): {
  processes: Process[];
  isLoading: boolean;
}
// Filters by metadata.targetId === sourceId. Default refetchInterval: 10000ms, limit: 20.
```

**`useProcessStream(processId, maxDepth?)`**

```typescript
useProcessStream(processId: string | undefined, maxDepth?: number): {
  processes: ProcessUpdate[];
  connected: boolean;
  tree: ProcessTreeNode | null;
  rootProcess: ProcessUpdate | null;
  logs: LogEntry[];
}
// SSE connection to /api/processes/:prefix/:id/tree/stream
// Reconnects automatically after 3s on error.
// maxDepth default: 10
```

**`useProcessListStream()`**

```typescript
useProcessListStream(): {
  processes: any[];
  connected: boolean;
}
// SSE connection to /api/processes/:prefix/stream (module-level singleton — one connection per prefix/baseUrl pair)
// Reconnects automatically after 3s on error.
```

---

### Types

```typescript
// From optio-ui
export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';

export interface ProcessTreeNode {
  _id: string;
  parentId: string | null;
  name: string;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable: boolean;
  depth: number;
  order: number;
  children: ProcessTreeNode[];
}
```

---

### i18n Keys

All components use `react-i18next`. Required keys:

| Key | Used by |
|-----|---------|
| `processes.launch` | ProcessItem (launch button tooltip) |
| `processes.cancel` | ProcessItem, ProcessTreeView (cancel button tooltip) |
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
| `common.noData` | ProcessLogPanel (empty state) |

---

## Architecture Notes

- **Collection name**: `{prefix}_processes` (MongoDB)
- **Redis stream**: `{prefix}:commands` — messages have `type` and `payload` (JSON string) fields
- **No Redis mode**: `init()` with `redis_url=None` disables command consumer; use direct Python API calls (`optio.launch()`, etc.) instead of REST
- **Progress flushing**: buffered every 100ms; override with `OPTIO_PROGRESS_FLUSH_INTERVAL_MS` env var
- **Child processes**: stored as MongoDB documents with `parentId`/`rootId`; automatically deleted on parent re-launch (`clear_result_fields`)
- **Ephemeral processes**: deleted from DB after reaching an end state
- **Migrations**: run automatically on `init()` via quaestor; migrations live in `packages/optio-core/src/optio_core/migrations/`
- **Scheduler**: APScheduler-backed; cron schedules defined on `TaskInstance.schedule` are synced on init and resync
