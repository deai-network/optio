/**
 * Which changes of `{prefix}_processes` a live stream must re-read for,
 * decided from the change event alone (no extra read). The rules lean towards
 * "relevant": a false positive costs one read, a false negative a stale stream.
 *
 * Spec: docs/2026-10-09-api-change-streams-design.md ("What is relevant")
 */
import type { ChangeStreamDocument, Document } from 'mongodb';

/**
 * The fields the list stream compares and sends; the list poll reads nothing
 * else (no log, widgetData, params or sessionEvents), so each read stays small.
 */
export const LIST_PROJECTION = {
  processId: 1, name: 1, status: 1, progress: 1, cancellable: 1, special: 1,
  warning: 1, metadata: 1, depth: 1, supportsResume: 1, hasSavedState: 1,
  supportsResurrect: 1, hasUnsavedWork: 1, resurrecting: 1,
  autoResumeScheduled: 1, browserOpenRequests: 1,
} as const;

const LIST_FIELDS: ReadonlySet<string> = new Set(Object.keys(LIST_PROJECTION));

/** A change of one process document, reduced to what the rules need. Ids are
 *  the `_id` as a string (ObjectId hex). `fields` are the top-level keys an
 *  update touched; `values` its `updatedFields` as delivered. */
export type ProcessChange =
  | { op: 'insert' | 'replace'; id: string; doc: Record<string, unknown> }
  | { op: 'update'; id: string; fields: Set<string>; values: Record<string, unknown> }
  | { op: 'delete'; id: string }
  | { op: 'invalidate' };

const topLevel = (path: string): string => path.split('.', 1)[0];

export function toProcessChange(ev: ChangeStreamDocument<Document>): ProcessChange | null {
  switch (ev.operationType) {
    case 'insert':
    case 'replace':
      return {
        op: ev.operationType,
        id: String(ev.documentKey._id),
        doc: (ev.fullDocument ?? {}) as Record<string, unknown>,
      };
    case 'update': {
      const desc = ev.updateDescription ?? {};
      const values = (desc.updatedFields ?? {}) as Record<string, unknown>;
      const fields = new Set<string>([
        ...Object.keys(values).map(topLevel),
        ...(desc.removedFields ?? []).map(topLevel),
        ...(desc.truncatedArrays ?? []).map((t) => topLevel(t.field)),
      ]);
      return { op: 'update', id: String(ev.documentKey._id), fields, values };
    }
    case 'delete':
      return { op: 'delete', id: String(ev.documentKey._id) };
    case 'drop':
    case 'rename':
    case 'dropDatabase':
    case 'invalidate':
      return { op: 'invalidate' };
    default:
      return null;
  }
}

const touches = (fields: Set<string>, wanted: ReadonlySet<string>): boolean => {
  for (const f of fields) if (wanted.has(f)) return true;
  return false;
};

/** The list stream (`members`: the ids it showed in its last read). */
export function listRelevant(c: ProcessChange, members: Set<string>): boolean {
  switch (c.op) {
    case 'invalidate':
    case 'insert':
    case 'replace': // may start or stop matching, like an insert
      return true;
    case 'delete':
      return members.has(c.id);
    case 'update':
      return c.fields.has('metadata') || (members.has(c.id) && touches(c.fields, LIST_FIELDS));
  }
}

/** A tree or multi-tree stream (`roots`: its root ids; `members`: the ids it
 *  showed in its last read, flat ids included). */
export function treeRelevant(c: ProcessChange, roots: Set<string>, members: Set<string>): boolean {
  switch (c.op) {
    case 'invalidate':
      return true;
    case 'insert':
    case 'replace':
      return members.has(c.id) || roots.has(String(c.doc.rootId));
    case 'delete':
    case 'update':
      return members.has(c.id);
  }
}

/** The session-events stream of `sessionId` (`members`: the ids it read). */
export function sessionRelevant(c: ProcessChange, sessionId: string, members: Set<string>): boolean {
  switch (c.op) {
    case 'invalidate':
      return true;
    case 'insert':
    case 'replace':
      return c.doc.originatingSessionId === sessionId;
    case 'delete':
      return false; // a deleted process has no new events to send
    case 'update':
      return c.values.originatingSessionId === sessionId
        || (members.has(c.id) && c.fields.has('sessionEvents'));
  }
}
