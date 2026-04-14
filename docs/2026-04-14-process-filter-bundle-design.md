# Process Filter Bundle Design

## Goal

Extend `ProcessFilters` from a pure controlled UI component into a self-contained bundle that provides both filter controls UI and filtering logic, so embedding applications can implement process filtering with minimal code.

## Architecture

React Context (option A). Filter state lives in a context provided by `WithFilteredProcesses`. The hook `useProcessFilter()` is the single internal API used by both the UI component and the list wrapper — and optionally by consumer code.

### New file: `src/components/ProcessFilter.tsx`

Replaces `src/components/ProcessFilters.tsx` (deleted).

**`WithFilteredProcesses`** — context provider, no UI. Owns `filterGroup`, `showDetails`, `showSpecial` via `useState`. Must wrap any subtree that uses `ProcessFilters`, `FilteredProcessList`, or `useProcessFilter`.

**`useProcessFilter()`** — reads from context, returns:
```typescript
{
  filterGroup: FilterGroup;
  setFilterGroup: (g: FilterGroup) => void;
  showDetails: boolean;
  setShowDetails: (v: boolean) => void;
  showSpecial: boolean;
  setShowSpecial: (v: boolean) => void;
  filterFn: (processes: any[]) => any[];
}
```

**`ProcessFilters`** — replaces the old props-based component. Calls `useProcessFilter()` internally. Same visual output (dropdown + two checkboxes). Takes no props.

**`FilteredProcessList`** — thin wrapper around `ProcessList`. Accepts the same props as `ProcessList` except `processes` is the raw unfiltered input. Calls `useProcessFilter()` to get `filterFn`, applies it, passes result to `ProcessList`.

### Filtering logic

Default state: `filterGroup = 'all'`, `showDetails = false`, `showSpecial = false`.

```typescript
filterFn = (processes) => processes.filter(p => {
  const state = p.status?.state;
  const isQuiet = state === 'idle' || state === 'done' || !state;

  if (isQuiet && !showDetails && (p.depth ?? 0) !== 0) return false;
  if (isQuiet && !showSpecial && p.special === true) return false;

  if (filterGroup === 'active') return state !== 'idle' && state !== 'done';
  if (filterGroup === 'hide_completed') return state !== 'done';
  if (filterGroup === 'errors') return state === 'failed';
  return true;
});
```

`showDetails = false` hides quiet (idle/done) non-root processes.
`showSpecial = false` hides quiet processes marked `special === true`.
Active processes always pass through quiet-state checks regardless of toggles.

### Typical consumer usage

```tsx
<WithFilteredProcesses>
  <ProcessFilters />
  <FilteredProcessList
    processes={rawProcesses}
    loading={!connected}
    onLaunch={launch}
    onCancel={cancel}
    onProcessClick={setSelectedProcessId}
  />
</WithFilteredProcesses>
```

Consumer code needing direct access to filter state or `filterFn`:
```tsx
const { filterFn, filterGroup, setFilterGroup } = useProcessFilter();
```

## Exports added to `src/index.ts`

- `WithFilteredProcesses`
- `FilteredProcessList`
- `useProcessFilter`
- `ProcessFilters` — already exported, no change

`FilterGroup` type remains exported from the new file.

## Testing

`src/__tests__/ProcessFilter.test.tsx`:
- Unit tests for `filterFn` covering all `FilterGroup` values, `showDetails`, and `showSpecial` combinations
- Render test: `FilteredProcessList` with a mixed process list renders only the expected subset

## Breaking changes

`ProcessFilters` no longer accepts props. Consuming apps must migrate to `WithFilteredProcesses` + `FilteredProcessList`. Migration note written to `~/private/guy-montag/docs/2026-04-14-optio-ui-process-filter-migration.md`.
