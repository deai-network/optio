# Process Filter Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the props-based `ProcessFilters` component with a self-contained filter bundle (`WithFilteredProcesses`, `ProcessFilters`, `FilteredProcessList`, `useProcessFilter`) that owns filter state internally and wires UI to logic automatically.

**Architecture:** A React context (`ProcessFilterContext`) holds `filterGroup`, `showDetails`, `showSpecial` state. `useProcessFilter()` reads this context and derives `filterFn`. `ProcessFilters` and `FilteredProcessList` both call `useProcessFilter()` internally — consumers only need to place `<WithFilteredProcesses>` and the two child components; no prop threading required.

**Tech Stack:** React 19, TypeScript, Ant Design 5, Vitest, @testing-library/react (jsdom)

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `packages/optio-ui/src/components/ProcessFilter.tsx` | Context, provider, hook, `ProcessFilters` UI, `FilteredProcessList` |
| Delete | `packages/optio-ui/src/components/ProcessFilters.tsx` | Replaced by above |
| Modify | `packages/optio-ui/src/index.ts` | Add new exports, keep `ProcessFilters` export pointing to new file |
| Create | `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx` | Tests for filter logic and `FilteredProcessList` wiring |

---

### Task 1: filterFn logic tests

**Files:**
- Create: `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx`

- [ ] **Step 1: Write failing tests for the filter function**

Create `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';

// We'll import these once the implementation exists.
// For now this file won't compile — that's expected (RED phase).
import { WithFilteredProcesses, useProcessFilter } from '../components/ProcessFilter.js';

function makeProcess(overrides: Record<string, any> = {}) {
  return {
    _id: 'abc',
    status: { state: 'idle' },
    depth: 0,
    special: false,
    ...overrides,
  };
}

function wrapper({ children }: { children: React.ReactNode }) {
  return <WithFilteredProcesses>{children}</WithFilteredProcesses>;
}

describe('useProcessFilter — filterFn', () => {
  it('default state: shows all root processes', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 0 }),
      makeProcess({ status: { state: 'done' }, depth: 0 }),
      makeProcess({ status: { state: 'running' }, depth: 0 }),
    ];
    expect(result.current.filterFn(processes)).toHaveLength(3);
  });

  it('default state: hides idle non-root processes (showDetails=false)', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 0 }),
      makeProcess({ status: { state: 'idle' }, depth: 1 }),
      makeProcess({ status: { state: 'running' }, depth: 1 }),
    ];
    // depth-1 idle is hidden, depth-0 idle and running are shown
    expect(result.current.filterFn(processes)).toHaveLength(2);
  });

  it('default state: hides special quiet processes (showSpecial=false)', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 0, special: true }),
      makeProcess({ status: { state: 'running' }, depth: 0, special: true }),
    ];
    // idle+special is hidden, running+special passes (not quiet)
    expect(result.current.filterFn(processes)).toHaveLength(1);
    expect(result.current.filterFn(processes)[0].status.state).toBe('running');
  });

  it('filterGroup=active: shows only non-idle, non-done', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    act(() => result.current.setFilterGroup('active'));
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 0 }),
      makeProcess({ status: { state: 'done' }, depth: 0 }),
      makeProcess({ status: { state: 'running' }, depth: 0 }),
      makeProcess({ status: { state: 'failed' }, depth: 0 }),
    ];
    const filtered = result.current.filterFn(processes);
    expect(filtered).toHaveLength(2);
    expect(filtered.map((p: any) => p.status.state)).toEqual(['running', 'failed']);
  });

  it('filterGroup=hide_completed: hides done only', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    act(() => result.current.setFilterGroup('hide_completed'));
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 0 }),
      makeProcess({ status: { state: 'done' }, depth: 0 }),
      makeProcess({ status: { state: 'failed' }, depth: 0 }),
    ];
    const filtered = result.current.filterFn(processes);
    expect(filtered).toHaveLength(2);
    expect(filtered.map((p: any) => p.status.state)).toEqual(['idle', 'failed']);
  });

  it('filterGroup=errors: shows only failed', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    act(() => result.current.setFilterGroup('errors'));
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 0 }),
      makeProcess({ status: { state: 'failed' }, depth: 0 }),
      makeProcess({ status: { state: 'running' }, depth: 0 }),
    ];
    const filtered = result.current.filterFn(processes);
    expect(filtered).toHaveLength(1);
    expect(filtered[0].status.state).toBe('failed');
  });

  it('showDetails=true: shows quiet non-root processes', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    act(() => result.current.setShowDetails(true));
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 1 }),
      makeProcess({ status: { state: 'done' }, depth: 2 }),
    ];
    expect(result.current.filterFn(processes)).toHaveLength(2);
  });

  it('showSpecial=true: shows quiet special processes', () => {
    const { result } = renderHook(() => useProcessFilter(), { wrapper });
    act(() => result.current.setShowSpecial(true));
    const processes = [
      makeProcess({ status: { state: 'idle' }, depth: 0, special: true }),
    ];
    expect(result.current.filterFn(processes)).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd packages/optio-ui && pnpm test 2>&1 | grep -E "FAIL|Cannot find|error"
```

