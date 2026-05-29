/**
 * Always-on, singleton session-events manager.
 *
 * Owns the tab's opaque `sessionId` (persisted in sessionStorage under
 * "optioSessionId"), a single EventSource against /api/session-events/stream,
 * requestId dedup, and dispatch by `type` to app-supplied callbacks.
 *
 * Module-level (not React state) so `useProcessActions.launch` can read the
 * sessionId without a context dependency, and so the EventSource survives
 * re-renders. Mounted once by OptioProvider.
 */

const SESSION_STORAGE_KEY = 'optioSessionId';

type SessionEvent =
  | { requestId: string; type: 'attention'; reason: string }
  | { requestId: string; type: 'domain'; keyword: string; data: unknown };

export interface SessionEventCallbacks {
  onAttention?: (processId: string, reason: string) => void;
  onDomainMessage?: (processId: string, keyword: string, data: unknown) => void;
}

let _sessionId: string | null = null;
let _eventSource: EventSource | null = null;
let _callbacks: SessionEventCallbacks = {};
let _baseUrl = '';
const _seen = new Set<string>();

function mintToken(): string {
  // crypto.randomUUID is available in all EventSource-capable browsers.
  try {
    return crypto.randomUUID().replace(/-/g, '');
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

/** Return the tab's sessionId, minting + persisting it on first use. */
export function getSessionId(): string {
  if (_sessionId) return _sessionId;
  let stored: string | null = null;
  try {
    stored = sessionStorage.getItem(SESSION_STORAGE_KEY);
  } catch { /* sessionStorage unavailable (SSR/tests) */ }
  if (stored) {
    _sessionId = stored;
  } else {
    _sessionId = mintToken();
    try {
      sessionStorage.setItem(SESSION_STORAGE_KEY, _sessionId);
    } catch { /* ignore */ }
  }
  return _sessionId;
}

function closeStream() {
  _eventSource?.close();
  _eventSource = null;
}

function connect() {
  closeStream();
  const sessionId = getSessionId();
  const url = `${_baseUrl}/api/session-events/stream?sessionId=${encodeURIComponent(sessionId)}`;
  const es = new EventSource(url);
  _eventSource = es;
  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type !== 'session-events') return;
      const processId: string = data.processId;
      const events: SessionEvent[] = data.events ?? [];
      for (const ev of events) {
        if (_seen.has(ev.requestId)) continue;
        _seen.add(ev.requestId);
        if (ev.type === 'attention') {
          _callbacks.onAttention?.(processId, ev.reason);
        } else if (ev.type === 'domain') {
          _callbacks.onDomainMessage?.(processId, ev.keyword, ev.data);
        }
      }
    } catch { /* ignore malformed */ }
  };
  // EventSource auto-reconnects on error; nothing to do here.
}

/**
 * Start (or update) the session-events subscription. Idempotent: safe to call
 * on every render. Updates callbacks + baseUrl in place; (re)connects only
 * when the connection is absent or the baseUrl changed.
 */
export function startSessionEvents(baseUrl: string, callbacks: SessionEventCallbacks): void {
  _callbacks = callbacks;
  // No handler → nothing to deliver to; don't hold an EventSource open. This
  // keeps the session-events stream off entirely for apps that don't use it.
  if (!callbacks.onAttention && !callbacks.onDomainMessage) {
    closeStream();
    _baseUrl = baseUrl;
    return;
  }
  if (_eventSource && _baseUrl === baseUrl) return;
  _baseUrl = baseUrl;
  connect();
}

/**
 * Mint a fresh sessionId and reconnect the SSE. Called by the app on logout /
 * any session cutoff. Clears the dedup set so a new session re-surfaces events.
 */
export function resetSession(): void {
  _sessionId = mintToken();
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, _sessionId);
  } catch { /* ignore */ }
  _seen.clear();
  if (_callbacks.onAttention || _callbacks.onDomainMessage) connect();
  else closeStream();
}

// Test-only full reset.
export function __resetSessionStateForTest(): void {
  closeStream();
  _sessionId = null;
  _callbacks = {};
  _baseUrl = '';
  _seen.clear();
}
