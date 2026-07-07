import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  uploadFiles,
  bundleUploadNotice,
  resolveUploadUrl,
  parseUploadNotice,
  uploadNoticeActivityText,
} from '../uploads.js';
import type { Attachment } from '../attachments.js';

function makeAttachment(name: string, bytes = 4, mime = 'image/png'): Attachment {
  const file = new File([new Uint8Array(bytes)], name, { type: mime });
  return { file, mime, filename: name };
}

describe('uploadFiles', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('POSTs one multipart FormData per file and returns the stored relpaths as ok', async () => {
    const calls: { url: string; body: any }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: any) => {
      calls.push({ url, body: init?.body });
      return new Response(
        JSON.stringify({ ok: true, files: [{ filename: 'pic.png', path: 'uploads/pic.png' }] }),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const out = await uploadFiles('/api/widget-upload/db/gm/p1', [makeAttachment('pic.png')], 10_000_000);

    expect(out).toEqual({ ok: ['uploads/pic.png'], failed: [] });
    expect(calls.length).toBe(1);
    expect(calls[0].url).toBe('/api/widget-upload/db/gm/p1');
    const fd = calls[0].body as FormData;
    expect(fd).toBeInstanceOf(FormData);
    expect(fd.getAll('file').length).toBe(1);
  });

  it('uploads each file independently (one POST each) for a multi-file batch', async () => {
    const calls: { url: string; body: any }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: any) => {
      calls.push({ url, body: init?.body });
      const fd = init?.body as FormData;
      const name = (fd.getAll('file')[0] as File).name;
      return new Response(
        JSON.stringify({ ok: true, files: [{ filename: name, path: `uploads/${name}` }] }),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const out = await uploadFiles(
      '/u',
      [makeAttachment('a.txt', 4, 'text/plain'), makeAttachment('b.txt', 4, 'text/plain')],
      10_000_000,
    );

    expect(out.ok).toEqual(['uploads/a.txt', 'uploads/b.txt']);
    expect(out.failed).toEqual([]);
    expect(calls.length).toBe(2);
    expect((calls[0].body as FormData).getAll('file').length).toBe(1);
    expect((calls[1].body as FormData).getAll('file').length).toBe(1);
  });

  it('reports one ok, one oversize, and one network-failed file per-file (never throws, others still stored)', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: any) => {
      const name = (init?.body as FormData).getAll('file')[0] as File;
      if (name.name === 'boom.png') throw new Error('network down');
      return new Response(
        JSON.stringify({ ok: true, files: [{ filename: 'ok.png', path: 'uploads/ok.png' }] }),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const out = await uploadFiles(
      '/u',
      [makeAttachment('ok.png'), makeAttachment('huge.png', 40), makeAttachment('boom.png')],
      10,
    );

    expect(out.ok).toEqual(['uploads/ok.png']);
    expect(out.failed.map((f) => f.name)).toEqual(['huge.png', 'boom.png']);
    expect(out.failed[0].error).toMatch(/size/i);
    expect(out.failed[1].error).toMatch(/network/i);
  });

  it('reports a failed entry (never throws) when the server responds non-ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 })),
    );
    const out = await uploadFiles('/u', [makeAttachment('pic.png')], 10_000_000);
    expect(out.ok).toEqual([]);
    expect(out.failed).toEqual([{ name: 'pic.png', error: 'upload failed (500)' }]);
  });
});

describe('parseUploadNotice', () => {
  it('passes text through unchanged when there is no upload notice', () => {
    expect(parseUploadNotice('just a prompt')).toEqual({ text: 'just a prompt', uploads: [] });
  });

  it('strips a single notice line and returns the captured path + clean body', () => {
    const raw = 'System: upload received, stored in uploads/a.md\n\nplease review';
    expect(parseUploadNotice(raw)).toEqual({ text: 'please review', uploads: ['uploads/a.md'] });
  });

  it('strips N notice lines and returns every captured path + clean body', () => {
    const raw =
      'System: upload received, stored in uploads/a.md\n' +
      'System: upload received, stored in uploads/b.png\n\n' +
      'look at both';
    expect(parseUploadNotice(raw)).toEqual({
      text: 'look at both',
      uploads: ['uploads/a.md', 'uploads/b.png'],
    });
  });

  it('leaves an unrelated System: harness message untouched (only upload lines are stripped)', () => {
    const raw = 'System: session resumed';
    expect(parseUploadNotice(raw)).toEqual({ text: 'System: session resumed', uploads: [] });
  });
});

describe('uploadNoticeActivityText', () => {
  it('lists the basenames for up to three files', () => {
    expect(uploadNoticeActivityText(['uploads/a.md', 'uploads/b.png'])).toBe('📎 Attached: a.md, b.png');
  });

  it('summarises with a count above three files', () => {
    const label = uploadNoticeActivityText(['uploads/a', 'uploads/b', 'uploads/c', 'uploads/d']);
    expect(label).toBe('📎 Attached 4 files: a, b, c, …');
  });
});

describe('bundleUploadNotice', () => {
  it('prefixes one System: line per stored path ahead of the body', () => {
    const out = bundleUploadNotice(['uploads/a.txt', 'uploads/b.txt'], 'please review');
    expect(out).toBe(
      'System: upload received, stored in uploads/a.txt\n' +
        'System: upload received, stored in uploads/b.txt\n\n' +
        'please review',
    );
    expect(out.indexOf('System:')).toBeLessThan(out.indexOf('please review'));
  });

  it('returns the body unchanged when there are no paths', () => {
    expect(bundleUploadNotice([], 'hello')).toBe('hello');
  });
});

describe('resolveUploadUrl', () => {
  it('expands the {widgetProxyUrl} token in widgetData.uploadUrl', () => {
    const out = resolveUploadUrl(
      { uploadUrl: '{widgetProxyUrl}../../../api/widget-upload/db/gm/p1' },
      '/api/widget/db/gm/p1/',
    );
    expect(out).toBe('/api/widget/db/gm/p1/../../../api/widget-upload/db/gm/p1');
  });

  it('returns an already-absolute uploadUrl untouched', () => {
    const out = resolveUploadUrl({ uploadUrl: '/api/widget-upload/db/gm/p1' }, '/api/widget/db/gm/p1/');
    expect(out).toBe('/api/widget-upload/db/gm/p1');
  });

  it('returns null when uploadUrl is missing or not a string', () => {
    expect(resolveUploadUrl({}, '/proxy/')).toBeNull();
    expect(resolveUploadUrl(undefined, '/proxy/')).toBeNull();
    expect(resolveUploadUrl(null, '/proxy/')).toBeNull();
    expect(resolveUploadUrl({ uploadUrl: 123 }, '/proxy/')).toBeNull();
    expect(resolveUploadUrl({ uploadUrl: '' }, '/proxy/')).toBeNull();
  });
});
