import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { MongoClient } from 'mongodb';
import {
  ProcessChangeHub, ChangeStreamsUnavailable, getProcessChangeHub,
  type ProcessChangeSource, type HubSubscriber,
} from '../process-change-hub.js';
import type { ProcessChange } from '../process-change-relevance.js';

// Spec: docs/2026-10-09-api-change-streams-design.md (Parts; When change
// streams are missing or break). Timing runs on vitest's fake timers.

function fakeSource() {
  const pending: { resolve: () => void; reject: (e: unknown) => void }[] = [];
  const s = {
    opens: 0,
    closes: 0,
    onChange: (_c: ProcessChange) => {},
    onError: (_e: unknown) => {},
    open(onChange: (c: ProcessChange) => void, onError: (e: unknown) => void) {
      s.opens++;
      s.onChange = onChange;
      s.onError = onError;
      return new Promise<{ close(): Promise<void> }>((resolve, reject) => {
        pending.push({
          resolve: () => resolve({ close: async () => { s.closes++; } }),
          reject,
        });
      });
    },
    succeed() { pending.shift()!.resolve(); },
    fail(err: unknown) { pending.shift()!.reject(err); },
  };
  return s;
}

function sub(relevant: (c: ProcessChange) => boolean = () => true) {
  const s = {
    refreshes: 0,
    isRelevant: relevant,
    refresh() { s.refreshes++; },
  };
  return s as typeof s & HubSubscriber;
}

const update = (id: string): ProcessChange =>
  ({ op: 'update', id, fields: new Set(['status']), values: {} });

