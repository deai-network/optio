# Multi-PID Process Tree Stream — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse N per-PID SSE connections to ONE shared connection per page via a multi-PID stream endpoint + React provider, eliminating the HTTP/1.1 per-origin connection cap that breaks the entity overview when targets ≥ 2.

**Architecture:** New optio-api endpoint `/api/processes/tree/multi/stream` takes two id lists (`treeIds` + `flatIds`), server fans in via a single mongo `$or` query per 1s poll, emits combined update / log / log-clear / resolution events. New optio-ui `MultiProcessStreamProvider` opens the single EventSource and dispatches per-PID slices via React context; existing `useProcessStream` and `useProcess` become context-aware with per-PID EventSource fallback for back-compat. Excavator's `EntityOverview` lifts PID collection to the page level and wraps its sync cards in the provider.

**Tech Stack:** TypeScript, fastify (optio-api fastify adapter), next.js-pages (alternate adapter), mongodb driver, vitest (optio-api + optio-ui tests), React 18, react-i18next, antd (excavator frontend), @testing-library/react + vitest (excavator frontend tests).

**Spec:** `docs/2026-05-21-multi-process-stream-design.md` (in optio repo).

**Three phases:**
- Phase 1: optio-api server changes
- Phase 2: optio-ui client changes
- Phase 3: optio release → excavator migration

---

## File Plan

**optio-api — modify:**
- `packages/optio-api/src/stream-poller.ts` — add `rootId` field to `createTreePoller` event payload; add `createMultiTreePoller` function
- `packages/optio-api/src/adapters/fastify.ts` — new endpoint route `/api/processes/tree/multi/stream`
- `packages/optio-api/src/adapters/nextjs-pages.ts` — same endpoint route (mirror)
- `packages/optio-api/src/__tests__/stream-poller.test.ts` — new tests for `createMultiTreePoller` + `rootId` field on `createTreePoller`

**optio-ui — modify:**
- Create: `packages/optio-ui/src/context/MultiProcessStreamContext.tsx` — context + provider component
- Create: `packages/optio-ui/src/__tests__/MultiProcessStreamProvider.test.tsx` — provider tests
- Modify: `packages/optio-ui/src/hooks/useProcessStream.ts` — context-aware with per-PID fallback
- Modify: `packages/optio-ui/src/hooks/useProcessQueries.ts` — `useProcess` context-aware
- Modify: `packages/optio-ui/src/index.ts` — export provider + context
- Modify: `packages/optio-ui/src/__tests__/useProcessStream.test.tsx` (or create) — context-routing tests

**Release:**
- `packages/optio-api/package.json` — version bump (release script)
- `packages/optio-ui/package.json` — version bump (release script)

**Excavator — modify:**
- `packages/frontend/package.json` — pin optio-api + optio-ui to new versions
- `packages/frontend/src/features/entities/components/EntityOverview.tsx` — lift PID collection, wrap cards in `MultiProcessStreamProvider`
- `packages/frontend/src/features/entities/components/__tests__/EntityOverview.test.tsx` — single-EventSource assertion + provider mock

---

## Phase 1 — optio-api server

### Task 1: Add `rootId` to `createTreePoller` event payload

**Files:**
- Modify: `packages/optio-api/src/stream-poller.ts:115-130` (per-process projection in `update` event)
- Test: `packages/optio-api/src/__tests__/stream-poller.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-api/src/__tests__/stream-poller.test.ts`:

```typescript
describe('createTreePoller rootId propagation', () => {
  it('includes rootId on every process in the update event', async () => {
    const events: any[] = [];
    const rootId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootId,
      processId: 'p-root', name: 'Root',
      rootId, parentId: null,
      depth: 0, order: 0,
      status: { state: 'running' },
      progress: { percent: null },
      cancellable: true,
      log: [],
    });
    const childId = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: childId,
      processId: 'p-child', name: 'Child',
      rootId, parentId: rootId,
      depth: 1, order: 0,
      status: { state: 'done' },
      progress: { percent: 100 },
      cancellable: false,
      log: [],
    });
    const poller = createTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'update') events.push(e); },
      onError: () => {},
      rootId: rootId.toString(),
      baseDepth: 0,
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    expect(events.length).toBeGreaterThan(0);
    const procs = events[0].processes;
    expect(procs).toHaveLength(2);
    expect(procs[0].rootId).toBe(rootId.toString());
    expect(procs[1].rootId).toBe(rootId.toString());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-api && pnpm vitest run src/__tests__/stream-poller.test.ts -t "rootId"`
Expected: FAIL — `procs[0].rootId` is `undefined`.

- [ ] **Step 3: Add `rootId` to the projection**

In `packages/optio-api/src/stream-poller.ts`, locate the `update` event projection inside `createTreePoller` (around line 113-130) and add `rootId`:

```typescript
sendEvent({
  type: 'update',
  processes: allProcs.map((p: any) => ({
    _id: p._id.toString(),
    parentId: p.parentId?.toString() ?? null,
    rootId: p.rootId?.toString() ?? null,
    name: p.name,
    status: p.status,
    progress: p.progress,
    cancellable: p.cancellable ?? false,
    depth: p.depth,
    order: p.order,
    widgetData: p.widgetData,
    uiWidget: p.uiWidget,
    supportsResume: p.supportsResume ?? false,
    hasSavedState: p.hasSavedState ?? false,
    metadata: p.metadata,
  })),
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-api && pnpm vitest run src/__tests__/stream-poller.test.ts`
Expected: PASS (new test + all existing).

- [ ] **Step 5: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-api/src/stream-poller.ts packages/optio-api/src/__tests__/stream-poller.test.ts
git commit -m "$(cat <<'EOF'
feat(optio-api): include rootId in tree-stream update payload

