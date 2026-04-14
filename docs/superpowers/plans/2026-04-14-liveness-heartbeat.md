# Instance Liveness Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect whether an optio-core instance is actively running by using a Redis heartbeat key with TTL, report liveness in the discovery endpoint, and reflect it in the dashboard UI.

**Architecture:** optio-core sets a Redis key `{database}/{prefix}:heartbeat` every 5 seconds with a 15-second TTL during `run()`. The `discoverInstances` function in optio-api checks these keys when Redis is available. The discovery contract adds `live: boolean` per instance. The UI context exposes `live` via `useOptioLive()`. The dashboard hides action buttons for offline instances and sorts the instance dropdown with live instances first.

**Tech Stack:** Python (redis-py, asyncio), TypeScript (ioredis, ts-rest, Zod, React), Vitest, pytest

---

## File Structure

**Modify:**
- `packages/optio-core/src/optio_core/lifecycle.py` — add heartbeat background task in `run()`, cancel on `shutdown()`
- `packages/optio-core/tests/test_integration.py` — verify heartbeat key exists during run
- `packages/optio-contracts/src/contract.ts` — add `live: z.boolean()` to `InstanceSchema`
- `packages/optio-api/src/discovery.ts` — accept optional Redis, check heartbeat keys
- `packages/optio-api/src/adapters/fastify.ts` — pass `redis` to `discoverInstances`
- `packages/optio-api/src/adapters/express.ts` — pass `redis` to `discoverInstances`
- `packages/optio-api/src/adapters/nextjs-app.ts` — pass `redis` to `discoverInstances`
- `packages/optio-api/src/adapters/nextjs-pages.ts` — pass `redis` to `discoverInstances`
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts` — test `live` field in discovery
- `packages/optio-ui/src/hooks/useInstanceDiscovery.ts` — add `live` to `OptioInstance`
- `packages/optio-ui/src/context/OptioProvider.tsx` — add `live` to context
- `packages/optio-ui/src/context/useOptioContext.ts` — add `useOptioLive()` hook
- `packages/optio-ui/src/index.ts` — export `useOptioLive`
- `packages/optio-ui/src/__tests__/useInstanceDiscovery.test.ts` — update for `live` field
- `packages/optio-ui/src/__tests__/OptioProvider.test.tsx` — test `live` in context
- `packages/optio-dashboard/src/app/App.tsx` — sort dropdown, add refresh button, hide actions when offline

---

### Task 1: Add heartbeat to optio-core

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py`

- [ ] **Step 1: Add heartbeat background task in run()**

In `packages/optio-core/src/optio_core/lifecycle.py`, add a `_heartbeat_task` attribute in `__init__`:

After line 37 (`self._running = False`), add:
```python
        self._heartbeat_task: asyncio.Task | None = None
```

- [ ] **Step 2: Create heartbeat coroutine**

Add a new method to the `Optio` class, after the `shutdown()` method (after line 261):

```python
    async def _heartbeat_loop(self) -> None:
        """Periodically set a heartbeat key in Redis with TTL."""
        db_name = self._config.mongo_db.name
        prefix = self._config.prefix
        key = f"{db_name}/{prefix}:heartbeat"
        while self._running:
            try:
                await self._redis.set(key, "1", ex=15)
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")
            await asyncio.sleep(5)
```

- [ ] **Step 3: Start heartbeat in run()**

In the `run()` method, after the scheduler start (after `await self._scheduler.start()` on line 226) and before the `try` block on line 228, add:

```python
        # Start heartbeat (if Redis is configured)
        if self._redis:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
```

- [ ] **Step 4: Cancel heartbeat in shutdown()**

In `shutdown()`, after `self._running = False` (line 239) and before the consumer stop, add:

```python
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
```

- [ ] **Step 5: Run non-integration tests**

Run: `cd /home/csillag/deai/optio/packages/optio-core && python -m pytest tests/test_no_redis.py -v`
Expected: All tests pass (heartbeat only runs when Redis is configured).

---

### Task 2: Add heartbeat integration test

**Files:**
- Modify: `packages/optio-core/tests/test_integration.py`

- [ ] **Step 1: Add heartbeat verification to test_full_lifecycle**

In `test_full_lifecycle()`, after starting `run()` (after `await asyncio.sleep(3)` on line 69), add a check for the heartbeat key before calling shutdown:

Insert after `await asyncio.sleep(3)` and before `await fw.shutdown()`:

