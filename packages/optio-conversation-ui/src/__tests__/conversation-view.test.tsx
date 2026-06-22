import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import type { ReactElement } from 'react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import type { ChatItem, ChatState } from '../chat.js';

// ConversationView is the engine-neutral chrome: render + local UI state + the
// input bar + a thin header. These tests drive it directly with a stub `state`
// and spy callbacks, asserting both the §3 visual polish (bubble tails, tints,
// copy button) and the interaction wiring (send/clear, Escape-to-interrupt,
// closable error Alert, modelSelector node, theme toggle) — independent of any
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
  return { items, busy: false, closed: false, ...over };
}

const ALL_KINDS: ChatItem[] = [
  { kind: 'user', text: 'hello from user', seq: 1 },
  { kind: 'assistant', text: 'hello from assistant', pending: false, seq: 2, msgId: 'm1' },
  { kind: 'activity', text: 'System: did a thing', seq: 3 },
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

describe('ConversationView model selector slot', () => {
  it('renders the modelSelector node in the input bar', () => {
    renderView(
      makeProps({
        modelSelector: <div data-testid="my-model-selector">picker</div>,
      }),
    );
    expect(screen.getByTestId('my-model-selector')).toBeTruthy();
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