Adds rootId to every process row in the createTreePoller update
event. Single-PID consumers ignore it. The upcoming multi-PID stream
needs it for client-side per-root slice routing.
EOF
)"
```

---

### Task 2: `createMultiTreePoller` — server-side fan-in

**Files:**
- Modify: `packages/optio-api/src/stream-poller.ts` (append new function + types)
- Test: `packages/optio-api/src/__tests__/stream-poller.test.ts` (append)

- [ ] **Step 1: Write the failing test**

Append to `packages/optio-api/src/__tests__/stream-poller.test.ts`:

```typescript
describe('createMultiTreePoller', () => {
  it('emits combined update for multiple tree roots', async () => {
    const events: any[] = [];
    const rootA = new ObjectId(); const childA = new ObjectId();
    const rootB = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertMany([
      { _id: rootA, processId: 'pA', name: 'A', rootId: rootA, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
      { _id: childA, processId: 'pAC', name: 'A-child', rootId: rootA, parentId: rootA, depth: 1, order: 0, status: { state: 'done' }, progress: {}, cancellable: false, log: [] },
      { _id: rootB, processId: 'pB', name: 'B', rootId: rootB, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
    ]);
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'update') events.push(e); },
      onError: () => {},
      treeRoots: [
        { rootId: rootA, baseDepth: 0 },
        { rootId: rootB, baseDepth: 0 },
      ],
      flatIds: [],
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    expect(events.length).toBeGreaterThan(0);
    const procs = events[0].processes;
    // Both roots + childA included; sorted by depth then order
    const ids = procs.map((p: any) => p.processId).sort();
    expect(ids).toEqual(['pA', 'pAC', 'pB']);
    // rootId routing field present
    const rowA = procs.find((p: any) => p.processId === 'pA');
    const rowAC = procs.find((p: any) => p.processId === 'pAC');
    const rowB = procs.find((p: any) => p.processId === 'pB');
    expect(rowA.rootId).toBe(rootA.toString());
    expect(rowAC.rootId).toBe(rootA.toString());
    expect(rowB.rootId).toBe(rootB.toString());
  });

  it('flat ids fetch only the named row, not descendants', async () => {
    const events: any[] = [];
    const flatRoot = new ObjectId(); const flatChild = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertMany([
      { _id: flatRoot, processId: 'flatR', name: 'flat-root', rootId: flatRoot, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
      { _id: flatChild, processId: 'flatC', name: 'flat-child', rootId: flatRoot, parentId: flatRoot, depth: 1, order: 0, status: { state: 'running' }, progress: {}, cancellable: false, log: [] },
    ]);
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'update') events.push(e); },
      onError: () => {},
      treeRoots: [],
      flatIds: [flatRoot],
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    expect(events.length).toBeGreaterThan(0);
    const ids = events[0].processes.map((p: any) => p.processId);
    expect(ids).toEqual(['flatR']);
    expect(ids).not.toContain('flatC');
  });

  it('log events carry rootId for client routing', async () => {
    const logs: any[] = [];
    const rootA = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootA, processId: 'pA', name: 'A', rootId: rootA, parentId: null,
      depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true,
      log: [{ timestamp: '2026-05-21T00:00:00Z', level: 'info', message: 'hello' }],
    });
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'log') logs.push(e); },
      onError: () => {},
      treeRoots: [{ rootId: rootA, baseDepth: 0 }],
      flatIds: [],
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    expect(logs.length).toBeGreaterThan(0);
    expect(logs[0].entries[0].rootId).toBe(rootA.toString());
  });

  it('flat-id descendants do NOT emit log entries', async () => {
    const logs: any[] = [];
    const flatRoot = new ObjectId(); const flatChild = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertMany([
      { _id: flatRoot, processId: 'flatR', name: 'flat-root', rootId: flatRoot, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
      { _id: flatChild, processId: 'flatC', name: 'flat-child', rootId: flatRoot, parentId: flatRoot, depth: 1, order: 0, status: { state: 'running' }, progress: {}, cancellable: false, log: [{ timestamp: '2026-05-21T00:00:00Z', level: 'info', message: 'descendant log' }] },
    ]);
    const { createMultiTreePoller } = await import('../stream-poller.js');
    const poller = createMultiTreePoller({
      db, prefix: PREFIX,
      sendEvent: (e: any) => { if (e.type === 'log') logs.push(e); },
      onError: () => {},
      treeRoots: [],
      flatIds: [flatRoot],
    });
    poller.start();
    await new Promise((r) => setTimeout(r, 1100));
    poller.stop();

    // No log event should be emitted — descendant's log entries are out of scope for flat subscribers.
    expect(logs).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-api && pnpm vitest run src/__tests__/stream-poller.test.ts -t createMultiTreePoller`
Expected: FAIL — `createMultiTreePoller` not exported.

- [ ] **Step 3: Implement `createMultiTreePoller`**

Append to `packages/optio-api/src/stream-poller.ts`:

```typescript
export interface MultiTreeRoot {
  rootId: ObjectId;
  baseDepth: number;
}

export interface MultiTreePollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  treeRoots: MultiTreeRoot[];
  flatIds: ObjectId[];
  maxDepth?: number;
}

export function createMultiTreePoller(opts: MultiTreePollerOptions): ListPollerHandle {
  const { db, prefix, sendEvent, onError, treeRoots, flatIds, maxDepth } = opts;
  const col = db.collection(`${prefix}_processes`);
  let interval: ReturnType<typeof setInterval> | null = null;
  let lastSnapshot = '';
  const lastLogCounts = new Map<string, number>();
  let firstPoll = true;

  // Process ids whose descendants the multi-poller is allowed to surface in
  // logs. Flat-subscribed processes contribute log entries from themselves
  // ONLY (no descendants). Tree-subscribed roots contribute the root + all
  // descendants below them. The set of "loggable" rows is the union of:
  //   - any process whose rootId belongs to treeRoots
  //   - any process whose _id is in flatIds (root row only, no descendants)
  // Because flatIds is the row id itself and tree rows match via rootId,
  // membership is decided at row-projection time using the rootId field.
  const treeRootSet = new Set(treeRoots.map((r) => r.rootId.toString()));
  const flatRowSet = new Set(flatIds.map((id) => id.toString()));

  function isLoggableRow(p: any): boolean {
    if (treeRootSet.has(p.rootId?.toString())) return true;
    if (flatRowSet.has(p._id.toString())) return true;
    return false;
  }

  function isFlatOnlyRow(p: any): boolean {
    // A row reached via flatIds (root row only) — log entries from
    // descendants are NOT in scope here. But flatIds only ever match the
    // row's own _id, so "flat-only" simply means the row is in flatRowSet.
    // Descendants of a flat-only root are not even pulled by the query.
    return flatRowSet.has(p._id.toString()) && !treeRootSet.has(p.rootId?.toString());
  }

  async function poll() {
    try {
      const branches: Record<string, unknown>[] = [];
      if (treeRoots.length > 0) {
        branches.push({
          $or: treeRoots.map((r) => {
            const f: Record<string, unknown> = { rootId: r.rootId };
            if (maxDepth !== undefined) {
              f.depth = { $lte: r.baseDepth + maxDepth };
            }
            return f;
          }),
        });
      }
      if (flatIds.length > 0) {
        branches.push({ _id: { $in: flatIds } });
      }
      if (branches.length === 0) {
        // Nothing to watch — emit empty update once for connect feedback, then idle.
        return;
      }
      const filter = branches.length === 1 ? branches[0] : { $or: branches };

      const allProcs = await col.find(filter).sort({ depth: 1, order: 1 }).toArray();
      const snapshot = JSON.stringify(
        allProcs.map((p: any) => ({
          id: p._id, status: p.status, progress: p.progress,
          widgetData: p.widgetData, uiWidget: p.uiWidget,
          supportsResume: p.supportsResume ?? false,
          hasSavedState: p.hasSavedState ?? false,
          metadata: p.metadata,
        })),
      );

      if (snapshot !== lastSnapshot) {
        lastSnapshot = snapshot;
        sendEvent({
          type: 'update',
          processes: allProcs.map((p: any) => ({
            _id: p._id.toString(),
            parentId: p.parentId?.toString() ?? null,
            rootId: p.rootId?.toString() ?? null,
            processId: p.processId,
            name: p.name,
            status: p.status,
            progress: p.progress,
            cancellable: p.cancellable ?? false,
            depth: p.depth,
            order: p.order,
            widgetData: p.widgetData,
            uiWidget: p.uiWidget,
            supportsResume: p.supportsResume ?? false,
            hasSavedState: p.hasSavedState ?? false,
            metadata: p.metadata,
          })),
        });
      }

      // Per-row log diffs — only for loggable rows (treeRoot members or flat
      // root rows; descendants of flat-only roots are out of scope).
      let logClearedRoots = new Set<string>();
      const newLogEntries: any[] = [];
      for (const p of allProcs) {
        if (!isLoggableRow(p)) continue;
        if (isFlatOnlyRow(p)) {
          // Flat-only root row contributes its OWN log entries, not its
          // descendants'. Since we don't even pull descendants for flat ids,
          // this branch is informational — the loop below handles the row.
        }
        const pid = p._id.toString();
        const logLen = (p.log ?? []).length;
        const lastLen = lastLogCounts.get(pid) ?? 0;

        if (logLen < lastLen) {
          logClearedRoots.add(p.rootId?.toString() ?? '');
          lastLogCounts.set(pid, 0);
        }

        const effectiveLastLen = lastLogCounts.get(pid) ?? 0;
        if (logLen > effectiveLastLen) {
          const entries = (p.log ?? []).slice(firstPoll ? 0 : effectiveLastLen);
          for (const entry of entries) {
            newLogEntries.push({
              ...entry,
              processId: pid,
              processLabel: p.name,
              rootId: p.rootId?.toString() ?? null,
            });
          }
          lastLogCounts.set(pid, logLen);
        }
      }
      firstPoll = false;

      for (const rid of logClearedRoots) {
        sendEvent({ type: 'log-clear', rootId: rid });
      }
      if (newLogEntries.length > 0) {
        newLogEntries.sort(
          (a: any, b: any) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
        );
        sendEvent({ type: 'log', entries: newLogEntries });
      }
    } catch {
      stop();
      onError();
    }
  }

  function start() {
    interval = setInterval(poll, 1000);
  }

  function stop() {
    if (interval) {
      clearInterval(interval);
      interval = null;
    }
  }

  return { start, stop };
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd packages/optio-api && pnpm vitest run src/__tests__/stream-poller.test.ts`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-api/src/stream-poller.ts packages/optio-api/src/__tests__/stream-poller.test.ts
git commit -m "$(cat <<'EOF'
feat(optio-api): createMultiTreePoller for multi-PID stream

Generalization of createTreePoller. Accepts treeRoots (full subtree
per root) + flatIds (root row only). Single mongo query per poll
with combined $or branches. Events carry rootId for client routing;
log entries are scoped to in-tree rows; log-clear is per-root.
EOF
)"
```

---

### Task 3: Fastify adapter route `/api/processes/tree/multi/stream`

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts` (after the single-PID tree/stream route, line ~505)
- Test: `packages/optio-api/src/adapters/__tests__/fastify-multi-stream.test.ts` (new — integration test against a real fastify app)

- [ ] **Step 1: Check existing fastify integration test setup**

Run: `ls packages/optio-api/src/adapters/__tests__/ && head -30 packages/optio-api/src/adapters/__tests__/fastify-routes.test.ts 2>/dev/null || head -30 packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`
Note the harness: how the adapter is mounted, how requests are made.

If no `fastify-routes.test.ts` exists, write the test using fastify's `inject` API directly per the existing nextjs-app pattern, against a real mongo (same fixture as stream-poller tests).

- [ ] **Step 2: Write the failing test**

Create `packages/optio-api/src/adapters/__tests__/fastify-multi-stream.test.ts`:

```typescript
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { MongoClient, ObjectId, type Db } from 'mongodb';
import Fastify from 'fastify';
import { registerOptioApiFastify } from '../fastify.js';

const MONGO_URL = process.env.MONGO_URL ?? 'mongodb://localhost:27017';
const DB_NAME = 'optio_test_fastify_multi';
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

describe('GET /api/processes/tree/multi/stream', () => {
  it('emits resolution + update events for resolved tree + flat ids', async () => {
    const rootA = new ObjectId();
    const flatB = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertMany([
      { _id: rootA, processId: 'pA', name: 'A', rootId: rootA, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [] },
      { _id: flatB, processId: 'pB', name: 'B', rootId: flatB, parentId: null, depth: 0, order: 0, status: { state: 'done' }, progress: {}, cancellable: false, log: [] },
    ]);

    const app = Fastify();
    // The exact mounting call mirrors how excavator mounts in its own api package.
    // Look at packages/optio-api/src/adapters/fastify.ts top-level exports to find
    // the wiring function (e.g. registerOptioApiFastify / mountOptioApi) and use it
    // with the test db.
    // Replace this with the actual mount, e.g.:
    //   registerOptioApiFastify(app, { dbOpts: { mongoUrl: MONGO_URL, dbName: DB_NAME, prefix: PREFIX }, ... });

    // Fastify's inject() returns a Response-like; SSE bodies are partial. We
    // read the body as a stream and look for the first 'update' event line.
    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/tree/multi/stream?treeIds=pA&flatIds=pB&prefix=${PREFIX}&maxDepth=10`,
    });
    expect(res.statusCode).toBe(200);
    // SSE: data lines start with "data: ". Pull the first one.
    const body = res.body;
    expect(body).toMatch(/^data: /m);
    // First event should be either a resolution (with empty missing) or the
    // first update. Both are acceptable per the spec.
    const firstLine = body.split('\n').find((l) => l.startsWith('data: '));
    expect(firstLine).toBeTruthy();
    const firstEvent = JSON.parse(firstLine!.slice(6));
    expect(['resolution', 'update']).toContain(firstEvent.type);
    await app.close();
  });

  it('emits resolution event with missing ids when some do not resolve', async () => {
    const rootA = new ObjectId();
    await db.collection(`${PREFIX}_processes`).insertOne({
      _id: rootA, processId: 'pA', name: 'A', rootId: rootA, parentId: null, depth: 0, order: 0, status: { state: 'running' }, progress: {}, cancellable: true, log: [],
    });

    const app = Fastify();
    // Same wiring as the previous test.

    const res = await app.inject({
      method: 'GET',
      url: `/api/processes/tree/multi/stream?treeIds=pA,non-existent-pid&prefix=${PREFIX}`,
    });
    expect(res.statusCode).toBe(200);
    const resolutionLine = res.body.split('\n').find(
      (l) => l.startsWith('data: ') && l.includes('"type":"resolution"'),
    );
    expect(resolutionLine).toBeTruthy();
    const ev = JSON.parse(resolutionLine!.slice(6));
    expect(ev.missing).toContain('non-existent-pid');
    await app.close();
  });
});
```

NB: The exact mount-call signature differs between fastify wiring helpers. Before running, open `packages/optio-api/src/adapters/fastify.ts` top and find the actual exported registration function name + signature, and adapt the two test setup blocks accordingly. Do not invent a function name.

- [ ] **Step 3: Run test to verify failure**

Run: `cd packages/optio-api && pnpm vitest run src/adapters/__tests__/fastify-multi-stream.test.ts`
Expected: FAIL — route returns 404 or the resolution branch doesn't behave as asserted.

- [ ] **Step 4: Add the route to `fastify.ts`**

In `packages/optio-api/src/adapters/fastify.ts`, after the existing `/api/processes/:id/tree/stream` route (around line 505), add:

```typescript
  app.get('/api/processes/tree/multi/stream', async (request: any, reply: any) => {
    const rawQuery = (request.query as Record<string, unknown>) ?? {};
    const treeIdsParam = (rawQuery.treeIds as string | undefined) ?? '';
    const flatIdsParam = (rawQuery.flatIds as string | undefined) ?? '';
    const treeInputIds = treeIdsParam ? treeIdsParam.split(',').filter(Boolean) : [];
    const flatInputIds = flatIdsParam ? flatIdsParam.split(',').filter(Boolean) : [];
    if (treeInputIds.length === 0 && flatInputIds.length === 0) {
      reply.code(400).send({ message: 'treeIds or flatIds must be non-empty' });
      return;
    }

    let sseOpts;
    try {
      sseOpts = parseSseOptions(rawQuery);
    } catch (e) {
      reply.code(400).send({ message: (e as Error).message });
      return;
    }
    const { db, prefix } = resolveDb(dbOpts, sseOpts);
    const col = db.collection(`${prefix}_processes`);

    // Resolve each input id via the existing helper. Track which ids failed
    // to resolve so we can emit a `resolution` event with the missing list.
    async function resolveOne(id: string): Promise<{ id: string; proc: any | null }> {
      const proc = await findProcessByEitherId(col, id);
      return { id, proc };
    }
    const [treeResolved, flatResolved] = await Promise.all([
      Promise.all(treeInputIds.map(resolveOne)),
      Promise.all(flatInputIds.map(resolveOne)),
    ]);

    const missing: string[] = [];
    const treeRoots: { rootId: any; baseDepth: number }[] = [];
    const flatIds: any[] = [];
    for (const r of treeResolved) {
      if (!r.proc) missing.push(r.id);
      else treeRoots.push({ rootId: r.proc.rootId, baseDepth: r.proc.depth });
    }
    for (const r of flatResolved) {
      if (!r.proc) missing.push(r.id);
      else flatIds.push(r.proc._id);
    }

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    // Emit the resolution event first (always — empty missing list when all
    // ids resolved). Client can treat empty as "no-op."
    sendEvent({ type: 'resolution', missing });

    if (treeRoots.length === 0 && flatIds.length === 0) {
      // All ids missing. Stream stays open so the client retry / reconnect
      // logic works the same as for any other empty-poll state.
      request.raw.on('close', () => {});
      return;
    }

    const poller = createMultiTreePoller({
      db,
      prefix,
      sendEvent,
      onError: () => reply.raw.end(),
      treeRoots,
      flatIds,
      maxDepth: sseOpts.maxDepth,
    });
    poller.start();
    request.raw.on('close', () => poller.stop());
  });
```

Make sure `createMultiTreePoller` is in the import list at the top of `fastify.ts`:

```typescript
import { createListPoller, createTreePoller, createMultiTreePoller } from '../stream-poller.js';
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd packages/optio-api && pnpm vitest run src/adapters/__tests__/fastify-multi-stream.test.ts`
Expected: PASS.

Also run the entire optio-api suite to catch regressions:

Run: `cd packages/optio-api && pnpm vitest run`
Expected: All passing (or only pre-existing failures unrelated to your changes — record any).

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/adapters/__tests__/fastify-multi-stream.test.ts
git commit -m "$(cat <<'EOF'
feat(optio-api): GET /api/processes/tree/multi/stream fastify route

Accepts treeIds + flatIds (comma-separated processId or ObjectId
strings). Resolves via findProcessByEitherId, partitions into
treeRoots / flatIds, emits one resolution event with the missing
list, then runs createMultiTreePoller.
EOF
)"
```

---

### Task 4: Nextjs-pages adapter route (mirror)

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts` (after the single-PID tree/stream route)
- Test: optional — only mirror existing nextjs-app test patterns if present

- [ ] **Step 1: Look at existing tree-stream route in nextjs-pages**

Run: `grep -n 'tree/stream' packages/optio-api/src/adapters/nextjs-pages.ts`
Read the surrounding handler — that's the structure to mirror.

- [ ] **Step 2: Add the multi-stream route**

In `packages/optio-api/src/adapters/nextjs-pages.ts`, after the single-PID tree-stream branch (around line 178), add a new `if` branch:

```typescript
    // Match multi-stream: /api/processes/tree/multi/stream (path only; req.url has query)
    if (path === '/api/processes/tree/multi/stream' && method === 'GET') {
      const rawQuery = req.query as Record<string, unknown>;
      const treeIdsParam = (rawQuery.treeIds as string | undefined) ?? '';
      const flatIdsParam = (rawQuery.flatIds as string | undefined) ?? '';
      const treeInputIds = treeIdsParam ? treeIdsParam.split(',').filter(Boolean) : [];
      const flatInputIds = flatIdsParam ? flatIdsParam.split(',').filter(Boolean) : [];
      if (treeInputIds.length === 0 && flatInputIds.length === 0) {
        res.status(400).json({ message: 'treeIds or flatIds must be non-empty' });
        return;
      }

      let sseOpts;
      try {
        sseOpts = parseSseOptions(rawQuery);
      } catch (e) {
        res.status(400).json({ message: (e as Error).message });
        return;
      }
      const { db, prefix } = resolveDb(dbOpts, sseOpts);
      const col = db.collection(`${prefix}_processes`);

      const [treeResolved, flatResolved] = await Promise.all([
        Promise.all(treeInputIds.map(async (id) => ({ id, proc: await col.findOne({ _id: ObjectId.isValid(id) ? new ObjectId(id) : ({ $exists: false } as any) }) }))),
        Promise.all(flatInputIds.map(async (id) => ({ id, proc: await col.findOne({ _id: ObjectId.isValid(id) ? new ObjectId(id) : ({ $exists: false } as any) }) }))),
      ]);

      // findProcessByEitherId equivalent: ObjectId form OR processId match.
      // The nextjs-pages adapter currently only matches by ObjectId in the
      // single-PID route (line 150), so mirror that for consistency. If you
      // want processId resolution here too, switch to findProcessByEitherId
      // import and call it (the fastify variant does).

      const missing: string[] = [];
      const treeRoots: { rootId: any; baseDepth: number }[] = [];
      const flatIds: any[] = [];
      for (const r of treeResolved) {
        if (!r.proc) missing.push(r.id);
        else treeRoots.push({ rootId: r.proc.rootId, baseDepth: r.proc.depth });
      }
      for (const r of flatResolved) {
        if (!r.proc) missing.push(r.id);
        else flatIds.push(r.proc._id);
      }

      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      });
      const sendEvent = (data: unknown) => res.write(`data: ${JSON.stringify(data)}\n\n`);

      sendEvent({ type: 'resolution', missing });

      if (treeRoots.length === 0 && flatIds.length === 0) {
        req.on('close', () => {});
        return;
      }

      const poller = createMultiTreePoller({
        db, prefix,
        sendEvent,
        onError: () => res.end(),
        treeRoots, flatIds,
        maxDepth: sseOpts.maxDepth,
      });
      poller.start();
      req.on('close', () => poller.stop());
      return;
    }
```

Update imports at the top of the file to include `createMultiTreePoller`:

```typescript
import { createListPoller, createTreePoller, createMultiTreePoller } from '../stream-poller.js';
```

- [ ] **Step 3: Decide on processId support**

The fastify adapter accepts both ObjectId hex AND processId strings (via `findProcessByEitherId`). The existing nextjs-pages single-PID route at line 150 only accepts ObjectId. Match the existing nextjs-pages convention (ObjectId only) in the multi-stream route too — keep the two adapters consistent within themselves.

If the existing single-PID nextjs-pages route uses `findProcessByEitherId` (verify by running `grep findProcessByEitherId packages/optio-api/src/adapters/nextjs-pages.ts`), then use it in the multi-route too for consistency.

- [ ] **Step 4: Optional smoke test**

Run: `cd packages/optio-api && pnpm tsc --noEmit`
Expected: no errors.

(There's no existing nextjs-pages SSE integration test pattern to mirror; the fastify variant in Task 3 provides coverage. The nextjs-pages route is mostly mechanical mirroring.)

- [ ] **Step 5: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-api/src/adapters/nextjs-pages.ts
git commit -m "$(cat <<'EOF'
feat(optio-api): nextjs-pages multi-stream route

Mirror of the fastify route. Same treeIds/flatIds query interface,
same resolution event, same poller wiring.
EOF
)"
```

---

## Phase 2 — optio-ui client

### Task 5: `MultiProcessStreamContext` + provider component

**Files:**
- Create: `packages/optio-ui/src/context/MultiProcessStreamContext.tsx`
- Create: `packages/optio-ui/src/__tests__/MultiProcessStreamProvider.test.tsx`
- Modify: `packages/optio-ui/src/index.ts` (export)

- [ ] **Step 1: Look up existing patterns**

Run: `ls packages/optio-ui/src/context/ && grep -l 'createContext' packages/optio-ui/src/context/*.ts*`
Mirror the existing context structure.

- [ ] **Step 2: Write the failing test**

Create `packages/optio-ui/src/__tests__/MultiProcessStreamProvider.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, renderHook, act } from '@testing-library/react';
import React, { useContext } from 'react';
import {
  MultiProcessStreamProvider,
  MultiProcessStreamContext,
} from '../context/MultiProcessStreamContext.js';

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  readyState = 0;
  onopen: ((e: any) => void) | null = null;
  onmessage: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close() { this.closed = true; }
  emit(data: any) { this.onmessage?.({ data: JSON.stringify(data) } as any); }
  static reset() { this.instances = []; }
}

beforeEach(() => {
  MockEventSource.reset();
  (globalThis as any).EventSource = MockEventSource;
});

function consumer(pid: string) {
  return () => {
    const ctx = useContext(MultiProcessStreamContext);
    return ctx?.getSlice(pid) ?? null;
  };
}

// Minimal wrapper providing the optio context bits the provider needs.
// Mirror the existing setup used by other optio-ui tests (e.g. useProcessListStream).
function Wrapper({ children }: { children: React.ReactNode }) {
  // The actual OptioProvider setup: copy from existing tests (look at
  // useProcessListStream.test.tsx for the pattern).
  return <>{children}</>;
}

describe('MultiProcessStreamProvider', () => {
  it('opens exactly one EventSource for a non-empty pid set', () => {
    render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['a', 'b']} flatIds={['c']}>
          <div />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain('treeIds=a%2Cb');
    expect(MockEventSource.instances[0].url).toContain('flatIds=c');
  });

  it('reconnects (closes old EventSource, opens new) on pids prop change', () => {
    const { rerender } = render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['a']} flatIds={[]}>
          <div />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(MockEventSource.instances).toHaveLength(1);
    const firstES = MockEventSource.instances[0];

    rerender(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['a', 'b']} flatIds={[]}>
          <div />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(firstES.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(2);
  });

  it('exposes per-pid slices populated by update events', () => {
    let ctxRef: any = null;
    function Probe() {
      ctxRef = useContext(MultiProcessStreamContext);
      return null;
    }
    render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );

    act(() => {
      MockEventSource.instances[0].emit({
        type: 'update',
        processes: [
          { _id: 'oid-A', processId: 'pA', parentId: null, rootId: 'oid-A', name: 'A', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
          { _id: 'oid-AC', processId: 'pAC', parentId: 'oid-A', rootId: 'oid-A', name: 'A-child', status: { state: 'done' }, progress: {}, cancellable: false, depth: 1, order: 0, metadata: {} },
        ],
      });
    });

    const sliceA = ctxRef.getSlice('pA');
    expect(sliceA).not.toBeNull();
    expect(sliceA.rootProcess.processId).toBe('pA');
    expect(sliceA.processes).toHaveLength(2);
    expect(sliceA.tree?.processId).toBe('pA');
  });

  it('returns null slice for a pid the provider does not watch', () => {
    let ctxRef: any = null;
    function Probe() { ctxRef = useContext(MultiProcessStreamContext); return null; }
    render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    expect(ctxRef.getSlice('pUnknown')).toBeNull();
  });

  it('log-clear for one root does not affect another root logs', () => {
    let ctxRef: any = null;
    function Probe() { ctxRef = useContext(MultiProcessStreamContext); return null; }
    render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['pA', 'pB']} flatIds={[]}>
          <Probe />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    act(() => {
      MockEventSource.instances[0].emit({
        type: 'update',
        processes: [
          { _id: 'oid-A', processId: 'pA', parentId: null, rootId: 'oid-A', name: 'A', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
          { _id: 'oid-B', processId: 'pB', parentId: null, rootId: 'oid-B', name: 'B', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} },
        ],
      });
      MockEventSource.instances[0].emit({
        type: 'log',
        entries: [
          { processId: 'oid-A', processLabel: 'A', rootId: 'oid-A', timestamp: '2026-05-21T00:00:00Z', level: 'info', message: 'hello A' },
          { processId: 'oid-B', processLabel: 'B', rootId: 'oid-B', timestamp: '2026-05-21T00:00:01Z', level: 'info', message: 'hello B' },
        ],
      });
    });
    expect(ctxRef.getSlice('pA').logs).toHaveLength(1);
    expect(ctxRef.getSlice('pB').logs).toHaveLength(1);

    act(() => {
      MockEventSource.instances[0].emit({ type: 'log-clear', rootId: 'oid-A' });
    });
    expect(ctxRef.getSlice('pA').logs).toHaveLength(0);
    expect(ctxRef.getSlice('pB').logs).toHaveLength(1);
  });
});
```

The `Wrapper` placeholder must replicate optio-ui's existing test wrappers (probably `OptioProvider` from `useOptioContext`). Open `packages/optio-ui/src/__tests__/useProcessListStream.test.tsx` and copy its provider/wrapper setup before running this test.

- [ ] **Step 3: Run test to verify failure**

Run: `cd packages/optio-ui && pnpm vitest run src/__tests__/MultiProcessStreamProvider.test.tsx`
Expected: FAIL — `MultiProcessStreamProvider` not exported.

- [ ] **Step 4: Create the provider module**

Create `packages/optio-ui/src/context/MultiProcessStreamContext.tsx`:

```typescript
import React, { createContext, useEffect, useRef, useState, useCallback } from 'react';
import { useOptioPrefix, useOptioBaseUrl, useOptioDatabase } from './useOptioContext.js';

// Mirror the per-process row shape emitted by createTreePoller /
// createMultiTreePoller (after Task 1's rootId addition).
export interface ProcessUpdate {
  _id: string;
  processId: string;
  parentId: string | null;
  rootId: string | null;
  name: string;
  status: { state: string; [k: string]: unknown };
  progress: { percent?: number | null; message?: string | null };
  cancellable: boolean;
  depth: number;
  order: number;
  widgetData?: unknown;
  uiWidget?: unknown;
  supportsResume?: boolean;
  hasSavedState?: boolean;
  metadata?: Record<string, unknown>;
}

export interface LogEntry {
  processId: string;
  processLabel: string;
  rootId: string | null;
  timestamp: string;
  level: string;
  message: string;
}

export interface ProcessTreeNode extends ProcessUpdate {
  children: ProcessTreeNode[];
}

export interface ProcessStreamSlice {
  rootProcess: ProcessUpdate | null;
  processes: ProcessUpdate[];
  tree: ProcessTreeNode | null;
  logs: LogEntry[];
  connected: boolean;
  processNotFound: boolean;
  error: Error | null;
}

export interface MultiProcessStreamContextValue {
  getSlice: (processId: string) => ProcessStreamSlice | null;
  connected: boolean;
}

export const MultiProcessStreamContext =
  createContext<MultiProcessStreamContextValue | null>(null);

function buildTree(flat: ProcessUpdate[], rootProcessId: string): ProcessTreeNode | null {
  const root = flat.find((p) => p.processId === rootProcessId);
  if (!root) return null;
  const nodeMap = new Map<string, ProcessTreeNode>();
  for (const p of flat) nodeMap.set(p._id, { ...p, children: [] });
  for (const p of flat) {
    if (p.parentId && nodeMap.has(p.parentId)) {
      nodeMap.get(p.parentId)!.children.push(nodeMap.get(p._id)!);
    }
  }
  return nodeMap.get(root._id) ?? null;
}

export function MultiProcessStreamProvider({
  treeIds,
  flatIds,
  maxDepth = 10,
  children,
}: {
  treeIds: string[];
  flatIds: string[];
  maxDepth?: number;
  children: React.ReactNode;
}) {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const baseUrl = useOptioBaseUrl();

  // State buckets, keyed by processId (the root pid for each slice).
  const [processesByRootPid, setProcessesByRootPid] = useState<Record<string, ProcessUpdate[]>>({});
  const [logsByRootPid, setLogsByRootPid] = useState<Record<string, LogEntry[]>>({});
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [connected, setConnected] = useState(false);

  // Map from internal rootId (ObjectId hex string) → root processId string,
  // so update events (which carry rootId as a string) can be grouped by the
  // processId callers know.
  const rootIdToPidRef = useRef<Map<string, string>>(new Map());

  const treeIdsKey = treeIds.join(',');
  const flatIdsKey = flatIds.join(',');

  useEffect(() => {
    setProcessesByRootPid({});
    setLogsByRootPid({});
    setMissing(new Set());
    rootIdToPidRef.current = new Map();

    const params = new URLSearchParams();
    if (treeIdsKey) params.set('treeIds', treeIdsKey);
    if (flatIdsKey) params.set('flatIds', flatIdsKey);
    params.set('prefix', prefix);
    if (database) params.set('database', database);
    params.set('maxDepth', String(maxDepth));

    const url = `${baseUrl}/api/processes/tree/multi/stream?${params.toString()}`;
    const es = new EventSource(url);

    es.onopen = () => setConnected(true);
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === 'resolution') {
          setMissing(new Set(data.missing as string[]));
        } else if (data.type === 'update') {
          const procs: ProcessUpdate[] = data.processes;
          // Build rootId → pid map from the root rows (parentId == null AND
          // _id == rootId) so log events keyed by rootId can be routed.
          for (const p of procs) {
            if (p.parentId === null && p.rootId === p._id) {
              rootIdToPidRef.current.set(p._id, p.processId);
            }
          }
          // Group all rows by the root pid we were asked to watch.
          const byPid: Record<string, ProcessUpdate[]> = {};
          for (const p of procs) {
            const rootPid = p.rootId
              ? rootIdToPidRef.current.get(p.rootId)
              : undefined;
            // Tree-subscribed root rows have rootId === _id and parentId === null;
            // descendant rows have rootId pointing back to the root.
            // Flat-subscribed root rows are also their own rootId.
            // Match against treeIds OR flatIds (the pid lists supplied to the
            // provider). Drop rows the page is not asked to surface.
            const pidToBin = rootPid && (treeIds.includes(rootPid) || flatIds.includes(rootPid))
              ? rootPid
              : p.processId; // fallback: row is its own root (flat)
            if (!byPid[pidToBin]) byPid[pidToBin] = [];
            byPid[pidToBin].push(p);
          }
          setProcessesByRootPid(byPid);
        } else if (data.type === 'log') {
          const incoming: LogEntry[] = data.entries;
          setLogsByRootPid((prev) => {
            const next = { ...prev };
            for (const entry of incoming) {
              const rootPid = entry.rootId
                ? rootIdToPidRef.current.get(entry.rootId)
                : null;
              if (!rootPid) continue;
              if (!next[rootPid]) next[rootPid] = [];
              next[rootPid] = [...next[rootPid], entry];
            }
            return next;
          });
        } else if (data.type === 'log-clear') {
          const rootPid = data.rootId
            ? rootIdToPidRef.current.get(data.rootId)
            : null;
          if (rootPid) {
            setLogsByRootPid((prev) => ({ ...prev, [rootPid]: [] }));
          }
        }
      } catch { /* swallow malformed event */ }
    };
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
    };
  }, [treeIdsKey, flatIdsKey, maxDepth, prefix, database, baseUrl]);

  const getSlice = useCallback(
    (processId: string): ProcessStreamSlice | null => {
      const watched = treeIds.includes(processId) || flatIds.includes(processId);
      if (!watched) return null;
      const processes = processesByRootPid[processId] ?? [];
      const rootProcess = processes.find((p) => p.processId === processId) ?? null;
      const isTreeKind = treeIds.includes(processId);
      const tree = isTreeKind && rootProcess
        ? buildTree(processes, processId)
        : (rootProcess ? { ...rootProcess, children: [] as ProcessTreeNode[] } : null);
      return {
        rootProcess,
        processes,
        tree,
        logs: logsByRootPid[processId] ?? [],
        connected,
        processNotFound: missing.has(processId),
        error: null,
      };
    },
    [treeIds, flatIds, processesByRootPid, logsByRootPid, missing, connected],
  );

  const value: MultiProcessStreamContextValue = { getSlice, connected };
  return (
    <MultiProcessStreamContext.Provider value={value}>
      {children}
    </MultiProcessStreamContext.Provider>
  );
}
```

Export from `packages/optio-ui/src/index.ts`:

```typescript
export {
  MultiProcessStreamProvider,
  MultiProcessStreamContext,
} from './context/MultiProcessStreamContext.js';
export type {
  ProcessStreamSlice,
  ProcessUpdate,
  LogEntry,
  ProcessTreeNode,
} from './context/MultiProcessStreamContext.js';
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd packages/optio-ui && pnpm vitest run src/__tests__/MultiProcessStreamProvider.test.tsx`
Expected: PASS (all 5 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-ui/src/context/MultiProcessStreamContext.tsx packages/optio-ui/src/__tests__/MultiProcessStreamProvider.test.tsx packages/optio-ui/src/index.ts
git commit -m "$(cat <<'EOF'
feat(optio-ui): MultiProcessStreamProvider + context

Opens one EventSource to /api/processes/tree/multi/stream per prop
tuple. Reconnects on pids change. Exposes per-pid slice via context
(getSlice). Routes update / log / log-clear events to the right
slice by rootId. Tree slices are built locally via buildTree; flat
slices skip descendants.
EOF
)"
```

