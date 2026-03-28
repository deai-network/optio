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
      <OptioProvider prefix="librarian" baseUrl="http://localhost:3000">
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

## Components

| Component | Description |
|-----------|-------------|
| `ProcessList` | Scrollable list of processes with launch/cancel buttons and progress bars |
| `ProcessItem` | Single process row — name, status badge, progress bar, action buttons |
| `ProcessStatusBadge` | Colored tag showing process state with live elapsed-time counter |
| `ProcessTreeView` | Ant Design `Tree` rendering a nested process hierarchy from SSE data |
| `ProcessLogPanel` | Scrolling monospace log viewer with level-colored tags; auto-scrolls to bottom |
| `ProcessFilters` | Filter group selector (all/active/hide_completed/errors) plus detail/special toggles |

## Hooks

| Hook | Description |
|------|-------------|
| `useProcessList` | Polls the process list endpoint; returns `{ processes, totalCount, isLoading }` |
| `useProcess` | Polls a single process by ID; returns `{ process, isLoading }` |
| `useProcessTree` | Polls the tree endpoint for a process; returns the tree body or `null` |
| `useProcessTreeLog` | Polls the tree log endpoint for a process; returns an array of log entries |
| `useProcessActions` | Returns imperative action functions: `launch`, `cancel`, `dismiss`, `resync`, `resyncClean` |
| `useProcessStream` | Opens an SSE connection to a single process tree stream; returns live `{ processes, tree, rootProcess, logs, connected }` |
| `useProcessListStream` | Opens a module-level singleton SSE connection to the process list stream; returns `{ processes, connected }` |

## i18n

Components require `react-i18next` to be configured in your application. All user-visible
strings are looked up via `useTranslation()` so you must provide translations for the
following keys:

| Key | Used by |
|-----|---------|
| `processes.launch` | `ProcessItem` — tooltip on launch button |
| `processes.cancel` | `ProcessItem`, `ProcessTreeView` — tooltip on cancel button |
| `processes.filterAll` | `ProcessFilters` — dropdown option |
| `processes.filterActive` | `ProcessFilters` — dropdown option |
| `processes.filterHideCompleted` | `ProcessFilters` — dropdown option |
| `processes.filterErrors` | `ProcessFilters` — dropdown option |
| `processes.showDetails` | `ProcessFilters` — checkbox label |
| `processes.showSpecial` | `ProcessFilters` — checkbox label |
| `status.<state>` | `ProcessStatusBadge` — state label (e.g. `status.running`, `status.done`, `status.failed`) |
| `common.noData` | `ProcessLogPanel` — empty state message |

## No Router Dependency

`optio-ui` has no dependency on React Router or any other routing library.
Components never render `<Link>` elements. Navigation is handled entirely through
the `onProcessClick?: (processId: string) => void` callback on `ProcessList` and
`ProcessItem` — your application decides what to do when a process is clicked (e.g.
`navigate(/processes/${id})`).

## See Also

- [Optio Overview](../../README.md)
