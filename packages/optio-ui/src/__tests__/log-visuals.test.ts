import { describe, it, expect } from 'vitest';
import { buildProcessVisuals, PALETTE, STRIDE } from '../log-visuals.js';
import type { ProcessTreeNode } from '../hooks/useProcessStream.js';

function leaf(id: string, depth: number): ProcessTreeNode {
  return {
    _id: id,
    parentId: null,
    name: id,
    status: { state: 'running' },
    progress: { percent: null },
    cancellable: false,
    depth,
    order: 0,
    children: [],
  };
}

function node(
  id: string,
  depth: number,
  children: ProcessTreeNode[],
): ProcessTreeNode {
  return { ...leaf(id, depth), children };
}

describe('buildProcessVisuals', () => {
  it('returns an empty map for a null tree', () => {
    const v = buildProcessVisuals(null);
    expect(v.size).toBe(0);
  });

  it('assigns the root depth 0 and PALETTE[0]', () => {
    const tree = leaf('root', 0);
    const v = buildProcessVisuals(tree);
    const root = v.get('root');
    expect(root).toBeDefined();
    expect(root!.depth).toBe(0);
    expect(root!.color).toBe(PALETTE[0]);
    expect(root!.label).toBe('root');
  });

  it('spaces siblings by STRIDE in the palette', () => {
    const tree = node('root', 0, [leaf('a', 1), leaf('b', 1)]);
    const v = buildProcessVisuals(tree);
    expect(v.get('root')!.color).toBe(PALETTE[0]);
    expect(v.get('a')!.color).toBe(PALETTE[(1 * STRIDE) % PALETTE.length]);
    expect(v.get('b')!.color).toBe(PALETTE[(2 * STRIDE) % PALETTE.length]);
  });

  it('records depth from the tree', () => {
    const tree = node('root', 0, [
      node('a', 1, [leaf('aa', 2)]),
    ]);
    const v = buildProcessVisuals(tree);
    expect(v.get('root')!.depth).toBe(0);
    expect(v.get('a')!.depth).toBe(1);
    expect(v.get('aa')!.depth).toBe(2);
  });

  it('wraps the palette after 10 processes', () => {
    let inner: ProcessTreeNode = leaf('c10', 10);
    for (let i = 9; i >= 1; i--) {
      inner = node(`c${i}`, i, [inner]);
    }
    const tree = node('root', 0, [inner]);

    const v = buildProcessVisuals(tree);
    const colorAt = (idx: number) => PALETTE[(idx * STRIDE) % PALETTE.length];
    expect(v.get('root')!.color).toBe(colorAt(0));
    expect(v.get('c1')!.color).toBe(colorAt(1));
    expect(v.get('c10')!.color).toBe(colorAt(10));
    expect(v.get('c10')!.color).toBe(v.get('root')!.color);
  });

  it('is stable: appending a new leaf does not change existing colors', () => {
    const before = node('root', 0, [leaf('a', 1)]);
    const after = node('root', 0, [leaf('a', 1), leaf('b', 1)]);
    const v1 = buildProcessVisuals(before);
    const v2 = buildProcessVisuals(after);
    expect(v2.get('root')!.color).toBe(v1.get('root')!.color);
    expect(v2.get('a')!.color).toBe(v1.get('a')!.color);
  });
});