Expected: compile/import error — `ProcessFilter.js` does not exist yet.

---

### Task 2: Implement ProcessFilter.tsx

**Files:**
- Create: `packages/optio-ui/src/components/ProcessFilter.tsx`

- [ ] **Step 1: Create the file**

```tsx
import { createContext, useContext, useState, useMemo, type ReactNode } from 'react';
import { Checkbox, Select, Space } from 'antd';
import { useTranslation } from 'react-i18next';
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

export function WithFilteredProcesses({ children }: { children: ReactNode }) {
  const [filterGroup, setFilterGroup] = useState<FilterGroup>('all');
  const [showDetails, setShowDetails] = useState(false);
  const [showSpecial, setShowSpecial] = useState(false);

  const filterFn = useMemo(() => (processes: any[]) => processes.filter((p) => {
    const state = p.status?.state;
    const isQuiet = QUIET_STATES.has(state) || !state;

    if (isQuiet && !showDetails && (p.depth ?? 0) !== 0) return false;
    if (isQuiet && !showSpecial && p.special === true) return false;

    if (filterGroup === 'active') return state !== 'idle' && state !== 'done';
    if (filterGroup === 'hide_completed') return state !== 'done';
    if (filterGroup === 'errors') return state === 'failed';
    return true;
  }), [filterGroup, showDetails, showSpecial]);

  return (
    <ProcessFilterContext.Provider value={{ filterGroup, setFilterGroup, showDetails, setShowDetails, showSpecial, setShowSpecial, filterFn }}>
      {children}
    </ProcessFilterContext.Provider>
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

- [ ] **Step 2: Run the tests**

```bash
cd packages/optio-ui && pnpm test 2>&1 | grep -E "PASS|FAIL|✓|×"
```

Expected: all 7 `useProcessFilter` tests pass.

---

### Task 3: FilteredProcessList render test

**Files:**
- Modify: `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx`

- [ ] **Step 1: Add the render test**

Append to `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { FilteredProcessList } from '../components/ProcessFilter.js';

vi.mock('../components/ProcessList.js', () => ({
  ProcessList: ({ processes }: { processes: any[] }) => (
    <div data-testid="list">{processes.map((p: any) => (
      <div key={p._id} data-testid="item">{p._id}</div>
    ))}</div>
  ),
}));

describe('FilteredProcessList', () => {
  it('passes only filtered processes to ProcessList', () => {
    const processes = [
      makeProcess({ _id: 'root-idle', status: { state: 'idle' }, depth: 0 }),
      makeProcess({ _id: 'child-idle', status: { state: 'idle' }, depth: 1 }),
      makeProcess({ _id: 'root-running', status: { state: 'running' }, depth: 0 }),
    ];
    render(
      <WithFilteredProcesses>
        <FilteredProcessList processes={processes} loading={false} />
      </WithFilteredProcesses>,
    );
    const items = screen.getAllByTestId('item');
    // Default: showDetails=false hides child-idle
    expect(items).toHaveLength(2);
    expect(items.map((el) => el.textContent)).toEqual(['root-idle', 'root-running']);
  });
});
```

Also add `import { vi } from 'vitest';` at the top of the file if not already present.

- [ ] **Step 2: Run tests**

```bash
cd packages/optio-ui && pnpm test 2>&1 | grep -E "PASS|FAIL|✓|×"
```

Expected: all 8 tests pass.

---

### Task 4: Delete old ProcessFilters.tsx and update index.ts

**Files:**
- Delete: `packages/optio-ui/src/components/ProcessFilters.tsx`
- Modify: `packages/optio-ui/src/index.ts`

- [ ] **Step 1: Delete the old file**

```bash
rm packages/optio-ui/src/components/ProcessFilters.tsx
```

- [ ] **Step 2: Update index.ts**

Replace the current contents of `packages/optio-ui/src/index.ts` with:

```typescript
// Provider
export { OptioProvider } from './context/OptioProvider.js';
export { useOptioLive } from './context/useOptioContext.js';

