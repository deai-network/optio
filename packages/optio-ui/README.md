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
| `vultus-core` | >=0.5.3 |

`vultus-core` is a peer so that an app has a single copy of it, and with it a single
components context (see [Loading spinner](#loading-spinner)). An app that uses both should
list `vultus-core` itself, at the range its vultus-antd depends on, so that optio-ui's peer
and vultus-antd's dependency resolve to one copy (two copies mean two contexts, and optio-ui
would quietly keep antd's spinners).

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
| `ProcessFilters` | Search box, filter group selector (all/active/hide_completed/errors) and detail/special toggles; reads its state from an enclosing `WithFilteredProcesses`, whose `FilteredProcessList` applies it |

## Hooks

| Hook | Description |
|------|-------------|
| `useProcessList` | Polls the process list endpoint with optional metadata filtering; returns `{ processes, totalCount, isLoading }` |
| `useProcess` | Polls a single process by ID; returns `{ process, isLoading }` |
| `useProcessTree` | Polls the tree endpoint for a process; returns the tree body or `null` |
| `useProcessTreeLog` | Polls the tree log endpoint for a process; returns an array of log entries |
| `useProcessActions` | Returns imperative action functions: `launch`, `cancel`, `dismiss`, `resurrect`, `resync`, `resyncClean` |
| `useProcessConversation` | Reads a suspended process's saved conversation record (`GET /processes/:id/conversation`); returns the ts-rest react-query result (`data.body` is `{ events, capturedAt }` on 200; non-2xx responses surface as `error`). Never retries a 4xx. |
| `useProcessStream` | Opens an SSE connection to a single process tree stream; returns live `{ processes, tree, rootProcess, logs, connected, processNotFound, error }` |
| `useProcessListStream` | Opens a module-level singleton SSE connection with optional metadata filtering; returns `{ processes, connected }`. Only one filter active at a time. |

## i18n

Components require `react-i18next` to be configured in your application. All user-visible
strings are looked up via `useTranslation()` so you must provide translations for the
following keys:

| Key | Used by |
|-----|---------|
| `processes.launch` | `LaunchControls` (in `ProcessItem`, `ProcessTreeView`) — tooltip on launch button |
| `processes.cancel` | `ProcessItem`, `ProcessTreeView` — tooltip on cancel button (a task that can resume shows `processes.suspend` instead) |
| `processes.search` | `ProcessFilters` — search box placeholder |
| `processes.filterAll` | `ProcessFilters` — dropdown option |
| `processes.filterActive` | `ProcessFilters` — dropdown option |
| `processes.filterHideCompleted` | `ProcessFilters` — dropdown option |
| `processes.filterErrors` | `ProcessFilters` — dropdown option |
| `processes.showDetails` | `ProcessFilters` — checkbox label |
| `processes.showSpecial` | `ProcessFilters` — checkbox label |
| `status.<state>` | `ProcessStatusBadge` — state label (e.g. `status.running`, `status.done`, `status.failed`) |
| `common.noData` | `ProcessLogPanel` — empty state message |

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
