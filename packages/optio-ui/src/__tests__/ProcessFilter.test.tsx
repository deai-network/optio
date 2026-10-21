import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';
import { NO_FIELD_CONTEXT, type FieldDefinition } from 'vultus-core';
import { toPredicate } from 'vultus-filtrum';
import { ProcessMetadataFilterSchema } from 'optio-contracts';

import {
  WithFilteredProcesses, useProcessFilter, FilteredProcessList, ProcessFilters,
} from '../components/ProcessFilter.js';
import { useSearchContext } from '@quaesitor-textus/core';

vi.mock('../components/ProcessList.js', () => ({
  ProcessList: ({ processes }: { processes: any[] }) => (
    <div data-testid="list">{processes.map((p: any) => (
      <div key={p._id} data-testid="item">{p._id}</div>
    ))}</div>
  ),
}));

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

// Used in search tests: exposes both filterFn and setQuery from the same render
function useFilterAndSearch() {
  const { filterFn } = useProcessFilter();
  const { setQuery } = useSearchContext();
  return { filterFn, setQuery };
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

// The filters' texts, in English.
const i18n = i18next.createInstance();
i18n.init({
  lng: 'en',
  resources: {
    en: {
      translation: {
        processes: {
          search: 'Search',
          filterAll: 'All',
          filterActive: 'Active',
          filterHideCompleted: 'Hide completed',
          filterErrors: 'Errors',
          showDetails: 'Show details',
          showSpecial: 'Show special',
        },
      },
    },
  },
});

// Default view: the three roots; the quiet sub-process and the quiet special one are hidden.
const LIST = [
  makeProcess({ _id: 'alpha-idle', name: 'alpha', status: { state: 'idle' }, depth: 0 }),
  makeProcess({ _id: 'beta-done', name: 'beta', status: { state: 'done' }, depth: 0 }),
  makeProcess({ _id: 'gamma-failed', name: 'gamma', status: { state: 'failed' }, depth: 0 }),
  makeProcess({ _id: 'child-idle', name: 'alpha step', status: { state: 'idle' }, depth: 1 }),
  makeProcess({ _id: 'special-idle', name: 'delta', status: { state: 'idle' }, depth: 0, special: true }),
];

const MODULE_FILTER: FieldDefinition = {
  kind: 'manyOf',
  id: 'module',
  label: 'Module',
  widget: 'select',
  choices: [
    { value: 'algebra', label: 'Algebra' },
    { value: 'geometry', label: 'Geometry' },
    { value: 'logic', label: 'Logic' },
  ],
};

/** What the filter state of the enclosing WithFilteredProcesses was at the last render. */
let filter: ReturnType<typeof useProcessFilter>;
function Probe() {
  filter = useProcessFilter();
  return null;
}

type AppFilterProps = { filters?: FieldDefinition[]; filterFields?: Record<string, string> };

function FiltersOverList(props: AppFilterProps) {
  return (
    <I18nextProvider i18n={i18n}>
      <WithFilteredProcesses {...props}>
        <ProcessFilters />
        <FilteredProcessList processes={LIST} loading={false} />
        <Probe />
      </WithFilteredProcesses>
    </I18nextProvider>
  );
}

const renderFilters = (props: AppFilterProps = {}) => render(<FiltersOverList {...props} />);

/** The ids of the processes the list shows. */
const shown = () => screen.queryAllByTestId('item').map((el) => el.textContent);

/** The vultus fields rendered, by their ids, in document order. */
function fieldIds(container: HTMLElement): string[] {
  return [...new Set([...container.querySelectorAll('[data-field-id]')].map((el) => el.getAttribute('data-field-id')!))];
}

/** Opens an antd select and clicks the option labelled `label`. */
async function choose(select: HTMLElement, label: string) {
  fireEvent.mouseDown(select);
  const option = await waitFor(() => {
    const found = screen.getAllByText(label).find((el) => el.closest('.ant-select-item-option'));
    expect(found).toBeTruthy();
    return found!;
  }, { timeout: 60_000 });
  fireEvent.click(option);
}

describe('ProcessFilters — the built-in filters are vultus fields', () => {
  it('renders the search, the group, "show details" and "show special" as vultus fields, labelled by their keys', () => {
    const { container } = renderFilters();
    expect(fieldIds(container)).toEqual(['search', 'filterGroup', 'showDetails', 'showSpecial']);
    expect(screen.getByRole('textbox', { name: 'Search' }).getAttribute('placeholder')).toBe('Search');
    expect(screen.getByRole('combobox')).toBeTruthy();
    expect(screen.getByText('All')).toBeTruthy();
    expect((screen.getByRole('checkbox', { name: 'Show details' }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: 'Show special' }) as HTMLInputElement).checked).toBe(false);
    expect(shown()).toEqual(['alpha-idle', 'beta-done', 'gamma-failed']);
  });

  it('typing in the search filters the list by name; Escape clears it', () => {
    renderFilters();
    const search = screen.getByRole('textbox', { name: 'Search' });
    fireEvent.change(search, { target: { value: 'gam' } });
    expect(shown()).toEqual(['gamma-failed']);
    fireEvent.keyDown(search, { key: 'Escape' });
    expect(shown()).toEqual(['alpha-idle', 'beta-done', 'gamma-failed']);
  });

  it('the group narrows the list to its processes', async () => {
    renderFilters();
    await choose(screen.getByRole('combobox'), 'Errors');
    await waitFor(() => expect(shown()).toEqual(['gamma-failed']), { timeout: 60_000 });
    expect(filter.filterGroup).toBe('errors');
  });

  it('"show details" shows the quiet sub-processes', () => {
    renderFilters();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show details' }));
    expect(shown()).toEqual(['alpha-idle', 'beta-done', 'gamma-failed', 'child-idle']);
  });

  it('"show special" shows the quiet special processes', () => {
    renderFilters();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show special' }));
    expect(shown()).toEqual(['alpha-idle', 'beta-done', 'gamma-failed', 'special-idle']);
  });
});

