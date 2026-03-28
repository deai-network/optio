# Optio Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone management dashboard package (`optio-dashboard`) that bundles optio-api and optio-ui into a single runnable app.

**Architecture:** A Fastify server registers optio-api routes and serves a pre-built React app using optio-ui components. Configured via environment variables, runnable via `npx optio-dashboard`.

**Tech Stack:** Fastify, React 19, Vite, Ant Design, optio-api, optio-ui, TypeScript

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/optio-dashboard/package.json` | Create | Package config with bin entry, scripts, dependencies |
| `packages/optio-dashboard/tsconfig.json` | Create | Server-side TypeScript config |
| `packages/optio-dashboard/tsconfig.app.json` | Create | Client-side TypeScript config for Vite |
| `packages/optio-dashboard/vite.config.ts` | Create | Vite config to build React app to dist/public |
| `packages/optio-dashboard/.env.example` | Create | Example environment variables |
| `packages/optio-dashboard/src/cli.ts` | Create | CLI entry point: parse env, start server |
| `packages/optio-dashboard/src/server.ts` | Create | Fastify setup: routes, SSE, static files |
| `packages/optio-dashboard/src/app/index.html` | Create | HTML shell |
| `packages/optio-dashboard/src/app/main.tsx` | Create | React entry point |
| `packages/optio-dashboard/src/app/App.tsx` | Create | Main app component with list + detail layout |
| `packages/optio-dashboard/src/app/i18n.ts` | Create | i18next setup with English translations |
| `packages/optio-dashboard/README.md` | Create | Package documentation |
| `README.md` | Modify | Add to packages table, swap architecture diagram |

---

### Task 1: Package scaffolding

**Files:**
- Create: `packages/optio-dashboard/package.json`
- Create: `packages/optio-dashboard/tsconfig.json`
- Create: `packages/optio-dashboard/tsconfig.app.json`
- Create: `packages/optio-dashboard/vite.config.ts`
- Create: `packages/optio-dashboard/.env.example`

- [ ] **Step 1: Create package.json**

Create `packages/optio-dashboard/package.json`:

```json
{
  "name": "optio-dashboard",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "bin": {
    "optio-dashboard": "./dist/cli.js"
  },
  "scripts": {
    "build": "vite build && tsc -p tsconfig.json",
    "build:app": "vite build",
    "build:server": "tsc -p tsconfig.json",
    "dev": "vite build --watch & tsc -p tsconfig.json --watch",
    "start": "node dist/cli.js"
  },
  "dependencies": {
    "optio-api": "workspace:*",
    "optio-ui": "workspace:*",
    "optio-contracts": "workspace:*",
    "fastify": "^5.2.0",
    "@fastify/static": "^8.0.0",
    "@ts-rest/fastify": "^3.51.0",
    "mongodb": "^6.12.0",
    "ioredis": "^5.4.0",
    "react": "^19.2.4",
    "react-dom": "^19.2.4",
    "@tanstack/react-query": "^5.95.2",
    "antd": "^5.29.3",
    "@ant-design/icons": "^5.6.0",
    "i18next": "^25.10.10",
    "react-i18next": "^17.0.0",
    "@ts-rest/core": "^3.51.0",
    "@ts-rest/react-query": "^3.51.0"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "vite": "^6.0.0",
    "@vitejs/plugin-react": "^4.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@types/node": "^22.0.0"
  }
}
```

- [ ] **Step 2: Create tsconfig.json (server)**

Create `packages/optio-dashboard/tsconfig.json`:

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "outDir": "dist",
    "rootDir": "src",
    "jsx": "react-jsx",
    "noEmit": false
  },
  "include": ["src/cli.ts", "src/server.ts"]
}
```

- [ ] **Step 3: Create tsconfig.app.json (client)**

Create `packages/optio-dashboard/tsconfig.app.json`:

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "jsx": "react-jsx",
    "noEmit": true
  },
  "include": ["src/app"]
}
```

- [ ] **Step 4: Create vite.config.ts**

Create `packages/optio-dashboard/vite.config.ts`:

```typescript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  root: path.resolve(__dirname, 'src/app'),
  build: {
    outDir: path.resolve(__dirname, 'dist/public'),
    emptyOutDir: true,
  },
});
```

- [ ] **Step 5: Create .env.example**

Create `packages/optio-dashboard/.env.example`:

```
MONGODB_URL=mongodb://localhost:27017/optio
REDIS_URL=redis://localhost:6379
OPTIO_PREFIX=optio
PORT=3000
```

- [ ] **Step 6: Commit**

```bash
git add packages/optio-dashboard/
git commit -m "feat: scaffold optio-dashboard package"
```

---

### Task 2: Server side (cli.ts + server.ts)

**Files:**
- Create: `packages/optio-dashboard/src/cli.ts`
- Create: `packages/optio-dashboard/src/server.ts`

- [ ] **Step 1: Create server.ts**

Create `packages/optio-dashboard/src/server.ts`:

```typescript
import Fastify from 'fastify';
import fastifyStatic from '@fastify/static';
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { registerProcessRoutes, registerProcessStream } from 'optio-api/fastify';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export interface DashboardConfig {
  mongodbUrl: string;
  redisUrl: string;
  prefix: string;
  port: number;
}

