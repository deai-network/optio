# Multi-PID Process Tree Stream

This spec was written against the following baseline:

**Base revision:** `5c0df47b752e15997fc389349f856e64fb180081` on branch `main` (as of 2026-05-21T08:58:19Z)

## Summary

Replace N independent per-PID SSE connections with ONE shared SSE per
page. A new optio-api endpoint accepts a list of known PIDs at connect
time and emits a combined event stream covering all of them. A new
optio-ui provider opens that single connection and dispatches per-PID
slices to existing `useProcessStream` consumers via React context. The
existing per-PID endpoint remains for back-compat — `useProcessStream`
transparently falls back to a per-PID EventSource when no provider is
mounted.

Two subscription kinds per multi-stream call:

- **Tree subscribers** (`treeIds`): root + all descendants, with logs +
  widget data. Same payload shape as today's single-PID tree-stream.
- **Flat subscribers** (`flatIds`): only the named row, no descendants,
  no descendant logs. Cheap channel for ProcessItem-style consumers
  that need progress + status but not the recipe-run subtree.

## Motivation

Excavator's entity-overview page renders one `TargetSyncCard` per
target. Each card opens up to three SSE EventSources today:

- Header `ProcessItem` — streams the real sync's tree.
- `ProcessDetailView` (active tab) — streams the active (real or dry)
  sync's tree.
- Check-task button — streams the entity-sync-check process for
  enabled/disabled state.

For an entity with N targets that's roughly 3N concurrent EventSources.
Browsers cap HTTP/1.1 long-lived connections at ~6 per origin. With
N ≥ 2 the cap bites: switching the second card's Segmented to Dry-run
needs a fresh tree-stream connection that the browser queues forever,
and `ProcessDetailView` shows "Loading…" indefinitely. HTTP/2 would
relax the cap (multiplexing), but the dev stack runs HTTP/1.1 and we
want a solution that works on any transport, not one that depends on
infra upgrades.

A single multi-PID connection makes the page's SSE footprint constant
regardless of card count.

## Scope

In scope:

- New optio-api endpoint `/api/processes/tree/multi/stream` (fastify +
  nextjs-pages adapters)
- New `createMultiTreePoller` in `packages/optio-api/src/stream-poller.ts`
- Adding a `rootId` field to per-process event payloads (both
  multi-stream and existing single-PID tree-stream, so client routing
  logic can be uniform)
- New `MultiProcessStreamProvider` + React context in
  `packages/optio-ui/src/`
- `useProcessStream(pid)` becomes context-aware (back-compat fallback
  to per-PID EventSource when no provider is mounted)
- `useProcess(pid)` augmented to read provider's flat slice when a
  provider is mounted (otherwise its existing 5s polling behavior is
  unchanged)
- Excavator `EntityOverview` migration: wrap its sync-card list in the
  provider, supplying the union of real/dry/check PIDs
- Patch releases: `optio-api`, `optio-ui` (and `optio-contracts` only
  if a shared type is added)
- Excavator dependency bumps for the new versions

Out of scope:

