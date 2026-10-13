# optio-api: per-request process visibility

Status: approved by the owner on 2026-10-13, in a brainstorm with "antd port" (aoe:fa9e60c76e02).
Origin: the brief `superego:~/chat/briefs/2026-10-13-optio-read-authorize.md`. It states the need
and proposed calling `authorize` with a `'read'` action for each process. This design solves the
same need differently; the brief's mechanism is not used.

## Problem

One optio instance serves several users of a host, and some processes must exist for only some
of them. The first case is excavator's data chats: a chat, and its process, should be visible to
its owner alone.

"Doesn't exist for you" must hold on every optio-api surface:
- the list, single reads, trees and logs;
- the live streams;
- the widget proxy, the saved conversation, and the actions.

No side channel may reveal a hidden process: not a count, not a 403 where a missing process gets
404, not a difference in the multi-tree `missing` list. optio stays free of policy: the host
decides who sees what.

Today the only per-request read control is `scope`: a flat exact-match metadata map, the tenant
boundary. It cannot say "everything of this customer except other users' chats". `authorize`
decides actions only.

## Decisions

1. **The rule is a predicate over the process's own metadata.** The host controls the metadata
   it attaches to its processes, so every foreseen rule can be decided from that metadata plus
   facts about the requesting user. Facts about the user (id, roles, groups) can be folded in,
   because the predicate is built per request. A per-process lookup in the host's own data is not
   needed.
2. **A separate `visibility` hook, next to `scope` and `authorize`.** `scope` remains the tenant
   boundary, and also bounds what a resync may regenerate or prune; the engine runs resync with a
   flat filter. Widening `scope` into a predicate would break that, and would mix "which tenant"
   with "which of the tenant's processes this user sees". Calling `authorize` once per process
   was rejected: it forces filtering after the query, which breaks paging and counts and makes
   stream reads cost one host call per process.
3. **optio evaluates the predicate only inside MongoDB queries,** never with a JavaScript copy of
   the semantics. There is one evaluator, so the rules cannot drift apart.
4. **A hidden process gets exactly the answer a nonexistent one gets** on every surface,
   including actions: 404 where today a refused action gets 403.
5. **`'read'` is removed from `OptioAction`.** It was declared but never passed to `authorize`.
   Keeping it would invite the reading the brief made.
6. **One choke point for process reads** (added in review, 2026-10-13). The rule is declared
   once by the host. Inside optio it must not depend on every route remembering to apply it,
   because a route added later could forget. Every read whose result can reach a response goes
   through one accessor, `processReads`, which always ANDs the condition in. A guard test fails
   the build when code reads the collection any other way (see "One choke point").

## The hook

```ts
/** Which processes this request may know about: a metadata predicate in optio's filter
 *  language (the same one as `metadataFilter`), or null for all of them. */
export type VisibilityCallback<TRequest> =
  (req: TRequest) => ProcessMetadataFilter | null | Promise<ProcessMetadataFilter | null>;

export interface AccessHooks<TRequest> {
  scope?: ScopeCallback<TRequest>;           // tenant boundary (unchanged)
  visibility?: VisibilityCallback<TRequest>; // new
  authorize?: AuthorizeCallback<TRequest>;   // what a request may do to a process it can see
}
```

- **Wiring.** All four adapters accept `visibility` in their options, next to `scope` and
  `authorize`.
- **Resolution.** The hook is resolved once per request, together with `scope`. A stream is one
  request: its predicate is fixed when the stream opens, and every re-read applies it to the
  processes' current metadata.
- **Empty values.** `null`, `undefined` and an empty map mean "no visibility restriction".
- **Validation.** The returned value is validated with `ProcessMetadataFilterSchema`, the schema
  `metadataFilter` uses. An invalid predicate fails the request (500) instead of passing it
  through unfiltered.
- **`Access`.** It gains `visibility: ProcessMetadataFilter | null` and is `restricted` when any
  of the three hooks is passed. When no hook is passed, `Access` stays `UNRESTRICTED`: no
  lookups and no extra conditions, as today.
