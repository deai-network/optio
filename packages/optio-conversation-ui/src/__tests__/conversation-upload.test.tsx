import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OpencodeView } from '../opencode/OpencodeView.js';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';
import { AntigravityView } from '../antigravity/AntigravityView.js';
import { KimiCodeView } from '../kimicode/KimiCodeView.js';
import { GrokView } from '../grok/GrokView.js';
import { CursorView } from '../cursor/CursorView.js';
import { CodexView } from '../codex/CodexView.js';

// Shared EventSource stub: every view opens one on mount for its event stream;
// the upload tests don't drive any events, so a no-op that records the last
// instance is enough (mirrors the model-widget harnesses).
class MockEventSource {
  static last: MockEventSource | null = null;
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  close() {}
}

beforeEach(() => {
  vi.restoreAllMocks();
  (globalThis as any).EventSource = MockEventSource as any;
  MockEventSource.last = null;
});

// A small in-memory PNG-ish file; the migrated views POST the raw bytes as
// multipart (no data-URL) — the FormData carries this File under `file`.
function makePng(name = 'pic.png'): File {
  return new File([new Uint8Array([1, 2, 3, 4])], name, { type: 'image/png' });
}

// Every migrated view resolves its upload route from widgetData.uploadUrl via
// resolveUploadUrl(); an already-absolute value resolves untouched, so the
// multipart POST lands on exactly this URL — NOT `${widgetProxyUrl}upload`.
const UPLOAD_URL = '/api/widget-upload/db/gm/p1';

const PROVIDERS = {
  providers: [{
    id: 'opencode', name: 'OpenCode Zen',
    models: {
      'big-pickle': { id: 'big-pickle', providerID: 'opencode', name: 'Big Pickle' },
    },
  }],
  default: { opencode: 'big-pickle' },
};

// Router that records the ordered calls. ANY multipart body is treated as the
// upload (detected by FormData, not by URL) so the test observes exactly which
// URL a view POSTs its files to; opencode's discovery GETs and every JSON POST
// (send / prompt_async) are recorded/answered ok.
function installFetch(opts: { calls: { url: string; body: any }[]; uploadPaths?: string[] }) {
  const fn = vi.fn(async (url: string, init?: any) => {
    if (init?.body instanceof FormData) {
      opts.calls.push({ url, body: init.body });
      const paths = opts.uploadPaths ?? ['uploads/pic.png'];
      return new Response(
        JSON.stringify({ ok: true, files: paths.map((p) => ({ filename: p.split('/').pop(), path: p })) }),
        { status: 200 },
      );
    }
    if (url.includes('/config/providers')) return new Response(JSON.stringify(PROVIDERS), { status: 200 });
    if (url.includes('/message')) return new Response('[]', { status: 200 });
    if (init?.method === 'POST') {
      opts.calls.push({ url, body: JSON.parse(init.body) });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

function makeProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  } as any;
}

// -------------------------------------------------------------------------
// showFileUpload gating (attach control visibility) — one native transport
// (claudecode) + the client-routed opencode transport.
// -------------------------------------------------------------------------

describe('attach control gating', () => {
  it('ClaudeCodeView hides the attach control when showFileUpload is absent/false', () => {
    installFetch({ calls: [] });
    render(<ClaudeCodeView {...makeProps({})} />);
    expect(screen.queryByTestId('attach-button')).toBeNull();
    expect(screen.queryByTestId('file-input')).toBeNull();
  });

  it('ClaudeCodeView shows the attach control when showFileUpload is true', () => {
    installFetch({ calls: [] });
    render(<ClaudeCodeView {...makeProps({ showFileUpload: true, uploadUrl: UPLOAD_URL })} />);
    expect(screen.getByTestId('attach-button')).toBeTruthy();
    expect(screen.getByTestId('file-input')).toBeTruthy();
  });

  it('OpencodeView hides the attach control when showFileUpload is absent/false', async () => {
    installFetch({ calls: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    expect(screen.queryByTestId('attach-button')).toBeNull();
    expect(screen.queryByTestId('file-input')).toBeNull();
  });

  it('OpencodeView shows the attach control when showFileUpload is true', async () => {
    installFetch({ calls: [] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showFileUpload: true, uploadUrl: UPLOAD_URL })} />);
    await waitFor(() => expect(screen.getByTestId('attach-button')).toBeTruthy());
    expect(screen.getByTestId('file-input')).toBeTruthy();
  });
});

// -------------------------------------------------------------------------
// The six `post('send', {text})` transports (claudecode + the ACP/app-server
// engines): all migrate to the shared generic route. Attaching a file POSTs
// multipart to the resolved uploadUrl, then sends a `send` body whose text is
// bundleUploadNotice(paths, prompt) — no inline data-URL file part anywhere.
// -------------------------------------------------------------------------

const SEND_VIEWS = [
  ['ClaudeCode', ClaudeCodeView],
  ['Antigravity', AntigravityView],
  ['KimiCode', KimiCodeView],
  ['Grok', GrokView],
  ['Cursor', CursorView],
  ['Codex', CodexView],
] as const;

describe.each(SEND_VIEWS)('%s upload via the generic route', (_name, View) => {
  it('POSTs multipart to the resolved uploadUrl then sends bundleUploadNotice text (no data-URL part)', async () => {
    const calls: { url: string; body: any }[] = [];
    installFetch({ calls, uploadPaths: ['uploads/pic.png'] });
    render(<View {...makeProps({ showFileUpload: true, uploadUrl: UPLOAD_URL })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makePng('pic.png')] } });
    await waitFor(() => expect(screen.getByTestId('attach-chips')).toBeTruthy());

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'review the screenshot' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/send'))).toBe(true));

    // The multipart upload lands on the resolved generic route, before /send.
    const uploadIdx = calls.findIndex((c) => c.url === UPLOAD_URL);
    const sendIdx = calls.findIndex((c) => c.url.endsWith('/send'));
    expect(uploadIdx).toBeGreaterThanOrEqual(0);
    expect(sendIdx).toBeGreaterThan(uploadIdx);

    const uploadBody = calls[uploadIdx].body as FormData;
    expect(uploadBody).toBeInstanceOf(FormData);
    expect(uploadBody.getAll('file').length).toBe(1);

    // The send text bundles a System: line ahead of the operator prompt.
    const sendBody = calls[sendIdx].body;
    expect(sendBody.text).toContain('System: upload received, stored in uploads/pic.png');
    expect(sendBody.text).toContain('review the screenshot');
    expect(sendBody.text.indexOf('System:')).toBeLessThan(sendBody.text.indexOf('review the screenshot'));
    // No inline data-URL file part rides the send.
    expect(JSON.stringify(sendBody)).not.toContain('data:image');
  });
});

