import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { Profiler } from 'react';
import { render, screen, act } from '@testing-library/react';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';
import { ConversationWidget } from '../ConversationWidget.js';

// The live view applies its backlog in one step
// (docs/2026-10-14-conversation-backlog-batch-design.md, section 3): an
// EventSource stub that delivers the listener's SSE frames, the named
// backlog-start / backlog-end markers included, and the connection's open.
class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onopen: ((e: Event) => void) | null = null;
  listeners = new Map<string, Array<(e: MessageEvent) => void>>();
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {}
  open() {
    this.onopen?.(new Event('open'));
  }
  emit(ev: unknown, seq: number) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) }));
  }
  emitNamed(type: string, data: unknown) {
    for (const fn of this.listeners.get(type) ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
}

const es = () => MockEventSource.last!;
const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
const answer = (text: string) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text }] } });
const BACKLOG = [user('what is 2+2?'), answer('2+2 is 4.'), user('and 3+3?')];

function props(over: Record<string, unknown> = {}) {
  return {
    process: { _id: 'p1', name: 'n', widgetData: { protocol: 'claudecode' }, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
    ...over,
  } as any;
}

// Called at every commit of the view, after the DOM was updated.
let onCommit: () => void = () => {};
function mount(over: Record<string, unknown> = {}) {
  return render(
    <Profiler id="view" onRender={() => onCommit()}>
      <ClaudeCodeView {...props(over)} />
    </Profiler>,
  );
}
function backlog(events: unknown[], after = 0) {
  act(() => {
    es().open();
    es().emitNamed('backlog-start', { after });
    events.forEach((ev, i) => es().emit(ev, after + i + 1));
  });
}
const end = (seq: number) => act(() => es().emitNamed('backlog-end', { seq }));
const shown = (text: string) => screen.queryByText(text) !== null;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
  onCommit = () => {};
});
afterEach(() => { vi.useRealTimers(); });

describe('ClaudeCodeView: the backlog in one step', () => {
  // How many of a backlog's messages the page shows, at every commit. User
  // messages only: their text renders in the commit that adds them (an answer's
  // markdown fills in one commit later, on every path, a renderer matter).
  const USERS = [user('first question'), user('second question'), user('third question')];
  const TEXTS = ['first question', 'second question', 'third question'];
  function recordPartials(): number[] {
    const seen: number[] = [];
    onCommit = () => { seen.push(TEXTS.filter((t) => document.body.textContent!.includes(t)).length); };
    return seen;
  }

  it('collects from backlog-start and never shows a part of the backlog', () => {
    const seen = recordPartials();
    mount();
    backlog(USERS);
    expect(shown('first question')).toBe(false);
    end(3);
    expect(seen.every((n) => n === 0 || n === 3)).toBe(true);
    expect(seen.at(-1)).toBe(3);
  });

  it('(control) a listener without markers, one message per task, shows the backlog in parts', () => {
    const seen = recordPartials();
    mount();
    USERS.forEach((ev, i) => act(() => es().emit(ev, i + 1)));
    expect(seen).toContain(1);
    expect(seen).toContain(2);
  });

  it('ends in the same state as one dispatch per event', () => {
    const batched = mount();
    backlog(BACKLOG);
    end(3);
    const batchedText = batched.container.textContent;
    batched.unmount();
    const oneByOne = mount();
    act(() => BACKLOG.forEach((ev, i) => es().emit(ev, i + 1)));
    expect(oneByOne.container.textContent).toBe(batchedText);
  });

  it('shows the placeholder until the first backlog-end', () => {
    mount({ placeholder: <div data-testid="host-card" /> });
    expect(screen.getByTestId('host-card')).toBeTruthy();
    backlog(BACKLOG);
    expect(screen.getByTestId('host-card')).toBeTruthy();
    end(3);
    expect(screen.queryByTestId('host-card')).toBeNull();
    expect(shown('2+2 is 4.')).toBe(true);
  });

  it('without a placeholder, the empty conversation until then', () => {
    mount();
    backlog(BACKLOG);
    expect(screen.getByTestId('conversation-input-box')).toBeTruthy();
    expect(shown('what is 2+2?')).toBe(false);
  });

  it('after the end, a message renders at once', () => {
    mount();
    backlog(BACKLOG);
    end(3);
    act(() => es().emit(answer('3+3 is 6.'), 4));
    expect(shown('3+3 is 6.')).toBe(true);
  });

  it('a reconnect keeps the conversation on screen and applies the catch-up at its end', () => {
    const onReady = vi.fn();
    mount({ placeholder: <div data-testid="host-card" />, onReady });
    backlog(BACKLOG);
    end(3);
    expect(onReady).toHaveBeenCalledTimes(1);
    act(() => {
      es().emitNamed('backlog-start', { after: 3 });
      es().emit(answer('3+3 is 6.'), 4);
    });
    expect(shown('2+2 is 4.')).toBe(true);
    expect(shown('3+3 is 6.')).toBe(false);
    expect(screen.queryByTestId('host-card')).toBeNull();
    end(4);
    expect(shown('3+3 is 6.')).toBe(true);
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('a message before any backlog-start goes live at once and calls onReady once', () => {
    const onReady = vi.fn();
    mount({ placeholder: <div data-testid="host-card" />, onReady });
    act(() => es().emit(user('what is 2+2?'), 1));
    expect(shown('what is 2+2?')).toBe(true);
    expect(screen.queryByTestId('host-card')).toBeNull();
    act(() => es().emit(answer('2+2 is 4.'), 2));
    expect(shown('2+2 is 4.')).toBe(true);
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('2 s after open without markers: live with what it has', () => {
    vi.useFakeTimers();
    const onReady = vi.fn();
    mount({ placeholder: <div data-testid="host-card" />, onReady });
    act(() => es().open());
    act(() => { vi.advanceTimersByTime(1_999); });
    expect(screen.getByTestId('host-card')).toBeTruthy();
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.queryByTestId('host-card')).toBeNull();
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('2 s after backlog-start without an end: applies what it has', () => {
    vi.useFakeTimers();
    mount();
    backlog(BACKLOG);
    act(() => { vi.advanceTimersByTime(1_999); });
    expect(shown('2+2 is 4.')).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    expect(shown('2+2 is 4.')).toBe(true);
  });

  it('creating the EventSource arms no fallback', () => {
    vi.useFakeTimers();
    const onReady = vi.fn();
    mount({ placeholder: <div data-testid="host-card" />, onReady });
    act(() => { vi.advanceTimersByTime(5_000); });
    expect(screen.getByTestId('host-card')).toBeTruthy();
    expect(onReady).not.toHaveBeenCalled();
  });
});

describe('ConversationWidget: onReady for views that do not batch yet', () => {
  it('other protocols call onReady when they mount', () => {
    const onReady = vi.fn();
    render(<ConversationWidget {...props({ onReady, process: { _id: 'p1', name: 'n', widgetData: { protocol: 'grok' }, status: { state: 'running' } } })} />);
    expect(onReady).toHaveBeenCalledTimes(1);
  });
});
