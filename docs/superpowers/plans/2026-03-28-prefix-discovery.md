# Prefix Auto-Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-detect optio prefixes from MongoDB so users don't need to configure them manually.

**Architecture:** A new `discoverPrefixes` handler scans MongoDB collections ending in `_processes`, validates document schema, and returns confirmed prefixes. Each adapter exposes this at `GET /api/optio/prefixes`. The UI gets new hooks (`usePrefixes`, `usePrefixDiscovery`) and the provider falls back to auto-discovery. The dashboard removes its `OPTIO_PREFIX` env var and uses auto-selection with a multi-prefix dropdown.

**Tech Stack:** TypeScript, MongoDB driver, ts-rest, React, Vitest

---

## File Structure

**Create:**
- `packages/optio-api/src/discovery.ts` — `discoverPrefixes(db)` function
- `packages/optio-ui/src/hooks/usePrefixDiscovery.ts` — `usePrefixes()` and `usePrefixDiscovery()` hooks

**Modify:**
- `packages/optio-contracts/src/contract.ts` — add `discoverPrefixes` route to contract
- `packages/optio-contracts/src/index.ts` — export new contract
- `packages/optio-api/src/index.ts` — export `discoverPrefixes`
- `packages/optio-api/src/adapters/fastify.ts` — register `GET /api/optio/prefixes`
- `packages/optio-api/src/adapters/express.ts` — register `GET /api/optio/prefixes`
- `packages/optio-api/src/adapters/nextjs-app.ts` — register `GET /api/optio/prefixes`
- `packages/optio-api/src/adapters/nextjs-pages.ts` — register `GET /api/optio/prefixes`
- `packages/optio-ui/src/client.ts` — add discovery contract to API client
- `packages/optio-ui/src/context/OptioProvider.tsx` — use `usePrefixDiscovery()` for fallback
- `packages/optio-ui/src/index.ts` — export new hooks
- `packages/optio-dashboard/src/cli.ts` — remove `OPTIO_PREFIX`
- `packages/optio-dashboard/src/server.ts` — remove `prefix` from config
- `packages/optio-dashboard/src/app/App.tsx` — auto-select / dropdown logic

**Test:**
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts` — add discovery endpoint test
- `packages/optio-ui/src/__tests__/usePrefixDiscovery.test.ts` — hook unit tests
- `packages/optio-ui/src/__tests__/OptioProvider.test.tsx` — provider priority chain tests

---

### Task 1: Add discovery contract to optio-contracts

**Files:**
- Modify: `packages/optio-contracts/src/contract.ts`
- Modify: `packages/optio-contracts/src/index.ts`

- [ ] **Step 1: Add the discovery contract**

In `packages/optio-contracts/src/contract.ts`, add a new contract after the existing `processesContract`:

```typescript
export const discoveryContract = c.router({
  prefixes: {
    method: 'GET',
    path: '/optio/prefixes',
    responses: {
      200: z.object({ prefixes: z.array(z.string()) }),
    },
    summary: 'Discover active optio prefixes in the database',
  },
});
```

- [ ] **Step 2: Export the new contract**

In `packages/optio-contracts/src/index.ts`, add:

```typescript
export { discoveryContract } from './contract.js';
```

- [ ] **Step 3: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-contracts/tsconfig.json --noEmit`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-contracts/src/contract.ts packages/optio-contracts/src/index.ts
git commit -m "feat(contracts): add discovery contract for prefix auto-detection"
```

---

### Task 2: Implement discoverPrefixes handler in optio-api

**Files:**
- Create: `packages/optio-api/src/discovery.ts`
- Modify: `packages/optio-api/src/index.ts`

- [ ] **Step 1: Create the discovery module**

Create `packages/optio-api/src/discovery.ts`:

```typescript
import type { Db } from 'mongodb';

const REQUIRED_FIELDS = ['processId', 'rootId', 'depth'];

