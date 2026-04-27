# optio-api Auth Bypass — Fix Design

**Base revision:** `c0220137f3ecdc5b7c7709cfdf82e3f6a81faf93` on branch `main` (as of 2026-04-27T16:17:47Z)

## Summary

Restore enforcement of the `authenticate` callback across all four `optio-api`
adapters. The callback is currently declared on `OptioApiOptions` but invoked
only by the Fastify widget-proxy `preHandler`. All other code paths — REST,
SSE streams, instance discovery — accept unauthenticated requests on every
adapter. The fix adds a single global enforcement point per adapter, mirroring
the pattern that originally shipped in `6b3da7c` (2026-03-29) and was
unintentionally dropped during the multi-database refactor merge in
`4b76363` / `f92024c` (2026-04-14).

## Background

`packages/optio-api/src/auth.ts` defines:

```typescript
export type OptioRole = 'viewer' | 'operator';
export type AuthCallback<TRequest> =
  (req: TRequest) => Promise<OptioRole | null> | OptioRole | null;

export async function checkAuth<TRequest>(
  req: TRequest,
  authenticate: AuthCallback<TRequest>,
  isWrite: boolean,
): Promise<AuthResult | null>;
```

The contract is: the host application supplies `authenticate`, optio-api calls
it on every request and rejects with 401 (no role) or 403 (viewer attempting a
write).

### Current coverage (broken)

| Adapter | REST | SSE | `/api/optio/instances` | Widget proxy |
|---|---|---|---|---|
| fastify | ❌ | ❌ | ❌ | ✅ |
| express | ❌ | ❌ | ❌ | n/a |
| nextjs-app | ❌ | ❌ | ❌ | n/a |
| nextjs-pages | ❌ | ❌ | ❌ | n/a |

Express, nextjs-app, and nextjs-pages do not even import `checkAuth`. The
`AuthCallback<...>` symbol is referenced only as a type, and `@ts-nocheck` at
the top of every adapter file hides the dangling reference.

### How the regression happened

1. **2026-03-29 (`6b3da7c`)** — auth callback added to all four adapters.
   Fastify wired via `app.addHook('onRequest', …)`, Express via
   `app.use('/api', async middleware)`, nextjs-app and nextjs-pages via
   inline `checkAuth` at the top of their unified handlers. A
   `describe('Fastify adapter auth', …)` block in `fastify.test.ts` covered
   401-on-read, 401-on-write, viewer-on-write-403, viewer-on-read-200,
   operator-on-both-200, async callback.
2. **2026-04-14 morning (`c871dfc` and siblings)** — `authenticate` made
   mandatory across all adapters and `checkAuth`. `fdd98c3` removed only the
   now-obsolete "no auth callback — all endpoints open" test.
3. **2026-04-14 afternoon (`f92024c` / `4b76363`)** — multi-database
   instance discovery merged. The branch had been forked from a parent before
   the auth wiring landed, and the rewrite of `registerOptioApi` /
   `createOptioRouteHandlers` / `createOptioHandler` replaced the old function
   bodies without carrying the auth wiring forward. The
   `describe('Fastify adapter auth', …)` block was wiped in the same merge,
   removing the regression alarm.
4. **2026-04-22 (`ebcf85c`)** — widget extensions. The widget-extensions
   design asserts: "the proxy routes reuse optio-api's existing `AuthCallback`
   mechanism" — a false premise at the time it was written. The widget-proxy
   `preHandler` reimplemented `checkAuth` from scratch, which works in
   isolation and is why `/api/widget/*` is the only path currently protected.

## Goal

Plug the holes. Restore enforcement on every route exposed by every adapter,
without altering the auth model itself (still a single `authenticate`
callback returning `'viewer' | 'operator' | null`).

Out of scope: richer principal/identity, audit trail, finer permissions,
removing `@ts-nocheck`. Tracked as future hardening.

## Approach

**Single global enforcement point per adapter.** Registered before any route
handler. Same `checkAuth(req, authenticate, isWriteMethod(req.method))` shape
the widget-proxy `preHandler` already uses (`isWriteMethod` is already
exported from `packages/optio-api/src/widget-proxy-core.ts`).

Method-only write detection. `GET` / `HEAD` / `OPTIONS` count as reads;
everything else is a write. This matches the existing widget-proxy
classification, so the two enforcement points stay consistent.

The widget-proxy `preHandler` retains its inline `checkAuth` call. With the
global Fastify hook in place, the inline check becomes redundant on every
request, but it is correct, isolated, and removing it is out of scope.
Defense-in-depth at the cost of one extra function call per widget request.

## Per-adapter plan

### fastify (`packages/optio-api/src/adapters/fastify.ts`)

Inside `registerOptioApi`, before `registerWidgetProxy(app, …)`:

```typescript
app.addHook('onRequest', async (req, reply) => {
  const r = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
  if (r) reply.code(r.status).send(r.body);
});
```

Fastify's `onRequest` fires before the widget-proxy plugin's `preHandler`, so
the hook covers REST, SSE, discovery, and widget proxy paths.

