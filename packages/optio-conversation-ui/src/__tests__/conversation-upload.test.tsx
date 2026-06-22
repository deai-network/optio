import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OpencodeView } from '../opencode/OpencodeView.js';
import { ClaudeCodeView } from '../claudecode/ClaudeCodeView.js';

// Shared EventSource stub: both views open one on mount for their event
// stream; the upload tests don't drive any events, so a no-op that records
// the last instance is enough (mirrors the model-widget harnesses).
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

// A small in-memory PNG-ish file; jsdom's FileReader.readAsDataURL turns it
// into a "data:<mime>;base64,<…>" URL, which is exactly what the opencode
// inline file part carries.
function makePng(name = 'pic.png'): File {
  return new File([new Uint8Array([1, 2, 3, 4])], name, { type: 'image/png' });
}

// -------------------------------------------------------------------------
// OpencodeView: inline data-URL file part on prompt_async
// -------------------------------------------------------------------------

const PROVIDERS = {
  providers: [{
    id: 'opencode', name: 'OpenCode Zen',
    models: {
      'big-pickle': { id: 'big-pickle', providerID: 'opencode', name: 'Big Pickle' },
    },
  }],
  default: { opencode: 'big-pickle' },
};

function installOpencodeFetch(opts: { posts: { url: string; body: any }[] }) {
  const fn = vi.fn(async (url: string, init?: any) => {
    if (init?.method === 'POST') {
      opts.posts.push({ url, body: JSON.parse(init.body) });
      return { ok: true, json: async () => ({}) } as any;
    }
    if (url.includes('/config/providers')) return { ok: true, json: async () => PROVIDERS } as any;
    if (url.includes('/message')) return { ok: true, json: async () => [] } as any;
    return { ok: true, json: async () => ({}) } as any;
  });
  (globalThis as any).fetch = fn;
  return fn;
}

function makeOpencodeProps(widgetData: any) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    widgetProxyUrl: '/api/widget/db/gm/p1/',
  } as any;
}

describe('OpencodeView file attach', () => {
  it('hides the attach control when showFileUpload is absent/false', async () => {
    installOpencodeFetch({ posts: [] });
    render(<OpencodeView {...makeOpencodeProps({ sessionID: 'fake-session-id', directory: '/wd' })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());
    expect(screen.queryByTestId('attach-button')).toBeNull();
    expect(screen.queryByTestId('file-input')).toBeNull();
  });

  it('shows the attach control when showFileUpload is true', async () => {
    installOpencodeFetch({ posts: [] });
    render(<OpencodeView {...makeOpencodeProps({ sessionID: 'fake-session-id', directory: '/wd', showFileUpload: true })} />);
    await waitFor(() => expect(screen.getByTestId('attach-button')).toBeTruthy());
    expect(screen.getByTestId('file-input')).toBeTruthy();
  });

  it('picking a file then sending includes a data-URL file part in prompt_async', async () => {
    const posts: { url: string; body: any }[] = [];
    installOpencodeFetch({ posts });
    render(<OpencodeView {...makeOpencodeProps({ sessionID: 'fake-session-id', directory: '/wd', showFileUpload: true })} />);
    await waitFor(() => expect(screen.getByTestId('conversation-input-box')).toBeTruthy());

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makePng('pic.png')] } });
    await waitFor(() => expect(screen.getByTestId('attach-chips')).toBeTruthy());

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'look at this' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(posts.some((p) => p.url.includes('/prompt_async'))).toBe(true));
    const sent = posts.find((p) => p.url.includes('/prompt_async'))!;
    const filePart = sent.body.parts.find((p: any) => p.type === 'file');
    expect(filePart).toBeTruthy();
    expect(filePart.mime).toBe('image/png');
    expect(filePart.filename).toBe('pic.png');
    expect(String(filePart.url)).toMatch(/^data:image\/png;base64,/);
    // The text prompt still rides last.
    expect(sent.body.parts[sent.body.parts.length - 1]).toEqual({ type: 'text', text: 'look at this' });
  });
});

// -------------------------------------------------------------------------
// ClaudeCodeView: POST /upload (multipart) then /send with System: preamble
// -------------------------------------------------------------------------

function makeClaudeProps(widgetData: any = {}) {
  return {
    process: { _id: 'p1', name: 'n', widgetData, status: { state: 'running' } },
    apiBaseUrl: '',
    widgetProxyUrl: '/api/widget/db/gm/p1/',
    prefix: 'gm',
    database: 'db',
  } as any;
}

// Router that records the ordered calls, answers /upload with a stored path
// and /send with ok. Upload bodies are FormData; send bodies are JSON.
function installClaudeFetch(opts: { calls: { url: string; body: any }[]; uploadPaths?: string[] }) {
  const fn = vi.fn(async (url: string, init?: any) => {
    if (url.endsWith('/upload')) {
      opts.calls.push({ url, body: init?.body });
      const paths = opts.uploadPaths ?? ['uploads/pic.png'];
      return new Response(
        JSON.stringify({ ok: true, files: paths.map((p) => ({ filename: p.split('/').pop(), path: p })) }),
        { status: 200 },
      );
    }
    if (url.endsWith('/send')) {
      opts.calls.push({ url, body: JSON.parse(init.body) });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('ClaudeCodeView file attach', () => {
  it('hides the attach control when showFileUpload is absent/false', () => {
    render(<ClaudeCodeView {...makeClaudeProps({})} />);
    expect(screen.queryByTestId('attach-button')).toBeNull();
    expect(screen.queryByTestId('file-input')).toBeNull();
  });

  it('shows the attach control when showFileUpload is true', () => {
    render(<ClaudeCodeView {...makeClaudeProps({ showFileUpload: true })} />);
    expect(screen.getByTestId('attach-button')).toBeTruthy();
    expect(screen.getByTestId('file-input')).toBeTruthy();
  });

  it('picking a file then sending POSTs /upload (FormData) then /send with a System: preamble', async () => {
    const calls: { url: string; body: any }[] = [];
    installClaudeFetch({ calls, uploadPaths: ['uploads/pic.png'] });
    render(<ClaudeCodeView {...makeClaudeProps({ showFileUpload: true })} />);

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makePng('pic.png')] } });
    await waitFor(() => expect(screen.getByTestId('attach-chips')).toBeTruthy());

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'review the screenshot' } });
    fireEvent.click(screen.getByTestId('conversation-send'));

    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/send'))).toBe(true));

    // /upload happened before /send.
    const uploadIdx = calls.findIndex((c) => c.url.endsWith('/upload'));
    const sendIdx = calls.findIndex((c) => c.url.endsWith('/send'));
    expect(uploadIdx).toBeGreaterThanOrEqual(0);
    expect(sendIdx).toBeGreaterThan(uploadIdx);

    // The upload body is a FormData carrying the file under field name "file".
    const uploadBody = calls[uploadIdx].body as FormData;
    expect(uploadBody).toBeInstanceOf(FormData);
    expect(uploadBody.getAll('file').length).toBe(1);

    // The send body bundles a System: upload line ahead of the operator prompt.
    const sendBody = calls[sendIdx].body;
    expect(sendBody.text).toContain('System: upload received, stored in uploads/pic.png');
    expect(sendBody.text).toContain('review the screenshot');
    expect(sendBody.text.indexOf('System:')).toBeLessThan(sendBody.text.indexOf('review the screenshot'));
  });
});
