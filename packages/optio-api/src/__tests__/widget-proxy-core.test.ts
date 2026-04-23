import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import {
  resolveWidgetUpstream,
  applyInnerAuthHeaders,
  applyInnerAuthQuery,
} from '../widget-proxy-core.js';
import { createWidgetUpstreamRegistry } from '../widget-upstream-registry.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_widget_proxy_core';
const PREFIX = 'test';

let client: MongoClient;
let db: Db;

beforeAll(async () => {
  client = new MongoClient(MONGO_URL);
  await client.connect();
  db = client.db(DB_NAME);
});

afterAll(async () => {
  await db.dropDatabase();
  await client.close();
});

beforeEach(async () => {
  await db.collection(`${PREFIX}_processes`).deleteMany({});
});

describe('resolveWidgetUpstream', () => {
  it('returns null when process is not found', async () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const missing = new ObjectId().toString();
    const result = await resolveWidgetUpstream(db, PREFIX, reg, missing);
    expect(result).toBeNull();
  });

  it('returns null when widgetUpstream is null', async () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: null,
    });

    const result = await resolveWidgetUpstream(db, PREFIX, reg, oid.toString());
    expect(result).toBeNull();
  });

  it('does NOT cache a negative lookup, so a later worker-side set_widget_upstream is seen on the next request', async () => {
    // Covers the regression where a relaunched process was 404'd in the
    // iframe until the 5s TTL expired because the dashboard loaded a cached
    // null from the previous session's teardown.
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: null,
    });

    const first = await resolveWidgetUpstream(db, PREFIX, reg, oid.toString());
    expect(first).toBeNull();

    // Worker registers upstream (simulating Python's set_widget_upstream).
    await db.collection(`${PREFIX}_processes`).updateOne(
      { _id: oid },
      { $set: { widgetUpstream: { url: 'http://127.0.0.1:9000', innerAuth: null } } },
    );

    const second = await resolveWidgetUpstream(db, PREFIX, reg, oid.toString());
    expect(second?.url).toBe('http://127.0.0.1:9000');
  });

  it('returns widgetUpstream when set and caches it', async () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const oid = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: oid, processId: 'p', name: 'P',
      rootId: oid, depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null }, log: [],
      cancellable: true,
      widgetUpstream: {
        url: 'http://127.0.0.1:9000',
        innerAuth: { kind: 'header', name: 'X-Tok', value: 's' },
      },
    });

    const first = await resolveWidgetUpstream(db, PREFIX, reg, oid.toString());
    expect(first?.url).toBe('http://127.0.0.1:9000');

    // Delete from DB; cache should still return the value on next call.
    await db.collection(`${PREFIX}_processes`).deleteOne({ _id: oid });
    const second = await resolveWidgetUpstream(db, PREFIX, reg, oid.toString());
    expect(second?.url).toBe('http://127.0.0.1:9000');
  });

  it('caches per (database, prefix) — same processId in two databases does not collide', async () => {
    const dbA = client.db('optio_test_widget_multidb_a');
    const dbB = client.db('optio_test_widget_multidb_b');
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    const oid = new ObjectId();

    try {
      const base = {
        _id: oid, processId: 'p', name: 'P',
        rootId: oid, depth: 0, order: 0,
        status: { state: 'running' },
        progress: { percent: null }, log: [],
        cancellable: true,
      };
      await dbA.collection(`${PREFIX}_processes`).insertOne({
        ...base,
        widgetUpstream: { url: 'http://upstream-A', innerAuth: null },
      });
      await dbB.collection(`${PREFIX}_processes`).insertOne({
        ...base,
        widgetUpstream: { url: 'http://upstream-B', innerAuth: null },
      });

      const a = await resolveWidgetUpstream(dbA, PREFIX, reg, oid.toString());
      const b = await resolveWidgetUpstream(dbB, PREFIX, reg, oid.toString());
      expect(a?.url).toBe('http://upstream-A');
      expect(b?.url).toBe('http://upstream-B');
    } finally {
      await dbA.dropDatabase();
      await dbB.dropDatabase();
    }
  });
});

describe('applyInnerAuthHeaders', () => {
  it('adds Authorization: Basic for BasicAuth', () => {
    const headers = applyInnerAuthHeaders(
      { kind: 'basic', username: 'u', password: 'p' },
      { host: 'x' },
    );
    expect(headers.authorization).toBe('Basic ' + Buffer.from('u:p').toString('base64'));
    expect(headers.host).toBe('x');
  });

  it('adds a custom header for HeaderAuth', () => {
    const headers = applyInnerAuthHeaders(
      { kind: 'header', name: 'X-Tok', value: 's' },
      {},
    );
    expect(headers['x-tok']).toBe('s');
  });

  it('is a no-op for null inner auth', () => {
    const h = applyInnerAuthHeaders(null, { foo: 'bar' });
    expect(h).toEqual({ foo: 'bar' });
  });

  it('does not modify headers for QueryAuth (that goes through URL)', () => {
    const h = applyInnerAuthHeaders(
      { kind: 'query', name: 'tok', value: 's' },
      { x: '1' },
    );
    expect(h).toEqual({ x: '1' });
  });
});

describe('applyInnerAuthQuery', () => {
  it('appends ?name=value for QueryAuth on a URL with no query', () => {
    const out = applyInnerAuthQuery(
      { kind: 'query', name: 'tok', value: 's' },
      '/foo/bar',
    );
    expect(out).toBe('/foo/bar?tok=s');
  });

  it('appends &name=value for QueryAuth on a URL with existing query', () => {
    const out = applyInnerAuthQuery(
      { kind: 'query', name: 'tok', value: 's' },
      '/foo?x=1',
    );
    expect(out).toBe('/foo?x=1&tok=s');
  });

  it('url-encodes the query value', () => {
    const out = applyInnerAuthQuery(
      { kind: 'query', name: 'tok', value: 'a b+c' },
      '/foo',
    );
    expect(out).toBe('/foo?tok=a%20b%2Bc');
  });

  it('is a no-op for non-query auth', () => {
    const out = applyInnerAuthQuery(
      { kind: 'header', name: 'X-Tok', value: 's' },
      '/foo',
    );
    expect(out).toBe('/foo');
  });
});
