# optio-api — LLM Reference

## Architectural rule

**Engine owns all writes.** This package reads MongoDB directly for queries (REST GETs, SSE streams, widget proxy, the conversation-archive proxy) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. The engine is the single source of truth for what commands are allowed and what state results.

---

## Package

- **name**: `optio-api`
- **entry points**:
  - `optio-api` → `dist/index.js` / `dist/index.d.ts`
  - `optio-api/fastify` → `dist/adapters/fastify.js` / `dist/adapters/fastify.d.ts`
- **dependencies**: `optio-contracts`, `@ts-rest/core`, `mongodb`, `ioredis`, `@clamator/protocol`, `@clamator/over-redis`, `zod`
- **note**: `zod` is declared explicitly here even though `optio-api` does not import it directly. `@clamator/protocol` declares `zod` as a peerDependency; without an explicit declaration in this package, pnpm can resolve `@clamator/protocol`'s peer requirement against a different physical `zod` copy than the one used by `optio-contracts`, which causes TypeScript to reject the codegenned `_generated/optio-engine.ts`. See `@clamator/protocol`'s README for the canonical statement of this consumer requirement.
- **optionalDependencies**: `@ts-rest/fastify`
- **peerDependencies**: `fastify ^5.2.0` (optional)

## Layered architecture

| Layer | Provides | Audience |
|-------|----------|----------|
| 1 | `createOptioTransports(redis): OptioTransports` — cache of `RpcClient` per `(database, prefix)` | RPC-only consumers (e.g., Excavator) + custom HTTP adapter authors |
| 2 | `createOptioContext({ dbOpts, redis }): OptioContext` — bundles `dbOpts`, `transports`, `redis`, `closeAll` | HTTP hosts (typical) |
| 3a | `registerOptioApi(app, { ctx, authenticate })` (or sugar form) — binds HTTP routes onto a framework | HTTP hosts |

The transport cache (Layer 1) is contract-agnostic. Any clamator contract — `OptioEngineClient` plus future consumer contracts — wraps a cached transport. RPC-only consumers stop at Layer 1; HTTP-handler code uses Layer 2.

## Exports

Re-exported from the main `optio-api` entry point:

- `OptioEngineClient` (re-exported from `_generated/optio-engine.ts`) — typed clamator client for the optio-engine RPC contract. Constructed as `new OptioEngineClient(transport)`.
- `createOptioTransports(redis: Redis): OptioTransports` — Layer 1 factory. Caches one `RpcClient` per `(database, prefix)`. Audience: RPC-only consumers, custom adapter authors.
- `OptioTransports` (type) — interface `{ get(database: string, prefix: string): RpcClient; closeAll(): Promise<void> }`.
- `createOptioContext({ dbOpts, redis }): OptioContext` — Layer 2 factory. Wraps Layer 1 + Mongo `Db`.
- `OptioContext` (type) — interface `{ dbOpts: DbOptions; transports: OptioTransports; redis: Redis; closeAll(): Promise<void> }`. Threaded into every handler call.
- `resolveDb(dbOpts, query)` — extract `db`, `database`, `prefix` from a query plus dbOpts.
- `resolveOptioEngine(ctx, query): OptioEngineClient` — Layer 2 helper combining `resolveDb` and `new OptioEngineClient(ctx.transports.get(...))`. Use inside per-request handler paths.

### Patterns

External RPC-only consumer (e.g., Excavator):

```typescript
import { createOptioTransports, OptioEngineClient } from 'optio-api';

const transports = createOptioTransports(redis);
const optioEngine = new OptioEngineClient(transports.get('mydb', 'optio'));
await optioEngine.launch({ processId: 'foo' });

// Consumer's own contract on the same transport infrastructure:
const myClient = new MyDomainClient(transports.get('mydb', 'mydomain'));
```

HTTP host:

```typescript
import { createOptioContext, registerOptioApi, resolveOptioEngine } from 'optio-api';

const ctx = createOptioContext({ dbOpts: { db }, redis });
registerOptioApi(app, { ctx, authenticate });  // explicit form
app.addHook('onClose', () => ctx.closeAll());

// Programmatic engine access in the host's own code:
const engine = resolveOptioEngine(ctx, {});
await engine.launch({ processId: 'foo' });
```

