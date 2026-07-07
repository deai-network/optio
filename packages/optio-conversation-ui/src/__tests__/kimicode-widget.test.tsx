import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// KimiCodeView parity (Stage 7): the session-controls bar (model + thinking +
// mode, INLINE over ACP — no restart), file upload (System: reference), file
// download (optio-file: sentinel), and tool/thinking verbosity all funnel
// through the shared ConversationView, driven by kimi's ACP wire over the
// listener SSE. Mirrors grok-widget.test.tsx — only the protocol string
// ('kimicode') and the extra thinking/mode controls differ. The dedicated
// session-controls assertions live in kimicode-controls.test.tsx.

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

function makeProps(widgetData: any = { protocol: 'kimicode' }) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  };
}

let seq = 0;
function fire(ev: unknown) {
  act(() => MockEventSource.last!.emit(ev, ++seq));
}

const toolCall = (title: string, rawInput: unknown) => ({
  jsonrpc: '2.0',
  method: 'session/update',
  params: { sessionId: 's1', update: { sessionUpdate: 'tool_call', toolCallId: 'tc1', title, rawInput } },
});

describe('KimiCodeView (Stage 7 parity)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
    seq = 0;
  });

  it('routes to KimiCodeView for the kimicode protocol', () => {
    render(<ConversationWidget {...makeProps({ protocol: 'kimicode' })} />);
    // The kimi view logs its activation with the SSE url — proves KimiCodeView
    // (not another view) mounted for protocol 'kimicode'.
    expect(MockEventSource.last?.url).toBe('/api/widget/db/gm/p1/events');
  });

  it('model control POSTs the chosen model to /control (INLINE set_model)', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ConversationWidget
        {...makeProps({
          protocol: 'kimicode',
          showSessionControls: true,
          controls: [
            {
              id: 'model', kind: 'select', label: 'Model', category: 'model', value: 'kimi-k2',
              options: [
                { value: 'kimi-k2', label: 'Kimi K2' },
                { value: 'kimi-k2-thinking', label: 'Kimi K2 Thinking' },
              ],
            },
          ],
        })}
      />,
    );
    const combo = document.querySelector('[data-testid="control-model"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Kimi K2 Thinking')).toBeTruthy());
    fireEvent.click(screen.getByText('Kimi K2 Thinking'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const calls = fetchMock.mock.calls as any[];
    const controlCall = calls.find((c) => String(c[0]).endsWith('/control'));
    expect(controlCall).toBeTruthy();
    expect(JSON.parse((controlCall[1] as RequestInit).body as string)).toEqual({ id: 'model', value: 'kimi-k2-thinking' });
  });

  it('upload attaches a System: reference to the next prompt', async () => {
    const fetchMock = vi.fn(async (...args: any[]) => {
      if (String(args[0]).includes('widget-upload')) {
        return new Response(JSON.stringify({ ok: true, files: [{ filename: 'note.txt', path: 'uploads/note.txt' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'kimicode', showFileUpload: true, maxUploadBytes: 1000, uploadUrl: '/api/widget-upload/db/gm/p1' })} />);

    const fileInput = screen.getByTestId('file-input') as HTMLInputElement;
    const file = new File([new Uint8Array([104, 105])], 'note.txt', { type: 'text/plain' });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'summarize this' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('conversation-send'));
    });

    const calls = fetchMock.mock.calls as any[];
    await waitFor(() => expect(calls.some((c) => String(c[0]).endsWith('/send'))).toBe(true));
    const sendCall = calls.find((c) => String(c[0]).endsWith('/send'));
    const sentText = JSON.parse((sendCall[1] as RequestInit).body as string).text as string;
    expect(sentText).toContain('uploads/note.txt');
    expect(sentText).toContain('summarize this');
    expect(screen.getByText('summarize this')).toBeTruthy();
  });

  it('an optio-file: link fetches /download and triggers a blob save', async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    const fetchMock = vi.fn(async () => new Response(bytes, { status: 200, headers: { 'content-type': 'text/markdown' } }));
    vi.stubGlobal('fetch', fetchMock as any);
    const createObjectURL = vi.fn(() => 'blob:x');
    const revokeObjectURL = vi.fn();
    (globalThis.URL as any).createObjectURL = createObjectURL;
    (globalThis.URL as any).revokeObjectURL = revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<ConversationWidget {...makeProps({ protocol: 'kimicode', fileDownload: true })} />);
    fire({
      jsonrpc: '2.0',
      method: 'session/update',
      params: { sessionId: 's1', update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Here: [report](optio-file:out/r.md)' } } },
    });
    fire({ jsonrpc: '2.0', id: 1, result: { stopReason: 'end_turn' } });

    const link = await screen.findByText(/report/);
    await act(async () => {
      fireEvent.click(link);
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const dlCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).includes('/download'));
    expect(dlCall).toBeTruthy();
    expect(String(dlCall[0])).toContain('path=out%2Fr.md');
    expect(clickSpy).toHaveBeenCalled();
  });

  it('verbose tool verbosity renders the ACP rawInput as a key-value table', () => {
    render(<ConversationWidget {...makeProps({ protocol: 'kimicode', toolVerbosity: 'verbose' })} />);
    fire(toolCall('Shell', { command: 'ls -la', cwd: '/w' }));
    expect(screen.getByText('command')).toBeTruthy();
    expect(screen.getByText('ls -la')).toBeTruthy();
    expect(screen.getByText('cwd')).toBeTruthy();
  });
});
