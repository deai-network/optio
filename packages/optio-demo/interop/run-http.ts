/**
 * Stage B HTTP-roundtrip scenarios. Hits a fastify server registered
 * with optio-api against the same redis + mongo as the engine. Verifies
 * the full HTTP -> handler -> engine cache -> RPC -> engine chain.
 */
import IORedis from 'ioredis';
import { MongoClient } from 'mongodb';
import Fastify from 'fastify';
import { registerOptioApi } from 'optio-api/fastify';
import { createOptioTransports, OptioEngineClient } from 'optio-api';

const REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';
const MONGODB_URL = process.env.MONGODB_URL ?? 'mongodb://localhost:27017/optio-demo';
const PROC = 'opencode-demo';
const DATABASE = 'optio-demo';
const PREFIX = 'optio';
const SCENARIO_TIMEOUT_MS = 10_000;

const redis = new IORedis(REDIS_URL);
const mongoClient = new MongoClient(MONGODB_URL);
const transports = createOptioTransports(redis);
const engine = new OptioEngineClient(transports.get(DATABASE, PREFIX));
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

// Wait until the process is in one of the given states.
// Even though launch/cancel/dismiss are now synchronous RPCs (3a/3b/3c),
// the engine still drives the proc through additional state transitions
// asynchronously (e.g. scheduled -> running -> done|failed|cancelled),
// so polling the process state via GET remains useful for scenarios that
// need the proc in a specific async-arrived state.
async function waitForState(stateOrStates: string | string[], timeoutMs: number = 3000) {
  const states = Array.isArray(stateOrStates) ? stateOrStates : [stateOrStates];
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
  // Transports start lazily via createOptioTransports + transports.get().
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

    // 3b. Launch with resume=true on a proc whose supportsResume is false ->
    //     409 no-resume-support. Temporarily flips opencode-demo's
    //     supportsResume field on the proc doc; restores after.
    await withTimeout('http-launch-no-resume-support', async () => {
      await dismissIfTerminal();
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      const procsColl = db.collection('optio_processes');
      const original = await procsColl.findOne({ processId: PROC }, { projection: { supportsResume: 1 } });
      try {
        await procsColl.updateOne({ processId: PROC }, { $set: { supportsResume: false } });
        const r = await http('POST', `/processes/${PROC}/launch`, { resume: true });
        if (r.status !== 409)
          return fail('http-launch-no-resume-support', `expected 409, got ${r.status} ${JSON.stringify(r.body)}`);
        if (r.body?.reason !== 'no-resume-support')
          return fail('http-launch-no-resume-support', `expected reason 'no-resume-support', got ${r.body?.reason}`);
        ok('http-launch-no-resume-support');
      } finally {
        await procsColl.updateOne(
          { processId: PROC },
          { $set: { supportsResume: original?.supportsResume ?? true } },
        );
      }
    });

    // 3c. Persistent launch block matches every task; POST launch -> 409
    //     launch-blocked. Cleanup with unblockLaunches.
    await withTimeout('http-launch-launch-blocked', async () => {
      await dismissIfTerminal();
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      const block = await engine.blockLaunches({
        launchFilter: {},
        reason: 'phase-4-interop',
      });
      if (!block.ok) {
        return fail('http-launch-launch-blocked', `blockLaunches setup failed: reason=${block.reason}`);
      }
      try {
        const r = await http('POST', `/processes/${PROC}/launch`, {});
        if (r.status !== 409)
          return fail('http-launch-launch-blocked', `expected 409, got ${r.status} ${JSON.stringify(r.body)}`);
        if (r.body?.reason !== 'launch-blocked')
          return fail('http-launch-launch-blocked', `expected reason 'launch-blocked', got ${r.body?.reason}`);
        ok('http-launch-launch-blocked');
      } finally {
        await engine.unblockLaunches({ launchFilter: {} }).catch(() => null);
      }
    });

    // 4. Reset baseline: dismiss + launch. Dismiss is now a synchronous
    // RPC (3c), but the engine still drives proc state through async
    // transitions, so we poll until the process reaches a launchable state
    // before launching.
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

    // 7b. Cancel-during-cancel race. Validates the phase-4 (a-prime) SoT
    //     cleanup: engine no longer admits re-cancel on cancel_requested.
    //     Launch a running proc; cancel #1 returns 200 (state moves toward
    //     cancel_requested); cancel #2 (no async yield) returns 409
    //     not-cancellable instead of the old misleading 200 no-op.
    await withTimeout('http-cancel-during-cancel', async () => {
      await dismissIfTerminal();
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      const launchRes = await http('POST', `/processes/${PROC}/launch`, {});
      if (launchRes.status !== 200) {
        return fail('http-cancel-during-cancel', `pre-launch failed: ${launchRes.status} ${JSON.stringify(launchRes.body)}`);
      }
      try {
        await waitForState(['scheduled', 'running'], 2000);
      } catch {
        console.log('[scenario] http-cancel-during-cancel: skipped (proc reached terminal state too fast)');
        ok('http-cancel-during-cancel', 'skipped (terminal-fast)');
        return;
      }
      const cancel1 = await http('POST', `/processes/${PROC}/cancel`, {});
      if (cancel1.status !== 200) {
        return fail('http-cancel-during-cancel', `cancel #1 expected 200, got ${cancel1.status} ${JSON.stringify(cancel1.body)}`);
      }
      const cancel2 = await http('POST', `/processes/${PROC}/cancel`, {});
      if (cancel2.status !== 409) {
        return fail('http-cancel-during-cancel', `cancel #2 expected 409, got ${cancel2.status} ${JSON.stringify(cancel2.body)}`);
      }
      if (cancel2.body?.reason !== 'not-cancellable') {
        return fail('http-cancel-during-cancel', `cancel #2 expected reason 'not-cancellable', got ${cancel2.body?.reason}`);
      }
      ok('http-cancel-during-cancel');
    });

    // 8. Dismiss success: launch -> cancel -> wait for terminal -> dismiss.
    await withTimeout('http-dismiss-success', async () => {
      await dismissIfTerminal();
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      await http('POST', `/processes/${PROC}/launch`, {});
      await http('POST', `/processes/${PROC}/cancel`, {});
      // Wait for terminal state. Cancel may go through cancelling -> cancelled
      // or directly to cancelled/failed/done.
      await waitForState(['cancelled', 'failed', 'done']);
      const r = await http('POST', `/processes/${PROC}/dismiss`, {});
      if (r.status !== 200) return fail('http-dismiss-success', `expected 200, got ${r.status} ${JSON.stringify(r.body)}`);
      ok('http-dismiss-success', `state=${r.body.status?.state}`);
    });

    // 9. Dismiss running proc -> 409 not-dismissable. The opencode-demo task
    // fails fast in the interop env (no SSH host), so we may not always be
    // able to observe a non-terminal state; in that case the scenario falls
    // back to skipping (handler unit tests cover the pre-check path).
    await withTimeout('http-dismiss-not-dismissable', async () => {
      await dismissIfTerminal();
      await waitForState(['idle', 'done', 'failed', 'cancelled']);
      await http('POST', `/processes/${PROC}/launch`, {});
      try {
        await waitForState(['running', 'scheduled', 'cancelling'], 1000);
      } catch {
        // proc raced into a terminal state too fast — skip and rely on unit tests.
        console.log('[scenario] http-dismiss-not-dismissable: skipped (proc reached terminal state too fast)');
        ok('http-dismiss-not-dismissable', 'skipped (terminal-fast)');
        return;
      }
      const r = await http('POST', `/processes/${PROC}/dismiss`, {});
      if (r.status !== 409) return fail('http-dismiss-not-dismissable', `expected 409, got ${r.status} ${JSON.stringify(r.body)}`);
      if (r.body?.reason !== 'not-dismissable')
        return fail('http-dismiss-not-dismissable', `expected reason 'not-dismissable', got ${r.body?.reason}`);
      ok('http-dismiss-not-dismissable');
    });

    // 10. Dismiss nonexistent -> 404 not-found.
    await withTimeout('http-dismiss-not-found', async () => {
      const r = await http('POST', `/processes/bogus-dismiss-id/dismiss`, {});
      if (r.status !== 404) return fail('http-dismiss-not-found', `expected 404, got ${r.status}`);
      if (r.body?.reason !== 'not-found') return fail('http-dismiss-not-found', `expected reason 'not-found'`);
      ok('http-dismiss-not-found');
    });

    // 11. Resync notification.
    await withTimeout('http-resync', async () => {
      const r = await http('POST', `/processes/resync`, {});
      if (r.status !== 202) return fail('http-resync', `expected 202, got ${r.status}`);
      if (r.body?.message !== 'Resync requested') return fail('http-resync', `unexpected body ${JSON.stringify(r.body)}`);
      ok('http-resync');
    });

    // 12. Resync clean.
    await withTimeout('http-resync-clean', async () => {
      const r = await http('POST', `/processes/resync`, { clean: true });
      if (r.status !== 202) return fail('http-resync-clean', `expected 202, got ${r.status}`);
      if (r.body?.message !== 'Nuke and resync requested') return fail('http-resync-clean', `unexpected body ${JSON.stringify(r.body)}`);
      ok('http-resync-clean');
    });
  } finally {
    await app.close();
    await transports.closeAll().catch(() => null);
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
