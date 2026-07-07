import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ConversationWidget } from '../ConversationWidget.js';

// CodexView parity (Stage 7): the model picker, file upload (System:
// reference), file download, and tool-verbosity all funnel through the shared
// ConversationView, driven by codex's app-server wire over the listener SSE.

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

function makeProps(widgetData: any = { protocol: 'codex' }) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/cx/p1/',
    prefix: 'cx',
    database: 'db',
  };
}

let seq = 0;
function fire(ev: unknown) {
  act(() => MockEventSource.last!.emit(ev, ++seq));
}

const cmdStarted = (command: string, cwd = '/w') => ({
  method: 'item/started',
  params: {
    threadId: 't1', turnId: 'turn-1', startedAtMs: 0,
    item: { type: 'commandExecution', id: 'i-cmd', command, cwd, status: 'inProgress' },
  },
});

describe('CodexView (Stages 6-7 parity)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource as any;
    seq = 0;
  });

  // NOTE: the model-picker POST is now the generic SessionControls path
  // (POST /control {id,value}); see codex-controls.test.tsx. The bespoke
  // /model surface was removed in the session-controls migration.

  it('upload attaches a System: reference to the next prompt', async () => {
    const fetchMock = vi.fn(async (...args: any[]) => {
      if (String(args[0]).includes('widget-upload')) {
        return new Response(JSON.stringify({ ok: true, files: [{ filename: 'note.txt', path: 'uploads/note.txt' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'codex', showFileUpload: true, maxUploadBytes: 1000, uploadUrl: '/api/widget-upload/db/gm/p1' })} />);

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

  it('an optio-file: link fetches /download and triggers a blob save', async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    const fetchMock = vi.fn(async () => new Response(bytes, { status: 200, headers: { 'content-type': 'text/markdown' } }));
    vi.stubGlobal('fetch', fetchMock as any);
    const createObjectURL = vi.fn(() => 'blob:x');
    const revokeObjectURL = vi.fn();
    (globalThis.URL as any).createObjectURL = createObjectURL;
    (globalThis.URL as any).revokeObjectURL = revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<ConversationWidget {...makeProps({ protocol: 'codex', fileDownload: true })} />);
    // codex answer carrying an optio-file: sentinel markdown link.
    fire({
      method: 'item/agentMessage/delta',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-msg', delta: 'Here: [report](optio-file:out/r.md)' },
    });
    fire({
      method: 'turn/completed',
      params: { threadId: 't1', turn: { id: 'turn-1', status: 'completed', items: [] } },
    });

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

  it('verbose tool verbosity renders the command item as a key-value table', () => {
    render(<ConversationWidget {...makeProps({ protocol: 'codex', toolVerbosity: 'verbose' })} />);
    fire(cmdStarted('ls -la', '/w'));
    expect(screen.getByText('command')).toBeTruthy();
    // codex names the tool row by the command itself (no separate ACP title),
    // so `ls -la` appears both as the row name and the KV `command` value.
    expect(screen.getAllByText('ls -la').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('cwd')).toBeTruthy();
  });

  it('a permission request renders a card and answering POSTs /permission', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock as any);
    render(<ConversationWidget {...makeProps({ protocol: 'codex' })} />);
    fire({
      id: 99, method: 'item/commandExecution/requestApproval',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-cmd', command: 'echo hi', cwd: '/w', reason: null, startedAtMs: 0 },
    });
    // ConversationView's permission card labels the accept button "Approve".
    const allow = await screen.findByRole('button', { name: /approve/i });
    await act(async () => {
      fireEvent.click(allow);
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const permCall = (fetchMock.mock.calls as any[]).find((c) => String(c[0]).endsWith('/permission'));
    expect(permCall).toBeTruthy();
    expect(JSON.parse((permCall[1] as RequestInit).body as string)).toEqual({ request_id: '99', behavior: 'allow' });
  });
});
