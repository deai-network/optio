import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import type { ReactNode } from 'react';
import { VultusProvider, type SpinnerProps } from 'vultus-antd';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { ConversationWidget } from '../ConversationWidget.js';
import { OpencodeView } from '../opencode/OpencodeView.js';
import type { ChatItem, ChatState } from '../chat.js';

// The spinner slot (vultus-core, lent through vultus-antd's VultusProvider
// components={{ Spinner }}): with a lent Spinner, the conversation's loading
// sites render it (keyed, size 'small') in place of antd's <Spin>, the working
// indicator prefers it over the agent-brand nativeSpinner, and each agent view
// scopes every key inside it with `optio:process:<id>`. Without one, every site
// renders antd's spinner as before (the existing tests pin that output; the
// no-provider cases here only check the slot leaves it alone), except the tool
// row: on the Ant Design X experiment it is an X ThoughtChain node, whose own
// loading status icon is the no-provider spinner.

const calls: SpinnerProps[] = [];
function RecSpinner(p: SpinnerProps) {
  calls.push(p);
  return <span data-testid="rec-spinner" data-key={p.spinnerKey} />;
}

/** The props the lent Spinner last received for `key`. */
function propsOf(key: string): SpinnerProps | undefined {
  return calls.filter((c) => c.spinnerKey === key).at(-1);
}

function keysIn(el: HTMLElement): (string | null)[] {
  return within(el).queryAllByTestId('rec-spinner').map((s) => s.getAttribute('data-key'));
}

// Ant Design X experiment: a tool row is an X ThoughtChain node. Its status
// slot holds X's own status icon (loading, check-circle, minus-circle, ...),
// or, while the tool runs and a Spinner is lent, that Spinner as the node icon.
function toolNode(line: HTMLElement): HTMLElement {
  return line.closest('.ant-thought-chain-node') as HTMLElement;
}
function statusOf(line: HTMLElement): HTMLElement {
  return toolNode(line).querySelector('.ant-thought-chain-status') as HTMLElement;
}
/** The aria-label of X's status icon in the row's node, if it shows one. */
function statusIcon(line: HTMLElement): string | null {
  return statusOf(line).querySelector('.anticon')?.getAttribute('aria-label') ?? null;
}

function lent(node: ReactNode) {
  return (
    <ConfigProvider>
      <VultusProvider components={{ Spinner: RecSpinner }}>{node}</VultusProvider>
    </ConfigProvider>
  );
}

function bare(node: ReactNode) {
  return <ConfigProvider>{node}</ConfigProvider>;
}

function makeState(items: ChatItem[], over: Partial<ChatState> = {}): ChatState {
  return { items, busy: false, closed: false, controls: [], ...over };
}

function makeProps(over: Partial<ConversationViewProps> = {}): ConversationViewProps {
  return {
    state: makeState([]),
    closed: false,
    busy: false,
    toolVerbosity: 'verbose',
    thinkingVerbosity: 'visible',
    showFileUpload: false,
    maxUploadBytes: 10_000_000,
    fileDownload: false,
    onSend: vi.fn(async () => true),
    onInterrupt: vi.fn(),
    onPermission: vi.fn(),
    onFileDownload: vi.fn(),
    ...over,
  };
}

beforeEach(() => {
  calls.length = 0;
  vi.restoreAllMocks();
});

describe('background task (site 11)', () => {
  const running = (over: Partial<Extract<ChatItem, { kind: 'tool' }>>): ChatItem => ({
    kind: 'tool', name: 'Bash', input: { command: 'sleep 100' },
    seq: 1, status: 'running', background: true, ...over,
  } as ChatItem);

  it('renders the lent Spinner keyed by the task id, in place of antd\'s', () => {
    const row = running({ seq: 1, callId: 'toolu_1', taskId: 'bfz' });
    render(lent(<ConversationView {...makeProps({ state: makeState([row]), toolVerbosity: 'silent' })} />));
    const line = screen.getByTestId('background-running');
    expect(line.firstElementChild?.getAttribute('data-testid')).toBe('rec-spinner');
    expect(keysIn(line)).toEqual(['optio:background:bfz']);
    expect(propsOf('optio:background:bfz')).toStrictEqual({ spinnerKey: 'optio:background:bfz', size: 'small' });
    expect(line.querySelector('.ant-spin')).toBeNull();
    expect(line.textContent).toContain('Background task running');
  });

  it('falls back to the call id, then the seq', () => {
    const rows = [running({ seq: 2, callId: 'toolu_2' }), running({ seq: 3 })];
    render(lent(<ConversationView {...makeProps({ state: makeState(rows), toolVerbosity: 'silent' })} />));
    const lines = screen.getAllByTestId('background-running');
    expect(lines.flatMap(keysIn)).toEqual(['optio:background:toolu_2', 'optio:background:3']);
  });

  it('without a provider: antd\'s Spin, as before', () => {
    render(bare(<ConversationView {...makeProps({ state: makeState([running({ taskId: 'bfz' })]), toolVerbosity: 'silent' })} />));
    const line = screen.getByTestId('background-running');
    expect(line.firstElementChild?.classList.contains('ant-spin')).toBe(true);
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });
});