---

### Task 6: `useProcessStream` becomes context-aware

**Files:**
- Modify: `packages/optio-ui/src/hooks/useProcessStream.ts`
- Test: `packages/optio-ui/src/__tests__/useProcessStream.test.tsx` (or extend existing)

- [ ] **Step 1: Check existing useProcessStream test**

Run: `ls packages/optio-ui/src/__tests__/useProcessStream*.test.tsx`
Note the existing patterns; this task adds tests in the same file or a new one.

- [ ] **Step 2: Write the failing tests**

Append to (or create) `packages/optio-ui/src/__tests__/useProcessStream-context.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, renderHook, act } from '@testing-library/react';
import React from 'react';
import { MultiProcessStreamProvider } from '../context/MultiProcessStreamContext.js';
import { useProcessStream } from '../hooks/useProcessStream.js';

// Reuse the MockEventSource from MultiProcessStreamProvider.test.tsx — copy
// the class into this file (or extract into a shared test helper). Tracking
// EventSource construction calls is critical for these tests.
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: ((e: any) => void) | null = null;
  onmessage: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  closed = false;
  constructor(url: string) { this.url = url; MockEventSource.instances.push(this); }
  close() { this.closed = true; }
  emit(d: any) { this.onmessage?.({ data: JSON.stringify(d) } as any); }
  static reset() { this.instances = []; }
}

beforeEach(() => {
  MockEventSource.reset();
  (globalThis as any).EventSource = MockEventSource;
  // Reuse the existing test Wrapper for OptioProvider/baseUrl/prefix wiring.
});

describe('useProcessStream context awareness', () => {
  it('consumes provider slice without opening a per-PID EventSource when provider knows the pid', () => {
    function Inner() {
      const { rootProcess } = useProcessStream('pA');
      return <div data-testid="root">{rootProcess?.processId ?? 'none'}</div>;
    }

    const { getByTestId } = render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
          <Inner />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    // One EventSource opened by the provider; none by the hook.
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain('/multi/stream');

    act(() => {
      MockEventSource.instances[0].emit({
        type: 'update',
        processes: [{ _id: 'oid-A', processId: 'pA', parentId: null, rootId: 'oid-A', name: 'A', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} }],
      });
    });
    expect(getByTestId('root').textContent).toBe('pA');
  });

  it('falls back to per-PID EventSource when no provider mounted', () => {
    function Inner() {
      useProcessStream('pX');
      return null;
    }
    render(<Wrapper><Inner /></Wrapper>);
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain('/api/processes/pX/tree/stream');
  });

  it('falls back to per-PID EventSource when provider does not watch the pid', () => {
    function Inner() {
      useProcessStream('pNotWatched');
      return null;
    }
    render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={['pA']} flatIds={[]}>
          <Inner />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );
    // Provider opens one. Hook opens another for the un-watched pid.
    expect(MockEventSource.instances).toHaveLength(2);
    const urls = MockEventSource.instances.map((es) => es.url);
    expect(urls.some((u) => u.includes('/multi/stream'))).toBe(true);
    expect(urls.some((u) => u.includes('/api/processes/pNotWatched/tree/stream'))).toBe(true);
  });
});

function Wrapper({ children }: { children: React.ReactNode }) {
  // Copy from existing useProcessStream.test or useProcessListStream.test.
  return <>{children}</>;
}
```

