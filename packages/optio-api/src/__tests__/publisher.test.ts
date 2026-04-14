import { describe, it, expect, beforeEach } from 'vitest';
import Redis from 'ioredis-mock';
import { getStreamName, publishLaunch, publishCancel, publishDismiss, publishResync } from '../publisher.js';

let redis: any;

beforeEach(async () => {
  redis = new Redis();
  await redis.flushall();
});

describe('getStreamName', () => {
  it('formats stream name as database/prefix:commands', () => {
    expect(getStreamName('mydb', 'optio')).toBe('mydb/optio:commands');
  });

  it('uses the provided database name', () => {
    expect(getStreamName('prod', 'jobs')).toBe('prod/jobs:commands');
  });
});

describe('publishLaunch', () => {
  it('writes to the database-scoped stream', async () => {
    await publishLaunch(redis, 'mydb', 'optio', 'task-1');
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    expect(entries).toHaveLength(1);
    const [, fields] = entries[0];
    expect(fields[fields.indexOf('type') + 1]).toBe('launch');
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.processId).toBe('task-1');
  });
});

describe('publishCancel', () => {
  it('writes cancel command to the database-scoped stream', async () => {
    await publishCancel(redis, 'mydb', 'optio', 'task-2');
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    expect(entries).toHaveLength(1);
    const [, fields] = entries[0];
    expect(fields[fields.indexOf('type') + 1]).toBe('cancel');
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.processId).toBe('task-2');
  });
});

describe('publishDismiss', () => {
  it('writes dismiss command to the database-scoped stream', async () => {
    await publishDismiss(redis, 'mydb', 'optio', 'task-3');
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    expect(entries).toHaveLength(1);
    const [, fields] = entries[0];
    expect(fields[fields.indexOf('type') + 1]).toBe('dismiss');
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.processId).toBe('task-3');
  });
});

describe('publishResync', () => {
  it('writes resync command with clean=false by default', async () => {
    await publishResync(redis, 'mydb', 'optio');
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    expect(entries).toHaveLength(1);
    const [, fields] = entries[0];
    expect(fields[fields.indexOf('type') + 1]).toBe('resync');
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.clean).toBe(false);
  });

  it('writes resync command with clean=true when specified', async () => {
    await publishResync(redis, 'mydb', 'optio', true);
    const entries = await redis.xrange('mydb/optio:commands', '-', '+');
    const [, fields] = entries[0];
    const payload = JSON.parse(fields[fields.indexOf('payload') + 1]);
    expect(payload.clean).toBe(true);
  });

  it('uses different stream for different database', async () => {
    await publishResync(redis, 'db-a', 'optio');
    await publishResync(redis, 'db-b', 'optio');
    const a = await redis.xrange('db-a/optio:commands', '-', '+');
    const b = await redis.xrange('db-b/optio:commands', '-', '+');
    expect(a).toHaveLength(1);
    expect(b).toHaveLength(1);
  });
});