Sugar form (host doesn't need to manage ctx explicitly):

```typescript
const ctx = registerOptioApi(app, { db, redis, authenticate });
// fastify wires onClose to ctx.closeAll automatically in the sugar form.
```

## OptioApiOptions

```typescript
interface OptioApiOptions {
  db: Db;                                  // MongoDB Db instance
  redis: Redis;                            // ioredis Redis instance
  prefix?: string;                         // Limits instance discovery
                                           // (GET /api/optio/instances) to this
                                           // prefix; unset, every prefix. Routes
                                           // take the prefix from each request's
                                           // query (default 'optio').
  authenticate: AuthCallback<TRequest>;    // TRequest depends on adapter (FastifyRequest,
                                           // express Request, web Request, NextApiRequest).
                                           // Returns 'viewer' | 'operator' | null.
  scope?: ScopeCallback<TRequest>;         // Optional tenant boundary. See "Access scope".
  authorize?: AuthorizeCallback<TRequest>; // Optional per-action decision. See "Access scope".
}
```

## Authentication

The `authenticate` callback is invoked on every request to every route across
all four adapters:

- REST endpoints from `processesContract` under `/api/processes/...`
- SSE streams: `/api/processes/stream` and `/api/processes/:id/tree/stream`
- Discovery: `/api/optio/instances`
- Widget reverse-proxy under `/api/widget/...` (Fastify only)

The callback receives the framework-native request object and returns an
`OptioRole` (`'viewer'` or `'operator'`) or `null`. Returning `null` denies
the request with `401 Unauthorized`. `'viewer'` permits safe HTTP methods
(`GET`, `HEAD`, `OPTIONS`); `'operator'` permits all methods. A mutating
method with a `viewer` role yields `403 Forbidden`.

Enforcement is implemented per adapter via the framework's request-lifecycle
hook (Fastify `onRequest`, Express `app.use('/api', …)`, Next.js inline at
the top of `GET`/`POST` or the returned Pages handler). The Fastify
widget-proxy plugin's `preHandler` retains its own `checkAuth` call as
defense in depth.

## Access scope and authorization

Three optional hooks, accepted by all four adapters next to `authenticate`, let
a multi-tenant host confine each request to its tenant's processes, hide
individual processes from it, and decide individual actions. Types live in
`auth.ts`; the shared enforcement in `access-scope.ts`, `visibility.ts` and the
handlers.

```typescript
type ScopeFilter = Record<string, string | number | boolean | null>;
type ScopeCallback<TRequest> = (req: TRequest) => ScopeFilter | null | Promise<ScopeFilter | null>;

type VisibilityCallback<TRequest> =
  (req: TRequest) => ProcessMetadataFilter | null | Promise<ProcessMetadataFilter | null>;

type OptioAction = 'launch' | 'cancel' | 'dismiss' | 'resurrect' | 'resync'
  | 'widget' | 'widget-control' | 'widget-upload' | 'instances';
interface ProcessRef { _id: string; processId: string; name: string; parentId?: string; rootId: string; metadata: Record<string, unknown> }
interface AuthorizeInput {
  role: OptioRole; action: OptioAction;
  process?: ProcessRef;                       // single-process actions
  metadataFilter?: Record<string, unknown>;   // resync: the effective (scoped) filter
  clean?: boolean;                            // resync
}
type AuthorizeCallback<TRequest> = (req: TRequest, input: AuthorizeInput) => boolean | Promise<boolean>;
```

`'resurrect'` is a single-process action like `'launch'`: `authorize` receives the
target `process`. `'widget'` also gates `GET /api/processes/:id/conversation` (reading
a suspended process's conversation record), with the target `process`.
`OptioAction` has no `'read'` member: it was declared but never passed to
`authorize`; a host that matched on `case 'read':` gets a compile error
pointing it at `visibility` instead.

**`scope(req)`** returns a flat exact-match metadata map, or `null` / `{}` for
an unscoped request. A process is in scope when every key equals its
`metadata` value (strict equality); a process whose own metadata lacks a key is
judged by its root's; one whose root lacks it too is out (fail closed).

**`visibility(req)`** returns a metadata predicate in optio's filter language
(the same one `metadataFilter` uses — `and`/`or`/`not`/`leaf`/`eq`/`ne`/`isIn`/
`notIn`/`exists`/`gt`/`gte`/`lt`/`lte`, re-exported by `optio-api` for hosts that
write one), or `null` / `undefined` / `{}` for no restriction. A process is
visible when its own `metadata` satisfies the predicate; children inherit
their parent's metadata at creation, so there is no root fallback. A missing
metadata key follows MongoDB's rules, deliberately: `eq('ownerId', me)` does
not match a process without an `ownerId` (fails closed), while `ne`, `notIn`
and `not(...)` do match it. A predicate that fails `ProcessMetadataFilterSchema`
fails the whole request (the promise rejects) rather than passing through
unfiltered. Excavator's rule, for illustration:
`visibility: (req) => or(not(eq('kind', 'data-chat')), eq('ownerId', userIdOf(req)))`.

`scope`'s enforcement, unchanged by `visibility`:

| Route | Out of scope |
|---|---|
| `GET /api/processes`, list stream, session-events stream | excluded: the scope is `$and`ed into the query, so a client filter cannot widen it |
| `GET /api/processes/:id`, `/tree`, `/log`, `/tree/log`, `/:id/tree/stream` | 404 `Process not found` |
| `GET /api/processes/tree/multi/stream` | reported in `missing` |
| `POST /api/processes/:id/launch` / `cancel` / `dismiss` / `resurrect` | 404 `{ reason: 'not-found' }`; the engine is not called |
| `GET /api/processes/:id/conversation` | 404 `{ reason: 'not-found' }`; the engine is not called |
| widget proxy, `widget-control`, `widget-upload` (Fastify) | the route's not-found response |
| `POST /api/processes/resync` | runs with the client's flat filter plus the scope keys (the engine filters regenerated tasks and pruned records by it); 403 if the filter contradicts the scope, 400 for a predicate-tree filter |

**`authorize(req, input)`** runs only after a process is confirmed in scope
and (where checked) visible: a process the request cannot see is a 404
*before* `authorize` is asked, not a 403. It runs for launch, cancel, dismiss,
resurrect, resync, the three widget routes, the conversation read (as
`'widget'`) and `GET /api/optio/instances`; `false` gives **403**
`{ message: 'Forbidden' }` (the conversation read: `{ reason: 'forbidden',
message: 'Forbidden' }`) and the engine is not called.

Without any hook behaviour is exactly as before: commands go straight to
the engine and no extra lookups run. With `scope` only or `authorize` only,
behaviour is unchanged as well — `visibility` is strictly additive.

**Where `visibility` is checked today.** Every process read that can reach a
response goes through one choke point, `processReads` (`process-reads.ts`;
see "One choke point" below) — no handler ANDs the visibility condition
itself. `gateProcess` — the single-process action gate behind launch, cancel,
dismiss, resurrect, the three widget routes and the conversation read —
checks `access.visibility` right alongside `access.scope`, via an explicit
`isVisible` check on an unfiltered read, before `authorize` is asked: a
hidden process is a 404 the same as an out-of-scope one, including when a
duplicated `processId` resolves (as the engine does) to a newest document
that is hidden while an older one of the same `processId` is visible. The
read handlers consult it too, through the `ProcessReads` each builds once
per request: `listProcesses` ANDs it into the query and `countDocuments`, so
a client `metadataFilter` cannot widen past it and `totalCount` stays exact
across every page; `getProcess` and `getProcessLog` 404 a hidden process, by
either id form; `getProcessTree` 404s a hidden entry point and otherwise
fetches only visible children at each level, so a hidden process takes its
subtree with it; `getProcessTreeLog` 404s a hidden entry point, fetches the
tree's processes under the condition, and — only when `reads.visibility` is
set — prunes orphans before collecting log lines, so a process left
parentless by a hidden ancestor contributes no lines. Streams and the
adapters' options do not consult `visibility` yet — that is a later change.

Per request, `checkAuth` records the authenticated role (keyed by the native
request object) and `accessFor(req, hooks)` resolves the hooks once into an
`Access` (`{ restricted, scope, visibility, authorize }`) that adapters pass to
the handlers. `hasAccessHooks(hooks)` is true when any of `scope`, `visibility`
or `authorize` is set; `resolveAccess` and `accessFor` use it to decide whether
to do any work at all. The Next.js adapters carry `Access` into the ts-rest
routes through an `AsyncLocalStorage`, because ts-rest's Next handlers see
their own request objects. Custom adapters: call `checkAuth`, then `accessFor`,
then pass the `Access` as the handlers' trailing argument and use
`findScopedProcess` / `findReadableProcess` / `gateProcess` / `gateInstances`
for routes outside the handlers.

**Visibility helpers (for custom adapters; `visibility.ts`).**
`normalizeVisibility(raw)` validates a hook's return value: `null`, `undefined`
and `{}` normalize to `null` (no restriction); anything else is parsed with
`ProcessMetadataFilterSchema` (same schema `metadataFilter` uses, so a legacy flat
map works as equality), throwing on an invalid predicate.
`visibilityToMongo(visibility)` / `andVisibility(base, visibility)` translate it to
a Mongo filter fragment and `$and` it into a base filter, the same shape as
`scopeToMongo` / `andScope`. `isVisible(col, doc, visibility)` judges a single
document by a Mongo count query (never a JS copy of the semantics); `null`
admits everything without issuing a query. `pruneOrphans(docs, keep?)` drops tree
children whose parent chain doesn't resolve to a kept root, preserving input
order and memoising per id. `findReadableProcess(reads, id, access)` (in
`access-scope.ts`) looks a process up by either id form under both `scope` and
`visibility`, returning `null` for a process that fails either — the single-read
building block `getProcess`, `getProcessLog`, `getProcessTree` and
`getProcessTreeLog` use in place of `findScopedProcess`. `findScopedProcess`,
`findReadableProcess`, `findActionTarget`, `inScope` and `gateProcess` all accept
either a `ProcessReads` or a plain `Collection` (wrapped internally with
`processReadsOf(col, null)`, since these lookups only ever need `.unfiltered`,
each followed by its own explicit visibility check); adapters still come in a
later change.

### One choke point

`process-reads.ts` is the only module that names the `{prefix}_processes`
collection and the only one that ANDs the visibility condition into a query,
so the rule the host declares once in `visibility` cannot be forgotten by a
route added later (spec: "One choke point", decision 6).

```typescript
function processCollection(db: Db, prefix: string): Collection;

interface ProcessReads {
  readonly visibility: ProcessMetadataFilter | null;
  find(filter: Filter<Document>, options?: FindOptions): FindCursor<WithId<Document>>;
  findOne(filter: Filter<Document>, options?: FindOptions): Promise<WithId<Document> | null>;
  countDocuments(filter: Filter<Document>, options?: CountDocumentsOptions): Promise<number>;
  /** The collection without the condition, for access-scope.ts alone. */
  readonly unfiltered: Collection;
}
function processReads(db: Db, prefix: string, visibility: ProcessMetadataFilter | null): ProcessReads;
function processReadsOf(col: Collection, visibility: ProcessMetadataFilter | null): ProcessReads;
```

Each handler builds one `ProcessReads` per request
(`processReads(db, prefix, access.visibility)`) and reads through it
exclusively; `find`/`findOne`/`countDocuments` AND the condition into the
caller's filter (never replacing it, so a client filter cannot widen past
it) and, with `visibility` null, pass the caller's filter object through
unchanged — the same reference, not a copy — so a host with no `visibility`
hook sees identical queries to a plain `Collection`. `.unfiltered` exists for
`access-scope.ts` alone: its lookups (scope's root fallback, the action
gate's target lookup) need a raw read, each followed by its own explicit
`isVisible`/`inScope` check, not `ProcessReads`'s own filtering.

**The guard test** (`process-reads-guard.test.ts`) scans every non-test
source file and fails when:

1. any file but `process-reads.ts` names a processes collection (a template
   ending in `_processes`);
2. `processCollection(` is called outside its allowlist — each allowed file
   carries its reason in the test: `process-change-hub.ts` (a change stream,
   which serves no documents), `discovery.ts` (instance discovery, gated by
   `'instances'`), and `widget-proxy-core.ts` / `agent-input.ts` (upstream
   lookups that run only after `gateProcess` admitted the process);
3. `.unfiltered` is used outside `access-scope.ts` and `process-reads.ts`.

`stream-poller.ts` and the four `adapters/*.ts` files still name the
collection directly and are temporarily exempt (`PENDING` in the guard test);
Task 4 and Task 5 migrate them and empty that list. A new route that reads
processes therefore either goes through `processReads` or fails this test
until its file is deliberately allowlisted, with a reason, in review.

## Fastify Adapter

Imported from `optio-api/fastify`.

**Return shape (two-form):**

`registerOptioApi(app, opts)` accepts either:

- **Sugar form** — `{ db | mongoClient, redis, authenticate, prefix?, verbose? }`. Adapter constructs an `OptioContext` internally, wires fastify's `onClose` to `ctx.closeAll()`, and returns the constructed `OptioContext` so the host can reach `transports`, etc.
- **Explicit form** — `{ ctx, authenticate, prefix?, verbose? }`. Caller owns ctx lifecycle; adapter does NOT wire `onClose` and returns `void`.

Engine access for non-HTTP code goes through the context (sugar: returned `OptioContext`; explicit: caller's own):

```typescript
const engine = resolveOptioEngine(ctx, {});
// or for a custom contract:
const my = new MyClient(ctx.transports.get(database, prefix));
```

```typescript
function registerProcessRoutes(app: FastifyInstance, opts: OptioApiOptions): void
```

Registers all REST routes under `/api/processes/:prefix/...` using `@ts-rest/fastify`
against the `processesContract` from `optio-contracts`.

```typescript
function registerProcessStream(app: FastifyInstance, opts: OptioApiOptions): void
```

Registers two SSE routes (raw HTTP, not ts-rest):

- `GET /api/processes/:prefix/stream` — flat list stream (uses `createListPoller`)
- `GET /api/processes/:prefix/:id/tree/stream?maxDepth=N` — tree + log delta stream (uses `createTreePoller`)

Both routes set `Content-Type: text/event-stream`, read MongoDB when a change is relevant to them (see "Stream Poller"), and call `poller.stop()` on request close.

```typescript
function registerWidgetProxy(app: FastifyInstance, opts: OptioWidgetProxyOptions): void

interface OptioWidgetProxyOptions {
  db: Db;
  prefix: string;
  authenticate: AuthCallback<FastifyRequest>;  // viewer role for reads, operator for writes
  ttlMs?: number;  // widgetUpstream TTL cache duration; default 5000 ms
}
```

Imported from `optio-api/fastify`. Wires the widget proxy under `/api/widget/:processId/*`
supporting HTTP, SSE, and WebSocket. Per-request behavior:

1. Extracts `processId` (24-hex ObjectId) from the URL; 404 on malformed URL.
2. Calls `authenticate` — viewer permission for safe HTTP methods, operator for mutating methods.
3. Looks up `widgetUpstream` from MongoDB, TTL-cached for `ttlMs` ms (default 5 s); 404 when missing.
4. Injects inner auth: `BasicAuth`/`HeaderAuth` → request headers; `QueryAuth` → URL query parameter.
5. Strips `/api/widget/:processId` prefix and forwards the sub-path to the upstream URL.
6. Maps upstream connection errors to 502 Bad Gateway; full error detail is logged server-side only.

## Handler Functions

All handlers are exported from `optio-api` (main entry point).

```typescript
// Query handlers
// Every handler takes an optional trailing `access: Access` (default
// UNRESTRICTED); see "Access scope and authorization".
async function listProcesses(
  ctx: OptioContext,
  query: ListProcessesQuery,
  access?: Access,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number }>

async function getProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<object | null>

async function getProcessTree(
  ctx: OptioContext,
  query: { database?: string; prefix?: string; maxDepth?: number },
  id: string,
): Promise<object | null>

async function getProcessLog(
  ctx: OptioContext,
  query: GetProcessLogQuery,
  id: string,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number } | null>

async function getProcessTreeLog(
  ctx: OptioContext,
  query: GetProcessTreeLogQuery,
  id: string,
): Promise<{ items: any[]; nextCursor: string | null; totalCount: number } | null>

// Command handlers route to the optio-engine clamator contract via
// `resolveOptioEngine(ctx, query).{launch,cancel,dismiss,resurrect,resync}(...)`. Engine
// owns all command-acceptance rules (state allowlists, supportsResume guard,
// persistent launch blocks); API handlers translate the discriminated-union
// result into HTTP status + body.
async function launchProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  resume?: boolean,  // default: false
): Promise<LaunchCommandResult>

async function cancelProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<CancelCommandResult>

async function dismissProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
): Promise<DismissCommandResult>

// POST /api/processes/:id/resurrect: saves the work a failed run left on its
// host, then resumes (engine RPC `resurrect`). `sessionId` is the initiating
// session (request body `{ sessionId?: string | null }`).
async function resurrectProcess(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,
  sessionId?: string | null,  // default: null
  access?: Access,
): Promise<ResurrectCommandResult>

async function resyncProcesses(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  clean?: boolean,  // default: false
  metadataFilter?: ProcessMetadataFilter,  // omit or pass {} for full sync
  access?: Access,
): Promise<ResyncCommandResult>  // { status: 202; body: { message } } | { status: 400 | 403; body: { message } }
```

Command results also carry `{ status: 403; body: { message: 'Forbidden' } }`
(`ForbiddenResult`) when the host's `authorize` hook refuses.

```typescript
// GET /api/processes/:id/conversation: the saved conversation record of a
// suspended process, read through the engine's conversation archive.
async function getProcessConversation(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,           // either id form
  access?: Access,
): Promise<ConversationResult>
// ConversationResult = ({ status: 200; body: { events: unknown[]; capturedAt: string | null } }
//   | { status: 403 | 404 | 409 | 500 | 503; body: { reason: ConversationFailureReason; message } })
//   & { headers: { 'Cache-Control': 'no-store' } }   // adapters must send `headers`
```

See "Conversation archive proxy" below.

The 404/409 response body for `launchProcess` / `cancelProcess` / `dismissProcess` /
`resurrectProcess` is `{ reason, message }`, typed via `LaunchErrorBody` / `CancelErrorBody` /
`DismissErrorBody` / `ResurrectErrorBody` in `optio-contracts/src/api-to-frontend.ts`. The
`reason` discriminator is one of the engine failure-reason enums from
`optio-contracts/src/engine-failure-reasons.ts`.

**Adapters** (`fastify`, `express`, `nextjs-app`, `nextjs-pages`): all four extract `body?.resume`
from the request body and forward it to `launchProcess` as the sixth argument. For resurrect they
forward `body?.sessionId ?? null` (the body is optional) to `resurrectProcess` as the fourth argument.

Per the architectural rule above, command handlers do not validate state — they forward the raw
id to the engine via `engine.launch / cancel / dismiss / resurrect` and translate the discriminated-union
result into HTTP status + body. Failure reasons (`not-found`, `not-launchable`, `no-resume-support`,
`launch-blocked`, `not-cancellable`, `not-dismissable`, and for resurrect `not-resurrectable`,
`no-resurrect-support`, `resurrect-in-progress`, `shutting-down`) come from the engine; the API only
maps each to 404 or 409 (`not-found` is 404, every other reason 409).

## Conversation archive proxy

`GET /api/processes/:id/conversation` (contract route `processes.getConversation`)
shows a suspended agent task's conversation read-only. The record is encrypted at
rest and only the engine holds the key, so the engine decrypts and this package
relays (spec `docs/2026-10-12-suspended-conversation-view-design.md` §3):

- The engine runs one aiohttp listener (optio-core `conversation_archive.py`) and
  publishes `{ _id: 'conversationArchive', url, password, updatedAt }` in
  `{prefix}_engine_endpoints`; it serves `GET <url>conversation/<_id>` behind basic auth
  `optio:<password>`.
- `getProcessConversation` (`handlers.ts`): `gateProcess(..., 'widget')` (out of
  scope: 404 `not-found`; refused: 403 `forbidden`); resolves the target document
  (`findActionTarget`, either id form; missing: 404 `not-found`); then
  `fetchConversation` with the document's `_id`.
- `conversation-archive.ts`: `resolveConversationArchive(db, prefix)` reads the
  endpoint document and caches a found address for 30 s
  (`CONVERSATION_ARCHIVE_CACHE_TTL_MS`), per MongoClient, database name and prefix; an
  absence is not cached, and a `url` that is not an absolute `http:` / `https:` URL
  counts as no endpoint (null, so 503). `fetchConversation` answers a
  `processObjectId` that is not 24 hex characters with 404 `not-found` itself (no
  MongoDB read, no upstream call; defence in depth for custom adapters, since the id
  becomes part of the upstream path). It asks with a 15 s timeout
  (`CONVERSATION_ARCHIVE_TIMEOUT_MS`, covering the body too) and passes the archive's
  200 / 404 (`not-found`, `no-conversation-support`, `no-conversation`) / 409
  (`not-suspended`) / 500 (`read-failed`) through; an error body passes with any
  string `reason`, so a newer engine's unknown reason reaches the client. Everything
  else is 503 `engine-unavailable` and drops the cached address
  (`forgetConversationArchive`), so the next request reads MongoDB again: no
  endpoint document, a connection failure, the timeout, a body over 64 MiB
  (`CONVERSATION_ARCHIVE_MAX_BODY_BYTES`; a declared `content-length` is checked
  first, then bytes are counted while reading), an upstream 401 (an engine
  restarted on the same port publishes a new password), or an answer that is not the
  archive's (wrong status or body shape). Redirects are not followed.
- Every answer carries `Cache-Control: no-store`; the handler returns it as
  `headers` and each adapter sets it natively (fastify `reply.headers`, express
  `res.set`, Next Pages `res.setHeader`, Next App ts-rest `responseHeaders`).
- Nothing on this path logs; the password and the record never reach a log line or
  an error message. No MongoDB writes.

## Types

```typescript
interface ListQuery {
  cursor?: string;              // ObjectId string; cursor-based pagination
  limit: number;
  rootId?: string;              // filter by root process ObjectId
  state?: string;               // filter by status.state
  metadataFilter?: ProcessMetadataFilter;  // exact-match metadata filter; parsed from URL-encoded JSON
}

interface PaginationQuery {
  cursor?: string;  // numeric string index into log array
  limit: number;
}

interface TreeLogQuery extends PaginationQuery {
  maxDepth?: number;  // relative depth limit from queried process
}

// Per-command result types. The 200 body is a process; the 404/409 body is
// `{ reason, message }` where `reason` is the corresponding engine failure-reason
// enum from `optio-contracts/src/engine-failure-reasons.ts`.
type LaunchCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: LaunchFailureReasonType; message: string } };

type CancelCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: CancelFailureReasonType; message: string } };

type DismissCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: DismissFailureReasonType; message: string } };

type ResurrectCommandResult =
  | { status: 200; body: any }
  | { status: 404 | 409; body: { reason: ResurrectFailureReasonType; message: string } };
```

## Stream Poller

```typescript
interface StreamPollerOptions {
  db: Db;
  prefix: string;
  sendEvent: (data: unknown) => void;  // called with JSON-serializable event objects
  onError: () => void;                 // called on a failed read; the stream stops itself first
  metadataFilter?: ProcessMetadataFilter;
  scope?: ScopeFilter | null;          // access scope; $and-ed into the query
  hub?: ProcessChangeHub;              // default: the shared hub of (db, prefix)
}

interface TreePollerOptions extends StreamPollerOptions {
  rootId: string;     // ObjectId string of the tree root
  baseDepth: number;  // absolute depth of the queried process
  maxDepth?: number;  // relative depth limit from baseDepth
}

interface ListPollerHandle {
  start(): void;  // subscribes to the hub, then reads once
  stop(): void;   // unsubscribes; no read after it
}

function createListPoller(opts: StreamPollerOptions): ListPollerHandle
function createTreePoller(opts: TreePollerOptions): ListPollerHandle
```

**How a stream learns about changes** (`process-change-hub.ts`,
`process-change-relevance.ts`; spec `docs/2026-10-09-api-change-streams-design.md`).
Every stream (list, tree, multi-tree, session events) subscribes to the
`ProcessChangeHub` of its `(database, prefix)` (`getProcessChangeHub(db, prefix)`;
shared per MongoClient, database name and prefix). The hub holds one MongoDB
change stream on `{prefix}_processes` while it has subscribers and asks a
stream to re-read only for a change relevant to it, judged from the change
event alone (no `fullDocument` lookup; a `$project` in the change stream's own pipeline cuts each event down to the operation, the id, the changed field names, an insert's `rootId` / `originatingSessionId` and an update's new `originatingSessionId`, so no log lines or documents reach the API): the list for any insert, a shown
process's sent fields, or any `metadata` change; a tree for an insert under its
root or any change of a member; session events for a process joining the
session or a member's `sessionEvents`. While a stream's read is in flight every
change counts as relevant. A stream re-reads at most once a second (the first
change after a quiet second at once). Where change streams are unavailable (a
standalone mongod refuses with error 40573; no permission to watch: 13; a
sharded cluster's mongos watches fine) the hub polls -- every stream re-reads once a second -- and tries
again every 5 minutes; after any other error it polls and reopens with backoff
(1 s doubling to 30 s); every reopen makes every stream re-read once.
`OPTIO_API_CHANGE_STREAMS=off` makes every hub poll. Event shapes are unchanged.

### SSE event shapes emitted by `createListPoller`

```typescript
{ type: 'update'; processes: Array<{ _id, processId, name, status, progress, cancellable, special, warning, metadata, depth }> }
```

### SSE event shapes emitted by `createTreePoller`

```typescript
{ type: 'update'; processes: Array<{ _id, parentId, name, status, progress, cancellable, depth, order, widgetData }> }
{ type: 'log'; entries: Array<{ ...logEntry, processId, processLabel }> }
{ type: 'log-clear' }
```

Every poller's `update` processes also carry `supportsResurrect`, `hasUnsavedWork` and
`resurrecting` (`false` when absent), all part of the snapshot fingerprint.

`createTreePoller` sends all existing log entries on the first poll, then only deltas. It detects log truncation (e.g. after resync) and emits `log-clear` before new entries.

The `widgetData` field is included in tree-stream `update` events and is part of the snapshot fingerprint, so worker-side mutations (via `ctx.set_widget_data`) trigger a new SSE event. The list stream (`createListPoller`) does **not** include `widgetData` — it is omitted from sidebar payloads.

`createListPoller` reads only the fields it sends (a projection: no `log`, `widgetData`, `params` or `sessionEvents`), so a poll costs the summary, not the processes' logs.

`widgetUpstream` is **never** included in any client-facing payload (list stream, tree stream, or REST responses).

## Building Custom Adapters

When writing a custom framework adapter (not Fastify/Express/Next.js), follow these rules:

1. **Accept an `OptioContext`** (or build one yourself from `createOptioContext({ dbOpts, redis })`). Do NOT construct `RedisRpcClient` directly; the ctx's `transports` cache ensures one client per `(database, prefix)` and handles lifecycle.

2. **For per-request engine access, call `resolveOptioEngine(ctx, query)`**. It encapsulates `resolveDb` + `new OptioEngineClient(...)`.

3. **Lifecycle**: when sugar form (you constructed ctx), wire `ctx.closeAll()` into your framework's shutdown hook. When explicit form (caller passed ctx), do NOT call closeAll — caller owns lifecycle.

Example skeleton:

```typescript
import {
  createOptioContext,
  resolveOptioEngine,
  type OptioContext,
  launchProcess, cancelProcess, dismissProcess, /* ... */
} from 'optio-api';

export function registerOptioApiMyFramework(app: MyApp, opts: SugarOpts | { ctx: OptioContext }) {
  const explicit = 'ctx' in opts;
  const ctx = explicit ? opts.ctx : createOptioContext({ dbOpts: { db: opts.db }, redis: opts.redis });

  // ... mount routes; handlers take (ctx, query, id?, ...) ...

  if (!explicit) {
    app.onClose(async () => ctx.closeAll());
    return ctx;
  }
  return;
}
```

## Layer rules (binding)

The `optio-api` package has three internal layers. Code lives in the layer that
matches its responsibility. These rules are binding: PR review will reject
violations.

### 1. Adapter layer — `packages/optio-api/src/adapters/{fastify,express,nextjs-app,nextjs-pages}.ts`

**Sole purpose:** integrate with the corresponding web framework.

**Allowed:**

- Framework-native request/response wrangling.
- Route registration via the framework's API.
- Framework lifecycle hooks (e.g. fastify `onClose`).
- Framework-specific SSE response writers (`reply.raw.writeHead` / `res.write` /
  Next.js `ReadableStream`).
- Body parser and middleware registration.

**Forbidden:** any code that would be repeated identically across the four
adapters. This explicitly includes:

- `resolveDb(...)` calls — extract to handler via `OptioContext`.
- Default-value fallbacks (`x ?? N`) — defaults belong in the contract Zod
  schemas (e.g. `PaginationQuerySchema.default(20)`).
- `parseMetadataFilterQuery`, `detectLegacyMetadataParams`, `maxDepth`
  coercion — use `sse-options.ts`.
- Engine cache instantiation — use `createOptioContext`.
- Business logic, RPC mechanics, `ObjectId` coercion.

**Test before adding code to an adapter:** *"Would I write this same code in
the other three adapters?"* If yes, extract.

**Test before adding a default:** check whether the contract layer
(`@optio/contracts`, `processesContract`) can express it via Zod
`.default(...)`. Defaults belong in the contract.

### 2. Handler layer — `packages/optio-api/src/handlers.ts` and collaborators

Framework-agnostic. Receives `OptioContext` + per-request data. Owns:

- Read-path Mongo queries.
- Write-path RPC calls (post-phase-3).
- Request → response shaping.
- Status-code mapping.

Collaborators: `process-id-resolver.ts`, `metadata-filter-query.ts`,
`sse-options.ts`.

### 3. Context layer — `packages/optio-api/src/context.ts`

Owns durable per-app resources: `dbOpts`, `transports` (Layer 1 cache), `redis`, and a `closeAll` teardown function. Constructed once at adapter registration via `createOptioContext({ dbOpts, redis })`. Threaded into every handler call.
