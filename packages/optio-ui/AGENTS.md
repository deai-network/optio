# optio-ui — LLM Reference

## Package

- **name**: `optio-ui`
- **version**: `0.7.0`
- **type**: `module` (ESM)
- **entry**: `src/index.ts`

**Dependencies** (bundled):
- `optio-contracts: workspace:*`
- `@ts-rest/core: ^3.51.0`
- `@ts-rest/react-query: ^3.51.0`
- `@quaesitor-textus/core: ^0.4.2`
- `@quaesitor-textus/antd: ^0.4.2`

**Peer dependencies** (must be provided by the consuming app):
- `antd: >=6`
- `@ant-design/icons: >=6`
- `react: >=18`
- `react-dom: >=18`
- `@tanstack/react-query: >=5`
- `react-i18next: >=15`
- `i18next: >=24`
- `vultus-core: >=0.4.0` — a peer so the app has one copy and one components context
  (see "Spinner slot"); 0.4.0 is the release with the slot. In this workspace a dev dependency
  linked to `../unitas/packages/vultus-core`.

## Conventions

### Process state — single source of truth

**The only acceptable way to make decisions about task lifecycle state is by
calling a predicate from `src/process-state.ts`.** That file is the single
source of truth for what counts as "launchable", "active", "terminal",
"resumable", "resurrectable", or "resurrecting".

Do **not**:

- Compare state strings inline (`state === 'running'`, `state in [...]`,
  `[...].includes(state)`).
- Define a local `Set` of state names (`LAUNCHABLE_STATES`, `ACTIVE_STATES`,
  `TERMINAL_STATES`, etc.) inside a component or hook.
