import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// AntigravityView is a thin transport adapter: it opens the per-task
// conversation listener SSE ({widgetProxyUrl}events), feeds the RAW
// transcript.jsonl objects to reduceAntigravityEvent, and hands all rendering +
// local UI (send / interrupt / upload / download / controls) to the shared
// ConversationView. Only the wire (antigravity's transcript events over the
// listener) differs from GrokView. These tests prove the widget dispatches on
// protocol==="antigravity" and that the transport wiring round-trips.

class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  addEventListener() {}
  removeEventListener() {}
  close() {}
  emit(ev: unknown, seq: number) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) }));
  }
  static reset() {
    MockEventSource.last = null;
  }
}

function makeProps(widgetData: any = { protocol: 'antigravity' }) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  };
}

let seq = 0;
function fire(ev: unknown) {
  act(() => MockEventSource.last!.emit(ev, ++seq));
}

describe('AntigravityView (transcript wire over the listener)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
    seq = 0;
  });

  it('renders a turn: a user + assistant transcript pair shows the coalesced answer', () => {
    render(<ConversationWidget {...makeProps({ protocol: 'antigravity' })} />);
    // The view must have opened the listener SSE.
    expect(MockEventSource.last).toBeTruthy();
    expect(MockEventSource.last!.url).toBe('/api/widget/db/gm/p1/events');
    fire({ type: 'user', conversationId: 'c1', text: 'say PONG' });
    fire({ type: 'assistant', conversationId: 'c1', text: 'PONG' });
    expect(screen.getByText('PONG')).toBeTruthy();
  });

  it('sending posts /send and shows the optimistic operator echo', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'antigravity' })} />);

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hello agy' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('conversation-send'));
    });

    const calls = fetchMock.mock.calls as any[];
    await waitFor(() => expect(calls.some((c) => String(c[0]).endsWith('/send'))).toBe(true));
    const sendCall = calls.find((c) => String(c[0]).endsWith('/send'));
    expect(JSON.parse((sendCall[1] as RequestInit).body as string).text).toBe('hello agy');
    expect(screen.getByText('hello agy')).toBeTruthy();
  });

  it('verbose tool verbosity renders the transcript tool input as a key-value table', () => {
    render(<ConversationWidget {...makeProps({ protocol: 'antigravity', toolVerbosity: 'verbose' })} />);
    fire({ type: 'tool', conversationId: 'c1', name: 'shell', input: { command: 'ls -la', cwd: '/w' } });
    expect(screen.getByText('command')).toBeTruthy();
    expect(screen.getByText('ls -la')).toBeTruthy();
    expect(screen.getByText('cwd')).toBeTruthy();
  });

  it('an optio-file: link in the answer fetches /download and triggers a blob save', async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    const fetchMock = vi.fn(async () => new Response(bytes, { status: 200, headers: { 'content-type': 'text/markdown' } }));
    vi.stubGlobal('fetch', fetchMock as any);
    (globalThis.URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (globalThis.URL as any).revokeObjectURL = vi.fn();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<ConversationWidget {...makeProps({ protocol: 'antigravity', fileDownload: true })} />);
    fire({ type: 'assistant', conversationId: 'c1', text: 'Here: [report](optio-file:out/r.md)' });

    const link = await screen.findByText(/report/);
    await act(async () => {
      fireEvent.click(link);
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const dlCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).includes('/download'));
    expect(dlCall).toBeTruthy();
    expect(String(dlCall[0])).toContain('path=out%2Fr.md');
    expect(clickSpy).toHaveBeenCalled();
  });
});
