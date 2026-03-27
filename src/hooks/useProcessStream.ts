import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useOptioPrefix, useOptioBaseUrl } from '../context/useOptioContext.js';

interface ProcessUpdate {
  _id: string;
  parentId: string | null;
  name: string;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable: boolean;
  depth: number;
  order: number;
}

export interface ProcessTreeNode extends ProcessUpdate {
  children: ProcessTreeNode[];
}

interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  data?: Record<string, unknown>;
  processId: string;
  processLabel: string;
}

interface ProcessStreamResult {
  processes: ProcessUpdate[];
  connected: boolean;
  tree: ProcessTreeNode | null;
  rootProcess: ProcessUpdate | null;
  logs: LogEntry[];
}

function buildTree(flat: ProcessUpdate[]): ProcessTreeNode | null {
  if (flat.length === 0) return null;
  const nodeMap = new Map<string, ProcessTreeNode>();
  for (const p of flat) nodeMap.set(p._id, { ...p, children: [] });
  let root: ProcessTreeNode | null = null;
  for (const node of nodeMap.values()) {
    if (node.parentId && nodeMap.has(node.parentId)) {
      nodeMap.get(node.parentId)!.children.push(node);
    } else if (node.depth === 0) {
      root = node;
    }
  }
  for (const node of nodeMap.values()) node.children.sort((a, b) => a.order - b.order);
  return root;
}

export function useProcessStream(processId: string | undefined, maxDepth = 10): ProcessStreamResult {
  const prefix = useOptioPrefix();
  const baseUrl = useOptioBaseUrl();
  const [state, setState] = useState<{ processes: ProcessUpdate[]; connected: boolean; logs: LogEntry[] }>({
    processes: [], connected: false, logs: [],
  });
  const eventSourceRef = useRef<EventSource | null>(null);

  const connect = useCallback(() => {
    if (!processId) return;
    const url = `${baseUrl}/api/processes/${prefix}/${processId}/tree/stream?maxDepth=${maxDepth}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;
    es.onopen = () => setState((s) => ({ ...s, connected: true }));
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'update') setState((s) => ({ ...s, processes: data.processes }));
        else if (data.type === 'log-clear') setState((s) => ({ ...s, logs: [] }));
        else if (data.type === 'log') setState((s) => ({ ...s, logs: [...s.logs, ...data.entries] }));
      } catch { /* ignore */ }
    };
    es.onerror = () => {
      setState((s) => ({ ...s, connected: false }));
      es.close();
      setTimeout(() => connect(), 3000);
    };
  }, [processId, maxDepth, prefix, baseUrl]);

  useEffect(() => {
    connect();
    return () => { eventSourceRef.current?.close(); };
  }, [connect]);

  const tree = useMemo(() => buildTree(state.processes), [state.processes]);
  const rootProcess = state.processes.find((p) => p.depth === 0) ?? null;
  return { ...state, tree, rootProcess };
}
