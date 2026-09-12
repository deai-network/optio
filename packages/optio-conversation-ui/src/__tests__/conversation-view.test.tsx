import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import type { ReactElement } from 'react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import type { ChatItem, ChatState } from '../chat.js';

// ConversationView is the engine-neutral chrome: render + local UI state + the
// input bar + a thin header. These tests drive it directly with a stub `state`
// and spy callbacks, asserting both the §3 visual polish (bubble tails, tints,
// copy button) and the interaction wiring (send/clear, Escape-to-interrupt,
// closable error Alert, session controls, theme toggle) — independent of any
// engine transport.

// jsdom has no clipboard by default; install a spy so the per-answer copy
// button has a writeText to call. Re-installed per test to reset call counts.
let writeText: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.restoreAllMocks();
  writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
    writable: true,
  });
});

// A ChatState carrying one of every ChatItem kind, so a single render exercises
// the whole renderItem switch. seq is the React key; the reducer keeps order.
function makeState(items: ChatItem[], over: Partial<ChatState> = {}): ChatState {
  return { items, busy: false, closed: false, controls: [], ...over };
}

const ALL_KINDS: ChatItem[] = [
  { kind: 'user', text: 'hello from user', seq: 1 },
  { kind: 'assistant', text: 'hello from assistant', pending: false, seq: 2, msgId: 'm1' },
  { kind: 'activity', text: 'System: did a thing', seq: 3 },
  { kind: 'thinking', text: 'let me reason about this', seq: 7 },
  { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 4 },
  {
    kind: 'permission',
    requestId: 'req-1',
    toolName: 'Write',
    input: { file_path: '/tmp/x' },
    answered: null,
    seq: 5,
  },
  { kind: 'closed', reason: 'done', seq: 6 },
];

// Fill the pinned ConversationViewProps; each test overrides the few props it
// cares about. onSend defaults to a resolved-true spy (the happy path).
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

// Always render under a ConfigProvider so theme.useToken() returns the real
// token set the bubble tints/tails are derived from.
function renderView(props: ConversationViewProps): ReturnType<typeof render> {
  return render(<ConfigProvider>{(<ConversationView {...props} />) as ReactElement}</ConfigProvider>);
}

function rerenderView(r: ReturnType<typeof render>, props: ConversationViewProps): void {
  r.rerender(<ConfigProvider>{(<ConversationView {...props} />) as ReactElement}</ConfigProvider>);
}

