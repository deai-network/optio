import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// GrokView session-controls migration (Task 4): the former bespoke model
// selector is now the id="model" entry of the engine-neutral controls bar,
// seeded from widgetData.controls and channelled back through /control (grok
// switches model INLINE over ACP — session/set_model, no restart).

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
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  };
}

const MODEL_CONTROL = {
  id: 'model',
  kind: 'select',
  label: 'Model',
  category: 'model',
  value: 'grok-composer-2.5-fast',
  options: [
    { value: 'grok-composer-2.5-fast', label: 'Composer 2.5', disabled: false },
    { value: 'grok-build', label: 'Grok Build', disabled: false },
  ],
};

describe('GrokView session controls', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
  });

  it('renders the seeded model control from widgetData.controls', () => {
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'grok', showSessionControls: true, controls: [MODEL_CONTROL] })}
      />,
    );
    expect(screen.getByTestId('control-model')).toBeTruthy();
  });

  it('selecting a model POSTs {id, value} to /control', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'grok', showSessionControls: true, controls: [MODEL_CONTROL] })}
      />,
    );
    // antd Select: open the dropdown, then pick the second option.
    const combo = document.querySelector('[data-testid="control-model"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Grok Build')).toBeTruthy());
    fireEvent.click(screen.getByText('Grok Build'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const calls = fetchMock.mock.calls as any[];
    const controlCall = calls.find((c) => String(c[0]).endsWith('/control'));
    expect(controlCall).toBeTruthy();
    expect(JSON.parse((controlCall[1] as RequestInit).body as string)).toEqual({
      id: 'model',
      value: 'grok-build',
    });
  });

  it('hides the controls bar when showSessionControls is false', () => {
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'grok', showSessionControls: false, controls: [MODEL_CONTROL] })}
      />,
    );
    expect(screen.queryByTestId('control-model')).toBeNull();
  });
});
