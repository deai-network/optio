import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

const THEME_KEY = 'optio-conversation:theme';

// The conversation views open an EventSource on mount for their stream. The
// theme tests never drive events, so a no-op recorder is enough (mirrors the
// claudecode-/opencode-model-widget tests).
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

beforeEach(() => {
  vi.restoreAllMocks();
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
  // Any mount-time network the view attempts must not throw.
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })));
  localStorage.clear();
});

describe('ConversationWidget ownTheme', () => {
  it('without ownTheme: no theme-toggle button and no localStorage write', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    render(<ConversationWidget {...makeProps()} />);

    // No ☀/🌙 toggle — the view inherits the host theme.
    expect(screen.queryByTestId('theme-toggle')).toBeNull();
    // The widget's own ConfigProvider is the only thing that persists a pref;
    // absent ownTheme, the conversation theme key is never written.
    expect(setItem.mock.calls.some(([k]) => k === THEME_KEY)).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it('with ownTheme: renders the theme-toggle button', () => {
    render(<ConversationWidget {...makeProps()} ownTheme />);
    expect(screen.getByTestId('theme-toggle')).toBeTruthy();
  });

  it('with ownTheme: initial mode defaults to light (🌙 offered) when nothing is persisted', () => {
    render(<ConversationWidget {...makeProps()} ownTheme />);
    // light mode offers the moon (switch-to-dark) glyph.
    expect(screen.getByTestId('theme-toggle').textContent).toContain('🌙');
  });

  it('with ownTheme: initial mode reads "dark" from localStorage (☀ offered)', () => {
    localStorage.setItem(THEME_KEY, 'dark');
    render(<ConversationWidget {...makeProps()} ownTheme />);
    // dark mode offers the sun (switch-to-light) glyph.
    expect(screen.getByTestId('theme-toggle').textContent).toContain('☀');
  });

  it('with ownTheme: clicking the toggle flips the mode and persists it', () => {
    render(<ConversationWidget {...makeProps()} ownTheme />);
    const toggle = screen.getByTestId('theme-toggle');

    // light → dark
    fireEvent.click(toggle);
    expect(localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(screen.getByTestId('theme-toggle').textContent).toContain('☀');

    // dark → light
    fireEvent.click(screen.getByTestId('theme-toggle'));
    expect(localStorage.getItem(THEME_KEY)).toBe('light');
    expect(screen.getByTestId('theme-toggle').textContent).toContain('🌙');
  });
});
