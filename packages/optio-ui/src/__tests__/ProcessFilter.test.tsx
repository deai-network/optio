import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, render, screen } from '@testing-library/react';
import React from 'react';

// We'll import these once the implementation exists.
// For now this file won't compile — that's expected (RED phase).
import { WithFilteredProcesses, useProcessFilter, FilteredProcessList } from '../components/ProcessFilter.js';

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
