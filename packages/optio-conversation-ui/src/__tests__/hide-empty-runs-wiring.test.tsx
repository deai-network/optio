import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// hideEmptyRuns (owner request 2026-10-12) is a host prop (WidgetProps, from
// optio-ui's useProcessWidgetState option): every engine's live view hands it
// to ConversationView. The view itself is stubbed here to see what it gets.

const seen = vi.hoisted(() => ({ props: [] as Array<{ hideEmptyRuns?: boolean }> }));
vi.mock('../ConversationView.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../ConversationView.js')>()),
  ConversationView: (props: { hideEmptyRuns?: boolean }) => {
    seen.props.push(props);
    return null;
  },
}));

class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onopen: ((e: unknown) => void) | null = null;
  constructor(url: string) { this.url = url; }
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

beforeEach(() => {
  seen.props = [];
  (globalThis as unknown as { EventSource: unknown }).EventSource = MockEventSource;
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const PROTOCOLS = ['claudecode', 'opencode', 'grok', 'codex', 'cursor', 'kimicode', 'antigravity'];

function widget(protocol: string, hideEmptyRuns?: boolean) {
  return (
    <ConversationWidget
      // sessionID: OpencodeView renders its conversation once it has one.
      process={{ _id: 'p1', name: 'n', widgetData: { protocol, sessionID: 's1' }, status: { state: 'running' } }}
      apiBaseUrl=""
      widgetProxyUrl="/api/widget/db/gm/p1/"
      prefix="gm"
      database="db"
      {...(hideEmptyRuns ? { hideEmptyRuns } : {})}
    />
  );
}

describe('every live view passes hideEmptyRuns to ConversationView', () => {
  for (const protocol of PROTOCOLS) {
    it(protocol, () => {
      render(widget(protocol, true));
      expect(seen.props.length).toBeGreaterThan(0);
      expect(seen.props.every((p) => p.hideEmptyRuns === true)).toBe(true);
      cleanup();
      seen.props = [];
      render(widget(protocol));
      expect(seen.props.length).toBeGreaterThan(0);
      expect(seen.props.every((p) => !p.hideEmptyRuns)).toBe(true);
    });
  }
});
