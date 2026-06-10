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
  | { requestId: string; type: 'client'; keyword: string; data: unknown };

export interface SessionEventCallbacks {
  onAttention?: (processId: string, reason: string) => void;
  onClientMessage?: (processId: string, keyword: string, data: unknown) => void;
}

let _sessionId: string | null = null;
let _eventSource: EventSource | null = null;
let _callbacks: SessionEventCallbacks = {};
let _baseUrl = '';
let _prefix: string | undefined;
let _database: string | undefined;
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
  // Scope the subscription to the selected instance, exactly like the
  // process-stream feeds — else it resolves the server's default instance.
  const params = new URLSearchParams();
  params.set('sessionId', sessionId);
  if (_prefix) params.set('prefix', _prefix);
  if (_database) params.set('database', _database);
  const url = `${_baseUrl}/api/session-events/stream?${params.toString()}`;
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
        } else if (ev.type === 'client') {
          _callbacks.onClientMessage?.(processId, ev.keyword, ev.data);
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
export function startSessionEvents(
  baseUrl: string,
  prefix: string | undefined,
  database: string | undefined,
  callbacks: SessionEventCallbacks,
): void {
  _callbacks = callbacks;
  // Activate only when there's a handler AND the instance is known. A missing
  // prefix means the upstream resolver hasn't decided yet — wait (deactivate),
  // do not guess a default.
  const active = Boolean((callbacks.onAttention || callbacks.onClientMessage) && prefix);
  const unchanged =
    _eventSource && _baseUrl === baseUrl && _prefix === prefix && _database === database;
  _baseUrl = baseUrl;
  _prefix = prefix;
  _database = database;
  if (!active) {
    closeStream();
    return;
  }
  if (unchanged) return;
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
  if (_callbacks.onAttention || _callbacks.onClientMessage) connect();
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
