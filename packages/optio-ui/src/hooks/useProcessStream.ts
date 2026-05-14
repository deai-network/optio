import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useOptioPrefix, useOptioBaseUrl, useOptioDatabase } from '../context/useOptioContext.js';

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

export interface LogEntry {
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
  /** True when the process exists check returned 404. Terminal: no retry. */
  processNotFound: boolean;
  /** Generic error reaching the API (network, 5xx, …). Distinct from
   *  ``processNotFound``; the SSE retry loop is still active when this is
   *  set, so it may clear on the next successful reconnect. */
  error: Error | null;
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

interface InternalState {
  processes: ProcessUpdate[];
  connected: boolean;
  logs: LogEntry[];
  processNotFound: boolean;
  error: Error | null;
}

const INITIAL_STATE: InternalState = {
  processes: [], connected: false, logs: [],
  processNotFound: false, error: null,
};

export function useProcessStream(processId: string | undefined, maxDepth = 10): ProcessStreamResult {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const baseUrl = useOptioBaseUrl();
  const [state, setState] = useState<InternalState>(INITIAL_STATE);
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const connect = useCallback(() => {
    if (!processId) return;
    retryTimeoutRef.current = null;

    // Pre-flight probe: EventSource swallows HTTP status codes, so we cannot
    // distinguish a 404 (process gone) from a network blip via SSE alone.
    // A cheap GET against the single-process endpoint gives us the status
    // explicitly. On 404 we set processNotFound and stop — the process is
    // not coming back, so there is no point reconnecting. On other errors
    // we surface a generic error and let the SSE retry loop handle it.
    const probeUrl = `${baseUrl}/api/processes/${processId}?prefix=${encodeURIComponent(prefix)}${database ? `&database=${encodeURIComponent(database)}` : ''}`;
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const openStream = () => {
      const url = `${baseUrl}/api/processes/${processId}/tree/stream?prefix=${encodeURIComponent(prefix)}&maxDepth=${maxDepth}${database ? `&database=${encodeURIComponent(database)}` : ''}`;
      const es = new EventSource(url);
      eventSourceRef.current = es;
      es.onopen = () => setState((s) => ({ ...s, connected: true, error: null }));
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
        // Re-probe on each SSE failure so a process that disappears
        // mid-stream (e.g. ephemeral cleanup) flips processNotFound
        // instead of looping forever. Track the retry timer so cleanup
        // can cancel a queued reconnect when processId changes.
        retryTimeoutRef.current = setTimeout(() => connect(), 3000);
      };
    };

    fetch(probeUrl, { signal: ctrl.signal })
      .then((resp) => {
        if (ctrl.signal.aborted) return;
        if (resp.status === 404) {
          setState((s) => ({ ...s, processNotFound: true, error: null, connected: false }));
          return;
        }
        if (!resp.ok) {
          setState((s) => ({
            ...s,
            error: new Error(`process probe failed: HTTP ${resp.status}`),
            connected: false,
          }));
          retryTimeoutRef.current = setTimeout(() => connect(), 3000);
          return;
        }
        // Process exists; clear any prior error and open the SSE.
        setState((s) => ({ ...s, processNotFound: false, error: null }));
        openStream();
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        setState((s) => ({
          ...s,
          error: err instanceof Error ? err : new Error(String(err)),
          connected: false,
        }));
        retryTimeoutRef.current = setTimeout(() => connect(), 3000);
      });
  }, [processId, maxDepth, prefix, database, baseUrl]);

  useEffect(() => {
    // Reset buffered state when processId changes so logs/tree/error from
    // the previous process don't leak through until the new connection
    // catches up — the stream handler only appends to state.logs, so
    // without this stale entries persist for several seconds.
    setState(INITIAL_STATE);
    connect();
    return () => {
      eventSourceRef.current?.close();
      abortRef.current?.abort();
      if (retryTimeoutRef.current !== null) {
        clearTimeout(retryTimeoutRef.current);
        retryTimeoutRef.current = null;
      }
    };
  }, [connect]);

  const tree = useMemo(() => buildTree(state.processes), [state.processes]);
  const rootProcess = state.processes.find((p) => p.depth === 0) ?? null;
  return { ...state, tree, rootProcess };
}
