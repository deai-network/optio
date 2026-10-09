import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ObjectId, type Db } from 'mongodb';
import {
  createListPoller, createTreePoller, createMultiTreePoller, createSessionEventsPoller,
  type ListPollerHandle,
} from '../stream-poller.js';
import type { HubSubscriber, ProcessChangeHub } from '../process-change-hub.js';
import type { ProcessChange } from '../process-change-relevance.js';

// The streams read once at start, then only when their hub asks.
// Spec: docs/2026-10-09-api-change-streams-design.md (The stream functions)

/** A Db whose process collection answers find() from memory and counts calls.
 *  `gate`, when set, holds every read until released. */
function fakeDb(docs: any[] = []) {
  const state = {
    finds: 0,
    gate: null as null | { promise: Promise<void>; release: () => void },
  };
  const cursor = {
    sort: () => cursor,
    project: () => cursor,
    toArray: async () => {
      if (state.gate) await state.gate.promise;
      return docs;
    },
  };
  const db = {
    collection: () => ({ find: () => { state.finds++; return cursor; } }),
  } as unknown as Db;
  const hold = () => {
    let release = () => {};
    const promise = new Promise<void>((r) => { release = r; });
    state.gate = { promise, release: () => { state.gate = null; release(); } };
  };
  return { db, state, hold };
}

function fakeHub() {
  const subs: HubSubscriber[] = [];
  const hub = {
    subscribe: async (s: HubSubscriber) => { subs.push(s); },
    unsubscribe: (s: HubSubscriber) => { subs.splice(subs.indexOf(s), 1); },
  } as unknown as ProcessChangeHub;
  return { hub, subs };
}

const unrelated: ProcessChange = { op: 'update', id: 'nobody', fields: new Set(['status']), values: {} };
const flush = () => vi.advanceTimersByTimeAsync(0);

const ROOT = new ObjectId();
const makers: Record<string, (db: Db, hub: ProcessChangeHub) => ListPollerHandle> = {
  list: (db, hub) => createListPoller({ db, prefix: 'p', hub, sendEvent: () => {}, onError: () => {} }),
  tree: (db, hub) => createTreePoller({
    db, prefix: 'p', hub, rootId: ROOT.toString(), baseDepth: 0, sendEvent: () => {}, onError: () => {},
  }),
  'multi-tree': (db, hub) => createMultiTreePoller({
    db, prefix: 'p', hub, treeRoots: [{ rootId: ROOT, baseDepth: 0 }], flatIds: [],
    sendEvent: () => {}, onError: () => {},
  }),
  'session events': (db, hub) => createSessionEventsPoller({
    db, prefix: 'p', hub, sessionId: 's1', sendEvent: () => {}, onError: () => {},
  }),
};

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe.each(Object.keys(makers))('the %s stream', (kind) => {
  it('reads once at start, then only when the hub asks', async () => {
    const { db, state } = fakeDb();
    const { hub, subs } = fakeHub();
    const poller = makers[kind](db, hub);

    poller.start();
    await flush();
    expect(subs).toHaveLength(1);
    expect(state.finds).toBe(1);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(state.finds).toBe(1);

    subs[0].refresh();
    await flush();
    expect(state.finds).toBe(2);

    poller.stop();
    expect(subs).toHaveLength(0);
  });

  it('while a read is in flight, every change is relevant and one more read follows', async () => {
    const { db, state, hold } = fakeDb();
    const { hub, subs } = fakeHub();
    const poller = makers[kind](db, hub);
    hold();
    poller.start();
    await flush();
    expect(state.finds).toBe(1);

    expect(subs[0].isRelevant(unrelated)).toBe(true);   // in flight
    subs[0].refresh();
    subs[0].refresh();
    state.gate!.release();
    await flush();
    expect(state.finds).toBe(2);

    expect(subs[0].isRelevant(unrelated)).toBe(false);  // settled
    poller.stop();
  });

  it('makes no read after stop()', async () => {
    const { db, state } = fakeDb();
    const { hub, subs } = fakeHub();
    const poller = makers[kind](db, hub);
    poller.start();
    await flush();
    const sub = subs[0];

    poller.stop();
    sub.refresh();
    await flush();

    expect(state.finds).toBe(1);
  });
});

describe.each(Object.keys(makers))('the %s stream with a failing hub', (kind) => {
  it('ends through onError instead of an unhandled rejection', async () => {
    const { db, state } = fakeDb();
    const failing = {
      subscribe: async () => { throw new Error('hub broke'); },
      unsubscribe: () => {},
    } as unknown as ProcessChangeHub;
    let errors = 0;
    const poller = kind === 'list'
      ? createListPoller({ db, prefix: 'p', hub: failing, sendEvent: () => {}, onError: () => { errors++; } })
      : kind === 'tree'
        ? createTreePoller({ db, prefix: 'p', hub: failing, rootId: ROOT.toString(), baseDepth: 0, sendEvent: () => {}, onError: () => { errors++; } })
        : kind === 'multi-tree'
          ? createMultiTreePoller({ db, prefix: 'p', hub: failing, treeRoots: [{ rootId: ROOT, baseDepth: 0 }], flatIds: [], sendEvent: () => {}, onError: () => { errors++; } })
          : createSessionEventsPoller({ db, prefix: 'p', hub: failing, sessionId: 's1', sendEvent: () => {}, onError: () => { errors++; } });

    poller.start();
    await flush();

    expect(errors).toBe(1);
    expect(state.finds).toBe(0);
  });
});
