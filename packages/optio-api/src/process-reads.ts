/**
 * The one choke point for process reads (spec: "One choke point", decision
 * 6). The host declares the `visibility` rule once; a route must not be
 * able to forget it. `process-reads-guard.test.ts` enforces that no other
 * module names the `{prefix}_processes` collection or ANDs the visibility
 * condition itself.
 */
import type {
  Collection, CountDocumentsOptions, Db, Document, Filter, FindCursor, FindOptions, WithId,
} from 'mongodb';
import type { ProcessMetadataFilter } from './types.js';
import { andVisibility } from './visibility.js';

/** The `{prefix}_processes` collection of `db`. The only other callers
 *  allowed to name it directly are listed, with their reasons, in
 *  `process-reads-guard.test.ts`. */
export function processCollection(db: Db, prefix: string): Collection {
  return db.collection(`${prefix}_processes`);
}

/**
 * Reads of one request: every query ANDs the request's visibility
 * condition in, never replacing a caller's own filter (so a client filter
 * cannot widen past it). With `visibility` null every method passes the
 * caller's filter through unchanged -- the same object, not a copy -- so a
 * host with no `visibility` hook sees identical queries to a plain
 * `Collection`.
 */
export interface ProcessReads {
  readonly visibility: ProcessMetadataFilter | null;
  find(filter: Filter<Document>, options?: FindOptions): FindCursor<WithId<Document>>;
  findOne(filter: Filter<Document>, options?: FindOptions): Promise<WithId<Document> | null>;
  countDocuments(filter: Filter<Document>, options?: CountDocumentsOptions): Promise<number>;
  /** The collection without the condition, for access-scope.ts alone
   *  (scope's root lookup and the action gate's target lookup), each use
   *  followed by its own explicit visibility check. */
  readonly unfiltered: Collection;
}

function withVisibility(
  filter: Filter<Document>,
  visibility: ProcessMetadataFilter | null,
): Filter<Document> {
  return andVisibility(filter as Record<string, unknown>, visibility) as Filter<Document>;
}

/** Wrap a plain collection as `ProcessReads` for custom adapters and
 *  compatibility; `processReads` is `processReadsOf(processCollection(db, prefix), v)`. */
export function processReadsOf(col: Collection, visibility: ProcessMetadataFilter | null): ProcessReads {
  return {
    visibility,
    find(filter, options) {
      return col.find(withVisibility(filter, visibility), options);
    },
    findOne(filter, options) {
      return col.findOne(withVisibility(filter, visibility), options);
    },
    countDocuments(filter, options) {
      return col.countDocuments(withVisibility(filter, visibility), options);
    },
    unfiltered: col,
  };
}

/** The `ProcessReads` of one request against `{prefix}_processes` of `db`. */
export function processReads(
  db: Db,
  prefix: string,
  visibility: ProcessMetadataFilter | null,
): ProcessReads {
  return processReadsOf(processCollection(db, prefix), visibility);
}
