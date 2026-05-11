/**
 * End-to-end interop scenarios. Direct clamator client → optio-demo engine.
 * Verifies the clamator RPC wire works end-to-end against a live optio-demo
 * engine subprocess.
 *
 * Assumptions (set up by run-interop.sh before this script runs):
 *  - Redis is reachable at REDIS_URL (default redis://localhost:6379).
 *  - An optio-demo engine subprocess has been started with prefix=optio
 *    and database=optio-demo. Heartbeat key optio-demo/optio:heartbeat
 *    has been written.
 *  - At least one task in optio-demo declares processId=opencode-demo.
 */
import IORedis from 'ioredis';
import { createOptioTransports, OptioEngineClient } from 'optio-api';

const REDIS_URL = process.env.REDIS_URL ?? 'redis://localhost:6379';
const DATABASE = 'optio-demo';
const PREFIX = 'optio';
const PROC = 'opencode-demo';

const SCENARIO_TIMEOUT_MS = 5000;
const FORCE_HANG = process.env.INTEROP_FORCE_HANG;

async function withTimeout<T>(name: string, fn: () => Promise<T>): Promise<T> {
  const start = Date.now();
  console.log(`[scenario] ${name} started`);
  let settled = false;
  const work: Promise<T> = FORCE_HANG === name
    ? (async () => {
        console.error(`[scenario] ${name} HANG (forced via INTEROP_FORCE_HANG)`);
        return await new Promise<T>(() => {});  // never resolves
      })()
    : fn().then((v) => {
        if (!settled) {
          settled = true;
          console.log(`[scenario] ${name} ok (${Date.now() - start}ms)`);
        }
        return v;
      });
  return await Promise.race<T>([
    work,
    new Promise<T>((_, reject) =>
      setTimeout(() => {
        if (!settled) {
          settled = true;
          reject(new Error(`[scenario] ${name} timed out after ${SCENARIO_TIMEOUT_MS}ms`));
        }
      }, SCENARIO_TIMEOUT_MS),
    ),
  ]);
}

// Top-level safety net: kill the runner if main() hasn't returned in 60s.
setTimeout(() => {
  console.error('[scenario] FATAL: 60s top-level timeout, exiting 15');
  process.exit(15);
}, 60_000).unref();

const redis = new IORedis(REDIS_URL);
const transports = createOptioTransports(redis);
const engine = new OptioEngineClient(transports.get(DATABASE, PREFIX));

let exitCode = 0;
function fail(scenario: string, msg: string) {
  console.error(`✗ ${scenario}: ${msg}`);
  exitCode = 1;
}
function ok(scenario: string) {
  console.log(`✓ ${scenario}`);
}

async function dismissIfTerminal() {
  // Helper: leave the proc in 'idle' between scenarios.
  await engine.dismiss({ processId: PROC }).catch(() => null);
}

