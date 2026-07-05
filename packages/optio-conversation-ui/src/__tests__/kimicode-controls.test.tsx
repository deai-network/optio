import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// KimiCodeView session-controls migration (Task 3 — the template engine): kimi
// is the only wrapper that surfaces more than the model knob. The bespoke model
// <Select> is now the id="model" entry of the engine-neutral controls bar,
// seeded from widgetData.controls (model select + thinking segmented off/on +
// mode select) and channelled back through /control (kimi switches INLINE over
// ACP — session/set_model for the model, session/set_config_option for
// thinking/mode). A live config_option_update is re-projected engine-side into
// a synthetic x-optio-control-update snapshot the reducer folds.

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
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  };
}

const MODEL_CONTROL = {
  id: 'model', kind: 'select', label: 'Model', category: 'model', value: 'kimi-k2',
  options: [
    { value: 'kimi-k2', label: 'Kimi K2' },
    { value: 'kimi-k2-thinking', label: 'Kimi K2 Thinking' },
  ],
};
const THINKING_CONTROL = {
  id: 'thinking', kind: 'segmented', label: 'Thinking', category: 'thought_level',
  value: 'off', levels: ['off', 'on'],
};
const MODE_CONTROL = {
  id: 'mode', kind: 'select', label: 'Mode', category: 'mode', value: 'default',
  options: [
    { value: 'default', label: 'Default' },
    { value: 'yolo', label: 'Yolo' },
  ],
};

function seededProps() {
  return makeProps({
    protocol: 'kimicode',
    showSessionControls: true,
    controls: [MODEL_CONTROL, THINKING_CONTROL, MODE_CONTROL],
  });
}

describe('KimiCodeView session controls', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
  });

  it('renders model + thinking + mode controls seeded from widgetData.controls', () => {
    render(<ConversationWidget {...seededProps()} />);
    expect(screen.getByTestId('control-model')).toBeTruthy();
    expect(screen.getByTestId('control-thinking')).toBeTruthy();
    expect(screen.getByTestId('control-mode')).toBeTruthy();
  });

  it('hides the controls bar when showSessionControls is false', () => {
    render(
      <ConversationWidget
        {...makeProps({ protocol: 'kimicode', showSessionControls: false, controls: [MODEL_CONTROL] })}
      />,
    );
    expect(screen.queryByTestId('control-model')).toBeNull();
  });

  it('selecting a model POSTs {id:"model", value} to /control', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...seededProps()} />);

    const combo = document.querySelector('[data-testid="control-model"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Kimi K2 Thinking')).toBeTruthy());
    fireEvent.click(screen.getByText('Kimi K2 Thinking'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const controlCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).endsWith('/control'));
    expect(controlCall).toBeTruthy();
    expect(JSON.parse((controlCall[1] as RequestInit).body as string)).toEqual({
      id: 'model', value: 'kimi-k2-thinking',
    });
  });

  it('toggling the thinking segmented POSTs {id:"thinking", value:"on"}', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...seededProps()} />);

    // antd Segmented renders each level as a clickable label (capitalized).
    fireEvent.click(screen.getByText('On'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const controlCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).endsWith('/control'));
    expect(controlCall).toBeTruthy();
    expect(JSON.parse((controlCall[1] as RequestInit).body as string)).toEqual({
      id: 'thinking', value: 'on',
    });
  });

  it('selecting a mode POSTs {id:"mode", value} to /control', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConversationWidget {...seededProps()} />);

    const combo = document.querySelector('[data-testid="control-mode"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Yolo')).toBeTruthy());
    fireEvent.click(screen.getByText('Yolo'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const controlCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).endsWith('/control'));
    expect(controlCall).toBeTruthy();
    expect(JSON.parse((controlCall[1] as RequestInit).body as string)).toEqual({
      id: 'mode', value: 'yolo',
    });
  });

  it('folds a live config_option_update snapshot (x-optio-control-update) into the bar', async () => {
    render(<ConversationWidget {...seededProps()} />);
    // Engine re-projects config_option_update into a full controls snapshot.
    act(() =>
      MockEventSource.last!.emit(
        {
          type: 'x-optio-control-update',
          controls: [
            { ...MODEL_CONTROL, value: 'kimi-k2-thinking' },
            { ...THINKING_CONTROL, value: 'on' },
            MODE_CONTROL,
          ],
        },
        1,
      ),
    );
    // The thinking segmented now reflects 'on' as the selected level
    // (rendered capitalized).
    await waitFor(() => {
      const seg = screen.getByTestId('control-thinking');
      const selected = seg.querySelector('.ant-segmented-item-selected');
      expect(selected?.textContent).toBe('On');
    });
  });
});
