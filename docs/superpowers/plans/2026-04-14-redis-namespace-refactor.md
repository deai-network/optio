# Redis Namespace Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change all Redis keys and streams from `{prefix}:*` to `{database}/{prefix}:*` so that instances using different MongoDB databases on the same Redis server don't collide.

**Architecture:** The database name is derived from the already-connected MongoDB object — `mongo_db.name` in Python, `db.databaseName` in TypeScript. The `resolveDb` helper is extended to return the database name string. Publisher functions gain a `database` parameter. Handler command functions gain a `database` parameter and pass it through to publishers. On the Python side, `lifecycle.py` constructs the stream name using the database name.

**Tech Stack:** TypeScript, Python, MongoDB driver, Redis (ioredis / redis-py), Vitest, pytest

---

## File Structure

**Modify:**
- `packages/optio-core/src/optio_core/lifecycle.py` — derive stream name from `mongo_db.name`
- `packages/optio-core/tests/test_integration.py` — update stream names in test assertions
- `packages/optio-core/README.md` — remove Redis collision warning
- `packages/optio-api/src/resolve-db.ts` — return `database` string alongside `db` and `prefix`
- `packages/optio-api/src/publisher.ts` — add `database` parameter to `getStreamName` and all publish functions
- `packages/optio-api/src/handlers.ts` — add `database` parameter to command handlers, pass through to publishers
- `packages/optio-api/src/index.ts` — update exported signatures if needed
- `packages/optio-api/src/adapters/fastify.ts` — pass `database` from `resolveDb` result to handlers
- `packages/optio-api/src/adapters/express.ts` — same
- `packages/optio-api/src/adapters/nextjs-app.ts` — same
- `packages/optio-api/src/adapters/nextjs-pages.ts` — same
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts` — verify stream name format

**Test:**
- `packages/optio-core/tests/test_integration.py`
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

---

### Task 1: Update optio-core stream naming

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

- [ ] **Step 1: Change the stream name construction**

In `packages/optio-core/src/optio_core/lifecycle.py`, line 77 currently reads:

```python
stream_name = f"{prefix}:commands"
```

Change it to:

```python
db_name = mongo_db.name
stream_name = f"{db_name}/{prefix}:commands"
```

Place the `db_name` line before the `stream_name` line, inside the `if redis_url:` block (after line 74).

- [ ] **Step 2: Verify Python tests still pass (non-integration)**

Run: `cd packages/optio-core && python -m pytest tests/test_no_redis.py -v`
Expected: All tests pass (these don't use Redis streams).

---

### Task 2: Update integration tests for new stream name

**Files:**
- Modify: `packages/optio-core/tests/test_integration.py`

- [ ] **Step 1: Update stream names in test_full_lifecycle**

In `test_full_lifecycle()`, lines 58-64 currently use:

```python
await redis.xadd(
    f"{prefix}:commands",
    {"type": "launch", "payload": json.dumps({"processId": "good_task"})},
)
await redis.xadd(
    f"{prefix}:commands",
    {"type": "launch", "payload": json.dumps({"processId": "bad_task"})},
)
```

Change both to use the database-scoped stream name. The `db_name` variable is already defined as `f"optio_inttest_{id(asyncio.get_event_loop())}"` on line 16. Change both `f"{prefix}:commands"` to `f"{db_name}/{prefix}:commands"`.

- [ ] **Step 2: Update stream names in test_child_process_tree**

In `test_child_process_tree()`, lines 127-129 currently use:

```python
await redis.xadd(
    f"{prefix}:commands",
    {"type": "launch", "payload": json.dumps({"processId": "parent"})},
)
```

The `db_name` is defined on line 96 as `f"optio_tree_{id(asyncio.get_event_loop())}"`. Change `f"{prefix}:commands"` to `f"{db_name}/{prefix}:commands"`.

- [ ] **Step 3: Run integration tests**

Run: `cd packages/optio-core && python -m pytest tests/test_integration.py -v`
Expected: All tests pass.

---

### Task 3: Update resolveDb to return database name

**Files:**
- Modify: `packages/optio-api/src/resolve-db.ts`

- [ ] **Step 1: Extend the return type**

In `packages/optio-api/src/resolve-db.ts`, change the `resolveDb` function to return `database` as a string alongside `db` and `prefix`.

Replace the current function:

```typescript
export function resolveDb(
  opts: DbOptions,
  query: { database?: string; prefix?: string },
): { db: Db; prefix: string } {
  const prefix = query.prefix || 'optio';

  if ('db' in opts && opts.db) {
    return { db: opts.db, prefix };
  }

  if (!query.database) {
    throw new Error('database query parameter is required in multi-db mode');
  }

  return { db: opts.mongoClient!.db(query.database), prefix };
}
```

With:

```typescript
export function resolveDb(
  opts: DbOptions,
  query: { database?: string; prefix?: string },
): { db: Db; database: string; prefix: string } {
  const prefix = query.prefix || 'optio';

  if ('db' in opts && opts.db) {
    return { db: opts.db, database: opts.db.databaseName, prefix };
  }

  if (!query.database) {
    throw new Error('database query parameter is required in multi-db mode');
  }

  return { db: opts.mongoClient!.db(query.database), database: query.database, prefix };
}
```

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: No errors (existing code destructures `{ db, prefix }` which still works — `database` is just an unused extra field).

---

### Task 4: Update publisher to use database-scoped stream names

**Files:**
- Modify: `packages/optio-api/src/publisher.ts`

- [ ] **Step 1: Add database parameter to all functions**

Replace `packages/optio-api/src/publisher.ts`:

```typescript
import type { Redis } from 'ioredis';