describe("WithFilteredProcesses — an app's own filters over the metadata", () => {
  it('without app filters, metadataFilter is undefined', () => {
    renderFilters();
    expect(filter.metadataFilter).toBeUndefined();
    renderFilters({ filters: [] });
    expect(filter.metadataFilter).toBeUndefined();
  });

  it('renders a many-of after the built-in filters; the values chosen give metadataFilter, the predicate toPredicate makes', async () => {
    const { container } = renderFilters({ filters: [MODULE_FILTER] });
    expect(fieldIds(container)).toEqual(['search', 'filterGroup', 'showDetails', 'showSpecial', 'module']);
    // Nothing chosen: the predicate is empty, so no filter.
    expect(filter.metadataFilter).toBeUndefined();

    const select = screen.getByRole('combobox', { name: 'Module' });
    await choose(select, 'Algebra');
    await choose(select, 'Logic');
    await waitFor(() => expect(filter.filterValues.module).toEqual(['algebra', 'logic']), { timeout: 60_000 });

    expect(filter.metadataFilter).toEqual({ module: { in: ['algebra', 'logic'] } });
    expect(filter.metadataFilter).toEqual(
      toPredicate([MODULE_FILTER], { module: ['algebra', 'logic'] }, { ctx: NO_FIELD_CONTEXT }),
    );
    // What optio-api's list stream accepts as its metadataFilter.
    expect(ProcessMetadataFilterSchema.safeParse(filter.metadataFilter).success).toBe(true);
    // The metadata filter is the server's: the list shown here is not narrowed by it.
    expect(shown()).toEqual(['alpha-idle', 'beta-done', 'gamma-failed']);
  });

  it("a filter starts at its initialValue, else at its kind's own default", () => {
    renderFilters({
      filters: [
        { kind: 'oneOf', id: 'level', choices: [{ value: 'basic' }, { value: 'advanced' }] },
        { ...MODULE_FILTER, initialValue: ['geometry'] },
      ] as FieldDefinition[],
    });
    expect(filter.filterValues).toEqual({ level: 'basic', module: ['geometry'] });
    expect(filter.metadataFilter).toEqual({ AND: [{ level: { eq: 'basic' } }, { module: { in: ['geometry'] } }] });
  });

  it('filterFields names the metadata key of a filter whose id differs', () => {
    renderFilters({
      filters: [{ ...MODULE_FILTER, id: 'modules', initialValue: ['logic'] } as FieldDefinition],
      filterFields: { modules: 'module' },
    });
    expect(filter.metadataFilter).toEqual({ module: { in: ['logic'] } });
  });

  it('metadataFilter keeps its identity while it says the same, even when the app passes its filters anew', () => {
    const props = { filters: [{ ...MODULE_FILTER, initialValue: ['logic'] } as FieldDefinition] };
    const { rerender } = render(<FiltersOverList {...props} />);
    const first = filter.metadataFilter;
    expect(first).toEqual({ module: { in: ['logic'] } });
    // Another filter changes: a render with the same app filters.
    fireEvent.change(screen.getByRole('textbox', { name: 'Search' }), { target: { value: 'al' } });
    expect(filter.metadataFilter).toBe(first);
    // The app renders again, with equal but new definitions and fields.
    rerender(<FiltersOverList filters={props.filters.map((d) => ({ ...d }))} filterFields={{}} />);
    expect(filter.metadataFilter).toBe(first);
  });
});
