import { pipeline } from 'node:stream/promises';
import type { Readable } from 'node:stream';
import type { GridFSBucket, ObjectId } from 'mongodb';
import type {
  MaterializeUploadParams,
  MaterializeUploadResult,
} from './_generated/optio-engine.js';

export interface UploadForwardResult {
  status: number;
  body: unknown;
}

/**
 * The two collaborators the upload forwarder needs, injected so tests can drive
 * it without a real Mongo/redis stack:
 *  - `bucket`: a GridFS bucket over the same Mongo db the API already holds; the
 *    uploaded bytes are staged here (they never cross Redis).
 *  - `engine`: the clamator `optio-engine` client, whose `materializeUpload` RPC
 *    reaches the task's process by `processId` and writes the blob into its
 *    workdir via the in-process upload writer.
 */
export interface UploadForwardDeps {
  bucket: Pick<GridFSBucket, 'openUploadStream' | 'delete'>;
  engine: {
    materializeUpload(params: MaterializeUploadParams): Promise<MaterializeUploadResult>;
  };
}

/**
 * Stream one multipart file into GridFS, then hand its blob id to the engine's
 * `materializeUpload` RPC (which resolves the running task by `processId` and
 * writes the file into `<workdir>/uploads/<name>`). The GridFS blob is transient
 * staging: it is best-effort deleted afterwards whether the RPC succeeded, failed
 * or threw. Bytes never enter the RPC params — only the blob id does.
 */
export async function forwardUpload(
  deps: UploadForwardDeps,
  processId: string,
  filename: string,
  stream: Readable,
): Promise<UploadForwardResult> {
  const upload = deps.bucket.openUploadStream(filename);
  const blobId = upload.id as ObjectId;

  try {
    await pipeline(stream, upload);
  } catch {
    await safeDelete(deps, blobId);
    return { status: 502, body: { ok: false, message: 'failed to stage upload' } };
  }

  let result: MaterializeUploadResult;
  try {
    result = await deps.engine.materializeUpload({
      processId,
      blobId: blobId.toHexString(),
      filename,
    });
  } catch {
    await safeDelete(deps, blobId);
    return { status: 502, body: { ok: false, message: 'session not reachable' } };
  }

  await safeDelete(deps, blobId);

  if (result.ok) {
    return { status: 200, body: { ok: true, files: [{ filename, path: result.path }] } };
  }
  return { status: 502, body: { ok: false, message: result.reason } };
}

async function safeDelete(deps: UploadForwardDeps, blobId: ObjectId): Promise<void> {
  try {
    await deps.bucket.delete(blobId);
  } catch {
    // Staged blobs are transient; a failed cleanup must not fail the request.
  }
}
