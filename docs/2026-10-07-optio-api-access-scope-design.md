# optio-api: per-request scope and authorization -- Design

**Base revision:** `80996cea` on `main` (2026-10-07)

## Problem

optio-api's only access control is `authenticate(req) -> 'viewer' | 'operator'
| null`, applied by HTTP method (`auth.ts`, `checkAuth`): `viewer` may use safe
methods, `operator` everything. Nothing looks at *which* process a request
touches. A multi-tenant host therefore cannot keep one tenant's users away from
another tenant's processes.

Verified on a local excavator stack (2026-10-07): excavator maps every
logged-in user to `operator`, so a plain user of customer 1 listed customer 0's
processes and launched one of them, and ran a resync on customer 0's dataspace.
By code reading the same holds for get/tree/log, cancel, dismiss, all SSE
streams, the widget proxy (HTTP and WebSocket -- live agent terminals),
`widget-control` (keystrokes into a running agent) and `widget-upload` (files
into a running task's workdir).

## Design

Two optional hooks next to `authenticate`, in every adapter's options
(Fastify, Express, Next.js App Router, Next.js Pages Router):

```ts
// auth.ts
export type OptioAction =
  | 'read'                                  // list, get, tree, logs, streams
  | 'launch' | 'cancel' | 'dismiss' | 'resync'
  | 'widget' | 'widget-control' | 'widget-upload'
  | 'instances';

export interface ProcessRef {               // what authorize sees of a process
  _id: string; processId: string; name: string;
  parentId?: string; rootId: string;
  metadata: Record<string, unknown>;
}

export interface AuthorizeInput {
  role: OptioRole;
  action: OptioAction;
  process?: ProcessRef;                     // single-process actions
  metadataFilter?: ProcessMetadataFilter;   // resync: the effective filter
  clean?: boolean;                          // resync
}

export type ScopeCallback<TRequest> =
  (req: TRequest) => ProcessMetadataFilter | null | Promise<ProcessMetadataFilter | null>;
export type AuthorizeCallback<TRequest> =
  (req: TRequest, input: AuthorizeInput) => boolean | Promise<boolean>;
```

- **`scope(req)`** returns a flat exact-match metadata map (the existing
  `ProcessMetadataFilter`), or `null` for unrestricted. It is the tenant
  boundary: a process is *in scope* when every key of the scope equals the
  process's `metadata` value. A process whose own metadata lacks a scope key is
  judged by its root process's metadata.
- **`authorize(req, input)`** is called after the scope check for every
  mutating action and for `instances`; `false` gives **403** `{ message:
  'Forbidden' }`.
- Both are optional. Without them behaviour is exactly as today.

### Enforcement points

Scope (out of scope behaves as not found -- the process's existence is not
revealed):

| Route | Effect |
|---|---|
| `GET /api/processes`, `GET /api/processes/stream` | scope ANDed into the query (`$and` with the client filter, so a client key cannot widen it) |
| `GET /api/processes/:id`, `/tree`, `/log`, `/tree/log`, `/:id/tree/stream` | entry process resolved, out of scope -> 404 |
| `GET /api/processes/tree/multi/stream` | out-of-scope ids reported in `missing`, like unknown ids |
| `GET /api/session-events/stream` | scope ANDed into the `originatingSessionId` query |
| `POST /api/processes/:id/launch` / `cancel` / `dismiss` | process resolved first (today these go straight to the engine RPC); missing or out of scope -> 404 `{ reason: 'not-found' }`; then `authorize`; then the RPC with the id as given |
| `POST /api/processes/resync` | effective filter = client filter AND scope; a client key that contradicts the scope -> 403. The engine already keeps a scoped resync inside its filter (generator output and stale-record removal are both filtered, `lifecycle._sync_definitions`), so a tenant can regenerate only its own tasks |
| widget proxy `/api/widget/<db>/<prefix>/<id>/...` (HTTP + WS upgrade), `POST /api/widget-control/...`, `POST /api/widget-upload/...` (Fastify only) | process resolved; out of scope -> the route's existing not-found response; then `authorize` |
| `GET /api/optio/instances` | `authorize({ action: 'instances' })` |

### Where the code goes (optio-api layer rules)

- `auth.ts`: the types above; `checkAuth` also returns the resolved role on
  success; a helper `resolveAccess(opts, req, role)` that binds the two hooks
  to the request and returns an `Access` value (`scope`, `authorize(input)`),
  so each adapter adds one call, not logic.
- `handlers.ts` and collaborators: every handler takes an optional `access`
  argument (default: unrestricted), applies the scope and calls `authorize`.
  New collaborator `access-scope.ts`: `inScope(doc, scope, col)` (own metadata,
  root fallback), `andScope(filter, scope)`, `toProcessRef(doc)`.
- `stream-poller.ts`: list and session-events pollers accept a `scope`; tree
  and multi-tree pollers are already bounded by resolved roots.
- Fastify widget preHandler, `forwardAgentInput` / `forwardUpload` callers:
  resolve the process doc once, check scope, call `authorize`.
- Adapters: pass `access` through; nothing else.
- `AGENTS.md` (package and root) and `README.md` document the hooks.

## Compatibility and release

Additive: hosts that pass neither hook see no change. `optio-api` minor
release; `optio-contracts` unchanged; `optio-dashboard` keeps working and can
adopt the hooks later.

## Testing

- `access-scope.ts` unit tests: in/out of scope, root fallback, `$and`
  composition, contradiction detection.
- Handler tests (real Mongo, as the existing `handlers.test.ts`): each read
  handler hides out-of-scope processes; launch/cancel/dismiss return 404
  without calling the engine for out-of-scope ids and 403 when `authorize`
  denies; resync sends the scoped filter and rejects contradictions.
- Pollers: list and session-events pollers emit only in-scope processes.
- Fastify adapter (`fastify.inject`, fake engine client): every route in the
  table above, with a scope and a denying `authorize`; unchanged behaviour
  with no hooks.
- End to end on a local excavator stack: the 2026-10-07 reproduction (customer
  1 user against customer 0's processes) is refused on every route.

## Host policy for excavator (in excavator, not optio)

- `scope`: unrestricted for `admin`; `{ customerId: identity.customerId }`
  otherwise. Every process excavator creates carries `metadata.customerId`.
- `authorize`: `instances` admin only; `resync` with `clean` needs `admin` or
  `customer-admin`; launch/cancel/dismiss honour an optional
  `metadata.requiredRoles` on the process (used by the coming source- and
  target-wide reset tasks); everything else allowed.