### express (`packages/optio-api/src/adapters/express.ts`)

Re-import `checkAuth` from `'../auth.js'` and `isWriteMethod` from
`'../widget-proxy-core.js'`. Inside `registerOptioApi`, before
`createExpressEndpoints` and before the SSE + discovery `app.get(...)`
registrations:

```typescript
app.use('/api', async (req, res, next) => {
  const r = await checkAuth(req, opts.authenticate, isWriteMethod(req.method));
  if (r) {
    res.status(r.status).json(r.body);
    return;
  }
  next();
});
```

### nextjs-app (`packages/optio-api/src/adapters/nextjs-app.ts`)

Re-import `checkAuth` and `isWriteMethod`. Add a shared helper:

```typescript
async function authGate(request: Request): Promise<Response | null> {
  const r = await checkAuth(request, opts.authenticate, isWriteMethod(request.method));
  if (!r) return null;
  return new Response(JSON.stringify(r.body), {
    status: r.status,
    headers: { 'Content-Type': 'application/json' },
  });
}
```

Call at the very top of both `GET` and `POST` before any path matching:

```typescript
async function GET(request: Request): Promise<Response> {
  const denied = await authGate(request);
  if (denied) return denied;
  // …existing routing logic…
}
```

### nextjs-pages (`packages/optio-api/src/adapters/nextjs-pages.ts`)

Re-import. Add the same `authGate` style helper adapted to
`(req: NextApiRequest, res: NextApiResponse) => Promise<boolean>` (returns
`true` if request was rejected and response was already sent). Call at the
top of the returned handler before any URL match.

`authenticate` becomes mandatory here too — drop the `?` on the type field
in `OptioApiOptions` to match the other three adapters.

## Tests

Restore the auth describe block in each adapter's test file, extended for
routes that did not exist in 6b3da7c:

For each of `fastify.test.ts`, `express.test.ts`, `nextjs-app.test.ts`,
`nextjs-pages.test.ts`:

```
describe('<adapter> auth', () => {
  it('null role → 401 on REST GET', …);
  it('null role → 401 on REST POST', …);
  it('null role → 401 on SSE list stream', …);
  it('null role → 401 on SSE tree stream', …);
  it('null role → 401 on /api/optio/instances', …);
  it('viewer → 200 on REST GET', …);
  it('viewer → 403 on REST POST', …);
  it('operator → 200 on REST GET', …);
  it('operator → 200 on REST POST', …);
  it('async authenticate works', …);
});
```

Existing happy-path integration tests already pass
`authenticate: () => 'operator'` — they continue to work unchanged.

The widget-proxy auth test (`fastify-widget-proxy.test.ts`) is unchanged.

## Files touched

- `packages/optio-api/src/adapters/fastify.ts`
- `packages/optio-api/src/adapters/express.ts`
- `packages/optio-api/src/adapters/nextjs-app.ts`
- `packages/optio-api/src/adapters/nextjs-pages.ts`
- `packages/optio-api/src/adapters/__tests__/fastify.test.ts`
- `packages/optio-api/src/adapters/__tests__/express.test.ts`
- `packages/optio-api/src/adapters/__tests__/nextjs-app.test.ts`
- `packages/optio-api/src/adapters/__tests__/nextjs-pages.test.ts`
- `AGENTS.md` (root) — confirm/clarify "auth covers all REST + SSE +
  discovery + widget"
- `packages/optio-api/AGENTS.md` — same clarification

## Postmortem

Three contributing factors:

1. **Branch divergence not reconciled at merge.** The multi-database branch
   (`f92024c`) was forked from a commit before the auth wiring landed in
   adapter bodies. The rewrite of `registerOptioApi` /
   `createOptioRouteHandlers` / `createOptioHandler` replaced the function
   bodies wholesale without diff-checking against the pre-merge `main` HEAD.
2. **Test describe block dropped in the same merge.** The
   `Fastify adapter auth` describe block was lost in `4b76363` with no
   replacement. With the alarm gone, the regression went undetected.
3. **`@ts-nocheck` masked dangling parameters.** Every adapter file starts
   with `// @ts-nocheck`. The unused `authenticate` parameter and the
   dangling `AuthCallback` type reference in three of four adapters were
   invisible to TypeScript. Removing `@ts-nocheck` is itself a separate
   project (the `// @ts-nocheck` exists because ts-rest's handler types do
   not infer cleanly across the monorepo); it remains out of scope here.

Future hardening — not part of this fix:

- The per-adapter 401 tests added in this fix already serve as the
  regression alarm — an unauthenticated request to any of the four adapters
  now produces a failing test if `checkAuth` is dropped from the request
  path again. No additional coverage-style guard is proposed.
- Scoped removal of `@ts-nocheck` once ts-rest typing in the monorepo is
  resolved.

## Out of scope

- Auth model redesign (richer principal, identity propagation, audit, finer
  permissions). The user's selected scope was "plug holes only" with no
  change to the model.
- Removing `@ts-nocheck`.
- Adding a CSRF / origin-check layer beyond what the host app supplies.
