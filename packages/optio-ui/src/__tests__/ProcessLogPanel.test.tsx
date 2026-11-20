import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, screen, within, fireEvent } from '@testing-library/react';
import React from 'react';
import { ProcessLogPanel, LOG_PANEL_WINDOW } from '../components/ProcessLogPanel.js';
import type { LogEntry, ProcessTreeNode } from '../hooks/useProcessStream.js';
import { PALETTE, STRIDE } from '../log-visuals.js';

// A render-counting double: antd's Text, which also records each message "line <n>" it renders. A
// row renders its message once per render, so `renderedLines` lists the rows that rendered.
const renderedLines = vi.hoisted(() => [] as string[]);
vi.mock('antd', async (importOriginal) => {
  const antd = await importOriginal<typeof import('antd')>();
  const { createElement } = await import('react');
  const RealText = antd.Typography.Text;
  function CountingText(props: React.ComponentProps<typeof RealText>) {
    if (typeof props.children === 'string' && props.children.startsWith('line ')) {
      renderedLines.push(props.children);
    }
    return createElement(RealText, props);
  }
  return { ...antd, Typography: { ...antd.Typography, Text: CountingText } };
});

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

describe('ProcessLogPanel: only the most recent lines render', () => {
  const lines = (from: number, count: number) =>
    Array.from({ length: count }, (_, i) => entry('a', 'alpha', `line ${from + i}`));
  const rows = (container: HTMLElement) =>
    Array.from(container.querySelectorAll('[data-testid="log-row"]'));
  const text = (row: Element | undefined) => row?.textContent ?? '';
  const showEarlier = () => screen.queryByRole('button', { name: 'Show earlier lines' });

  /** jsdom lays nothing out: give the scroll box a geometry (400px tall, 10,000px of content). */
  function scrollBox(container: HTMLElement) {
    const box = container.firstElementChild as HTMLElement;
    let top = 0;
    Object.defineProperty(box, 'scrollHeight', { configurable: true, get: () => 10_000 });
    Object.defineProperty(box, 'clientHeight', { configurable: true, get: () => 400 });
    Object.defineProperty(box, 'scrollTop', {
      configurable: true, get: () => top, set: (v: number) => { top = v; },
    });
    return {
      get top() { return top; },
      scrollTo(v: number) { top = v; fireEvent.scroll(box); },
      reset(v: number) { top = v; }, // moves it without a scroll event
    };
  }

  it('the window is 500 lines', () => {
    expect(LOG_PANEL_WINDOW).toBe(500);
  });

  it('5,000 entries render the last 500 rows, oldest first', () => {
    const { container } = render(<ProcessLogPanel logs={lines(0, 5000)} tree={tree2level()} />);
    const shown = rows(container);
    expect(shown).toHaveLength(LOG_PANEL_WINDOW);
    expect(text(shown[0])).toContain('line 4500');
    expect(text(shown[shown.length - 1])).toContain('line 4999');
    // The first row shown names its process, as the first row of a log does.
    expect(within(shown[0] as HTMLElement).getByText('alpha')).toBeTruthy();
  });

  it('the control at the top shows 500 more, until every line shows', () => {
    const { container } = render(<ProcessLogPanel logs={lines(0, 1200)} tree={tree2level()} />);
    const control = showEarlier()!;
    expect(control).toBeTruthy();
    // On top: before the first row.
    expect(control.compareDocumentPosition(rows(container)[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(control);
    expect(rows(container)).toHaveLength(1000);
    expect(text(rows(container)[0])).toContain('line 200');

    fireEvent.click(showEarlier()!);
    expect(rows(container)).toHaveLength(1200);
    expect(text(rows(container)[0])).toContain('line 0');
    expect(showEarlier()).toBeNull();
  });

  it('a short log: every line, no control, no note', () => {
    const { container } = render(<ProcessLogPanel logs={lines(0, 3)} tree={tree2level()} />);
    expect(rows(container)).toHaveLength(3);
    expect(showEarlier()).toBeNull();
    expect(screen.queryByText('Older lines are not kept here')).toBeNull();
  });

  it('the buffer dropped older entries: the note on top, once every kept line shows', () => {
    const { container } = render(
      <ProcessLogPanel logs={lines(100, 600)} tree={tree2level()} droppedLogCount={100} />,
    );
    expect(showEarlier()).toBeTruthy();
    expect(screen.queryByText('Older lines are not kept here')).toBeNull();
    fireEvent.click(showEarlier()!);
    expect(rows(container)).toHaveLength(600);
    const note = screen.getByText('Older lines are not kept here');
    expect(note.compareDocumentPosition(rows(container)[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('follows the bottom as new entries come, also when the buffer is full (its length unchanged)', () => {
    const { container, rerender } = render(<ProcessLogPanel logs={lines(0, 2000)} tree={tree2level()} />);
    const box = scrollBox(container);
    box.reset(0);
    rerender(<ProcessLogPanel logs={lines(5, 2000)} tree={tree2level()} />);
    expect(box.top).toBe(10_000);
    const shown = rows(container);
    expect(shown).toHaveLength(LOG_PANEL_WINDOW);
    expect(text(shown[shown.length - 1])).toContain('line 2004');
  });

  it('scrolled up: no auto-scroll, and the lines read stay (none removed above them)', () => {
    const logs = lines(0, 5000);
    const { container, rerender } = render(<ProcessLogPanel logs={logs} tree={tree2level()} />);
    const box = scrollBox(container);
    box.scrollTo(100);
    rerender(<ProcessLogPanel logs={[...logs, ...lines(5000, 10)]} tree={tree2level()} />);
    expect(box.top).toBe(100);
    const shown = rows(container);
    expect(text(shown[0])).toContain('line 4500');
    expect(shown).toHaveLength(510);

    // Back at the bottom: it follows again.
    box.scrollTo(10_000 - 400);
    rerender(<ProcessLogPanel logs={[...logs, ...lines(5000, 20)]} tree={tree2level()} />);
    expect(box.top).toBe(10_000);
    const after = rows(container);
    expect(text(after[after.length - 1])).toContain('line 5019');
  });
});

describe('ProcessLogPanel: a row renders again only when what it shows changes', () => {
  const lines = (from: number, count: number) =>
    Array.from({ length: count }, (_, i) => entry('a', 'alpha', `line ${from + i}`));
  const rows = (container: HTMLElement) =>
    Array.from(container.querySelectorAll('[data-testid="log-row"]'));
  const lineNo = (message: string) => Number(message.slice('line '.length));

  it('"Show earlier lines" renders the 500 rows it adds; of the rows shown already only the old first one, which stops naming its process', () => {
    const tree = tree2level();
    const { container } = render(<ProcessLogPanel logs={lines(0, 2000)} tree={tree} droppedLogCount={3000} />);
    renderedLines.length = 0;

    fireEvent.click(screen.getByRole('button', { name: 'Show earlier lines' }));

    expect(rows(container)).toHaveLength(1000);
    expect(renderedLines.filter((m) => lineNo(m) < 1500)).toHaveLength(LOG_PANEL_WINDOW);
    expect(renderedLines.filter((m) => lineNo(m) >= 1500)).toEqual(['line 1500']);
  });

  it('a batch into a full buffer renders only its rows and the new first row, whatever droppedLogCount the caller passes', () => {
    const logs = lines(0, 2000);
    const { container, rerender } = render(<ProcessLogPanel logs={logs} tree={tree2level()} />);
    renderedLines.length = 0;

    // The buffer keeps 2,000 entries: 5 new ones push the oldest 5 out, every index shifts by 5.
    // The new tree object is another update message of the same tree.
    rerender(<ProcessLogPanel logs={[...logs.slice(5), ...lines(2000, 5)]} tree={tree2level()} />);

    const shown = rows(container);
    expect(shown).toHaveLength(LOG_PANEL_WINDOW);
    expect(shown[0].textContent).toContain('line 1505');
    expect(renderedLines).toEqual(['line 1505', 'line 2000', 'line 2001', 'line 2002', 'line 2003', 'line 2004']);
  });

  it('the same entry object twice in the log still gets a row each, and React sees no duplicate key', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const twice = entry('a', 'alpha', 'line 1');
      const { container, rerender } = render(<ProcessLogPanel logs={[twice, twice]} tree={tree2level()} />);
      rerender(<ProcessLogPanel logs={[twice, twice, entry('a', 'alpha', 'line 2')]} tree={tree2level()} />);
      expect(rows(container).map((row) => row.textContent?.includes('line 1'))).toEqual([true, true, false]);
      expect(errors.mock.calls.filter((call) => String(call[0]).includes('same key'))).toEqual([]);
    } finally {
      errors.mockRestore();
    }
  });
});
