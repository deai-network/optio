import { describe, it, expect, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import { Readable, Writable } from 'node:stream';
import { forwardUpload, type UploadForwardDeps } from '../upload-forward.js';

const BLOB_ID = new ObjectId();
const PID = new ObjectId().toHexString();

function fakeBucket() {
  const stored: Record<string, Buffer> = {};
  const deleted: string[] = [];
  const bucket = {
    openUploadStream(filename: string) {
      const chunks: Buffer[] = [];
      const w = new Writable({
        write(chunk, _enc, cb) {
          chunks.push(Buffer.from(chunk));
          cb();
        },
      });
      w.on('finish', () => {
        stored[filename] = Buffer.concat(chunks);
      });
      (w as any).id = BLOB_ID;
      return w as any;
    },
    async delete(id: ObjectId) {
      deleted.push(id.toHexString());
    },
    _stored: stored,
    _deleted: deleted,
  };
  return bucket;
}

function makeDeps(materialize: UploadForwardDeps['engine']['materializeUpload']) {
  const bucket = fakeBucket();
  const engine = { materializeUpload: vi.fn(materialize) };
  const deps: UploadForwardDeps = { bucket: bucket as any, engine };
  return { deps, bucket, engine };
}

describe('forwardUpload', () => {
  it('streams the file into GridFS, calls materializeUpload with the blobId, and returns the path', async () => {
    const { deps, bucket, engine } = makeDeps(async () => ({ ok: true, path: 'uploads/notes.md' }));
    const res = await forwardUpload(deps, PID, 'notes.md', Readable.from([Buffer.from('hello ')]));

    // File streamed into GridFS
    expect(bucket._stored['notes.md']).toEqual(Buffer.from('hello '));
    // Engine called with the staged blob id (hex) — bytes NOT in the params
    expect(engine.materializeUpload).toHaveBeenCalledOnce();
    expect(engine.materializeUpload).toHaveBeenCalledWith({
      processId: PID,
      blobId: BLOB_ID.toHexString(),
      filename: 'notes.md',
    });
    // Staged blob is transient — deleted afterwards
    expect(bucket._deleted).toEqual([BLOB_ID.toHexString()]);
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true, files: [{ filename: 'notes.md', path: 'uploads/notes.md' }] });
  });

  it('reports failure (and still deletes the staged blob) when materialize returns ok:false', async () => {
    const { deps, bucket } = makeDeps(async () => ({ ok: false, reason: 'no upload writer' }));
    const res = await forwardUpload(deps, PID, 'x.txt', Readable.from([Buffer.from('x')]));
    expect(res.status).toBe(502);
    expect(res.body).toEqual({ ok: false, message: 'no upload writer' });
    expect(bucket._deleted).toEqual([BLOB_ID.toHexString()]);
  });

  it('502s (and still deletes the staged blob) when the RPC throws', async () => {
    const { deps, bucket } = makeDeps(async () => {
      throw new Error('rpc timeout');
    });
    const res = await forwardUpload(deps, PID, 'x.txt', Readable.from([Buffer.from('x')]));
    expect(res.status).toBe(502);
    expect(bucket._deleted).toEqual([BLOB_ID.toHexString()]);
  });
});
