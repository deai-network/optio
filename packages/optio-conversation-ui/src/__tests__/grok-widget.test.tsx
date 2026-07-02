import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// GrokView parity (Stage 7): the model picker, file upload (System: reference),
// file download, and tool-verbosity all funnel through the shared
// ConversationView, driven by grok's ACP wire over the listener SSE.

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

function makeProps(widgetData: any = { protocol: 'grok' }) {
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

describe('GrokView (Stage 7 parity)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
    seq = 0;
  });

  it('model selector POSTs the chosen model to /model', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ConversationWidget
        {...makeProps({
          protocol: 'grok',
          showModelSelector: true,
          currentModel: 'grok-composer-2.5-fast',
          models: [
            { id: 'grok-composer-2.5-fast', label: 'Composer 2.5' },
            { id: 'grok-build', label: 'Grok Build' },
          ],
        })}
      />,
    );
    // antd Select: open the dropdown, then pick the second option.
    const combo = document.querySelector('[data-testid="model-select"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Grok Build')).toBeTruthy());
    fireEvent.click(screen.getByText('Grok Build'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const calls = fetchMock.mock.calls as any[];
    const modelCall = calls.find((c) => String(c[0]).endsWith('/model'));
    expect(modelCall).toBeTruthy();
    expect(JSON.parse((modelCall[1] as RequestInit).body as string)).toEqual({ model: 'grok-build' });
  });

  it('upload attaches a System: reference to the next prompt', async () => {
    const fetchMock = vi.fn(async (...args: any[]) => {
      if (String(args[0]).endsWith('/upload')) {
        return new Response(JSON.stringify({ ok: true, files: [{ filename: 'note.txt', path: 'uploads/note.txt' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'grok', showFileUpload: true, maxUploadBytes: 1000 })} />);

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
    // The optimistic echo shows the operator's text, not the System: preamble.
    expect(screen.getByText('summarize this')).toBeTruthy();
  });

  it('verbose tool verbosity renders the ACP rawInput as a key-value table', () => {
    render(<ConversationWidget {...makeProps({ protocol: 'grok', toolVerbosity: 'verbose' })} />);
    fire(toolCall('Shell', { command: 'ls -la', cwd: '/w' }));
    expect(screen.getByText('command')).toBeTruthy();
    expect(screen.getByText('ls -la')).toBeTruthy();
    expect(screen.getByText('cwd')).toBeTruthy();
  });
});