describe('ConversationView item rendering', () => {
  it('renders each ChatItem kind', () => {
    renderView(makeProps({ state: makeState(ALL_KINDS) }));

    // user / assistant bubbles carry their text (assistant via AnswerBlock).
    expect(screen.getByText('hello from user')).toBeTruthy();
    expect(screen.getByText('hello from assistant')).toBeTruthy();
    // activity (System:) bubble.
    expect(screen.getByText('System: did a thing')).toBeTruthy();
    // tool call card (verbose).
    expect(screen.getByTestId('tool-call')).toBeTruthy();
    expect(screen.getByText('Bash')).toBeTruthy();
    // unanswered permission card with approve/deny.
    expect(screen.getByTestId('permission-card')).toBeTruthy();
    expect(screen.getByTestId('permission-approve')).toBeTruthy();
    expect(screen.getByTestId('permission-deny')).toBeTruthy();
    // closed divider item.
    expect(screen.getByText(/conversation ended/)).toBeTruthy();
  });

  it('thinking rows are gated by thinkingVerbosity and styled distinctly from System', () => {
    const state = makeState([{ kind: 'thinking', text: 'my reasoning trace', seq: 1 }]);
    // hidden → not rendered
    const hidden = renderView(makeProps({ state, thinkingVerbosity: 'hidden' }));
    expect(screen.queryByText('my reasoning trace')).toBeNull();
    expect(screen.queryByTestId('thinking')).toBeNull();
    hidden.unmount();
    // visible → rendered, in its own 'thinking' element (a "Reasoning" caption),
    // NOT the centered lavender 'activity'/System bubble.
    renderView(makeProps({ state, thinkingVerbosity: 'visible' }));
    expect(screen.getByTestId('thinking')).toBeTruthy();
    expect(screen.getByText('my reasoning trace')).toBeTruthy();
    expect(screen.getByText('Reasoning')).toBeTruthy();
  });

  it('gives the user bubble a right-tail radius and the assistant bubble a left-tail radius', () => {
    renderView(
      makeProps({
        state: makeState([
          { kind: 'user', text: 'u', seq: 1 },
          { kind: 'assistant', text: 'a', pending: false, seq: 2, msgId: 'm1' },
        ]),
      }),
    );
    const user = screen.getByText('u') as HTMLElement;
    const assistant = screen.getByText('a').closest('div[style*="border-radius"]') as HTMLElement;
    expect(user.style.borderRadius).toBe('14px 14px 4px 14px');
    expect(assistant.style.borderRadius).toBe('14px 14px 14px 4px');
  });

  it('renders a status-tinted permission card (warning border)', () => {
    renderView(
      makeProps({
        state: makeState([
          {
            kind: 'permission',
            requestId: 'r',
            toolName: 'Write',
            input: {},
            answered: null,
            seq: 1,
          },
        ]),
      }),
    );
    const card = screen.getByTestId('permission-card');
    // The tint comes from the antd warning tokens — assert the card carries a
    // (non-empty, themed) border + background rather than the flat default.
    expect((card as HTMLElement).style.border).toBeTruthy();
    expect((card as HTMLElement).style.background).toBeTruthy();
  });

  it('hides an already-answered permission card', () => {
    renderView(
      makeProps({
        state: makeState([
          {
            kind: 'permission',
            requestId: 'r',
            toolName: 'Write',
            input: {},
            answered: 'allow',
            seq: 1,
          },
        ]),
      }),
    );
    expect(screen.queryByTestId('permission-card')).toBeNull();
  });

  it('routes approve/deny clicks to onPermission with the request id and behavior', () => {
    const onPermission = vi.fn();
    renderView(
      makeProps({
        onPermission,
        state: makeState([
          {
            kind: 'permission',
            requestId: 'req-9',
            toolName: 'Write',
            input: {},
            answered: null,
            seq: 1,
          },
        ]),
      }),
    );
    fireEvent.click(screen.getByTestId('permission-approve'));
    expect(onPermission).toHaveBeenCalledWith('req-9', 'allow');
    fireEvent.click(screen.getByTestId('permission-deny'));
    expect(onPermission).toHaveBeenCalledWith('req-9', 'deny');
  });
});

describe('ConversationView per-answer copy', () => {
  it('copies the raw answer text to the clipboard', () => {
    renderView(
      makeProps({
        state: makeState([
          { kind: 'assistant', text: 'copy me verbatim', pending: false, seq: 1, msgId: 'm1' },
        ]),
      }),
    );
    fireEvent.click(screen.getByTestId('answer-copy'));
    expect(writeText).toHaveBeenCalledWith('copy me verbatim');
  });
});

