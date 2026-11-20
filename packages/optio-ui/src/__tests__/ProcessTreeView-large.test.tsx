import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

// antd's Tree as is, recording the props each render gives it.
const { treeRenders } = vi.hoisted(() => ({ treeRenders: [] as any[] }));
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  const { createElement } = await import('react');
  function Tree(props: any) {
    treeRenders.push(props);
    return createElement(actual.Tree, props);
  }
  return { ...actual, Tree };
});
// A node's LaunchControls reads the process's snapshot list: none here.
vi.mock('../hooks/useProcessSnapshots.js', () => ({
  useProcessSnapshots: vi.fn(() => undefined),
}));

const { ProcessTreeView, LARGE_TREE_THRESHOLD, LARGE_TREE_HEIGHT } = await import('../components/ProcessTreeView.js');

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

function node(id: string, extra: Record<string, unknown> = {}): any {
  return {
    _id: id, name: `Task ${id}`, parentId: null, depth: 0, order: 0, children: [],
    status: { state: 'running' }, progress: { percent: null }, cancellable: true, ...extra,
  };
}
/** A root with `count` children, in the given state. */
function wide(count: number, state = 'running'): any {
  return node('root', {
    children: Array.from({ length: count }, (_, i) => node(`c${i}`, { status: { state } })),
  });
}
const lastTree = () => treeRenders[treeRenders.length - 1];
/** The tree's node rows (not rc-tree's hidden measuring node). */
const treeNodes = (container: HTMLElement) =>
  container.querySelectorAll('.ant-tree-treenode:not([aria-hidden])').length;

function view(tree: any, props: Record<string, unknown> = {}) {
  return (
    <I18nextProvider i18n={i18n}>
      <ProcessTreeView treeData={tree} sseState={{ connected: true }} {...props} />
    </I18nextProvider>
  );
}

beforeEach(() => {
  treeRenders.length = 0;
});

describe('ProcessTreeView: memoized on its real inputs', () => {
  it('a re-render with the same tree and new inline callbacks (as ProcessDetailView passes) rebuilds nothing', () => {
    const tree = wide(3);
    const { rerender } = render(view(tree, { onCancel: () => {}, onLaunch: () => {} }));
    const first = lastTree();
    rerender(view(tree, { onCancel: () => {}, onLaunch: () => {} }));
    const second = lastTree();
    expect(second).not.toBe(first); // it did render again
    expect(second.treeData).toBe(first.treeData);
    expect(second.expandedKeys).toBe(first.expandedKeys);
  });

  it('a new tree rebuilds the nodes', () => {
    const { rerender } = render(view(wide(3)));
    const first = lastTree();
    rerender(view(wide(3)));
    expect(lastTree().treeData).not.toBe(first.treeData);
  });

  it('a control calls the latest callback given, not the first', () => {
    const first = vi.fn();
    const latest = vi.fn();
    const { rerender } = render(view(node('n1'), { onCancel: first }));
    rerender(view(node('n1'), { onCancel: latest }));
    fireEvent.click(screen.getByRole('button', { name: 'processes.cancel' }));
    expect(latest).toHaveBeenCalledWith('n1');
    expect(first).not.toHaveBeenCalled();
  });

  it('a callback taken away takes its control away', () => {
    const { rerender } = render(view(node('n1'), { onCancel: () => {} }));
    expect(screen.queryByRole('button', { name: 'processes.cancel' })).not.toBeNull();
    rerender(view(node('n1')));
    expect(screen.queryByRole('button', { name: 'processes.cancel' })).toBeNull();
  });
});

describe('ProcessTreeView: a large tree renders virtualized', () => {
  it('the threshold is 500 nodes, the box 600px', () => {
    expect(LARGE_TREE_THRESHOLD).toBe(500);
    expect(LARGE_TREE_HEIGHT).toBe(600);
  });

  it('below the threshold: as before, no height, every node rendered and expanded', () => {
    const tree = wide(50);
    const { container } = render(view(tree));
    expect(lastTree().height).toBeUndefined();
    expect(lastTree().onExpand).toBeUndefined();
    expect(lastTree().expandedKeys).toHaveLength(51);
    expect(treeNodes(container)).toBe(51);
  });

  it('above it: the Tree gets the height and renders far fewer DOM nodes than the tree has', () => {
    const { container } = render(view(wide(2000)));
    expect(lastTree().height).toBe(LARGE_TREE_HEIGHT);
    expect(treeNodes(container)).toBeGreaterThan(0);
    expect(treeNodes(container)).toBeLessThan(100);
  });

  it('above it: only the root is expanded at first; a node opens on its switcher', () => {
    const big = wide(600);
    big.children[0].children = [node('g1'), node('g2')];
    const { container } = render(view(big));
    expect(lastTree().expandedKeys).toEqual(['root']);
    expect(screen.queryByText('Task g1')).toBeNull();

    const firstChild = container.querySelectorAll('.ant-tree-treenode:not([aria-hidden])')[1];
    expect(firstChild.textContent).toContain('Task c0');
    fireEvent.click(firstChild.querySelector('.ant-tree-switcher')!);
    expect(lastTree().expandedKeys).toEqual(['root', 'c0']);
    expect(screen.getByText('Task g1')).toBeTruthy();
  });

  it('the count is of the nodes shown: finished sub-tasks hidden (the default) keep a big finished tree small', () => {
    const { container } = render(view(wide(2000, 'done')));
    expect(lastTree().height).toBeUndefined();
    expect(treeNodes(container)).toBe(1);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(lastTree().height).toBe(LARGE_TREE_HEIGHT);
    expect(treeNodes(container)).toBeLessThan(100);
  });
});
