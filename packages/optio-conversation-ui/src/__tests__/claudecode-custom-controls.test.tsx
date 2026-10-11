import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';
import type { CustomControl, SessionControl } from '../chat.js';

// EventSource stub that can deliver the listener's SSE frames.
class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  close() {}
  emit(ev: unknown, seq: number) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(ev), lastEventId: String(seq) }));
  }
}

const SPEED: CustomControl = {
  id: 'speed',
  label: { en: 'Model', hu: 'Modell', de: 'Modell' },
  description: null,
  options: [
    { value: 'fast', label: 'Fast', description: null },
    { value: 'extensive', label: 'Extensive', description: null },
  ],
  value: 'extensive',
};

const CONTROLS: SessionControl[] = [
  { id: 'model', kind: 'select', label: 'Model', value: 'opus',
    options: [{ value: 'opus', label: 'Opus' }, { value: 'sonnet', label: 'Sonnet' }] },
];

function makeProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  } as any;
}

/** The label of the custom control's highlighted segment, or null when none is. */
function selected(id: string): string | null {
  const radios = Array.from(screen.getByTestId(`custom-control-${id}`).querySelectorAll<HTMLInputElement>('input[type=radio]'));
  const on = radios.find((r) => r.checked);
  return on ? on.closest('label')!.textContent : null;
}

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

let seq = 0;
function fire(ev: unknown) {
  act(() => MockEventSource.last!.emit(ev, ++seq));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
  seq = 0;
});

describe('ClaudeCodeView custom controls', () => {
  it('shown from widgetData.customControls without showSessionControls; the built-ins stay hidden', () => {
    render(<ClaudeCodeView {...makeProps({ controls: CONTROLS, customControls: [SPEED] })} />);
    expect(screen.getByTestId('custom-control-speed')).toBeTruthy();
    expect(selected('speed')).toBe('Extensive');
    expect(screen.queryByTestId('control-model')).toBeNull();
  });

  it('shown next to the built-ins when both are on', () => {
    render(<ClaudeCodeView {...makeProps({ showSessionControls: true, controls: CONTROLS, customControls: [SPEED] })} />);
    expect(screen.getByTestId('custom-control-speed')).toBeTruthy();
    expect(screen.getByTestId('control-model')).toBeTruthy();
  });

  it('none without widgetData.customControls', () => {
    render(<ClaudeCodeView {...makeProps({})} />);
    expect(screen.queryByTestId('custom-control-speed')).toBeNull();
  });

  it('picking posts {id: "custom:<id>", value} to /control and shows the pick at once', async () => {
    let answer!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);

    fireEvent.click(screen.getByText('Fast'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/widget/db/gm/p1/control');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ id: 'custom:speed', value: 'fast' });
    // Optimistic: highlighted before the listener answers.
    expect(selected('speed')).toBe('Fast');

    await act(async () => answer(reply(200, { ok: true })));
    expect(selected('speed')).toBe('Fast');
    expect(screen.queryByTestId('conversation-error')).toBeNull();
  });

  it('a pick refused because the conversation closed (no follow-up update) is dropped, with the reason shown', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(409, { reason: 'closed' })));
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent).toContain('the conversation has ended'));
    expect(selected('speed')).toBe('Extensive');
  });

  it("a rejected pick shows claude's message and keeps the running value", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(409, { reason: 'rejected', error: 'Model sonnet is not available' })));
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent).toContain('Model sonnet is not available'));
    expect(selected('speed')).toBe('Extensive');
  });

  it('a pick refused while busy says so and keeps the running value', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(409, { reason: 'busy' })));
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent).toContain('busy'));
    expect(selected('speed')).toBe('Extensive');
  });

  it('a pick that never reaches the listener is dropped, with an error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network down'); }));
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(screen.getByTestId('conversation-error')).toBeTruthy());
    expect(selected('speed')).toBe('Extensive');
  });

  it("a pick whose answer is lost after the engine applied it keeps the engine's value", async () => {
    // The engine's update for the pick came before the failure: putting back
    // the value shown at click time would overwrite the running one.
    let fail!: () => void;
    const fetchMock = vi.fn(() => new Promise<Response>((_, reject) => {
      fail = () => reject(new TypeError('network down'));
    }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    fire({ type: 'x-optio-control-update', controls: CONTROLS, customControls: [{ ...SPEED, value: 'fast' }] });
    await act(async () => fail());
    await waitFor(() => expect(screen.getByTestId('conversation-error')).toBeTruthy());
    expect(selected('speed')).toBe('Fast');
  });

  it('a refused pick keeps the values the engine re-sent meanwhile, not the one shown at click time', async () => {
    // The listener re-sends the running values before it answers busy; here
    // they match no option (the built-ins were changed meanwhile).
    let answer!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    fire({ type: 'x-optio-control-update', controls: CONTROLS, customControls: [{ ...SPEED, value: null }] });
    await act(async () => answer(reply(409, { ok: false, reason: 'busy' })));
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent).toContain('busy'));
    expect(selected('speed')).toBeNull();
  });

  it('a failed pick with no update since goes back to the value the engine sent last', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(409, { ok: false, reason: 'closed' })));
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fire({ type: 'x-optio-control-update', controls: CONTROLS, customControls: [{ ...SPEED, value: 'fast' }] });
    fireEvent.click(screen.getByText('Extensive'));
    await waitFor(() => expect(screen.getByTestId('conversation-error')).toBeTruthy());
    expect(selected('speed')).toBe('Fast');
  });

  it.each(['bad-id', 'bad-value'])('a %s refusal says the option is not available, not retry', async (reason) => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(400, { ok: false, reason })));
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent)
      .toBe('Switch failed: this option is not available.'));
    expect(selected('speed')).toBe('Extensive');
  });

  it('control updates fold customControls; an update without them keeps them', () => {
    render(<ClaudeCodeView {...makeProps({ customControls: [SPEED] })} />);
    fire({ type: 'x-optio-control-update', controls: CONTROLS, customControls: [{ ...SPEED, value: null }] });
    expect(selected('speed')).toBeNull();
    fire({ type: 'x-optio-control-update', controls: CONTROLS, customControls: [{ ...SPEED, value: 'fast' }] });
    expect(selected('speed')).toBe('Fast');
    fire({ type: 'x-optio-control-update', controls: CONTROLS });
    expect(selected('speed')).toBe('Fast');
  });
});