async function main() {
  // Transports are started lazily by createOptioTransports on first get().
  try {
    // Reset state baseline.
    await dismissIfTerminal();

    // 1. Launch success
    await withTimeout('launch-success', async () => {
      const r = await engine.launch({ processId: PROC });
      if (!r.ok) fail('launch success', `expected ok=true, got reason=${r.reason}`);
      else ok('launch success');
    });

    // 2. Launch on running → not-launchable
    await withTimeout('launch-not-launchable', async () => {
      const r = await engine.launch({ processId: PROC });
      if (r.ok) fail('launch not-launchable', 'expected ok=false');
      else if (r.reason !== 'not-launchable')
        fail('launch not-launchable', `expected reason=not-launchable, got ${r.reason}`);
      else ok('launch not-launchable');
    });

    // 3. Cancel success
    await withTimeout('cancel-success', async () => {
      const r = await engine.cancel({ processId: PROC });
      if (!r.ok) fail('cancel success', `expected ok=true, got reason=${r.reason}`);
      else ok('cancel success');
    });

    // Allow the cancel to fully propagate (scheduled→cancelled or
    // running→cancel_requested→cancelling→cancelled may take a few frames).
    await new Promise((res) => setTimeout(res, 500));

    // 4. Dismiss success
    await withTimeout('dismiss-success', async () => {
      const r = await engine.dismiss({ processId: PROC });
      if (!r.ok) fail('dismiss success', `expected ok=true, got reason=${r.reason}`);
      else ok('dismiss success');
    });

    // 5. Cancel idle → not-cancellable
    await withTimeout('cancel-not-cancellable', async () => {
      const r = await engine.cancel({ processId: PROC });
      if (r.ok) fail('cancel not-cancellable', 'expected ok=false');
      else if (r.reason !== 'not-cancellable')
        fail('cancel not-cancellable', `expected not-cancellable, got ${r.reason}`);
      else ok('cancel not-cancellable');
    });

    // 6. Dismiss idle → not-dismissable
    await withTimeout('dismiss-not-dismissable', async () => {
      const r = await engine.dismiss({ processId: PROC });
      if (r.ok) fail('dismiss not-dismissable', 'expected ok=false');
      else if (r.reason !== 'not-dismissable')
        fail('dismiss not-dismissable', `expected not-dismissable, got ${r.reason}`);
      else ok('dismiss not-dismissable');
    });

    // 7. Launch nonexistent
    await withTimeout('launch-not-found', async () => {
      const r = await engine.launch({ processId: 'no-such-process' });
      if (r.ok) fail('launch not-found', 'expected ok=false');
      else if (r.reason !== 'not-found')
        fail('launch not-found', `expected not-found, got ${r.reason}`);
      else ok('launch not-found');
    });

    // 8. Block / unblock cycle. Uses an empty filter ({}) which matches every
    // task — works regardless of whether opencode-demo carries metadata.
    await withTimeout('block-unblock-cycle', async () => {
      await dismissIfTerminal(); // ensure proc is idle / launchable.
      const block = await engine.blockLaunches({
        launchFilter: {},
        reason: 'phase-2-interop',
      });
      if (!block.ok) {
        fail('blockLaunches', `expected ok=true, got reason=${block.reason}`);
      } else {
        ok('blockLaunches');
        const launchBlocked = await engine.launch({ processId: PROC });
        if (launchBlocked.ok) {
          fail('launch-blocked', 'expected ok=false');
        } else if (launchBlocked.reason !== 'launch-blocked') {
          fail(
            'launch-blocked',
            `expected reason=launch-blocked, got ${launchBlocked.reason}`,
          );
        } else {
          ok('launch-blocked');
        }
        const unblock = await engine.unblockLaunches({ launchFilter: {} });
        if (unblock.removed < 1) fail('unblockLaunches', `expected removed>=1, got ${unblock.removed}`);
        else ok('unblockLaunches');

        // Re-launch should now succeed.
        const relaunch = await engine.launch({ processId: PROC });
        if (!relaunch.ok) fail('relaunch after unblock', `got reason=${relaunch.reason}`);
        else ok('relaunch after unblock');
      }
    });

    // 9. Resync notification
    await withTimeout('resync-notification', async () => {
      await engine.resync({});
      ok('resync notification (no-throw)');
    });

    // 10. groupCancel invalid persist
    await withTimeout('groupCancel-invalid-persist', async () => {
      const r = await engine.groupCancel({
        metadataFilter: { tag: 'demo' },
        persist: true,
      });
      if (r.ok) fail('groupCancel invalid-persist', 'expected ok=false');
      else if (r.reason !== 'invalid-persist-without-block')
        fail('groupCancel invalid-persist', `expected invalid-persist-without-block, got ${r.reason}`);
      else ok('groupCancel invalid-persist');
    });
  } finally {
    await transports.closeAll().catch(() => null);
    await redis.quit();
  }

  process.exit(exitCode);
}

main().catch((e) => {
  console.error('fatal:', e);
  process.exit(2);
});
