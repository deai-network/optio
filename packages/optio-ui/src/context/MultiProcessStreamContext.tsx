import { createContext, useEffect, useRef, useState, useCallback, type ReactNode } from 'react';
import { useOptioPrefix, useOptioBaseUrl, useOptioDatabase } from './useOptioContext.js';
import { handleBrowserOpenRequests } from '../handlers/browserOpen.js';

export interface MultiProcessUpdate {
  _id: string;
  processId: string;
  parentId: string | null;
  rootId: string | null;
  name: string;
  status: { state: string; [k: string]: unknown };
  progress: { percent?: number | null; message?: string | null };
  cancellable: boolean;
  depth: number;
  order: number;
  widgetData?: unknown;
  uiWidget?: unknown;
  supportsResume?: boolean;
  hasSavedState?: boolean;
  metadata?: Record<string, unknown>;
  browserOpenRequests?: { requestId: string; url: string }[];
}

export interface MultiLogEntry {
  processId: string;
  processLabel: string;
  rootId: string | null;
  timestamp: string;
  level: string;
  message: string;
}

export interface MultiProcessTreeNode extends MultiProcessUpdate {
  children: MultiProcessTreeNode[];
}

export interface ProcessStreamSlice {
  rootProcess: MultiProcessUpdate | null;
  processes: MultiProcessUpdate[];
  tree: MultiProcessTreeNode | null;
  logs: MultiLogEntry[];
  connected: boolean;
  processNotFound: boolean;
  error: Error | null;
}

export interface MultiProcessStreamContextValue {
  getSlice: (processId: string) => ProcessStreamSlice | null;
  registerTree: (processId: string) => () => void;
  registerFlat: (processId: string) => () => void;
  connected: boolean;
}

export const MultiProcessStreamContext =
  createContext<MultiProcessStreamContextValue | null>(null);

function buildTree(flat: MultiProcessUpdate[], rootProcessId: string): MultiProcessTreeNode | null {
  const root = flat.find((p) => p.processId === rootProcessId);
  if (!root) return null;
  const nodeMap = new Map<string, MultiProcessTreeNode>();
  for (const p of flat) nodeMap.set(p._id, { ...p, children: [] });
  for (const p of flat) {
    if (p.parentId && nodeMap.has(p.parentId)) {
      nodeMap.get(p.parentId)!.children.push(nodeMap.get(p._id)!);
    }
  }
  for (const node of nodeMap.values()) {
    node.children.sort((a, b) => a.order - b.order);
  }
  return nodeMap.get(root._id) ?? null;
}

