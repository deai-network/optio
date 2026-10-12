import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { SuspendedConversationWidget } from '../SuspendedConversationWidget.js';
import { initialChatState, type ChatItem } from '../chat.js';

// hideEmptyRuns (owner request 2026-10-12): the view leaves out the runs a
// resume added where nothing happened; live it keeps the current last run, the
// suspended replay (closed) does not. The rule itself: empty-runs.test.ts.

const useProcessConversation = vi.hoisted(() => vi.fn());
vi.mock('optio-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('optio-ui')>();
  return { ...actual, useProcessConversation: (...args: unknown[]) => useProcessConversation(...args) };
});

afterEach(cleanup);

const READY = "OK, I'm ready.";
let seq = 0;
const resumedRun = (reply: string): ChatItem[] => [
  { kind: 'resumed', seq: ++seq },
  { kind: 'activity', text: 'System: you have been resumed', seq: ++seq, system: true },
  { kind: 'assistant', text: reply, pending: false, seq: ++seq, msgId: null },
];
const ITEMS: ChatItem[] = [
  { kind: 'user', text: 'When was the last call?', seq: ++seq },
  { kind: 'assistant', text: 'On 2 September.', pending: false, seq: ++seq, msgId: null },
  ...resumedRun(`${READY} (1)`),
  ...resumedRun(`${READY} (2)`),
];

function view(over: Partial<ConversationViewProps>) {
  const props: ConversationViewProps = {
    state: { ...initialChatState, items: ITEMS },
    closed: false, busy: false, toolVerbosity: 'description-while-active', thinkingVerbosity: 'hidden',
    showFileUpload: false, maxUploadBytes: 0, fileDownload: false,
    onSend: vi.fn().mockResolvedValue(true), onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    ...over,
  };
  return render(<ConversationView {...props} />);
}

describe('ConversationView hideEmptyRuns', () => {
  it('without it, every run shows', () => {
    view({});
    expect(screen.queryByText(`${READY} (1)`)).not.toBeNull();
    expect(screen.queryByText(`${READY} (2)`)).not.toBeNull();
  });

  it('live, it leaves out the empty runs but keeps the current one', () => {
    view({ hideEmptyRuns: true });
    expect(screen.queryByText('On 2 September.')).not.toBeNull();
    expect(screen.queryByText(`${READY} (1)`)).toBeNull();
    expect(screen.queryByText(`${READY} (2)`)).not.toBeNull();
  });

  it('closed, it leaves out an empty last run too', () => {
    view({ hideEmptyRuns: true, closed: true, closedLabel: 'Session has been saved' });
    expect(screen.queryByText('On 2 September.')).not.toBeNull();
    expect(screen.queryByText(`${READY} (1)`)).toBeNull();
    expect(screen.queryByText(`${READY} (2)`)).toBeNull();
    expect(screen.queryByText('Session has been saved')).not.toBeNull();
  });
});

describe('the suspended replay honors hideEmptyRuns', () => {
  const at = (s: number) => new Date(Date.parse('2026-10-12T09:00:00.000Z') + s * 1000).toISOString();
  const RECORD: unknown[] = [
    [1, { type: 'user', timestamp: at(0), message: { role: 'user', content: [{ type: 'text', text: 'When was the last call?' }] } }],
    [2, { type: 'assistant', timestamp: at(1), message: { role: 'assistant', id: 'm1', content: [{ type: 'text', text: 'On 2 September.' }] } }],
    [3, { type: 'result', subtype: 'success', result: 'On 2 September.' }],
    [4, { type: 'x-optio-resumed' }],
    [5, { type: 'user', timestamp: at(60), message: { role: 'user', content: [{ type: 'text', text: 'System: you have been resumed' }] } }],
    [6, { type: 'assistant', timestamp: at(61), message: { role: 'assistant', id: 'm2', content: [{ type: 'text', text: READY }] } }],
    [7, { type: 'result', subtype: 'success', result: READY }],
  ];
  const props = (hideEmptyRuns?: boolean) => ({
    process: {
      _id: 'p1', name: 'n', widgetData: { protocol: 'claudecode' },
      status: { state: 'cancelled', stoppedAt: '2026-10-12T09:05:01.000Z' }, supportsResume: true, hasSavedState: true,
    },
    apiBaseUrl: '', widgetProxyUrl: '/api/widget/db/gm/p1/', prefix: 'gm', database: 'db',
    ...(hideEmptyRuns ? { hideEmptyRuns } : {}),
  });
  const loaded = { data: { status: 200, body: { events: RECORD, capturedAt: at(62) } }, error: null, isPending: false, isFetching: false, refetch: vi.fn() };

  it('shows the empty last run without it', () => {
    useProcessConversation.mockReturnValue(loaded);
    render(<SuspendedConversationWidget {...props()} />);
    expect(screen.queryByText('On 2 September.')).not.toBeNull();
    expect(screen.queryByText(READY)).not.toBeNull();
  });

  it('leaves the empty last run out with it', () => {
    useProcessConversation.mockReturnValue(loaded);
    render(<SuspendedConversationWidget {...props(true)} />);
    expect(screen.queryByText('On 2 September.')).not.toBeNull();
    expect(screen.queryByText(READY)).toBeNull();
    expect(screen.queryByText('System: you have been resumed')).toBeNull();
  });
});