// -------------------------------------------------------------------------
// OpencodeView: the client-routed transport. Migrates OFF inline data-URL
// file parts to the same generic route — the prompt_async carries a single
// text part with the System notice, no `type: 'file'` part.
// -------------------------------------------------------------------------

describe('OpencodeView upload via the generic route', () => {
  it('POSTs multipart to the resolved uploadUrl then sends a text-only prompt with the System notice', async () => {
    const calls: { url: string; body: any }[] = [];
    installFetch({ calls, uploadPaths: ['uploads/pic.png'] });
    render(<OpencodeView {...makeProps({ sessionID: 'fake-session-id', directory: '/wd', showFileUpload: true, uploadUrl: UPLOAD_URL })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makePng('pic.png')] } });
    await waitFor(() => expect(screen.getByTestId('attach-chips')).toBeTruthy());

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'look at this' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(calls.some((c) => c.url.includes('/prompt_async'))).toBe(true));

    const uploadIdx = calls.findIndex((c) => c.url === UPLOAD_URL);
    expect(uploadIdx).toBeGreaterThanOrEqual(0);
    expect((calls[uploadIdx].body as FormData).getAll('file').length).toBe(1);

    const sent = calls.find((c) => c.url.includes('/prompt_async'))!;
    // No inline file part — only a text part carrying the System notice + prompt.
    const fileParts = sent.body.parts.filter((p: any) => p.type === 'file');
    expect(fileParts.length).toBe(0);
    const textPart = sent.body.parts.find((p: any) => p.type === 'text');
    expect(textPart.text).toContain('System: upload received, stored in uploads/pic.png');
    expect(textPart.text).toContain('look at this');
    expect(JSON.stringify(sent.body)).not.toContain('data:image');
  });
});

// -------------------------------------------------------------------------
// Upload FAILURE: a per-file failure surfaces immediately as an error row
// (client-side, transient), and the typed body still sends.
// -------------------------------------------------------------------------

describe('a failed upload surfaces an error row', () => {
  it('ClaudeCodeView renders an error item naming the failed file and still sends the typed body', async () => {
    const calls: { url: string; body: any }[] = [];
    const fn = vi.fn(async (url: string, init?: any) => {
      if (init?.body instanceof FormData) return new Response('nope', { status: 500 });
      if (init?.method === 'POST') {
        calls.push({ url, body: JSON.parse(init.body) });
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    vi.stubGlobal('fetch', fn);

    render(<ClaudeCodeView {...makeProps({ showFileUpload: true, uploadUrl: UPLOAD_URL })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makePng('pic.png')] } });
    await waitFor(() => expect(screen.getByTestId('attach-chips')).toBeTruthy());

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'review this' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    // The failed upload is surfaced immediately as an error row naming the file.
    await waitFor(() => expect(screen.getByTestId('conversation-error-item')).toBeTruthy());
    expect(screen.getByTestId('conversation-error-item').textContent).toContain('pic.png');

    // The typed body still sends (a body is present), with no System upload notice.
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/send'))).toBe(true));
    const sent = calls.find((c) => c.url.endsWith('/send'))!;
    expect(sent.body.text).toBe('review this');
    expect(sent.body.text).not.toContain('System: upload received');
  });
});