```python
    # Verify heartbeat is set while running
    heartbeat = await redis.get(f"{db_name}/{prefix}:heartbeat")
    assert heartbeat is not None, "Heartbeat key should exist while running"
```

Then after the shutdown and cleanup, add a check that the heartbeat expires. Insert after `await fw.shutdown()` and the try/except block (after line 74):

```python
    # Verify heartbeat expires after shutdown (wait for TTL)
    # The key may still exist briefly; just verify it was there
```

Note: We don't wait 15 seconds for expiry in tests — just verify it was set during run. The TTL mechanism is a Redis primitive that doesn't need testing.

- [ ] **Step 2: Run integration tests**

Run: `cd /home/csillag/deai/optio/packages/optio-core && python -m pytest tests/test_integration.py::test_full_lifecycle -v`
Expected: Test passes, heartbeat assertion succeeds.

---

### Task 3: Add live field to discovery contract

**Files:**
- Modify: `packages/optio-contracts/src/contract.ts`

- [ ] **Step 1: Add live to InstanceSchema**

In `packages/optio-contracts/src/contract.ts`, find the `InstanceSchema` definition:

```typescript
const InstanceSchema = z.object({
  database: z.string(),
  prefix: z.string(),
});
```

Add `live`:

```typescript
const InstanceSchema = z.object({
  database: z.string(),
  prefix: z.string(),
  live: z.boolean(),
});
```

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-contracts/tsconfig.json --noEmit`
Expected: No errors.

---

### Task 4: Update discoverInstances to check heartbeat

**Files:**
- Modify: `packages/optio-api/src/discovery.ts`

- [ ] **Step 1: Add Redis parameter and heartbeat check**

Replace `packages/optio-api/src/discovery.ts`:

```typescript
import type { Db, MongoClient } from 'mongodb';
import type { Redis } from 'ioredis';
import type { DbOptions } from './resolve-db.js';

const REQUIRED_FIELDS = ['processId', 'rootId', 'depth'];

interface OptioInstance {
  database: string;
  prefix: string;
  live: boolean;
}

async function discoverPrefixesInDb(db: Db): Promise<string[]> {
  const collections = await db.listCollections().toArray();
  const candidates = collections
    .map((c) => c.name)
    .filter((name) => name.endsWith('_processes'))
    .map((name) => name.slice(0, -'_processes'.length));

  const confirmed: string[] = [];

  for (const prefix of candidates) {
    const doc = await db.collection(`${prefix}_processes`).findOne();
    if (doc && REQUIRED_FIELDS.every((f) => f in doc)) {
      confirmed.push(prefix);
    }
  }

  return confirmed.sort();
}

async function checkLive(redis: Redis | undefined, database: string, prefix: string): Promise<boolean> {
  if (!redis) return false;
  const key = `${database}/${prefix}:heartbeat`;
  const result = await redis.exists(key);
  return result === 1;
}

export async function discoverInstances(opts: DbOptions, redis?: Redis): Promise<OptioInstance[]> {
  if ('db' in opts && opts.db) {
    const dbName = opts.db.databaseName;
    const prefixes = await discoverPrefixesInDb(opts.db);
    const instances: OptioInstance[] = [];
    for (const prefix of prefixes) {
      const live = await checkLive(redis, dbName, prefix);
      instances.push({ database: dbName, prefix, live });
    }
    return instances;
  }

  const adminDb = opts.mongoClient!.db().admin();
  const { databases } = await adminDb.listDatabases();
  const instances: OptioInstance[] = [];

  for (const dbInfo of databases) {
    const db = opts.mongoClient!.db(dbInfo.name);
    const prefixes = await discoverPrefixesInDb(db);
    for (const prefix of prefixes) {
      const live = await checkLive(redis, dbInfo.name, prefix);
      instances.push({ database: dbInfo.name, prefix, live });
    }
  }

  return instances.sort((a, b) =>
    a.database.localeCompare(b.database) || a.prefix.localeCompare(b.prefix),
  );
}
```

- [ ] **Step 2: Build (adapters will have errors — they need to pass redis)**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: Errors in adapters because `discoverInstances` now expects an optional second argument. That's fine — Task 5 fixes it.

---

### Task 5: Update all adapters to pass Redis to discoverInstances

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`

- [ ] **Step 1: Update Fastify adapter**

In `packages/optio-api/src/adapters/fastify.ts`, find the discovery endpoint (around line 87-90):

