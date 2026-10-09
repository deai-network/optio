import { describe, it, expect } from 'vitest';
import { ObjectId } from 'mongodb';
import {
  listRelevant, treeRelevant, sessionRelevant, toProcessChange, type ProcessChange,
} from '../process-change-relevance.js';

// Spec: docs/2026-10-09-api-change-streams-design.md ("What is relevant")

const R = new ObjectId();
const OTHER = new ObjectId();
const hex = (o: ObjectId) => o.toString();

const insert = (id: string, doc: Record<string, unknown>): ProcessChange =>
  ({ op: 'insert', id, doc });
const update = (id: string, fields: string[], values: Record<string, unknown> = {}): ProcessChange =>
  ({ op: 'update', id, fields: new Set(fields), values });
const del = (id: string): ProcessChange => ({ op: 'delete', id });
const INVALIDATE: ProcessChange = { op: 'invalidate' };

describe('listRelevant', () => {
  const members = new Set(['a']);

  it('treats any insert as relevant (it may match the filter)', () => {
    expect(listRelevant(insert('x', {}), members)).toBe(true);
  });
  it('re-reads for a shown process whose sent fields change', () => {
    expect(listRelevant(update('a', ['status']), members)).toBe(true);
    expect(listRelevant(update('a', ['progress']), members)).toBe(true);
  });
  it('ignores a shown process whose change the list does not send', () => {
    expect(listRelevant(update('a', ['log']), members)).toBe(false);
  });
  it('re-reads when another process changes metadata (it may start matching)', () => {
    expect(listRelevant(update('x', ['metadata']), members)).toBe(true);
  });
  it('ignores other changes of processes it does not show', () => {
    expect(listRelevant(update('x', ['status']), members)).toBe(false);
  });
  it('re-reads when a shown process is deleted, not another', () => {
    expect(listRelevant(del('a'), members)).toBe(true);
    expect(listRelevant(del('x'), members)).toBe(false);
  });
  it('re-reads on invalidate', () => {
    expect(listRelevant(INVALIDATE, members)).toBe(true);
  });
});

describe('treeRelevant', () => {
  const roots = new Set([hex(R)]);
  const members = new Set([hex(R), 'c']);

  it('re-reads for an insert under its root, not under another', () => {
    expect(treeRelevant(insert('n', { rootId: R }), roots, members)).toBe(true);
    expect(treeRelevant(insert('n', { rootId: OTHER }), roots, members)).toBe(false);
  });
  it('re-reads for any update of a member', () => {
    expect(treeRelevant(update('c', ['log']), roots, members)).toBe(true);
  });
  it('ignores updates of other processes', () => {
    expect(treeRelevant(update('z', ['status']), roots, members)).toBe(false);
  });
  it('re-reads on invalidate', () => {
    expect(treeRelevant(INVALIDATE, roots, members)).toBe(true);
  });
});

describe('sessionRelevant', () => {
  const members = new Set(['p']);

  it('re-reads when a process joins this session, not another', () => {
    expect(sessionRelevant(
      update('q', ['originatingSessionId'], { originatingSessionId: 's1' }), 's1', members,
    )).toBe(true);
    expect(sessionRelevant(
      update('q', ['originatingSessionId'], { originatingSessionId: 's2' }), 's1', members,
    )).toBe(false);
  });
  it('re-reads for a member whose sessionEvents change, not for its status', () => {
    expect(sessionRelevant(update('p', ['sessionEvents']), 's1', members)).toBe(true);
    expect(sessionRelevant(update('p', ['status']), 's1', members)).toBe(false);
  });
  it('re-reads on invalidate', () => {
    expect(sessionRelevant(INVALIDATE, 's1', members)).toBe(true);
  });
});

describe('toProcessChange', () => {
  it('reduces an update to its top-level fields and keeps the new values', () => {
    const id = new ObjectId();
    const c = toProcessChange({
      operationType: 'update',
      documentKey: { _id: id },
      updateDescription: {
        updatedFields: { 'progress.percent': 5, 'log.3': { message: 'x' }, originatingSessionId: 's1' },
        removedFields: ['widgetData'],
        truncatedArrays: [{ field: 'sessionEvents', newSize: 0 }],
      },
    } as any);
    expect(c).toEqual({
      op: 'update', id: hex(id),
      fields: new Set(['progress', 'log', 'originatingSessionId', 'widgetData', 'sessionEvents']),
      values: { 'progress.percent': 5, 'log.3': { message: 'x' }, originatingSessionId: 's1' },
    });
  });
  it('turns drop, rename, dropDatabase and invalidate into invalidate', () => {
    for (const operationType of ['drop', 'rename', 'dropDatabase', 'invalidate']) {
      expect(toProcessChange({ operationType } as any)).toEqual({ op: 'invalidate' });
    }
  });
});
