# Optio Dashboard — Design Spec

## Goal

Create a standalone, zero-code management dashboard for Optio. Users who only run the Python core can get a full monitoring UI without building their own Node.js backend or React frontend.

## What It Is

A single deployable package (`optio-dashboard`) that bundles optio-api and optio-ui into a ready-to-run Fastify server serving a pre-built React app. Configured entirely via environment variables. Runnable via `npx optio-dashboard`.

## What It Is NOT

- Not embeddable — for users who don't have their own backend or frontend. If you need custom routes or components, use optio-api and optio-ui directly.
- Not extensible — no plugin system, no custom routes, no custom components.

## Package Structure

```
packages/optio-dashboard/
├── package.json          # bin: { "optio-dashboard": "./dist/cli.js" }
├── .env.example
├── src/
│   ├── cli.ts            # Entry point: parse env, start server
│   ├── server.ts         # Fastify setup: register routes + serve static
│   └── app/              # React app (built at package build time)
│       ├── index.html
│       ├── App.tsx        # OptioProvider + layout with list + detail
│       └── main.tsx       # React entry point
├── tsconfig.json
└── vite.config.ts        # Builds the React app to dist/public/
```

## Server Side

### `cli.ts`

Entry point with shebang (`#!/usr/bin/env node`). Reads environment variables, calls `startServer()`.

### `server.ts`

Creates a Fastify instance and:
1. Connects to MongoDB via `MONGODB_URL`
2. Connects to Redis via `REDIS_URL`
3. Registers optio-api routes: `registerProcessRoutes(app, { db, redis, prefix })`
4. Registers optio-api SSE streams: `registerProcessStream(app, { db, prefix })`
5. Serves `dist/public/` as static files (the pre-built React app)
6. Listens on `PORT`

### Configuration

All via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_URL` | `mongodb://localhost:27017/optio` | MongoDB connection string (database name is extracted from the URL) |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `OPTIO_PREFIX` | `optio` | Namespace prefix for collections and Redis streams |
| `PORT` | `3000` | HTTP port |

`.env.example` provided with these defaults.

## Client Side

A minimal React app using existing optio-ui components:

### `App.tsx`

- `QueryClientProvider` + `OptioProvider` wrapping the layout
- Two-panel layout using Ant Design:
  - Left panel: `ProcessList` with click handler
  - Right panel: `ProcessTreeView` + `ProcessLogPanel` for the selected process
- Uses `useProcessListStream` for the list
- Uses `useProcessStream` for the selected process detail
- Action buttons via `useProcessActions` (launch, cancel, dismiss)
- Basic i18n setup with English translations for all optio-ui keys

### `main.tsx`

Standard React 19 entry point rendering `<App />` into `#root`.

### `index.html`

Minimal HTML shell with `<div id="root">` and Vite script tag.

## Build

- `vite build` compiles the React app to `dist/public/`
- `tsc` compiles the server code to `dist/cli.js` and `dist/server.js`
- The published package includes both compiled server and pre-built static assets

## Dependencies

### Runtime
- `optio-api` (workspace)
- `optio-ui` (workspace)
- `optio-contracts` (workspace, transitive via optio-api)
- `fastify`
- `@fastify/static`
- `mongodb`
- `ioredis`
- `react`, `react-dom`
- `@tanstack/react-query`
- `antd`, `@ant-design/icons`
- `i18next`, `react-i18next`

### Dev
- `vite`
- `@vitejs/plugin-react`
- `typescript`

## Testing

Minimal — this is a thin wrapper:
- One integration test: server starts, serves the static app at `/`, API responds at `/api/processes/:prefix`
- No unit tests (barely any logic to test)

## Documentation

- `packages/optio-dashboard/README.md`: quick start, env vars table, note that this is the "no code" option
- Root `README.md`: add optio-dashboard to the Packages table, replace `architecture.png` with `architecture-future.png`

## Out of Scope

- Authentication (consistent with optio-api — no auth for now)
- Custom routes or components
- Docker image (users can Dockerize themselves)