- Re-derive these in consuming apps (e.g. `excavator`'s `ProcessDetailPage`) —
  import the predicate instead.

Do:

- Use `isLaunchable(process)` / `isLaunchableState(state)`,
  `isActive(process)` / `isActiveState(state)`,
  `isTerminal(process)` / `isTerminalState(state)`,
  `isWidgetLive(process)` / `isWidgetLiveState(state)`,
  `isCancellable(process)` / `isCancellableState(state)`,
  `isResumable(process)`, `isResurrectable(process)` (launchable AND not
  resurrecting AND `supportsResurrect === true` AND `hasUnsavedWork === true`),
  `isResurrecting(process)` (`resurrecting === true`: a resurrect is saving the
  unsaved work while the state stays as it was), `isSuspended(process)`
  (`status.state === 'cancelled'` AND `supportsResume === true` AND
  `hasSavedState === true`: a cancelled task whose saved conversation can be
  read without launching it).
- If you genuinely need a new state-derived concept (e.g. "show widget UI" —
  which is *almost* `isActive` but excludes `scheduled`), add a named
  predicate to `process-state.ts` rather than spelling out the set at the
  callsite. Give it a name that says *what it gates*, not what states it
  contains.
- Add a unit test next to the existing ones in
  `src/__tests__/process-state.test.ts`.

Reason: state-set duplication has bitten us before. The state vocabulary
evolves (e.g. `cancel_requested` was added later than `cancelling`), and any
inline copy is a latent bug waiting for the next vocabulary change. A single
file, fully tested, fixes this once.

If in doubt: add a predicate, don't inline the check.

## Spinner slot (vultus-core)

An app lends a spinner through vultus-core's components context
(`useVultusComponents().Spinner`, set with `VultusComponentsContext.Provider` or vultus-antd's
`VultusProvider components={{ Spinner }}`). optio-ui shows it in place of antd's spinners in
`ProcessList` and `LaunchControls`, and before the "Loading…" text in `ProcessDetailView` and
`IframeWidget`. Each site calls
`useVultusComponents()` and `useSpinnerKey(localKey)` unconditionally (before any early
return) and renders `<Spinner spinnerKey={key} size=… />` when a Spinner is lent, its
unchanged antd code otherwise.

| Site | Local key | Size | Lent Spinner |
|------|-----------|------|--------------|
| `ProcessList` | `optio:process-list` | `'default'` | `List loading={{ spinning: loading, indicator: <SpinnerSlotIndicator><Spinner …/></SpinnerSlotIndicator> }}`; the wrapper (`span.optio-spinner-slot-indicator`) takes the `className`/`style`/`percent` antd's Spin injects into its indicator and undoes `.ant-spin-dot`'s 1em box, so the Spinner receives only `spinnerKey` and `size` |
| `LaunchControls` (resurrecting) | `optio:process:${process._id}:resurrecting` | `'small'` | the disabled button's `icon`, in place of `LoadingOutlined` |
| `ProcessDetailView` (no tree yet) | `optio:process:${processId}:detail` | `'small'` | before the "Loading…" text (test id `optio-detail-loading` unchanged) |
| `IframeWidget` (no `widgetData` yet) | `optio:process:${process._id}:widget` | `'small'` | before the "Loading…" text (test id `optio-widget-loading` unchanged) |

A `SpinnerScope` above a site prefixes its key with the scope chain (`outer/inner/<local key>`).
Without a lent Spinner every site renders exactly as before. Tests: `src/__tests__/spinnerSlot.test.tsx`.

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

### LaunchControls

```ts
interface LaunchControlsProps {
  process: any;
  onLaunch?: (id: string, opts?: { resume?: boolean }) => void;
  onResurrect?: (id: string) => void;  // save a failed run's host work, then resume
  size?: ButtonProps['size'];
}
```

Smart launch button (`packages/optio-ui/src/components/LaunchControls.tsx`).

Rendering rules:
- Renders **nothing** when the process is not in a launchable state (`idle | done | failed | cancelled`), or when `onLaunch` is not provided.
- Renders a single disabled **Resurrecting** indicator while `isResurrecting(process)` (spinning loading icon, or the app's lent Spinner — see "Spinner slot"; `aria-label` "Resurrecting", tooltip "Resurrecting: saving the unsaved work…"); clicking it does nothing. It takes precedence over all the cases below (a non-empty `denyReason` still wins).
- Renders a **Resurrect** split button when `onResurrect` is provided and `isResurrectable(process)` (`supportsResurrect=true` and `hasUnsavedWork=true`); it takes precedence over the two cases below (a non-empty `denyReason` still wins):
  - Primary button: **Resurrect** (medicine-box icon, `aria-label` "Resurrect") — calls `onResurrect(id)`.
  - Menu item **Resume from last snapshot**, only when `isResumable(process)` — calls `onLaunch(id, { resume: true })`.
  - Menu item **Restart** — calls `onLaunch(id, { resume: false })`.
  - Both menu items first open a confirmation modal ("This discards the unsaved work left by the failed run.", danger OK button **Discard and continue**); `onLaunch` runs only on OK. The modal comes from `Modal.useModal()`, so it is themed without an antd `<App>` ancestor.
  - Without `onResurrect`, a resurrectable process falls through to the cases below.
- Renders a single play button (calls `onLaunch(id)` with no opts) when `supportsResume=false` **or** `hasSavedState=false`.
- Renders an Ant Design `Dropdown.Button` when both `supportsResume=true` and `hasSavedState=true`:
  - Primary button: **Resume** — calls `onLaunch(id, { resume: true })`.
  - Dropdown menu item: **Restart (discard saved state)** — calls `onLaunch(id)`.

Used by `ProcessList` (replaces the old inline launch button logic) and `ProcessDetailView` (header action area).

---

### ProcessList

```ts
interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onResurrect?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}
```

Renders an Ant Design `List` (its loading indicator is the app's lent Spinner when one is set — see "Spinner slot"). Each item delegates to `ProcessItem`. The launch button is provided via `LaunchControls`; `onResurrect` is forwarded to it through `ProcessItem`.
`FilteredProcessList` (`ProcessFilter.tsx`) takes the same callbacks, including `onResurrect`, and forwards them to `ProcessList`.

Launchable states: `idle | done | failed | cancelled`.
Active states: `running | scheduled | cancel_requested | cancelling`.

---

### ProcessItem

```ts
type ProcessItemSize = 'small' | 'default' | 'big';

interface ProcessItemProps {
  process: any;
  onLaunch?: (id: string) => void;
  onResurrect?: (id: string) => void;  // forwarded to LaunchControls
  onCancel?: (id: string) => void;
  readonly?: boolean;
  onProcessClick?: (id: string) => void;
  size?: ProcessItemSize;  // default: 'default'
  inline?: boolean;        // default: false — when true, progress bar shares the row with name + actions (flex middle slot)
  inactiveContent?: ReactNode;  // shown where the progress bar would go when the process is NOT active (inline: middle slot, non-inline: second row). Pass last-run context here.
}
```

Single process row. Behavioral rules:
- Launch button visible when `!readonly && state in LAUNCHABLE_STATES && onLaunch provided`.
- If `process.warning` is set, launch button is wrapped in `Popconfirm` requiring confirmation.
- Cancel button visible when `!readonly && isCancellable(process) && onCancel provided` — i.e. state is `scheduled | running` AND `process.cancellable === true`. Hidden during `cancel_requested`/`cancelling` (cancel already in flight; re-click is a no-op).
- Progress bar: active + `progress.percent != null` → determinate bar; active + no percent → indeterminate animated gradient bar; not active → hidden.
- Name rendered as `Button[type=link]` when `onProcessClick` provided, plain `Text` otherwise. If `process.description` is set, the name is wrapped in a `Tooltip` showing the description.
- Progress message shown inline (blue) when `progress.message` is set and the process is active or `isResurrecting(process)` (a resurrect keeps the state `failed`, so its progress message is the only feedback).

Size mapping (one knob → progress bar height, button size, badge size):

| `size` | Progress | Button | Badge |
|--------|----------|--------|-------|
| `small`   | `'small'`   (6px bar)  | `'small'`  | tag — antd defaults |
| `default` | `'default'` (8px bar)  | `'middle'` | fontSize 14, padding 2px 8px |
| `big`     | `{ height: 12 }` (12px bar) | `'large'`  | fontSize 16, padding 4px 12px |

Internal callers (`ProcessList`, `RecentProcesses`) pass `size="small"` to preserve the pre-existing compact look. New call sites pick whatever fits the surrounding layout.

---

### ProcessStatusBadge

```ts
type ProcessStatusBadgeSize = 'small' | 'default' | 'big';

interface ProcessStatusBadgeProps {
  state: string;
  error?: string;
  runningSince?: string | null;          // ISO datetime string
  size?: ProcessStatusBadgeSize;         // default: 'small' (= antd Tag defaults; preserves legacy look)
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
| `cancelled` | `orange` |

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
  description?: string | null;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  supportsResume?: boolean;     // these flags are read by LaunchControls
  hasSavedState?: boolean;
  supportsResurrect?: boolean;
  hasUnsavedWork?: boolean;
  resurrecting?: boolean;
  children?: ProcessNode[];
}

interface SseState {
  connected: boolean;
}

interface ProcessTreeViewProps {
  treeData: ProcessNode | null;   // root node; renders null when treeData is null
  sseState: SseState;             // used to show 'Live' / 'Disconnected' label
  onCancel?: (processId: string) => void;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onResurrect?: (processId: string) => void;
}
```

Renders an Ant Design `Tree` (non-selectable, with lines) from a nested `ProcessNode`
hierarchy. All nodes are expanded by default.

Process names show a tooltip with the `description` text when hovering, if `description` is set.

Internal checkbox "Hide finished sub-tasks" (default: checked) filters out child nodes
with `status.state === 'done'` recursively before rendering.

Progress bar visibility rules (identical to `ProcessList`/`ProcessItem`):
- Active + percent: determinate bar + percentage text label.
- Active + no percent: indeterminate animated gradient bar.
- Not active: hidden.

Progress message (blue, own line) shown when `progress.message` is set and the node is active
or `isResurrecting(node)`.

Cancel button appears per-node when `state in ACTIVE_STATES && node.cancellable && onCancel provided`.

Launch affordances appear per-node via `LaunchControls` when `onLaunch` is provided; `onResurrect` is forwarded to it.

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

### ProcessDetailView

```ts
interface ProcessDetailViewProps {
  processId: string | null | undefined;
}

function ProcessDetailView({ processId }: ProcessDetailViewProps): JSX.Element
```

Self-fetching detail panel. Uses `useProcessStream` internally. Rendering branches:

- **No processId** — renders a centred "Select a process" placeholder.
- **No tree yet** (stream not yet received) — renders a "Loading…" placeholder (preceded by the app's lent Spinner, if any — see "Spinner slot").
- **`tree.uiWidget` is set and registered for the process's mode** — renders the named widget
  component (live or suspended variant, per `useProcessWidgetState`'s `mode`), passing
  `{ process: tree, apiBaseUrl, widgetProxyUrl, prefix, database }`. In suspended mode, also
  renders a `LaunchControls` (Resume) row above the widget unless `readOnly` is set.
- **`tree.uiWidget` is set but unregistered for the live mode** — warns to `console.warn` and
  falls back to the default tree+log view.
- **`tree.uiWidget` is set, the process `isSuspended`, but no suspended variant is
  registered** — falls back to the default tree+log view, silently (no warning — this is the
  normal case for a widget that doesn't support a read-only suspended view yet).
- **No `uiWidget`** — renders `ProcessTreeView` (its `onLaunch`, `onResurrect` and `onCancel` wired to `useProcessActions()` unless the `readOnly` prop is set) + `ProcessLogPanel`.

`widgetProxyUrl` shape passed to widget components: `${apiBaseUrl}/api/widget/${process._id}/`
(trailing slash is load-bearing for relative URLs inside iframes).

Exported from `optio-ui`.

---

### ProcessWidget / useProcessWidget / useProcessWidgetState

```ts
function useProcessWidget(tree: ProcessTreeNode | null | undefined): ReactNode | null

interface UseProcessWidgetStateResult {
  widget: ReactNode;
  mode: 'live' | 'suspended';
}
function useProcessWidgetState(tree: ProcessTreeNode | null | undefined): UseProcessWidgetStateResult | null

interface ProcessWidgetProps {
  tree: ProcessTreeNode | null | undefined;
}
function ProcessWidget({ tree }: ProcessWidgetProps): ReactNode  // null when not ready
```

Layout-free widget primitives (`packages/optio-ui/src/components/ProcessWidget.tsx`).
**`useProcessWidget`'s shape is unchanged** from before suspended-widget support existed —
still `ReactNode | null`, nothing else — it now simply also yields the suspended variant
while the process is suspended (same readiness gate as below), so existing callers that render
its result directly (e.g. an external app's `const widget = useProcessWidget(tree); ...{widget}`)
get the suspended view for free, with no code change and no risk of a non-element value
reaching React's child-rendering path. `useProcessWidgetState` is a new, separate export for
callers that need to know *which* mode produced the element — `ProcessWidget` → `WidgetProps`
mapping is otherwise identical between the two. `useProcessWidget` is implemented as
`useProcessWidgetState(tree)?.widget ?? null`.

`useProcessWidgetState` owns:

- Readiness gate: `tree.uiWidget` set, and either state is widget-live (`isWidgetLive`) or the
  process `isSuspended`; `tree.widgetData != null`.
- Registry lookup: `getWidget(uiWidget)` while live, `getSuspendedWidget(uiWidget)` while
  suspended. Live + unregistered → emits `console.warn` and returns `null`. Suspended +
  unregistered → returns `null` silently (no warning — a widget with no suspended variant,
  e.g. the built-in `iframe` widget, is expected to have nothing to show here).
- Database check (from `OptioProvider` / instance discovery). If unknown → emits `console.warn` and returns `null` (widget URLs need the database segment).
- `widgetProxyUrl` build: `${baseUrl}/api/widget/${database}/${prefix}/${tree._id}/` (trailing slash load-bearing). Built the same way for both modes, whether or not the suspended variant actually uses the proxy.

What neither hook owns: layout. `useProcessWidgetState` returns `{ widget, mode }` — the
registered component invoked with `{ process, apiBaseUrl, widgetProxyUrl, prefix, database }`
(same props in both modes) plus which mode produced it — or `null` when nothing should render.
The caller decides what to render around it (log strip, tabs, full-bleed, collapsible drawer,
Resume control, etc.).

Typical usage — caller needs the mode (e.g. to show a Resume control only when suspended):
```tsx
const result = useProcessWidgetState(tree);
return result
  ? <MyWidgetLayout mode={result.mode}>{result.widget}</MyWidgetLayout>
  : <MyFallback />;
```

Typical usage — caller only needs the element, exactly as before suspended widgets existed:
```tsx
const widget = useProcessWidget(tree);
return widget ? <MyWidgetLayout>{widget}</MyWidgetLayout> : <MyFallback />;
```

`ProcessDetailView` consumes `useProcessWidgetState` internally: both modes get the
log-strip-on-top + widget-fill layout; suspended mode additionally shows a `LaunchControls`
(Resume) row above the widget, unless `readOnly`. Other consumers (e.g. an external app's
process detail page) are free to choose their own layout.

---

## Widget System

### registerWidget / WidgetComponent / WidgetProps

```ts
interface WidgetProps {
  process: any;            // full process tree root object (includes widgetData, uiWidget)
  apiBaseUrl: string;      // optio API base URL
  widgetProxyUrl: string;  // ${apiBaseUrl}/api/widget/${process._id}/ — trailing slash is load-bearing
  prefix: string;          // process namespace prefix
  database?: string;       // optional database discriminator
}

type WidgetComponent = ComponentType<WidgetProps>;

interface RegisterWidgetOptions {
  suspended?: WidgetComponent;
}

function registerWidget(name: string, component: WidgetComponent, options?: RegisterWidgetOptions): void
function getSuspendedWidget(name: string): WidgetComponent | undefined
```

`registerWidget` adds a named component to the module-level widget registry. Call this at
module load time (before any `ProcessDetailView` renders). Exported from `optio-ui`.

`options.suspended` registers a second component under the same name, shown instead of the
live one while the process `isSuspended` (same `WidgetProps`, no extra fields). It lives in a
separate map (`getSuspendedWidget`, not exported from the package root — consumed internally
by `useProcessWidgetState`, which both `useProcessWidget` and `ProcessDetailView` go through);
a plain two-argument `registerWidget(name, component)` call leaves
any suspended variant already registered for that name untouched, so every existing call site
keeps working unchanged. A widget with no suspended variant (e.g. the built-in `iframe`
widget) simply isn't shown while suspended — `ProcessDetailView` falls back to the default
tree/log layout, exactly as it does for any other terminal state today.

### Built-in 'iframe' widget

Automatically registered (as `'iframe'`) when anything from `optio-ui` is imported.
Reads configuration from `process.widgetData` (type `IframeWidgetData`):

| Field | Type | Description |
|-------|------|-------------|
| `iframeSrc` | `string \| undefined` | iframe `src`; falls back to `widgetProxyUrl` when absent |
| `localStorageOverrides` | `Record<string, string> \| undefined` | Key/value pairs written to `localStorage` on mount, removed on unmount |
| `sandbox` | `string \| undefined` | iframe `sandbox` attribute |
| `allow` | `string \| undefined` | iframe `allow` attribute (feature policy) |
| `title` | `string \| undefined` | iframe `title`; falls back to `process.name` |

Renders a "Loading…" placeholder (preceded by the app's lent Spinner, if any — see
"Spinner slot") until `process.widgetData` arrives.

Shows a dismissible "Session ended." banner overlay when the process is in a terminal
state (`done`, `failed`, or `cancelled`).

---

## Hooks

### useProcessActions

```ts
function useProcessActions(options?: {
  onResyncSuccess?: (clean: boolean) => void;
}): {
  launch: (processId: string, opts?: { resume?: boolean }) => void;
  cancel: (processId: string) => void;
  dismiss: (processId: string) => void;
  resurrect: (processId: string) => void;
  resync: () => void;
  resyncClean: () => void;
  isResyncing: boolean;
}
```

All mutations invalidate the `['processes']` query key on success.
`resync` sends `body: {}`, `resyncClean` sends `body: { clean: true }`.
`onResyncSuccess(clean)` is called after a successful resync with the `clean` flag value.
`launch` sends `body: { resume: true }` when `opts.resume` is true; empty body otherwise.
`resurrect` POSTs `/processes/:id/resurrect` with `body: { sessionId: getSessionId() }`.

---

### useProcessConversation

```ts
function useProcessConversation(
  processId: string,
  opts?: { enabled?: boolean; stoppedAt?: string | null },
): UseQueryResult<{ status: 200; body: { events: unknown[]; capturedAt: string | null } }, unknown>
```

Reads a suspended process's saved conversation record via
`api.processes.getConversation.useQuery` (`GET /processes/:id/conversation`, proxied by
optio-api from the engine's conversation archive — see root AGENTS.md "optio-core:
conversation archive listener" and `packages/optio-api/AGENTS.md`). Returns the raw ts-rest
react-query result, unmodified (the return type is loose — `any`, since `OptioClient` is
`any`-typed — but correct in shape at runtime, as described below):

- a 200 appears as `data` (`data.body` is `{ events, capturedAt }`);
- any non-2xx (401/403/404/409/500/503) is thrown by ts-rest and surfaces as `error`
  (`error.status` is the HTTP status; `error.body` is `{ reason, message }` for every status
  except 401, which is `{ message }` only).

`opts.enabled` (default `true`) gates the query, ANDed with `!!processId`. When `false`,
**nothing** fetches — neither the conversation nor the process doc described below.

Never retries a 4xx (`error.status` 400–499) — the process's state won't change because the
read is repeated. Transient errors (network, 5xx) get up to 2 retries.

`staleTime: Infinity`: a record is fixed for a given suspend, and the key below carries that
suspend's `stoppedAt`, so a window refocus or a remount never refetches it (each refetch would
make the engine decrypt it again). An explicit `refetch()` (e.g. a Retry button) still fetches.

The query key is `['process-conversation', database, prefix, processId, statusTimestamp]`,
where `statusTimestamp` is the process's current `status.stoppedAt`. This is the timestamp a
suspend (a cancel with saved state) sets, so a resume-then-resuspend cycle gets a fresh cache
entry — without it, remounting after a second suspend would briefly (or indefinitely, if the
component never actually unmounts) show the first suspend's cached conversation while the new
one loads.

`statusTimestamp` comes from one of two places:
- **`opts.stoppedAt`**, when the caller supplies it (even explicitly `null`) — the hook then
  does **not** fetch or poll the process document at all. Use this when the caller already has
  the process doc (e.g. a suspended widget gets `process.status.stoppedAt` on its own
  `WidgetProps.process`), to avoid a redundant `GET /processes/:id` plus its 5 s poll running
  for every mounted suspended widget just to re-derive a value already in hand.
- an internal `useProcess(processId)` call, when `opts.stoppedAt` is omitted — the pre-existing
  default behaviour, for callers that don't already have the process doc.

---

### useProcessList

```ts
function useProcessList(options?: {
  refetchInterval?: number | false;  // default 5000ms
  metadataFilter?: ProcessMetadataFilter;
}): {
  processes: any[];
  totalCount: number;
  isLoading: boolean;
}
```

Query key: `['processes', database, prefix, filterKey]` where `filterKey` is `JSON.stringify(metadataFilter)` or `''` when absent — so different filters get separate cache entries. Fetches up to 50 items. When `metadataFilter` is provided it is forwarded as a `?metadataFilter=<URL-encoded JSON>` query param.

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
function useProcessListStream(options?: {
  metadataFilter?: ProcessMetadataFilter;
}): {
  processes: any[];
  connected: boolean;
}
```

SSE endpoint: `{baseUrl}/api/processes/{prefix}/stream`.

Uses a **module-level singleton** `EventSource` shared across all hook instances
(via `useSyncExternalStore`). Reconnects after 3 seconds on error. The singleton is
keyed on `(baseUrl, database, prefix, metadataFilter)` — supplying a different
`metadataFilter` from the same component closes the old EventSource and opens a
new one with the new query. Two components mounted concurrently with different
filters will fight: the most recent render wins, the other gets data for the wrong
filter. Hoist filter state above all consumers, or use a single top-level filter
selector.

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
  description?: string | null;
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
| `processes.resume` | `LaunchControls` | Primary button label when `hasSavedState=true` (default: "Resume") |
| `processes.restart` | `LaunchControls` | Dropdown menu item label (default: "Restart (discard saved state)") |
| `processes.resurrect` | `LaunchControls` | Resurrect button `aria-label` (default: "Resurrect") |
| `processes.resurrectHint` | `LaunchControls` | Resurrect button tooltip (default: "Save the work left by the failed run, then resume") |
| `processes.resumeFromSnapshot` | `LaunchControls` | Resurrect menu item and confirmation title (default: "Resume from last snapshot") |
| `processes.restartDiscarding` | `LaunchControls` | Resurrect menu item and confirmation title (default: "Restart") |
| `processes.discardUnsavedWork` | `LaunchControls` | Confirmation body (default: "This discards the unsaved work left by the failed run.") |
| `processes.discardAndContinue` | `LaunchControls` | Confirmation OK button (default: "Discard and continue") |
| `processes.resurrecting` | `LaunchControls` | Resurrecting indicator tooltip (default: "Resurrecting: saving the unsaved work…") |
| `processes.resurrectingLabel` | `LaunchControls` | Resurrecting indicator `aria-label` (default: "Resurrecting") |
| `processes.cancel` | `ProcessItem`, `ProcessTreeView` | Tooltip on cancel button |
| `processes.filterAll` | `ProcessFilters` | Select option label |
| `processes.filterActive` | `ProcessFilters` | Select option label |
| `processes.filterHideCompleted` | `ProcessFilters` | Select option label |
| `processes.filterErrors` | `ProcessFilters` | Select option label |
| `processes.showDetails` | `ProcessFilters` | Checkbox label |
| `processes.showSpecial` | `ProcessFilters` | Checkbox label |
| `status.<state>` | `ProcessStatusBadge` | State tag label (dynamic key, falls back to raw state) |
| `common.noData` | `ProcessLogPanel` | Empty state description |