export async function startServer(config: DashboardConfig) {
  const app = Fastify({ logger: true });

  // Connect to MongoDB
  const mongoClient = new MongoClient(config.mongodbUrl);
  await mongoClient.connect();
  const dbName = new URL(config.mongodbUrl).pathname.slice(1) || 'optio';
  const db = mongoClient.db(dbName);

  // Connect to Redis
  const redis = new Redis(config.redisUrl);

  // Register Optio API routes and SSE streams
  await registerProcessRoutes(app, { db, redis, prefix: config.prefix });
  await registerProcessStream(app, { db, redis, prefix: config.prefix });

  // Serve the pre-built React app
  await app.register(fastifyStatic, {
    root: path.join(__dirname, 'public'),
    wildcard: false,
  });

  // SPA fallback: serve index.html for all non-API, non-file routes
  app.setNotFoundHandler(async (request, reply) => {
    if (request.url.startsWith('/api/')) {
      reply.code(404).send({ message: 'Not found' });
      return;
    }
    return reply.sendFile('index.html');
  });

  // Graceful shutdown
  const shutdown = async () => {
    app.log.info('Shutting down...');
    await app.close();
    redis.disconnect();
    await mongoClient.close();
    process.exit(0);
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  await app.listen({ port: config.port, host: '0.0.0.0' });

  return app;
}
```

- [ ] **Step 2: Create cli.ts**

Create `packages/optio-dashboard/src/cli.ts`:

```typescript
#!/usr/bin/env node
import { startServer } from './server.js';

const config = {
  mongodbUrl: process.env.MONGODB_URL || 'mongodb://localhost:27017/optio',
  redisUrl: process.env.REDIS_URL || 'redis://localhost:6379',
  prefix: process.env.OPTIO_PREFIX || 'optio',
  port: parseInt(process.env.PORT || '3000', 10),
};

startServer(config).catch((err) => {
  console.error('Failed to start Optio Dashboard:', err);
  process.exit(1);
});
```

- [ ] **Step 3: Verify server compiles**

Run: `cd packages/optio-dashboard && npx tsc -p tsconfig.json --noEmit`
Expected: Success (or type errors to fix)

Note: `registerProcessStream` takes `OptioApiOptions` which has `{ db, redis, prefix }`. Verify this matches the import. If `registerProcessStream` doesn't need `redis`, pass only what it needs.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-dashboard/src/
git commit -m "feat: add optio-dashboard server (cli.ts + server.ts)"
```

---

### Task 3: React app — i18n setup

**Files:**
- Create: `packages/optio-dashboard/src/app/i18n.ts`

- [ ] **Step 1: Create i18n.ts**

Create `packages/optio-dashboard/src/app/i18n.ts`:

```typescript
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

i18n.use(initReactI18next).init({
  lng: 'en',
  resources: {
    en: {
      translation: {
        'processes.launch': 'Launch',
        'processes.cancel': 'Cancel',
        'processes.filterAll': 'All',
        'processes.filterActive': 'Active',
        'processes.filterHideCompleted': 'Hide completed',
        'processes.filterErrors': 'Errors',
        'processes.showDetails': 'Show details',
        'processes.showSpecial': 'Show special',
        'status.idle': 'Idle',
        'status.scheduled': 'Scheduled',
        'status.running': 'Running',
        'status.done': 'Done',
        'status.failed': 'Failed',
        'status.cancel_requested': 'Cancel requested',
        'status.cancelling': 'Cancelling',
        'status.cancelled': 'Cancelled',
        'common.noData': 'No data',
      },
    },
  },
  interpolation: { escapeValue: false },
});

export default i18n;
```

- [ ] **Step 2: Commit**

```bash
git add packages/optio-dashboard/src/app/i18n.ts
git commit -m "feat: add i18n setup with English translations for optio-ui"
```

---

### Task 4: React app — main component and entry point

**Files:**
- Create: `packages/optio-dashboard/src/app/index.html`
- Create: `packages/optio-dashboard/src/app/main.tsx`
- Create: `packages/optio-dashboard/src/app/App.tsx`

- [ ] **Step 1: Create index.html**

Create `packages/optio-dashboard/src/app/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Optio Dashboard</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="./main.tsx"></script>
</body>
</html>
```

- [ ] **Step 2: Create main.tsx**

Create `packages/optio-dashboard/src/app/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import './i18n.js';
import App from './App.js';

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
```

- [ ] **Step 3: Create App.tsx**

Create `packages/optio-dashboard/src/app/App.tsx`:

```tsx
import { useState } from 'react';
import { Layout, Typography } from 'antd';
import {
  OptioProvider,
  ProcessList,
  ProcessTreeView,
  ProcessLogPanel,
  useProcessActions,
  useProcessStream,
  useProcessListStream,
} from 'optio-ui';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

const prefix = (window as any).__OPTIO_PREFIX__ || 'optio';

function Dashboard() {
  const [selectedProcessId, setSelectedProcessId] = useState<string | null>(null);
  const { processes, connected: listConnected } = useProcessListStream();
  const { tree, logs, connected: treeConnected } = useProcessStream(
    selectedProcessId ?? undefined,
  );
  const { launch, cancel, dismiss } = useProcessActions();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
        <Title level={4} style={{ color: '#fff', margin: 0 }}>Optio Dashboard</Title>
      </Header>
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
    </Layout>
  );
}

export default function App() {
  return (
    <OptioProvider prefix={prefix}>
      <Dashboard />
    </OptioProvider>
  );
}
```

- [ ] **Step 4: Build the React app**

Run: `cd packages/optio-dashboard && npx vite build`
Expected: Build succeeds, output in `dist/public/`

- [ ] **Step 5: Commit**

```bash
git add packages/optio-dashboard/src/app/
git commit -m "feat: add React dashboard app with process list and detail view"
```

---

### Task 5: Full build and smoke test

- [ ] **Step 1: Install dependencies**

Run: `cd /home/csillag/deai/optio && pnpm install`

- [ ] **Step 2: Build everything**

Run: `cd packages/optio-dashboard && npm run build`
Expected: Both Vite (React app) and tsc (server) succeed. `dist/public/` contains the built React app, `dist/cli.js` and `dist/server.js` exist.

- [ ] **Step 3: Verify the CLI entry point has the shebang**

Run: `head -1 packages/optio-dashboard/dist/cli.js`
Expected: `#!/usr/bin/env node`

Note: TypeScript strips shebangs. If missing, the build script needs adjustment. Fix by prepending the shebang in a post-build step, or use a build plugin.

- [ ] **Step 4: Commit any fixes**

```bash
git add packages/optio-dashboard/
git commit -m "fix: ensure optio-dashboard builds correctly"
```

---

### Task 6: README and root docs update

**Files:**
- Create: `packages/optio-dashboard/README.md`
- Modify: `README.md`

- [ ] **Step 1: Create packages/optio-dashboard/README.md**

```markdown
# optio-dashboard

Standalone management UI for Optio. Bundles optio-api and optio-ui into a single deployable app — no custom backend or frontend required.

## Quick Start

```bash
npx optio-dashboard
```

Or install and run:

```bash
npm install optio-dashboard
optio-dashboard
```

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_URL` | `mongodb://localhost:27017/optio` | MongoDB connection string (database name extracted from URL path) |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `OPTIO_PREFIX` | `optio` | Namespace prefix for MongoDB collections and Redis streams |
| `PORT` | `3000` | HTTP port to listen on |

Copy `.env.example` to `.env` and adjust as needed.

## What This Is

A thin wrapper around [optio-api](../optio-api/README.md) and [optio-ui](../optio-ui/README.md). If you need custom API endpoints, custom UI components, or want to embed Optio into an existing application, use those packages directly instead.

## See Also

- [Optio Overview](../../README.md)
```

- [ ] **Step 2: Update root README.md — packages table**

Add `optio-dashboard` to the packages table:

```markdown
| **optio-dashboard** | Standalone management UI — no code required | [`packages/optio-dashboard/README.md`](packages/optio-dashboard/README.md) |
```

- [ ] **Step 3: Update root README.md — architecture diagram**

Replace `![Architecture](docs/images/architecture.png)` with `![Architecture](docs/images/architecture-future.png)`.

- [ ] **Step 4: Commit**

```bash
git add packages/optio-dashboard/README.md README.md
git commit -m "docs: add optio-dashboard README, update root README with new architecture diagram"
```