- **Builders.** optio-api re-exports optio-contracts' predicate builders (`and`, `or`, `not`,
  `leaf`, `eq`, `ne`, `isIn`, `notIn`, `exists`, `gt`, `gte`, `lt`, `lte`), so a host writes its
  predicate with no direct dependency on optio-contracts.

### Semantics

- **What visible means.** A process is visible when its own `metadata` satisfies the predicate.
  Children inherit their parent's metadata when they are created (optio-core), so no fallback to
  the root is needed. `scope`'s single-process root fallback stays as it is.
- **Missing keys follow MongoDB rules, deliberately.** A missing key equals nothing:
  `eq('ownerId', me)` hides a process that has no `ownerId`, so the rule fails closed. `ne`,
  `nin` and `NOT` do match a missing key, so `not(eq('kind', 'data-chat'))` keeps every
  process that isn't a chat.
- **Excavator's rule, for illustration** (the change itself is excavator's, not part of this
  work):
  `visibility: (req) => or(not(eq('kind', 'data-chat')), eq('ownerId', userIdOf(req)))`.
- **Declared once.** The host registers the hook once, with the adapter's other options. Clients
  never send it, and no route takes it as a parameter. It is a function of the request only so
  that a rule can name the requesting user; a rule that doesn't depend on the user ignores `req`.
- **Rules per group of processes** are clauses of the one predicate, typically one per `kind`,
  plus a last clause that decides everything else, including processes with no `kind`:
  ```ts
  visibility: (req) => or(
    and(eq('kind', 'data-chat'), eq('ownerId', me(req))),     // chats: their owner only
    and(eq('kind', 'report'), isIn('teamId', teams(req))),    // reports: the user's teams
    not(isIn('kind', ['data-chat', 'report'])),                // everything else: visible
  )
  ```

## Where it applies

**The condition.** "The condition" below means the request's visibility predicate translated
with `metadataFilterToMongo`. Where scope applies today, it is ANDed with the scope's Mongo form.

### REST

| Route / handler | Behaviour with a visibility predicate |
|---|---|
| `GET /processes` (`listProcesses`) | The condition joins the query (with `metadataFilter`, scope, `rootId`, `state` and the cursor). `countDocuments` uses the same filter, so `totalCount` is exact and pages are full. Cursor semantics and the contract are unchanged. |
| `GET /processes/:id`, `GET /processes/:id/log` | The lookup by either id form includes the condition. A hidden process is a 404, the same as a missing one. |
| `GET /processes/:id/tree` | A hidden entry point is a 404. The tree is built downward from the entry point, fetching only visible children, so a hidden process takes its subtree with it. |
| `GET /processes/:id/tree/log` | A hidden entry point is a 404. The tree's processes are fetched with the condition and pruned (see Trees), and only the remaining processes' log lines go in. `totalCount` counts those lines. |
| Action gate (`gateProcess`) | The lookup includes the condition. A hidden process is a 404 before `authorize` is asked; only a visible one goes to `authorize` (403 when it refuses). This covers launch, cancel, dismiss and resurrect, the widget proxy (HTTP and WebSocket), widget-control, widget-upload, and `GET /processes/:id/conversation`. |
| Unaffected | Resync (not per-process; bounded by `scope`), `/optio/instances`, and everything when no hooks are passed. **A clean resync (`clean: true`) deletes every root record its scope filter matches, hidden ones included** (owner ruling 2026-10-13): the host's `authorize('resync', { clean })` decides who may run one. A plain resync only regenerates, and prunes records whose task no longer exists. |

### Trees

- **Per-process rule.** Visibility is judged for each process from its own metadata.
- **No orphans.** Tree-shaped answers (the tree, the tree log, and the tree and multi-tree
  streams) additionally never contain an orphan. A process is kept only when its parent is kept
  or it has no parent. A flat list has no notion of an orphan, so it applies only the
  per-process rule.
