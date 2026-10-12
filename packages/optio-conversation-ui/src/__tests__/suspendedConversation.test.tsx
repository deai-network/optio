import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import type { ReactNode } from 'react';
import { VultusProvider, type SpinnerProps } from 'vultus-antd';
import { getSuspendedWidget } from 'optio-ui';
import { SuspendedConversationWidget } from '../SuspendedConversationWidget.js';
import { ClaudeCodeReplayView } from '../claudecode/ClaudeCodeReplayView.js';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { registerConversationWidget } from '../ConversationWidget.js';
import { reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';

// The read-only view of a suspended conversation task
// (docs/2026-10-12-suspended-conversation-view-design.md, section 5): the saved
// record, fetched once through optio-ui's useProcessConversation (mocked here),
// replayed through the claudecode reducer into a closed ConversationView.

const useProcessConversation = vi.hoisted(() => vi.fn());
vi.mock('optio-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('optio-ui')>();
  return { ...actual, useProcessConversation: (...args: unknown[]) => useProcessConversation(...args) };
});
// The real reducer, wrapped so the tests can see how the replay calls it.
vi.mock('../claudecode/events.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../claudecode/events.js')>();
  return { ...actual, reduceEvent: vi.fn(actual.reduceEvent) };
});
const reduceSpy = vi.mocked(reduceEvent);

const CAPTURED_AT = '2026-10-12T09:05:00+00:00';
const at = (s: number) => new Date(Date.parse('2026-10-12T09:00:00.000Z') + s * 1000).toISOString();

// A saved record: a user message, the assistant's text, and a finished tool row.
const RECORD: unknown[] = [
  [1, { type: 'user', timestamp: at(0), message: { role: 'user', content: [{ type: 'text', text: 'List the files' }] } }],
  [2, { type: 'assistant', timestamp: at(1), message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'Listing them now.' }] } }],
  [3, { type: 'assistant', timestamp: at(2), message: { role: 'assistant', id: 'm2', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'ls' } }] } }],
  [4, { type: 'user', timestamp: at(3), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'a b', is_error: false }] } }],
  [5, { type: 'result', subtype: 'success', result: 'Listing them now.' }],
];

function process(over: Record<string, unknown> = {}) {
  return {
    _id: 'p1',
    name: 'n',
    widgetData: { protocol: 'claudecode' },
    status: { state: 'cancelled', stoppedAt: '2026-10-12T09:05:01.000Z' },
    supportsResume: true,
    hasSavedState: true,
    ...over,
  };
}

function widgetProps(over: Record<string, unknown> = {}) {
  return {
    process: process(over),
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  };
}

const refetch = vi.fn();
const loaded = (events: unknown[], capturedAt: string | null = CAPTURED_AT) => ({
  data: { status: 200, body: { events, capturedAt } },
  error: null,
  isPending: false,
  isFetching: false,
  refetch,
});
const failed = (status: number, body: unknown) => ({
  data: undefined,
  error: { status, body },
  isPending: false,
  isFetching: false,
  refetch,
});
const pending = () => ({ data: undefined, error: null, isPending: true, isFetching: true, refetch });

