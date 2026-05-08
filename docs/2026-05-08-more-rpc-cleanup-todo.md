# 2026-05-08 — More RPC cleanup TODO (seed)

**Status:** Seed only. Not designed. To be fleshed out later.

**Companion to:** `2026-05-08-engine-rpc-migration-design.md` (the engine RPC migration spec).

## What this is

Once the API↔engine command path moves to clamator RPC and the engine becomes the sole authority, a second class of code-and-design choices that currently exists *because* there was no RPC reply will remain in the codebase. They are not part of the primary cleanup, but they are also obsoleted (or partially obsoleted) by the move to RPC and should be reviewed afterward.

This file is a placeholder so the topic does not get lost. It records the symptoms found during the brainstorming on 2026-05-08; the redesign happens in a later session.

## The pattern

Today, after a user clicks "Launch" / "Cancel" / "Dismiss" / "Resync" in the UI:

1. UI fires HTTP POST via the contract client.
2. API handler publishes a redis `xadd` to `${prefix}:commands` and returns HTTP 200 with the **pre-command** process body. (Stale.)
3. UI's mutation `onSuccess` invalidates the `['processes']` query cache.
4. Two background mechanisms eventually surface the engine's actual write:
   - The API server runs a 1 s `setInterval` MongoDB poller that diffs snapshots and emits SSE events (`packages/optio-api/src/stream-poller.ts`, lines 66 and 175 — `createListPoller` and `createTreePoller`).
   - The UI's `useProcess*` hooks have `refetchInterval: 5000` (`packages/optio-ui/src/hooks/useProcessQueries.ts` lines 24, 41, 57, 70). Combined with cache invalidation on mutation success, this triggers a fast re-fetch after each command.

Net effect: the user-facing "did my command actually do something?" loop is closed via repeated re-reads of the process collection. Latency to first visible update is roughly 1 second (the SSE poller's tick).

## Why this exists

There is no reply channel from engine to API today. The API has no way to know whether the engine accepted the command, what state it transitioned to, or whether anything happened at all. So the system polls until the change shows up in the read path. The 1 s SSE polling loop and the 5 s tanstack-query refetch interval are both compensations for the missing reply.

## Why it becomes worth revisiting after the RPC migration

Once the API's command handlers `await` an engine RPC and receive the post-command process state back synchronously, the *command-outcome confirmation* part of the polling pattern is no longer needed. The HTTP response body is already correct. The cache-invalidate-then-poll dance after a mutation is doing work it does not need to do.

What the polling is still doing — and would still need to do — is surfacing background changes that the API never triggered: scheduler firings, progress updates, child-process state transitions, log appends, widget data updates. These come from the engine's normal operation, not from API calls. Any redesign here has to keep that working.

So the question for the redesign: which of these mechanisms can shrink or go away once RPC handles command-outcome confirmation directly, and which need to stay (and possibly become more efficient) for engine-driven background updates?

## Specific items to look at later

Each of these is a spot where the polling pattern shows up. The redesign should consider each on its merits.

1. **`refetchInterval: 5000` on every `useProcess*` hook** (`useProcessQueries.ts:24,41,57,70`). Default for `useProcessList`, `useProcess`, `useProcessTree`, `useProcessTreeLog`. Once SSE is the live path and RPC makes the post-mutation refetch unnecessary, this 5 s interval may be redundant. It might be reducible to "no automatic refetch; tanstack-query treats SSE updates as the live source." Not obvious — there are reads (e.g., paginated log fetches) where SSE is not the source, and `refetchInterval` matters.

2. **Cache invalidation on mutation success** (`useProcessActions.ts:15`, `invalidate()` called in every mutation's `onSuccess`). Once the mutation response carries the post-command process, the immediate invalidate-and-refetch is unnecessary for the mutated process; it might still be useful for mutated *trees* (cancel propagates to children; resync replaces many docs at once). Granularity: invalidate just the relevant cache key, not all of `['processes']`.

3. **API SSE poller frequency** (`stream-poller.ts` — `setInterval(poll, 1000)` in two places). 1 s is a polling-derived latency floor for live UI updates. With RPC handling command-outcome immediately, the SSE loop only carries engine-driven background changes. Polling is the wrong mechanism for those — engine could push change events (via clamator notifications, or a redis pub/sub on a separate channel) when it writes, and the SSE bridge subscribes. That would make the "live" feel actually live, and remove the 1 s poller from the API's hot path entirely. Big design choice; not trivial; needs its own brainstorming session.

4. **Latency claim in any user-visible docs.** Anywhere docs describe "live updates within 1 second" or similar, that claim is downstream of the SSE poll interval. If item 3 changes, the claim moves.

5. **Resync as a notification.** Under the clamator migration, resync is a fire-and-forget notification (no reply). The API still has to tell the UI something. Today it returns `{ message: "Resync requested" }`. Post-migration that stays the same. But from the UI's perspective, "resync requested" → eventually-consistent display is exactly the same polling pattern as today. Worth deciding whether resync should become an RPC method (with a reply once the resync starts/completes) instead of a notification.

## What this file is not

Not a design. Not a plan. Not a list of items to do in the primary cleanup. Just a pointer so the next brainstorming session has the symptoms in one place.