- **Scope in trees.** Trees and the tree streams check `scope` at the entry point, as today. Their
  reads of the rest of the tree add only the visibility condition. Adding scope to the descendant
  queries would change today's results for scope-only hosts, because scope's query form has no
  root fallback for a child that lacks the scope keys.
- **Edge case: a visible child under a hidden parent.** This needs metadata that diverged after
  inheritance, e.g. a host re-stamping only the root. Such a child stays readable on its own
  (single read, list) but appears in no tree. This is documented rather than paid for with an
  ancestor walk on every read.

### Streams (`stream-poller.ts`, shared by all adapters)

- **The list stream (`/processes/stream`).** The condition joins its query, with scope and
  `metadataFilter`. It already re-reads on inserts and on any metadata change, so a process that
  appears or disappears shows up on the next update.
- **The tree stream (`/processes/:id/tree/stream`).**
  - A hidden entry point is a 404 at open, the same as an out-of-scope one.
  - Each read fetches with the condition and prunes orphans. Log lines come only from the
    processes that remain.
  - **Relevance fix.** Today a tree stream re-reads only for changes to processes it already
    shows, so a hidden process becoming visible would go unnoticed. With a visibility predicate
    active, a tree stream also re-reads for any metadata change, as the list stream already
    does. A false positive costs one read; metadata changes are rare.
- **The multi-tree stream (`/processes/tree/multi/stream`, fastify and nextjs-pages).**
  - Hidden ids are reported in the `resolution` event's `missing` list, exactly like
    nonexistent ids.
  - Each read applies the condition. Processes reached through a tree are pruned for orphans;
    flat ids are filtered by the condition only. The relevance fix applies here too.
- **Session events (`/session-events/stream`).** The condition joins its filter (scope already
  does). A hidden process's events don't stream.
- **Mid-stream transitions behave like creation and deletion do today.**
  - A process becoming visible appears in the next update, with its full log.
  - A process becoming hidden leaves the next update, together with its subtree, and sends no
    further lines. Lines already sent stay with the client, as they do when a process is
    deleted mid-stream.
  - An entry point becoming hidden leaves the stream empty, as for a deleted root.

The pollers take the visibility predicate as an option (next to the existing `scope` option of
the list and session-events pollers). The adapters only pass it through. The filtering, pruning
and relevance logic live in `stream-poller.ts` and `process-change-relevance.ts`.

## One choke point

`src/process-reads.ts` is the only module that names the `{prefix}_processes` collection and the
only one that applies the visibility condition:

```ts
export function processCollection(db: Db, prefix: string): Collection;

/** Reads of one request: every query ANDs the request's visibility condition. */
export interface ProcessReads {
  readonly visibility: ProcessMetadataFilter | null;
  find(filter: Filter<Document>, options?: FindOptions): FindCursor<WithId<Document>>;
  findOne(filter: Filter<Document>, options?: FindOptions): Promise<WithId<Document> | null>;
  countDocuments(filter: Filter<Document>, options?: CountDocumentsOptions): Promise<number>;
  /** The collection without the condition, for access-scope.ts alone (scope's root lookup and the
   *  action gate's target lookup, each followed by an explicit visibility check). */
  readonly unfiltered: Collection;
}
export function processReads(db: Db, prefix: string, visibility: ProcessMetadataFilter | null): ProcessReads;
export function processReadsOf(col: Collection, visibility: ProcessMetadataFilter | null): ProcessReads;
```

- **Null visibility.** With `visibility` null, every method passes the caller's filter through
  unchanged, so hosts without the hook see identical queries.
- **Callers.** The handlers, the stream pollers and the adapters read processes only through
  `ProcessReads`.
- **Lookup helpers.** `access-scope.ts`'s lookups (`findScopedProcess`, `findReadableProcess`,
  `findActionTarget`, `inScope`, `gateProcess`) accept a `ProcessReads` or, for custom adapters
  and compatibility, a plain `Collection`.
