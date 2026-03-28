# Multi-Framework Adapter Support for optio-api

**Date:** 2026-03-28
**Status:** Draft

## Problem

optio-api is positioned as an embeddable component for any Node.js app, but currently only provides a Fastify adapter. Users on Express or Next.js cannot use it without writing their own wiring code.

## Goal

Add Express and Next.js adapters so that optio-api works out of the box with the four most common Node.js server patterns: Fastify, Express, Next.js Pages Router, and Next.js App Router. All four adapters expose the same REST endpoints and SSE streams, making them equally consumable by optio-ui.

## Design

### Package Structure

New adapter files alongside the existing one:

```
packages/optio-api/src/adapters/
  fastify.ts        (existing, refactored)
  express.ts        (new)
  nextjs-pages.ts   (new)
  nextjs-app.ts     (new)
```

Package exports:

```
optio-api              → framework-agnostic handlers, pollers, publishers (unchanged)
optio-api/fastify      → registerOptioApi(app, opts)
optio-api/express      → registerOptioApi(app, opts)
optio-api/nextjs/pages → createOptioHandler(opts)
optio-api/nextjs/app   → createOptioRouteHandlers(opts)
```

### Adapter API

All adapters accept the same options:

```typescript
interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string; // defaults to 'optio'
}
```

**Fastify:**

```typescript
import { registerOptioApi } from 'optio-api/fastify';
const app = Fastify();
registerOptioApi(app, { db, redis });
```

**Express:**

```typescript
import { registerOptioApi } from 'optio-api/express';
const app = express();
registerOptioApi(app, { db, redis });
```

**Next.js Pages Router:**

```typescript
// pages/api/processes/[...optio].ts
import { createOptioHandler } from 'optio-api/nextjs/pages';
export default createOptioHandler({ db, redis });
```

Returns a single `NextApiHandler` that routes all requests via the catch-all segment.

**Next.js App Router:**

```typescript
// app/api/processes/[...optio]/route.ts
import { createOptioRouteHandlers } from 'optio-api/nextjs/app';
export const { GET, POST } = createOptioRouteHandlers({ db, redis });
```

Returns an object with `GET` and `POST` exports that use the Web API `Request`/`Response` model.

### ts-rest Integration

Each adapter uses the ts-rest binding for its framework:

| Adapter | ts-rest package |
|---------|----------------|
| Fastify | `@ts-rest/fastify` (existing) |
| Express | `@ts-rest/express` |
| Next.js Pages | `@ts-rest/next` |
| Next.js App | `@ts-rest/serverless` |

All share the same contract from `optio-contracts`.

### SSE Streaming

Each adapter implements SSE using its framework's conventions:

- **Fastify**: `reply.raw` (Node `ServerResponse`) — already implemented
- **Express**: `res.write()` / `res.flush()` on the Node response
- **Next.js Pages Router**: `res.write()` via `NextApiResponse`
- **Next.js App Router**: `new Response(readableStream)` with a `ReadableStream` pushing events via its controller

The existing stream pollers (`createListPoller`, `createTreePoller`) accept a `sendEvent` callback and remain unchanged. Each adapter provides its own callback implementation.

### Dependencies

Framework-specific dependencies go in both `peerDependencies` and `optionalDependencies` — users only install what they use:

- `fastify`, `@ts-rest/fastify` — for Fastify adapter
- `express`, `@ts-rest/express` — for Express adapter
- `next`, `@ts-rest/next` — for Next.js Pages adapter
- `@ts-rest/serverless` — for Next.js App adapter

### Breaking Change

The Fastify adapter's current two-function API (`registerProcessRoutes` + `registerProcessStream`) is replaced by a single `registerOptioApi` function. The `optio-dashboard` package must be updated to use the new API.

### What Does NOT Change

- `optio-contracts` — the ts-rest contract is already framework-agnostic
- `optio-ui` — communicates via HTTP, unaware of backend framework
- `optio-core` — Python-side, unaffected
- Framework-agnostic handlers in `src/handlers.ts`
- Stream pollers in `src/stream-poller.ts`
- Publishers in `src/publisher.ts`

## Testing

Integration tests for each adapter: spin up a real server instance, make HTTP requests, and verify responses and SSE streams behave correctly.

## Documentation Updates

- `packages/optio-api/README.md`: Add usage examples for all four adapters
- Root `README.md`: Mention that Fastify, Express, and Next.js are supported
