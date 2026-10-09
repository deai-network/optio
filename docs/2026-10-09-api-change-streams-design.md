# optio-api streams on change streams -- Design

**Base revision:** `4031653a` on `main` (2026-10-09).

## Summary

optio-api's live streams (process list, process tree, multi-tree, session
events) stop polling MongoDB every second. One shared change stream per
`(database, prefix)` per API process tells each open stream when a change is
relevant to it, and only then does that stream read. What clients receive is
unchanged. Where change streams are unavailable (a standalone MongoDB), or while
one is down, streams poll once a second as today.

This is about the traffic between MongoDB and the API only. What the API sends
to browsers (a whole list on every change) is a separate question, not part of
this design.

## Why

Measured on the excavator host (2026-10-09):

- The optio demo with two windows open (a conversation and a task list each):
  about 10 queries a second against its process collection, 4.4 MB a minute
  returned, about 1.8 CPU-s a minute in the demo's API process -- with nothing
  changing.
- One excavator Processes list over Plakomm (132 processes), run through
  optio-api's own list poller: 60 reads a minute and about 2 CPU-s a minute in
  the API; the snapshot changed about 24 times a minute while syncs ran.
- Every open window adds its own polls, and each poll's cost grows with the
  collection and its logs.

## Parts

### `ProcessChangeHub` (`packages/optio-api/src/process-change-hub.ts`, new)

- One hub per `(database, prefix)` per API process, from a module-level
  registry keyed by the `MongoClient` (`db.client`), the database name and the
  prefix -- not by the `Db` object, which multi-database mode may create anew per
  request.
- It opens a change stream on `{prefix}_processes` when its first subscriber
  arrives and closes it when the last one leaves. No `fullDocument` lookup: an
  insert or replace event carries the whole document, an update event the
  changed fields with their new values (`updateDescription`), which is all the
  relevance rules use. No extra read per change.
- Each change is offered to every subscriber's `isRelevant(change)`; the
  subscribers that answer yes are asked to re-read.
- `drop`, `rename`, `invalidate` and `dropDatabase` events, and every (re)open
  of the stream, ask every subscriber to re-read once.
- The change source behind the hub is an interface (`open(onChange, onError)`
  returning a `close()`), so tests can drive the hub with a fake source.

### The stream functions

`createListPoller`, `createTreePoller`, `createMultiTreePoller` and
`createSessionEventsPoller` keep their names, options and `start()` / `stop()`
handles, so the four adapters and custom adapters need no change. Inside, each
becomes a hub subscriber:

- `start()` subscribes, waits until the hub's stream is open (or the hub is
  polling), then makes its initial read: today's query, snapshot comparison and
  events. A change that lands between subscribing and that read is either in the
  read or arrives as a change and triggers another read.
- After that, it re-reads only when the hub says so, at most once a second: the
  first relevant change after a quiet second re-reads at once; further changes
  within the second collapse into one read when the second is up. (The interval
  is an option for tests.)
- `stop()` unsubscribes.
- A failed read stops the stream and calls `onError`, as today; the adapters
  close the client connection and the client reconnects, as today.

### What is relevant

From the change event alone. "Members" are the `_id`s a stream returned in its
last read; field names compare by their top-level key (`progress.percent` ->
`progress`), over `updatedFields`, `removedFields` and `truncatedArrays`.