```typescript
  app.get('/api/optio/instances', async (_request: any, reply: any) => {
    const instances = await discoverInstances(dbOpts);
    reply.send({ instances });
  });
```

Change to:

```typescript
  app.get('/api/optio/instances', async (_request: any, reply: any) => {
    const instances = await discoverInstances(dbOpts, redis);
    reply.send({ instances });
  });
```

- [ ] **Step 2: Update Express adapter**

In `packages/optio-api/src/adapters/express.ts`, find the discovery endpoint and change `discoverInstances(dbOpts)` to `discoverInstances(dbOpts, redis)`.

- [ ] **Step 3: Update Next.js App Router adapter**

In `packages/optio-api/src/adapters/nextjs-app.ts`, find the discovery endpoint and change `discoverInstances(dbOpts)` to `discoverInstances(dbOpts, redis)`.

- [ ] **Step 4: Update Next.js Pages Router adapter**

In `packages/optio-api/src/adapters/nextjs-pages.ts`, find the discovery endpoint and change `discoverInstances(dbOpts)` to `discoverInstances(dbOpts, redis)`.

- [ ] **Step 5: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: No errors.

---

### Task 6: Update Fastify integration test for live field

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

- [ ] **Step 1: Update existing discovery test**

In the existing test `GET /api/optio/instances — discovers instances from collections with optio schema`, the assertion currently checks:

```typescript
expect(body.instances).toEqual([
  { database: 'optio_test_fastify', prefix: 'optio' },
]);
```