const flush = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('ProcessChangeHub', () => {
  it('a change reaches only the subscribers it is relevant to', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub(() => true);
    const b = sub(() => false);
    const done = Promise.all([hub.subscribe(a), hub.subscribe(b)]);
    src.succeed();
    await done;
    expect(hub.mode).toBe('streaming');

    src.onChange(update('x'));

    expect(a.refreshes).toBe(1);
    expect(b.refreshes).toBe(0);
  });

  it('changes within a second give one refresh at its end; a change after a quiet second refreshes at once', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub();
    const done = hub.subscribe(a);
    src.succeed();
    await done;

    src.onChange(update('x'));
    expect(a.refreshes).toBe(1);
    for (let i = 0; i < 50; i++) src.onChange(update('x'));
    await vi.advanceTimersByTimeAsync(999);
    expect(a.refreshes).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(a.refreshes).toBe(2);

    await vi.advanceTimersByTimeAsync(2000);
    src.onChange(update('x'));
    expect(a.refreshes).toBe(3);
  });

  it('a change while a refresh is pending gives exactly one more refresh', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub();
    const done = hub.subscribe(a);
    src.succeed();
    await done;

    src.onChange(update('x'));   // at once
    src.onChange(update('x'));   // pending
    src.onChange(update('x'));   // joins the pending one
    await vi.advanceTimersByTimeAsync(5000);

    expect(a.refreshes).toBe(2);
  });

  it('an unavailable source polls every second and tries again later', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource, { retryUnavailableMs: 10_000 });
    const a = sub();
    const done = hub.subscribe(a);
    src.fail(new ChangeStreamsUnavailable('standalone'));
    await done;
    expect(hub.mode).toBe('polling');

    await vi.advanceTimersByTimeAsync(3000);
    expect(a.refreshes).toBe(3);
    expect(src.opens).toBe(1);

    await vi.advanceTimersByTimeAsync(7000);   // t = 10 000: the new attempt
    expect(a.refreshes).toBe(10);
    expect(src.opens).toBe(2);
    src.succeed();
    await flush();
    expect(hub.mode).toBe('streaming');

    await vi.advanceTimersByTimeAsync(1000);   // the one refresh after the (re)open
    expect(a.refreshes).toBe(11);
    await vi.advanceTimersByTimeAsync(5000);   // no more polling
    expect(a.refreshes).toBe(11);
  });

  it('the default retry after an unavailable source is 5 minutes', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const done = hub.subscribe(sub());
    src.fail(new ChangeStreamsUnavailable('standalone'));
    await done;

    await vi.advanceTimersByTimeAsync(299_999);
    expect(src.opens).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(src.opens).toBe(2);
  });

  it('an error reopens with backoff, polls meanwhile, and refreshes everyone once', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub();
    const done = hub.subscribe(a);
    src.succeed();
    await done;

    src.onError(new Error('primary stepped down'));
    expect(hub.mode).toBe('polling');
    for (const delay of [1000, 2000, 4000, 8000, 16_000, 30_000, 30_000]) {
      const before = src.opens;
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(src.opens).toBe(before);
      await vi.advanceTimersByTimeAsync(1);
      expect(src.opens).toBe(before + 1);
      src.fail(new Error('still down'));
      await flush();
    }
    expect(a.refreshes).toBeGreaterThan(80);   // polled once a second meanwhile

    await vi.advanceTimersByTimeAsync(30_000);
    const beforeOpen = a.refreshes;
    src.succeed();
    await flush();
    expect(hub.mode).toBe('streaming');
    await vi.advanceTimersByTimeAsync(5000);
    expect(a.refreshes).toBe(beforeOpen + 1);

    src.onError(new Error('again'));           // the backoff starts over
    await vi.advanceTimersByTimeAsync(1000);
    expect(src.opens).toBe(10);
  });

  it('invalidate refreshes every subscriber', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub(() => false);
    const b = sub(() => false);
    const done = Promise.all([hub.subscribe(a), hub.subscribe(b)]);
    src.succeed();
    await done;

    src.onChange({ op: 'invalidate' });

    expect([a.refreshes, b.refreshes]).toEqual([1, 1]);
  });

  it('the stream closes when the last subscriber leaves, and no timer remains', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub();
    const b = sub();
    const done = Promise.all([hub.subscribe(a), hub.subscribe(b)]);
    src.succeed();
    await done;
    src.onChange(update('x'));
    src.onChange(update('x'));                 // a pending throttled refresh each

    hub.unsubscribe(a);
    expect(src.closes).toBe(0);
    hub.unsubscribe(b);
    await flush();

    expect(src.closes).toBe(1);
    expect(hub.mode).toBe('idle');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a polling hub leaves no timer when the last subscriber leaves', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub();
    const done = hub.subscribe(a);
    src.fail(new ChangeStreamsUnavailable('standalone'));
    await done;

    hub.unsubscribe(a);

    expect(hub.mode).toBe('idle');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('an open that resolves after the last subscriber left is closed at once', async () => {
    const src = fakeSource();
    const hub = new ProcessChangeHub(src as ProcessChangeSource);
    const a = sub();
    const done = hub.subscribe(a);
    hub.unsubscribe(a);
    src.succeed();
    await done;
    await flush();

    expect(src.closes).toBe(1);
    expect(hub.mode).toBe('idle');
  });
});

describe('getProcessChangeHub', () => {
  it('shares one hub per client, database and prefix', () => {
    const client = new MongoClient('mongodb://localhost:27017');
    const other = new MongoClient('mongodb://localhost:27017');
    const hub = getProcessChangeHub(client.db('a'), 'p');

    expect(getProcessChangeHub(client.db('a'), 'p')).toBe(hub);   // another Db object
    expect(getProcessChangeHub(client.db('a'), 'q')).not.toBe(hub);
    expect(getProcessChangeHub(client.db('b'), 'p')).not.toBe(hub);
    expect(getProcessChangeHub(other.db('a'), 'p')).not.toBe(hub);
  });

  it('polls when OPTIO_API_CHANGE_STREAMS=off', async () => {
    const saved = process.env.OPTIO_API_CHANGE_STREAMS;
    process.env.OPTIO_API_CHANGE_STREAMS = 'off';
    try {
      const client = new MongoClient('mongodb://localhost:27017');
      const hub = getProcessChangeHub(client.db('a'), 'p');
      const a = sub();
      await hub.subscribe(a);
      expect(hub.mode).toBe('polling');
      hub.unsubscribe(a);
    } finally {
      if (saved === undefined) delete process.env.OPTIO_API_CHANGE_STREAMS;
      else process.env.OPTIO_API_CHANGE_STREAMS = saved;
    }
  });
});