// Records every EventSource the view might open (it must open none).
class RecordingEventSource {
  static instances: RecordingEventSource[] = [];
  constructor(public url: string) {
    RecordingEventSource.instances.push(this);
  }
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  useProcessConversation.mockReset();
  reduceSpy.mockClear();
  refetch.mockReset();
  RecordingEventSource.instances = [];
  vi.stubGlobal('EventSource', RecordingEventSource);
  fetchSpy = vi.fn(async () => new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchSpy);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const wrap = (node: ReactNode) => <ConfigProvider>{node}</ConfigProvider>;

const calls: SpinnerProps[] = [];
function RecSpinner(p: SpinnerProps) {
  calls.push(p);
  return <span data-testid="rec-spinner" data-key={p.spinnerKey} />;
}
const lent = (node: ReactNode) => (
  <ConfigProvider>
    <VultusProvider components={{ Spinner: RecSpinner }}>{node}</VultusProvider>
  </ConfigProvider>
);

const statuses = () => screen.queryAllByTestId('tool-call').map((r) => r.getAttribute('data-tool-status'));

describe('SuspendedConversationWidget: the claudecode replay', () => {
  it('fetches the record with the process id and its own stoppedAt (no process-doc poll)', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(useProcessConversation).toHaveBeenCalledWith('p1', { enabled: true, stoppedAt: '2026-10-12T09:05:01.000Z' });
  });

  it('passes stoppedAt: null when the process carries none', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps({ status: { state: 'cancelled' } })} />));
    expect(useProcessConversation).toHaveBeenCalledWith('p1', { enabled: true, stoppedAt: null });
  });

  it('renders the saved messages and the finished tool row, read-only, under the suspended divider', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));

    expect(screen.getByText('List the files')).toBeTruthy();
    expect(screen.getByText('Listing them now.')).toBeTruthy();
    expect(statuses()).toEqual(['done']);
    expect(screen.getByTestId('tool-call').textContent).toContain('Bash');

    expect(screen.getByTestId('conversation-closed').textContent).toBe('Suspended — resume to continue');
    // One divider: the closing event's own "conversation ended (suspended)" row is not shown.
    expect(screen.queryByText(/conversation ended/)).toBeNull();

    // The composer and the send button are disabled.
    expect((screen.getByTestId('conversation-input-box') as HTMLTextAreaElement).disabled).toBe(true);
    const send = within(screen.getByTestId('conversation-send-combined')).getByRole('button');
    expect((send as HTMLButtonElement).disabled).toBe(true);
  });

  it('opens no EventSource and makes no network call of its own', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('List the files')).toBeTruthy();
    expect(RecordingEventSource.instances).toHaveLength(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('a tool still running when the task was suspended shows as stopped', () => {
    const running: unknown[] = [
      RECORD[0],
      [7, { type: 'assistant', timestamp: at(4), message: { role: 'assistant', id: 'm3', content: [{ type: 'tool_use', id: 't2', name: 'Bash', input: { command: 'sleep 99' } }] } }],
    ];
    useProcessConversation.mockReturnValue(loaded(running));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(statuses()).toEqual(['stopped']);
    expect(screen.getByTestId('tool-call').textContent).toContain('⏹');
    // Not busy: no working indicator.
    expect(screen.queryByText('working…')).toBeNull();
  });

  it('folds every event with the fixed capturedAt clock, then closes at maxSeq + 1', () => {
    // Out of order, with a gap: the close goes after the highest seq, not the last entry.
    const toolUse = (RECORD[2] as [number, unknown])[1];
    useProcessConversation.mockReturnValue(loaded([RECORD[1], RECORD[0], [9, toolUse]]));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    const fixed = Date.parse(CAPTURED_AT);
    expect(reduceSpy.mock.calls.map((c) => c[3])).toEqual([fixed, fixed, fixed, fixed]);
    expect(reduceSpy.mock.calls.map((c) => c[2])).toEqual([2, 1, 9, 10]);
    expect(reduceSpy.mock.calls.at(-1)![1]).toEqual({ type: 'x-optio-closed', reason: 'suspended' });
  });

  it('without capturedAt, one clock read at mount serves the whole fold', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD, null));
    const before = Date.now();
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    const after = Date.now();
    const nows = reduceSpy.mock.calls.map((c) => c[3] as number);
    expect(nows).toHaveLength(RECORD.length + 1);
    expect(new Set(nows).size).toBe(1);
    expect(nows[0]).toBeGreaterThanOrEqual(before);
    expect(nows[0]).toBeLessThanOrEqual(after);
  });

  it('an empty record still renders the closed, read-only view', () => {
    useProcessConversation.mockReturnValue(loaded([]));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByTestId('conversation-closed').textContent).toBe('Suspended — resume to continue');
    expect(reduceSpy.mock.calls.map((c) => c[2])).toEqual([1]);
  });

  it('re-folds only when the events array changes', () => {
    const result = loaded(RECORD);
    useProcessConversation.mockReturnValue(result);
    const { rerender } = render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(reduceSpy).toHaveBeenCalledTimes(RECORD.length + 1);
    rerender(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(reduceSpy).toHaveBeenCalledTimes(RECORD.length + 1);
    useProcessConversation.mockReturnValue(loaded([...RECORD]));
    rerender(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(reduceSpy).toHaveBeenCalledTimes(2 * (RECORD.length + 1));
  });

  it('applies the tool verbosity from widgetData', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    const byDefault = render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    // The live view's default: description-only, a persistent one-line row.
    expect(statuses()).toEqual(['done']);
    byDefault.unmount();

    render(wrap(<SuspendedConversationWidget {...widgetProps({
      widgetData: { protocol: 'claudecode', toolVerbosity: 'silent' },
    })} />));
    expect(screen.queryAllByTestId('tool-call')).toHaveLength(0);
    expect(screen.getByText('Listing them now.')).toBeTruthy();
  });

  it('offers no upload, download or session controls even when widgetData enables them', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps({
      widgetData: {
        protocol: 'claudecode',
        showFileUpload: true,
        fileDownload: true,
        showSessionControls: true,
        controls: [{ id: 'model', kind: 'select', label: 'Model', value: 'opus', options: [{ value: 'opus', label: 'Opus' }] }],
      },
    })} />));
    expect(screen.queryByTestId('attach-button')).toBeNull();
    expect(screen.queryByTestId('file-input')).toBeNull();
    expect(screen.getByTestId('session-toolbar').textContent).not.toContain('Model');
  });

  it('the resume band of a once-resumed session renders its label', () => {
    useProcessConversation.mockReturnValue(loaded([...RECORD, [6, { type: 'x-optio-resumed' }]]));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByTestId('resume-band').textContent).toBe('Session has been saved and resumed.');
  });

  it('defaults to the claudecode replay when widgetData has no protocol', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps({ widgetData: {} })} />));
    expect(screen.getByText('List the files')).toBeTruthy();
  });
});

