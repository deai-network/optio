import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor, act } from '@testing-library/react';
import { ConfigProvider, theme } from 'antd';
import type { ReactNode } from 'react';
import { VultusProvider, type SpinnerProps } from 'vultus-antd';
import { getSuspendedWidget } from 'optio-ui';
import { SuspendedConversationWidget } from '../SuspendedConversationWidget.js';
import { ClaudeCodeReplayView } from '../claudecode/ClaudeCodeReplayView.js';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { registerConversationWidget } from '../ConversationWidget.js';
import { reduceEvent } from '../claudecode/events.js';
import { resetResumeSends, startResumeSend } from '../claudecode/resumeAndSend.js';
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
  resetResumeSends();
  reduceSpy.mockClear();
  refetch.mockReset();
  RecordingEventSource.instances = [];
  vi.stubGlobal('EventSource', RecordingEventSource);
  fetchSpy = vi.fn(async () => new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchSpy);
});
afterEach(() => {
  resetResumeSends();
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

    expect(screen.getByTestId('conversation-closed').textContent).toBe('Session has been saved');
    // One divider: the closing event's own "conversation ended (suspended)" row is not shown.
    expect(screen.queryByText(/conversation ended/)).toBeNull();

    // The composer takes text (sending resumes the session); Send waits for text.
    expect((screen.getByTestId('conversation-input-box') as HTMLTextAreaElement).disabled).toBe(false);
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
    expect(screen.getByTestId('conversation-closed').textContent).toBe('Session has been saved');
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

  it('folds reasoning into thinking rows when widgetData has thinkingVerbosity visible, as the live view does', () => {
    const thought: unknown[] = [
      [1, { type: 'assistant', message: { role: 'assistant', id: 'm1', content: [{ type: 'thinking', thinking: 'Weighing wc -l against a loop.', signature: 's' }] } }],
    ];
    useProcessConversation.mockReturnValue(loaded(thought));
    const hidden = render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(reduceSpy.mock.calls.map((c) => c[4])).toEqual([{ thinkingRows: false }, { thinkingRows: false }]);
    expect(screen.queryByTestId('thinking')).toBeNull();
    hidden.unmount();
    reduceSpy.mockClear();

    render(wrap(<SuspendedConversationWidget {...widgetProps({
      widgetData: { protocol: 'claudecode', thinkingVerbosity: 'visible' },
    })} />));
    expect(reduceSpy.mock.calls.map((c) => c[4])).toEqual([{ thinkingRows: true }, { thinkingRows: true }]);
    expect(screen.getByTestId('thinking').textContent).toContain('Weighing wc -l against a loop.');
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

  it('a leftover permission card stays visible but inert: Approve and Deny send nothing', () => {
    const parked: unknown[] = [
      RECORD[0],
      [2, { type: 'control_request', request_id: 'req-9', request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'rm -rf /tmp/x' } } }],
    ];
    useProcessConversation.mockReturnValue(loaded(parked));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByTestId('permission-card')).toBeTruthy();
    fireEvent.click(screen.getByTestId('permission-approve'));
    fireEvent.click(screen.getByTestId('permission-deny'));
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(RecordingEventSource.instances).toHaveLength(0);
    expect(screen.getByTestId('permission-card')).toBeTruthy();
  });

  it('defaults to the claudecode replay when widgetData has no protocol', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps({ widgetData: {} })} />));
    expect(screen.getByText('List the files')).toBeTruthy();
  });
});

