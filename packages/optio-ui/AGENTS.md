# optio-ui — LLM Reference

## Package

- **name**: `optio-ui`
- **version**: `0.1.0`
- **type**: `module` (ESM)
- **entry**: `src/index.ts`

**Dependencies** (bundled):
- `optio-contracts: workspace:*`
- `@ts-rest/core: ^3.51.0`
- `@ts-rest/react-query: ^3.51.0`
- `@ant-design/icons: ^5.6.0`
- `antd: ^5.29.3`

**Peer dependencies** (must be provided by the consuming app):
- `react: >=18`
- `react-dom: >=18`
- `@tanstack/react-query: >=5`
- `react-i18next: >=15`
- `i18next: >=24`

## OptioProvider

```ts
interface OptioProviderProps {
  prefix: string;       // process namespace prefix sent to all API calls
  baseUrl?: string;     // API base URL, default ''
  children: ReactNode;
}
```

Creates a React context containing `{ prefix, baseUrl, client }`. Must wrap all
components and hooks from this package. Requires `QueryClientProvider` from
`@tanstack/react-query` to be present higher in the tree.

Internal context value shape:
```ts
interface OptioContextValue {
  prefix: string;
  baseUrl: string;
  client: OptioClient;  // ts-rest client created from baseUrl
}
```

## Components

### ProcessList

```ts
interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}
```

Renders an Ant Design `List`. Each item delegates to `ProcessItem`.

Launchable states: `idle | done | failed | cancelled`.
Active states: `running | scheduled | cancel_requested | cancelling`.

---

### ProcessItem

```ts
interface ProcessItemProps {
  process: any;
  onLaunch?: (id: string) => void;
  onCancel?: (id: string) => void;
  readonly?: boolean;
  onProcessClick?: (id: string) => void;
}
```

Single process row. Behavioral rules:
- Launch button visible when `!readonly && state in LAUNCHABLE_STATES && onLaunch provided`.
- If `process.warning` is set, launch button is wrapped in `Popconfirm` requiring confirmation.
- Cancel button visible when `!readonly && state in ACTIVE_STATES && process.cancellable && onCancel provided`.
- Progress bar: active + `progress.percent != null` → determinate bar; active + no percent → indeterminate animated gradient bar; not active → hidden.
- Name rendered as `Button[type=link]` when `onProcessClick` provided, plain `Text` otherwise.
- Progress message shown inline (blue) when process is active and `progress.message` is set.

---

### ProcessStatusBadge

```ts
interface ProcessStatusBadgeProps {
  state: string;
  error?: string;
  runningSince?: string | null;  // ISO datetime string
}
```

Renders a colored Ant Design `Tag`. State-to-color mapping:

| state | color |
|-------|-------|
| `idle` | `default` |
| `scheduled` | `cyan` |
| `running` | `blue` |
| `done` | `green` |
| `failed` | `red` |
| `cancel_requested` | `orange` |
| `cancelling` | `orange` |
| `cancelled` | `default` |

Active states (`running | scheduled | cancel_requested | cancelling`): shows live
elapsed time in `m:ss` format, updating every second via `setInterval`.

When `state === 'failed'` and `error` is set: renders an `ExclamationCircleOutlined`
icon with a `Tooltip` showing the error string.

Label text is resolved via `t('status.<state>', state)` — falls back to the raw state
string if no translation exists.

---

### ProcessTreeView

```ts
interface ProcessNode {
  _id: string;
  name: string;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  children?: ProcessNode[];
}

interface SseState {
  connected: boolean;
}

interface ProcessTreeViewProps {
  treeData: ProcessNode | null;   // root node; renders null when treeData is null
  sseState: SseState;             // used to show 'Live' / 'Disconnected' label
  onCancel?: (processId: string) => void;
}
```

Renders an Ant Design `Tree` (non-selectable, with lines) from a nested `ProcessNode`
hierarchy. All nodes are expanded by default.

Internal checkbox "Hide finished sub-tasks" (default: checked) filters out child nodes
with `status.state === 'done'` recursively before rendering.

Progress bar visibility rules (identical to `ProcessList`/`ProcessItem`):
- Active + percent: determinate bar + percentage text label.
- Active + no percent: indeterminate animated gradient bar.
- Not active: hidden.

Cancel button appears per-node when `state in ACTIVE_STATES && node.cancellable && onCancel provided`.

---

### ProcessLogPanel

```ts
interface LogEntry {
  timestamp: string;    // ISO datetime string
  level: string;        // 'event' | 'info' | 'debug' | 'warning' | 'error'
  message: string;
  processName?: string;
}

interface ProcessLogPanelProps {
  logs: LogEntry[];
}
```

Monospace scrolling log container (`maxHeight: 400px`). Auto-scrolls to bottom on new
entries, but only when the user was already at the bottom (within 30px). Renders
`Empty` with `t('common.noData')` when `logs` is empty.

Level-to-color mapping:

| level | color |
|-------|-------|
| `event` | `cyan` |
| `info` | `blue` |
| `debug` | `default` |
| `warning` | `gold` |
| `error` | `red` |

---