export async function discoverPrefixes(db: Db): Promise<string[]> {
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
```

- [ ] **Step 2: Export from index**

In `packages/optio-api/src/index.ts`, add this line at the end:

```typescript
// Discovery
export { discoverPrefixes } from './discovery.js';
```

- [ ] **Step 3: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-api/src/discovery.ts packages/optio-api/src/index.ts
git commit -m "feat(api): add discoverPrefixes handler"
```

---

### Task 3: Register discovery endpoint in all adapters

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`
- Modify: `packages/optio-api/src/adapters/express.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-app.ts`
- Modify: `packages/optio-api/src/adapters/nextjs-pages.ts`

Each adapter needs a new `GET /api/optio/prefixes` route that calls `discoverPrefixes(db)` and returns `{ prefixes }`. This route is registered directly (not via ts-rest) since it's outside the `:prefix` router.

- [ ] **Step 1: Update Fastify adapter**

In `packages/optio-api/src/adapters/fastify.ts`:

Add import at the top (after the existing handler import):
```typescript
import { discoverPrefixes } from '../discovery.js';
```

Add this route inside `registerOptioApi`, right before `app.register(s.plugin(routes))` (before line 69):
```typescript
  app.get('/api/optio/prefixes', async (_request, reply) => {
    const prefixes = await discoverPrefixes(db);
    return reply.send({ prefixes });
  });
```

- [ ] **Step 2: Update Express adapter**

In `packages/optio-api/src/adapters/express.ts`:

Add import at the top:
```typescript
import { discoverPrefixes } from '../discovery.js';
```

Add this route inside `registerOptioApi`, before the `createExpressEndpoints` call:
```typescript
  app.get('/api/optio/prefixes', async (_req, res) => {
    const prefixes = await discoverPrefixes(db);
    res.json({ prefixes });
  });
```

The route must be registered before the ts-rest endpoints so it doesn't get captured by the `:prefix` path parameter.

- [ ] **Step 3: Update Next.js App Router adapter**

In `packages/optio-api/src/adapters/nextjs-app.ts`:

Add import at the top:
```typescript
import { discoverPrefixes } from '../discovery.js';
```

In the `GET` handler inside `createOptioRouteHandlers`, add a match at the top of the handler (before the existing stream regex checks):
```typescript
    if (url.pathname.endsWith('/api/optio/prefixes')) {
      const prefixes = await discoverPrefixes(db);
      return new Response(JSON.stringify({ prefixes }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }
```

- [ ] **Step 4: Update Next.js Pages Router adapter**

In `packages/optio-api/src/adapters/nextjs-pages.ts`:

Add import at the top:
```typescript
import { discoverPrefixes } from '../discovery.js';
```

In the returned handler inside `createOptioHandler`, add a match at the top (before the existing stream regex checks):
```typescript
    if (req.url?.endsWith('/api/optio/prefixes') && req.method === 'GET') {
      const prefixes = await discoverPrefixes(db);
      res.status(200).json({ prefixes });
      return;
    }
```

- [ ] **Step 5: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-api/tsconfig.json --noEmit`
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-api/src/adapters/fastify.ts packages/optio-api/src/adapters/express.ts packages/optio-api/src/adapters/nextjs-app.ts packages/optio-api/src/adapters/nextjs-pages.ts
git commit -m "feat(api): register GET /api/optio/prefixes in all adapters"
```

---

### Task 4: Add integration test for discovery endpoint

**Files:**
- Modify: `packages/optio-api/src/adapters/__tests__/fastify.test.ts`

- [ ] **Step 1: Write the test**

Add these tests at the end of the existing `describe` block in `packages/optio-api/src/adapters/__tests__/fastify.test.ts`:

```typescript
  it('GET /api/optio/prefixes — returns empty when no collections', async () => {
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/prefixes',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.prefixes).toEqual([]);
  });

  it('GET /api/optio/prefixes — discovers prefixes from collections with optio schema', async () => {
    await seedProcess();
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/prefixes',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.prefixes).toEqual(['optio']);
  });

  it('GET /api/optio/prefixes — ignores collections without optio schema', async () => {
    await db.collection('fake_processes').insertOne({ unrelated: true });
    const app = createApp();

    const res = await app.inject({
      method: 'GET',
      url: '/api/optio/prefixes',
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(body.prefixes).toEqual([]);

    await db.collection('fake_processes').drop();
  });
```

- [ ] **Step 2: Run the test**

Run: `cd packages/optio-api && npx vitest run src/adapters/__tests__/fastify.test.ts`
Expected: All tests pass, including the three new ones.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-api/src/adapters/__tests__/fastify.test.ts
git commit -m "test(api): add integration tests for prefix discovery endpoint"
```

---

### Task 5: Add UI hooks for prefix discovery

**Files:**
- Create: `packages/optio-ui/src/hooks/usePrefixDiscovery.ts`
- Modify: `packages/optio-ui/src/client.ts`
- Modify: `packages/optio-ui/src/index.ts`

- [ ] **Step 1: Add discovery contract to the UI client**

In `packages/optio-ui/src/client.ts`, import the new contract and add it to the router:

Replace the existing contract import:
```typescript
import { processesContract, discoveryContract } from 'optio-contracts';
```

Replace the `apiContract` line:
```typescript
const apiContract = c.router(
  { processes: processesContract, discovery: discoveryContract },
  { pathPrefix: '/api' },
);
```

- [ ] **Step 2: Create the discovery hooks**

Create `packages/optio-ui/src/hooks/usePrefixDiscovery.ts`:

```typescript
import { useOptioClient } from '../context/useOptioContext.js';

interface UsePrefixesResult {
  prefixes: string[];
  isLoading: boolean;
  error: unknown;
}

export function usePrefixes(): UsePrefixesResult {
  const client = useOptioClient();
  const { data, isLoading, error } = client.discovery.prefixes.useQuery(
    ['optio-prefixes'],
    {},
  );
  return {
    prefixes: data?.body?.prefixes ?? [],
    isLoading,
    error,
  };
}

interface UsePrefixDiscoveryResult {
  prefix: string | null;
  prefixes: string[];
  isLoading: boolean;
}

export function usePrefixDiscovery(): UsePrefixDiscoveryResult {
  const { prefixes, isLoading } = usePrefixes();
  const prefix = prefixes.length === 1 ? prefixes[0] : null;
  return { prefix, prefixes, isLoading };
}
```

- [ ] **Step 3: Export from index**

In `packages/optio-ui/src/index.ts`, add:

```typescript
export { usePrefixes, usePrefixDiscovery } from './hooks/usePrefixDiscovery.js';
```

- [ ] **Step 4: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-ui/tsconfig.json --noEmit`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-ui/src/hooks/usePrefixDiscovery.ts packages/optio-ui/src/client.ts packages/optio-ui/src/index.ts
git commit -m "feat(ui): add usePrefixes and usePrefixDiscovery hooks"
```

---

### Task 6: Update OptioProvider to use auto-discovery fallback

**Files:**
- Modify: `packages/optio-ui/src/context/OptioProvider.tsx`

- [ ] **Step 1: Update the provider**

Replace the full content of `packages/optio-ui/src/context/OptioProvider.tsx`:

```tsx
import { createContext, useMemo, type ReactNode } from 'react';
import { createOptioClient, type OptioClient } from '../client.js';
import { usePrefixDiscovery } from '../hooks/usePrefixDiscovery.js';

interface OptioContextValue {
  prefix: string;
  baseUrl: string;
  client: OptioClient;
}

export const OptioContext = createContext<OptioContextValue>(null as any);

interface OptioProviderProps {
  prefix?: string;
  baseUrl?: string;
  children: ReactNode;
}

function OptioProviderInner({ explicitPrefix, baseUrl, client, children }: {
  explicitPrefix: string | undefined;
  baseUrl: string;
  client: OptioClient;
  children: ReactNode;
}) {
  const { prefix: discoveredPrefix } = usePrefixDiscovery();
  const prefix = explicitPrefix ?? discoveredPrefix ?? 'optio';

  return (
    <OptioContext.Provider value={{ prefix, baseUrl, client }}>
      {children}
    </OptioContext.Provider>
  );
}

export function OptioProvider({ prefix, baseUrl = '', children }: OptioProviderProps) {
  const client = useMemo(() => createOptioClient(baseUrl), [baseUrl]);

  return (
    <OptioContext.Provider value={{ prefix: prefix ?? 'optio', baseUrl, client }}>
      <OptioProviderInner explicitPrefix={prefix} baseUrl={baseUrl} client={client}>
        {children}
      </OptioProviderInner>
    </OptioContext.Provider>
  );
}
```

Note: The outer `OptioContext.Provider` is needed so that `usePrefixDiscovery()` inside `OptioProviderInner` can access the client. The inner one overwrites the context with the resolved prefix. The outer starts with `prefix ?? 'optio'` as the initial value while discovery loads.

- [ ] **Step 2: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-ui/tsconfig.json --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add packages/optio-ui/src/context/OptioProvider.tsx
git commit -m "feat(ui): OptioProvider falls back to auto-discovered prefix"
```

---

### Task 7: Add UI hook and provider tests

**Files:**
- Create: `packages/optio-ui/src/__tests__/usePrefixDiscovery.test.ts`
- Create: `packages/optio-ui/src/__tests__/OptioProvider.test.tsx`

- [ ] **Step 1: Write usePrefixDiscovery tests**

Create `packages/optio-ui/src/__tests__/usePrefixDiscovery.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { usePrefixDiscovery } from '../hooks/usePrefixDiscovery.js';

// Mock the context hook
vi.mock('../context/useOptioContext.js', () => ({
  useOptioClient: () => ({
    discovery: {
      prefixes: {
        useQuery: (_key: unknown, _args: unknown) => mockQueryResult,
      },
    },
  }),
}));

let mockQueryResult: { data: any; isLoading: boolean; error: unknown };

describe('usePrefixDiscovery', () => {
  it('returns null prefix when loading', () => {
    mockQueryResult = { data: undefined, isLoading: true, error: null };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe(null);
    expect(result.current.prefixes).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });

  it('returns the prefix when exactly one is found', () => {
    mockQueryResult = {
      data: { body: { prefixes: ['myapp'] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe('myapp');
    expect(result.current.prefixes).toEqual(['myapp']);
  });

  it('returns null prefix when multiple are found', () => {
    mockQueryResult = {
      data: { body: { prefixes: ['optio', 'myapp'] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe(null);
    expect(result.current.prefixes).toEqual(['optio', 'myapp']);
  });

  it('returns null prefix when none are found', () => {
    mockQueryResult = {
      data: { body: { prefixes: [] } },
      isLoading: false,
      error: null,
    };
    const { result } = renderHook(() => usePrefixDiscovery());
    expect(result.current.prefix).toBe(null);
    expect(result.current.prefixes).toEqual([]);
  });
});
```

- [ ] **Step 2: Write OptioProvider tests**

Create `packages/optio-ui/src/__tests__/OptioProvider.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OptioProvider } from '../context/OptioProvider.js';
import { useOptioPrefix } from '../context/useOptioContext.js';

// Mock the discovery hook
let mockDiscoveryResult = { prefix: null as string | null, prefixes: [] as string[], isLoading: false };

vi.mock('../hooks/usePrefixDiscovery.js', () => ({
  usePrefixDiscovery: () => mockDiscoveryResult,
}));

function PrefixDisplay() {
  const prefix = useOptioPrefix();
  return <div data-testid="prefix">{prefix}</div>;
}

function renderWithProvider(props: { prefix?: string }) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <OptioProvider {...props}>
        <PrefixDisplay />
      </OptioProvider>
    </QueryClientProvider>,
  );
}

describe('OptioProvider prefix resolution', () => {
  it('uses explicit prefix when provided', () => {
    mockDiscoveryResult = { prefix: 'discovered', prefixes: ['discovered'], isLoading: false };
    renderWithProvider({ prefix: 'explicit' });
    expect(screen.getByTestId('prefix').textContent).toBe('explicit');
  });

  it('uses discovered prefix when no explicit prefix given', () => {
    mockDiscoveryResult = { prefix: 'discovered', prefixes: ['discovered'], isLoading: false };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('discovered');
  });

  it('falls back to optio when no explicit prefix and discovery returns null', () => {
    mockDiscoveryResult = { prefix: null, prefixes: [], isLoading: false };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('optio');
  });
});
```

- [ ] **Step 3: Run the tests**

Run: `cd packages/optio-ui && npx vitest run src/__tests__/`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-ui/src/__tests__/usePrefixDiscovery.test.ts packages/optio-ui/src/__tests__/OptioProvider.test.tsx
git commit -m "test(ui): add tests for prefix discovery hooks and provider"
```

---

### Task 8: Update dashboard to use auto-discovery

**Files:**
- Modify: `packages/optio-dashboard/src/cli.ts`
- Modify: `packages/optio-dashboard/src/server.ts`
- Modify: `packages/optio-dashboard/src/app/App.tsx`

- [ ] **Step 1: Remove prefix from CLI config**

Replace `packages/optio-dashboard/src/cli.ts` with:

```typescript
#!/usr/bin/env node
import { startServer } from './server.js';

const config = {
  mongodbUrl: process.env.MONGODB_URL || 'mongodb://localhost:27017/optio',
  redisUrl: process.env.REDIS_URL || 'redis://localhost:6379',
  port: parseInt(process.env.PORT || '3000', 10),
};

startServer(config).catch((err) => {
  console.error('Failed to start Optio Dashboard:', err);
  process.exit(1);
});
```

- [ ] **Step 2: Remove prefix from server config**

In `packages/optio-dashboard/src/server.ts`:

Remove `prefix` from the `DashboardConfig` interface:
```typescript
export interface DashboardConfig {
  mongodbUrl: string;
  redisUrl: string;
  port: number;
}
```

Remove `prefix` from the `registerOptioApi` call (line 31):
```typescript
  await registerOptioApi(app, { db, redis });
```

- [ ] **Step 3: Update dashboard App to use auto-discovery with dropdown**

Replace `packages/optio-dashboard/src/app/App.tsx` with:

```tsx
import { useState } from 'react';
import { Layout, Typography, Select, Alert } from 'antd';
import {
  OptioProvider,
  ProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
  usePrefixes,
} from 'optio-ui';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

function PrefixSelector({ onSelect }: { onSelect: (prefix: string) => void }) {
  const { prefixes, isLoading, error } = usePrefixes();

  if (isLoading) return null;
  if (error) return <Alert type="error" message="Failed to detect prefixes" />;
  if (prefixes.length === 0) {
    return <Alert type="info" message="No optio instance detected in the database" />;
  }

  return (
    <div style={{ padding: 24 }}>
      <Typography.Text>Multiple optio instances detected. Select one:</Typography.Text>
      <Select
        style={{ width: '100%', marginTop: 8 }}
        placeholder="Select prefix"
        options={prefixes.map((p) => ({ label: p, value: p }))}
        onChange={onSelect}
      />
    </div>
  );
}

function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { tree, logs, connected: treeConnected } = useProcessStream(
    selectedProcessId ?? undefined,
  );
  const { launch, cancel, dismiss } = useProcessActions();

  return (
    <Layout>
      <Sider width={400} style={{ background: '#fff', overflow: 'auto' }}>
        <ProcessList
          processes={processes}
          loading={!listConnected}
          onLaunch={launch}
          onCancel={cancel}
          onProcessClick={setSelectedProcessId}
        />
      </Sider>
      <Content style={{ padding: '24px', overflow: 'auto' }}>
        {selectedProcessId ? (
          <>
            <ProcessTreeView
              treeData={tree}
              sseState={{ connected: treeConnected }}
              onCancel={cancel}
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
  );
}

function AppContent() {
  const { prefixes, isLoading } = usePrefixes();
  const [manualPrefix, setManualPrefix] = useState<string | null>(null);

  if (isLoading) return null;

  // Multiple prefixes and user hasn't picked yet
  if (prefixes.length > 1 && !manualPrefix) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
        </Header>
        <PrefixSelector onSelect={setManualPrefix} />
      </Layout>
    );
  }

  // Zero prefixes
  if (prefixes.length === 0) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
        </Header>
        <Alert
          type="info"
          message="No optio instance detected in the database"
          style={{ margin: 24 }}
        />
      </Layout>
    );
  }

  const prefix = manualPrefix ?? prefixes[0];

  return (
    <OptioProvider prefix={prefix}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
          <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
        </Header>
        <Dashboard />
      </Layout>
    </OptioProvider>
  );
}

export default function App() {
  return (
    <OptioProvider>
      <AppContent />
    </OptioProvider>
  );
}
```

Note: The outer `<OptioProvider>` (no prefix) provides the client context needed for `usePrefixes()` inside `AppContent`. Once a prefix is resolved, the inner `<OptioProvider prefix={prefix}>` overrides the context for `Dashboard` and its children.

- [ ] **Step 4: Remove .env.example OPTIO_PREFIX if it exists**

Check if `packages/optio-dashboard/.env.example` exists and remove the `OPTIO_PREFIX` line.

- [ ] **Step 5: Build and verify**

Run: `node_modules/.bin/tsc -p packages/optio-dashboard/tsconfig.json --noEmit`
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-dashboard/src/cli.ts packages/optio-dashboard/src/server.ts packages/optio-dashboard/src/app/App.tsx
git commit -m "feat(dashboard): remove OPTIO_PREFIX, use auto-discovery with dropdown"
```

---

### Task 9: Run full test suite

- [ ] **Step 1: Run all tests**

Run: `pnpm test`
Expected: All tests pass across all packages.

- [ ] **Step 2: Fix any failures**

Address any test failures before proceeding.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address test failures from prefix discovery changes"
```
