import { ObjectId, type Collection, type Document } from 'mongodb';

/**
 * Look up a single process document by either form of identifier the
 * system uses to refer to it:
 *
 *  - The Mongo ObjectId hex string (24 hex chars), matched against `_id`.
 *  - The application-level `processId` string from `mkPid()`, matched
 *    against the `processId` field on the document.
 *
 * Both fields live on every process row. The `processId` form is what
 * callers tend to know first (it's deterministic, generated client-side
 * before the row exists), while `_id` is what mongo assigns when the
 * row is materialized. Excavator's recipe-debug flow returns the
 * `processId` string to the frontend at submit time, then frontend code
 * hands it to optio-ui's `useProcessStream`, which calls into this API.
 * Without this fallback, the inline `new ObjectId(id)` in the old handlers
 * threw on the non-hex string and the request 500'd.
 *
 * Resolution order: ObjectId form first (cheap, indexed by `_id`), then
 * `processId` form. The two are disjoint in practice — `mkPid()` output
 * is much longer than 24 chars and contains underscores — so the order
 * does not matter for correctness, only for which index is hit on the
 * common path.
 */
export async function findProcessByEitherId<T extends Document = Document>(
  col: Collection<T>,
  id: string,
): Promise<T | null> {
  if (ObjectId.isValid(id)) {
    return col.findOne({ _id: new ObjectId(id) } as any) as Promise<T | null>;
  }
  return col.findOne({ processId: id } as any) as Promise<T | null>;
}