- [ ] **Step 3: Run test to verify failure**

Run: `cd packages/optio-ui && pnpm vitest run src/__tests__/useProcessStream-context.test.tsx`
Expected: FAIL — first two cases pass by accident only if the existing hook conveniently works; the "no extra EventSource when in provider" case fails because the hook always opens its own SSE.

- [ ] **Step 4: Refactor `useProcessStream` for context-awareness**

Open `packages/optio-ui/src/hooks/useProcessStream.ts` and modify the function. Sketch (preserve all existing behavior in the fallback path):

```typescript
import { useContext, useEffect, useRef, useState, useMemo } from 'react';
import { MultiProcessStreamContext } from '../context/MultiProcessStreamContext.js';
// ... existing imports ...

export function useProcessStream(
  processId: string | undefined,
  maxDepth = 10,
): ProcessStreamResult {
  const ctx = useContext(MultiProcessStreamContext);

  // Always-called local fallback state (Rules of Hooks: every render must
  // call the same hooks regardless of whether the slice path is active).
  const [localState, setLocalState] = useState<ProcessStreamState>(initialLocalState);
  const eventSourceRef = useRef<EventSource | null>(null);
  // ... (keep existing local refs) ...

  const slice = ctx && processId ? ctx.getSlice(processId) : null;
  // True if we should NOT open a per-PID EventSource because the provider is
  // covering this pid.
  const sliceActive = !!slice;

  useEffect(() => {
    if (sliceActive || !processId) {
      // No-op — slice path is active OR no pid provided.
      return;
    }
    // ... EXISTING per-PID EventSource setup goes here (unchanged) ...
    return () => {
      eventSourceRef.current?.close();
      // ... existing cleanup ...
    };
  }, [processId, maxDepth, prefix, database, baseUrl, sliceActive]);

  // Return slice when the provider covers this pid; otherwise fall back to
  // local per-PID state.
  if (slice) {
    return {
      processes: slice.processes,
      tree: slice.tree,
      logs: slice.logs,
      connected: slice.connected,
      rootProcess: slice.rootProcess,
      processNotFound: slice.processNotFound,
      error: slice.error,
    };
  }

  // ... return localState shaped as ProcessStreamResult, same as before ...
  return {
    processes: localState.processes,
    tree: useMemo(() => buildTree(localState.processes), [localState.processes]),
    logs: localState.logs,
    connected: localState.connected,
    rootProcess: localState.processes.find((p) => p.depth === 0) ?? null,
    processNotFound: localState.processNotFound,
    error: localState.error,
  };
}
```

