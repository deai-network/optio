import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

class MockEventSource {
  static instances: MockEventSource[] = [];
  static last: MockEventSource | null = null;
  url: string;
  closed = false;
  onopen: ((e: any) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  private listeners = new Map<string, Set<(e: any) => void>>();
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
    MockEventSource.last = this;
  }
  addEventListener(type: string, fn: (e: any) => void) {
    let set = this.listeners.get(type);
    if (!set) this.listeners.set(type, (set = new Set()));
    set.add(fn);
  }
  removeEventListener(type: string, fn: (e: any) => void) {
    this.listeners.get(type)?.delete(fn);
  }
  close() {
    this.closed = true;
  }
  // Deliver a raw stream-json event as the listener's SSE frame would
  // (data: <raw event JSON>, id: <seq>), to whichever handler the widget set.
  emit(ev: unknown, seq: number) {
    const msg = new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) });
    this.onmessage?.(msg);
    for (const fn of this.listeners.get('message') ?? []) fn(msg);
  }
  static reset() {
    MockEventSource.instances = [];
    MockEventSource.last = null;
  }
}

function makeProps(over: any = {}) {
  return {
    process: { _id: 'p1', name: 'n', widgetData: {}, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
    ...over,
  };
}

let seq = 0;
function fire(ev: unknown) {
  act(() => MockEventSource.last!.emit(ev, ++seq));
}

describe('ConversationWidget', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
    seq = 0;
  });

  it('opens an EventSource on the proxy events endpoint and renders bubbles from events', () => {
    render(<ConversationWidget {...makeProps()} />);
    expect(MockEventSource.last).not.toBeNull();
    expect(MockEventSource.last!.url).toBe('/api/widget/db/gm/p1/events');

    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'what is 2+2?' }] } });
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: '2+2 is 4.' }] } });
    fire({ type: 'result', subtype: 'success', result: '2+2 is 4.' });

    expect(screen.getByText('what is 2+2?')).toBeTruthy();
    expect(screen.getByText('2+2 is 4.')).toBeTruthy();
  });

  it('send button POSTs the text to the proxy send endpoint', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'hello' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/send');
    expect(JSON.parse(init.body as string)).toEqual({ text: 'hello' });
  });

  it('clears the working indicator after a mid-turn send completes', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);

    // Turn already in progress (agent working) when the operator sends.
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'count to 10' }] } });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: '1 2 3' }] } });
    expect(screen.getByText('working…')).toBeTruthy();

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'actually stop at 5' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('conversation-send'));
    });

    // The turn ends. The indicator must disappear — it must not stay stuck on
    // a send flag that the busy-change effect never cleared.
    fire({ type: 'result', subtype: 'success', result: 'ok' });
    await waitFor(() => expect(screen.queryByText('working…')).toBeNull());
  });

  it('permission card Approve POSTs the request_id to the proxy permission endpoint', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);

    fire({
      type: 'control_request',
      request_id: 'req-9',
      request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'ls' } },
    });

    fireEvent.click(screen.getByRole('button', { name: /approve/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/permission');
    expect(JSON.parse(init.body as string)).toMatchObject({ request_id: 'req-9', behavior: 'allow' });
  });

  function propsV(level: string) {
    return makeProps({ process: { _id: 'p1', name: 'n', widgetData: { toolVerbosity: level }, status: { state: 'running' } } });
  }
  function fireTool(name: string, input: unknown) {
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', name, input }] } });
  }

  it('tool verbosity: silent renders nothing for a tool call', () => {
    render(<ConversationWidget {...propsV('silent')} />);
    fireTool('bash', { command: 'ls', description: 'list files' });
    expect(screen.queryByTestId('tool-call')).toBeNull();
  });

  it('tool verbosity: description-only shows the description, no input table', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fireTool('bash', { command: 'ls -la', description: 'list files' });
    expect(screen.getByTestId('tool-call').textContent).toContain('list files');
    expect(screen.queryByText('command')).toBeNull();
  });

  it('tool verbosity: description-only falls back to a salient field', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fireTool('read', { file_path: '/a/b.txt' });
    expect(screen.getByTestId('tool-call').textContent).toContain('/a/b.txt');
    expect(screen.queryByText('file_path')).toBeNull();
  });

  it('tool verbosity: description-only with no salient field shows just the name', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fireTool('mystery', { count: 3 });
    expect(screen.getByTestId('tool-call').textContent).toContain('mystery');
    expect(screen.queryByText('count')).toBeNull();
  });

  it('tool verbosity: verbose renders the input key-value table', () => {
    render(<ConversationWidget {...propsV('verbose')} />);
    fireTool('read', { file_path: '/a/b.txt' });
    expect(screen.getByText('file_path')).toBeTruthy();
    expect(screen.getByText('/a/b.txt')).toBeTruthy();
  });
  it('working indicator renders OUTSIDE the resize-observed content node (no reflow loop)', () => {
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'go' }] } });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'on it' }] } });
    expect(screen.getByText('working…')).toBeTruthy(); // visible while busy
    // ...but NOT inside the ResizeObserver-observed content node (its animation
    // there caused a forced-reflow-per-frame CPU loop).
    expect(screen.getByTestId('conversation-content').textContent).not.toContain('working…');
  });
});
