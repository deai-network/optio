import { useSyncExternalStore } from 'react';
import { useOptioPrefix, useOptioBaseUrl, useOptioDatabase } from '../context/useOptioContext.js';

interface ProcessListStreamState {
  processes: any[];
  connected: boolean;
}

let _state: ProcessListStreamState = { processes: [], connected: false };
let _listeners: Set<() => void> = new Set();
let _eventSource: EventSource | null = null;
let _connectedKey: string | null = null;

function notify() {
  _listeners.forEach((fn) => fn());
}

function connect(baseUrl: string, prefix: string, database?: string) {
  const key = `${baseUrl}|${database}|${prefix}`;
  if (_eventSource && _connectedKey === key) return;
  _eventSource?.close();

  const url = `${baseUrl}/api/processes/stream?prefix=${encodeURIComponent(prefix)}${database ? `&database=${encodeURIComponent(database)}` : ''}`;
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
    setTimeout(() => connect(baseUrl, prefix, database), 3000);
  };
}

function subscribe(listener: () => void) {
  _listeners.add(listener);
  return () => { _listeners.delete(listener); };
}

function getSnapshot(): ProcessListStreamState {
  return _state;
}

export function useProcessListStream(): ProcessListStreamState {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const baseUrl = useOptioBaseUrl();
  connect(baseUrl, prefix, database);
  return useSyncExternalStore(subscribe, getSnapshot);
}
