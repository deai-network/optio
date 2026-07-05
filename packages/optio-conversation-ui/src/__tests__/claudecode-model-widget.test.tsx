import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';
import type { SessionControl } from '../chat.js';

// Minimal EventSource stub: ClaudeCodeView opens one on mount for the event
// stream. The control tests don't drive any events, so a no-op that records
// the last instance is enough (mirrors the opencode-model-widget test).
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

const CONTROLS: SessionControl[] = [
  {
    id: 'model',
    kind: 'select',
    label: 'Model',
    category: 'model',
    value: 'claude-sonnet-4-5',
    options: [
      { value: 'claude-sonnet-4-5', label: 'Sonnet 4.5' },
      { value: 'claude-opus-4-1', label: 'Opus 4.1' },
    ],
  },
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

describe('ClaudeCodeView session controls', () => {
  it('are hidden when showSessionControls is absent/false', () => {
    render(<ClaudeCodeView {...makeProps({ controls: CONTROLS })} />);
    expect(screen.queryByTestId('control-model')).toBeNull();
  });

  it('render the model control when showSessionControls is true', () => {
    render(<ClaudeCodeView {...makeProps({ showSessionControls: true, controls: CONTROLS })} />);
    expect(screen.getByTestId('control-model')).toBeTruthy();
  });

  it('selecting a model POSTs {id, value} to the proxy /control endpoint', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ClaudeCodeView {...makeProps({ showSessionControls: true, controls: CONTROLS })} />,
    );

    // antd Select renders the options into a popup on open; open it via the
    // selector node, then click the labelled option (value = the model id).
    fireEvent.mouseDown(screen.getByTestId('control-model').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('Opus 4.1')).toBeTruthy());
    fireEvent.click(screen.getByText('Opus 4.1'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/control');
    expect(JSON.parse(init.body as string)).toEqual({ id: 'model', value: 'claude-opus-4-1' });
  });
});