describe('ConversationView Escape-to-interrupt', () => {
  it('calls onInterrupt on a window-level Escape while busy', () => {
    const onInterrupt = vi.fn();
    renderView(makeProps({ busy: true, onInterrupt }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it('does not interrupt on Escape when idle', () => {
    const onInterrupt = vi.fn();
    renderView(makeProps({ busy: false, onInterrupt }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it('does not interrupt on Escape when closed even if busy', () => {
    const onInterrupt = vi.fn();
    renderView(makeProps({ busy: true, closed: true, onInterrupt }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onInterrupt).not.toHaveBeenCalled();
  });
});

describe('ConversationView send', () => {
  it('calls onSend with the typed text and empty attachments, then clears on true', async () => {
    const onSend = vi.fn(async () => true);
    renderView(makeProps({ onSend }));
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'ship it' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(onSend).toHaveBeenCalledWith('ship it', []));
    // Cleared on success.
    await waitFor(() => expect((screen.getByTestId('conversation-input-box') as HTMLTextAreaElement).value).toBe(''));
  });

  it('shows a closable error Alert when onSend returns false, and clears it on close', async () => {
    const onSend = vi.fn(async () => false);
    renderView(makeProps({ onSend }));
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'will fail' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(screen.getByTestId('conversation-error')).toBeTruthy());
    // The failed send does not clear the input (operator can retry).
    expect((screen.getByTestId('conversation-input-box') as HTMLTextAreaElement).value).toBe('will fail');

    // The Alert is closable: its close button removes the error.
    fireEvent.click(screen.getByTestId('conversation-error').querySelector('.ant-alert-close-icon') as HTMLElement);
    await waitFor(() => expect(screen.queryByTestId('conversation-error')).toBeNull());
  });
});

describe('ConversationView session controls slot', () => {
  it('renders declared session controls in the input bar', () => {
    renderView(
      makeProps({
        controls: [{ id: 'model', kind: 'select', label: 'Model', value: 'a',
                     options: [{ value: 'a', label: 'A' }] }],
        onControlChange: () => {},
      }),
    );
    expect(screen.getByTestId('control-model')).toBeTruthy();
  });
});

describe('ConversationView theme toggle', () => {
  it('renders the theme toggle only when onToggleTheme is provided and calls it', () => {
    const onToggleTheme = vi.fn();
    renderView(makeProps({ onToggleTheme, themeMode: 'light' }));
    const toggle = screen.getByTestId('theme-toggle');
    expect(toggle.textContent).toBe('🌙');
    fireEvent.click(toggle);
    expect(onToggleTheme).toHaveBeenCalledTimes(1);
  });

  it('shows the ☀ glyph in dark mode', () => {
    renderView(makeProps({ onToggleTheme: vi.fn(), themeMode: 'dark' }));
    expect(screen.getByTestId('theme-toggle').textContent).toBe('☀');
  });

  it('omits the theme toggle when onToggleTheme is absent', () => {
    renderView(makeProps({}));
    expect(screen.queryByTestId('theme-toggle')).toBeNull();
    // The wide toggle is always present in the header regardless.
    expect(screen.getByTestId('wide-toggle')).toBeTruthy();
  });
});

describe('ConversationView tool rows: elapsed time, results, background jobs', () => {
  afterEach(() => vi.useRealTimers());

  it('a running timed row shows a live elapsed counter', () => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
    const state = makeState([{ kind: 'tool', name: 'Bash', input: { command: 'sleep 99' }, seq: 1, startedAt: 88_000 }]);
    renderView(makeProps({ state, toolVerbosity: 'description-only' }));
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('12s');
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('15s');
  });

  it('a finished row shows its final duration and stops counting', () => {
    vi.useFakeTimers();
    vi.setSystemTime(500_000);
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: 'make' }, seq: 1, status: 'done', startedAt: 0, endedAt: 184_000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'description-only' }));
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('3m 04s');
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId('tool-elapsed').textContent).toContain('3m 04s');
  });

  it('rows without startedAt show no counter', () => {
    renderView(makeProps({ state: makeState([{ kind: 'tool', name: 'Bash', input: {}, seq: 1 }]) }));
    expect(screen.queryByTestId('tool-elapsed')).toBeNull();
  });

  it('verbose shows the result under the args once the row is expanded', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 1, status: 'done', result: 'file1', startedAt: 0, endedAt: 1000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'verbose' }));
    expect(screen.queryByTestId('tool-result')).toBeNull(); // finished -> collapsed
    fireEvent.click(screen.getByText('Bash'));
    expect(screen.getByTestId('tool-result').textContent).toBe('file1');
  });

  it('silent hides tool rows but keeps a finished background job as one muted line', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 1, status: 'done', startedAt: 0, endedAt: 1000 },
      { kind: 'tool', name: 'Bash', input: { command: './harness.sh' }, seq: 2, status: 'done', background: true, result: 'Lean-verify all 15', startedAt: 0, endedAt: 372_000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'silent' }));
    expect(screen.queryByTestId('tool-call')).toBeNull();
    const line = screen.getByTestId('background-finished').textContent!;
    expect(line).toContain('Background task finished: Lean-verify all 15');
    expect(line).toContain('6m 12s');
  });

  it('a stopped tool row shows the stopped glyph and status', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: './harness.sh' }, seq: 1, status: 'stopped', background: true, startedAt: 0, endedAt: 1000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'description-only' }));
    const row = screen.getByTestId('tool-call');
    expect(row.getAttribute('data-tool-status')).toBe('stopped');
    expect(row.textContent).toContain('⏹');
  });

  it('silent hides tool rows but keeps a stopped background job as one muted line', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: './harness.sh' }, seq: 1, status: 'stopped', background: true, result: 'Lean-verify all 15', startedAt: 0, endedAt: 372_000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'silent' }));
    expect(screen.queryByTestId('tool-call')).toBeNull();
    const line = screen.getByTestId('background-finished').textContent!;
    expect(line).toContain('⏹ Background task stopped: Lean-verify all 15');
    expect(line).toContain('6m 12s');
  });

  it('silent keeps a failed background job as one muted failed line', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: './build.sh' }, seq: 1, status: 'failed', background: true, result: 'Build test venv', startedAt: 0, endedAt: 65_000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'silent' }));
    expect(screen.queryByTestId('tool-call')).toBeNull();
    const line = screen.getByTestId('background-finished').textContent!;
    expect(line).toContain('✗ Background task failed: Build test venv');
    expect(line).toContain('1m 05s');
  });

  it('a background line with an empty summary names the tool instead', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: {}, seq: 1, status: 'done', background: true, result: '', startedAt: 0, endedAt: 9_000 },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'silent' }));
    expect(screen.getByTestId('background-finished').textContent).toBe('✓ Background task finished: Bash · 9s');
  });

  it('description-while-active keeps a finished background job as the muted line, hiding other finished rows', () => {
    const state = makeState([
      { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 1, status: 'done', startedAt: 0, endedAt: 1000 },
      { kind: 'tool', name: 'Bash', input: { command: './harness.sh' }, seq: 2, status: 'done', background: true, result: 'Lean-verify all 15', startedAt: 0, endedAt: 372_000 },
      { kind: 'tool', name: 'Read', input: { file_path: '/x' }, seq: 3, status: 'running' },
    ]);
    renderView(makeProps({ state, toolVerbosity: 'description-while-active' }));
    const rows = screen.getAllByTestId('tool-call');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('/x');
    const line = screen.getByTestId('background-finished').textContent!;
    expect(line).toContain('✓ Background task finished: Lean-verify all 15');
    expect(line).toContain('6m 12s');
  });

  it('silent and description-while-active render the same background line', () => {
    for (const status of ['done', 'failed', 'stopped'] as const) {
      const row: ChatItem = { kind: 'tool', name: 'Bash', input: { command: 'x' }, seq: 1, status, background: true, result: 'job', startedAt: 0, endedAt: 3_000 };
      const silent = renderView(makeProps({ state: makeState([row]), toolVerbosity: 'silent' }));
      const html = screen.getByTestId('background-finished').outerHTML;
      silent.unmount();
      const active = renderView(makeProps({ state: makeState([row]), toolVerbosity: 'description-while-active' }));
      expect(screen.getByTestId('background-finished').outerHTML).toBe(html);
      active.unmount();
    }
  });
});