describe('tool row (site 12)', () => {
  it('a running tool renders the lent Spinner keyed by its call id', () => {
    const row: ChatItem = { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 4, status: 'running', callId: 'toolu_9' };
    render(lent(<ConversationView {...makeProps({ state: makeState([row]) })} />));
    const line = screen.getByTestId('tool-call');
    // The lent Spinner is the node's icon, in place of X's loading icon.
    expect(keysIn(statusOf(line))).toEqual(['optio:tool:toolu_9']);
    expect(keysIn(toolNode(line))).toEqual(['optio:tool:toolu_9']);
    expect(propsOf('optio:tool:toolu_9')).toStrictEqual({ spinnerKey: 'optio:tool:toolu_9', size: 'small' });
    expect(statusIcon(line)).toBeNull();
    expect(toolNode(line).querySelector('.ant-spin')).toBeNull();
    expect(toolNode(line).textContent).toContain('Bash');
  });

  it('without a call id the key falls back to the seq', () => {
    const row: ChatItem = { kind: 'tool', name: 'Read', input: { file_path: '/x' }, seq: 5 };
    render(lent(<ConversationView {...makeProps({ state: makeState([row]) })} />));
    expect(keysIn(toolNode(screen.getByTestId('tool-call')))).toEqual(['optio:tool:5']);
  });

  it('finished and stopped tools show their status icon, no spinner', () => {
    const rows: ChatItem[] = [
      { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 1, status: 'done', callId: 'a' },
      { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 2, status: 'stopped', callId: 'b' },
    ];
    render(lent(<ConversationView {...makeProps({ state: makeState(rows) })} />));
    const lines = screen.getAllByTestId('tool-call');
    expect(lines.map(toolNode).flatMap(keysIn)).toEqual([]);
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
    expect(statusIcon(lines[0])).toBe('check-circle');
    expect(statusIcon(lines[1])).toBe('minus-circle');
  });

  it('without a provider: X\'s own loading icon', () => {
    const row: ChatItem = { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 4, status: 'running', callId: 'toolu_9' };
    render(bare(<ConversationView {...makeProps({ state: makeState([row]) })} />));
    const line = screen.getByTestId('tool-call');
    expect(statusOf(line).classList.contains('ant-thought-chain-status-loading')).toBe(true);
    expect(statusIcon(line)).toBe('loading');
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });
});

describe('working indicator (site 13)', () => {
  const native = <span data-testid="native-spinner" />;

  it('renders the lent Spinner keyed optio:working', () => {
    render(lent(<ConversationView {...makeProps({ busy: true })} />));
    const indicator = screen.getByText('working…');
    expect(keysIn(indicator)).toEqual(['optio:working']);
    expect(propsOf('optio:working')).toStrictEqual({ spinnerKey: 'optio:working', size: 'large' });
    expect(indicator.querySelector('.ant-spin')).toBeNull();
  });

  it('the lent Spinner wins over the agent-brand nativeSpinner', () => {
    render(lent(<ConversationView {...makeProps({ busy: true, nativeSpinner: native })} />));
    const indicator = screen.getByText('working…');
    expect(keysIn(indicator)).toEqual(['optio:working']);
    expect(screen.queryByTestId('native-spinner')).toBeNull();
  });

  it('not busy: no indicator, no spinner', () => {
    render(lent(<ConversationView {...makeProps({ busy: false })} />));
    expect(screen.queryByText('working…')).toBeNull();
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });

  it('without a provider: the nativeSpinner when given, else antd\'s Spin, as before', () => {
    const { unmount } = render(bare(<ConversationView {...makeProps({ busy: true, nativeSpinner: native })} />));
    let indicator = screen.getByText('working…');
    expect(within(indicator).getByTestId('native-spinner')).toBeTruthy();
    expect(indicator.querySelector('.ant-spin')).toBeNull();
    unmount();
    render(bare(<ConversationView {...makeProps({ busy: true })} />));
    indicator = screen.getByText('working…');
    expect(indicator.querySelector('.ant-spin')).not.toBeNull();
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });
});

