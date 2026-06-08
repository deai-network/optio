import { describe, it, expect, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import { forwardAgentInput } from '../agent-input.js';

function fakeDb(upstream: unknown) {
  return {
    databaseName: 'testdb',
    collection: () => ({
      findOne: async () => (upstream === undefined ? null : { controlUpstream: upstream }),
    }),
  } as any;
}

const PID = new ObjectId().toHexString();

describe('forwardAgentInput', () => {
  it('404s when no controlUpstream is registered', async () => {
    const res = await forwardAgentInput(fakeDb(null), 'gm', PID, 'hi', vi.fn());
    expect(res.status).toBe(404);
  });

  it('400s on a malformed processId', async () => {
    const res = await forwardAgentInput(fakeDb({ url: 'http://e:1' }), 'gm', 'not-an-oid', 'hi', vi.fn());
    expect(res.status).toBe(400);
  });

  it('forwards POST to <url>/input and returns 200 on ok', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const res = await forwardAgentInput(
      fakeDb({ url: 'http://engine:7682', innerAuth: null }), 'gm', PID, 'hello', fetchImpl as any,
    );
    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('http://engine:7682/input');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ text: 'hello' });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true });
  });

  it('502s when the listener reports failure', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: false, reason: 'send-failed' }), { status: 502 }));
    const res = await forwardAgentInput(
      fakeDb({ url: 'http://engine:7682', innerAuth: null }), 'gm', PID, 'x', fetchImpl as any,
    );
    expect(res.status).toBe(502);
  });

  it('502s when the listener is unreachable', async () => {
    const fetchImpl = vi.fn(async () => { throw new Error('ECONNREFUSED'); });
    const res = await forwardAgentInput(
      fakeDb({ url: 'http://engine:7682', innerAuth: null }), 'gm', PID, 'x', fetchImpl as any,
    );
    expect(res.status).toBe(502);
  });
});
