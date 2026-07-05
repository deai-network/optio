import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// CodexView SessionControls migration (session-controls plan, Task 7): the
// bespoke model <Select> is replaced by the generic SessionControls renderer,
// seeded from widgetData.controls; a change POSTs { id, value } to /control
// (codex switches the model INLINE — the choice rides the next turn/start).

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
  emit(ev: unknown, seq: number) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) }));
  }
  static reset() {
    MockEventSource.last = null;
  }
}

function makeProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/cx/p1/',
    prefix: 'cx',
    database: 'db',
  };
}

const MODEL_CONTROL = {
  id: 'model',
  kind: 'select',
  label: 'Model',
  category: 'model',
  value: 'gpt-5.5',
  options: [
    { value: 'gpt-5.5', label: 'GPT-5.5', disabled: false },
    { value: 'gpt-5.4-mini', label: 'GPT-5.4 Mini', disabled: false },
  ],
};

describe('CodexView SessionControls', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
  });

  it('renders the seeded model control with a testid', () => {
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'codex', showSessionControls: true, controls: [MODEL_CONTROL] })}
      />,
    );
    expect(screen.getByTestId('control-model')).toBeTruthy();
  });

  it('selecting a model POSTs { id, value } to /control', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'codex', showSessionControls: true, controls: [MODEL_CONTROL] })}
      />,
    );
    const combo = document.querySelector('[data-testid="control-model"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('GPT-5.4 Mini')).toBeTruthy());
    await act(async () => {
      fireEvent.click(screen.getByText('GPT-5.4 Mini'));
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const controlCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).endsWith('/control'));
    expect(controlCall).toBeTruthy();
    expect(JSON.parse((controlCall[1] as RequestInit).body as string)).toEqual({
      id: 'model',
      value: 'gpt-5.4-mini',
    });
  });
});