function getStreamName(database: string, prefix: string): string {
  return `${database}/${prefix}:commands`;
}

export async function publishLaunch(redis: Redis, database: string, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'launch', 'payload', JSON.stringify({ processId }));
}

export async function publishCancel(redis: Redis, database: string, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'cancel', 'payload', JSON.stringify({ processId }));
}

export async function publishDismiss(redis: Redis, database: string, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'dismiss', 'payload', JSON.stringify({ processId }));
}

export async function publishResync(redis: Redis, database: string, prefix: string, clean: boolean = false): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'resync', 'payload', JSON.stringify({ clean }));
}
```

- [ ] **Step 2: Build (will fail — handlers need updating)**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: Errors in handlers.ts because publisher signatures changed. That's expected — Task 5 fixes it.

---

### Task 5: Update handlers to pass database to publishers

**Files:**
- Modify: `packages/optio-api/src/handlers.ts`

- [ ] **Step 1: Add database parameter to command handlers**

In `packages/optio-api/src/handlers.ts`, update the four command handlers to accept `database` and pass it through to publishers.

Change `launchProcess` signature from:
```typescript
export async function launchProcess(db: Db, redis: Redis, prefix: string, id: string): Promise<CommandResult> {
```
To:
```typescript
export async function launchProcess(db: Db, redis: Redis, database: string, prefix: string, id: string): Promise<CommandResult> {
```

And change the publish call from:
```typescript
await publishLaunch(redis, prefix, proc.processId);
```
To:
```typescript
await publishLaunch(redis, database, prefix, proc.processId);
```

Apply the same pattern to `cancelProcess`, `dismissProcess`, and `resyncProcesses`:

`cancelProcess`:
```typescript
export async function cancelProcess(db: Db, redis: Redis, database: string, prefix: string, id: string): Promise<CommandResult> {
```
```typescript
await publishCancel(redis, database, prefix, proc.processId);
```

`dismissProcess`:
```typescript
export async function dismissProcess(db: Db, redis: Redis, database: string, prefix: string, id: string): Promise<CommandResult> {
```
```typescript
await publishDismiss(redis, database, prefix, proc.processId);
```

`resyncProcesses`:
```typescript
export async function resyncProcesses(redis: Redis, database: string, prefix: string, clean: boolean = false): Promise<{ message: string }> {
```
```typescript
await publishResync(redis, database, prefix, clean);
```

- [ ] **Step 2: Build (will fail — adapters need updating)**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: Errors in adapters because handler signatures changed. Tasks 6-9 fix those.

---

### Task 6: Update Fastify adapter to pass database

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`

- [ ] **Step 1: Pass database to command handlers**

In the Fastify adapter, each command handler route already calls `resolveDb(dbOpts, query)` which now returns `{ db, database, prefix }`. Update the destructuring and handler calls:

For `launch`:
```typescript
    launch: async ({ params, query }) => {
      const { db, database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.launchProcess(db, redis, database, prefix, params.id);
      return result as any;
    },
```

For `cancel`:
```typescript
    cancel: async ({ params, query }) => {
      const { db, database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.cancelProcess(db, redis, database, prefix, params.id);
      return result as any;
    },
```

For `dismiss`:
```typescript
    dismiss: async ({ params, query }) => {
      const { db, database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.dismissProcess(db, redis, database, prefix, params.id);
      return result as any;
    },
```

For `resync`:
```typescript
    resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean } }) => {
      const { database, prefix } = resolveDb(dbOpts, query);
      const result = await handlers.resyncProcesses(redis, database, prefix, body.clean ?? false);
      return { status: 200 as const, body: result };
    },
```

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: May still have errors from other adapters.

---

### Task 7: Update Express adapter to pass database

**Files:**
- Modify: `packages/optio-api/src/adapters/express.ts`

- [ ] **Step 1: Same changes as Fastify**

Read the current Express adapter. Apply the same pattern as Task 6: destructure `{ db, database, prefix }` from `resolveDb(dbOpts, query)` in the `launch`, `cancel`, `dismiss`, and `resync` handlers, and pass `database` as the new parameter to `handlers.launchProcess`, `handlers.cancelProcess`, `handlers.dismissProcess`, and `handlers.resyncProcesses`.

- [ ] **Step 2: Build and verify**

---

### Task 8: Update Next.js App Router adapter to pass database

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`

- [ ] **Step 1: Same changes as Fastify**

Read the current Next.js App Router adapter. Apply the same pattern: destructure `{ db, database, prefix }` and pass `database` to command handlers.

- [ ] **Step 2: Build and verify**

---

### Task 9: Update Next.js Pages Router adapter to pass database

**Files:**
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`

- [ ] **Step 1: Same changes as Fastify**

Read the current Next.js Pages Router adapter. Apply the same pattern: destructure `{ db, database, prefix }` and pass `database` to command handlers.

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: No errors across all adapters now.

---

### Task 10: Update tests and verify stream name format

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

- [ ] **Step 1: Add test verifying stream name format**

Add a test to the Fastify test file that verifies the publisher writes to the correct Redis stream. The test seeds a process, launches it via the API, and checks that the Redis mock received a command on the correctly-named stream.

After the existing launch test, add:

```typescript
  it('POST /api/processes/:id/launch — publishes to database-scoped Redis stream', async () => {
    const doc = await seedProcess({ status: { state: 'idle' } });
    const app = createApp();

    await app.inject({
      method: 'POST',
      url: `/api/processes/${doc._id.toString()}/launch`,
    });

    // ioredis-mock stores streams in memory — check the stream name
    const streams = await redis.xrange('optio_test_fastify/optio:commands', '-', '+');
    expect(streams.length).toBeGreaterThan(0);
    const lastMsg = streams[streams.length - 1];
    expect(lastMsg[1]).toContain('launch');
  });
```

Note: `optio_test_fastify` is the database name used in the test setup, and `optio` is the default prefix.

- [ ] **Step 2: Run all Fastify tests**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/fastify.test.ts`
Expected: All tests pass.

---

### Task 11: Remove README collision warning

**Files:**
- Modify: `packages/optio-core/README.md`

- [ ] **Step 1: Simplify the prefix description**

In `packages/optio-core/README.md`, find the `prefix` parameter row in the `init()` table. It currently contains a long warning about Redis collisions. Replace the description with a simpler version that reflects the new scoping:

Find:
```
| `prefix` | `str` | `"optio"` | Namespace for collections (`{prefix}_processes`) and Redis streams (`{prefix}:commands`). Override if you need to avoid name collisions in a shared database. **Important:** The prefix also scopes Redis streams. If two optio-core instances share the same Redis server and the same prefix, their command streams will collide — even if they use different MongoDB databases. Use distinct prefixes when running multiple instances against the same Redis. |
```

Replace with:
```
| `prefix` | `str` | `"optio"` | Namespace for collections (`{prefix}_processes`) and Redis streams (`{database}/{prefix}:commands`). Override if you need to avoid name collisions in a shared database. Redis streams are automatically scoped by both database name and prefix, so instances using different databases won't collide even on a shared Redis server. |
```

---

### Task 12: Run full test suite

- [ ] **Step 1: Run TypeScript tests**

Run: `pnpm test`
Expected: All tests pass across all packages.

- [ ] **Step 2: Run Python tests**

Run: `cd packages/optio-core && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 3: Fix any failures**

Address any test failures before finalizing.
