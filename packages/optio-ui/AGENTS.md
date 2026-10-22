# optio-ui — LLM Reference

## Package

- **name**: `optio-ui`
- **version**: `0.8.2`
- **type**: `module` (ESM)
- **entry**: `src/index.ts`

**Dependencies** (bundled):
- `optio-contracts: workspace:*`
- `@ts-rest/core: ^3.51.0`
- `@ts-rest/react-query: ^3.51.0`
- `@quaesitor-textus/core: ^0.4.2`
- `@quaesitor-textus/antd: ^0.4.2`
- `vultus-filtrum: ^0.1.0` — the predicate of an app's own process filters (`toPredicate`, see
  "ProcessFilters"); it brings `filtrum-core`, and its peer `vultus-core` is optio-ui's own peer.

**Peer dependencies** (must be provided by the consuming app):
- `antd: >=6`
- `@ant-design/icons: >=6`
- `react: >=18`
- `react-dom: >=18`
- `@tanstack/react-query: >=5`
- `react-i18next: >=15`
- `i18next: >=24`
- `vultus-core: >=0.5.4` — a peer so the app has one copy and one components context
  (see "Spinner slot"), the copy vultus-antd uses too (vultus-antd 0.7.2 depends on
  `^0.5.4`): the task factory form's action finds its form through vultus-core's
  `ActionErrorContext`, which vultus-antd's `FormPanel` provides.
- `vultus-antd: >=0.7.2` — the task factory form's widgets (`Field`, `FormPanel`,
  `FormSubmitButton`, `ActionButton`; see "TaskFactoryLaunchModal").

In this workspace both vultus packages are dev dependencies (`^0.5.4`, `^0.7.2`), linked to
`../unitas/packages/vultus-*` when that checkout exists (`pnpm-workspace.yaml`). vultus-filtrum
is not linked: it comes from npm. Linked, its `filtrum-core` dependency would resolve to this
workspace's `packages/filtrum-core`, and the lockfile would record that link from the unitas
side, with the name of this checkout's directory in it.

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

