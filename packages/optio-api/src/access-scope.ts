import { ObjectId, type Collection, type Document } from 'mongodb';
import { ProcessMetadataFilterLegacySchema } from 'optio-contracts';
import type { ProcessMetadataFilter } from './types.js';
import type { Access, OptioAction, ProcessRef, ScopeFilter } from './auth.js';
import { findProcessByEitherId } from './process-id-resolver.js';
import { isVisible } from './visibility.js';
import { processReadsOf, type ProcessReads } from './process-reads.js';

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
 * The lookups below (`inScope`, `findScopedProcess`, `findReadableProcess`,
 * `findActionTarget`, `gateProcess`) accept a `ProcessReads` or, for custom
 * adapters and compatibility, a plain `Collection` -- wrapped here with
 * `processReadsOf(col, null)`, since only its `.unfiltered` collection is
 * needed: each raw read below is followed by its own explicit visibility
 * check (`isVisible`), so these lookups never rely on `ProcessReads`'s own
 * filtering.
 */
function asReads(input: Collection | ProcessReads): ProcessReads {
  return 'unfiltered' in input ? input : processReadsOf(input, null);
}

/**
 * Whether a process is inside the scope. A process whose own metadata lacks
 * any scope key is judged by its root's metadata; one whose root lacks it too
 * is outside (fail closed).
 */
export async function inScope(
  input: Collection | ProcessReads,
  doc: Document,
  scope: ScopeFilter | null,
): Promise<boolean> {
  if (!scope) return true;
  const own = (doc.metadata ?? {}) as Record<string, unknown>;
  if (Object.keys(scope).every((k) => k in own)) return matches(own, scope);
  if (!doc.parentId || !doc.rootId) return false;
  const reads = asReads(input);
  const root = await reads.unfiltered.findOne({ _id: doc.rootId }, { projection: { metadata: 1 } });
  return matches((root?.metadata ?? {}) as Record<string, unknown>, scope);
}

/** Look a process up by either id form; outside the scope it is not found. */
export async function findScopedProcess(
  input: Collection | ProcessReads,
  id: string,
  scope: ScopeFilter | null,
): Promise<Document | null> {
  const reads = asReads(input);
  const doc = await findProcessByEitherId(reads.unfiltered, id);
  if (!doc) return null;
  return (await inScope(reads, doc, scope)) ? doc : null;
}

/**
 * Look a process up by either id form; outside the scope or hidden by the
 * visibility predicate it is not found (the same answer as a missing one).
 */
export async function findReadableProcess(
  input: Collection | ProcessReads,
  id: string,
  access: Pick<Access, 'scope' | 'visibility'>,
): Promise<Document | null> {
  const reads = asReads(input);
  const doc = await findScopedProcess(reads, id, access.scope);
  if (!doc) return null;
  return (await isVisible(reads.unfiltered, doc, access.visibility)) ? doc : null;
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
 * The document a single-process action targets: by `_id` for a 24-hex id,
 * else by `processId`, where a duplicated `processId` resolves to the newest
 * document, as the engine does.
 */
export async function findActionTarget(
  input: Collection | ProcessReads,
  id: string,
): Promise<Document | null> {
  const col = asReads(input).unfiltered;
  return ObjectId.isValid(id) && /^[a-f0-9]{24}$/i.test(id)
    ? col.findOne({ _id: new ObjectId(id) })
    : col.find({ processId: id }).sort({ _id: -1 }).limit(1).next();
}

/**
 * Admit a single-process action: the process must exist, be in scope and
 * visible (else 404, its existence is not revealed) and pass `authorize`
 * (else 403). Unrestricted access skips the lookup, leaving everything to
 * the engine as before. The target is found by `findActionTarget`, so a
 * duplicated `processId` is judged by its newest document, the one the
 * engine acts on.
 */
export async function gateProcess(
  input: Collection | ProcessReads,
  id: string,
  access: Access,
  action: OptioAction,
): Promise<GateResult> {
  if (!access.restricted) return { ok: true, doc: null };
  const reads = asReads(input);
  const doc = await findActionTarget(reads, id);
  if (
    !doc ||
    !(await inScope(reads, doc, access.scope)) ||
    !(await isVisible(reads.unfiltered, doc, access.visibility))
  ) {
    return { ok: false, status: 404 };
  }
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