describe('SuspendedConversationWidget: other protocols and the error states', () => {
  it('protocol grok shows the resume-to-view note and fetches nothing', () => {
    useProcessConversation.mockReturnValue({ data: undefined, error: null, isPending: true, isFetching: false, refetch });
    render(wrap(<SuspendedConversationWidget {...widgetProps({ widgetData: { protocol: 'grok' } })} />));
    expect(screen.getByText('Resume to view this conversation.')).toBeTruthy();
    expect(useProcessConversation).toHaveBeenCalledWith('p1', { enabled: false, stoppedAt: '2026-10-12T09:05:01.000Z' });
    expect(screen.queryByTestId('optio-widget-loading')).toBeNull();
    expect(screen.queryByTestId('conversation-input-box')).toBeNull();
  });

  it.each(['no-conversation', 'no-conversation-support'])('%s shows the no-saved-conversation note', (reason) => {
    useProcessConversation.mockReturnValue(failed(404, { reason, message: 'whatever the engine said' }));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('No saved conversation to show; resume to continue it.')).toBeTruthy();
    expect(screen.queryByText('Retry')).toBeNull();
  });

  it('not-suspended (the process just resumed) renders nothing', () => {
    useProcessConversation.mockReturnValue(failed(409, { reason: 'not-suspended', message: 'Process is not suspended' }));
    const { container } = render(<SuspendedConversationWidget {...widgetProps()} />);
    expect(container.innerHTML).toBe('');
  });

  it('not-suspended wins over a record fetched before the resume', () => {
    useProcessConversation.mockReturnValue({ ...loaded(RECORD), error: { status: 409, body: { reason: 'not-suspended', message: 'x' } } });
    const { container } = render(<SuspendedConversationWidget {...widgetProps()} />);
    expect(container.innerHTML).toBe('');
  });

  it('a 500 shows the message and a Retry that refetches', () => {
    useProcessConversation.mockReturnValue(failed(500, { reason: 'read-failed', message: 'Could not read the conversation record.' }));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('Could not read the conversation record.')).toBeTruthy();
    expect(refetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('a 503 and a 401 (message only) show their message with Retry', () => {
    useProcessConversation.mockReturnValue(failed(503, { reason: 'engine-unavailable', message: 'The engine is not reachable.' }));
    const first = render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('The engine is not reachable.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    first.unmount();

    useProcessConversation.mockReturnValue(failed(401, { message: 'Unauthorized' }));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('Unauthorized')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('a network failure (no body) still shows a message and Retry', () => {
    useProcessConversation.mockReturnValue({ data: undefined, error: new TypeError('Failed to fetch'), isPending: false, isFetching: false, refetch });
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('Failed to fetch')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});

describe('SuspendedConversationWidget: loading', () => {
  beforeEach(() => {
    calls.length = 0;
  });

  it('shows the lent spinner keyed by the process archive, then "Loading…"', () => {
    useProcessConversation.mockReturnValue(pending());
    render(lent(<SuspendedConversationWidget {...widgetProps()} />));
    const loading = screen.getByTestId('optio-widget-loading');
    expect(within(loading).getAllByTestId('rec-spinner').map((s) => s.getAttribute('data-key')))
      .toEqual(['optio:process:p1:archive']);
    expect(calls.filter((c) => c.spinnerKey === 'optio:process:p1:archive').at(-1))
      .toStrictEqual({ spinnerKey: 'optio:process:p1:archive', size: 'small' });
    expect(loading.textContent).toBe('Loading…');
  });

  it('without a provider: the bare "Loading…" text', () => {
    useProcessConversation.mockReturnValue(pending());
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByTestId('optio-widget-loading').innerHTML).toBe('Loading…');
  });
});

describe('ClaudeCodeReplayView', () => {
  it('scopes its spinner keys under the process', () => {
    calls.length = 0;
    const running: unknown[] = [
      [1, { type: 'assistant', timestamp: at(0), message: { role: 'assistant', id: 'm1', content: [{ type: 'tool_use', id: 't9', name: 'Bash', input: { command: 'sleep 1' } }] } }],
      [2, { type: 'system', subtype: 'task_started', task_id: 'b1', tool_use_id: 't9', is_backgrounded: true }],
    ];
    // The todo list's in-progress step carries a spinner inside the scope.
    render(lent(
      <ClaudeCodeReplayView
        process={process()}
        widgetData={{ protocol: 'claudecode', todos: [{ id: 'td1', text: 'Step one', status: 'in_progress' }] }}
        events={running}
        capturedAt={CAPTURED_AT}
      />,
    ));
    const keys = calls.map((c) => c.spinnerKey);
    expect(keys).toContain('optio:process:p1/optio:todo:td1');
    expect(keys.every((k) => k.startsWith('optio:process:p1/'))).toBe(true);
  });
});

describe('ConversationView closedLabel', () => {
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

  it('absent: the divider reads "conversation ended", as before', () => {
    render(wrap(<ConversationView {...makeProps({ closed: true })} />));
    expect(screen.getByTestId('conversation-closed').textContent).toBe('conversation ended');
  });

  it('set: it replaces only the divider text', () => {
    const state = makeState([{ kind: 'closed', reason: 'done', seq: 1 }], { closed: true });
    render(wrap(<ConversationView {...makeProps({ state, closed: true, closedLabel: 'Suspended — resume to continue' })} />));
    expect(screen.getByTestId('conversation-closed').textContent).toBe('Suspended — resume to continue');
    // A closed row in the transcript keeps its own text.
    expect(screen.getByText('conversation ended (done)')).toBeTruthy();
  });
});

describe('registerConversationWidget', () => {
  it('registers the suspended variant beside the live widget', () => {
    vi.spyOn(console, 'info').mockImplementation(() => {});
    registerConversationWidget();
    const Suspended = getSuspendedWidget('conversation')!;
    expect(Suspended).toBeTruthy();
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<Suspended {...widgetProps()} />));
    expect(screen.getByText('List the files')).toBeTruthy();
    expect(screen.queryByTestId('theme-toggle')).toBeNull();
  });

  it('with ownTheme, the suspended variant owns the light/dark toggle too', () => {
    vi.spyOn(console, 'info').mockImplementation(() => {});
    registerConversationWidget({ ownTheme: true });
    const Suspended = getSuspendedWidget('conversation')!;
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(<Suspended {...widgetProps()} />);
    expect(screen.getByTestId('theme-toggle')).toBeTruthy();
  });
});