| Stream | Relevant |
|---|---|
| List | any insert; a replace or delete of a member; an update of a member touching a field the list sends (the list projection's fields); an update of any process touching `metadata` (it may start or stop matching the filter or the scope) |
| Tree | an insert or replace whose document's `rootId` is the tree's root; any update, replace or delete of a member |
| Multi-tree | as the tree, for any of its roots; any update, replace or delete of a member (flat ids included) |
| Session events | an update that sets `originatingSessionId` to this session; an insert or replace whose document has it; an update of a member touching `sessionEvents` |

Anything else is ignored. Whatever a rule lets through only costs one read;
whatever it wrongly drops would leave a stream stale, so the rules lean towards
"relevant".

## When change streams are missing or break

- **Refused at open** (MongoDB standalone: code 40573; no permission to watch:
  code 13): the hub goes into polling mode -- every subscriber polls once a
  second, as today -- and tries the change stream again every 5 minutes (a
  deployment may have become a replica set since), switching over by itself.
- **Any other error, at open or on an open stream** (network, failover, a
  primary stepping down): the hub closes the stream and reopens it with backoff,
  1 s doubling to 30 s. Meanwhile subscribers poll once a second; when it
  reopens, every subscriber re-reads once, so nothing missed stays missed and no
  resume token is needed.
- A hub with no subscribers holds no change stream and no timers.

## Unchanged

The SSE routes, event shapes and their order; the stream functions' signatures;
the queries the streams run (including the list projection); the access scope;
optio-api's read-only rule; optio-core.

## Cost

One change-stream cursor per `(database, prefix)` per API process while at least
one stream is open, whatever the number of windows. With nothing changing, no
reads. A tree or conversation of an idle process: no reads instead of 60 a
minute. A list over a busy dataspace: reads when it changes, at most one a second
(Plakomm: about 24 a minute instead of 60).

## Testing

In `packages/optio-api/src/__tests__/` (vitest, no wall-clock dependence; fake
timers for anything timed):

- **Relevance rules,** as pure functions over hand-written change events:
  - list: an insert is relevant; a member's update touching `status` or
    `progress` is, one touching only `log` is not; a non-member's update
    touching `metadata` is, one touching `status` is not; deleting a member is,
    deleting a non-member is not;
  - tree: an insert under this root is relevant, under another root not; any
    member update is;
  - session events: an update setting `originatingSessionId` to this session is
    relevant, to another not; a member's `sessionEvents` change is, its
    `status`-only change is not;
  - `invalidate`, `drop`, `rename`, `dropDatabase` reach every subscriber.
- **The hub, with a fake change source:** a change reaches only the subscribers
  it is relevant to; changes within a second give one read at the end of it, a
  change after a quiet second reads at once; a refused open (40573) gives
  one-second polling and a new attempt after 5 minutes; after an error the hub
  reopens with backoff and every subscriber re-reads once; the stream closes when
  the last subscriber leaves.
- **Real MongoDB** (the host's test MongoDB is a replica set): a tree stream,
  reading through a recording `Db` wrapper, gets exactly one re-read for an
  update of an unrelated process followed by one of its own (changes arrive in
  order; the one-second interval set to zero); the list and session-events
  streams emit the same events as today. Skipped, with the reason, on a
  standalone server.
- **The existing stream tests** pass unchanged, both on change streams and with
  the hub forced into polling mode.

## Documentation

optio-api's AGENTS.md "Stream Poller" section and the root AGENTS.md: how a
stream learns about changes (one shared change stream per `(database, prefix)`,
at most one read a second per stream, polling as the fallback); event shapes as
documented.

## Rollout

- One release batch per `docs/release-cookbook.md`, "Standard flow: release
  everything": every optio package with commits since its last release tag, in
  dependency order -- including the wire pair (optio-core: the process indexes and
  TTL fixes, `docs/2026-10-09-process-indexes-design.md`) and optio-api (this
  design and the list projection).
- Then excavator's optio dependency bump for every optio package it uses that
  was released.
- Nothing manual on any deployment: excavator's main MongoDB is already a
  replica set; a deployment without one keeps polling.

## Measurement

On the excavator host, before and after, with a harness that runs optio-api's
stream functions directly against `excavator.gm_processes` (no browser), and the
profiler attributing reads by application name: a tree stream of a finished
process and a session-events stream of an idle session (reads a minute: 60 ->
0), a list stream over Plakomm (60 -> about 24); the harness's CPU and the
MongoDB work for each.