- Use `isLaunchable(process)` (a launchable state, and not a task factory's
  launch) / `isLaunchableState(state)` (the state alone),
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
  read without launching it), `isTaskFactory(process)` (`taskFactory === true`:
  a task factory's record, whose launch opens its launch form; see
  "TaskFactoryLaunchModal"), `isTaskFactoryLaunch(process)` (`metadata.taskFactory`
  a non-empty string, the factory's id: a factory's launch, from its form or a
  program, which ran once with its values and is never launched again, so
  `isLaunchable` is false for it in any state; optio-core refuses its launch,
  `not-launchable`).
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
  prefix?: string;      // process namespace prefix sent to all API calls
  database?: string;    // database of the optio instance
  live?: boolean;
  baseUrl?: string;     // API base URL, default '' (same origin)
  onAttention?: (processId: string, reason: string) => void;
  onClientMessage?: (processId: string, keyword: string, data: unknown) => void;
  onBrowserOpen?: BrowserOpenHandler;  // undefined: window.open + notification fallback
  children: ReactNode;
}
```

Creates a React context containing `{ prefix, database, live, baseUrl, client, resetSession }`.
Must wrap all components and hooks from this package. Requires `QueryClientProvider` from
`@tanstack/react-query` to be present higher in the tree.

`prefix`, `database` and `live` left out are filled from instance discovery
(`useInstanceDiscovery`, `GET /api/optio/instances`) only when exactly one instance is
visible; otherwise `prefix` is `'optio'`, `database` is `undefined` and `live` is `false`.
Widgets need the database (`useProcessWidgetState` returns `null` without it).

`onAttention` / `onClientMessage` open the session-events stream
(`{baseUrl}/api/session-events/stream?sessionId=…[&prefix=…][&database=…]`), only when
`prefix` is passed explicitly: the stream is started with the prop, not the discovered value.

Leave `baseUrl` at `''` and serve the API from the page's origin (in development, a dev-server
proxy for `/api` with `ws: true`): optio-api sends no CORS headers, and the `EventSource`
streams are opened without `withCredentials`, so a cross-origin API gets no session cookie.

Internal context value shape:
```ts
interface OptioContextValue {
  prefix: string;
  database: string | undefined;
  live: boolean;
  baseUrl: string;
  client: OptioClient;  // ts-rest client created from baseUrl
  resetSession: () => void;
}
```

## Components

### LaunchControls

```ts
interface LaunchControlsProps {
  process: any;
  onLaunch?: (id: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;  // a task factory's form launched it: the child it made
  onResurrect?: (id: string) => void;  // save a failed run's host work, then resume
  size?: ButtonProps['size'];          // default 'small'
  iconFontSize?: number;               // pixel size of the button icons
  denyReason?: string | null;          // non-empty: a disabled play button with this tooltip
}
```

Smart launch button (`packages/optio-ui/src/components/LaunchControls.tsx`).

Rendering rules:
- Renders **nothing** when the process is not launchable (`isLaunchable`: not in a launchable state, `idle | done | failed | cancelled`, or a task factory's launch, `isTaskFactoryLaunch`, which is never launched again, e.g. one a TTL keeps), or when `onLaunch` is not provided.
- Renders a single disabled **Resurrecting** indicator while `isResurrecting(process)` (spinning loading icon, or the app's lent Spinner — see "Spinner slot"; `aria-label` "Resurrecting", tooltip "Resurrecting: saving the unsaved work…"); clicking it does nothing. It takes precedence over all the cases below (a non-empty `denyReason` still wins).
- For a task factory's record (`isTaskFactory(process)`), a single play button (tooltip `processes.launch`) that opens the factory's `TaskFactoryLaunchModal` instead of calling `onLaunch` (which only allows it, as for any process); no `Popconfirm`, the form shows the `warning`. The form's launch hands the child to `onLaunched`. It takes precedence over the cases below (`denyReason` and Resurrecting still win).
- Renders a **Resurrect** split button when `onResurrect` is provided and `isResurrectable(process)` (`supportsResurrect=true` and `hasUnsavedWork=true`); it takes precedence over the two cases below (a non-empty `denyReason` still wins):
  - Primary button: **Resurrect** (medicine-box icon, `aria-label` "Resurrect") — calls `onResurrect(id)`.
  - Menu item **Resume from last snapshot**, only when `isResumable(process)` — calls `onLaunch(id, { resume: true })`.
  - Menu item **Restart** — calls `onLaunch(id, { resume: false })`.
  - Both menu items first open a confirmation modal ("This discards the unsaved work left by the failed run.", danger OK button **Discard and continue**); `onLaunch` runs only on OK. The modal comes from `Modal.useModal()`, so it is themed without an antd `<App>` ancestor.
  - Without `onResurrect`, a resurrectable process falls through to the cases below.
- Renders a single play button (calls `onLaunch(id)` with no opts) when `supportsResume=false` **or** `hasSavedState=false`.
- Renders a split button (`Space.Compact`: a button and a `Dropdown` arrow) when both `supportsResume=true` and `hasSavedState=true`:
  - Primary button: **Resume** — calls `onLaunch(id, { resume: true })`.
  - Dropdown menu item: **Restart (discard saved state)** — calls `onLaunch(id, { resume: false })`.

Used by `ProcessItem` (so `ProcessList`), `ProcessTreeView`, and `ProcessDetailView` (the suspended widget's Resume row). Not exported from the package root.

---

### ProcessList

```ts
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

Renders an Ant Design `List` (its loading indicator is the app's lent Spinner when one is set — see "Spinner slot"). Each item delegates to `ProcessItem`. The launch button is provided via `LaunchControls`; `onResurrect` and `onLaunched` are forwarded to it through `ProcessItem`.
`FilteredProcessList` (`ProcessFilter.tsx`) takes the same callbacks, including `onResurrect` and `onLaunched`, and forwards them to `ProcessList`.

Launchable states: `idle | done | failed | cancelled`.
Active states: `running | scheduled | cancel_requested | cancelling`.

---

### ProcessItem

```ts
type ProcessItemSize = 'small' | 'default' | 'big';

interface ProcessItemProps {
  process: any;
  onLaunch?: (id: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;  // forwarded to LaunchControls (a task factory's launch)
  onResurrect?: (id: string) => void;  // forwarded to LaunchControls
  onCancel?: (id: string) => void;
  readonly?: boolean;
  onProcessClick?: (id: string) => void;
  size?: ProcessItemSize;  // default: 'default'
  inline?: boolean;        // default: false — when true, progress bar shares the row with name + actions (flex middle slot)
  inactiveContent?: ReactNode;  // shown where the progress bar would go when the process is NOT active (inline: middle slot, non-inline: second row). Pass last-run context here.
  launchDenyReason?: string | null;  // forwarded to LaunchControls as denyReason
}
```

Single process row. Behavioral rules:
- Launch button visible when `!readonly && state in LAUNCHABLE_STATES && onLaunch provided`.
- If `process.warning` is set, launch button is wrapped in `Popconfirm` requiring confirmation (a task factory's shows it in its launch form instead).
- A task factory's launch button opens its `TaskFactoryLaunchModal` (see `LaunchControls`).
- Cancel button visible when `!readonly && isCancellable(process) && onCancel provided` — i.e. state is `scheduled | running` AND `process.cancellable === true`. Hidden during `cancel_requested`/`cancelling` (cancel already in flight; re-click is a no-op). When `process.supportsResume` it is a **Suspend** button (pause icon, not `danger`, `processes.suspend`), otherwise **Cancel** (`processes.cancel`); `ProcessTreeView` does the same.
- Progress bar: active + `progress.percent != null` → determinate bar; active + no percent → indeterminate animated gradient bar; not active → hidden.
- Name rendered as `Button[type=link]` when `onProcessClick` provided, plain `Text` otherwise. If `process.description` is set, the name is wrapped in a `Tooltip` showing the description.
- Progress message shown inline (blue) when `progress.message` is set and the process is active or `isResurrecting(process)` (a resurrect keeps the state `failed`, so its progress message is the only feedback).

Size mapping (one knob → progress bar height, button size, badge size):

| `size` | Progress | Button | Badge |
|--------|----------|--------|-------|
| `small`   | `'small'`   (6px bar)  | `'small'`  | tag — antd defaults |
| `default` | `'default'` (8px bar)  | `'middle'` | fontSize 14, padding 2px 8px |
| `big`     | `{ height: 12 }` (12px bar) | `'large'`  | fontSize 16, padding 4px 12px |

The internal caller (`ProcessList`) passes `size="small"` to preserve the pre-existing compact look. New call sites pick whatever fits the surrounding layout.

---

### ProcessStatusBadge

```ts
type ProcessStatusBadgeSize = 'small' | 'default' | 'big';

interface ProcessStatusBadgeProps {
  state: string;
  error?: string;
  runningSince?: string | null;          // ISO datetime string
  size?: ProcessStatusBadgeSize;         // default: 'small' (= antd Tag defaults; preserves legacy look)
  autoResumeScheduled?: boolean;         // adds a stopwatch icon (status.autoResumeScheduled tooltip)
  className?: string;                    // extra class on the Tag
  supportsResume?: boolean;              // with hasSavedState: the suspend labels below
  hasSavedState?: boolean;
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
string if no translation exists. Exceptions, both in `blue`: with `supportsResume`,
`cancel_requested`/`cancelling` read `t('status.suspending', 'Suspending')`, and with
`supportsResume` and `hasSavedState`, `cancelled` reads `t('status.suspended', 'Suspended')`.

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

interface SseState {
  connected: boolean;
}

interface ProcessTreeViewProps {
  treeData: ProcessNode | null;   // root node; renders null when treeData is null
  sseState: SseState;             // used to show 'Live' / 'Disconnected' label
  onCancel?: (processId: string) => void;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;  // forwarded to each node's LaunchControls
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

Launch affordances appear per-node via `LaunchControls` when `onLaunch` is provided; `onResurrect` and `onLaunched` are forwarded to it.

---

### ProcessLogPanel

```ts
interface LogEntry {
  timestamp: string;    // ISO datetime string
  level: string;        // 'event' | 'info' | 'debug' | 'warning' | 'error'
  message: string;
  data?: Record<string, unknown>;
  processId: string;
  processLabel: string;
}

interface ProcessLogPanelProps {
  logs: LogEntry[];
  tree: ProcessTreeNode | null;  // indents and colours each row by its process; null: no bars
  fillParent?: boolean;          // fill the parent's height instead of maxHeight 400
}
```

Monospace scrolling log container (`maxHeight: 400px`, or the parent's height with
`fillParent`). Auto-scrolls to bottom on new entries, but only when the user was already at
the bottom (within 30px). Renders
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

interface WithFilteredProcessesProps {
  children: ReactNode;
  filters?: readonly FieldDefinition[];            // the app's own filters over the metadata (vultus-core definitions)
  filterFields?: Readonly<Record<string, string>>; // filter id → metadata key, where they differ
}

function WithFilteredProcesses(props: WithFilteredProcessesProps): JSX.Element
function ProcessFilters({ className, style }?: { className?: string; style?: CSSProperties }): JSX.Element
function FilteredProcessList(props: ProcessListProps): JSX.Element  // ProcessList over filterFn(processes)
function useProcessFilter(): {
  filterGroup: FilterGroup; setFilterGroup: (g: FilterGroup) => void;
  showDetails: boolean; setShowDetails: (v: boolean) => void;
  showSpecial: boolean; setShowSpecial: (v: boolean) => void;
  filters: readonly FieldDefinition[];               // the app's filters, [] without
  filterValues: Readonly<Record<string, unknown>>;   // their values, by filter id
  setFilterValue: (id: string, value: unknown) => void;
  metadataFilter: ProcessMetadataFilter | undefined; // their predicate, for the list stream
  filterFn: (processes: any[]) => any[];
}
```

`ProcessFilters` renders every filter as a vultus-antd `Field` in local mode (the value and
`onChange` given at the render site, as `TaskFactoryLaunchModal`'s fields), in an Ant Design
`Space` (`size={[16, 8]}`, wrapping, a 16px bottom margin; `className` and `style` reach it):

| Field id | Kind | Widget | Value | Label |
|----------|------|--------|-------|-------|
| `search` | `text` | `search`, width 200 | quaesitor-textus' query (`useSearchContext()`: `query` / `setQuery`); searches `name` and `description` | `processes.search`, also its placeholder |
| `filterGroup` | `oneOf` | `select`, width 180 | `filterGroup` (default `'all'`) | none; the choices `processes.filterAll`, `processes.filterActive`, `processes.filterHideCompleted`, `processes.filterErrors` |
| `showDetails` | `boolean` | `checkbox` | `showDetails` (default `false`) | `processes.showDetails` |
| `showSpecial` | `boolean` | `checkbox` | `showSpecial` (default `false`) | `processes.showSpecial` |

then the app's `filters`, in their order, each with its definition's widget hint, else its
kind's default (a `manyOf`'s is `checkboxes`; `widget: 'select'` keeps the row compact). It
holds no state of its own: it reads and sets the state of the enclosing
`WithFilteredProcesses` (via `useProcessFilter()`). `FilteredProcessList` applies the
built-in filters to its `processes` (`filterFn`, in the browser); the app's filters are not
in `filterFn`.

**An app's own filters.** `filters` are vultus-core field definitions over the process
metadata, of the kinds vultus-filtrum gives a predicate for: `oneOf` and `oneOfTree` (`eq`),
`manyOf` and `manyOfTree` (`in`). (vultus-filtrum gives a `text` filter a `search` leaf, which
optio-api's metadata filter does not accept; any other kind throws its
`UnsupportedFieldKindError` when WithFilteredProcesses renders.) Each matches the metadata key
of its id, or the one `filterFields` names (`{ modules: 'module' }`). `WithFilteredProcesses`
keeps their values in its own state: what was chosen, else the filter's default, its
`initialValue`, else its kind's own (vultus-core's `initialValueOf`: a `oneOf`'s first enabled
choice, a `manyOf`'s empty list, ...), as vultus-filtrum's README asks of its caller. Their
definitions' functions get the field context (`useFieldContext()`) at WithFilteredProcesses;
the fields get theirs at ProcessFilters, the same unless a `FieldContextProvider` sits between.

`metadataFilter` is `toPredicate(filters, filterValues, { ctx, fields: filterFields })`
(vultus-filtrum), or `undefined` when there are no app filters or the predicate is `{}` (no
filter has a value). It keeps its identity while it says the same (it is memoised on its JSON),
even when the app passes its definitions anew on each render. The app hands it to its list:
`useProcessListStream({ metadataFilter })` (or `useProcessList`), which must then be called
inside `WithFilteredProcesses`; optio-api applies it to `metadata.*` (filtrum-mongo with
`fieldPrefix: 'metadata.'`), so `module` filters `metadata.module`. Its shapes
(`{ module: { in: [...] } }`, `{ level: { eq: 'basic' } }`, several ANDed) are what
`ProcessMetadataFilterSchema` accepts. The list stream is a module-level singleton: every
`useProcessListStream` on the page should pass the same `metadataFilter`.

```tsx
const PROCESS_FILTERS: FieldDefinition[] = [{
  kind: 'manyOf', id: 'module', label: 'Module', placeholder: 'All modules', widget: 'select',
  choices: [{ value: 'algebra', label: 'Algebra' }, { value: 'geometry', label: 'Geometry' }],
}];

function ProcessesPage() {
  return (
    <WithFilteredProcesses filters={PROCESS_FILTERS}>
      <Processes />
    </WithFilteredProcesses>
  );
}

function Processes() {
  const { metadataFilter } = useProcessFilter();  // { module: { in: ['algebra'] } } once Algebra is chosen
  const { processes, connected } = useProcessListStream({ metadataFilter });
  return (
    <>
      <ProcessFilters />
      <FilteredProcessList processes={processes} loading={!connected} />
    </>
  );
}
```

The three components and the hook are exported from `optio-ui`, as are the `FilterGroup` and
`WithFilteredProcessesProps` types. Tests: `src/__tests__/ProcessFilter.test.tsx`.

### ProcessDetailView

```ts
interface ProcessDetailViewProps {
  processId: string | null | undefined;
  readOnly?: boolean;            // default false: no Launch / Cancel / Resume affordances
  showLogs?: boolean;            // default true; false: the widget alone, full height
  waitForWidget?: boolean;       // default false; true: a big spinner until the widget shows
  widgetPlaceholder?: ReactNode; // shown instead of that spinner; implies waitForWidget
  onLaunched?: (child: Process) => void;  // a task factory's launch form (from the tree) launched it
}

function ProcessDetailView(props: ProcessDetailViewProps): JSX.Element
```

Self-fetching detail panel. Uses `useProcessStream` internally. Rendering branches:

- **No processId** — renders a centred "Select a process" placeholder.
- **`processNotFound`** / **`error`** from the stream — "Process not found." / "Error while accessing process: …".
- **Waiting** (`waitForWidget` or `widgetPlaceholder`) — renders the placeholder, or a large spinner (the app's lent Spinner, key `optio:process:${processId}:detail`, else antd's `Spin`), in place of the "Loading…" line below, and in place of the default tree+log view while a scheduled or running process has no widget to show yet.
- **No tree yet** (stream not yet received) — renders a "Loading…" placeholder (preceded by the app's lent Spinner, if any — see "Spinner slot").
- **`tree.uiWidget` is set and registered for the process's mode** — renders the named widget
  component (live or suspended variant, per `useProcessWidgetState`'s `mode`), passing
  `{ process: tree, apiBaseUrl, widgetProxyUrl, prefix, database }`, plus `readOnly: true`
  when the `readOnly` prop is set (so a widget does not launch or resume the process
  itself). In suspended mode, also
  renders a `LaunchControls` (Resume) row above the widget unless `readOnly` is set, with
  `onLaunch` and `onResurrect` wired as in the default layout (a process left with
  `hasUnsavedWork` gets Resurrect, and Resume asks before it discards the unsaved work).
- **`tree.uiWidget` is set but unregistered for the live mode** — warns to `console.warn` and
  falls back to the default tree+log view.
- **`tree.uiWidget` is set, the process `isSuspended`, but no suspended variant is
  registered** — falls back to the default tree+log view, silently (no warning — this is the
  normal case for a widget that doesn't support a read-only suspended view yet).
- **No `uiWidget`** — renders `ProcessTreeView` (its `onLaunch`, `onResurrect` and `onCancel` wired to `useProcessActions()` unless the `readOnly` prop is set, `onLaunched` passed on) + `ProcessLogPanel`. A task factory's record (the root of its launches) gets the play button that opens its `TaskFactoryLaunchModal`; the launch then shows under it in the tree, with no play button of its own (`isTaskFactoryLaunch`).

`widgetProxyUrl` shape passed to widget components: `${apiBaseUrl}/api/widget/${database}/${prefix}/${process._id}/`
(database and prefix URI-encoded; trailing slash is load-bearing for relative URLs inside iframes).

Exported from `optio-ui`.

---

### TaskFactoryLaunchModal

```ts
interface TaskFactoryRecord {          // what it needs of the factory's record
  _id: string;
  name: string;
  warning?: string | null;
  parameters?: unknown[] | null;       // TaskFactoryParameter[] (optio-core checked them at sync)
  ttlSeconds?: number | null;          // how long it keeps an ended launch; null: until dismissed
}

interface TaskFactoryLaunchModalProps {
  process: TaskFactoryRecord;          // the factory's record (a Process, a list's or a tree's)
  open: boolean;
  onClose: () => void;                 // Cancel, the close icon, Escape, and after a launch
  onLaunched?: (child: Process) => void;  // the launched child; the modal then closes
  overrides?: TaskFactoryOverrides;    // field id -> partial vultus-core definition, merged in
}

type TaskFactoryOverrides = Readonly<Record<string, Partial<FieldDefinition>>>;
function mergeOverrides(definitions: readonly FieldDefinition[], overrides: TaskFactoryOverrides | undefined): FieldDefinition[];
```

The launch form of a task factory (`packages/optio-ui/src/components/TaskFactoryLaunchModal.tsx`;
spec `docs/2026-10-20-task-factory-design.md` §3). `LaunchControls` opens it for a factory's
record (`isTaskFactory`: `taskFactory: true`), which carries `parameters` and `warning`: the REST
reads (`ProcessSchema`) and optio-api's list, tree and multi-tree streams (`ProcessUpdate`) all send
them.

- An antd `Modal` (title `processes.factoryTitle`, "Launch “{{name}}”"; `destroyOnHidden`, so each
  opening starts from the initial values) around a vultus-antd `FormPanel` (vertical): the factory's
  `warning` (a warning `Alert`) on top, then one antd `Form.Item` per parameter (name = its id,
  label, the value the form keeps) holding a vultus-antd `<Field>` of the parameter's definition,
  then **Cancel** (a vultus action, `ActionButton`) and **Launch** (`FormSubmitButton`).
- The definitions (`src/task-factory.ts`, `fieldDefinitionOf`): the parameter's label and its
  choices' labels resolved for the UI language (`i18n.language`: that language, else its base
  language for a regional code, else `en`, else the first; a parameter without a label is named by
  its id); `required` and a text's `maxLength`, which vultus-core has no option for, left out. The
  form checks them (the items' rules): a required parameter without a value (`isNoValue`: null;
  `''` for a text; `[]` for a manyOf) shows `processes.factoryRequired`, a text over its
  `maxLength` (in code points, as optio-core counts) `processes.factoryMaxLength`, and nothing
  is launched. The other kinds always hold a value; only a required text or manyOf is marked
  required. Initial values: `initialValue`, else the kind's own default (vultus-core's
  `initialValueOf`).
- **The built-in `ephemeral` field** (`launchFormDefinitions(record, language, t)` in
  `src/task-factory.ts`, `EPHEMERAL_FIELD_ID`): after the parameters, a boolean rendered as a checkbox (`widget: 'checkbox'`, which an override may change; the form item then leaves the label to the widget, so it shows once) with the label
  `processes.launchEphemeral` ("Remove when finished"), initially `ttlSeconds == null` (checked
  unless the factory keeps ended launches for a TTL), and a description (the field's help):
  `processes.launchEphemeralHelp` ("Checked: the launch disappears from the list when it ends.
  Unchecked: it stays, with its log, until it is removed.") or, with a TTL,
  `processes.launchEphemeralHelpTtl` (the same, "… until it is removed, at most {{duration}} after
  it ends."), `duration` the largest of days, hours, minutes, seconds that divides the TTL
  (`processes.durationDays|Hours|Minutes|Seconds`, i18next plurals).
- **Overrides** (`overrides`, `mergeOverrides`, exported, pure): merged into each field's finished
  definition, the `ephemeral` one included: plain objects key by key, recursively; anything else (a
  value, an array such as `choices`, a function) replaces; functions of the field context are fine.
  Throws at render `Task factory overrides: no field '<id>' in the form` for an id the form lacks and
  `Task factory overrides: field '<id>' cannot change its kind or id` for a `kind` or `id` override.
  The form resolves each merged definition's `label`, `visible`/`hidden` and initial value as vultus
  does (`resolveIn`, `calculateVisible`, `initialValueOf`, with `useFieldContext()`): a field that is
  not visible is a hidden `Form.Item` (label and all), its value still in the form and sent.
- **Launch** is the vultus action `useTaskFactoryLaunchAction(record, { onLaunched, hiddenLabels })`
  (`src/hooks/useTaskFactoryLaunch.ts`, created inside the `FormPanel`): `fire(values)` calls
  `useProcessActions().launchAsync(_id, { params, ephemeral })` (`ephemeral` the checkbox's value,
  `params` every other field's value, as the form holds it), `errorRoute: 'processes.launch'` with
  `optioErrorRoutes`. On success it calls
  `onLaunched(child)` and closes. A refusal leaves the modal open with the values entered:
  - `invalid-parameters` whose message `"<id>: <what>"` names a parameter of the form: `<what>` on
    that field (`form.setFields`), and the reason's text (`errors.processes.launch.invalid-parameters`)
    in the form's error (`FormPanel`'s inline `Alert`);
  - `invalid-parameters` naming none, or a field that is not visible (then `"<label>: <what>"`,
    `hiddenLabels` giving the labels): its message, as the engine wrote it, in the form's error;
  - any other reason: its text (`errors.processes.launch.<reason>`) in the form's error; an error
    without a reason (network, 401/403), its message.
- React events inside the modal (click, mouse down, key down) stop at it: they would otherwise
  bubble, along React's tree, to the handlers around the launch control (the process tree's
  keyboard navigation took arrow keys, Home and End typed in a field).

`useProcessActions().launch/launchAsync(id, { resume?, params?, ephemeral? })`: `ephemeral` goes in
the launch body when given (a factory launch's removal when it ends).

Exported from `optio-ui`, with `mergeOverrides` and the `TaskFactoryLaunchModalProps`,
`TaskFactoryOverrides`, `TaskFactoryRecord`, `TaskFactoryParameter`, `ParameterChoice` and
`ParameterLabel` types.

### Error routes (`optioErrorRoutes`)

```ts
type OptioErrorRouteId = 'processes.launch';
const optioErrorRoutes: ErrorRoutesRegistry<OptioErrorRouteId>;  // vultus-core's registry
```

The error routes of optio-ui's own actions (`src/error-routes.ts`): for `processes.launch`, every
`LaunchFailureReason` (optio-contracts; typed `Record<LaunchFailureReason, RegistryEntry>`, so a
reason added there fails the typecheck until it has an entry) → `{ i18nKey:
'errors.processes.launch.<reason>' }`. vultus-core's `routeApiError` shows `t(i18nKey)`: the app
translates these keys (no inline default; see "i18n Keys"). Exported from `optio-ui`, for apps that
reuse or merge it.

---

### ProcessWidget / useProcessWidget / useProcessWidgetState

```ts
interface UseProcessWidgetOptions {
  readOnly?: boolean;       // passed to the widget as WidgetProps.readOnly
  hideEmptyRuns?: boolean;  // passed to the widget as WidgetProps.hideEmptyRuns
  placeholder?: ReactNode;  // returned (mode 'waiting') until an active process's widget can show; also WidgetProps.placeholder
}
function useProcessWidget(tree: ProcessTreeNode | null | undefined, options?: UseProcessWidgetOptions): ReactNode | null

interface UseProcessWidgetStateResult {
  widget: ReactNode;
  mode: 'live' | 'suspended' | 'resuming' | 'waiting';  // 'waiting' only with options.placeholder
}
function useProcessWidgetState(tree: ProcessTreeNode | null | undefined, options?: UseProcessWidgetOptions): UseProcessWidgetStateResult | null

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
`useProcessWidgetState(tree, options)?.widget ?? null`. `options.readOnly` adds
`readOnly: true` to the widget's props (absent otherwise); `ProcessDetailView` passes its own
`readOnly`. `options.hideEmptyRuns` and `options.placeholder` are passed on the same way.

**Resuming.** A relaunch clears `widgetData` until the task sets it again. When the hook
rendered a process with its suspended variant and that process is then launched (active, live
widget not ready: not widget-live with `widgetData`), it keeps returning the suspended variant
with `mode: 'resuming'`, so a host's layout does not flip between widget and no-widget. It
hands over to the live widget once that is ready (the live widget mounts hidden beside the
suspended variant, with `WidgetProps.onReady`, and is shown once it calls it, or after 10 s;
a suspend hands over the other way alike), and returns `null` as usual if the run ends
first. The latch is per hook instance and per process `_id`; a page opened mid-resume shows no
widget until the live one is ready. `ProcessDetailView` shows no Resume row in this mode.

`useProcessWidgetState` owns:

- Readiness gate: `tree.uiWidget` set, and either state is widget-live (`isWidgetLive`) or the
  process `isSuspended`; `tree.widgetData != null`. With `options.placeholder`, an active
  process that is not ready yet (and names a registered widget) gets the placeholder, mode
  `'waiting'`, instead of `null`.
- Registry lookup: `getWidget(uiWidget)` while live, `getSuspendedWidget(uiWidget)` while
  suspended. Live + unregistered → emits `console.warn` and returns `null`. Suspended +
  unregistered → returns `null` silently (no warning — a widget with no suspended variant,
  e.g. the built-in `iframe` widget, is expected to have nothing to show here).
- Database check (from `OptioProvider` / instance discovery). If unknown → emits `console.warn` and returns `null` (widget URLs need the database segment).
- `widgetProxyUrl` build: `${baseUrl}/api/widget/${database}/${prefix}/${tree._id}/` (database and prefix URI-encoded; trailing slash load-bearing). Built the same way for both modes, whether or not the suspended variant actually uses the proxy.

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
(Resume, or Resurrect when resurrectable) row above the widget, unless `readOnly`. Other consumers (e.g. an external app's
process detail page) are free to choose their own layout.

---

## Widget System

### registerWidget / WidgetComponent / WidgetProps

```ts
interface WidgetProps {
  process: any;            // full process tree root object (includes widgetData, uiWidget)
  apiBaseUrl: string;      // optio API base URL
  widgetProxyUrl: string;  // ${apiBaseUrl}/api/widget/${database}/${prefix}/${process._id}/ — trailing slash is load-bearing
  prefix: string;          // process namespace prefix
  database?: string;       // database of the instance (always set when useProcessWidgetState renders the widget)
  readOnly?: boolean;      // the host shows the process read-only: do not launch or resume it
  hideEmptyRuns?: boolean; // conversation widgets: leave out runs where nothing happened
  placeholder?: ReactNode; // the host's placeholder, for the widget's own loading states
  onReady?: () => void;    // given only while the widget mounts hidden for a handover; call once it shows the process
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
separate map (`getSuspendedWidget`, also exported from the package root — consumed internally
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
interface LaunchOptions {
  resume?: boolean;
  params?: Record<string, unknown>;  // a task factory's values, by parameter id
}

function useProcessActions(options?: {
  onResyncSuccess?: (clean: boolean) => void;
}): {
  launch: (processId: string, opts?: LaunchOptions) => void;
  launchAsync: (processId: string, opts?: LaunchOptions) => Promise<Process>;
  cancel: (processId: string) => void;
  dismiss: (processId: string) => void;
  resurrect: (processId: string) => void;
  resync: (metadataFilter?: ProcessMetadataFilter) => void;
  resyncClean: (metadataFilter?: ProcessMetadataFilter) => void;
  isResyncing: boolean;
}
```

All mutations invalidate the `['processes']` query key on success.
`resync` sends `body: {}`, `resyncClean` sends `body: { clean: true }`; a `metadataFilter`
is added to the body when given.
`onResyncSuccess(clean)` is called after a successful resync with the `clean` flag value.
`launch` sends `body: { sessionId: getSessionId() }`, plus `resume: true` when `opts.resume` is true
and `params` when `opts.params` is given (a task factory's launch: the API makes and launches its
child). It reports nothing. `launchAsync` sends the same, for an action that shows the outcome (a
vultus `useAction`, as the task factory form's): it resolves with the launched record (a factory's:
its child) and rejects with ts-rest's `{ status, body: { reason, message } }` on a refusal.
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
read is repeated. A 503 (the engine's conversation archive unreachable, e.g. during an engine
restart) gets 6 retries, 1 + 2 + 4 + 8 + 15 + 15 s apart; other transient errors (network,
other 5xx) get up to 2 retries.

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

Query key: `['process', database, prefix, id]`. Disabled when `id` is falsy, and when a
`MultiProcessStreamProvider` ancestor streams the process (its slice is returned instead).

---

### useProcessTree

```ts
function useProcessTree(
  id: string | undefined,
  options?: { refetchInterval?: number | false }  // default 5000ms
): any | null  // returns tree body or null
```

Query key: `['process-tree', database, prefix, id]`. Disabled when `id` is falsy.

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

Query key: `['process-tree-log', database, prefix, id]`. Disabled when `id` is falsy.

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
  processNotFound: boolean;  // the probe got a 404: terminal, no retry
  error: Error | null;       // probe or network failure; retrying continues
}
```

SSE endpoint: `{baseUrl}/api/processes/{processId}/tree/stream?prefix={prefix}&maxDepth={maxDepth}[&database={database}]`,
opened after a probe `GET {baseUrl}/api/processes/{processId}?prefix=…[&database=…]`.

Reconnects (probe first) 3 seconds after an error. Each component instance manages its
own `EventSource`, unless a `MultiProcessStreamProvider` ancestor streams the process
(`{baseUrl}/api/processes/tree/multi/stream`); then its slice is returned.

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

SSE endpoint: `{baseUrl}/api/processes/stream?prefix={prefix}[&database={database}][&metadataFilter={JSON}]`.

Uses a **module-level singleton** `EventSource` shared across all hook instances
(via `useSyncExternalStore`). Reconnects after 3 seconds on error. The singleton is
keyed on `(baseUrl, database, prefix, metadataFilter)` — supplying a different
`metadataFilter` from the same component closes the old EventSource and opens a
new one with the new query. Two components mounted concurrently with different
filters will fight: the most recent render wins, the other gets data for the wrong
filter. Hoist filter state above all consumers, or use a single top-level filter
selector. An app's own process filters give their filter as `useProcessFilter().metadataFilter`
(see "ProcessFilters").

## Types

### FilterGroup

```ts
export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';
```

Exported from `ProcessFilter.tsx`, re-exported from `index.ts`, as is `WithFilteredProcessesProps`
(see "ProcessFilters").

---

### ProcessTreeNode

```ts
interface ProcessUpdate {
  processId?: string;                // the process's stable id
  launchMode?: 'resume' | 'fresh';   // how its latest run started (tree streams)
  _id: string;
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
  warning?: string;                  // shown before a launch (a task factory's form shows it on top)
  taskFactory?: boolean;             // a task factory's record: its launch opens its launch form
  parameters?: unknown[];            // a task factory's parameters, for that form
}

export interface ProcessTreeNode extends ProcessUpdate {
  children: ProcessTreeNode[];
}
```

`MultiProcessUpdate` (`MultiProcessStreamContext.tsx`, the multi-tree stream's items) carries
`warning`, `taskFactory` and `parameters` too.

Exported from `useProcessStream.ts`, re-exported from `index.ts`.

## i18n Keys

Complete list of all translation keys used in component source files. The first group has
no inline default (the app must translate them); the second carries an English default.

| Key | Component | Usage |
|-----|-----------|-------|
| `processes.launch` | `LaunchControls` | Tooltip on the single launch button |
| `processes.cancel` | `ProcessItem`, `ProcessTreeView` | Tooltip / `aria-label` of the stop button when the task cannot resume |
| `processes.search` | `ProcessFilters` | The search field's label (its accessible name) and placeholder |
| `processes.filterAll` | `ProcessFilters` | A choice of the group field (select) |
| `processes.filterActive` | `ProcessFilters` | A choice of the group field (select) |
| `processes.filterHideCompleted` | `ProcessFilters` | A choice of the group field (select) |
| `processes.filterErrors` | `ProcessFilters` | A choice of the group field (select) |
| `processes.showDetails` | `ProcessFilters` | The "show details" field's label (checkbox) |
| `processes.showSpecial` | `ProcessFilters` | The "show special" field's label (checkbox) |
| `common.noData` | `ProcessLogPanel` | Empty state description |
| `status.<state>` | `ProcessStatusBadge` | State tag label (dynamic key, falls back to raw state) |
| `errors.processes.launch.<reason>` | `TaskFactoryLaunchModal` (`optioErrorRoutes`) | A launch refusal in the form, one key per `LaunchFailureReason`: `not-found`, `not-launchable`, `no-resume-support`, `launch-blocked`, `shutting-down`, `parameters-required`, `not-a-factory`, `invalid-parameters` (beside the field its message names). vultus-core's `routeApiError` reads `t(key)`, so there is no default: without the app's text the key shows |
| `processes.suspend` | `ProcessItem`, `ProcessTreeView` | Tooltip / `aria-label` of the stop button when the task can resume (default: "Suspend") |
| `processes.resume` | `LaunchControls` | Resume button tooltip when `supportsResume` and `hasSavedState` (default: "Resume") |
| `processes.restart` | `LaunchControls` | Dropdown menu item label (default: "Restart (discard saved state)") |
| `processes.moreOptions` | `LaunchControls` | Tooltip on the dropdown arrow of the Resume and Resurrect split buttons (default: "More options") |
| `processes.resurrect` | `LaunchControls` | Resurrect button `aria-label` (default: "Resurrect") |
| `processes.resurrectHint` | `LaunchControls` | Resurrect button tooltip (default: "Save the work left by the failed run, then resume") |
| `processes.resumeFromSnapshot` | `LaunchControls` | Resurrect menu item and confirmation title (default: "Resume from last snapshot") |
| `processes.restartDiscarding` | `LaunchControls` | Resurrect menu item and confirmation title (default: "Restart") |
| `processes.discardUnsavedWork` | `LaunchControls` | Confirmation body (default: "This discards the unsaved work left by the failed run.") |
| `processes.discardAndContinue` | `LaunchControls` | Confirmation OK button (default: "Discard and continue") |
| `processes.resurrecting` | `LaunchControls` | Resurrecting indicator tooltip (default: "Resurrecting: saving the unsaved work…") |
| `processes.resurrectingLabel` | `LaunchControls` | Resurrecting indicator `aria-label` (default: "Resurrecting") |
| `status.suspending` | `ProcessStatusBadge` | `cancel_requested` / `cancelling` label when `supportsResume` (default: "Suspending") |
| `status.suspended` | `ProcessStatusBadge` | `cancelled` label when `supportsResume` and `hasSavedState` (default: "Suspended") |
| `status.autoResumeScheduled` | `ProcessStatusBadge` | Tooltip / `aria-label` of the auto-resume stopwatch icon (default: "Scheduled for auto-restart") |
| `processes.factoryTitle` | `TaskFactoryLaunchModal` | The modal's title, `{{name}}` the factory's name (default: "Launch “{{name}}”") |
| `processes.factoryLaunch` | `TaskFactoryLaunchModal` | The launch button (default: "Launch") |
| `processes.factoryCancel` | `TaskFactoryLaunchModal` | The cancel button (default: "Cancel") |
| `processes.factoryRequired` | `TaskFactoryLaunchModal` | A required parameter without a value (default: "Required") |
| `processes.factoryMaxLength` | `TaskFactoryLaunchModal` | A text over its `maxLength`, `{{count}}` (default: "At most {{count}} characters") |
| `processes.launchEphemeral` | `TaskFactoryLaunchModal` | The `ephemeral` field's label (default: "Remove when finished") |
| `processes.launchEphemeralHelp` | `TaskFactoryLaunchModal` | Its help without a TTL (default: "Checked: the launch disappears from the list when it ends. Unchecked: it stays, with its log, until it is removed.") |
| `processes.launchEphemeralHelpTtl` | `TaskFactoryLaunchModal` | Its help with a TTL, `{{duration}}` in words (default: "Checked: … Unchecked: it stays, with its log, until it is removed, at most {{duration}} after it ends.") |
| `processes.durationSeconds_one` / `_other` | `TaskFactoryLaunchModal` | `{{duration}}` of the TTL help, `{{count}}` (defaults: "{{count}} second" / "{{count}} seconds") |
| `processes.durationMinutes_one` / `_other` | `TaskFactoryLaunchModal` | As above (defaults: "{{count}} minute" / "{{count}} minutes") |
| `processes.durationHours_one` / `_other` | `TaskFactoryLaunchModal` | As above (defaults: "{{count}} hour" / "{{count}} hours") |
| `processes.durationDays_one` / `_other` | `TaskFactoryLaunchModal` | As above (defaults: "{{count}} day" / "{{count}} days") |
