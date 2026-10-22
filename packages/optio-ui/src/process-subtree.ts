/**
 * Selecting one process out of the tree a stream carries (the record's root tree: a factory
 * launch's is its factory's, with every launch under it), and its own log entries.
 */
import type { LogEntry, ProcessTreeNode } from './hooks/useProcessStream.js';

const OBJECT_ID = /^[0-9a-f]{24}$/i;

/**
 * Whether `id` names the record `p`: its `processId`, or its `_id` (a 24-hex one in any case, as
 * optio-api resolves it).
 */
export function namesProcess(p: { _id: string; processId?: string }, id: string): boolean {
  if (p._id === id || p.processId === id) return true;
  return OBJECT_ID.test(id) && p._id.toLowerCase() === id.toLowerCase();
}

/** The node of `tree` that `id` names (namesProcess), with everything under it; null when none. */
export function subtreeOf(tree: ProcessTreeNode, id: string): ProcessTreeNode | null {
  if (namesProcess(tree, id)) return tree;
  for (const child of tree.children) {
    const found = subtreeOf(child, id);
    if (found) return found;
  }
  return null;
}

/** The entries of `logs` whose record (`processId`, the record's `_id`) is a node of `subtree`. */
export function logsOfSubtree(logs: LogEntry[], subtree: ProcessTreeNode): LogEntry[] {
  const ids = new Set<string>();
  const collect = (node: ProcessTreeNode) => {
    ids.add(node._id);
    node.children.forEach(collect);
  };
  collect(subtree);
  return logs.filter((entry) => ids.has(entry.processId));
}
