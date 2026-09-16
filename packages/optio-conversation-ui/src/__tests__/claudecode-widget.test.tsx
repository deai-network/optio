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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

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
    // Busy: the send is the main half of [Send when ready | Interrupt and send].
    await act(async () => {
      fireEvent.click(document.querySelector('[data-action-id="send-when-ready"]') as HTMLElement);
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

  it('narration from a thinking block renders as a reply even with thinkingVerbosity hidden', () => {
    render(<ConversationWidget {...makeProps({ process: { _id: 'p1', name: 'n', widgetData: { thinkingVerbosity: 'hidden' }, status: { state: 'running' } } })} />);
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'thinking', thinking: '', signature: 's' }] } });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'thinking', thinking: 'Checking the VPN before the harness run.', signature: 's' }] } });
    expect(screen.getByText('Checking the VPN before the harness run.')).toBeTruthy();
  });

  it('description-only keeps one row per call and marks finished calls', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }] } });
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'a b', is_error: false }] } });
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't2', name: 'Read', input: { file_path: '/x' } }] } });
    const rows = screen.getAllByTestId('tool-call');
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-tool-status')).toBe('done');
    expect(rows[1].getAttribute('data-tool-status')).toBe('running');
  });

  it('a background task notification never renders as a user bubble', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: './harness.sh' } }] } });
    fire({ type: 'system', subtype: 'task_started', task_id: 'b1', tool_use_id: 't1', is_backgrounded: true });
    fire({ type: 'user', origin: { kind: 'task-notification' }, message: { role: 'user', content: '<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>t1</tool-use-id>\n<status>completed</status>\n<summary>Rewrite harness</summary>\n</task-notification>' } });
    expect(screen.queryByText(/task-notification/)).toBeNull();
    expect(screen.getByTestId('tool-call').getAttribute('data-tool-status')).toBe('done');
  });

  // Wire times of the session below: a foreground call that finished, a
  // background job that ran 12 s (task_updated end_time) and a call still
  // running. System events carry no timestamp, as on the wire.
  const at = (s: number) => new Date(Date.UTC(2026, 8, 12, 10, 0, s)).toISOString();
  function fireSession(summary = 'Lean-verify all 15') {
    fire({ type: 'assistant', timestamp: at(0), message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls', description: 'list files' } }] } });
    fire({ type: 'user', timestamp: at(1), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'a b', is_error: false }] } });
    fire({ type: 'assistant', timestamp: at(2), message: { role: 'assistant', content: [{ type: 'tool_use', id: 't2', name: 'Bash', input: { command: './harness.sh', run_in_background: true } }] } });
    fire({ type: 'system', subtype: 'task_started', task_id: 'b2', tool_use_id: 't2', is_backgrounded: true });
    fire({ type: 'user', timestamp: at(3), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't2', content: 'Command running in background with ID: b2', is_error: false }] } });
    fire({ type: 'system', subtype: 'task_updated', task_id: 'b2', patch: { status: 'completed', end_time: Date.parse(at(14)) } });
    fire({ type: 'system', subtype: 'task_notification', task_id: 'b2', tool_use_id: 't2', status: 'completed', summary });
    fire({ type: 'assistant', timestamp: at(20), message: { role: 'assistant', content: [{ type: 'tool_use', id: 't3', name: 'Read', input: { file_path: '/x' } }] } });
  }
  const statuses = () => screen.queryAllByTestId('tool-call').map((r) => r.getAttribute('data-tool-status'));

  it('tool verbosity silent: no tool rows, one muted line for the finished background job', () => {
    render(<ConversationWidget {...propsV('silent')} />);
    fireSession();
    expect(statuses()).toEqual([]);
    expect(screen.getByTestId('background-finished').textContent).toBe('✓ Background task finished: Lean-verify all 15 · 12s');
  });

  it('tool verbosity description-while-active: the running call, plus the background job line', () => {
    render(<ConversationWidget {...propsV('description-while-active')} />);
    fireSession();
    expect(statuses()).toEqual(['running']);
    expect(screen.getByTestId('tool-call').textContent).toContain('/x');
    expect(screen.getByTestId('background-finished').textContent).toBe('✓ Background task finished: Lean-verify all 15 · 12s');
  });

  it('tool verbosity description-only: one line per call, the background job with its summary and duration', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fireSession();
    expect(statuses()).toEqual(['done', 'done', 'running']);
    const bg = screen.getAllByTestId('tool-call')[1].textContent!;
    expect(bg).toContain('Lean-verify all 15');
    expect(bg).toContain('12s');
    expect(screen.queryByTestId('background-finished')).toBeNull();
    expect(screen.queryByTestId('tool-result')).toBeNull();
  });

  it('tool verbosity verbose: finished calls collapse and expand to their result; the running call shows its args', () => {
    render(<ConversationWidget {...propsV('verbose')} />);
    fireSession();
    expect(statuses()).toEqual(['done', 'done', 'running']);
    expect(screen.getByText('file_path')).toBeTruthy();
    expect(screen.queryByTestId('tool-result')).toBeNull();
    fireEvent.click(screen.getAllByTestId('tool-call')[0].firstElementChild!);
    expect(screen.getByTestId('tool-result').textContent).toBe('a b');
  });

  it('an empty background summary shows the tool name in the silent line', () => {
    render(<ConversationWidget {...propsV('silent')} />);
    fireSession('');
    expect(screen.getByTestId('background-finished').textContent).toBe('✓ Background task finished: Bash · 12s');
  });

  it('a call still running when the session closes shows as stopped', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fire({ type: 'assistant', timestamp: at(0), message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'sleep 99' } }] } });
    fire({ type: 'x-optio-closed', reason: 'process ended' });
    const row = screen.getByTestId('tool-call');
    expect(row.getAttribute('data-tool-status')).toBe('stopped');
    expect(row.textContent).toContain('⏹');
  });

  it('a background job the resumed run lost shows as stopped after the resume marker', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fire({ type: 'assistant', timestamp: at(0), message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: './harness.sh' } }] } });
    fire({ type: 'system', subtype: 'task_started', task_id: 'b1', tool_use_id: 't1', is_backgrounded: true });
    fire({ type: 'user', timestamp: at(1), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'Command running in background with ID: b1', is_error: false }] } });
    fire({ type: 'x-optio-resumed' });
    expect(screen.getByTestId('tool-call').getAttribute('data-tool-status')).toBe('stopped');
    expect(screen.queryByTestId('conversation-closed')).toBeNull();
  });

  it('a new call whose input has a result key still shows as running', () => {
    render(<ConversationWidget {...propsV('description-only')} />);
    fire({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Write', input: { file_path: '/x', result: 'draft' } }] } });
    expect(screen.getByTestId('tool-call').getAttribute('data-tool-status')).toBe('running');
  });

  it('a busy send shows the Queued bubble under the /send id; the listener event does not duplicate it; the echo takes it', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, id: 'q7', queued: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'count to 10' }] } });
    fire({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: '1 2 3' } } });
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'stop at 5' } });
    await act(async () => {
      fireEvent.click(document.querySelector('[data-action-id="send-when-ready"]') as HTMLElement);
    });
    await waitFor(() => expect(screen.getByTestId('queued-bubble').textContent).toContain('stop at 5'));
    fire({ type: 'x-optio-queued', id: 'q7', text: 'stop at 5' });
    expect(screen.getAllByTestId('queued-bubble')).toHaveLength(1);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'stop at 5' }] } });
    expect(screen.queryByTestId('queued-bubble')).toBeNull();
    expect(screen.getByText('stop at 5')).toBeTruthy();
  });

  it('Interrupt and send POSTs the text to /steer and echoes it', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, id: 's1' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'long job' }] } });
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'change of plan' } });
    fireEvent.click(screen.getByTestId('conversation-send-combined').querySelector('.ant-dropdown-trigger') as HTMLElement);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Interrupt and send' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/steer');
    expect(JSON.parse(init.body as string)).toEqual({ text: 'change of plan' });
    await waitFor(() => expect(screen.getByText('change of plan')).toBeTruthy());
  });

  // Fix 13b: Send now delivers up to the bubble it was clicked on.
  it('Send now on a queued bubble POSTs an empty text and that bubble\'s id as upTo to /steer', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, id: null }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'long job' }] } });
    fire({ type: 'x-optio-queued', id: 'q1', text: 'later' });
    fireEvent.click(screen.getByTestId('queued-send-now'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/steer');
    expect(JSON.parse(init.body as string)).toEqual({ text: '', upTo: 'q1' });
  });

  // Fix 24: the cancel-and-resend layer Fix 17 made dead (steering.py never
  // cancels a queued message on our behalf any more) is gone: Send now on
  // message 1 of 2 no longer tells the reducer to expect a resend for
  // message 2, so a genuine command_lifecycle 'cancelled' for it (e.g. if
  // cancel_async_message were ever re-enabled) must produce its ordinary
  // "Not delivered" note again, exactly as an unrelated cancel already does.
  it('Send now on the 1st of 2 queued bubbles: a cancel for the 2nd still produces its normal Not delivered note', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, id: null }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'long job' }] } });
    fire({ type: 'x-optio-queued', id: 'q1', text: 'first' });
    fire({ type: 'x-optio-queued', id: 'q2', text: 'second' });
    const links = screen.getAllByTestId('queued-send-now');
    fireEvent.click(links[0]);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    fire({ type: 'command_lifecycle', command_uuid: 'q2', state: 'cancelled' });
    expect(screen.getByTestId('activity-muted').textContent).toBe('Not delivered: second');
    expect(screen.getAllByTestId('queued-bubble')).toHaveLength(1);
    expect(screen.getByText('first')).toBeTruthy();
  });

  // Fix 6 (manual-test finding 1): the Interrupt button must learn whether
  // its POST actually reached the listener, so ClaudeCodeView's onInterrupt
  // now resolves the boolean postJson/post already computes instead of
  // discarding it.
  // Fix 18: Interrupt is now a vultus ActionButton nested inside the
  // data-testid="conversation-interrupt" wrapper span, so the click must
  // land on the real <button> — a click fired on the wrapper does not
  // bubble down to it. The accessible name is still 'Interrupt'.
  it('a failed /interrupt POST makes onInterrupt resolve false, surfacing the pending-then-failed button state', async () => {
    const fetchMock = vi.fn(async () => new Response('', { status: 500 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'go' }] } });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'working' }] } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Interrupt' }));
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/interrupt');
    await waitFor(() =>
      expect(screen.getByTestId('conversation-error').textContent).toContain('Interrupt failed — retry.'),
    );
  });

  it('an ok /interrupt POST makes onInterrupt resolve true, with no error shown', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'go' }] } });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'working' }] } });
    const button = screen.getByRole('button', { name: 'Interrupt' });
    await act(async () => {
      fireEvent.click(button);
    });
    // Wait on a settled POSITIVE signal — the button's pending spinner
    // clearing — before asserting the error is absent. Asserting right
    // after fetch was merely CALLED (Fix 27 I7) runs before Response.json()
    // is read and the failure branch could even render, so it could not
    // have caught a broken success path.
    await waitFor(() => expect(button.classList.contains('ant-btn-loading')).toBe(false));
    expect(screen.queryByTestId('conversation-error')).toBeNull();
  });

  it('an interrupt renders the cut-off answer with a jagged edge and one muted row, no error', () => {
    render(<ConversationWidget {...makeProps()} />);
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'essay please' }] } });
    fire({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'Lighthouses stand' } } });
    fire({ type: 'x-optio-interrupt', by: 'user' });
    fire({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'Lighthouses stand tall' }] } });
    fire({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: '[Request interrupted by user]' }] } });
    fire({ type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: 'aborted_streaming' });
    expect(screen.getByTestId('answer-interrupted').textContent).toContain('Lighthouses stand tall');
    expect(screen.getByTestId('activity-muted').textContent).toBe('⏹ Interrupted by you');
    expect(screen.queryByTestId('conversation-error-item')).toBeNull();
    expect(screen.queryByText('[Request interrupted by user]')).toBeNull();
  });
});