- **The guard test** (`process-reads-guard.test.ts`) scans the non-test sources and fails when:
  1. any file other than `process-reads.ts` names a processes collection (a template ending in
     `_processes`);
  2. `processCollection(` is called outside its allowlist. Each allowed file carries its reason
     in the test:
     - `process-change-hub.ts`: a change stream, which serves no documents;
     - `discovery.ts`: instance discovery, gated by `'instances'`;
     - `widget-proxy-core.ts` and `agent-input.ts`: upstream lookups that run only after
       `gateProcess` admitted the process;
  3. `.unfiltered` is used outside `access-scope.ts` and `process-reads.ts`.

A new route that reads processes therefore either goes through `processReads` or fails the build
until its file is deliberately allowlisted, with a reason, in review.

## `authorize` after this change

`authorize` decides what a request may **do** to a process it can see, plus resync and instance
discovery. Its doc comment ("Decides a mutating action (and instance discovery)") becomes
accurate again; the saved conversation is gated as `'widget'`. `OptioAction` loses `'read'`. A
host with `case 'read':` gets a compile error, which points it to `visibility`.

## Compatibility and release

- **Additive.** A new optional hook. Hosts with no hooks, with `scope` only, or with `authorize`
  only behave exactly as before.
- **404 instead of 403.** A host that adds `visibility` also gets 404 instead of 403 for actions
  on processes it hides. That is intended.
- **Breaking, type-level only.** `'read'` is removed from `OptioAction`. Hence a **minor**
  release: optio-api 0.2.x → 0.3.0, via `make release-optio-api BUMP=minor`
  (`docs/release-cookbook.md`), on the owner's go only.
- **Excavator.** It adopts this in its own change: bump optio-api, add its `visibility` rule, and
  stamp `ownerId` on chat processes.

## Documentation (same commit as the API change, per the root AGENTS.md)

- **`auth.ts`:** doc comments for `VisibilityCallback`, `AccessHooks`, `Access` and
  `AuthorizeCallback`.
- **`packages/optio-api/AGENTS.md`, "Access scope and authorization":**
  - the three hooks and how they combine;
  - the MongoDB missing-key rules;
  - the tree rule and the divergent-metadata edge case;
  - stream behaviour mid-stream;
  - the removal of `'read'`.
- **The root `AGENTS.md`, "TypeScript: optio-api"** (the options listing and the access
  comments): kept consistent with the package file.
- **The optio-api `README.md`:** the access-hooks example gains `visibility`.

## Testing

TDD, affected test files only; a baseline of the 13 affected files was green (279 tests). No
wall-clock dependence: streams are awaited by condition with a generous ceiling.

- **Access.** The hook is resolved and validated. Null, undefined and an empty map mean no
  restriction. An invalid predicate rejects. `restricted` is true with `visibility` alone.
- **`gateProcess`.** A hidden process gets 404 and `authorize` is not called. A visible one gets
  today's behaviour (`authorize` decides, 403 on refusal).
- **Handlers.**
  - The list drops hidden processes, `totalCount` is exact, and paging reaches every visible
    process.
  - `getProcess` and `getProcessLog` answer 404 for a hidden process.
  - The tree omits a hidden subtree.
  - The tree log omits a hidden subtree's lines and counts exactly.
  - Orphans are pruned when metadata diverges.
  - The conversation route answers 404 for a hidden process.
- **Pollers.**
  - The list, tree, multi-tree and session-events streams omit hidden processes; the multi-tree
    `missing` list includes hidden ids.
  - A process becoming visible mid-stream appears; one becoming hidden disappears.
  - Tree relevance: a metadata change is relevant when visibility is active, and irrelevant
    otherwise, as today.
- **Adapters (fastify, express, nextjs-pages, nextjs-app).** `visibility` is passed through: a
  hidden process is a 404 on a single read and at tree-stream open, and a malformed predicate is
  a 500.
- **The choke point.**
  - `processReads` ANDs the condition into `find`, `findOne` and `countDocuments`, and passes
    the filter through untouched when the visibility is null.
  - The guard test enforces its three rules.
- **Unchanged.** The existing action, scope and no-hooks tests stay as they are; the only edit is
  removing `'read'` where a test uses it.
