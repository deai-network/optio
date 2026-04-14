# Redis Namespace Refactor and Instance Liveness

## Problem

Redis keys and streams are currently scoped only by prefix (`{prefix}:commands`). Two optio-core instances sharing the same Redis server and prefix but using different MongoDB databases will collide. Additionally, there is no way to know whether a discovered optio instance is actually live — whether an optio-core process is running and will respond to commands.

## Solution

Two phases:

1. **Redis namespace refactor** — scope all Redis keys/streams by `{database}/{prefix}` instead of just `{prefix}`, eliminating collision risk.
2. **Liveness heartbeat** — optio-core periodically sets a TTL-based heartbeat key in Redis. The discovery endpoint reports liveness per instance. The UI shows which instances are live and hides action buttons for offline ones.

---

## Phase 1: Redis Namespace Refactor

All Redis keys and streams change from `{prefix}:*` to `{database}/{prefix}:*`.

The database name is derived from the already-connected MongoDB database object — `mongo_db.name` in Python (Motor), `db.databaseName` in TypeScript (MongoDB driver). No new parameters are needed.

### optio-core (Python)

In `lifecycle.py`, derive the Redis namespace from `mongo_db.name` and `prefix`. The command stream name changes from `f"{prefix}:commands"` to `f"{mongo_db.name}/{prefix}:commands"`. This flows through to the `CommandConsumer`.

### optio-api (TypeScript)

In `publisher.ts`, `getStreamName()` changes to accept both `database` and `prefix`, producing `{database}/{prefix}:commands`. The `resolveDb` helper already produces both values from the request — adapters pass them through to publishers and stream pollers.

### README

Remove the Redis collision warning added to optio-core's README (the collision problem is now solved by design).

### Testing

- optio-core: unit test verifying the stream name format is `{database}/{prefix}:commands`.
- optio-api Fastify integration test: verify the publisher uses the correct stream name.

---

## Phase 2: Liveness Heartbeat

### Heartbeat mechanism

optio-core periodically sets a Redis key `{database}/{prefix}:heartbeat` with a 15-second TTL. The key is set every 5 seconds during `run()` using `SET key value EX 15`. The value is a simple `"1"` — its presence is what matters.

On `shutdown()`, the heartbeat background task is cancelled. The key expires naturally within 15 seconds.

### Discovery endpoint

`discoverInstances()` gains an optional `redis` parameter (the same `Redis` instance already available in the adapters). When provided, after scanning MongoDB for instances, it checks Redis for each discovered instance's heartbeat key (`{database}/{prefix}:heartbeat`). Returns a `live: boolean` field per instance. When Redis is not provided, `live` defaults to `false` for all instances.

The discovery contract's `InstanceSchema` gains `live: z.boolean()`.

### UI context

- `OptioContext` gains `live: boolean`.
- `OptioProvider` passes `live` from the selected instance's discovery result.
- New `useOptioLive()` hook returns the `live` boolean from context.

### UI behavior

**Instance selector dropdown:**
- Live instances sorted first, then a visual separator, then offline instances with "(offline)" appended to their label.
- A refresh button beside the dropdown re-fetches the discovery endpoint, updating liveness data, sort order, and labels.

**Action buttons (launch, cancel, dismiss, resync):**
- Components check `useOptioLive()` and hide action buttons when the instance is offline.
- No blocking logic in hooks or API calls — sending commands to an offline instance is harmless (messages go to an unread Redis stream). The hiding is purely cosmetic since the buttons wouldn't achieve anything.

**Read-only views:**
- Process list, tree view, log panel, and SSE streams continue to work normally regardless of liveness.

### Testing

- optio-core: unit test verifying the heartbeat key is set during `run()` and expires after shutdown.
- optio-api Fastify integration test: verify `GET /api/optio/instances` returns `live` boolean per instance.
- optio-ui: update existing `useInstanceDiscovery` and `OptioProvider` tests for `live` field.
