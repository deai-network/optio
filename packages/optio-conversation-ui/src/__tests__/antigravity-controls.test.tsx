import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// AntigravityView session-controls (Stage 7 Task 7.1): the model is the
// id="model" entry of the engine-neutral controls bar, seeded from
// widgetData.controls and channelled back through /control. Antigravity has no
// inline switch — agy switches model by RESTART (the next `agy -p` turn carries
// the new --model) — but from the widget's perspective the wire is identical to
// every other engine: an optimistic fold + POST {id, value} to /control.

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
  static reset() {
    MockEventSource.last = null;
  }
}

function makeProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/ag/p1/',
    prefix: 'ag',
    database: 'db',
  };
}

const MODEL_CONTROL = {
  id: 'model',
  kind: 'select',
  label: 'Model',
  category: 'model',
  value: 'gemini-2.5-pro',
  options: [
    { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro', disabled: false },
    { value: 'claude-sonnet-4', label: 'Claude Sonnet 4', disabled: false },
  ],
};

describe('AntigravityView session controls', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
  });

  it('renders the seeded model control from widgetData.controls', () => {
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'antigravity', showSessionControls: true, controls: [MODEL_CONTROL] })}
      />,
    );
    expect(screen.getByTestId('control-model')).toBeTruthy();
  });

  it('selecting a model POSTs {id, value} to /control', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'antigravity', showSessionControls: true, controls: [MODEL_CONTROL] })}
      />,
    );
    // antd Select: open the dropdown, then pick the second option.
    const combo = document.querySelector('[data-testid="control-model"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Claude Sonnet 4')).toBeTruthy());
    fireEvent.click(screen.getByText('Claude Sonnet 4'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const calls = fetchMock.mock.calls as any[];
    const controlCall = calls.find((c) => String(c[0]).endsWith('/control'));
    expect(controlCall).toBeTruthy();
    expect(JSON.parse((controlCall[1] as RequestInit).body as string)).toEqual({
      id: 'model',
      value: 'claude-sonnet-4',
    });
  });

  it('hides the controls bar when showSessionControls is false', () => {
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'antigravity', showSessionControls: false, controls: [MODEL_CONTROL] })}
      />,
    );
    expect(screen.queryByTestId('control-model')).toBeNull();
  });
});