// Components
export { ProcessList, ProcessItem } from './components/ProcessList.js';
export { ProcessStatusBadge } from './components/ProcessStatusBadge.js';
export { ProcessTreeView } from './components/ProcessTreeView.js';
export { ProcessLogPanel } from './components/ProcessLogPanel.js';
export { WithFilteredProcesses, ProcessFilters, FilteredProcessList, useProcessFilter } from './components/ProcessFilter.js';

// Hooks
export { useInstances, useInstanceDiscovery, type OptioInstance } from './hooks/useInstanceDiscovery.js';
export { useProcessActions } from './hooks/useProcessActions.js';
export { useProcessList, useProcess, useProcessTree, useProcessTreeLog } from './hooks/useProcessQueries.js';
export { useProcessStream } from './hooks/useProcessStream.js';
export { useProcessListStream } from './hooks/useProcessListStream.js';
export { usePrefixes, usePrefixDiscovery } from './hooks/usePrefixDiscovery.js';

// Types
export type { FilterGroup } from './components/ProcessFilter.js';
export type { ProcessTreeNode } from './hooks/useProcessStream.js';
```

- [ ] **Step 3: Type-check**

```bash
cd packages/optio-ui && node_modules/.bin/tsc --noEmit 2>&1
```

Expected: no errors.

- [ ] **Step 4: Run full test suite**

```bash
cd packages/optio-ui && pnpm test 2>&1
```

Expected: all tests pass.

---

### Task 5: Update optio-dashboard to use the new components

**Files:**
- Modify: `packages/optio-dashboard/src/app/App.tsx`

- [ ] **Step 1: Update App.tsx**

In `packages/optio-dashboard/src/app/App.tsx`, update the `Dashboard` component to use the filter bundle. Change the imports and the `Dashboard` component body:

```tsx
import { useState } from 'react';
import { Alert, Button, Layout, Select, Typography } from 'antd';
import {
  OptioProvider,
  WithFilteredProcesses,
  ProcessFilters,
  FilteredProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
  useInstances,
  useOptioLive,
} from 'optio-ui';
import { LoginForm } from './LoginForm.js';
import { useSession, signOut } from './auth-client.js';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { tree, logs, connected: treeConnected } = useProcessStream(
    selectedProcessId ?? undefined,
  );
  const { launch, cancel, dismiss } = useProcessActions();
  const live = useOptioLive();

  return (
    <WithFilteredProcesses>
      <Layout>
        <Layout>
          <Sider width={400} style={{ background: '#fff', overflow: 'auto' }}>
            <ProcessFilters />
            <FilteredProcessList
              processes={processes}
              loading={!listConnected}
              onLaunch={live ? launch : undefined}
              onCancel={live ? cancel : undefined}
              onProcessClick={setSelectedProcessId}
            />
          </Sider>
          <Content style={{ padding: '24px', overflow: 'auto' }}>
            {selectedProcessId ? (
              <>
                <ProcessTreeView
                  treeData={tree}
                  sseState={{ connected: treeConnected }}
                  onCancel={live ? cancel : undefined}
                />
                <ProcessLogPanel logs={logs} />
              </>
            ) : (
              <div style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
                Select a process to view details
              </div>
            )}
          </Content>
        </Layout>
      </Layout>
    </WithFilteredProcesses>
  );
}
```

The rest of `App.tsx` (`instanceKey`, `AppContent`, `App`) remains unchanged.

- [ ] **Step 2: Type-check the dashboard**

```bash
cd packages/optio-dashboard && node_modules/.bin/tsc --noEmit 2>&1
```

Expected: no errors.

---

### Task 6: Build and commit

**Files:** all modified files

- [ ] **Step 1: Build optio-ui**

```bash
cd packages/optio-ui && node_modules/.bin/tsc 2>&1
```

Expected: no errors, `dist/` updated.

- [ ] **Step 2: Run all tests**

```bash
cd packages/optio-ui && pnpm test 2>&1
```

Expected: all tests pass.

- [ ] **Step 3: Build dashboard**

```bash
cd packages/optio-dashboard && make build 2>&1 | tail -5
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-ui/src/components/ProcessFilter.tsx \
        packages/optio-ui/src/index.ts \
        packages/optio-ui/src/__tests__/ProcessFilter.test.tsx \
        packages/optio-dashboard/src/app/App.tsx
git rm packages/optio-ui/src/components/ProcessFilters.tsx
git commit -m "Add process filter bundle: WithFilteredProcesses, ProcessFilters, FilteredProcessList, useProcessFilter"
```