describe('todo (site 14)', () => {
  const todos = [
    { id: 't1', text: 'Write', status: 'in_progress' as const },
    { id: 't2', text: 'Ship', status: 'pending' as const },
    { id: 't3', text: 'Retry (failed)', status: 'in_progress' as const },
  ];
  const state = makeState([{ kind: 'user', text: 'hello from user', seq: 1 }]);

  it('an in-progress todo renders the lent Spinner keyed by its id', () => {
    render(lent(<ConversationView {...makeProps({ state, todos })} />));
    const rows = screen.getAllByTestId('conversation-todo');
    // Ant Design X experiment: the spinner is the task node's icon.
    const slot = rows[0].closest('.ant-thought-chain-node')!
      .querySelector('[data-testid="conversation-todo-spinner"]') as HTMLElement;
    expect(keysIn(slot)).toEqual(['optio:todo:t1']);
    expect(propsOf('optio:todo:t1')).toStrictEqual({ spinnerKey: 'optio:todo:t1', size: 'small' });
    expect(slot.querySelector('.ant-spin')).toBeNull();
    // Pending and failed steps keep their marks.
    expect(keysIn(rows[1])).toEqual([]);
    expect(keysIn(rows[2])).toEqual([]);
  });

  it("without a provider: X's loading icon on the task node, as a running tool row", () => {
    render(bare(<ConversationView {...makeProps({ state, todos })} />));
    const node = screen.getAllByTestId('conversation-todo')[0].closest('.ant-thought-chain-node')!;
    expect(node.querySelector('[data-testid="conversation-todo-spinner"]')).toBeNull();
    expect(node.querySelector('.ant-thought-chain-status-loading')).not.toBeNull();
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });
});

describe('OpencodeView loading (site 15)', () => {
  const props = {
    process: { _id: 'p7', name: 'n', widgetData: {}, status: { state: 'running' } },
    widgetProxyUrl: '/api/widget/db/gm/p7/',
  } as any;

  it('renders the lent Spinner before "Loading…"', () => {
    render(lent(<OpencodeView {...props} />));
    const loading = screen.getByTestId('optio-widget-loading');
    expect(keysIn(loading)).toEqual(['optio:process:p7:widget']);
    expect(propsOf('optio:process:p7:widget')).toStrictEqual({ spinnerKey: 'optio:process:p7:widget', size: 'small' });
    expect(loading.textContent).toBe('Loading…');
    const spinner = within(loading).getByTestId('rec-spinner');
    const text = Array.from(loading.childNodes).find((n) => n.nodeType === Node.TEXT_NODE && n.textContent === 'Loading…');
    expect(text).toBeTruthy();
    expect(spinner.compareDocumentPosition(text!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('without a provider: the bare "Loading…" text, as before', () => {
    render(bare(<OpencodeView {...props} />));
    expect(screen.getByTestId('optio-widget-loading').innerHTML).toBe('Loading…');
  });
});

describe('re-rendering across the lent Spinner and the loading gate is safe', () => {
  // The slot hooks are context reads, which React does not reject when called
  // conditionally; its dev build only logs the changed hook order. Assert that
  // it never does.
  let consoleError: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { consoleError = vi.spyOn(console, 'error'); });
  afterEach(() => {
    vi.unstubAllGlobals();
    const logged = consoleError.mock.calls.map((args) => args.map(String).join(' ')).join('\n');
    consoleError.mockRestore();
    expect(logged).not.toMatch(/order of Hooks|Rendered (more|fewer) hooks/);
  });

  // The tree keeps its shape whether or not a Spinner is lent (only the
  // provided value changes: `Spinner: undefined` clears the slot), so a
  // rerender updates the component in place instead of remounting it.
  const stable = (node: ReactNode, withSpinner: boolean) => (
    <ConfigProvider>
      <VultusProvider components={{ Spinner: withSpinner ? RecSpinner : undefined }}>{node}</VultusProvider>
    </ConfigProvider>
  );

  it('OpencodeView: loading -> session -> loading, with and without a lent Spinner', async () => {
    (globalThis as any).EventSource = MockEventSource as any;
    installFetch();
    const view = (sessionID: string | undefined) => (
      <OpencodeView {...widgetProps(sessionID ? { protocol: 'opencode', sessionID, directory: '/wd' } : { protocol: 'opencode' })} />
    );
    const { rerender } = render(stable(view(undefined), false));
    expect(screen.getByTestId('optio-widget-loading').innerHTML).toBe('Loading…');
    rerender(stable(view(undefined), true));
    expect(keysIn(screen.getByTestId('optio-widget-loading'))).toEqual(['optio:process:p1:widget']);
    rerender(stable(view('s1'), true));
    await screen.findByTestId('conversation-input-box');
    expect(screen.queryByTestId('optio-widget-loading')).toBeNull();
    rerender(stable(view(undefined), false));
    expect(screen.getByTestId('optio-widget-loading').innerHTML).toBe('Loading…');
  });

  it('ConversationView: a running tool, lent and not, then finished', () => {
    const tool = (status: 'running' | 'done'): ChatItem =>
      ({ kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 1, status, callId: 'c1' });
    const view = (status: 'running' | 'done', withSpinner: boolean) =>
      stable(<ConversationView {...makeProps({ state: makeState([tool(status)]), busy: status === 'running' })} />, withSpinner);
    const line = () => screen.getByTestId('tool-call');
    const { rerender } = render(view('running', false));
    expect(statusIcon(line())).toBe('loading');
    rerender(view('running', true));
    expect(keysIn(toolNode(line()))).toEqual(['optio:tool:c1']);
    expect(statusIcon(line())).toBeNull();
    expect(keysIn(screen.getByText('working…'))).toEqual(['optio:working']);
    rerender(view('running', false));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
    expect(statusIcon(line())).toBe('loading');
    rerender(view('done', true));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
    expect(statusIcon(line())).toBe('check-circle');
  });
});

// ── Agent views: every key inside a view is scoped with optio:process:<id> ──

class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onopen: ((e: any) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  private listeners = new Map<string, Set<(e: any) => void>>();
  constructor(url: string) {
    this.url = url;
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
  close() {}
  emit(ev: unknown, seq: number) {
    const msg = new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) });
    this.onmessage?.(msg);
    for (const fn of this.listeners.get('message') ?? []) fn(msg);
  }
}

function widgetProps(widgetData: Record<string, unknown>) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  } as any;
}