### ProcessFilters

```ts
export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';

interface ProcessFiltersProps {
  filterGroup: FilterGroup;
  onFilterChange: (group: FilterGroup) => void;
  showDetails: boolean;
  onShowDetailsChange: (show: boolean) => void;
  showSpecial: boolean;
  onShowSpecialChange: (show: boolean) => void;
}
```

Renders a `Select` (width 180) plus two `Checkbox` controls in an Ant Design `Space`.
The component is purely controlled — no internal state. The `FilterGroup` type is
exported from `optio-ui`.

## Hooks

### useProcessActions

```ts
function useProcessActions(options?: {
  onResyncSuccess?: (clean: boolean) => void;
}): {
  launch: (processId: string) => void;
  cancel: (processId: string) => void;
  dismiss: (processId: string) => void;
  resync: () => void;
  resyncClean: () => void;
  isResyncing: boolean;
}
```

All mutations invalidate the `['processes']` query key on success.
`resync` sends `body: {}`, `resyncClean` sends `body: { clean: true }`.
`onResyncSuccess(clean)` is called after a successful resync with the `clean` flag value.

---

### useProcessList

```ts
function useProcessList(options?: {
  refetchInterval?: number | false;  // default 5000ms
}): {
  processes: any[];
  totalCount: number;
  isLoading: boolean;
}
```

Query key: `['processes', prefix]`. Fetches up to 50 items.

---

### useProcess

```ts
function useProcess(
  id: string | undefined,
  options?: { refetchInterval?: number | false }  // default 5000ms
): {
  process: any | null;
  isLoading: boolean;
}
```

Query key: `['process', prefix, id]`. Disabled when `id` is falsy.

---

### useProcessTree

```ts
function useProcessTree(
  id: string | undefined,
  options?: { refetchInterval?: number | false }  // default 5000ms
): any | null  // returns tree body or null
```

Query key: `['process-tree', prefix, id]`. Disabled when `id` is falsy.

---

### useProcessTreeLog

```ts
function useProcessTreeLog(
  id: string | undefined,
  options?: {
    refetchInterval?: number | false;  // default 5000ms
    limit?: number;                    // default 100
  }
): any[]  // array of log items
```

Query key: `['process-tree-log', prefix, id]`. Disabled when `id` is falsy.

---

### useProcessStream

```ts
function useProcessStream(
  processId: string | undefined,
  maxDepth?: number  // default 10
): {
  processes: ProcessUpdate[];
  connected: boolean;
  tree: ProcessTreeNode | null;
  rootProcess: ProcessUpdate | null;
  logs: LogEntry[];
}
```

SSE endpoint: `{baseUrl}/api/processes/{prefix}/{processId}/tree/stream?maxDepth={maxDepth}`.

Reconnects automatically after 3 seconds on error. Each component instance manages its
own `EventSource`.

SSE message types:
- `{ type: 'update', processes: ProcessUpdate[] }` — replaces full process list.
- `{ type: 'log', entries: LogEntry[] }` — appends log entries.
- `{ type: 'log-clear' }` — clears log buffer.

`tree` is derived via `buildTree(processes)` — constructs a `ProcessTreeNode` hierarchy
from the flat `processes` array using `parentId` linkage; root is the node with `depth === 0`.
`rootProcess` is the first process with `depth === 0`.

---

### useProcessListStream

```ts
function useProcessListStream(): {
  processes: any[];
  connected: boolean;
}
```

SSE endpoint: `{baseUrl}/api/processes/{prefix}/stream`.

Uses a **module-level singleton** `EventSource` shared across all hook instances
(via `useSyncExternalStore`). Reconnects after 3 seconds on error. Only one connection
is maintained per `baseUrl|prefix` combination.

## Types

### FilterGroup

```ts
export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';
```

Exported from `ProcessFilters.tsx`, re-exported from `index.ts`.

---

### ProcessTreeNode

```ts
interface ProcessUpdate {
  _id: string;
  parentId: string | null;
  name: string;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable: boolean;
  depth: number;
  order: number;
}

export interface ProcessTreeNode extends ProcessUpdate {
  children: ProcessTreeNode[];
}
```

Exported from `useProcessStream.ts`, re-exported from `index.ts`.

## i18n Keys

Complete list of all translation keys used in component source files:

| Key | Component | Usage |
|-----|-----------|-------|
| `processes.launch` | `ProcessItem` | Tooltip on launch button |
| `processes.cancel` | `ProcessItem`, `ProcessTreeView` | Tooltip on cancel button |
| `processes.filterAll` | `ProcessFilters` | Select option label |
| `processes.filterActive` | `ProcessFilters` | Select option label |
| `processes.filterHideCompleted` | `ProcessFilters` | Select option label |
| `processes.filterErrors` | `ProcessFilters` | Select option label |
| `processes.showDetails` | `ProcessFilters` | Checkbox label |
| `processes.showSpecial` | `ProcessFilters` | Checkbox label |
| `status.<state>` | `ProcessStatusBadge` | State tag label (dynamic key, falls back to raw state) |
| `common.noData` | `ProcessLogPanel` | Empty state description |
