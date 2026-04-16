# Text Search Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add client-side text search (name + description) to the process filtering UI using `@quaesitor-textus/antd`.

**Architecture:** `WithFilteredProcesses` is split into an outer shell (renders `<WithSearch>`) and `ProcessFilterInner` (holds all filter state). `ProcessFilterInner` calls `useSearchContext` with a `mapping` function over `name` and `description`, and folds the returned `filterFunction` into the combined `filterFn` exposed by `useProcessFilter()`. `ProcessFilters` gains a `<SearchInput />`. `ProcessItem` wraps `process.name` in `<HighlightedText />`.

**Tech Stack:** React, Ant Design 5, `@quaesitor-textus/antd` (WithSearch, useSearchContext, SearchInput, HighlightedText), vitest, @testing-library/react

---

## File Map

| File | Change |
|------|--------|
| `packages/optio-ui/package.json` | Add `@quaesitor-textus/antd` dependency |
| `packages/optio-ui/src/components/ProcessFilter.tsx` | Split outer/inner, embed `WithSearch`, use `useSearchContext`, add `SearchInput` |
| `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx` | Add search-related test cases |
| `packages/optio-ui/src/components/ProcessList.tsx` | Wrap `process.name` in `<HighlightedText />` |

---

## Task 1: Install the dependency

**Files:**
- Modify: `packages/optio-ui/package.json`

- [ ] **Step 1: Add the package to dependencies**

In `packages/optio-ui/package.json`, add to the `"dependencies"` object (after `"antd"`):

```json
"@quaesitor-textus/antd": "^0.1.2",
```

- [ ] **Step 2: Install**

Run from the repo root:

```bash
pnpm install
```

Expected: lock file updated, package installed under `node_modules/@quaesitor-textus/antd`.

---

## Task 2: Write failing tests for search behavior

**Files:**
- Modify: `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx`

- [ ] **Step 1: Add search test imports**

At the top of `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx`, add to the existing import block:

```tsx
import { useSearchContext } from '@quaesitor-textus/antd';
```

- [ ] **Step 2: Add a combined hook used only in search tests**

After the existing `wrapper` function, add:

```tsx
// Used in search tests: exposes both filterFn and setQuery from the same render
function useFilterAndSearch() {
  const { filterFn } = useProcessFilter();
  const { setQuery } = useSearchContext();
  return { filterFn, setQuery };
}
```

- [ ] **Step 3: Add the search test suite**

At the bottom of the file (after the existing `describe` blocks), add:

```tsx
describe('useProcessFilter — search integration', () => {
  it('empty query: all processes pass', () => {
    const { result } = renderHook(() => useFilterAndSearch(), { wrapper });
    const processes = [
      makeProcess({ _id: 'p1', name: 'alpha', depth: 0 }),
      makeProcess({ _id: 'p2', name: 'beta', depth: 0 }),
    ];
    expect(result.current.filterFn(processes)).toHaveLength(2);
  });

  it('query matches name: only matching processes pass', () => {
    const { result } = renderHook(() => useFilterAndSearch(), { wrapper });
    act(() => result.current.setQuery('alp'));
    const processes = [
      makeProcess({ _id: 'p1', name: 'alpha', depth: 0 }),
      makeProcess({ _id: 'p2', name: 'beta', depth: 0 }),
    ];
    const filtered = result.current.filterFn(processes);
    expect(filtered).toHaveLength(1);
    expect(filtered[0]._id).toBe('p1');
  });

  it('query matches description: only matching processes pass', () => {
    const { result } = renderHook(() => useFilterAndSearch(), { wrapper });
    act(() => result.current.setQuery('detail'));
    const processes = [
      makeProcess({ _id: 'p1', name: 'alpha', description: 'some detail here', depth: 0 }),
      makeProcess({ _id: 'p2', name: 'beta', description: 'nothing here', depth: 0 }),
    ];
    const filtered = result.current.filterFn(processes);
    expect(filtered).toHaveLength(1);
    expect(filtered[0]._id).toBe('p1');
  });

  it('search and filterGroup compose: only matched + active processes pass', () => {
    const { result } = renderHook(
      () => ({ ...useProcessFilter(), search: useSearchContext() }),
      { wrapper },
    );
    act(() => result.current.search.setQuery('task'));
    act(() => result.current.setFilterGroup('active'));
    const processes = [
      makeProcess({ _id: 'p1', name: 'task-a', status: { state: 'running' }, depth: 0 }),
      makeProcess({ _id: 'p2', name: 'task-b', status: { state: 'idle' }, depth: 0 }),
      makeProcess({ _id: 'p3', name: 'other', status: { state: 'running' }, depth: 0 }),
    ];
    const filtered = result.current.filterFn(processes);
    expect(filtered).toHaveLength(1);
    expect(filtered[0]._id).toBe('p1');
  });
});
```

