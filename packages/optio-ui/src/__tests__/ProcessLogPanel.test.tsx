import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, screen, within } from '@testing-library/react';
import React from 'react';
import { ProcessLogPanel } from '../components/ProcessLogPanel.js';
import type { LogEntry, ProcessTreeNode } from '../hooks/useProcessStream.js';
import { PALETTE, STRIDE } from '../log-visuals.js';

function leaf(id: string, depth: number, name = id): ProcessTreeNode {
  return {
    _id: id,
    parentId: null,
    name,
    status: { state: 'running' },
    progress: { percent: null },
    cancellable: false,
    depth,
    order: 0,
    children: [],
  };
}

function tree2level(): ProcessTreeNode {
  return {
    ...leaf('root', 0, 'root'),
    children: [
      leaf('a', 1, 'alpha'),
      leaf('b', 1, 'beta'),
    ],
  };
}

function entry(
  processId: string,
  processLabel: string,
  message: string,
  level = 'info',
  timestampMs = 0,
): LogEntry {
  return {
    timestamp: new Date(timestampMs).toISOString(),
    level,
    message,
    processId,
    processLabel,
  };
}

afterEach(() => cleanup());

describe('ProcessLogPanel', () => {
  it('renders a label tag only on transition rows', () => {
    const logs: LogEntry[] = [
      entry('a', 'alpha', 'first'),
      entry('a', 'alpha', 'second'),
      entry('b', 'beta', 'third'),
      entry('b', 'beta', 'fourth'),
      entry('a', 'alpha', 'fifth'),
    ];
    render(<ProcessLogPanel logs={logs} tree={tree2level()} />);

    expect(screen.getAllByText('alpha')).toHaveLength(2);
    expect(screen.getAllByText('beta')).toHaveLength(1);
  });

  it('applies depth-based padding to rows', () => {
    const logs: LogEntry[] = [
      entry('root', 'root', 'r'),
      entry('a', 'alpha', 'a'),
    ];
    const { container } = render(
      <ProcessLogPanel logs={logs} tree={tree2level()} />,
    );

    const rows = container.querySelectorAll('[data-testid="log-row"]');
    expect(rows.length).toBe(2);
    expect((rows[0] as HTMLElement).style.paddingLeft).toBe('0px');
    expect((rows[1] as HTMLElement).style.paddingLeft).toBe('16px');
  });

  it('renders a colored left bar matching the assigned color', () => {
    const logs: LogEntry[] = [entry('a', 'alpha', 'a')];
    const { container } = render(
      <ProcessLogPanel logs={logs} tree={tree2level()} />,
    );

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    const bar = row.querySelector('[data-testid="log-bar"]') as HTMLElement;
    const expectedHex = PALETTE[(1 * STRIDE) % PALETTE.length];
    // JSDOM normalizes inline hex backgrounds to rgb(); compare both forms.
    const r = parseInt(expectedHex.slice(1, 3), 16);
    const g = parseInt(expectedHex.slice(3, 5), 16);
    const b = parseInt(expectedHex.slice(5, 7), 16);
    expect(bar.style.background).toBe(`rgb(${r}, ${g}, ${b})`);
  });

  it('caps indent at 8 * 16 = 128px for deep trees', () => {
    let cur: ProcessTreeNode = leaf('d12', 12);
    for (let i = 11; i >= 1; i--) {
      cur = { ...leaf(`d${i}`, i), children: [cur] };
    }
    const tree: ProcessTreeNode = { ...leaf('root', 0), children: [cur] };

    const logs: LogEntry[] = [entry('d12', 'd12', 'deep')];
    const { container } = render(<ProcessLogPanel logs={logs} tree={tree} />);

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    expect(row.style.paddingLeft).toBe('128px');
  });

  it('falls back gracefully for an unknown processId', () => {
    const logs: LogEntry[] = [entry('ghost', 'ghost-label', 'orphan')];
    const { container } = render(
      <ProcessLogPanel logs={logs} tree={tree2level()} />,
    );

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    expect(row.style.paddingLeft).toBe('0px');
    expect(within(row).getByText('ghost-label')).toBeTruthy();
  });

  it('renders flat (no indent, no bar) when tree is null', () => {
    const logs: LogEntry[] = [entry('a', 'alpha', 'a')];
    const { container } = render(<ProcessLogPanel logs={logs} tree={null} />);

    const row = container.querySelector('[data-testid="log-row"]') as HTMLElement;
    expect(row.style.paddingLeft).toBe('0px');
    expect(row.querySelector('[data-testid="log-bar"]')).toBeNull();
  });
});
