# optio-api Authentication Design

## Overview

Add optional authentication support to `optio-api` via a callback-based approach that integrates with host applications' existing auth systems.

## Goals

- Let host applications protect Optio API endpoints using their own auth mechanisms
- Support a read/write permission split (viewer vs. operator roles)
- Keep auth opt-in — omitting the callback preserves current open-access behavior
- No changes to `optio-contracts` or `optio-ui`

## Phase 1: Auth Callback in optio-api

### Types

```ts
type OptioRole = 'viewer' | 'operator';

// Per-adapter — NativeRequest is the framework's request type
type AuthCallback<NativeRequest> =
  (req: NativeRequest) => Promise<OptioRole | null> | OptioRole | null;
```

The callback receives the framework-native request object (Fastify `FastifyRequest`, Express `Request`, Next.js `NextRequest` / `NextApiRequest`), giving the host app full access to headers, cookies, sessions, etc.

Return values:
- `'operator'` — full access to all endpoints
- `'viewer'` — read endpoints only; write endpoints return 403
- `null` — 401 Unauthorized on all endpoints

### Options Change

Each adapter's `OptioApiOptions` gains an optional `authenticate` field:

```ts
export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
  authenticate?: AuthCallback<FrameworkSpecificRequest>;
}
```

When `authenticate` is not provided, all requests get full access (current behavior).

### Endpoint Classification

**Read** (viewer + operator):
- `list` — GET `/:prefix`
- `get` — GET `/:prefix/:id`
- `getTree` — GET `/:prefix/:id/tree`
- `getLog` — GET `/:prefix/:id/log`
- `getTreeLog` — GET `/:prefix/:id/tree/log`
- List SSE stream — GET `/:prefix/stream`
- Tree SSE stream — GET `/:prefix/:id/tree/stream`

**Write** (operator only):
- `launch` — POST `/:prefix/:id/launch`
- `cancel` — POST `/:prefix/:id/cancel`
- `dismiss` — POST `/:prefix/:id/dismiss`
- `resync` — POST `/:prefix/resync`

### Auth Check Logic

Each adapter implements a helper that runs at the top of every route handler and SSE endpoint:

```ts
async function checkAuth(req, authenticate, isWrite) {
  if (!authenticate) return; // no auth configured — allow all
  const role = await authenticate(req);
  if (role === null) throw new HttpError(401, 'Unauthorized');
  if (isWrite && role === 'viewer') throw new HttpError(403, 'Forbidden');
}
```

The `isWrite` flag is hardcoded per endpoint. The error is translated to the appropriate HTTP response by each adapter using framework-native patterns (Fastify `reply.code()`, Express `res.status()`, Next.js `NextResponse`).

### Adapter Changes

All four adapters (Fastify, Express, Next.js Pages, Next.js App) get the same structural change:

1. `OptioApiOptions` type gains `authenticate?` with the framework-native request type
2. Each ts-rest route handler gets `await checkAuth(req, authenticate, isWrite)` at the top
3. Each SSE stream endpoint gets the same check, classified as read

No changes to `handlers.ts` — auth lives entirely in the adapter layer. Handlers remain pure functions of `(db, prefix, params)`.

### No Changes to Other Packages

- **optio-contracts**: No changes. Auth is a server-side concern. 401/403 are standard HTTP responses and don't need ts-rest contract definitions.
- **optio-ui**: No changes in Phase 1. The hooks will naturally receive 401/403 responses; host applications handle those at their own app level.

### Usage Example

```ts
import { registerOptioApi } from 'optio-api/fastify';

registerOptioApi(app, {
  db,
  redis,
  authenticate: async (req) => {
    // Example: passport integration
    if (!req.user) return null;
    return req.user.isAdmin ? 'operator' : 'viewer';
  },
});
```

## Phase 2: Dashboard Auth (future, not planned for implementation)

When the `OPTIO_PASSWORD` environment variable is set, the dashboard activates a simple password-based auth system. When the variable is not set, behavior is unchanged (open access).

### Server Side

- Dependency: `@fastify/cookie` for signed cookies
- `POST /login` — accepts `{ password }`, validates against `OPTIO_PASSWORD`, sets a signed cookie, returns 200 or 401
- `POST /logout` — clears the cookie, returns 200
- Auth callback wired into optio-api: checks the signed cookie, returns `'operator'` if valid, `null` if not

### Client Side

- A password-only login form (no username) — lives in the dashboard package, not in optio-ui
- On app load, makes a request; if 401, shows the login form
- On successful login, cookie is set automatically by the browser, app renders normally
- A logout button in the dashboard header