export function MultiProcessStreamProvider({
  maxDepth = 10,
  children,
}: {
  maxDepth?: number;
  children: ReactNode;
}) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const baseUrl = useOptioBaseUrl();

  const [processesByRootPid, setProcessesByRootPid] = useState<Record<string, MultiProcessUpdate[]>>({});
  const [logsByRootPid, setLogsByRootPid] = useState<Record<string, MultiLogEntry[]>>({});
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [connected, setConnected] = useState(false);

  // ObjectId hex (rootId on events) → root processId string. Built from root rows
  // we see in update events (rows where parentId === null AND _id === rootId).
  const rootIdToPidRef = useRef<Map<string, string>>(new Map());

  // Refcount maps: pid → number of mounted consumers
  const treeRefsRef = useRef<Map<string, number>>(new Map());
  const flatRefsRef = useRef<Map<string, number>>(new Map());

  // version increments when the effective pid union changes (a pid crosses 0 boundary)
  const [version, setVersion] = useState(0);

  // Timer for debouncing rapid register/unregister calls
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleReconnect = useCallback(() => {
    if (debounceTimerRef.current !== null) return;
    debounceTimerRef.current = setTimeout(() => {
      debounceTimerRef.current = null;
      setVersion((v) => v + 1);
    }, 100);
  }, []);

  const registerTree = useCallback((pid: string): (() => void) => {
    const prev = treeRefsRef.current.get(pid) ?? 0;
    treeRefsRef.current.set(pid, prev + 1);
    if (prev === 0) scheduleReconnect();
    return () => {
      const cur = treeRefsRef.current.get(pid) ?? 0;
      const next = cur - 1;
      if (next <= 0) {
        treeRefsRef.current.delete(pid);
        scheduleReconnect();
      } else {
        treeRefsRef.current.set(pid, next);
      }
    };
  }, [scheduleReconnect]);

  const registerFlat = useCallback((pid: string): (() => void) => {
    const prev = flatRefsRef.current.get(pid) ?? 0;
    flatRefsRef.current.set(pid, prev + 1);
    if (prev === 0) scheduleReconnect();
    return () => {
      const cur = flatRefsRef.current.get(pid) ?? 0;
      const next = cur - 1;
      if (next <= 0) {
        flatRefsRef.current.delete(pid);
        scheduleReconnect();
      } else {
        flatRefsRef.current.set(pid, next);
      }
    };
  }, [scheduleReconnect]);

  // Reconnect effect: fires when version (or config) changes
  useEffect(() => {
    const treeIds = Array.from(treeRefsRef.current.keys());
    const flatIds = Array.from(flatRefsRef.current.keys());

    if (treeIds.length === 0 && flatIds.length === 0) {
      // No registered consumers — nothing to stream
      setConnected(false);
      return;
    }

    // Reset slice state for new connection
    setProcessesByRootPid({});
    setLogsByRootPid({});
    setMissing(new Set());
    setConnected(false);
    rootIdToPidRef.current = new Map();

    const params = new URLSearchParams();
    if (treeIds.length) params.set('treeIds', treeIds.join(','));
    if (flatIds.length) params.set('flatIds', flatIds.join(','));
    params.set('prefix', prefix);
    if (database) params.set('database', database);
    params.set('maxDepth', String(maxDepth));

    const url = `${baseUrl}/api/processes/tree/multi/stream?${params.toString()}`;
    const es = new EventSource(url);

    es.onopen = () => setConnected(true);
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === 'resolution') {
          setMissing(new Set(data.missing as string[]));
        } else if (data.type === 'update') {
          const procs: MultiProcessUpdate[] = data.processes;
          for (const p of procs) handleBrowserOpenRequests(p.browserOpenRequests);
          // Capture treeIds/flatIds at the time this event fires (closure over the effect run)
          const currentTreeIds = Array.from(treeRefsRef.current.keys());
          const currentFlatIds = Array.from(flatRefsRef.current.keys());
          for (const p of procs) {
            if (p.parentId === null && p.rootId === p._id) {
              rootIdToPidRef.current.set(p._id, p.processId);
            }
          }
          const byPid: Record<string, MultiProcessUpdate[]> = {};
          for (const p of procs) {
            const rootPid = p.rootId
              ? rootIdToPidRef.current.get(p.rootId)
              : undefined;
            const pidToBin = rootPid && (currentTreeIds.includes(rootPid) || currentFlatIds.includes(rootPid))
              ? rootPid
              : p.processId;
            if (!byPid[pidToBin]) byPid[pidToBin] = [];
            byPid[pidToBin].push(p);
          }
          setProcessesByRootPid(byPid);
        } else if (data.type === 'log') {
          const incoming: MultiLogEntry[] = data.entries;
          setLogsByRootPid((prev) => {
            const next = { ...prev };
            for (const entry of incoming) {
              const rootPid = entry.rootId
                ? rootIdToPidRef.current.get(entry.rootId)
                : null;
              if (!rootPid) continue;
              if (!next[rootPid]) next[rootPid] = [];
              next[rootPid] = [...next[rootPid], entry];
            }
            return next;
          });
        } else if (data.type === 'log-clear') {
          const rootPid = data.rootId
            ? rootIdToPidRef.current.get(data.rootId)
            : null;
          if (rootPid) {
            setLogsByRootPid((prev) => ({ ...prev, [rootPid]: [] }));
          }
        }
      } catch { /* swallow malformed event */ }
    };
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, maxDepth, prefix, database, baseUrl]);

  const getSlice = useCallback(
    (processId: string): ProcessStreamSlice | null => {
      const isTreeKind = treeRefsRef.current.has(processId);
      const isFlatKind = flatRefsRef.current.has(processId);
      const watched = isTreeKind || isFlatKind;
      if (!watched) return null;
      const processes = processesByRootPid[processId] ?? [];
      const rootProcess = processes.find((p) => p.processId === processId) ?? null;
      const tree = isTreeKind && rootProcess
        ? buildTree(processes, processId)
        : (rootProcess ? { ...rootProcess, children: [] as MultiProcessTreeNode[] } : null);
      return {
        rootProcess,
        processes,
        tree,
        logs: logsByRootPid[processId] ?? [],
        connected,
        processNotFound: missing.has(processId),
        error: null,
      };
    },
    [processesByRootPid, logsByRootPid, missing, connected],
  );

  const value: MultiProcessStreamContextValue = { getSlice, registerTree, registerFlat, connected };
  return (
    <MultiProcessStreamContext.Provider value={value}>
      {children}
    </MultiProcessStreamContext.Provider>
  );
}
