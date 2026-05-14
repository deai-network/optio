import type { ProcessTreeNode } from './hooks/useProcessStream.js';

/**
 * Perceptually-distinct color palette used to give each process in a tree
 * a stable visual identity in log views.
 *
 * The PALETTE is paired with a STRIDE: each process is assigned a sequential
 * DFS index and colored as PALETTE[(index * STRIDE) % PALETTE.length].
 * gcd(PALETTE.length, STRIDE) must equal 1 so that all palette slots are
 * visited before any repeats. STRIDE > 1 ensures that adjacent indices (e.g.
 * sibling processes whose log lines tend to interleave) land far apart in
 * the palette, avoiding near-identical hues.
 */
export const PALETTE: readonly string[] = [
  '#ef4444', // red
  '#10b981', // emerald
  '#3b82f6', // blue
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#06b6d4', // cyan
  '#ec4899', // pink
  '#84cc16', // lime
  '#f97316', // orange
  '#a855f7', // purple
];

export const STRIDE = 3;

export interface ProcessVisual {
  depth: number;
  color: string;
  label: string;
}

export function buildProcessVisuals(
  tree: ProcessTreeNode | null,
): Map<string, ProcessVisual> {
  const out = new Map<string, ProcessVisual>();
  if (!tree) return out;

  let index = 0;
  const visit = (node: ProcessTreeNode): void => {
    out.set(node._id, {
      depth: node.depth,
      color: PALETTE[(index * STRIDE) % PALETTE.length],
      label: node.name,
    });
    index += 1;
    for (const child of node.children) visit(child);
  };
  visit(tree);
  return out;
}