Critical Rule-of-Hooks discipline: every `useEffect`, `useState`, `useRef`, `useMemo` must be called on every render regardless of whether `slice` is null. The branch only changes the *return value* and the body of the effect.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd packages/optio-ui && pnpm vitest run`
Expected: PASS for the new context-awareness tests AND the existing useProcessStream tests.

- [ ] **Step 6: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-ui/src/hooks/useProcessStream.ts packages/optio-ui/src/__tests__/useProcessStream-context.test.tsx
git commit -m "$(cat <<'EOF'
feat(optio-ui): useProcessStream consumes provider slice when mounted

When MultiProcessStreamProvider covers the requested pid, the hook
returns the provider's slice without opening a per-PID EventSource.
When no provider is mounted (or it does not cover the pid), behavior
is unchanged — per-PID SSE fallback.
EOF
)"
```

---

### Task 7: `useProcess` consumes provider slice (flat-only path)

**Files:**
- Modify: `packages/optio-ui/src/hooks/useProcessQueries.ts:33-47` (the `useProcess` function)
- Test: `packages/optio-ui/src/__tests__/useProcess-context.test.tsx` (new)

- [ ] **Step 1: Write the failing test**

Create `packages/optio-ui/src/__tests__/useProcess-context.test.tsx`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';
import React from 'react';
import { MultiProcessStreamProvider } from '../context/MultiProcessStreamContext.js';
import { useProcess } from '../hooks/useProcessQueries.js';

