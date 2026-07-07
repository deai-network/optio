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
import { type Attachment, withinCap } from './attachments.js';

/**
 * Multipart-POST the attachments to the generic upload route and return the
 * stored `uploads/<name>` relpaths, or `null` on oversize / any failure so the
 * caller can surface a retry instead of sending a half-uploaded prompt.
 *
 * @param uploadUrl  the resolved `/api/widget-upload/<db>/<prefix>/<pid>` URL
 *                   (see {@link resolveUploadUrl}).
 * @param attachments  the picked files; each is appended under the `file` field.
 * @param maxBytes  per-file size cap; any attachment above it aborts the upload.
 */
export async function uploadFiles(
  uploadUrl: string,
  attachments: Attachment[],
  maxBytes: number,
): Promise<string[] | null> {
  if (!withinCap(attachments, maxBytes)) return null;
  const fd = new FormData();
  for (const a of attachments) fd.append('file', a.file, a.filename);
  try {
    const resp = await fetch(uploadUrl, { method: 'POST', body: fd });
    if (!resp.ok) return null;
    const j = await resp.json();
    return (j.files ?? []).map((f: any) => String(f.path));
  } catch {
    return null;
  }
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