describe('ConversationView elapsed-counter interval', () => {
  afterEach(() => vi.useRealTimers());

  // The mount effect schedules one-shot focus timers (up to 1000 ms); let them
  // fire so that only the counter interval is left to count.
  function settle(): void {
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
  }
  const runningRow = (seq: number, startedAt: number): ChatItem => ({ kind: 'tool', name: 'Bash', input: { command: 'sleep 99' }, seq, status: 'running', startedAt });
  const doneRow = (seq: number): ChatItem => ({ kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq, status: 'done', startedAt: 0, endedAt: 2_000 });
  const props = (items: ChatItem[]) => makeProps({ state: makeState(items), toolVerbosity: 'description-only' });

  it('runs one interval while a timed row runs and none once every row has finished', () => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
    const r = renderView(props([runningRow(1, 90_000)]));
    settle();
    expect(vi.getTimerCount()).toBe(1);
    rerenderView(r, props([{ ...(runningRow(1, 90_000) as any), status: 'done', endedAt: 95_000 }]));
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.getByTestId('tool-elapsed').textContent).toBe(' · 5s');
  });

  it('clears the interval on unmount', () => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
    const r = renderView(props([runningRow(1, 90_000)]));
    settle();
    expect(vi.getTimerCount()).toBe(1);
    r.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('starts no interval for running rows without startedAt', () => {
    vi.useFakeTimers();
    renderView(props([{ kind: 'tool', name: 'Bash', input: {}, seq: 1, status: 'running' }]));
    settle();
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.queryByTestId('tool-elapsed')).toBeNull();
  });

  it('restarts counting when a new timed row arrives after all rows finished', () => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
    const r = renderView(props([doneRow(1)]));
    settle();
    expect(vi.getTimerCount()).toBe(0);
    rerenderView(r, props([doneRow(1), runningRow(2, 101_000)]));
    expect(vi.getTimerCount()).toBe(1);
    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(screen.getAllByTestId('tool-elapsed')[1].textContent).toBe(' · 2s');
  });
});