// Reuse MockEventSource pattern from prior tests.

describe('useProcess context awareness', () => {
  it('returns provider slice rootProcess when pid is in flatIds', () => {
    function Inner() {
      const { process } = useProcess('pA');
      return <div data-testid="state">{process?.status?.state ?? 'none'}</div>;
    }
    const { getByTestId } = render(
      <Wrapper>
        <MultiProcessStreamProvider treeIds={[]} flatIds={['pA']}>
          <Inner />
        </MultiProcessStreamProvider>
      </Wrapper>,
    );

    act(() => {
      MockEventSource.instances[0].emit({
        type: 'update',
        processes: [{ _id: 'oid-A', processId: 'pA', parentId: null, rootId: 'oid-A', name: 'A', status: { state: 'running' }, progress: {}, cancellable: true, depth: 0, order: 0, metadata: {} }],
      });
    });
    expect(getByTestId('state').textContent).toBe('running');
  });
});

// MockEventSource + Wrapper boilerplate copied from prior tests.
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd packages/optio-ui && pnpm vitest run src/__tests__/useProcess-context.test.tsx`
Expected: FAIL — `useProcess` doesn't read context yet.

- [ ] **Step 3: Update `useProcess` to read context first**

In `packages/optio-ui/src/hooks/useProcessQueries.ts`, modify `useProcess`:

```typescript
import { useContext } from 'react';
import { MultiProcessStreamContext } from '../context/MultiProcessStreamContext.js';

