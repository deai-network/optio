/**
 * Shared client-side upload helpers for every conversation view.
 *
 * All engines now materialize uploads through one generic optio-api route
 * (`POST /api/widget-upload/<db>/<prefix>/<pid>`) that streams the bytes into
 * GridFS and calls the `materializeUpload` clamator RPC, which writes the file
 * into the task's `<workdir>/uploads/<name>`. The view POSTs the picked files
 * here, gets back the stored workdir-relative paths, and bundles one
 * `System:` notice line per file into the prompt so the agent can Read them.
 *
 * Lifted from the per-engine ClaudeCodeView so all 7 views share one copy —
 * no inline data-URL file parts anywhere.
 */
import { type Attachment } from './attachments.js';

/** Outcome of a multi-file upload: the stored relpaths that succeeded, plus a
 *  per-file reason for each one that did not. Each file uploads independently,
 *  so a single oversize / failing attachment never sinks the others. */
export interface UploadOutcome {
  ok: string[];
  failed: { name: string; error: string }[];
}

/**
 * Multipart-POST each attachment to the generic upload route INDEPENDENTLY and
 * report a per-file outcome. Oversize files and fetch/HTTP failures land as
 * `failed` entries (short human reason) instead of throwing or aborting the
 * whole batch, so the caller can send the ones that stored and surface the rest
 * as error rows.
 *
 * @param uploadUrl  the resolved `/api/widget-upload/<db>/<prefix>/<pid>` URL
 *                   (see {@link resolveUploadUrl}).
 * @param attachments  the picked files; each is POSTed alone under the `file` field.
 * @param maxBytes  per-file size cap; an attachment above it becomes a `failed` entry.
 */
export async function uploadFiles(
  uploadUrl: string,
  attachments: Attachment[],
  maxBytes: number,
): Promise<UploadOutcome> {
  const ok: string[] = [];
  const failed: { name: string; error: string }[] = [];
  for (const a of attachments) {
    if (a.file.size > maxBytes) {
      failed.push({ name: a.filename, error: 'exceeds the size limit' });
      continue;
    }
    const fd = new FormData();
    fd.append('file', a.file, a.filename);
    try {
      const resp = await fetch(uploadUrl, { method: 'POST', body: fd });
      if (!resp.ok) {
        failed.push({ name: a.filename, error: `upload failed (${resp.status})` });
        continue;
      }
      const j = await resp.json();
      const paths = (j.files ?? []).map((f: any) => String(f.path));
      if (paths.length === 0) failed.push({ name: a.filename, error: 'upload failed' });
      else ok.push(...paths);
    } catch {
      failed.push({ name: a.filename, error: 'network error' });
    }
  }
  return { ok, failed };
}

// The upload-notice line the view bundles ahead of the prompt (see
// bundleUploadNotice); the reducers parse it back out with parseUploadNotice.
const NOTICE_LINE = /^System: upload received, stored in ([^\n]*)\n/;

/**
 * Split the leading `System: upload received, stored in <path>` notice lines
 * (one per uploaded file, as {@link bundleUploadNotice} writes them) off the
 * front of a wire/history user message. Returns the captured `<path>`s AND the
 * clean remaining `text` (the operator's real prompt). No notice → passthrough.
 *
 * Reducers use `parseUploadNotice(raw).text` for the user bubble (unchanged
 * behaviour vs. the old per-engine stripUploadNotice) and `.uploads` to emit a
 * persistent muted "attached files" activity row.
 */
export function parseUploadNotice(text: string): { text: string; uploads: string[] } {
  const uploads: string[] = [];
  let rest = text;
  let m: RegExpMatchArray | null;
  while ((m = rest.match(NOTICE_LINE))) {
    uploads.push(m[1]);
    rest = rest.slice(m[0].length);
  }
  if (uploads.length > 0) rest = rest.replace(/^\n/, ''); // drop the blank separator line
  return { text: rest, uploads };
}

/**
 * A short, human muted-row label for the uploaded files, keyed off each stored
 * path's basename, e.g. `📎 Attached: a.md, b.png`. Above three files it
 * summarises with a count so the row stays short.
 */
export function uploadNoticeActivityText(uploads: string[]): string {
  const names = uploads.map((p) => p.split('/').pop() || p);
  if (names.length <= 3) return `📎 Attached: ${names.join(', ')}`;
  return `📎 Attached ${names.length} files: ${names.slice(0, 3).join(', ')}, …`;
}

/**
 * Prefix one `System: upload received, stored in <path>` line per stored file
 * ahead of the operator's prompt `body`, so the agent knows where the uploads
 * landed. Returns `body` unchanged when no files were uploaded.
 */
export function bundleUploadNotice(paths: string[], body: string): string {
  if (paths.length === 0) return body;
  const notice = paths.map((p) => `System: upload received, stored in ${p}`).join('\n');
  return `${notice}\n\n${body}`;
}

/**
 * Resolve the upload route URL a view POSTs to from its widgetData. Every
 * engine publishes the SAME `widgetData.uploadUrl` key — a `{widgetProxyUrl}`
 * token (the same substitution {@link IframeWidget} applies) that expands to the
 * API's `/api/widget-upload/<db>/<prefix>/<pid>` route. Resolving it here keeps
 * all 7 views on one code path. Returns `null` when the task did not advertise
 * an upload URL (upload disabled), so the view can hide/skip the upload step.
 */
export function resolveUploadUrl(widgetData: unknown, widgetProxyUrl: string): string | null {
  const raw = (widgetData as { uploadUrl?: unknown } | null | undefined)?.uploadUrl;
  if (typeof raw !== 'string' || !raw) return null;
  return raw.replace(/\{widgetProxyUrl\}/g, widgetProxyUrl);
}
