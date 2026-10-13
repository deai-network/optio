import type { Collection, Document } from 'mongodb';
import { ProcessMetadataFilterSchema } from 'optio-contracts';
import type { ProcessMetadataFilter } from './types.js';
import { metadataFilterToMongo } from './metadata-filter-query.js';

/**
 * Validate a `visibility` hook's return value. `null`, `undefined` and `{}`
 * mean "no restriction" and normalize to `null`. Anything else is parsed
 * with `ProcessMetadataFilterSchema` (the same schema `metadataFilter`
 * uses); an invalid predicate throws rather than passing through
 * unfiltered.
 */
export function normalizeVisibility(raw: unknown): ProcessMetadataFilter | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'object' && !Array.isArray(raw) && Object.keys(raw as object).length === 0) {
    return null;
  }
  const result = ProcessMetadataFilterSchema.safeParse(raw);
  if (!result.success) {
    const message = result.error.issues[0]?.message ?? 'invalid metadata predicate';
    throw new Error(`optio-api: the visibility hook returned an invalid metadata predicate: ${message}`);
  }
  return result.data as ProcessMetadataFilter;
}

/** `null` -> `{}`; otherwise the predicate's Mongo query fragment. */
export function visibilityToMongo(visibility: ProcessMetadataFilter | null): Record<string, unknown> {
  if (!visibility) return {};
  return metadataFilterToMongo(visibility);
}

/**
 * Confine a Mongo filter to the visibility predicate. `$and` rather than a
 * merge, so a client filter cannot widen past it, built the same way as
 * `andScope`.
 */
export function andVisibility(
  base: Record<string, unknown>,
  visibility: ProcessMetadataFilter | null,
): Record<string, unknown> {
  if (!visibility) return base;
  const condition = visibilityToMongo(visibility);
  if (Object.keys(base).length === 0) return condition;
  return { $and: [base, condition] };
}

/**
 * Whether a single document satisfies the visibility predicate, judged by
 * its own metadata in Mongo (never by a JavaScript copy of the semantics).
 * `null` admits everything without issuing a query.
 */
export async function isVisible(
  col: Collection,
  doc: Document,
  visibility: ProcessMetadataFilter | null,
): Promise<boolean> {
  if (!visibility) return true;
  const condition = visibilityToMongo(visibility);
  const count = await col.countDocuments({ $and: [{ _id: doc._id }, condition] }, { limit: 1 });
  return count === 1;
}

/**
 * Drop tree orphans: a doc is kept when its `parentId` is null or absent,
 * its own `_id` is named in `keep`, or its parent is itself kept. A parent
 * not present in `docs` counts as not kept. Input order is preserved; each
 * id's chain is walked (and memoised) once.
 */
export function pruneOrphans<T extends Document>(docs: T[], keep?: ReadonlySet<string>): T[] {
  const byId = new Map<string, T>(docs.map((doc) => [doc._id.toString(), doc]));
  const memo = new Map<string, boolean>();

  function isKept(doc: T): boolean {
    const id = doc._id.toString();
    const cached = memo.get(id);
    if (cached !== undefined) return cached;
    let kept: boolean;
    if (!doc.parentId) {
      kept = true;
    } else if (keep?.has(id)) {
      kept = true;
    } else {
      const parent = byId.get(doc.parentId.toString());
      kept = parent ? isKept(parent) : false;
    }
    memo.set(id, kept);
    return kept;
  }

  return docs.filter(isKept);
}
