# optio-api streams on change streams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** optio-api's list, tree, multi-tree and session-events streams read MongoDB only when a change is relevant to them, from one shared change stream per `(database, prefix)`, with one-second polling as the fallback.

**Architecture:** Pure relevance rules (`process-change-relevance.ts`) decide from a change event alone whether a stream must re-read. A `ProcessChangeHub` (`process-change-hub.ts`) owns one change stream per `(client, database, prefix)`, fans changes out to subscribers, throttles each subscriber's re-reads to one a second, and drives polling when change streams are unavailable or down. The four `create*Poller` functions keep their signatures and become hub subscribers.

**Tech Stack:** TypeScript, `mongodb` Node driver (`collection.watch`), vitest (fake timers for the hub; real MongoDB replica set for integration).

**Spec:** `docs/2026-10-09-api-change-streams-design.md`

## Global Constraints

- Event shapes, SSE routes, the `create*Poller` signatures and the queries they run are unchanged (the list projection stays).
- At most one re-read a second per stream (`minIntervalMs` default 1000; leading edge: a change after a quiet second reads at once).
- Polling mode: every subscriber re-reads every 1000 ms. Unavailable at open (no `setName` in `hello`, or error code 40573 or 13): polling, new attempt after 300 000 ms. Any other open or stream error: polling, reopen with backoff 1000 ms doubling to 30 000 ms; every (re)open makes every subscriber re-read once.
- No `fullDocument` lookup; `startAtOperationTime` from `hello.operationTime` (as excavator's agent-pool-stats source).
- Hub registry key: the `MongoClient` (`db.client`, WeakMap), then `` `${db.databaseName}\u0000${prefix}` ``.
- Exit hatch and test switch: environment variable `OPTIO_API_CHANGE_STREAMS=off` makes every hub poll (documented in optio-api's AGENTS.md).
- optio-api stays read-only (no writes, no index creation). Tests have no wall-clock dependence; integration waits use the existing `waitUntil(pred, 60_000)`.
- Tests run on the excavator host in `~/deai/optio/packages/optio-api` (node_modules live there; no `pnpm install` in another checkout). Editing optio-api's `src/` there restarts nothing; rebuilding its `dist/` does (excavator API under `tsx watch`) and happens only in Task 5, announced.
- No Co-Authored-By lines.

## Review Focus

- **A change that lands while a stream's re-read is in flight** must not be lost: the stream reads once more after the current read. Pinned in Task 2 (hub test: a change during a pending refresh produces one more refresh) and Task 3 (refreshes are serialized, a request during a read queues exactly one more read).
- **A burst of hundreds of changes** (a sync writing every step) must cost at most one read per second per stream. Pinned in Task 2 (throttle test with 50 changes in one second).
- **An SSE client that disconnects before the hub's stream opened** (`stop()` before `start()`'s subscribe resolved) must leave no subscription and no read behind. Pinned in Task 3.
- **The change stream closing because the collection is dropped** (resync clean, test teardown) must end in a reopen and a re-read, not a dead hub. Pinned in Task 2 (invalidate then error from the fake source → reopen + refresh all).
- **Many prefixes / databases in one API process** (multi-db mode) each get their own hub and stream; two streams on the same `(database, prefix)` share one. Pinned in Task 2 (registry test).

---

### Task 1: relevance rules

**Files:**
- Create: `packages/optio-api/src/process-change-relevance.ts`
- Modify: `packages/optio-api/src/stream-poller.ts` (import `LIST_PROJECTION` from the new module instead of defining it)
- Test: `packages/optio-api/src/__tests__/process-change-relevance.test.ts`

**Interfaces:**
- Produces:
  - `type ProcessChange = { op: 'insert' | 'replace'; id: string; doc: Record<string, unknown> } | { op: 'update'; id: string; fields: Set<string>; values: Record<string, unknown> } | { op: 'delete'; id: string } | { op: 'invalidate' }` -- `fields` are top-level keys of `updatedFields`, `removedFields` and `truncatedArrays[].field`; `values` is `updatedFields` as delivered.
  - `toProcessChange(ev: ChangeStreamDocument): ProcessChange | null` (`drop`, `rename`, `dropDatabase`, `invalidate` -> `{ op: 'invalidate' }`; other operation types -> `null`).
  - `LIST_PROJECTION` (moved here, unchanged).
  - `listRelevant(c: ProcessChange, members: Set<string>): boolean`
  - `treeRelevant(c: ProcessChange, roots: Set<string>, members: Set<string>): boolean`
  - `sessionRelevant(c: ProcessChange, sessionId: string, members: Set<string>): boolean`
  - Rules exactly as the spec's table; `invalidate` is relevant to all three.

- [ ] **Step 1: Write the failing tests** (hand-written events, no database):

```ts
// list (members = {'a'})
listRelevant(insert('x', {}), members) === true
listRelevant(update('a', ['status']), members) === true
listRelevant(update('a', ['progress']), members) === true
listRelevant(update('a', ['log']), members) === false
listRelevant(update('x', ['metadata']), members) === true      // may start matching
listRelevant(update('x', ['status']), members) === false
listRelevant(del('a'), members) === true
listRelevant(del('x'), members) === false
// tree (roots = {'r'}, members = {'r', 'c'})
treeRelevant(insert('n', { rootId: oid('r') }), roots, members) === true
treeRelevant(insert('n', { rootId: oid('other') }), roots, members) === false
treeRelevant(update('c', ['log']), roots, members) === true
// session (sessionId 's1', members = {'p'})
sessionRelevant(update('q', ['originatingSessionId'], { originatingSessionId: 's1' }), 's1', members) === true
sessionRelevant(update('q', ['originatingSessionId'], { originatingSessionId: 's2' }), 's1', members) === false
sessionRelevant(update('p', ['sessionEvents']), 's1', members) === true
sessionRelevant(update('p', ['status']), 's1', members) === false
// every rule: { op: 'invalidate' } === true
// toProcessChange: an update event with updatedFields {'progress.percent': 5, 'log.3': {...}}
//   and removedFields ['widgetData'] -> fields {'progress', 'log', 'widgetData'}; a 'drop' event -> invalidate
```

(`oid('r')` is an `ObjectId` whose hex is used as the member id; ids compare as hex strings.)

- [ ] **Step 2: Run, see them fail** -- `npx vitest run src/__tests__/process-change-relevance.test.ts`; expected: module not found.
- [ ] **Step 3: Implement** `process-change-relevance.ts`.
- [ ] **Step 4: Run it and `src/__tests__/stream-poller.test.ts`** -- expected PASS.
- [ ] **Step 5: Commit** -- `feat(optio-api): change relevance rules for the live streams`.

### Task 2: `ProcessChangeHub`

**Files:**
- Create: `packages/optio-api/src/process-change-hub.ts`
- Test: `packages/optio-api/src/__tests__/process-change-hub.test.ts`

**Interfaces:**
- Consumes: `ProcessChange`, `toProcessChange` (Task 1).
- Produces:
  - `class ChangeStreamsUnavailable extends Error`
  - `interface ProcessChangeSource { open(onChange: (c: ProcessChange) => void, onError: (err: unknown) => void): Promise<{ close(): Promise<void> }> }` -- rejects with `ChangeStreamsUnavailable` when the server cannot watch; after it resolves, `onError` is called once when the watch fails or ends.
  - `createMongoProcessChangeSource(db: Db, prefix: string): ProcessChangeSource` -- `hello` without `setName` or a watch error with code 40573 / 13 -> `ChangeStreamsUnavailable`; `watch([], { startAtOperationTime })` on `{prefix}_processes`; events through `toProcessChange`.
  - `interface HubSubscriber { isRelevant(c: ProcessChange): boolean; refresh(): void }`
  - `class ProcessChangeHub { constructor(source: ProcessChangeSource, opts?: { minIntervalMs?: number; pollIntervalMs?: number; retryUnavailableMs?: number; backoffMinMs?: number; backoffMaxMs?: number }); subscribe(sub: HubSubscriber): Promise<void>; unsubscribe(sub: HubSubscriber): void; readonly mode: 'idle' | 'opening' | 'streaming' | 'polling' }` -- `subscribe` resolves once the stream is open or the hub is polling; the hub calls `sub.refresh()` through a per-subscriber throttle (leading edge, `minIntervalMs`).
  - `getProcessChangeHub(db: Db, prefix: string): ProcessChangeHub` -- the registry; uses `createMongoProcessChangeSource`, or a source that always rejects with `ChangeStreamsUnavailable` when `OPTIO_API_CHANGE_STREAMS=off`.

- [ ] **Step 1: Write the failing tests** with a fake source (`open` resolves/rejects on demand; the test pushes changes and errors) and `vi.useFakeTimers()`:
  - `a change reaches only the subscribers it is relevant to`
  - `changes within a second give one refresh at its end; a change after a quiet second refreshes at once` -- 50 relevant changes in the first 1000 ms after a refresh: 1 more refresh at t=1000, none before
  - `a change while a refresh is pending gives exactly one more refresh`
  - `an unavailable source polls every second and tries again after 5 minutes` -- reject with `ChangeStreamsUnavailable`: `mode === 'polling'`, refreshes at 1000/2000/3000 ms; `open` called again at 300 000 ms; when that resolves, `mode === 'streaming'`, every subscriber refreshed once, polling stops
  - `an error reopens with backoff and refreshes everyone once` -- `onError` -> `mode === 'polling'`; `open` again at 1000 ms (then 2000, 4000 ... capped 30 000 while it keeps failing); after a successful reopen each subscriber refreshed once
  - `invalidate refreshes every subscriber` (then the source's `onError` -> reopen as above)
  - `the stream closes when the last subscriber leaves, and no timer remains` (`vi.getTimerCount() === 0`)
  - `the registry shares one hub per client, database and prefix` -- same `Db` twice -> same hub; `client.db('a')` and `client.db('a')` (two `Db` objects) -> same hub; other prefix or database -> other hub
- [ ] **Step 2: Run, see them fail** (module not found).
- [ ] **Step 3: Implement** `process-change-hub.ts`.
- [ ] **Step 4: Run** `npx vitest run src/__tests__/process-change-hub.test.ts` -- expected PASS.
- [ ] **Step 5: Commit** -- `feat(optio-api): ProcessChangeHub, one change stream per database and prefix`.

### Task 3: the streams subscribe to the hub

**Files:**
- Modify: `packages/optio-api/src/stream-poller.ts` (all four `create*Poller`)
- Test: create `packages/optio-api/src/__tests__/stream-poller-change-streams.test.ts`; existing `stream-poller.test.ts`, `stream-poller-scope.test.ts`, `session-events-poller.test.ts`, `adapters/__tests__/fastify-multi-stream.test.ts` unchanged

**Interfaces:**
- Consumes: `getProcessChangeHub`, `HubSubscriber`, `ProcessChangeHub` (Task 2); the relevance functions (Task 1).
- Produces: every `create*Poller` option type gains `hub?: ProcessChangeHub` (tests and custom wiring; default `getProcessChangeHub(db, prefix)`). Signatures otherwise unchanged.

Each poller keeps its `poll()` body (query, snapshot comparison, events) as `read()`, records the `_id` hex strings it returned as `members`, and becomes a `HubSubscriber`: `isRelevant` calls its rule with its members (tree: roots = `{rootId}`; multi-tree: roots = its `treeRoots`, members include `flatIds`); `refresh()` runs `read()` serialized -- a request during a read queues exactly one more. `start()`: `await hub.subscribe(sub)` then `read()`, unless `stop()` came first. `stop()`: `hub.unsubscribe(sub)`; no read starts afterwards. A failed read: `stop()` then `onError()`, as today.

- [ ] **Step 1: Write the failing tests in `stream-poller-change-streams.test.ts`** (real MongoDB; `describe.skipIf(!isReplicaSet)` with the reason in the title; hub = `new ProcessChangeHub(createMongoProcessChangeSource(db, PREFIX), { minIntervalMs: 0 })`; a recording `Db` proxy counts `find` calls as in `stream-poller.test.ts`):
  - `a tree stream re-reads for its own process, not for an unrelated one` -- after the first update event: update an unrelated root's `status`, then the tree root's `status`; wait for the second update event; `finds === 2`
  - `a list stream re-reads when a shown process changes status` -- second update event arrives with the new state
  - `a session-events stream picks up a process that joins the session` -- `$set originatingSessionId` then `$push sessionEvents`; the events arrive
  - `stop() before the hub opened leaves no subscription and no read` -- `start(); stop();` -> after `hub.subscribe` settles, `finds === 0` and the hub has no subscribers
- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement** in `stream-poller.ts`.
- [ ] **Step 4: Run the existing stream tests twice:** `npx vitest run src/__tests__/stream-poller*.test.ts src/__tests__/session-events-poller.test.ts src/adapters/__tests__/fastify-multi-stream.test.ts` and the same with `OPTIO_API_CHANGE_STREAMS=off` -- expected PASS both times; then the whole suite `npx vitest run` and `npx tsc --noEmit -p .` -- expected PASS.
- [ ] **Step 5: Commit** -- `feat(optio-api): live streams read on relevant changes instead of every second`.

### Task 4: documentation

**Files:**
- Modify: `packages/optio-api/AGENTS.md` ("Stream Poller": how streams learn about changes, the 1 s cap, polling fallback, `OPTIO_API_CHANGE_STREAMS=off`, the `hub` option), `AGENTS.md` (the optio-api "Stream Poller" paragraph: "Poll interval: 1000ms" replaced by the new behaviour)

- [ ] **Step 1:** Edit both; event shapes stay as documented.
- [ ] **Step 2: Commit** -- `docs(agents): optio-api streams on change streams`.

### Task 5: measure, land, release

- [ ] **Step 1: Baseline** on the excavator host, old code (the current `packages/optio-api/dist`): a harness (temporary file in `packages/optio-api`, removed afterwards) runs `createListPoller` (Plakomm filter), `createTreePoller` (a finished Plakomm sync root) and `createSessionEventsPoller` (an idle session id), 60 s each, with `appName` set; the profiler (`~/chat/idlecost/lp/prof3.py`) counts reads per stream. Expected: about 60 reads a minute each.
- [ ] **Step 2: New code** (`src/` through `tsx`), same harness. Expected: tree and session about 0 after the initial read; list about 24. Record both in `~/chat/idlecost/lp/change-streams-{before,after}.log`.
- [ ] **Step 3:** Final review of the branch, fixes, push `main` to GitHub.
- [ ] **Step 4: Report to the owner and wait for approval of the result.**
- [ ] **Step 5 (after approval):** release every optio package with commits since its last release tag, per `docs/release-cookbook.md` "Standard flow: release everything", in dependency order; then excavator's optio dependency bumps for every released package it uses, engine and API tests, commit, push. Announce the `dist` rebuilds (they restart the excavator API) in topics.log.