- [ ] **Step 4: Run tests to confirm they fail**

```bash
pnpm --filter optio-ui test
```

Expected: the new `search integration` tests fail (import of `useSearchContext` or search behavior not yet wired up in `WithFilteredProcesses`). Existing tests should still pass.

---

## Task 3: Implement ProcessFilter changes

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessFilter.tsx`

- [ ] **Step 1: Replace the file contents**

Replace the entire contents of `packages/optio-ui/src/components/ProcessFilter.tsx` with:

```tsx
import { createContext, useContext, useState, useMemo, type ReactNode } from 'react';
import { Checkbox, Select, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import { WithSearch, useSearchContext, SearchInput } from '@quaesitor-textus/antd';
import { ProcessList } from './ProcessList.js';

export type FilterGroup = 'all' | 'active' | 'hide_completed' | 'errors';

const QUIET_STATES = new Set(['idle', 'done']);

interface ProcessFilterContextValue {
  filterGroup: FilterGroup;
  setFilterGroup: (g: FilterGroup) => void;
  showDetails: boolean;
  setShowDetails: (v: boolean) => void;
  showSpecial: boolean;
  setShowSpecial: (v: boolean) => void;
  filterFn: (processes: any[]) => any[];
}

const ProcessFilterContext = createContext<ProcessFilterContextValue>(null as any);

function ProcessFilterInner({ children }: { children: ReactNode }) {
  const [filterGroup, setFilterGroup] = useState<FilterGroup>('all');
  const [showDetails, setShowDetails] = useState(false);
  const [showSpecial, setShowSpecial] = useState(false);

  const { filterFunction: searchFilter } = useSearchContext<any>({
    mapping: (p: any) => [p.name, p.description].filter(Boolean).join(' '),
  });

  const filterFn = useMemo(
    () => (processes: any[]) => {
      const searched = processes.filter(searchFilter);
      return searched.filter((p) => {
        const state = p.status?.state;
        const isQuiet = QUIET_STATES.has(state) || !state;

        if (isQuiet && !showDetails && (p.depth ?? 0) !== 0) return false;
        if (isQuiet && !showSpecial && p.special === true) return false;

        if (filterGroup === 'active') return state !== 'idle' && state !== 'done';
        if (filterGroup === 'hide_completed') return state !== 'done';
        if (filterGroup === 'errors') return state === 'failed';
        return true;
      });
    },
    [searchFilter, filterGroup, showDetails, showSpecial],
  );

  return (
    <ProcessFilterContext.Provider
      value={{ filterGroup, setFilterGroup, showDetails, setShowDetails, showSpecial, setShowSpecial, filterFn }}
    >
      {children}
    </ProcessFilterContext.Provider>
  );
}

export function WithFilteredProcesses({ children }: { children: ReactNode }) {
  return (
    <WithSearch>
      <ProcessFilterInner>{children}</ProcessFilterInner>
    </WithSearch>
  );
}

export function useProcessFilter(): ProcessFilterContextValue {
  return useContext(ProcessFilterContext);
}

export function ProcessFilters() {
  const { filterGroup, setFilterGroup, showDetails, setShowDetails, showSpecial, setShowSpecial } = useProcessFilter();
  const { t } = useTranslation();

  return (
    <Space size={16} style={{ marginBottom: 16 }}>
      <SearchInput style={{ width: 200 }} placeholder={t('processes.search')} />
      <Select
        value={filterGroup}
        onChange={setFilterGroup}
        style={{ width: 180 }}
        options={[
          { value: 'all', label: t('processes.filterAll') },
          { value: 'active', label: t('processes.filterActive') },
          { value: 'hide_completed', label: t('processes.filterHideCompleted') },
          { value: 'errors', label: t('processes.filterErrors') },
        ]}
      />
      <Checkbox checked={showDetails} onChange={(e) => setShowDetails(e.target.checked)}>
        {t('processes.showDetails')}
      </Checkbox>
      <Checkbox checked={showSpecial} onChange={(e) => setShowSpecial(e.target.checked)}>
        {t('processes.showSpecial')}
      </Checkbox>
    </Space>
  );
}

interface FilteredProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}

