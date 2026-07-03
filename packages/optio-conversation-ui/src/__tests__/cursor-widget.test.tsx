import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// CursorView parity (Stage 7): file upload (System: reference) funnels through
// the shared ConversationView, driven by cursor's ACP wire over the listener
// SSE — the same mechanism GrokView uses (headless cursor has no inline
// ingest, so uploads land in the workdir and the next prompt references them).

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

function makeProps(widgetData: any = { protocol: 'cursor' }) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  };
}

describe('CursorView (Stage 7 parity)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
  });

  it('model selector POSTs the chosen model to /model', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <ConversationWidget
        {...makeProps({
          protocol: 'cursor',
          showModelSelector: true,
          currentModel: 'gpt-5',
          models: [
            { id: 'gpt-5', label: 'GPT-5' },
            { id: 'sonnet-4-thinking', label: 'Sonnet 4 (thinking)' },
          ],
        })}
      />,
    );
    // antd Select: open the dropdown, then pick the second option.
    const combo = document.querySelector('[data-testid="model-select"] .ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(combo);
    await waitFor(() => expect(screen.getByText('Sonnet 4 (thinking)')).toBeTruthy());
    fireEvent.click(screen.getByText('Sonnet 4 (thinking)'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const calls = fetchMock.mock.calls as any[];
    const modelCall = calls.find((c) => String(c[0]).endsWith('/model'));
    expect(modelCall).toBeTruthy();
    expect(JSON.parse((modelCall[1] as RequestInit).body as string)).toEqual({ model: 'sonnet-4-thinking' });
  });

  it('hides the attach control when showFileUpload is absent/false', () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'cursor' })} />);
    expect(screen.queryByTestId('attach-button')).toBeNull();
    expect(screen.queryByTestId('file-input')).toBeNull();
  });

  it('shows the attach control when showFileUpload is true', () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'cursor', showFileUpload: true, maxUploadBytes: 1000 })} />);
    expect(screen.getByTestId('attach-button')).toBeTruthy();
    expect(screen.getByTestId('file-input')).toBeTruthy();
  });

  it('upload POSTs /upload (FormData) then attaches a System: reference to the next prompt', async () => {
    const fetchMock = vi.fn(async (...args: any[]) => {
      if (String(args[0]).endsWith('/upload')) {
        return new Response(JSON.stringify({ ok: true, files: [{ filename: 'note.txt', path: 'uploads/note.txt' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'cursor', showFileUpload: true, maxUploadBytes: 1000 })} />);

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

    // /upload happened before /send, and carries a FormData under field "file".
    const uploadCall = calls.find((c) => String(c[0]).endsWith('/upload'));
    expect(uploadCall).toBeTruthy();
    const uploadBody = (uploadCall[1] as RequestInit).body as FormData;
    expect(uploadBody).toBeInstanceOf(FormData);
    expect(uploadBody.getAll('file').length).toBe(1);

    const sendCall = calls.find((c) => String(c[0]).endsWith('/send'));
    const sentText = JSON.parse((sendCall[1] as RequestInit).body as string).text as string;
    expect(sentText).toContain('uploads/note.txt');
    expect(sentText).toContain('summarize this');
    expect(sentText.indexOf('System:')).toBeLessThan(sentText.indexOf('summarize this'));
    // The optimistic echo shows the operator's text, not the System: preamble.
    expect(screen.getByText('summarize this')).toBeTruthy();
  });
});
