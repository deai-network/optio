import type { Readable } from 'node:stream';
import { GridFSBucket, type Db, type ObjectId } from 'mongodb';
import { processesContract } from 'optio-contracts';

/**
 * A task's result file, as optio-core's `store_result` leaves it: one GridFS
 * file in the default `fs` bucket, named on the process record as
 * `result: { fileId, filename, contentType, size }`. `fileId` is optio's own:
 * no response and no stream carries it (`publicResult`).
 *
 * Spec: docs/2026-10-25-factory-results-design.md §1 ("Serving it")
 */
export interface StoredResult {
  fileId: ObjectId;
  filename: string;
  contentType: string;
  size: number;
}

/** A record's `result` as clients see it: without the GridFS `fileId`. */
export function publicResult(result: Record<string, unknown> | null | undefined) {
  if (!result) return result;
  const { fileId: _fileId, ...rest } = result;
  return rest;
}

/**
 * The collaborator the result route reads GridFS through, injected so tests
 * can watch it (`getProcessResult` defaults to `GRIDFS_RESULT_DOWNLOAD`):
 *  - `bucket(db)`: the bucket optio-core's `store_result` writes to, over the
 *    database of the process record.
 */
export interface ResultDownloadDeps {
  bucket(db: Db): Pick<GridFSBucket, 'find' | 'openDownloadStream'>;
}

export const GRIDFS_RESULT_DOWNLOAD: ResultDownloadDeps = {
  bucket: (db) => new GridFSBucket(db),
};

/**
 * Open a record's result file: its length and a stream of its bytes, or null
 * when the file is gone (the sweep or a reset took it).
 */
export async function openResult(
  deps: ResultDownloadDeps,
  db: Db,
  result: StoredResult,
): Promise<{ length: number; stream: Readable } | null> {
  const bucket = deps.bucket(db);
  const file = await bucket.find({ _id: result.fileId }, { limit: 1 }).next();
  if (!file) return null;
  return { length: file.length, stream: bucket.openDownloadStream(result.fileId) };
}

/**
 * `Content-Disposition` of a result file: `inline`, an ASCII `filename`
 * fallback in which anything outside printable ASCII, and `"`, `\`, `;` and
 * `%`, become `_` (the header stays one valid line), and the exact name as
 * RFC 5987's `filename*=UTF-8''…`.
 */
export function contentDisposition(filename: string): string {
  const ascii = filename.replace(/[^\x20-\x7e]|["\\;%]/gu, '_');
  // encodeURIComponent leaves ' ( ) * as they are; RFC 5987's attr-char has none of them.
  const encoded = encodeURIComponent(filename).replace(
    /['()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
  );
  return `inline; filename="${ascii}"; filename*=UTF-8''${encoded}`;
}

/**
 * The headers that keep a result file inert. It renders inline in the host
 * app's origin, next to its session cookie, and `store_result` takes any
 * media type: script in an HTML, SVG or XML result would run as the app.
 * So the browser may not sniff a type into an active one (`nosniff`), and
 * every type but PDF is sandboxed. PDF is left out because a sandboxed
 * document may refuse Chromium's built-in PDF viewer.
 *
 * A value is PDF only when the whole of it is one media type, exactly
 * `application/pdf` (any case), with parameters but no comma: browsers split
 * a Content-Type on commas and render the last type, so
 * `application/pdf;a=b, text/html` is HTML to them. A PDF type with a comma
 * inside a quoted parameter is sandboxed too, the safe direction.
 */
const SINGLE_PDF_TYPE = /^\s*application\/pdf\s*(;[^,]*)?$/i;

export function resultSafetyHeaders(
  contentType: string,
): { 'X-Content-Type-Options': 'nosniff'; 'Content-Security-Policy'?: 'sandbox' } {
  if (SINGLE_PDF_TYPE.test(contentType)) return { 'X-Content-Type-Options': 'nosniff' };
  return { 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': 'sandbox' };
}

export type ResultQueryResult =
  | { ok: true; query: { database?: string; prefix?: string } }
  | { ok: false; status: 400; body: { message: string } };

/**
 * The instance a result request names, checked with the contract's own query
 * schema. Every adapter serves the route outside ts-rest (ts-rest would send
 * the contract's `application/octet-stream` instead of the file's own type),
 * so this is the check ts-rest would have made; a malformed query is a 400.
 */
export function resultQueryOf(raw: Record<string, unknown>): ResultQueryResult {
  const parsed = processesContract.getResult.query.safeParse(raw);
  if (!parsed.success) {
    return { ok: false, status: 400, body: { message: 'database and prefix must be single strings' } };
  }
  return { ok: true, query: parsed.data };
}
