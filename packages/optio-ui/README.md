# optio-ui

React components and hooks for monitoring and controlling Optio processes.

<!-- TODO: Add UI screenshot here when available -->
<!-- ![Optio UI](../../docs/images/ui-screenshot.png) -->

## Install

```bash
npm install optio-ui
```

## Peer Dependencies

| Package | Version |
|---------|---------|
| `react` | >=18 |
| `react-dom` | >=18 |
| `@tanstack/react-query` | >=5 |
| `react-i18next` | >=15 |
| `i18next` | >=24 |
| `antd` | >=6 |
| `@ant-design/icons` | >=6 |
| `vultus-core` | >=0.5.4 |
| `vultus-antd` | >=0.7.2 |

`vultus-core` is a peer so that an app has a single copy of it, and with it a single
components context (see [Loading spinner](#loading-spinner)). The app should list
`vultus-core` itself, at the range its vultus-antd depends on, so that optio-ui's peer
and vultus-antd's dependency resolve to one copy (two copies mean two contexts: optio-ui
would quietly keep antd's spinners, and a task factory's launch form would not find its form).
`vultus-antd` gives that form its widgets (`TaskFactoryLaunchModal`).

## Quick Setup

Wrap your application (or the relevant subtree) with `OptioProvider`, then use
components and hooks anywhere inside it. The provider requires a `QueryClient` from
`@tanstack/react-query` to already be present higher up the tree.

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OptioProvider, ProcessList, useProcessList, useProcessActions } from 'optio-ui';

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <OptioProvider prefix="optio" database="myapp">
        <ProcessPage />
      </OptioProvider>
    </QueryClientProvider>
  );
}

function ProcessPage() {
  const { processes, isLoading } = useProcessList();
  const { launch, cancel } = useProcessActions();

  return (
    <ProcessList
      processes={processes}
      loading={isLoading}
      onLaunch={launch}
      onCancel={cancel}
      onProcessClick={(id) => console.log('clicked', id)}
    />
  );
}
```

`prefix` and `database` name the optio instance. Left out, they are taken from instance
discovery, but only when exactly one instance is visible; otherwise `prefix` falls back to
`"optio"` and `database` stays unset. Process widgets need the database (without it they fall
back to the default view), so pass both.

Serve the API from the page's own origin and leave `baseUrl` at its default `''`. optio-api
sends no CORS headers, and optio-ui opens its event streams (`EventSource`) without
`withCredentials`, so the browser blocks a cross-origin `baseUrl` (e.g. `http://localhost:3000`
from a dev server on another port), and with CORS added its streams would still carry no
session cookie. In development, let the dev server proxy `/api` to the API server, with
`ws: true` so that widget WebSockets pass too:

```ts
// vite.config.ts
server: { proxy: { '/api': { target: 'http://localhost:3000', ws: true } } }
```

Section 9 of [How to set up a new optio app](../../docs/how-to-set-up-a-new-optio-app.md)
wires a complete app (providers, i18n, proxy) this way.

## Components