// Every POST succeeds except `interrupt`, which stays in flight (so the
// Interrupt button stays pending); GETs answer an empty history / config.
function installFetch() {
  const fn = vi.fn((url: string, init?: RequestInit) => {
    const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    if (init?.method === 'POST') {
      if (String(url).endsWith('/interrupt')) return new Promise<Response>(() => {});
      return json({ ok: true });
    }
    if (String(url).includes('/config/providers')) return json({ providers: [], default: {} });
    if (String(url).includes('/message')) return json([]);
    return json({});
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('agent views scope their spinner keys with optio:process:<id>', () => {
  beforeEach(() => {
    MockEventSource.last = null;
    (globalThis as any).EventSource = MockEventSource as any;
    installFetch();
  });
  afterEach(() => vi.unstubAllGlobals());

  it('ClaudeCodeView: the working indicator and the pending Interrupt button', async () => {
    render(lent(<ConversationWidget {...widgetProps({})} />));
    act(() => MockEventSource.last!.emit({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'go' }] } }, 1));
    act(() => MockEventSource.last!.emit({ type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'working' }] } }, 2));
    expect(keysIn(screen.getByText('working…'))).toEqual(['optio:process:p1/optio:working']);

    const button = screen.getByRole('button', { name: 'Interrupt' });
    await act(async () => {
      fireEvent.click(button);
    });
    await waitFor(() => expect(keysIn(button)).toEqual(['optio:process:p1/vultus:action:interrupt']));
    expect(propsOf('optio:process:p1/vultus:action:interrupt'))
      .toStrictEqual({ spinnerKey: 'optio:process:p1/vultus:action:interrupt', size: 'small' });
  });

  it.each([
    ['claudecode', {}],
    ['opencode', { protocol: 'opencode', sessionID: 's1', directory: '/wd' }],
    ['grok', { protocol: 'grok' }],
    ['codex', { protocol: 'codex' }],
    ['cursor', { protocol: 'cursor' }],
    ['kimicode', { protocol: 'kimicode' }],
    ['antigravity', { protocol: 'antigravity' }],
  ])('%s: a send makes the view busy; its working indicator key is scoped', async (_name, widgetData) => {
    render(lent(<ConversationWidget {...widgetProps(widgetData)} />));
    const box = await screen.findByTestId('conversation-input-box');
    fireEvent.change(box, { target: { value: 'hi' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    });
    const indicator = await screen.findByText('working…');
    expect(keysIn(indicator)).toEqual(['optio:process:p1/optio:working']);
  });
});
