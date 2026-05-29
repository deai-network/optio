import { notification } from 'antd';

interface BrowserOpenRequest {
  requestId: string;
  url: string;
}

// Module-level dedup across all feed chokepoints. A given requestId fires
// exactly once per app instance, no matter which feed surfaces it.
const _seen = new Set<string>();

/**
 * View-scoped browser-open handler. Called from every per-process feed
 * chokepoint with the `browserOpenRequests` each `update` carries. For each
 * not-yet-seen requestId it attempts `window.open(url)` and raises an
 * app-level antd notification with an "Open in a new tab ↗" link — the
 * always-available fallback when window.open is popup-blocked (an SSE
 * callback has no user gesture). Imperative/global; visible regardless of
 * which view is mounted.
 */
export function handleBrowserOpenRequests(requests: BrowserOpenRequest[] | undefined): void {
  if (!requests || requests.length === 0) return;
  for (const req of requests) {
    if (!req || typeof req.requestId !== 'string') continue;
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

// Test-only reset of the dedup set.
export function __resetBrowserOpenSeenForTest(): void {
  _seen.clear();
}
