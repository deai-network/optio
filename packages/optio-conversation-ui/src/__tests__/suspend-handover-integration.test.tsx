import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StrictMode } from 'react';
import { render, screen, act } from '@testing-library/react';
import { useProcessWidgetState } from 'optio-ui';
import { registerConversationWidget } from '../ConversationWidget.js';

// Integration: optio-ui's hook with the real conversation widget (the live
// ClaudeCodeView and the suspended view) and a host placeholder, through a
// suspend: running -> cancelling -> cancelled. The live transcript must stay
// on screen until the saved one shows; the placeholder must never show.

const conversationQuery = vi.fn();
vi.mock('../../../optio-ui/src/context/useOptioContext.js', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useOptioBaseUrl: () => '',
  useOptioPrefix: () => 'gm',
  useOptioDatabase: () => 'db',
}));
vi.mock('optio-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('optio-ui')>()),
  useProcessConversation: (...args: unknown[]) => conversationQuery(...args),
}));

class MockEventSource {
  static last: MockEventSource | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onopen: ((e: Event) => void) | null = null;
  listeners = new Map<string, Array<(e: MessageEvent) => void>>();
  constructor(public url: string) { MockEventSource.last = this; }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {}
  named(type: string, data: unknown) {
    for (const fn of this.listeners.get(type) ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
  emit(ev: unknown, seq: number) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) }));
  }
}

const LIVE_TEXT = 'a live question';
const SAVED_TEXT = 'a saved question';
const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
const WD = { protocol: 'claudecode' };
const proc = (state: string, extra: Record<string, unknown> = {}) => ({
  _id: 'p1', processId: 'ds__analyze_s1_claudecode', name: 'P', parentId: null, depth: 0, order: 0, children: [],
  status: { state }, progress: { percent: null }, cancellable: true, supportsResume: true, hasSavedState: true,
  uiWidget: 'conversation', widgetData: WD, ...extra,
});
const pending = { data: undefined, error: null, isPending: true, isFetching: true, refetch: vi.fn() };
const loaded = { data: { status: 200, body: { events: [[1, user(SAVED_TEXT)]], capturedAt: '2026-10-14T05:00:00+00:00' } }, error: null, isPending: false, isFetching: false, refetch: vi.fn() };

function Host({ tree }: { tree: any }) {
  const result = useProcessWidgetState(tree, { placeholder: <div data-testid="host-card">card</div> });
  return <div style={{ height: 600 }}>{result?.widget}</div>;
}
const app = (tree: any) => <StrictMode><Host tree={tree} /></StrictMode>;
const visible = (testId: string) => screen.queryAllByTestId(testId).some((el) => {
  for (let n: HTMLElement | null = el; n; n = n.parentElement) if (n.style.display === 'none') return false;
  return true;
});
const textVisible = (text: string) => screen.queryAllByText(text).some((el) => {
  for (let n: HTMLElement | null = el as HTMLElement; n; n = n.parentElement) if (n.style.display === 'none') return false;
  return true;
});

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
  (globalThis as any).EventSource = MockEventSource as any;
  conversationQuery.mockReset();
  conversationQuery.mockReturnValue(pending);
  registerConversationWidget();
});

describe('a suspend with the real conversation widget', () => {
  it('keeps the live transcript until the saved one shows; never the card', () => {
    const view = render(app(proc('running')));
    act(() => {
      MockEventSource.last!.onopen?.(new Event('open'));
      MockEventSource.last!.named('backlog-start', { after: 0 });
      MockEventSource.last!.emit(user(LIVE_TEXT), 1);
      MockEventSource.last!.named('backlog-end', { seq: 1 });
    });
    expect(textVisible(LIVE_TEXT)).toBe(true);
    expect(visible('host-card')).toBe(false);

    view.rerender(app(proc('cancelling')));
    expect(textVisible(LIVE_TEXT)).toBe(true);
    expect(visible('host-card')).toBe(false);

    view.rerender(app(proc('cancelled', { status: { state: 'cancelled', stoppedAt: '2026-10-14T05:22:04.000Z' } })));
    expect(visible('host-card')).toBe(false);
    expect(textVisible(LIVE_TEXT)).toBe(true);

    conversationQuery.mockReturnValue(loaded);
    view.rerender(app(proc('cancelled', { status: { state: 'cancelled', stoppedAt: '2026-10-14T05:22:04.000Z' } })));
    expect(visible('host-card')).toBe(false);
    expect(textVisible(SAVED_TEXT)).toBe(true);
    expect(textVisible(LIVE_TEXT)).toBe(false);
  });
});
