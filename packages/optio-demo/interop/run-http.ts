/**
 * Stage B HTTP-roundtrip scenarios. Hits a fastify server registered
 * with optio-api against the same redis + mongo as the engine. Verifies
 * the full HTTP -> handler -> engine cache -> RPC -> engine chain.
 */
import IORedis from 'ioredis';
import { MongoClient } from 'mongodb';
import Fastify from 'fastify';
import { registerOptioApi } from 'optio-api/fastify';

const REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';
const MONGODB_URL = process.env.MONGODB_URL ?? 'mongodb://localhost:27017/optio-demo';
const PROC = 'opencode-demo';
const SCENARIO_TIMEOUT_MS = 10_000;

const redis = new IORedis(REDIS_URL);
const mongoClient = new MongoClient(MONGODB_URL);
let baseUrl = '';
let exitCode = 0;

function fail(name: string, msg: string) {
  console.error(`✗ ${name}: ${msg}`);
  exitCode = 1;
}
function ok(name: string, info?: string) {
  console.log(`✓ ${name}${info ? ` (${info})` : ''}`);
}

async function withTimeout<T>(name: string, fn: () => Promise<T>): Promise<T> {
  const start = Date.now();
  console.log(`[scenario] ${name} started`);
  let settled = false;
  return await Promise.race<T>([
    fn().then((v) => {
      if (!settled) { settled = true; console.log(`[scenario] ${name} ok (${Date.now() - start}ms)`); }
      return v;
    }),
    new Promise<T>((_, reject) =>
      setTimeout(() => {
        if (!settled) { settled = true; reject(new Error(`[scenario] ${name} timed out after ${SCENARIO_TIMEOUT_MS}ms`)); }
      }, SCENARIO_TIMEOUT_MS),
    ),
  ]);
}

setTimeout(() => {
  console.error('[scenario] FATAL: 60s top-level timeout, exiting 15');
  process.exit(15);
}, 60_000).unref();

async function http(method: string, path: string, body?: unknown) {
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  return { status: res.status, body: json };
}

async function dismissIfTerminal() {
  await http('POST', `/processes/${PROC}/dismiss`).catch(() => null);
}

// Wait until the process is in an idle/launchable state.
// dismiss in this commit (3a) still publishes via the legacy redis stream
// and is async — the engine consumes it asynchronously. Polling the
// process state via GET avoids races. Once cancel/dismiss are migrated
// (3b/3c), this can be replaced with a simple synchronous dismiss + launch.
async function waitForState(states: string[], timeoutMs: number = 3000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const r = await http('GET', `/processes/${PROC}`).catch(() => null);
    if (r && r.status === 200 && states.includes(r.body?.status?.state)) return r.body;
    await new Promise((res) => setTimeout(res, 100));
  }
  throw new Error(`waitForState: timed out waiting for state in ${states.join(',')} after ${timeoutMs}ms`);
}

async function main() {
  await mongoClient.connect();
  const db = mongoClient.db('optio-demo');
  const app = Fastify();
  await registerOptioApi(app, { db, redis, prefix: 'optio', authenticate: () => 'operator' });
  await app.listen({ port: 0, host: '127.0.0.1' });
  const address = app.server.address();
  if (!address || typeof address !== 'object') throw new Error('fastify did not bind');
  baseUrl = `http://127.0.0.1:${address.port}/api`;
  console.log(`[http] listening on ${baseUrl}`);

  try {
    await dismissIfTerminal();

    // 1. Launch success.
    await withTimeout('http-launch-success', async () => {
      const r = await http('POST', `/processes/${PROC}/launch`, {});
      if (r.status !== 200) return fail('http-launch-success', `expected 200, got ${r.status} ${JSON.stringify(r.body)}`);
      if (typeof r.body?._id !== 'string') return fail('http-launch-success', `body missing _id: ${JSON.stringify(r.body)}`);
      ok('http-launch-success', `state=${r.body.status?.state}`);
    });

    // 2. Launch on running process -> 409 not-launchable.
    await withTimeout('http-launch-not-launchable', async () => {
      const r = await http('POST', `/processes/${PROC}/launch`, {});
      if (r.status !== 409) return fail('http-launch-not-launchable', `expected 409, got ${r.status}`);
      if (r.body?.reason !== 'not-launchable')
        return fail('http-launch-not-launchable', `expected reason 'not-launchable', got ${r.body?.reason}`);
      ok('http-launch-not-launchable');
    });

    // 3. Launch nonexistent processId -> 404 not-found.
    await withTimeout('http-launch-not-found', async () => {
      const r = await http('POST', `/processes/this-id-does-not-exist/launch`, {});
      if (r.status !== 404) return fail('http-launch-not-found', `expected 404, got ${r.status}`);
      if (r.body?.reason !== 'not-found')
        return fail('http-launch-not-found', `expected reason 'not-found', got ${r.body?.reason}`);
      ok('http-launch-not-found');
    });

    // 4. Reset baseline: dismiss + launch. Dismiss currently still flows
    // through the legacy redis stream and is async, so we poll until the
    // process reaches an idle/launchable state before launching.
    await dismissIfTerminal();
    await withTimeout('http-launch-baseline-reset', async () => {
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      const r = await http('POST', `/processes/${PROC}/launch`, {});
      if (r.status !== 200) return fail('http-launch-baseline-reset', `expected 200, got ${r.status} ${JSON.stringify(r.body)}`);
      ok('http-launch-baseline-reset');
    });

    // 5. Cancel success: launch then cancel immediately, while the proc is
    // still in scheduled/running. Cancel takes no body, but fastify rejects
    // empty bodies with application/json content-type, so pass {} like launch.
    // The opencode-demo task fails fast in the interop env (no SSH host), so
    // any delay between launch and cancel risks racing into a terminal state.
    await withTimeout('http-cancel-success', async () => {
      await dismissIfTerminal();
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      const launchRes = await http('POST', `/processes/${PROC}/launch`, {});
      if (launchRes.status !== 200) {
        return fail('http-cancel-success', `pre-launch failed: ${launchRes.status} ${JSON.stringify(launchRes.body)}`);
      }
      const r = await http('POST', `/processes/${PROC}/cancel`, {});
      if (r.status !== 200) return fail('http-cancel-success', `expected 200, got ${r.status} ${JSON.stringify(r.body)}`);
      ok('http-cancel-success', `state=${r.body.status?.state}`);
    });

    // 6. Cancel idle proc -> 409 not-cancellable.
    await withTimeout('http-cancel-not-cancellable', async () => {
      await dismissIfTerminal();
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      const r = await http('POST', `/processes/${PROC}/cancel`, {});
      if (r.status !== 409) return fail('http-cancel-not-cancellable', `expected 409, got ${r.status}`);
      if (r.body?.reason !== 'not-cancellable')
        return fail('http-cancel-not-cancellable', `expected reason 'not-cancellable', got ${r.body?.reason}`);
      ok('http-cancel-not-cancellable');
    });

    // 7. Cancel nonexistent -> 404 not-found.
    await withTimeout('http-cancel-not-found', async () => {
      const r = await http('POST', `/processes/bogus-cancel-id/cancel`, {});
      if (r.status !== 404) return fail('http-cancel-not-found', `expected 404, got ${r.status}`);
      if (r.body?.reason !== 'not-found') return fail('http-cancel-not-found', `expected reason 'not-found'`);
      ok('http-cancel-not-found');
    });
  } finally {
    await app.close();
    await redis.quit();
    await mongoClient.close();
  }
}

main()
  .then(() => process.exit(exitCode))
  .catch((e) => {
    console.error('[scenario] FATAL:', e);
    process.exit(15);
  });