- Replacing `useProcessListStream` (already a single global
  EventSource serving a different "all processes matching this
  metadata filter" use case)
- WebSocket-based bidirectional subscription (one-way SSE only; PIDs
  supplied at URL connect time)
- Server-side push instead of polling (still 1-second diff polling)
- Unifying multi-PID stream with list-stream (could be done later;
  YAGNI for now)
- Pages other than EntityOverview — they keep working via the
  fallback path

## Design

### Server — `createMultiTreePoller`

New polled SSE source in `packages/optio-api/src/stream-poller.ts`,
parallel to `createTreePoller`. Inputs:

```ts
interface MultiTreePollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;
  onError: () => void;
  treeRoots: { rootId: ObjectId; baseDepth: number }[];
  flatIds: ObjectId[];
  maxDepth?: number;
}
```

Per-poll query (one query, two `$or` branches):

```ts
find({
  $or: [
    // Tree branch: descendants of any tree root, depth-bounded per root.
    { $or: treeRoots.map(r => ({
        rootId: r.rootId,
        depth: { $lte: r.baseDepth + (maxDepth ?? Number.MAX_SAFE_INTEGER) },
      })) },
    // Flat branch: only the specific named rows.
    { _id: { $in: flatIds } },
  ],
}).sort({ depth: 1, order: 1 })
```

The two `$or` branches are unioned at the top level, so a row matching
both (a tree-root's own row that also appears in `flatIds`) is
returned once.

Each emitted process row carries `rootId` in addition to the existing
fields, so the client can route by rootId without walking the parent
chain:

```ts
{
  _id, processId, parentId,
  rootId,  // ← new field, also added to single-PID TreePoller for consistency
  name, status, progress, cancellable, depth, order,
  widgetData, uiWidget, supportsResume, hasSavedState, metadata,
}
```

Event shape:

- `{ type: 'update', processes: [...] }` — combined snapshot, emitted
  on any diff. Same diff-suppression logic as `createTreePoller` (last
  serialized snapshot compared to current).
- `{ type: 'log', entries: [...] }` — log entries are already tagged
  with `processId` + `processLabel`. Add `rootId` per entry so the
  client routes to the correct slice.
- `{ type: 'log-clear', rootId }` — scoped to one root. Client clears
  only that root's logs slice.

Endpoint route (added to both `fastify.ts` and `nextjs-pages.ts`
adapters):

```
GET /api/processes/tree/multi/stream
    ?treeIds=<comma-separated ids>
    &flatIds=<comma-separated ids>
    &maxDepth=<integer>
    &prefix=<string>
    &database=<string>
```

Either of `treeIds` or `flatIds` may be empty; at least one must be
non-empty.

Each id is resolved via `findProcessByEitherId` (existing helper) —
accepts ObjectId hex or processId string, same as the single-PID
endpoint. Server runs all resolutions in parallel via `Promise.all`,
partitions resolved docs into `treeRoots` (with their `baseDepth =
proc.depth`) and `flatIds` (using `_id`), then passes to
`createMultiTreePoller`.

**Resolution failure model:** if any id resolves to no doc, the
server emits a single `{ type: 'resolution', missing: [<id>, ...] }`
event before the polling loop starts. Client provider exposes
`missing` so per-PID consumers whose id is in the list can render
"Process not found." The poller continues for the resolved ids — one
missing id does not break the stream for the rest.

### Client — `MultiProcessStreamProvider`

New React context provider in `packages/optio-ui/src/context/`.

Props:

```ts
interface MultiProcessStreamProviderProps {
  treeIds: string[];   // processIds wanting full tree
  flatIds: string[];   // processIds wanting only the root row
  maxDepth?: number;   // applied per tree root; default 10
  children: ReactNode;
}
```

Internals:

- Opens one `EventSource` to the multi-stream endpoint with the
  current `treeIds`/`flatIds`/`maxDepth`/`prefix`/`database`.
- Holds state in a `Map<rootProcessId, ProcessStreamSlice>` keyed by
  the root id. Each slice carries the same shape as today's
  `useProcessStream` return value: `{ rootProcess, processes, tree,
  logs, connected, processNotFound, error }`.
- For tree roots, `processes` contains the root + descendants
  belonging to that root; `tree` is built locally via `buildTree`.
- For flat roots, `processes` contains only the root row;
  `tree.children` is empty.
- Reconnects on prop change — `useEffect` deps include
  `treeIdsKey = treeIds.join(',')`, `flatIdsKey = flatIds.join(',')`,
  `maxDepth`, `prefix`, `database`, `baseUrl`. Cleanup closes the
  EventSource; effect body opens a new one. Slice state resets to
  empty between reconnects.

Context value exposes:

```ts
interface MultiProcessStreamContextValue {
  getSlice: (processId: string) => ProcessStreamSlice | null;
  connected: boolean;
}
```

`getSlice` returns the slice if the provider was asked to watch that
id; otherwise `null`. Callers must not assume membership — the
fallback path inside `useProcessStream` handles the null case.

### Client — `useProcessStream` becomes context-aware

```ts
export function useProcessStream(
  processId: string | undefined,
  maxDepth: number = 10,
): ProcessStreamResult {
  const ctx = useContext(MultiProcessStreamContext);
  const slice = ctx && processId ? ctx.getSlice(processId) : null;
  const fallbackActive = !slice && !!processId;

  // ... unconditional hook calls below for Rules of Hooks ...
  // Per-PID EventSource setup happens inside a useEffect that
  // short-circuits when `fallbackActive` is false. State updates
  // from that effect feed a local state object; when the slice path
  // is active, the local state is ignored.

  return slice ?? localFallbackResult;
}
```

Three resolution outcomes inside the hook:

1. `ctx === null` → fallback per-PID EventSource (current behavior).
2. `ctx !== null && ctx.getSlice(processId)` returns a slice → consume
   slice, suppress per-PID EventSource setup.
3. `ctx !== null && ctx.getSlice(processId)` returns `null` (the
   provider exists but doesn't track this PID) → same as case 1,
   per-PID fallback. Lets a page nest one-off consumers below the
   provider without forcing them into the union.

### Client — `useProcess` (flat-only) consumes provider slice

`useProcess` already exists as a 5s react-query polling hook. Augment
it: if `MultiProcessStreamContext` is mounted and includes this PID,
return the slice's `rootProcess` (push-driven updates). Otherwise,
fall through to existing polling.

This is a transparent latency improvement for in-provider consumers
(the check button being the immediate beneficiary) without breaking
out-of-tree consumers.

### Excavator migration — `EntityOverview`

Move PID collection from `TargetSyncCard` up to `EntityOverview`:

```tsx
export function EntityOverview() {
  // ... existing entity/source/progress queries ...

  const treeIds: string[] = [];
  const flatIds: string[] = [];
  for (const tid of (entity?.targets ?? []).map(String)) {
    const realPid = mkEntitySyncPid(entity!.projectId, String(entity!._id), tid);
    const dryPid = mkEntityDryRunSyncPid(entity!.projectId, String(entity!._id), tid);
    const checkPid = mkEntityCheckSyncPid(entity!.projectId, String(entity!._id), tid);
    treeIds.push(realPid, dryPid);  // ProcessDetailView needs descendants
    flatIds.push(realPid, checkPid); // Header ProcessItem + check button
  }

  return (
    <MultiProcessStreamProvider treeIds={treeIds} flatIds={flatIds}>
      {/* existing layout */}
    </MultiProcessStreamProvider>
  );
}
```

`TargetSyncCard` body unchanged. All three `useProcessStream` calls
inside it (`realPid`, `activePid` via ProcessDetailView, `checkPid`)
transparently consume slices from the provider.

`realPid` is in both `treeIds` (for `ProcessDetailView` when the real
tab is active) and `flatIds` (for the header `ProcessItem`). Server
dedupes via mongo `$or` semantics. Client provider sees one row per
processId and uses it to populate both slices.

### Wire-level event sequence on connect

1. Client opens `EventSource`.
2. Server resolves all ids; if any missing, emits one
   `{ type: 'resolution', missing: [...] }`.
3. Server runs the poll loop. First poll emits a full
   `{ type: 'update', processes: [...] }`.
4. If any process has a non-empty log, emit a
   `{ type: 'log', entries: [...] }` event with the initial entries.
5. Subsequent polls emit `update` only on diff, `log` for new entries,
   `log-clear` on a per-root log shrink.

### Adding `rootId` to single-PID TreePoller for consistency

The single-PID `createTreePoller` does not currently emit `rootId` per
process. Add it (one extra field on the projection) so the client's
slice-routing logic is identical between provider-driven and fallback
paths. No behavior change for existing single-PID consumers — extra
field is ignored by callers that don't use it.

## State / persistence

None. This is a read-only streaming endpoint. No schema changes, no
new collections, no migrations.

## Error handling

- **Mongo query failure mid-poll:** server calls `onError` (closes the
  HTTP response), client `onerror` fires, EventSource retry kicks in.
  Same model as existing pollers.
- **Partial resolution failure** (some ids missing): emit `resolution`
  event, continue with the rest. Per-PID consumers whose id is missing
  see `processNotFound = true` in their slice; their UI renders
  "Process not found." rather than "Loading…".
- **All ids missing:** emit `resolution { missing: [<all>] }`, then no
  poll loop. EventSource stays open (the client may reconnect with a
  different set later). Alternative considered: 404 the request; not
  chosen because the resolution event lets the client distinguish
  per-id "not found" cleanly.
- **Reconnect during prop change:** state slices reset to empty
  between reconnects so a removed id's stale data doesn't linger.
- **Browser closing connection** (page navigation, EventSource
  garbage-collected): server's `request.raw.on('close')` cleans up the
  poller interval. Same path as single-PID.

## Cancellation

No active cancellation logic — streaming is observation only. The
poller stops when the HTTP request closes.

## Testing

### Server (optio-api)

Unit tests in `packages/optio-api/tests/`:

- `createMultiTreePoller` with one tree root + one flat id: emits one
  `update` per snapshot diff; flat row included; descendants of the
  tree root included; flat id's descendants NOT included.
- `rootId` field present on every emitted process.
- Log routing: log added to a process under tree root A emits with
  `rootId: A`; not emitted for flat ids.
- `log-clear` scoped to a single root.
- Resolution: mixed ObjectId hex + processId string in `treeIds` /
  `flatIds` resolves correctly.
- Partial-missing resolution emits the `resolution` event and proceeds
  with the resolved ids.

Integration tests:

- Fastify adapter route accepts both id formats in both lists.
- nextjs-pages adapter likewise.

### Client (optio-ui)

Unit tests in `packages/optio-ui/src/__tests__/`:

- `MultiProcessStreamProvider` opens exactly ONE EventSource per
  (treeIds, flatIds, maxDepth) tuple; opens a NEW one on prop change;
  closes the old one in cleanup.
- `getSlice(pid)` returns the slice for a watched PID and `null` for
  an unwatched one.
- `useProcessStream(pid)`:
  - With provider + pid in scope → consumes slice, no per-PID
    EventSource opened (mock `EventSource` constructor; assert zero
    additional calls beyond the provider's one).
  - With provider + pid NOT in scope → opens per-PID EventSource
    (fallback).
  - Without provider → opens per-PID EventSource (current behavior).
- `useProcess(pid)`:
  - With provider + pid in flat scope → returns slice rootProcess.
  - Without provider → 5s polling, no change.

### End-to-end (excavator)

Tests in
`packages/frontend/src/features/entities/components/__tests__/EntityOverview.test.tsx`:

- Stubbed `EventSource` constructor. Render `EntityOverview` with a
  2-target entity. Assert `EventSource` constructed exactly ONCE.
- URL of the single EventSource contains all expected PIDs in both
  `treeIds=` and `flatIds=` query parameters.
- Switching Segmented dry/real does NOT construct additional
  EventSources.
- Disabled / running / known_bad behaviors of the check button still
  hold (re-use existing tests; mock provider slice instead of
  `useProcessStream` for the check PID).

## Release order

1. **optio-api 0.1.x+1**: server endpoint + `createMultiTreePoller` +
   `rootId` field on TreePoller. Patch bump.
2. **optio-ui 0.1.x+1**: provider + context + context-aware
   `useProcessStream` / `useProcess`. Patch bump.
3. **optio-contracts**: bump only if a shared type is added;
   otherwise unchanged. If bumped, use wire-locked
   `make release-wire` to ship optio-contracts + optio-core
   together.
4. **excavator**: bump optio-api + optio-ui pins;
   migrate `EntityOverview`.

Each release follows the existing `make release-<package> BUMP=patch`
flow.

## Implementation order

1. Server: add `rootId` to existing `createTreePoller` event payload.
2. Server: add `createMultiTreePoller` + endpoint route in fastify
   adapter.
3. Server: add endpoint route in nextjs-pages adapter.
4. Server: tests.
5. Client: `MultiProcessStreamContext` + provider component.
6. Client: refactor `useProcessStream` to context-awareness with
   per-PID fallback.
7. Client: refactor `useProcess` similarly.
8. Client: tests.
9. Release optio-api, optio-ui.
10. Excavator: bump pins.
11. Excavator: migrate `EntityOverview` to wrap in provider; collect
    PIDs at page level.
12. Excavator: tests.

## Risks / open points

1. **Combined snapshot size.** One large entity (many sub-recipe
   processes per root) × multiple roots → bigger `update` payload than
   the per-PID version. Mitigation: server emits only on diff (same as
   single-PID). If practical payloads grow problematic, server can
   split into per-root partial updates later.
2. **Reconnect storm during config edits.** Rapid target add/remove
   would close/reopen the EventSource frequently. Mitigation: debounce
   in `EntityOverview` if observed; not built in now (YAGNI).
3. **Log ordering across roots.** Log entries from different roots
   must be sorted by timestamp before emit. Server already sorts in
   single-PID TreePoller; multi version must preserve this.
4. **Per-PID fallback existing.** Other consumers (e.g. a standalone
   `ProcessDetailView` on a per-process detail route) keep working via
   the fallback. Important for back-compat. Tests must cover the
   fallback path.
5. **`rootId` field added to existing TreePoller event.** Strictly
   additive (no caller currently reads or rejects it), but worth
   noting because it changes the wire payload.
