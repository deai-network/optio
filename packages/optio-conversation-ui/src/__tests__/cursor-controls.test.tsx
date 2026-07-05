import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// CursorView session-controls migration: the bespoke model <Select> is gone;
// cursor now seeds the engine-neutral id="model" control from widgetData.controls
// and channels a change back via POST /control ({id, value}). Plan-gated models
// (cursor greys them via the availability probe) arrive as disabled
// ControlOptions carrying whyDisabled, rendered as a disabled option + tooltip.

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

const CONTROLS = [
  {
    id: 'model',
    kind: 'select',
    label: 'Model',
    value: 'composer-1',
    category: 'model',
    options: [
      { value: 'composer-1', label: 'Composer 1', disabled: false },
      { value: 'opus-4.5', label: 'Opus 4.5', disabled: true, whyDisabled: 'Upgrade your plan to continue' },
    ],
  },
];

describe('CursorView session controls', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
  });

  it('renders the model control seeded from widgetData.controls', () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'cursor', showSessionControls: true, controls: CONTROLS })} />);
    expect(screen.getByTestId('control-model')).toBeTruthy();
  });

  it('surfaces a plan-gated model as a disabled option carrying whyDisabled as its title', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'cursor', showSessionControls: true, controls: CONTROLS })} />);

    const combo = document.querySelector('[data-testid="control-model"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Opus 4.5')).toBeTruthy());
    const opt = screen.getByText('Opus 4.5').closest('.ant-select-item');
    expect(opt?.getAttribute('title')).toBe('Upgrade your plan to continue');
    expect(opt?.className).toContain('ant-select-item-option-disabled');
  });

  it('does not render controls when showSessionControls is false', () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'cursor', showSessionControls: false, controls: CONTROLS })} />);
    expect(screen.queryByTestId('control-model')).toBeNull();
  });
});
