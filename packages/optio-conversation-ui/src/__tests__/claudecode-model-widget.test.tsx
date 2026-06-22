import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';

// Minimal EventSource stub: ClaudeCodeView opens one on mount for the event
// stream. The model-picker tests don't drive any events, so a no-op that
// records the last instance is enough (mirrors the opencode-model-widget test).
class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  close() {}
}

const MODELS = [
  { id: 'claude-sonnet-4-5', label: 'Sonnet 4.5' },
  { id: 'claude-opus-4-1', label: 'Opus 4.1' },
];

function makeProps(widgetData: any = {}) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  } as any;
}

beforeEach(() => {
  vi.restoreAllMocks();
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
});

describe('ClaudeCodeView model picker', () => {
  it('is hidden when showModelSelector is absent/false', () => {
    render(<ClaudeCodeView {...makeProps({ models: MODELS })} />);
    expect(screen.queryByTestId('model-select')).toBeNull();
  });

  it('is shown when showModelSelector is true and models are supplied', () => {
    render(<ClaudeCodeView {...makeProps({ showModelSelector: true, models: MODELS })} />);
    expect(screen.getByTestId('model-select')).toBeTruthy();
  });

  it('selecting a model POSTs the model id to the proxy model endpoint', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ClaudeCodeView
        {...makeProps({ showModelSelector: true, models: MODELS, currentModel: 'claude-sonnet-4-5' })}
      />,
    );

    // antd Select renders the options into a popup on open; open it via the
    // selector node, then click the labelled option (value = the model id).
    fireEvent.mouseDown(screen.getByTestId('model-select').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('Opus 4.1')).toBeTruthy());
    fireEvent.click(screen.getByText('Opus 4.1'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/model');
    expect(JSON.parse(init.body as string)).toEqual({ model: 'claude-opus-4-1' });
  });
});