Update to include `live: false` (no real Redis heartbeat in tests since ioredis-mock doesn't have a running optio-core):

```typescript
expect(body.instances).toEqual([
  { database: 'optio_test_fastify', prefix: 'optio', live: false },
]);
```

- [ ] **Step 2: Add test for live instance detection**

Add a new test that simulates a heartbeat key and verifies `live: true`:

```typescript
  it('GET /api/optio/instances — reports live: true when heartbeat key exists', async () => {
    await seedProcess();
    await redis.set('optio_test_fastify/optio:heartbeat', '1', 'EX', 15);
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/instances',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.instances).toEqual([
      { database: 'optio_test_fastify', prefix: 'optio', live: true },
    ]);

    await redis.del('optio_test_fastify/optio:heartbeat');
  });
```

- [ ] **Step 3: Run tests**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/fastify.test.ts`
Expected: All tests pass.

---

### Task 7: Update UI hooks and context for live field

**Files:**
- Modify: `packages/optio-ui/src/hooks/useInstanceDiscovery.ts`
- Modify: `packages/optio-ui/src/context/OptioProvider.tsx`
- Modify: `packages/optio-ui/src/context/useOptioContext.ts`
- Modify: `packages/optio-ui/src/index.ts`

- [ ] **Step 1: Add live to OptioInstance and expose refetch**

In `packages/optio-ui/src/hooks/useInstanceDiscovery.ts`, add `live` to the `OptioInstance` interface:

```typescript
export interface OptioInstance {
  database: string;
  prefix: string;
  live: boolean;
}
```

Also update `useInstances` to expose `refetch` (needed by the dashboard's refresh button). Update the interface and function:

```typescript
interface UseInstancesResult {
  instances: OptioInstance[];
  isLoading: boolean;
  error: unknown;
  refetch: () => void;
}

export function useInstances(): UseInstancesResult {
  const client = useOptioClient();
  const { data, isLoading, error, refetch } = client.discovery.instances.useQuery(
    ['optio-instances'],
    {},
  );
  return {
    instances: data?.body?.instances ?? [],
    isLoading,
    error,
    refetch,
  };
}
```

- [ ] **Step 2: Add live to OptioContext**

In `packages/optio-ui/src/context/OptioProvider.tsx`, add `live` to `OptioContextValue`:

```typescript
interface OptioContextValue {
  prefix: string;
  database: string | undefined;
  live: boolean;
  baseUrl: string;
  client: OptioClient;
}
```

Update `OptioProviderProps` to accept optional `live`:

```typescript
interface OptioProviderProps {
  prefix?: string;
  database?: string;
  live?: boolean;
  baseUrl?: string;
  children: ReactNode;
}
```

Update `OptioProviderInner` to resolve `live`:

```typescript
function OptioProviderInner({ explicitPrefix, explicitDatabase, explicitLive, baseUrl, client, children }: {
  explicitPrefix: string | undefined;
  explicitDatabase: string | undefined;
  explicitLive: boolean | undefined;
  baseUrl: string;
  client: OptioClient;
  children: ReactNode;
}) {
  const { instance: discoveredInstance } = useInstanceDiscovery();
  const prefix = explicitPrefix ?? discoveredInstance?.prefix ?? 'optio';
  const database = explicitDatabase ?? discoveredInstance?.database;
  const live = explicitLive ?? discoveredInstance?.live ?? false;

  return (
    <OptioContext.Provider value={{ prefix, database, live, baseUrl, client }}>
      {children}
    </OptioContext.Provider>
  );
}
```

Update the outer `OptioProvider` to pass `live`:

```typescript
export function OptioProvider({ prefix, database, live, baseUrl = '', children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  return (
    <OptioContext.Provider value={{ prefix: prefix ?? 'optio', database, live: live ?? false, baseUrl, client }}>
      <OptioProviderInner explicitPrefix={prefix} explicitDatabase={database} explicitLive={live} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
```

- [ ] **Step 3: Add useOptioLive hook**

In `packages/optio-ui/src/context/useOptioContext.ts`, add:

```typescript
export function useOptioLive(): boolean {
  return useContext(OptioContext).live;
}
```

- [ ] **Step 4: Export useOptioLive**

In `packages/optio-ui/src/index.ts`, add to the existing context/hook exports:

```typescript
export { useOptioLive } from './context/useOptioContext.js';
```

- [ ] **Step 5: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-ui/tsconfig.json --noEmit`
Expected: No errors.

---

### Task 8: Update UI tests for live field

**Files:**
- Modify: `packages/optio-ui/src/__tests__/useInstanceDiscovery.test.ts`
- Modify: `packages/optio-ui/src/__tests__/OptioProvider.test.tsx`

- [ ] **Step 1: Update useInstanceDiscovery tests**

In `packages/optio-ui/src/__tests__/useInstanceDiscovery.test.ts`, update all mock data to include `live`:

For "returns the instance when exactly one is found":
```typescript
    mockQueryResult = {
      data: { body: { instances: [{ database: 'mydb', prefix: 'myapp', live: true }] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => useInstanceDiscovery());
    expect(result.current.instance).toEqual({ database: 'mydb', prefix: 'myapp', live: true });
```

For "returns null instance when multiple are found":
```typescript
    mockQueryResult = {
      data: { body: { instances: [
        { database: 'db1', prefix: 'optio', live: true },
        { database: 'db2', prefix: 'myapp', live: false },
      ] } },
```

For "returns null instance when none are found" — no changes needed (empty array).

- [ ] **Step 2: Update OptioProvider tests**

In `packages/optio-ui/src/__tests__/OptioProvider.test.tsx`:

Update the `ContextDisplay` component to show `live`:

```tsx
function ContextDisplay() {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const live = useOptioLive();
  return (
    <>
      <div data-testid="prefix">{prefix}</div>
      <div data-testid="database">{database ?? 'undefined'}</div>
      <div data-testid="live">{String(live)}</div>
    </>
  );
}
```

Add `useOptioLive` to the import from `useOptioContext.js`.

Update mock data to include `live` in the instance objects:

```typescript
let mockDiscoveryResult = {
  instance: null as { database: string; prefix: string; live: boolean } | null,
  instances: [] as { database: string; prefix: string; live: boolean }[],
  isLoading: false,
};
```

Update the "uses explicit prefix and database" test:
```typescript
    mockDiscoveryResult = {
      instance: { database: 'discovered-db', prefix: 'discovered', live: true },
      instances: [{ database: 'discovered-db', prefix: 'discovered', live: true }],
      isLoading: false,
    };
    renderWithProvider({ prefix: 'explicit', database: 'explicit-db', live: true });
    expect(screen.getByTestId('live').textContent).toBe('true');
```

Update the "uses discovered instance" test:
```typescript
    mockDiscoveryResult = {
      instance: { database: 'auto-db', prefix: 'auto', live: true },
      instances: [{ database: 'auto-db', prefix: 'auto', live: true }],
      isLoading: false,
    };
    renderWithProvider({});
    expect(screen.getByTestId('live').textContent).toBe('true');
```

Update the "falls back" test:
```typescript
    mockDiscoveryResult = { instance: null, instances: [], isLoading: false };
    renderWithProvider({});
    expect(screen.getByTestId('live').textContent).toBe('false');
```

Update `renderWithProvider` to accept `live`:
```typescript
function renderWithProvider(props: { prefix?: string; database?: string; live?: boolean }) {
```

- [ ] **Step 3: Run UI tests**

Run: `cd packages/optio-ui && npx vitest run src/__tests__/`
Expected: All tests pass.

---

### Task 9: Update dashboard for liveness

**Files:**
- Modify: `packages/optio-dashboard/src/app/App.tsx`

- [ ] **Step 1: Update InstanceSelector for live sorting and refresh**

Replace the `InstanceSelector` component to sort live instances first with a separator, add "(offline)" suffix, and include a refresh button:

```tsx
function InstanceSelector({ onSelect }: { onSelect: (instance: { database: string; prefix: string; live: boolean }) => void }) {
  const { instances, isLoading, error, refetch } = useInstances();

  if (isLoading) return null;
  if (error) return <Alert type="error" message="Failed to detect instances" />;
  if (instances.length === 0) {
    return <Alert type="info" message="No optio instance detected in the database" />;
  }

  const liveInstances = instances.filter((i) => i.live);
  const offlineInstances = instances.filter((i) => !i.live);

  const options: { label: string; value: string }[] = [];
  for (const inst of liveInstances) {
    options.push({
      label: `${inst.database}/${inst.prefix}`,
      value: `${inst.database}/${inst.prefix}`,
    });
  }
  if (liveInstances.length > 0 && offlineInstances.length > 0) {
    options.push({ label: '───', value: '__separator__', disabled: true } as any);
  }
  for (const inst of offlineInstances) {
    options.push({
      label: `${inst.database}/${inst.prefix} (offline)`,
      value: `${inst.database}/${inst.prefix}`,
    });
  }

  return (
    <div style={{ padding: 24 }}>
      <Typography.Text>Multiple optio instances detected. Select one:</Typography.Text>
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <Select
          style={{ flex: 1 }}
          placeholder="Select instance"
          options={options}
          onChange={(value) => {
            const inst = instances.find((i) => `${i.database}/${i.prefix}` === value);
            if (inst) onSelect(inst);
          }}
        />
        <Button onClick={() => refetch()}>Refresh</Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Update AppContent to pass live to OptioProvider**

In `AppContent`, the `selected` instance now has a `live` field. Pass it to `OptioProvider`:

```typescript
  const selected = manualInstance ?? instances[0];

  return (
    <OptioProvider prefix={selected.prefix} database={selected.database} live={selected.live}>
```

Also update the `manualInstance` state type:

```typescript
  const [manualInstance, setManualInstance] = useState<{ database: string; prefix: string; live: boolean } | null>(null);
```

- [ ] **Step 3: Update Dashboard to hide actions when offline**

In the `Dashboard` component, import and check `useOptioLive`:

Add to imports from `optio-ui`:
```typescript
import {
  OptioProvider,
  ProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
  useInstances,
  useOptioLive,
} from 'optio-ui';
```

In `Dashboard`:
```typescript
function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { tree, logs, connected: treeConnected } = useProcessStream(
    selectedProcessId ?? undefined,
  );
  const { launch, cancel, dismiss } = useProcessActions();
  const live = useOptioLive();

  return (
    <Layout>
      <Layout>
        <Sider width={400} style={{ background: '#fff', overflow: 'auto' }}>
          <ProcessList
            processes={processes}
            loading={!listConnected}
            onLaunch={live ? launch : undefined}
            onCancel={live ? cancel : undefined}
            onProcessClick={setSelectedProcessId}
          />
        </Sider>
        <Content style={{ padding: '24px', overflow: 'auto' }}>
          {selectedProcessId ? (
            <>
              <ProcessTreeView
                treeData={tree}
                sseState={{ connected: treeConnected }}
                onCancel={live ? cancel : undefined}
              />
              <ProcessLogPanel logs={logs} />
            </>
          ) : (
            <div style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
              Select a process to view details
            </div>
          )}
        </Content>
      </Layout>
    </Layout>
  );
}
```

The `ProcessList` and `ProcessTreeView` components already check `onLaunch &&` and `onCancel &&` before rendering buttons, so passing `undefined` hides them.

---

### Task 10: Run full test suite

- [ ] **Step 1: Run TypeScript tests**

Run: `pnpm test`
Expected: All tests pass.

- [ ] **Step 2: Run Python tests**

Run: `cd packages/optio-core && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 3: Fix any failures**

Address any test failures before finalizing.
