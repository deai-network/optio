# Add Client-Side Text Search to Process Filtering

**Base revision:** `b5bb7e0269369c1a9b97c0f3dbab7f9e6bd6ccf0` on branch `main` (as of 2026-04-16T00:00:00Z)

## Summary

Add a client-side text search input to the existing process filtering UI (`ProcessFilter.tsx`). The search filters processes by `name` and `description` fields. Matching text in the process name is highlighted in the list. Uses `@quaesitor-textus/antd` for the search input, context, and highlight components.

This is the first phase; server-side search will be added later.

---

## Architecture

`WithFilteredProcesses` is split into an outer shell and an inner provider:

- **Outer** (`WithFilteredProcesses`): renders `<WithSearch>` wrapping `<ProcessFilterInner>`. No state of its own.
- **Inner** (`ProcessFilterInner`): holds all existing filter state (`filterGroup`, `showDetails`, `showSpecial`). Calls `useSearchContext()` from `@quaesitor-textus/antd` to obtain the search `filterFunction`. Combines them into a single `filterFn`: search filter applied first, then the group/details/special logic.

The public API of `useProcessFilter()` is unchanged. Consumers get a single `filterFn` that incorporates both search and process-group filtering.

---

## Components

### `WithFilteredProcesses` (modified)

Renders `<WithSearch fields={['name', 'description']}>` wrapping `<ProcessFilterInner>`. All existing state and logic moves into `ProcessFilterInner`.

### `ProcessFilterInner` (new, internal)

Contains the existing `useState` declarations and the `useMemo` for `filterFn`. The memoised `filterFn` now:
1. Applies the search `filterFunction` (from `useSearchContext()`) to narrow the list.
2. Applies the existing group/details/special logic to the result.

Provides the `ProcessFilterContext` as before.

### `ProcessFilters` (modified)

Adds `<SearchInput />` at the top of the `<Space>`, before the existing `<Select>` and checkboxes. No other changes.

### `ProcessItem` (modified, in `ProcessList.tsx`)

In `nameContent`, both occurrences of `{process.name}` are replaced with `<HighlightedText text={process.name} />`. `ProcessItem` is always rendered inside `FilteredProcessList` → `WithFilteredProcesses` → `WithSearch`, so the context is available. When used standalone outside that tree, `HighlightedText` is expected to fall back to plain text (library behaviour — verify on integration).

---

## Data

Search fields: `name` (string, always present) and `description` (string, nullable/optional). Both passed to `WithSearch` so the library knows which fields to match against when `filterFunction` is called.

---

## Dependencies

Add `@quaesitor-textus/antd` as a dependency in `packages/optio-ui/package.json`.

---

## Out of scope

- Server-side search (future work)
- Highlighting in `description` (future work; `description` is currently shown only in a tooltip)
- `ProcessItem` used outside `WithFilteredProcesses` — no changes needed for that case beyond verifying `HighlightedText` degrades gracefully
