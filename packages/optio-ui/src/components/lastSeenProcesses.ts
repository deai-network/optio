import type { LogEntry, ProcessTreeNode } from '../hooks/useProcessStream.js';

/** What the detail view last saw of a process: the stream's tree and logs. */
export interface ProcessSnapshot {
  tree: ProcessTreeNode;
  logs: LogEntry[];
}

const CAPACITY = 5;

// Insertion order is recency order: the first key is the least recently shown.
const cache = new Map<string, ProcessSnapshot>();

/** Remembers the snapshot of a process the detail view shows, as the most recent; drops the least recent past 5. */
export function rememberProcess(id: string, snapshot: ProcessSnapshot): void {
  cache.delete(id);
  cache.set(id, snapshot);
  if (cache.size > CAPACITY) cache.delete(cache.keys().next().value as string);
}

export function lastSeenProcess(id: string): ProcessSnapshot | undefined {
  return cache.get(id);
}

/** Empties the cache (tests). */
export function forgetAllProcesses(): void {
  cache.clear();
}