| Component | Description |
|-----------|-------------|
| `ProcessList` | Scrollable list of processes with launch/cancel buttons and progress bars |
| `ProcessItem` | Single process row — name, status badge, progress bar, action buttons |
| `ProcessStatusBadge` | Colored tag showing process state with live elapsed-time counter |
| `ProcessTreeView` | Ant Design `Tree` rendering a nested process hierarchy from SSE data |
| `ProcessLogPanel` | Scrolling monospace log viewer with level-colored tags; auto-scrolls to bottom |
| `ProcessFilters` | The filters of a process list, each a vultus field: the app's own filters over the metadata that come first, search, filter group (all/active/hide_completed/errors), "show details" and "show special", then the app's own that come last; reads its state from an enclosing `WithFilteredProcesses`, whose `FilteredProcessList` applies it (see [Process filters](#process-filters)) |
| `TaskFactoryLaunchModal` | A task factory's launch form: a field per parameter (vultus-antd), a built-in "Remove when finished" field (the launch's `ephemeral`), the factory's warning, Launch; a refusal shows on the field it names. `overrides` (field id to a partial vultus definition, `mergeOverrides`) changes any field, e.g. hides one and launches with its value. The launch button of a factory's record (in `ProcessItem`, `ProcessTreeView`, `ProcessDetailView`) opens it; `onLaunched(child)` gets the launch. `launchOverrides` (a factory's `processId` to its overrides) on `ProcessItem`, `ProcessList`, `FilteredProcessList`, `ProcessTreeView` and `ProcessDetailView` gives a factory's play button its overrides |
| `TaskFactoryLaunchButton` | A button (`factoryId`, `overrides?`, `onLaunched?`, `size?`) that opens a task factory's launch form from anywhere in an app; `useTaskFactory(factoryId, { overrides, onLaunched })` gives `{ launch, isPending, dialog }` for a custom control |

## Hooks

| Hook | Description |
|------|-------------|
| `useProcessList` | Polls the process list endpoint with optional metadata filtering; returns `{ processes, totalCount, isLoading }` |
| `useProcess` | Polls a single process by ID; returns `{ process, isLoading }` |
| `useProcessTree` | Polls the tree endpoint for a process; returns the tree body or `null` |
| `useProcessTreeLog` | Polls the tree log endpoint for a process; returns an array of log entries |
| `useProcessActions` | Returns imperative action functions: `launch` (with a task factory's `params`), `launchAsync` (the same, resolving with the launched record, rejecting with the refusal), `cancel`, `dismiss`, `resurrect`, `resync`, `resyncClean` |
| `useProcessConversation` | Reads a suspended process's saved conversation record (`GET /processes/:id/conversation`); returns the ts-rest react-query result (`data.body` is `{ events, capturedAt }` on 200; non-2xx responses surface as `error`). Never retries a 4xx. |
| `useProcessStream` | Opens an SSE connection to a single process tree stream; returns live `{ processes, tree, rootProcess, logs, connected, processNotFound, error }` |
| `useProcessListStream` | Opens a module-level singleton SSE connection with optional metadata filtering; returns `{ processes, connected }`. Only one filter active at a time. |
| `useProcessFilter` | The filter state of the enclosing `WithFilteredProcesses`: the built-in filters' values, `filterFn` (applies them), the app filters' `filterValues` and their `metadataFilter` |

## Process filters

`WithFilteredProcesses` holds the filter state of a process list; `ProcessFilters` renders
its filters as vultus-antd fields in one row, and `FilteredProcessList` shows the processes
that pass the built-in ones (search over name and description, the filter group, "show
details", "show special"; applied in the browser).

An app adds its own filters over the process metadata in two slots, `customFiltersBegin`
(before the built-in filters in `ProcessFilters`' row) and `customFiltersEnd` (after them):
vultus-core field definitions of kind `oneOf`, `manyOf`, `oneOfTree` or `manyOfTree`, their ids
unique across both, each 240px wide in the row. Each filters the metadata key of its id;
`filterFields` names another (`{ modules: 'module' }`). They start at their
`initialValue`, else at their kind's own default (a many-of: nothing chosen). Their predicate,
made by [vultus-filtrum](https://www.npmjs.com/package/vultus-filtrum) (`toPredicate`), is
`useProcessFilter().metadataFilter`; it is `undefined` while there are no app filters or none
has a value, and keeps its identity while it says the same. Hand it to the list stream, from a
component inside `WithFilteredProcesses`: optio-api applies it to the processes' metadata.

Every filter's value lives in a store, under the filter's id (`search`, `filterGroup`,
`showDetails`, `showSpecial`, and the app filters' ids; a value at its default is absent).
`WithFilteredProcesses`'s optional `store` is vultus-core's `ArgStore`: an app gives one over its
URL, and a filtered list can be linked; a change to the filters replaces the stored value (Back
does not step through them), and a change made to the store from outside shows at once. Without
a `store`, the filters keep a memory store of their own, gone on unmount.

```tsx
import type { FieldDefinition } from 'vultus-core';
import {
  FilteredProcessList, ProcessFilters, WithFilteredProcesses, useProcessFilter, useProcessListStream,
} from 'optio-ui';

// A module constant (or memoised): the definitions of the app's own filters.
const PROCESS_FILTERS: FieldDefinition[] = [{
  kind: 'manyOf',
  id: 'module',            // filters metadata.module
  label: 'Module',
  placeholder: 'All modules',
  widget: 'select',        // a manyOf's default widget is a checkbox per choice
  choices: [
    { value: 'algebra', label: 'Algebra' },
    { value: 'geometry', label: 'Geometry' },
  ],
}];

export function ProcessesPage() {
  return (
    <WithFilteredProcesses customFiltersBegin={PROCESS_FILTERS}>
      <Processes />
    </WithFilteredProcesses>
  );
}

function Processes() {
  // With Algebra and Geometry chosen: { module: { in: ['algebra', 'geometry'] } }
  const { metadataFilter } = useProcessFilter();
  const { processes, connected } = useProcessListStream({ metadataFilter });
  return (
    <>
      <ProcessFilters />
      <FilteredProcessList processes={processes} loading={!connected} />
    </>
  );
}
```

The list stream is one per page: every `useProcessListStream` on it should pass the same
`metadataFilter`. `useProcessList({ metadataFilter })` takes it too.

## i18n

Components require `react-i18next` to be configured in your application. All user-visible
strings are looked up via `useTranslation()` so you must provide translations for the
following keys:

| Key | Used by |
|-----|---------|
| `processes.launch` | `LaunchControls` (in `ProcessItem`, `ProcessTreeView`), `useTaskFactory` — tooltip on launch button; label of a factory's launch action |
| `processes.deleted`, `processes.notFound` | `ProcessDetailView` — the notice over the last view of a process deleted while open; a process it never showed (defaults "Process deleted.", "Process not found.") |
| `processes.removeLaunch` | `LaunchControls` — tooltip of the remove button of an ended launch (default "Remove") |
| `processes.factoryLoading`, `processes.factoryNotAFactory`, `processes.factoryLaunching` | `useTaskFactory` — why `launch` is disabled (defaults "Loading…", "Not a task factory", "Launching…") |
| `errors.processes.dismiss.<reason>` | `LaunchControls` — a refused removal (`optioErrorRoutes`; `not-found`, `not-dismissable`) |
| `processes.cancel` | `ProcessItem`, `ProcessTreeView` — tooltip on cancel button (a task that can resume shows `processes.suspend` instead) |
| `processes.search` | `ProcessFilters` — the search field's label and placeholder |
| `processes.filterAll` | `ProcessFilters` — a choice of the filter group |
| `processes.filterActive` | `ProcessFilters` — a choice of the filter group |
| `processes.filterHideCompleted` | `ProcessFilters` — a choice of the filter group |
| `processes.filterErrors` | `ProcessFilters` — a choice of the filter group |
| `processes.showDetails` | `ProcessFilters` — the "show details" checkbox's label |
| `processes.showSpecial` | `ProcessFilters` — the "show special" checkbox's label |
| `status.<state>` | `ProcessStatusBadge` — state label (e.g. `status.running`, `status.done`, `status.failed`) |
| `common.noData` | `ProcessLogPanel` — empty state message |
| `errors.processes.launch.<reason>` | `TaskFactoryLaunchModal` — a refused launch, one key per launch failure reason (`optioErrorRoutes`; e.g. `errors.processes.launch.shutting-down`) |

The other keys (e.g. `processes.suspend`, `processes.resume`, `status.suspended`) carry an
English default; [AGENTS.md](AGENTS.md#i18n-keys) lists them all.

## Loading spinner

An app can lend its own spinner through vultus-core's components slot. optio-ui then shows
it in place of antd's spinners in `ProcessList` and `LaunchControls`, and before the
"Loading…" text in `ProcessDetailView` and the built-in `iframe` widget:

```tsx
import { VultusComponentsContext } from 'vultus-core';

<VultusComponentsContext.Provider value={{ Spinner: MySpinner }}>
  <App />
</VultusComponentsContext.Provider>
```

(With vultus-antd, `<VultusProvider components={{ Spinner: MySpinner }}>` does the same.)
`MySpinner` receives vultus-core's `SpinnerProps`: a required `spinnerKey` (the spinner's
identity), an optional `size` and an optional `label`.

| Where | `spinnerKey` | `size` |
|-------|--------------|--------|
| `ProcessList` while `loading` (the List's indicator) | `optio:process-list` | `'default'` |
| `LaunchControls` "Resurrecting" indicator (in place of antd's loading icon) | `optio:process:<id>:resurrecting` | `'small'` |
| `ProcessDetailView` before "Loading…" | `optio:process:<id>:detail` | `'small'` |
| Built-in `iframe` widget before "Loading…" | `optio:process:<id>:widget` | `'small'` |

`<id>` is the process `_id`. Inside a vultus-core `<SpinnerScope name="...">` each key is
prefixed with the scope chain (`outer/inner/optio:process-list`), so a page showing several
instances keeps the keys unique. Without a lent spinner nothing changes.

## No Router Dependency

`optio-ui` has no dependency on React Router or any other routing library.
Components never render `<Link>` elements. Navigation is handled entirely through
the `onProcessClick?: (processId: string) => void` callback on `ProcessList` and
`ProcessItem` — your application decides what to do when a process is clicked (e.g.
`navigate(/processes/${id})`).

## See Also

- [Optio Overview](../../README.md)