export function useProcess(id: string | undefined, options?: { refetchInterval?: number | false }) {
  const ctx = useContext(MultiProcessStreamContext);
  const slice = ctx && id ? ctx.getSlice(id) : null;

  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const api = useOptioClient();
  const { data, isLoading } = api.processes.get.useQuery({
    queryKey: ['process', database, prefix, id],
    queryData: { params: { id: id! }, query: { database, prefix } },
    enabled: !!id && !slice, // skip polling when provider covers this pid
    refetchInterval: options?.refetchInterval ?? 5000,
  });

  if (slice) {
    return { process: slice.rootProcess, isLoading: false };
  }
  return {
    process: data?.status === 200 ? data.body : null,
    isLoading,
  };
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd packages/optio-ui && pnpm vitest run`
Expected: PASS all.

- [ ] **Step 5: Commit**

```bash
cd /home/csillag/deai/optio
git add packages/optio-ui/src/hooks/useProcessQueries.ts packages/optio-ui/src/__tests__/useProcess-context.test.tsx
git commit -m "$(cat <<'EOF'
feat(optio-ui): useProcess consumes provider slice when mounted

When MultiProcessStreamProvider covers the requested pid, useProcess
returns the slice's rootProcess and disables its 5s polling. Out of
provider, polling behavior is unchanged.
EOF
)"
```

---

## Phase 3 — Release + excavator migration

### Task 8: Release optio-api + optio-ui

**Files:**
- None (release script handles version + publish)

- [ ] **Step 1: Run full optio test suite**

Run: `cd /home/csillag/deai/optio && /home/csillag/deai/optio/.venv/bin/python -m pytest packages/optio-core/tests && pnpm -r test`
Expected: PASS (or only pre-existing flakes unrelated to this work).

- [ ] **Step 2: Release optio-api**

Run: `cd /home/csillag/deai/optio && make release-optio-api BUMP=patch`
Expected: New version published to npm. Capture the version from output (likely `0.1.x+1`).

- [ ] **Step 3: Release optio-ui**

Run: `cd /home/csillag/deai/optio && make release-optio-ui BUMP=patch`
Expected: New version published to npm. Capture the version.

- [ ] **Step 4: Wait for npm to index both packages**

Run: `until npm view optio-api@<new-version> version >/dev/null 2>&1 && npm view optio-ui@<new-version> version >/dev/null 2>&1; do sleep 5; done && echo available`
Expected: prints `available` once both versions are queryable.

(Substitute `<new-version>` with the versions captured in steps 2-3.)

---

### Task 9: Bump excavator's optio-api + optio-ui pins

**Files:**
- Modify: `packages/frontend/package.json`
- Modify: `packages/api/package.json` (only if it pins optio-api separately)

- [ ] **Step 1: Find current pins**

Run: `grep -rE '"optio-(api|ui)"' /home/csillag/deai/excavator/packages/*/package.json`
Note exact files + current versions.

- [ ] **Step 2: Update pins to the released versions**

Edit each `package.json` to bump the floor. Example (substitute the actual new version):

```json
{
  "dependencies": {
    "optio-ui": "^0.1.3"
  }
}
```

- [ ] **Step 3: Reinstall**

Run: `cd /home/csillag/deai/excavator && pnpm install`
Expected: lockfile updates without errors.

- [ ] **Step 4: Type check + test**

Run: `cd /home/csillag/deai/excavator/packages/frontend && pnpm tsc --noEmit && pnpm vitest run`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd /home/csillag/deai/excavator
git add packages/frontend/package.json pnpm-lock.yaml
# Add packages/api/package.json if changed
git commit -m "$(cat <<'EOF'
deps(frontend): bump optio-ui and optio-api for multi-stream provider

Picks up MultiProcessStreamProvider and the context-aware
useProcessStream / useProcess hooks. Migration of EntityOverview to
use the provider lands in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Migrate `EntityOverview` to `MultiProcessStreamProvider`

**Files:**
- Modify: `packages/frontend/src/features/entities/components/EntityOverview.tsx`
- Test: `packages/frontend/src/features/entities/components/__tests__/EntityOverview.test.tsx`

- [ ] **Step 1: Write the failing assertion: only ONE EventSource per page**

Open `packages/frontend/src/features/entities/components/__tests__/EntityOverview.test.tsx`. The current optio-ui mock replaces `useProcessStream` directly — for this test we want to count REAL EventSource opens, so we stop mocking `useProcessStream` for this case and instead let it run with a mock `EventSource` global. Alternatively, augment the optio-ui mock to expose a counter the test can read.

Simplest: mock `MultiProcessStreamProvider` to a render-pass-through that records the (treeIds, flatIds) it was given; mock `useProcessStream` / `useProcess` as before, but extend mocks to record which pids were requested. Assert:
- `MultiProcessStreamProvider` was mounted with `treeIds` containing both targets' real + dry pids
- `flatIds` contains the check pids
- `useProcessStream` was called for each card's PIDs (existing behavior preserved)

Append to the test file:

```typescript
const providerSpy = vi.fn();
vi.mock('optio-ui', async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    MultiProcessStreamProvider: ({ treeIds, flatIds, children }: any) => {
      providerSpy({ treeIds, flatIds });
      return children;
    },
    // Keep ProcessDetailView, ProcessItem, useProcessStream, useProcess mocks
    // already defined elsewhere in this test file.
  };
});

describe('EntityOverview multi-stream provider', () => {
  it('wraps cards in MultiProcessStreamProvider with the union of all targets pids', () => {
    providerSpy.mockClear();
    (useEntity as any).mockReturnValue({
      entity: { _id: 'e1', projectId: 'proj1', slug: 'users', targets: ['t1', 't2'] },
      isLoading: false,
    });
    renderEO();
    expect(providerSpy).toHaveBeenCalledTimes(1);
    const args = providerSpy.mock.calls[0][0];
    // Tree: real + dry for each target (ProcessDetailView needs descendants).
    expect(args.treeIds).toContain('proj1__entity-sync_e1_t1');
    expect(args.treeIds).toContain('proj1__entity-sync-dry_e1_t1');
    expect(args.treeIds).toContain('proj1__entity-sync_e1_t2');
    expect(args.treeIds).toContain('proj1__entity-sync-dry_e1_t2');
    // Flat: real (for header ProcessItem) + check (for the button).
    expect(args.flatIds).toContain('proj1__entity-sync_e1_t1');
    expect(args.flatIds).toContain('proj1__entity-sync-check_e1_t1');
    expect(args.flatIds).toContain('proj1__entity-sync_e1_t2');
    expect(args.flatIds).toContain('proj1__entity-sync-check_e1_t2');
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd packages/frontend && pnpm vitest run src/features/entities/components/__tests__/EntityOverview.test.tsx -t "multi-stream provider"`
Expected: FAIL — `providerSpy` not called (no provider wrapped yet).

- [ ] **Step 3: Wrap `EntityOverview`'s sync-card list in the provider**

Open `packages/frontend/src/features/entities/components/EntityOverview.tsx`. Modify the `EntityOverview` component (the export at the bottom of the file):

```tsx
import { MultiProcessStreamProvider } from 'optio-ui';

export function EntityOverview() {
  const { slug } = useParams<{ slug: string }>();
  const { entity } = useEntity(slug);
  const { sources } = useSourceList();
  const { progress } = useEntitySyncProgressQuery(entity?._id?.toString());
  const { t } = useTranslation();

  if (!entity) return null;

  const source = sources.find((s) => String(s._id) === String(entity.sourceId));
  const progressByTarget = new Map(
    progress.items.map((it) => [it.targetId, it]),
  );

  // Collect all PIDs we want streamed up front, so the provider opens a
  // single EventSource regardless of target count.
  const treeIds: string[] = [];
  const flatIds: string[] = [];
  for (const tid of entity.targets.map(String)) {
    const realPid = mkEntitySyncPid(entity.projectId, String(entity._id), tid);
    const dryPid = mkEntityDryRunSyncPid(entity.projectId, String(entity._id), tid);
    const checkPid = mkEntityCheckSyncPid(entity.projectId, String(entity._id), tid);
    // ProcessDetailView needs descendants for whichever tab is active;
    // include both so switching tabs does not require reconnect.
    treeIds.push(realPid, dryPid);
    // Header ProcessItem and check button only need root row + metadata.
    flatIds.push(realPid, checkPid);
  }

  return (
    <MultiProcessStreamProvider treeIds={treeIds} flatIds={flatIds}>
      <Space direction="vertical" size={16} style={{ display: 'flex' }}>
        {source && (
          <Card
            size="small"
            title={
              <Space>
                <span>{t('entities.detail.overview.source.title')}</span>
                <SourceLink source={source} />
              </Space>
            }
          />
        )}
        {entity.targets.map((tid: any) => {
          const tidStr = String(tid);
          const p = progressByTarget.get(tidStr);
          return (
            <TargetSyncCard
              key={tidStr}
              projectSlug={entity.projectId}
              entityId={String(entity._id)}
              targetId={tidStr}
              lastSyncStart={p?.lastSyncStart ?? null}
              lastSyncFinished={p?.lastSyncFinished ?? null}
              totalRecordsWritten={p?.totalRecordsWritten ?? 0}
              totalRecordsAtSource={p?.totalRecordsAtSource ?? null}
            />
          );
        })}
        <AvailableTargetsCard
          entity={{
            _id: String(entity._id),
            name: entity.name,
            targets: entity.targets.map(String),
          }}
        />
      </Space>
    </MultiProcessStreamProvider>
  );
}
```

The body of `TargetSyncCard` is unchanged — it still calls `useProcessStream(realPid)`, `<ProcessDetailView processId={activePid} />`, `useProcessStream(checkPid)`. All three transparently consume slices from the provider.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd packages/frontend && pnpm vitest run src/features/entities/components/__tests__/EntityOverview.test.tsx`
Expected: All passing (existing 15 + the new multi-stream test).

- [ ] **Step 5: Type check**

Run: `cd packages/frontend && pnpm tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Manual UI smoke test**

Start the frontend dev server (`make frontend` from repo root in a separate terminal), the API (`make backend-api`), and the engine (`make backend-engine`). Open an entity detail page with ≥2 targets (e.g. http://localhost:5173/entities/ticket). Switch the second card's Segmented to "Dry-run". Verify:

- Card body loads (no "Loading…" stall)
- DevTools → Network → filter "EventSource" shows exactly ONE open SSE connection to `/api/processes/tree/multi/stream`
- Switching tabs does not open additional EventSources

Report observations. Do NOT mark this step done unless the browser was actually opened and the EventSource count verified.

- [ ] **Step 7: Commit**

```bash
cd /home/csillag/deai/excavator
git add packages/frontend/src/features/entities/components/EntityOverview.tsx packages/frontend/src/features/entities/components/__tests__/EntityOverview.test.tsx
git commit -m "$(cat <<'EOF'
feat(entities): EntityOverview uses MultiProcessStreamProvider

Lifts PID collection from per-card to the page level and wraps the
sync card list in MultiProcessStreamProvider. The provider opens ONE
EventSource regardless of target count; all useProcessStream /
useProcess calls inside the cards consume slices from it. Fixes the
HTTP/1.1 per-origin connection cap that left later cards' Dry-run
tabs stuck on "Loading…" for entities with 2+ targets.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

---

## Self-Review

**1. Spec coverage:**

- §Scope/Design: `createMultiTreePoller` (Task 2), endpoint route fastify + nextjs-pages (Tasks 3-4), `rootId` field addition (Task 1), provider + context (Task 5), context-aware `useProcessStream` (Task 6), context-aware `useProcess` (Task 7), EntityOverview migration (Task 10), release chain (Tasks 8-9).
- §Wire-level event sequence: resolution event before update (Task 3 implementation), log routing with `rootId` (Task 2), per-root log-clear (Task 2).
- §Testing: server unit tests (Task 2), fastify route integration (Task 3), provider tests (Task 5), useProcessStream / useProcess context tests (Tasks 6-7), EntityOverview single-EventSource assertion (Task 10).

All spec sections mapped.

**2. Placeholder scan:**

- Task 3 step 2 has the `Wrapper` placeholder where the test must use the existing optio-api fastify-registration helper. Plan explicitly tells the engineer to read `fastify.ts` top for the actual function name and adapt — not a "TBD" but a "look up locally" instruction. Acceptable.
- Task 5 step 2 has the `Wrapper` placeholder where the test must reuse optio-ui's existing OptioProvider setup. Plan tells the engineer to copy from `useProcessListStream.test.tsx`. Acceptable — the harness can't be predicted without reading the file.
- Task 6 step 4 has a sketch with `// ... existing local refs ...` because the existing `useProcessStream.ts` has substantial setup that must be preserved verbatim. The plan instructs to keep it unchanged. Acceptable, but riskier than fully-inlined code — flag for execution-time care.

No TBD / TODO / "similar to Task N" / "add appropriate error handling" patterns found.

**3. Type consistency:**

- `treeIds: string[]` / `flatIds: string[]` consistent across provider, hook, EntityOverview.
- `ProcessUpdate` shape includes `rootId: string | null` consistent across server emit (Task 1, 2) and client decode (Task 5).
- `MultiProcessStreamContext` named consistently in Tasks 5, 6, 7.
- `MultiProcessStreamProvider` exported from `optio-ui` index (Task 5) and imported in Task 10.
- `findProcessByEitherId` used in fastify (Task 3); nextjs-pages (Task 4) acknowledges divergence and lets the engineer match local convention.

No type drift between tasks.

---

## Execution Handoff

**Plan complete and saved to `docs/2026-05-21-multi-process-stream-plan.md` (optio repo, flat per AGENTS.md). Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — batch tasks in this session with checkpoints.

**Which?**
