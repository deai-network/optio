import { useSyncExternalStore } from 'react';
import type { ProcessMetadataFilter } from 'optio-contracts';
import { useOptioPrefix, useOptioBaseUrl, useOptioDatabase } from '../context/useOptioContext.js';
import { handleBrowserOpenRequests } from '../handlers/browserOpen.js';

interface ProcessListStreamState {
  processes: any[];
  connected: boolean;
}

let _state: ProcessListStreamState = { processes: [], connected: false };
let _listeners: Set<() => void> = new Set();
let _eventSource: EventSource | null = null;
let _connectedKey: string | null = null;
let _retryTimeout: ReturnType<typeof setTimeout> | null = null;

function notify() {
  _listeners.forEach((fn) => fn());
}

function closeAndReset() {
  _eventSource?.close();
  _eventSource = null;
  _connectedKey = null;
  if (_retryTimeout !== null) {
    clearTimeout(_retryTimeout);
    _retryTimeout = null;
  }
}

function connect(baseUrl: string, prefix: string, database: string | undefined, filterKey: string) {
  const key = `${baseUrl}|${database}|${prefix}|${filterKey}`;
  if (_eventSource && _connectedKey === key) return;
  // Cancel any pending retry from a previous (now-stale) key before
  // wiring up the new one — otherwise the old retry can fire later and
  // either reopen with stale params or shadow this one.
  closeAndReset();

  const params = new URLSearchParams();
  params.set('prefix', prefix);
  if (database) params.set('database', database);
  if (filterKey) params.set('metadataFilter', filterKey);
  const url = `${baseUrl}/api/processes/stream?${params.toString()}`;

  const es = new EventSource(url);
  _connectedKey = key;
  _eventSource = es;

  es.onopen = () => {
    _state = { ..._state, connected: true };
    notify();
  };

  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'update') {
        for (const p of data.processes) handleBrowserOpenRequests(p.browserOpenRequests);
        _state = { processes: data.processes, connected: true };
        notify();
      }
    } catch { /* ignore */ }
  };

  es.onerror = () => {
    _state = { ..._state, connected: false };
    notify();
    es.close();
    _eventSource = null;
    _connectedKey = null;
    // Track the retry timer so closeAndReset() / a new connect() with a
    // different key can cancel it. Without this the old retry fires
    // later with stale params and ricochets between connections.
    _retryTimeout = setTimeout(() => {
      _retryTimeout = null;
      connect(baseUrl, prefix, database, filterKey);
    }, 3000);
  };
}

function subscribe(listener: () => void) {
  _listeners.add(listener);
  return () => { _listeners.delete(listener); };
}

function getSnapshot(): ProcessListStreamState {
  return _state;
}

/**
 * Single-stream hook: only one EventSource is active per app instance. If two
 * components mount concurrently with different `metadataFilter` values they
 * will fight — the most recent render wins and the other gets data for the
 * wrong filter. Use with a single top-level filter selector, or hoist filter
 * state above all consumers.
 */
export function useProcessListStream(
  options?: { metadataFilter?: ProcessMetadataFilter },
): ProcessListStreamState {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const baseUrl = useOptioBaseUrl();

  const filterKey = options?.metadataFilter
    ? JSON.stringify(options.metadataFilter)
    : '';

  connect(baseUrl, prefix, database, filterKey);
  return useSyncExternalStore(subscribe, getSnapshot);
}
