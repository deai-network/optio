import { describe, it, expect, vi, beforeEach } from 'vitest';
import { uploadFiles, bundleUploadNotice, resolveUploadUrl } from '../uploads.js';
import type { Attachment } from '../attachments.js';

function makeAttachment(name: string, bytes = 4, mime = 'image/png'): Attachment {
  const file = new File([new Uint8Array(bytes)], name, { type: mime });
  return { file, mime, filename: name };
}

describe('uploadFiles', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('POSTs a multipart FormData with each file under the "file" field and returns the stored relpaths', async () => {
    const calls: { url: string; body: any }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: any) => {
      calls.push({ url, body: init?.body });
      return new Response(
        JSON.stringify({ ok: true, files: [{ filename: 'pic.png', path: 'uploads/pic.png' }] }),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const paths = await uploadFiles('/api/widget-upload/db/gm/p1', [makeAttachment('pic.png')], 10_000_000);

    expect(paths).toEqual(['uploads/pic.png']);
    expect(calls.length).toBe(1);
    expect(calls[0].url).toBe('/api/widget-upload/db/gm/p1');
    const fd = calls[0].body as FormData;
    expect(fd).toBeInstanceOf(FormData);
    expect(fd.getAll('file').length).toBe(1);
  });

  it('appends every attachment under the "file" field for a multi-file upload', async () => {
    const calls: { url: string; body: any }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: any) => {
      calls.push({ url, body: init?.body });
      return new Response(
        JSON.stringify({
          ok: true,
          files: [
            { filename: 'a.txt', path: 'uploads/a.txt' },
            { filename: 'b.txt', path: 'uploads/b.txt' },
          ],
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const paths = await uploadFiles(
      '/u',
      [makeAttachment('a.txt', 4, 'text/plain'), makeAttachment('b.txt', 4, 'text/plain')],
      10_000_000,
    );

    expect(paths).toEqual(['uploads/a.txt', 'uploads/b.txt']);
    const fd = calls[0].body as FormData;
    expect(fd.getAll('file').length).toBe(2);
  });

  it('returns null without POSTing when an attachment exceeds maxBytes', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const paths = await uploadFiles('/u', [makeAttachment('big.png', 20)], 10);

    expect(paths).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('returns null when the server responds non-ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 })),
    );
    const paths = await uploadFiles('/u', [makeAttachment('pic.png')], 10_000_000);
    expect(paths).toBeNull();
  });

  it('returns null when fetch throws', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }),
    );
    const paths = await uploadFiles('/u', [makeAttachment('pic.png')], 10_000_000);
    expect(paths).toBeNull();
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
