import type { Collection, Document } from 'mongodb';
import { ProcessMetadataFilterSchema } from 'optio-contracts';
import type { ProcessMetadataFilter } from './types.js';
import { metadataFilterToMongo } from './metadata-filter-query.js';

const LEAF_OP_KEYS = new Set(['eq', 'ne', 'in', 'nin', 'exists', 'gt', 'gte', 'lt', 'lte']);
const LEAF_ARRAY_OPS = new Set(['in', 'nin']);

/** Whether `value` is shaped like a leaf ops object (`{ eq: ..., in: [...] }`),
 *  as opposed to a legacy flat map's scalar value. Every one of its own keys
 *  must be a known leaf op; `FilterScalar` is never an object, so this is
 *  unambiguous at every level the schema allows. */
function isLeafOpsObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).every((k) => LEAF_OP_KEYS.has(k))
  );
}

function fail(field: string): never {
  throw new Error(
    `optio-api: the visibility hook returned an invalid metadata predicate: '${field}' is undefined`,
  );
}

/**
 * Walk a visibility hook's raw return value -- before the schema parse --
 * and reject any `undefined` value, however deep: `eq('ownerId', undefined)`
 * (or the equivalent `{ ownerId: { eq: undefined } }`) parses fine under
 * zod's `.optional()` leaf ops, then reaches the Mongo driver as `$eq:
 * undefined`, which the driver sends as `null` -- and `$eq: null` matches a
 * *missing* key, not just a null one. That turns a clause meant to narrow
 * (`eq('ownerId', me)`) into one that admits every unowned document too.
 * `null` itself is fine (and documented as matching a missing key, per
 * MongoDB semantics) -- only `undefined` is rejected.
 */
function assertNoUndefinedValues(node: unknown): void {
  if (node === null || typeof node !== 'object' || Array.isArray(node)) return;
  const obj = node as Record<string, unknown>;
  if ('AND' in obj || 'OR' in obj) {
    for (const item of (obj.AND ?? obj.OR) as unknown[]) assertNoUndefinedValues(item);
    return;
  }
  if ('NOT' in obj) {
    assertNoUndefinedValues(obj.NOT);
    return;
  }
  // A field map: each value is either leaf ops (nested predicate, or the
  // top-level predicate form) or -- only at the top level -- a legacy flat
  // scalar.
  for (const [field, value] of Object.entries(obj)) {
    if (isLeafOpsObject(value)) {
      for (const [op, opValue] of Object.entries(value)) {
        if (opValue === undefined) fail(`${field}.${op}`);
        if (LEAF_ARRAY_OPS.has(op) && Array.isArray(opValue)) {
          opValue.forEach((v, i) => { if (v === undefined) fail(`${field}.${op}[${i}]`); });
        }
      }
    } else if (value === undefined) {
      fail(field);
    }
  }
}

/**
 * Validate a `visibility` hook's return value. `null`, `undefined` and `{}`
 * mean "no restriction" and normalize to `null`. Anything else is parsed
 * with `ProcessMetadataFilterSchema` (the same schema `metadataFilter`
 * uses); an invalid predicate throws rather than passing through
 * unfiltered -- including an `undefined` leaf value anywhere in the tree
 * (see `assertNoUndefinedValues`), which the schema's `.optional()` leaf
 * ops let through but which the Mongo driver treats as `null` -- and
 * `eq(key, null)` matches a *missing* key too, so a null or undefined user
 * id must never reach the predicate; `undefined` itself is rejected
 * outright, never normalized to "no restriction".
 */
export function normalizeVisibility(raw: unknown): ProcessMetadataFilter | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'object' && !Array.isArray(raw) && Object.keys(raw as object).length === 0) {
    return null;
  }
  // Checked before the schema parse: zod's own `.optional()` leaf ops and
  // array element types let an explicit `undefined` through at some depths
  // (nested leaf ops) and reject it at others (a legacy flat value, an
  // `in`/`nin` array element) with a generic message that does not name the
  // field. Walking the raw value first gives one consistent, field-naming
  // error for every case.
  assertNoUndefinedValues(raw);
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
