import { ObjectId, type Collection, type Document } from 'mongodb';
import { ProcessMetadataFilterLegacySchema } from 'optio-contracts';
import type { ProcessMetadataFilter } from './types.js';
import type { Access, OptioAction, ProcessRef, ScopeFilter } from './auth.js';
import { findProcessByEitherId } from './process-id-resolver.js';

/** `{ k: v }` -> `{ 'metadata.k': v }`: exact-match equality on metadata. */
export function scopeToMongo(scope: ScopeFilter | null): Record<string, unknown> {
  if (!scope) return {};
  return Object.fromEntries(Object.entries(scope).map(([k, v]) => [`metadata.${k}`, v]));
}

/**
 * Confine a Mongo filter to the scope. `$and` rather than a merge, so a client
 * filter on a scope key can only narrow the result, never replace the scope.
 */
export function andScope(
  base: Record<string, unknown>,
  scope: ScopeFilter | null,
): Record<string, unknown> {
  if (!scope) return base;
  const scoped = scopeToMongo(scope);
  if (Object.keys(base).length === 0) return scoped;
  return { $and: [base, scoped] };
}

function matches(metadata: Record<string, unknown>, scope: ScopeFilter): boolean {
  return Object.entries(scope).every(([k, v]) => k in metadata && metadata[k] === v);
}

/**
 * Whether a process is inside the scope. A process whose own metadata lacks
 * any scope key is judged by its root's metadata; one whose root lacks it too
 * is outside (fail closed).
 */
export async function inScope(
  col: Collection,
  doc: Document,
  scope: ScopeFilter | null,
): Promise<boolean> {
  if (!scope) return true;
  const own = (doc.metadata ?? {}) as Record<string, unknown>;
  if (Object.keys(scope).every((k) => k in own)) return matches(own, scope);
  if (!doc.parentId || !doc.rootId) return false;
  const root = await col.findOne({ _id: doc.rootId }, { projection: { metadata: 1 } });
  return matches((root?.metadata ?? {}) as Record<string, unknown>, scope);
}

/** Look a process up by either id form; outside the scope it is not found. */
export async function findScopedProcess(
  col: Collection,
  id: string,
  scope: ScopeFilter | null,
): Promise<Document | null> {
  const doc = await findProcessByEitherId(col, id);
  if (!doc) return null;
  return (await inScope(col, doc, scope)) ? doc : null;
}

export function toProcessRef(doc: Document): ProcessRef {
  return {
    _id: doc._id.toString(),
    processId: doc.processId,
    name: doc.name,
    ...(doc.parentId ? { parentId: doc.parentId.toString() } : {}),
    rootId: doc.rootId.toString(),
    metadata: (doc.metadata ?? {}) as Record<string, unknown>,
  };
}

export type GateResult =
  | { ok: true; doc: Document | null }
  | { ok: false; status: 404 | 403 };

/**
 * Admit a single-process action: the process must exist, be in scope (else
 * 404, its existence is not revealed) and pass `authorize` (else 403).
 * Unrestricted access skips the lookup, leaving everything to the engine as
 * before. A duplicated `processId` resolves to the newest document, as the
 * engine does.
 */
export async function gateProcess(
  col: Collection,
  id: string,
  access: Access,
  action: OptioAction,
): Promise<GateResult> {
  if (!access.restricted) return { ok: true, doc: null };
  const doc = ObjectId.isValid(id) && /^[a-f0-9]{24}$/i.test(id)
    ? await col.findOne({ _id: new ObjectId(id) })
    : await col.find({ processId: id }).sort({ _id: -1 }).limit(1).next();
  if (!doc || !(await inScope(col, doc, access.scope))) return { ok: false, status: 404 };
  if (!(await access.authorize({ action, process: toProcessRef(doc) }))) {
    return { ok: false, status: 403 };
  }
  return { ok: true, doc };
}

/** Instance discovery lists every optio database/prefix: `authorize` decides. */
export async function gateInstances(access: Access): Promise<boolean> {
  if (!access.restricted) return true;
  return access.authorize({ action: 'instances' });
}

export type ScopedFilterResult =
  | { ok: true; filter: ProcessMetadataFilter | undefined }
  | { ok: false; status: 400 | 403; message: string };

/**
 * The filter a resync runs with under a scope: the client's flat filter plus
 * the scope keys. The engine filters both the regenerated tasks and the stale
 * records it removes by this map, so a scoped resync cannot reach outside.
 * Resync filters are flat exact-match maps on the engine side; a predicate
 * tree is refused rather than guessed at.
 */
export function scopedResyncFilter(
  filter: ProcessMetadataFilter | undefined,
  scope: ScopeFilter | null,
): ScopedFilterResult {
  if (!scope) return { ok: true, filter };
  const flat = ProcessMetadataFilterLegacySchema.safeParse(filter ?? {});
  if (!flat.success) {
    return {
      ok: false,
      status: 400,
      message: 'A scoped resync needs a flat metadataFilter ({ key: value, ... })',
    };
  }
  const client = flat.data as Record<string, unknown>;
  for (const [k, v] of Object.entries(scope)) {
    if (k in client && client[k] !== v) {
      return { ok: false, status: 403, message: 'Forbidden' };
    }
  }
  return { ok: true, filter: { ...client, ...scope } as ProcessMetadataFilter };
}