export function FilteredProcessList({ processes, ...rest }: FilteredProcessListProps) {
  const { filterFn } = useProcessFilter();
  return <ProcessList processes={filterFn(processes)} {...rest} />;
}
```

- [ ] **Step 2: Run tests**

```bash
pnpm --filter optio-ui test
```

Expected: all tests pass, including the new search integration tests.

If any search tests still fail, check:
- That `useSearchContext` in the test file and in `ProcessFilterInner` share the same `WithSearch` context (they should, since `wrapper` uses `WithFilteredProcesses` which now embeds `WithSearch`).
- That `filterFunction` is correctly returned by `useSearchContext`.

---

## Task 4: Add HighlightedText to ProcessItem

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessList.tsx`

- [ ] **Step 1: Add the import**

At the top of `packages/optio-ui/src/components/ProcessList.tsx`, add after the existing imports:

```tsx
import { HighlightedText } from '@quaesitor-textus/antd';
```

- [ ] **Step 2: Replace both `{process.name}` occurrences in `nameContent`**

Find the `nameContent` block (lines ~42–48):

```tsx
  const nameContent = onProcessClick ? (
    <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => onProcessClick(process._id)}>
      {process.name}
    </Button>
  ) : (
    <Text>{process.name}</Text>
  );
```

Replace with:

```tsx
  const nameContent = onProcessClick ? (
    <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => onProcessClick(process._id)}>
      <HighlightedText text={process.name} />
    </Button>
  ) : (
    <Text><HighlightedText text={process.name} /></Text>
  );
```

- [ ] **Step 3: Run tests**

```bash
pnpm --filter optio-ui test
```

Expected: all tests pass. (Existing `ProcessList` tests mock `ProcessList` itself so they are unaffected. If `HighlightedText` throws when used outside `WithSearch`, it will surface here — fix by verifying the library's default context value or by wrapping in a try/catch; the spec notes this as a known verification point.)

---

## Task 5: Commit

- [ ] **Step 1: Stage the changed files**

```bash
git add packages/optio-ui/package.json \
        packages/optio-ui/src/components/ProcessFilter.tsx \
        packages/optio-ui/src/__tests__/ProcessFilter.test.tsx \
        packages/optio-ui/src/components/ProcessList.tsx \
        pnpm-lock.yaml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "Add client-side text search to process filtering (name + description)"
```

---

## Notes

- The `processes.search` i18n key used in `SearchInput`'s placeholder (`t('processes.search')`) needs to be added to all i18n resource files in the consuming app. The `optio-ui` package itself doesn't own those files — the app integrating this component is responsible.
- `HighlightedText` behaviour when rendered outside a `WithSearch` tree is untested. Verify manually after first render in the dashboard.
