import { notification } from 'antd';

export interface BrowserOpenRequest {
  requestId: string;
  url: string;
  createdAt?: string | number; // ISO string (or epoch ms) stamped server-side
}

export type BrowserOpenHandler = (requests: BrowserOpenRequest[] | undefined) => void;

// Module-level dedup across all feed chokepoints. A given requestId fires
// exactly once per app instance, no matter which feed surfaces it.
const _seen = new Set<string>();

// Browser-open is an ephemeral "open this now" signal, but the requests
// accumulate on the process doc and the poller replays the FULL array on every
// (re)connection's first update. `_seen` dedups within one app instance, but a
// full page reload starts empty and would re-open the whole history. So ignore
// any request older than this window: a genuinely-early request (pushed seconds
// before subscribe) still fires; a reload-replayed one (minutes/hours old) does
// not. A missing/unparseable timestamp is treated as stale.
export const BROWSER_OPEN_MAX_AGE_MS = 60_000;

export function isBrowserOpenStale(
  createdAt: string | number | undefined,
  nowMs: number = Date.now(),
): boolean {
  if (createdAt == null) return true;
  const t = typeof createdAt === 'number' ? createdAt : Date.parse(createdAt);
  if (Number.isNaN(t)) return true;
  return nowMs - t > BROWSER_OPEN_MAX_AGE_MS;
}

/**
 * Default view-scoped browser-open handler: best-effort `window.open(url)` with
 * an app-level antd notification fallback ("Open in a new tab ↗") when the popup
 * is blocked (an SSE callback has no user gesture). Used unless a consumer
 * injects its own handler via `OptioProvider`'s `onBrowserOpen` prop
 * (`setBrowserOpenHandler`). Imperative/global.
 */
export function defaultHandleBrowserOpenRequests(requests: BrowserOpenRequest[] | undefined): void {
  if (!requests || requests.length === 0) return;
  for (const req of requests) {
    if (!req || typeof req.requestId !== 'string') continue;
    if (isBrowserOpenStale(req.createdAt)) continue; // ignore replayed history
    if (_seen.has(req.requestId)) continue;
    _seen.add(req.requestId);

    // The capture shim may quote the URL (e.g. `"https://x"`). Strip a single
    // pair of surrounding double quotes so window.open / href get a clean URL.
    const url = req.url.replace(/^"(.*)"$/, '$1');

    let opened: Window | null = null;
    try {
      opened = window.open(url, '_blank', 'noopener,noreferrer');
    } catch {
      opened = null;
    }
    if (!opened) {
      notification.info({
        message: 'A task wants to open a page',
        description: (
          // eslint-disable-next-line react/no-unknown-property
          <a href={url} target="_blank" rel="noopener noreferrer">
            Open in a new tab ↗
          </a>
        ),
        duration: 0,
      });
    }
  }
}

// Active handler — swapped by OptioProvider's `onBrowserOpen` prop. Mirrors how
// onAttention/onClientMessage feed sessionEvents' module-level `_callbacks`.
let _handler: BrowserOpenHandler = defaultHandleBrowserOpenRequests;

/**
 * Inject a custom browser-open handler (or `undefined` to restore the default).
 * Called by `OptioProvider` from its `onBrowserOpen` prop. A consumer can thus
 * own the open-or-fallback UX (e.g. detect popup-block and surface an app banner)
 * without any optio-ui change.
 */
export function setBrowserOpenHandler(fn: BrowserOpenHandler | undefined): void {
  _handler = fn ?? defaultHandleBrowserOpenRequests;
}

/**
 * Dispatcher invoked by every per-process feed chokepoint with the
 * `browserOpenRequests` each `update` carries. Routes to the active handler
 * (default or injected). The chokepoints call this unchanged.
 */
export function handleBrowserOpenRequests(requests: BrowserOpenRequest[] | undefined): void {
  _handler(requests);
}

// Test-only reset of the dedup set.
export function __resetBrowserOpenSeenForTest(): void {
  _seen.clear();
}