describe('SuspendedConversationWidget: sending resumes the session', () => {
  const box = () => screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
  const LAUNCH = '/api/processes/p1/launch?database=db&prefix=gm';
  const GET = '/api/processes/p1?database=db&prefix=gm';
  const jsonResp = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
  // launch answers `launch`; the process then stays in `state`.
  const api = (launch: Response, state = 'scheduled') =>
    vi.fn(async (url: string) => (url === LAUNCH ? launch : url === GET ? jsonResp(200, { status: { state } }) : jsonResp(404, {})));
  const typeAndSend = (text: string) => {
    fireEvent.change(box(), { target: { value: text } });
    fireEvent.click(within(screen.getByTestId('conversation-send-combined')).getByRole('button'));
  };

  it('Send asks first; Cancel sends nothing and keeps the text', async () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    typeAndSend('carry on');
    const cancel = await screen.findByRole('button', { name: 'Cancel' });
    expect(document.querySelector('.ant-modal-confirm-title')?.textContent).toBe('Resume the session?');
    expect(document.querySelector('.ant-modal-confirm-content')?.textContent).toBe(
      'This session is currently suspended. Sending this message will resume it. Is this what you want?',
    );
    fireEvent.click(cancel);
    await waitFor(() => expect(box().disabled).toBe(false));
    expect(box().value).toBe('carry on');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('conversation-error')).toBeNull();
  });

  it('Resume and send: resumes the process, clears the input and shows the message waiting', async () => {
    const f = api(jsonResp(200, { _id: 'p1' }));
    vi.stubGlobal('fetch', f);
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    typeAndSend('carry on');
    fireEvent.click(await screen.findByRole('button', { name: 'Resume and send' }));
    await waitFor(() => expect(f.mock.calls.some((c) => c[0] === LAUNCH)).toBe(true));
    const launch = f.mock.calls.find((c) => c[0] === LAUNCH) as unknown as [string, RequestInit];
    expect(JSON.parse(String(launch[1].body))).toMatchObject({ resume: true });
    await waitFor(() => expect(screen.getByTestId('optio-resume-send-pending')).toBeTruthy());
    expect(box().value).toBe('');
    // Locked while the message waits: no second resume from here.
    expect(box().disabled).toBe(true);
  });

  it('a refused resume shows its message and keeps the text', async () => {
    vi.stubGlobal('fetch', api(jsonResp(409, { reason: 'already-running', message: 'Process is already running' })));
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    typeAndSend('carry on');
    fireEvent.click(await screen.findByRole('button', { name: 'Resume and send' }));
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent).toContain('Process is already running'));
    expect(box().value).toBe('carry on');
    expect(screen.queryByTestId('optio-resume-send-pending')).toBeNull();
  });

  it('a message that could not be sent comes back into the input, with the reason', async () => {
    vi.stubGlobal('fetch', api(jsonResp(200, { status: { state: 'scheduled' } }), 'failed'));
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    await act(async () => {
      await startResumeSend(
        { processId: 'p1', apiBaseUrl: '', database: 'db', prefix: 'gm', widgetProxyUrl: '/api/widget/db/gm/p1/' },
        'try again later',
        { pollMs: 0, timeoutMs: 600_000, graceMs: 600_000 },
      );
    });
    await waitFor(() => expect(screen.getByTestId('optio-resume-send-failed').textContent).toContain(
      'Your message was not sent: The session stopped before the message could be sent.',
    ));
    expect(box().value).toBe('try again later');
    expect(box().disabled).toBe(false);
    fireEvent.click(screen.getByTestId('optio-resume-send-failed').querySelector('.ant-alert-close-icon') as HTMLElement);
    await waitFor(() => expect(screen.queryByTestId('optio-resume-send-failed')).toBeNull());
  });

  it('over unsaved work, the question warns that resuming discards it', async () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps({ supportsResurrect: true, hasUnsavedWork: true })} />));
    typeAndSend('carry on');
    const ok = await screen.findByRole('button', { name: 'Discard and send' });
    expect(document.querySelector('.ant-modal-confirm-content')?.textContent).toBe(
      'This session is currently suspended. Sending this message will resume it. Is this what you want? '
      + 'This discards the unsaved work left by the failed run.',
    );
    expect(ok.className).toContain('ant-btn-dangerous');
  });

  it('readOnly (a read-only host): the composer stays disabled', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} {...{ readOnly: true }} />));
    expect(box().disabled).toBe(true);
  });

  it('mounted without the widget URLs, the replay stays read-only', () => {
    render(wrap(
      <ClaudeCodeReplayView process={process() as any} widgetData={{ protocol: 'claudecode' }} events={RECORD} capturedAt={CAPTURED_AT} />,
    ));
    expect(box().disabled).toBe(true);
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

  it('a record fetched before the resume stays on screen when a refetch answers not-suspended', () => {
    // The replay is kept through a resume (optio-ui's 'resuming' mode).
    useProcessConversation.mockReturnValue({ ...loaded(RECORD), error: { status: 409, body: { reason: 'not-suspended', message: 'x' } } });
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('List the files')).toBeTruthy();
  });

  it('a 500 shows the message and a Retry that refetches', () => {
    useProcessConversation.mockReturnValue(failed(500, { reason: 'read-failed', message: 'Could not read the conversation record.' }));
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    expect(screen.getByText('Could not read the conversation record.')).toBeTruthy();
    expect(refetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('Retry shows progress while the refetch runs and ignores clicks', () => {
    useProcessConversation.mockReturnValue({
      ...failed(500, { reason: 'read-failed', message: 'Could not read the conversation record.' }),
      isFetching: true,
    });
    render(wrap(<SuspendedConversationWidget {...widgetProps()} />));
    const retry = screen.getByRole('button', { name: /Retry/ });
    expect(retry.classList.contains('ant-btn-loading')).toBe(true);
    fireEvent.click(retry);
    expect(refetch).not.toHaveBeenCalled();
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

describe('SuspendedConversationWidget: while the session resumes', () => {
  // optio-ui keeps the suspended variant on screen until the live widget is
  // ready; the relaunch has cleared widgetData and stoppedAt meanwhile.
  const resumingProcess = (state = 'running') => ({ status: { state }, widgetData: null });

  it('keeps the replay, with the settings and record it had, and puts a spinner panel in place of the input', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    const props = widgetProps({ widgetData: { protocol: 'claudecode', toolVerbosity: 'silent' } });
    const { rerender } = render(wrap(<SuspendedConversationWidget {...props} />));
    expect(screen.getByTestId('conversation-input-box')).toBeTruthy();
    rerender(wrap(<SuspendedConversationWidget {...props} process={{ ...props.process, ...resumingProcess() }} />));
    expect(screen.getByText('List the files')).toBeTruthy();
    expect(screen.queryAllByTestId('tool-call')).toHaveLength(0); // still silent
    // Same query as before the resume: the record is not fetched again.
    expect(useProcessConversation.mock.calls.at(-1)![1]).toEqual({ enabled: true, stoppedAt: '2026-10-12T09:05:01.000Z' });
    expect(screen.getByTestId('optio-replay-resuming').textContent).toContain('Resuming the session…');
    expect(screen.queryByTestId('conversation-input-box')).toBeNull();
  });

  it('a message sent to resume is mentioned in the panel, once', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: { state: 'scheduled' } }), { status: 200 })));
    await startResumeSend(
      { processId: 'p1', apiBaseUrl: '', database: 'db', prefix: 'gm', widgetProxyUrl: '/api/widget/db/gm/p1/' },
      'carry on',
      { pollMs: 50, timeoutMs: 600_000, graceMs: 600_000 },
    );
    useProcessConversation.mockReturnValue(loaded(RECORD));
    const props = widgetProps();
    const { rerender } = render(wrap(<SuspendedConversationWidget {...props} />));
    rerender(wrap(<SuspendedConversationWidget {...props} process={{ ...props.process, ...resumingProcess('scheduled') }} />));
    expect(screen.getByTestId('optio-replay-resuming').textContent).toContain('Your message will be sent once it is ready.');
    expect(screen.queryByTestId('optio-resume-send-pending')).toBeNull();
  });

  it('shows the app\'s lent spinner, big, keyed under the process', () => {
    calls.length = 0;
    useProcessConversation.mockReturnValue(loaded(RECORD));
    const props = widgetProps();
    const { rerender } = render(lent(<SuspendedConversationWidget {...props} />));
    rerender(lent(<SuspendedConversationWidget {...props} process={{ ...props.process, ...resumingProcess() }} />));
    const panel = screen.getByTestId('optio-replay-resuming');
    expect(within(panel).getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process:p1/resuming');
    expect(calls.find((c) => c.spinnerKey === 'optio:process:p1/resuming')?.size).toBe(48);
  });

  it('without a lent spinner: antd\'s large Spin', () => {
    useProcessConversation.mockReturnValue(loaded(RECORD));
    const props = widgetProps();
    const { rerender } = render(wrap(<SuspendedConversationWidget {...props} />));
    rerender(wrap(<SuspendedConversationWidget {...props} process={{ ...props.process, ...resumingProcess() }} />));
    expect(screen.getByTestId('optio-replay-resuming').querySelector('.ant-spin-lg')).not.toBeNull();
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

  describe('non-replay states paint their own surface', () => {
    // jsdom normalises colours on assignment; compare like with like.
    const css = (color: string) => {
      const el = document.createElement('div');
      el.style.background = color;
      return el.style.background;
    };
    const darkLayout = css(theme.getDesignToken({ algorithm: theme.darkAlgorithm }).colorBgLayout);
    const lightLayout = css(theme.getDesignToken().colorBgLayout);
    const darkText = (() => {
      const el = document.createElement('div');
      el.style.color = theme.getDesignToken({ algorithm: theme.darkAlgorithm }).colorText;
      return el.style.color;
    })();

    beforeEach(() => {
      vi.spyOn(console, 'info').mockImplementation(() => {});
      localStorage.setItem('optio-conversation:theme', 'dark');
    });
    afterEach(() => {
      localStorage.removeItem('optio-conversation:theme');
    });

    it('the tokens under test differ between light and dark', () => {
      expect(darkLayout).not.toBe(lightLayout);
    });

    it.each([
      ['the other-protocol note', 'optio-conversation-suspended-note', { widgetData: { protocol: 'grok' } }, pending()],
      ['the no-record note', 'optio-conversation-suspended-note', {}, failed(404, { reason: 'no-conversation', message: 'x' })],
      ['the error with Retry', 'optio-conversation-suspended-error', {}, failed(500, { reason: 'read-failed', message: 'boom' })],
      ['loading', 'optio-conversation-suspended-loading', {}, pending()],
    ])('ownTheme dark: %s sits on the dark layout surface', (_name, testId, over, result) => {
      registerConversationWidget({ ownTheme: true });
      const Suspended = getSuspendedWidget('conversation')!;
      useProcessConversation.mockReturnValue(result);
      render(<Suspended {...widgetProps(over)} />);
      const surface = screen.getByTestId(testId);
      expect(surface.style.background).toBe(darkLayout);
      expect(surface.style.color).toBe(darkText);
    });

    it('without ownTheme: the host theme\'s (light) layout surface', () => {
      registerConversationWidget();
      const Suspended = getSuspendedWidget('conversation')!;
      useProcessConversation.mockReturnValue(pending());
      render(wrap(<Suspended {...widgetProps({ widgetData: { protocol: 'grok' } })} />));
      expect(screen.getByTestId('optio-conversation-suspended-note').style.background).toBe(lightLayout);
    });
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
