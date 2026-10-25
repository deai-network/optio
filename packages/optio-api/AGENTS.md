# optio-api — LLM Reference

## Architectural rule

**Engine owns all writes.** This package reads MongoDB directly for queries (REST GETs, SSE streams, widget proxy, the conversation-archive proxy, a task's result file in GridFS) and forwards every mutating operation to the engine via clamator RPC. The API enforces no state machine, no policy, no command-acceptance rules. The engine is the single source of truth for what commands are allowed and what state results.

---

## Package

- **name**: `optio-api`
- **entry points**:
  - `optio-api` → `dist/index.js` / `dist/index.d.ts`
  - `optio-api/fastify` → `dist/adapters/fastify.js` / `dist/adapters/fastify.d.ts`
  - `optio-api/express` → `dist/adapters/express.js` / `dist/adapters/express.d.ts`
  - `optio-api/nextjs/pages` → `dist/adapters/nextjs-pages.js` / `dist/adapters/nextjs-pages.d.ts`
  - `optio-api/nextjs/app` → `dist/adapters/nextjs-app.js` / `dist/adapters/nextjs-app.d.ts`
- **dependencies**: `optio-contracts`, `filtrum-mongo`, `@ts-rest/core`, `mongodb`, `ioredis`, `@clamator/protocol`, `@clamator/over-redis`, `@fastify/http-proxy`, `@fastify/multipart`, `zod`
- **note**: `zod` is declared explicitly here even though `optio-api` does not import it directly. `@clamator/protocol` declares `zod` as a peerDependency; without an explicit declaration in this package, pnpm can resolve `@clamator/protocol`'s peer requirement against a different physical `zod` copy than the one used by `optio-contracts`, which causes TypeScript to reject the codegenned `_generated/optio-engine.ts`. See `@clamator/protocol`'s README for the canonical statement of this consumer requirement.
- **optionalDependencies**: `@ts-rest/fastify`
- **peerDependencies** (all optional): `fastify ^5.2.0`, `express ^4.21.0 || ^5.0.0`, `next ^13.0.0 || ^14.0.0 || ^15.0.0`

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
- `OptioTransports` (type) — interface `{ get(database: string, prefix: string): RpcClientCore; closeAll(): Promise<void> }`. Each transport's Redis key prefix is `<database>/<prefix>`.
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
await optioEngine.launch({ processId: 'foo', sessionId: null });  // sessionId: required, nullable

// Consumer's own contract on the same transport infrastructure:
const myClient = new MyDomainClient(transports.get('mydb', 'mydomain'));
```

The transport key prefix is `<database>/<prefix>`, the same `key_prefix` optio-core's
RPC server uses (`f"{db_name}/{prefix}"`). A service the app registers on that server
(`optio_core.rpc_server.register_service(...)`) shares optio's key prefix, so the app
reaches it with `transports.get(database, optioPrefix)`, as for `OptioEngineClient`;
`transports.get('mydb', 'mydomain')` only works with a separate RPC server using the
key prefix `mydb/mydomain`.

HTTP host:

```typescript
import { createOptioContext, resolveOptioEngine } from 'optio-api';
import { registerOptioApi } from 'optio-api/fastify';

const ctx = createOptioContext({ dbOpts: { db }, redis });
registerOptioApi(app, { ctx, authenticate });  // explicit form
app.addHook('onClose', () => ctx.closeAll());

// Programmatic engine access in the host's own code:
const engine = resolveOptioEngine(ctx, {});
await engine.launch({ processId: 'foo', sessionId: null });
```

Sugar form (host doesn't need to manage ctx explicitly):

```typescript
const ctx = registerOptioApi(app, { db, redis, authenticate });
// fastify wires onClose to ctx.closeAll automatically in the sugar form.
```

## OptioApiOptions

```typescript
interface OptioApiOptions {
  db: Db;                                  // MongoDB Db instance (or `mongoClient: MongoClient`
                                           // for multi-db; or `ctx: OptioContext` in place of
                                           // db + redis: the explicit form)
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
  visibility?: VisibilityCallback<TRequest>; // Optional per-process hide. See "Access scope".
  launchMetadata?: LaunchMetadataCallback<TRequest>; // Optional server-side stamp of a task
                                           // factory's launch (not an access hook).
                                           // See "Launch stamp".
  verbose?: boolean;                       // Fastify only: log the widget proxy's upstream calls
}
```

## Authentication

The `authenticate` callback is invoked on every request to every route across
all four adapters:

- REST endpoints from `processesContract` under `/api/processes/...` (the result file,
  `GET /api/processes/:id/result`, is served outside ts-rest; see "Result file")
- SSE streams: `/api/processes/stream` and `/api/processes/:id/tree/stream`; also
  `/api/processes/tree/multi/stream` (Fastify, Next.js Pages Router) and
  `/api/session-events/stream` (Fastify only)
- Discovery: `/api/optio/instances`
- Widget reverse-proxy under `/api/widget/...`, `POST /api/widget-control/...` and
  `POST /api/widget-upload/...` (Fastify only)

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

The Fastify and Express hooks are not confined to optio's routes. Fastify's `onRequest` hook is
added to the instance passed to `registerOptioApi`: on the root instance it
gates every route of the host app (its own routes and static files included),
so an `authenticate` returning `null` 401s those too. Registering inside an
encapsulated scope (`app.register(async (scope) => registerOptioApi(scope, …))`)
confines it to optio's routes. Express's middleware likewise covers every
`/api/*` route of the host app.

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
  | 'widget' | 'widget-control' | 'widget-upload' | 'result' | 'instances';
interface ProcessRef {
  _id: string; processId: string; name: string; parentId?: string; rootId: string; metadata: Record<string, unknown>;
  taskFactory?: boolean;                      // true for a task factory's record, absent otherwise
}
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
`'result'` gates `GET /api/processes/:id/result` (a task's result file), with the
target `process`; it is a read, so a `viewer` reaches it.
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
and `not(...)` do match it. The same MongoDB semantics make `eq(key, null)`
match a *missing* key as well as a genuinely null one -- so a null or
undefined user id must never reach a leaf built this way; guard the call
(`req.user?.id` must not become `eq('ownerId', undefined)`) or deny access
instead. `undefined` is rejected outright, never treated as "no
restriction" the way the top-level `null`/`undefined`/`{}` is: a predicate
with an `undefined` value anywhere in it (`eq('ownerId', undefined)`
included, however deeply nested in `and`/`or`/`not`) fails
`ProcessMetadataFilterSchema`'s walk and fails the whole request (the
promise rejects) rather than passing through unfiltered -- without this
check, `$eq: undefined` reaches the Mongo driver as `$eq: null`, matching
every process with no `ownerId` at all. Excavator's rule, for illustration:
`visibility: (req) => or(not(eq('kind', 'data-chat')), eq('ownerId', userIdOf(req)))`
(guarded: `userIdOf` never returns `undefined` for an authenticated request).

**Declared once, at init.** The host registers `visibility` once, with the
adapter's other options, next to `scope` and `authorize`; clients never send
it, and no route takes it as a parameter. It is a function of the request
only so that a rule can name the requesting user; a rule per group of
processes is a clause of that one predicate, typically one per `kind`, plus a
last clause for everything else (including processes with no `kind`):

```ts
visibility: (req) => or(
  and(eq('kind', 'data-chat'), eq('ownerId', me(req))),     // chats: their owner only
  and(eq('kind', 'report'), isIn('teamId', teams(req))),    // reports: the user's teams
  not(isIn('kind', ['data-chat', 'report'])),                // everything else: visible
)
```

`scope`'s enforcement, unchanged by `visibility`:

| Route | Out of scope |
|---|---|
| `GET /api/processes`, list stream, session-events stream | excluded: the scope is `$and`ed into the query, so a client filter cannot widen it |
| `GET /api/processes/:id`, `/tree`, `/log`, `/tree/log`, `/:id/tree/stream` | 404 `Process not found` |
| `GET /api/processes/tree/multi/stream` | reported in `missing` |
| `POST /api/processes/:id/launch` / `cancel` / `dismiss` / `resurrect` | 404 `{ reason: 'not-found' }`; the engine is not called |
| `GET /api/processes/:id/conversation` | 404 `{ reason: 'not-found' }`; the engine is not called |
| `GET /api/processes/:id/result` | 404 `{ reason: 'not-found' }`; GridFS is not read |
| widget proxy, `widget-control`, `widget-upload` (Fastify) | the route's not-found response |
| `POST /api/processes/resync` | runs with the client's flat filter plus the scope keys (the engine filters regenerated tasks and pruned records by it); 403 if the filter contradicts the scope, 400 for a predicate-tree filter |

**Resync is unaffected by `visibility`** (not per-process; bounded by `scope`
alone). **A clean resync (`clean: true`) deletes every root record its scope
filter matches, hidden ones included** (owner ruling 2026-10-13): the host's
`authorize('resync', { clean })` decides who may run one. A plain resync only
regenerates, and prunes records whose task no longer exists.

**`authorize(req, input)`** runs only after a process is confirmed in scope
and (where checked) visible: a process the request cannot see is a 404
*before* `authorize` is asked, not a 403. It runs for launch, cancel, dismiss,
resurrect, resync, the three widget routes, the conversation read (as
`'widget'`), the result file (as `'result'`) and `GET /api/optio/instances`;
`false` gives **403** `{ message: 'Forbidden' }` (the conversation read:
`{ reason: 'forbidden', message: 'Forbidden' }`) and the engine is not called
(the result file: GridFS is not read).

Without any hook behaviour is exactly as before: commands go straight to
the engine and no extra lookups run. A `launchMetadata` hook alone (see
"Launch stamp") adds one: each launch reads its target, to learn whether it
is a factory's record. With `scope` only or `authorize` only, behaviour is
unchanged as well — `visibility` is strictly additive.

**Where `visibility` is checked today.** Every process read that can reach a
response goes through one choke point, `processReads` (`process-reads.ts`;
see "One choke point" below) — no handler ANDs the visibility condition
itself. `gateProcess` — the single-process action gate behind launch, cancel,
dismiss, resurrect, the three widget routes, the conversation read and the
result file —
checks `access.visibility` right alongside `access.scope`, via an explicit
`isVisible` check, before `authorize` is asked: a hidden process is a 404
the same as an out-of-scope one, including when a duplicated `processId`
resolves (as the engine does) to a newest document that is hidden while an
older one of the same `processId` is visible (`findActionTarget` finds that
newest document with an unfiltered read — so it is never substituted for an
older, visible duplicate — and only then checks it against `visibility`,
returning null rather than the document when it is hidden). The read
handlers consult it too, through the `ProcessReads` each builds once per
request: `listProcesses` ANDs it into the query and `countDocuments`, so a
client `metadataFilter` cannot widen past it, and `totalCount` counts the
visible matches from the cursor on — the same cursor semantics as without
visibility (e.g. 3, then 2, then 1 across a 3-page list, never inflated by
hidden processes interleaved between the visible ones); `getProcess` and
`getProcessLog` 404 a hidden process, by either id form; `getProcessTree`
404s a hidden entry point and otherwise fetches only visible children at
each level, so a hidden process takes its subtree with it;
`getProcessTreeLog` 404s a hidden entry point, fetches the tree's processes
under the condition, and — only when `reads.visibility` is set — prunes
orphans before collecting log lines, so a process left parentless by a
hidden ancestor contributes no lines. **Edge case:** a child whose own
metadata diverges from what it inherited (e.g. a host re-stamping only the
root) can be visible while its parent is hidden; it stays readable on its
own (a single read, the list) but appears in no tree, since a tree keeps a
process only when its parent is kept -- documented rather than paid for with
an ancestor walk on every tree read. The streams (`stream-poller.ts`)
consult it too, each building its own `ProcessReads` at creation time — see
"Stream Poller" below; the multi-tree stream (Fastify, Next.js Pages Router)
reports a hidden id in the `resolution` event's `missing` list, exactly like
a nonexistent or out-of-scope one. All four adapters accept `visibility` next
to `scope` and `authorize` and pass it through to every handler call and
every poller they create (list, tree, multi-tree, session-events); Fastify
and Express resolve the tree-stream entry point and the multi-tree
`resolveOne` lookup (Express has no multi-tree stream) with `findReadableProcess`, while the Next.js adapters
keep their `_id`-only lookup (`reads.findOne({ _id })` followed by
`inScope`) — both forms read through the request's `ProcessReads`, so a
hidden entry point is a 404 (missing, for multi-tree) the same as an
out-of-scope one.

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
either a `ProcessReads` or a plain `Collection`, wrapped internally with
`processReadsOf(col, null)` (no visibility, so a plain `Collection` sees
unchanged behaviour). Given a `ProcessReads`, a lookup honours its
`visibility`: `findScopedProcess` hides a process it excludes, and
`findActionTarget` returns null for a target it excludes, never substituting
another document. The only unfiltered reads left are `inScope`'s own root
lookup (it judges scope only and returns no document of its own) and
`findActionTarget`'s own target lookup (the newest document by `_id` or
`processId`), whose result is checked against `visibility` before it is
returned. Fastify and Express use `findReadableProcess` for their
tree-stream and (Fastify) multi-tree lookups; the Next.js adapters use the `_id`-only
`reads.findOne` + `inScope` form instead (see "Access scope and
authorization" above).

### Launch stamp

A fourth optional hook, `launchMetadata`, accepted by all four adapters, stamps
a task factory's launch on the server (types in `auth.ts`):

```typescript
type LaunchMetadata = Record<string, string | number | boolean | null>;
type LaunchMetadataCallback<TRequest> =
  (req: TRequest, input: { process: ProcessRef; params?: Record<string, unknown> })
    => LaunchMetadata | null | Promise<LaunchMetadata | null>;
type LaunchStamp = (process: ProcessRef) => Promise<LaunchMetadata | null>;
function launchStamp<TRequest>(hook, req, params): LaunchStamp;  // binds the hook to one request
```

- It is called for every launch of a task factory (`process.taskFactory: true`, the
  factory's record), after `authorize` allowed the launch (a 404 or 403 never reaches
  it), with the request's body `params`. Its map goes to the engine as the launch's
  `metadata`; optio-core merges it into the new child's metadata. `null` sends none.
- A plain process's launch (a re-run, which makes no new record) never calls it and
  sends no `metadata`.
- The request body cannot stamp: the launch body schema has no `metadata`, and zod strips
  one a client sends. Only server-side code stamps, so the stamp is trusted; an app's
  `visibility` and `authorize` can match it (e.g. `{ user: <id> }`: a user sees and acts on
  only their own launches).
- It is not an access hook: `hasAccessHooks` stays keyed on `scope` / `visibility` /
  `authorize`, so `launchMetadata` alone leaves every request `UNRESTRICTED`. With no
  access hooks `gateProcess` reads nothing, so `launchProcess` reads the target itself
  (`findActionTarget`) to learn whether it is a factory -- only when a stamp is set.
- A stamp key the factory's own metadata already has (with `task_for`, the instance's
  too), or the reserved `taskFactory`, refuses the launch in the engine: 400
  `invalid-metadata`, its message (naming the keys) passed on unchanged.

Adapters bind it per request with `opts.launchMetadata && launchStamp(opts.launchMetadata,
req, body?.params)` and pass that as `launchProcess`'s trailing `stamp`, with the
framework's own request object, the one `authenticate` got (the Next.js App Router carries
it into the ts-rest route in its `AsyncLocalStorage`, beside the `Access`).

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
`access-scope.ts` alone, which honours a `ProcessReads`'s `visibility`
itself rather than relying on `ProcessReads`'s own filtering: the only reads
that stay unfiltered are scope's root lookup (`inScope`, which judges scope
only and returns no document) and the action gate's target lookup
(`findActionTarget`, an unfiltered lookup by `_id` or the newest `processId`
duplicate, so a hidden newest document is never silently replaced by an
older visible one), whose result is checked against `visibility` before it
is returned.

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

The guard scans every non-test source file except `process-reads.ts` itself
(which is where the collection is named and `processCollection(`/`.unfiltered`
are implemented). The four `adapters/*.ts` files and `stream-poller.ts` carry
no exemption: they read only through `processReads`. A new route that reads
processes therefore either goes through `processReads` or fails this test
until its file is deliberately allowlisted, with a reason, in review.

**The guard is a tripwire for the common bypasses, not a proof.** It is a
text scan, not a type or data-flow check, so it has blind spots: a
collection name built by string concatenation (`db.collection(prefix +
'_processes')` rather than the literal template the scan matches), an
aliased import (`import { processCollection as pc } from './process-reads.js'`,
or renaming `processReads`/`processReadsOf` on import), and a destructured
`unfiltered` (`const { unfiltered } = reads;` used later, outside
`access-scope.ts`/`process-reads.ts`, where the scan only catches
`.unfiltered` as a property access). Review still has to catch a
sufficiently determined bypass; the guard only catches the straightforward
ones -- forgetting `processReads`, not working around it.

## Breaking changes (0.3.0)

Type-level only; nothing here changes behaviour for code that already compiles against it the
obvious way. Spec: `docs/2026-10-13-optio-api-visibility-design.md` ("Compatibility and
release").

- `'read'` is removed from `OptioAction`: it was declared but never passed to `authorize`.
- `Access` gains a new required `visibility` field (`ProcessMetadataFilter | null`). A hand-built
  `Access` (custom adapters, tests) needs `visibility: null` added.
- Every stream poller's `visibility` option becomes required -- `StreamPollerOptions` (and so
  `TreePollerOptions`), `MultiTreePollerOptions`, `SessionEventsPollerOptions` -- so forgetting it
  is now a compile error instead of silently streaming everything (M9). The four built-in
  adapters already pass it; every other call site needs `visibility: null` added.

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

`registerOptioApi` is the adapter's only export (plus the `OptioApiOptions` type); it
registers everything below itself.

REST routes: all of `processesContract` under `/api/processes/...` (instance chosen by
the `?database=&prefix=` query parameters), using `@ts-rest/fastify`, except
`GET /api/processes/:id/result` (a task's result file), a raw route as in every adapter
(see "Result file").

SSE routes (raw HTTP, not ts-rest), each taking the optional `?database=&prefix=`:

- `GET /api/processes/stream` — flat list stream (uses `createListPoller`)
- `GET /api/processes/:id/tree/stream?maxDepth=N` — tree + log delta stream (uses `createTreePoller`)
- `GET /api/processes/tree/multi/stream?treeIds=&flatIds=` — multi-tree stream (uses `createMultiTreePoller`; first event `{ type: 'resolution', missing }`)
- `GET /api/session-events/stream?sessionId=` — session events (uses `createSessionEventsPoller`; no `sessionId`: the connection stays open and emits nothing)

All set `Content-Type: text/event-stream`, read MongoDB when a change is relevant to them (see "Stream Poller"), and call `poller.stop()` on request close.

Also `GET /api/optio/instances` (discovery), `POST /api/widget-control/:database/:prefix/:processId`
(body `{ text }` or `{ key }`, POSTed to the `/input` route of the process's `controlUpstream`) and
`POST /api/widget-upload/:database/:prefix/:processId` (one multipart file, at most
100 MiB, streamed into GridFS and handed to the engine's `materializeUpload`).

The widget proxy is internal (`registerWidgetProxy`, not exported); `registerOptioApi`
wires it with its own `dbOpts`, `authenticate`, access hooks and `verbose`, and a fixed
5 s `widgetUpstream` cache TTL. It serves `/api/widget/:database/:prefix/:processId/*`
supporting HTTP, SSE, and WebSocket. Per-request behavior:

1. Extracts `database`, `prefix` and `processId` (24-hex ObjectId) from the URL; 404 on malformed URL.
2. Calls `authenticate` — viewer permission for safe HTTP methods, operator for mutating methods.
3. Gates the process as the `'widget'` action (`gateProcess`): out of scope or hidden is 404, refused 403.
4. Looks up `widgetUpstream` from MongoDB, TTL-cached for 5 s (positive lookups only); 404 when missing, 503 when the lookup fails.
5. Injects inner auth: `BasicAuth`/`HeaderAuth` → request headers; `QueryAuth` → URL query parameter.
6. Strips the `/api/widget/:database/:prefix/:processId` prefix and forwards the sub-path to the upstream URL.
7. Maps upstream connection errors to 502 Bad Gateway; full error detail is logged server-side only.

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
  sessionId?: string | null,  // default: null; the initiating session
  access?: Access,
  params?: Record<string, unknown>,  // a task factory's values, passed to the engine unchanged
  ephemeral?: boolean,  // overrides the factory's retention of the launch's record
  stamp?: LaunchStamp,  // launchMetadata bound to the request; called for a factory's launch
                        // only, its map sent as the engine's `metadata` (see "Launch stamp")
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
): Promise<DismissCommandResult>  // 200 body: the record, or null when dismissing deleted it

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

// GET /api/processes/:id/result: a task's result file, streamed from GridFS.
async function getProcessResult(
  ctx: OptioContext,
  query: { database?: string; prefix?: string },
  id: string,           // either id form
  access?: Access,
  deps?: ResultDownloadDeps,  // default GRIDFS_RESULT_DOWNLOAD (result-download.ts)
): Promise<ResultDownloadResult>
// ResultDownloadResult =
//   | { status: 200; headers: { 'Content-Type'; 'Content-Length'; 'Content-Disposition';
//       'X-Content-Type-Options': 'nosniff'; 'Content-Security-Policy'?: 'sandbox';
//       'Cache-Control': 'private, no-store' }; body: Readable }
//   | { status: 404; body: { reason: 'not-found' | 'no-result'; message };
//       headers: { 'Cache-Control': 'private, no-store' } }
//   | ForbiddenResult
```

See "Conversation archive proxy" and "Result file" below.

The error response body (404/409, and 400 for a launch) for `launchProcess` / `cancelProcess` /
`dismissProcess` / `resurrectProcess` is `{ reason, message }`, typed via `LaunchErrorBody` / `CancelErrorBody` /
`DismissErrorBody` / `ResurrectErrorBody` in `optio-contracts/src/api-to-frontend.ts`. The
`reason` discriminator is one of the engine failure-reason enums from
`optio-contracts/src/engine-failure-reasons.ts`.

**Adapters** (`fastify`, `express`, `nextjs-app`, `nextjs-pages`): all four extract `body?.resume`
from the request body and forward it to `launchProcess` as the fourth argument; as the fifth
(`sessionId`), Fastify forwards `body?.sessionId ?? null` and the other three pass `null`; after
`access` come `body?.params` (a task factory's values), `body?.ephemeral`, and the stamp,
`opts.launchMetadata && launchStamp(opts.launchMetadata, req, body?.params)` (see "Launch
stamp"). For resurrect they forward `body?.sessionId ?? null` (the body is optional) to
`resurrectProcess` as the fourth argument.

Per the architectural rule above, command handlers do not validate state — they forward the raw
id to the engine via `engine.launch / cancel / dismiss / resurrect` and translate the discriminated-union
result into HTTP status + body. Failure reasons (`not-found`, `not-launchable`, `no-resume-support`,
`launch-blocked`, `not-cancellable`, `not-dismissable`, `shutting-down` for launch and resurrect,
for resurrect `not-resurrectable`, `no-resurrect-support`, `resurrect-in-progress`, and for launch
the task-factory reasons `parameters-required`, `invalid-parameters`, `not-a-factory`, and
`invalid-metadata` for a launch stamp that collides) come from the engine; the API only maps each
to a status: `not-found` is 404, launch's `parameters-required`, `invalid-parameters` and
`invalid-metadata` are 400, every other reason is 409. A factory's launch answers 200 with the
child's record. The body keeps `{ reason, message }`; for `invalid-parameters` the message is the
engine's `"<id>: <what>"` text passed on unchanged (optio-ui reads the parameter's id from it), for
`invalid-metadata` the engine's text naming the colliding keys, unchanged (each a fixed text only if
the engine sent none), for the other reasons a fixed text.

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

## Result file

`GET /api/processes/:id/result` (contract route `processes.getResult`) streams the
file a task stored with optio-core's `store_result`: one GridFS file in the default
`fs` bucket, named on the record as `result: { fileId, filename, contentType, size }`
(spec `docs/2026-10-25-factory-results-design.md` §1).

- `getProcessResult` (`handlers.ts`): `gateProcess(..., 'result')` (out of scope or
  hidden: 404 `not-found`; refused: 403 `{ message: 'Forbidden' }`); resolves the
  record (`findActionTarget`, either id form; missing: 404 `not-found`); then opens its
  file through `ResultDownloadDeps` (`result-download.ts`; `bucket(db)`, default
  `GRIDFS_RESULT_DOWNLOAD`). The record is read first and the file only through it, so
  a removed or hidden record never serves a leftover file (the engine's sweep deletes
  those later). No `result`, or a `result` whose file is gone: 404 `no-result`.
- 200: the GridFS download stream with `Content-Type` the record's `contentType`,
  `Content-Length` the GridFS file's length, and `contentDisposition(filename)`:
  `inline; filename="<ascii>"; filename*=UTF-8''<percent-encoded>`, where the ASCII
  fallback has anything outside printable ASCII, and `"`, `\`, `;`, `%`, replaced by
  `_`, so the header stays one valid line. Every 200 also carries
  `X-Content-Type-Options: nosniff`, and every 200 whose `contentType` is not exactly one
  `application/pdf` type (any case, parameters allowed, no comma: browsers split a
  Content-Type on commas and render the last type, so `application/pdf;a=b, text/html` is
  HTML to them) carries `Content-Security-Policy: sandbox` (`resultSafetyHeaders`): the
  file renders inline in the host app's origin, next to its session cookie, and
  `store_result` takes any media type, so script in an HTML, SVG, XHTML or XML result
  would otherwise run as the app. PDF is left out because a sandboxed document may refuse
  Chromium's built-in PDF viewer. Adapters send every header of `headers`.
- The 200 and both 404s carry `Cache-Control: private, no-store` (the file is one user's,
  and a process without a result may store one later); adapters send a 404's `headers`
  with its JSON body (Fastify `reply.headers`, Express `res.set`, Next Pages
  `res.setHeader`, Next App the `Response`'s headers). The 403 has none.
- Every adapter serves it outside ts-rest (Fastify and Express as their own routes, the
  Next adapters as a raw match before ts-rest): ts-rest would send the contract's
  `application/octet-stream` in place of the file's own type, and `@ts-rest/express`
  pipes without closing the response when GridFS fails mid-file. Express and the Pages
  Router pipe with `pipeline` (a client abort or a GridFS error closes both ends), the
  App Router answers `Readable.toWeb(body)`, Fastify sends the stream. The query is
  checked with the contract's own schema (`resultQueryOf`; a malformed one is a 400).
  The Pages Router's ts-rest router still lists a never-reached `getResult`:
  `@ts-rest/next` recurses without end into a contract route that has no
  implementation, which would break every route after it.
- `result.fileId` is optio's own: `stripServerSideFields` drops it from every REST
  answer (single reads, lists, trees, command replies), and the streams send `result`
  through `publicResult` (see "Stream Poller").

## Types

```typescript
interface ListQuery {
  cursor?: string;              // ObjectId string; cursor-based pagination
  limit: number;
  rootId?: string;              // filter by root process ObjectId
  state?: string;               // filter by status.state
  metadataFilter?: ProcessMetadataFilter;  // metadata predicate (or legacy flat exact-match map); parsed from URL-encoded JSON
}

interface PaginationQuery {
  cursor?: string;  // numeric string index into log array
  limit: number;
}

interface TreeLogQuery extends PaginationQuery {
  maxDepth?: number;  // relative depth limit from queried process
}

// Per-command result types. The 200 body is a process; an error body (404/409,
// and 400 for a launch) is `{ reason, message }` where `reason` is the corresponding engine failure-reason
// enum from `optio-contracts/src/engine-failure-reasons.ts`.
type LaunchCommandResult =
  | { status: 200; body: any }
  | { status: 400 | 404 | 409; body: { reason: LaunchFailureReasonType; message: string } };

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
  visibility: ProcessMetadataFilter | null; // required; access.visibility; read through processReads
  hub?: ProcessChangeHub;              // default: the shared hub of (db, prefix)
}

interface TreePollerOptions extends Omit<StreamPollerOptions, 'metadataFilter' | 'scope'> {
  rootId: string;     // ObjectId string of the tree root
  baseDepth: number;  // absolute depth of the queried process
  maxDepth?: number;  // relative depth limit from baseDepth
}

interface MultiTreePollerOptions {
  db: Db; prefix: string;
  sendEvent: (data: unknown) => void; onError: () => void;
  treeRoots: Array<{ rootId: ObjectId; baseDepth: number }>;
  flatIds: ObjectId[];
  maxDepth?: number;
  visibility: ProcessMetadataFilter | null;
  hub?: ProcessChangeHub;
}

interface SessionEventsPollerOptions {
  db: Db; prefix: string; sessionId: string;
  sendEvent: (data: unknown) => void; onError: () => void;
  scope?: ScopeFilter | null;
  visibility: ProcessMetadataFilter | null;
  hub?: ProcessChangeHub;
}

interface ListPollerHandle {
  start(): void;  // subscribes to the hub, then reads once
  stop(): void;   // unsubscribes; no read after it
}

function createListPoller(opts: StreamPollerOptions): ListPollerHandle
function createTreePoller(opts: TreePollerOptions): ListPollerHandle
function createMultiTreePoller(opts: MultiTreePollerOptions): ListPollerHandle
function createSessionEventsPoller(opts: SessionEventsPollerOptions): ListPollerHandle
```

**`visibility`.** Every poller builds one `ProcessReads` at creation
(`processReads(db, prefix, visibility ?? null)`) and reads only through it,
next to the existing `scope` option of the list and session-events pollers;
`null`/`undefined` means no restriction, with queries identical to today's.
The tree and multi-tree pollers additionally prune orphans out of each read
when `visibility` is set (multi-tree passes `keep` = the flat ids the read
actually returned, since they are fetched standalone and have no ancestors
in the same query), and recompute `members` from what is left after pruning.
Their relevance rule becomes `treeRelevant(c, roots, members, {
metadataChanges: visibility != null })`: with a visibility predicate active,
a metadata change of *any* process is relevant, not only a member's, because
it may make a currently-hidden process visible (the "relevance fix" — the
list stream already re-reads on any metadata change; without `visibility`
the rule is unchanged from today).

**Mid-stream transitions** behave like creation and deletion do today
(spec: "Streams"). A process becoming visible appears in the next update,
with its full log; the entry point itself becoming hidden leaves the
stream, as for a deletion. That is literally `processes: []` only when the
entry point *is* the tree's root: the tree poller's query filters by
`rootId`, not by the entry's own `_id`, so it reads the whole tree and then
`pruneOrphans` walks each process's parent chain down from a `parentId`-less
root; hiding a non-root entry only drops that entry's own subtree, leaving
the rest of the tree (siblings, ancestors) streaming as before. A process
becoming hidden leaves the next update together with its subtree and sends
no further lines; lines already sent stay with the client, as they do for
a deletion. A multi-tree stream's hidden ids are reported in `resolution`'s
`missing` list exactly like nonexistent ones (adapter-level; unaffected by
this change).

**A visible non-root entry under a hidden root** (the root's own metadata
no longer matches, while the entry and its subtree still do -- the
diverged-metadata edge case) empties the tree stream and the tree log
completely: the root itself fails the visibility-filtered read, so no
document in the tree has a `parentId`-less (or otherwise already-kept)
anchor for `pruneOrphans` to walk down from, and the entry's own subtree is
pruned along with everything else, even though the entry itself is
visible. `GET /processes/:id/tree` (REST) is unaffected by this: it reads
the entry's own document directly, then walks down by `parentId` from
there (`buildTree`), never up through the root, so it still returns the
entry's subtree in full.

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

The list, tree and multi-tree pollers' `update` processes carry a task factory's `taskFactory`
(`false` when absent) and `parameters` (absent on other records), and the tree and multi-tree ones
`warning` too (the list's always did): optio-ui opens a factory's launch form from them (spec
`docs/2026-10-20-task-factory-design.md` §3). `LIST_PROJECTION` reads `taskFactory` and
`parameters`, so the list re-reads when a shown process's change touches them; `warning`,
`taskFactory` and `parameters` are in every poller's snapshot fingerprint, so a resync that changes
only a factory's definition sends the update again. The tree and multi-tree pollers share one
snapshot and one payload builder (`treeSnapshotOf`, `treeUpdateOf` in `stream-poller.ts`). All
three also carry `ttlSeconds` (how long the record is kept after its run ended, in seconds; a
factory's is its launches', which optio-ui's launch form reads): every record has it, `null` when
it has no TTL (spec `docs/2026-10-22-task-factory-launch-options-design.md` §1.3). It is read by
`LIST_PROJECTION` and part of every snapshot fingerprint. All three carry `result` too (a task's
result file, `{ filename, contentType, size }`; absent when there is none), read by
`LIST_PROJECTION` and part of every snapshot fingerprint, always through `publicResult`
(`result-download.ts`), which drops its GridFS `fileId`.

`createTreePoller` sends all existing log entries on the first poll, then only deltas. It detects log truncation (e.g. after resync) and emits `log-clear` before new entries.

The `widgetData` field is included in tree-stream `update` events and is part of the snapshot fingerprint, so worker-side mutations (via `ctx.set_widget_data`) trigger a new SSE event. The list stream (`createListPoller`) does **not** include `widgetData` — it is omitted from sidebar payloads.

`createListPoller` reads only the fields it sends (a projection: no `log`, `widgetData`, `params` or `sessionEvents`), so a poll costs the summary, not the processes' logs.

`widgetUpstream` and `controlUpstream` are **never** included in any client-facing payload (list stream, tree stream, or REST responses): both carry `innerAuth` credentials. REST responses drop them in `stripServerSideFields` (handlers.ts); the streams never select them. A result's `fileId` (the GridFS id the result route reads by) is never included either: `stripServerSideFields` drops it, and the streams, which do read `result`, send it through `publicResult`.

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
(`optio-contracts`, `processesContract`) can express it via Zod
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
