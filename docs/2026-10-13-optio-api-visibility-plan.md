# optio-api visibility hook: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-request `visibility` hook to optio-api. It is a metadata predicate that optio
ANDs into every read, every stream and the action gate, so a hidden process answers exactly like a
missing one.

**Architecture:**
- **Helpers.** A small module `src/visibility.ts` holds the pure helpers: normalise and validate
  the predicate, translate it to Mongo, check one document in Mongo, prune tree orphans.
- **Access.** `auth.ts` resolves the hook into `Access.visibility`. `access-scope.ts` uses it
  for single-process lookups and the action gate.
- **Callers.** The handlers and pollers add the condition to their queries. The four adapters only
  pass the hook through.

**Tech stack:** TypeScript, MongoDB driver 6, vitest 3, filtrum predicates (via optio-contracts).

**Spec:** `docs/2026-10-13-optio-api-visibility-design.md`. Read it first; this plan argues from
it.

## Global constraints

**Where to work**
- All work happens on host `excavator`, reached only through
  `ssh -F /home/csillag/share/fleet-ssh/config excavator '<cmd>'`.
- The worktree is `~/deai/optio/.worktrees/csillag/visibility`, on branch `csillag/visibility`.
  The package is `packages/optio-api`.
- Never touch `~/deai/optio` itself: excavator's dev stack hot-reloads it.
- Never run `pnpm install`. `packages/optio-api/node_modules` is a symlink to the main checkout's;
  use `./node_modules/.bin/vitest` and `./node_modules/.bin/tsc` from the package directory.

**Editing files**
- Edit files on excavator, either with `scp -F /home/csillag/share/fleet-ssh/config` or with a
  python script piped over stdin (`ssh … 'python3 -' < script.py`).
- Single quotes inside a `'…'` ssh command break the quoting.

**Tests**
- Run only the affected test files, never the full suite.
- Always keep full results:
  `./node_modules/.bin/vitest run <files> --reporter=verbose > /tmp/vis-tests/<name>.log 2>&1`,
  then read the log. Never rerun just to learn what failed.
- Tests use the real MongoDB at `MONGO_URL` (default `mongodb://localhost:27017`), each file in
  its own `optio_test_*` database.
- No wall-clock dependence: wait on a condition with the file's `waitUntil(pred, 60_000)`, never
  sleep-then-assert.
- Baseline: the 13 affected files passed (279 tests); log in `/tmp/vis-tests/baseline.log`.

**Commits**
- Commits are signed automatically (`commit.gpgsign`). Add **no** `Co-Authored-By` or other
  self-credit lines.
- `git add` explicit paths only; never `git add -A` (the `node_modules` symlink must stay
  untracked).
- When a commit changes optio-api's public API (exports, types, adapter options), the same commit
  updates `packages/optio-api/AGENTS.md`. It also updates the root `AGENTS.md` ("TypeScript:
  optio-api", around lines 806–1018) where that file covers the same thing.

**Rules from the spec**
- With no hooks passed, behaviour is unchanged: `UNRESTRICTED`, no lookups, no extra conditions.
  With `scope` only or `authorize` only, behaviour is unchanged as well.
- Visibility is evaluated only inside Mongo queries, never by a JavaScript copy of the semantics.
- From Task 3b on, every process read whose result can reach a response goes through
  `processReads` (`src/process-reads.ts`). The guard test `process-reads-guard.test.ts` enforces
  this. No code outside `process-reads.ts` applies `andVisibility` itself.
- A hidden process answers exactly as a missing one: 404, absent, or in `missing`. On actions it
  is 404, before `authorize` is asked.
- Tree reads below the entry point add only the visibility condition, never scope. Orphan pruning
  applies only when a visibility predicate is set.
- No push and no release. The owner integrates and releases.

## Review focus

The inputs most likely to bite a host, each pinned by a test in the task named:

1. **A predicate key missing on a process.** `not(eq('kind', 'data-chat'))` must keep a process
   with no `kind`; `eq('ownerId', 'u1')` must hide a process with no `ownerId`. (Task 1)
2. **A duplicated `processId`** whose newest document is hidden while an older one is visible.
   The gate must answer 404, because the engine acts on the newest. (Task 2)
3. **Single reads by the `processId` string** (not the ObjectId) of a hidden process must answer
   404 as well. (Task 3)
4. **A client `metadataFilter` that tries to widen past visibility**, e.g.
   `or(eq('kind', 'data-chat'))`, must still see only visible processes. (Task 3)
5. **A hook that returns a legacy flat map** (`{ ownerId: 'u1' }`) must work as equality.
   (Task 2)

---

### Task 1: the visibility helpers (`src/visibility.ts`)

**Files:**
- Create: `packages/optio-api/src/visibility.ts`
- Create: `packages/optio-api/src/__tests__/visibility.test.ts`
- Modify: `packages/optio-api/src/index.ts` (exports)
- Modify: `packages/optio-api/AGENTS.md` and the root `AGENTS.md` (list the new helpers next to
  the "Scope helpers (for custom adapters)")

**Interfaces:**
- Produces:
  - `normalizeVisibility(raw: unknown): ProcessMetadataFilter | null`. `null`, `undefined` and
    `{}` give `null`. Otherwise the value is parsed with `ProcessMetadataFilterSchema` (from
    optio-contracts) and returned. On failure it throws
    `Error('optio-api: the visibility hook returned an invalid metadata predicate: <first zod issue message>')`.
  - `visibilityToMongo(visibility: ProcessMetadataFilter | null): Record<string, unknown>`.
    `null` gives `{}`; otherwise it returns `metadataFilterToMongo(visibility)`.
  - `andVisibility(base: Record<string, unknown>, visibility: ProcessMetadataFilter | null): Record<string, unknown>`,
    built like `andScope`: `base` when `null`; the condition alone when `base` is empty;
    otherwise `{ $and: [base, condition] }`.
  - `isVisible(col: Collection, doc: Document, visibility: ProcessMetadataFilter | null): Promise<boolean>`.
    `true` without a query when `visibility` is `null`. Otherwise it returns whether
    `col.countDocuments({ $and: [{ _id: doc._id }, condition] }, { limit: 1 })` is 1.
  - `pruneOrphans<T extends Document>(docs: T[], keep?: ReadonlySet<string>): T[]`. It returns
    the docs, in input order, whose `parentId` is null or absent, whose own `_id` (as a string)
    is in `keep`, or whose parent is itself kept. A parent not in `docs` counts as not kept.
    Memoise per id so each chain is walked once.

- [ ] **Step 1: Write the failing tests** in `visibility.test.ts` (real Mongo for `isVisible`, as
  in `access-scope.test.ts`):
  - `normalizeVisibility`:
    - `null`, `undefined` and `{}` give `null`.
    - `or(not(eq('kind', 'data-chat')), eq('ownerId', 'u1'))` is returned unchanged.
    - `{ ownerId: 'u1' }` (legacy flat) is returned unchanged.
    - `{ AND: [] }` throws an Error whose message starts with
      `optio-api: the visibility hook returned an invalid metadata predicate`.
  - `andVisibility`:
    - `({ a: 1 }, null)` gives `{ a: 1 }`.
    - `({}, eq('k', 'v'))` gives `{ 'metadata.k': 'v' }`, or whatever `metadataFilterToMongo`
      returns for it. Assert equality with `metadataFilterToMongo(eq('k', 'v'))`.
    - `({ a: 1 }, eq('k', 'v'))` gives `{ $and: [{ a: 1 }, metadataFilterToMongo(eq('k', 'v'))] }`.
  - `isVisible`, with predicate `or(not(eq('kind', 'data-chat')), eq('ownerId', 'u1'))`:
    - true for `{ kind: 'data-chat', ownerId: 'u1' }`;
    - false for `{ kind: 'data-chat', ownerId: 'u2' }`;
    - false for `{ kind: 'data-chat' }`, which has no owner (fails closed);
    - true for `{}` and `{ kind: 'source' }` (Review focus 1);
    - true for any doc with `null` visibility, and no query is issued then (pass a collection
      stub whose `countDocuments` throws).
  - `pruneOrphans`, on docs root R, child C (parent R), grandchild G (parent C), and orphan O
    (parent X, absent):
    - all kept except O;
    - with C removed from the input, G is dropped as well;
    - with `keep = new Set([O id])`, O is kept;
    - the input order is preserved.

- [ ] **Step 2: Run the tests and see them fail.**
  `./node_modules/.bin/vitest run src/__tests__/visibility.test.ts --reporter=verbose > /tmp/vis-tests/t1-red.log 2>&1`.
  Expected: they fail because `../visibility.js` cannot be resolved.

- [ ] **Step 3: Implement `src/visibility.ts`** with the five functions above. Import
  `metadataFilterToMongo` from `./metadata-filter-query.js` and `ProcessMetadataFilterSchema`
  from `optio-contracts`. Then export the five from `src/index.ts`, in a block
  `// Visibility helpers (for custom adapters)`.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/vis-tests/t1-green.log`. Also run
  `./node_modules/.bin/tsc --noEmit -p .`. Expected: all pass, and no type errors.

- [ ] **Step 5: Document the helpers and commit.** Add them to the "Scope helpers" lists in both
  AGENTS.md files, then commit:
  `git add packages/optio-api/src/visibility.ts packages/optio-api/src/__tests__/visibility.test.ts packages/optio-api/src/index.ts packages/optio-api/AGENTS.md AGENTS.md && git commit -m "feat(optio-api): visibility helpers (normalize, to Mongo, isVisible, pruneOrphans)"`

### Task 2: the hook in `Access`, single-process lookup, and the action gate

**Files:**
- Modify: `packages/optio-api/src/auth.ts`
- Modify: `packages/optio-api/src/access-scope.ts`
- Modify: `packages/optio-api/src/index.ts`
- Test: `packages/optio-api/src/__tests__/access-scope.test.ts`
- Docs: `packages/optio-api/AGENTS.md` ("Access scope and authorization"), the root `AGENTS.md`
  (the options listing and the access comments around lines 870–910)

**Interfaces:**
- Consumes: `normalizeVisibility` and `isVisible` (Task 1).
- Produces:
  - `export type VisibilityCallback<TRequest> = (req: TRequest) => ProcessMetadataFilter | null | Promise<ProcessMetadataFilter | null>`.
  - `AccessHooks<TRequest>` gains `visibility?: VisibilityCallback<TRequest>`.
  - `Access` gains `visibility: ProcessMetadataFilter | null`. `UNRESTRICTED.visibility` is
    `null`.
  - `export function hasAccessHooks(hooks: AccessHooks<any>): boolean`: true when `scope`,
    `visibility` or `authorize` is set. `resolveAccess` and `accessFor` use it in place of their
    `!hooks.scope && !hooks.authorize` checks.
  - `resolveAccess` sets `visibility: normalizeVisibility(hooks.visibility ? await hooks.visibility(req) : null)`.
    An invalid predicate therefore rejects the access promise.
  - `OptioAction` without `'read'`.
  - `findReadableProcess(col: Collection, id: string, access: Pick<Access, 'scope' | 'visibility'>): Promise<Document | null>`,
    in `access-scope.ts`. It does `findScopedProcess`, then `isVisible`; null when either fails.
  - `gateProcess` answers `{ ok: false, status: 404 }` for a process that is not visible, before
    calling `authorize`.
  - `index.ts` exports `VisibilityCallback`, `hasAccessHooks` and `findReadableProcess`. It also
    re-exports `and, or, not, leaf, eq, ne, isIn, notIn, exists, gt, gte, lt, lte` from
    `optio-contracts`, under `// Predicate builders (for visibility hooks)`.

- [ ] **Step 1: Write the failing tests** in `access-scope.test.ts`:
  - `resolveAccess`:
    - with only `visibility`, `restricted` is true, `scope` is null, and `visibility` equals the
      hook's predicate;
    - an async hook works;
    - a hook returning `null` or `{}` gives `visibility: null`;
    - a hook returning `{ AND: [] }` rejects with the `invalid metadata predicate` message;
    - the legacy `{ ownerId: 'u1' }` is kept (Review focus 5);
    - `accessFor` resolves the hook once per request (assert the call count, as the existing
      scope test does).
  - `findReadableProcess`:
    - null for a hidden process, by `_id` and by `processId`;
    - the document for a visible one;
    - scope is still applied.
  - `gateProcess`:
    - a hidden process gives 404, and the `authorize` mock is not called;
    - a visible one that `authorize` denies gives 403;
    - a duplicated `processId` whose newest document is hidden and whose older one is visible
      gives 404 (Review focus 2).
  - Change any existing test that uses the action `'read'` to another action.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/vis-tests/t2-red.log`. Expected:
  the new tests fail (no `visibility` on `Access`, no `findReadableProcess`).

- [ ] **Step 3: Implement** the items listed under Produces. Rewrite the doc comments in
  `auth.ts`:
  - `AuthorizeCallback`: "Decides what a request may do to a process it can see (actions, widget
    access, the saved conversation), plus resync and instance discovery; `false` gives 403. A
    process the request cannot see is a 404 before `authorize` is asked."
  - `VisibilityCallback` (spec, "The hook"): the predicate, when it is resolved, the
    empty-value rule, the missing-key rule, and that an invalid predicate fails the request.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/vis-tests/t2-green.log`, together
  with `visibility.test.ts`. Then run `tsc --noEmit -p .`. Expected: all pass, and no type
  errors.

- [ ] **Step 5: Docs and commit.**
  - In the "Access scope and authorization" section, document:
    - the three hooks and how they combine;
    - `VisibilityCallback`, with excavator's example from the spec;
    - the MongoDB missing-key rule;
    - 404 before `authorize`;
    - the removal of `'read'`.
  - Mirror the root `AGENTS.md` access comments to match.
  - Commit with the message: `feat(optio-api): visibility hook in Access; hidden processes 404 at the action gate; drop the unused 'read' action`.

### Task 3: the read handlers

**Files:**
- Modify: `packages/optio-api/src/handlers.ts`
- Test: `packages/optio-api/src/__tests__/handlers-access.test.ts`

**Interfaces:**
- Consumes: `andVisibility`, `pruneOrphans` (Task 1); `findReadableProcess` and
  `Access.visibility` (Task 2).
- Produces: no new exports. The handler signatures are unchanged.

- [ ] **Step 1: Write the failing tests** in `handlers-access.test.ts`. Add a helper
  `visible(predicate)` that returns an `Access` with `restricted: true`, `scope: null`, the
  predicate as `visibility`, and an `authorize` mock resolving true. Use the predicate
  `or(not(eq('kind', 'data-chat')), eq('ownerId', 'u1'))`.
  - `listProcesses`:
    - Fixture: 3 visible and 3 hidden processes, interleaved by `_id`.
    - Following `nextCursor` with `limit: 1` returns every visible process, exactly once, in
      `_id` order. Every page holds 1 item, and `totalCount` is 3 on every page.
    - A client `metadataFilter` of `or(eq('kind', 'data-chat'))` still returns only the visible
      chat (Review focus 4).
  - `getProcess` and `getProcessLog` return null for a hidden process, both by `_id` and by its
    `processId` string (Review focus 3). They return the process for a visible one.
  - `getProcessTree`:
    - Fixture: visible root, hidden child, visible grandchild under it (diverged metadata).
    - The tree has no children.
    - A hidden root gives null.
  - `getProcessTreeLog`, same fixture:
    - only the root's log lines are returned, and `totalCount` is 1;
    - a hidden entry gives null.
  - `getProcessConversation` answers 404 (`reason: 'not-found'`) for a hidden process, and
    `authorize` is not called.
  - Without access (`UNRESTRICTED`), the tree with a deleted middle parent keeps today's result.
    This pins "prune only with visibility".

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/vis-tests/t3-red.log`.

- [ ] **Step 3: Implement:**
  - `listProcesses`: the filter becomes
    `andVisibility(andScope(metadataFilterToMongo(...), access.scope), access.visibility)`. Then
    the `rootId`, `state` and cursor keys are added as now. `countDocuments` uses the same
    filter.
  - `getProcess`, `getProcessLog`, `getProcessTree` and `getProcessTreeLog`: replace
    `findScopedProcess(col, id, access.scope)` with `findReadableProcess(col, id, access)`.
  - `buildTree` gains a `visibility: ProcessMetadataFilter | null` parameter. Its children query
    becomes `andVisibility({ parentId }, visibility)`.
  - `getProcessTreeLog`: its process query becomes `andVisibility(filter, access.visibility)`.
    When `access.visibility` is set, pass the result through `pruneOrphans` before collecting
    log lines.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/vis-tests/t3-green.log`, together
  with `handlers.test.ts` and `access-scope.test.ts`. Then run `tsc`. Expected: all pass.

- [ ] **Step 5: Commit** with the message: `feat(optio-api): read handlers honour visibility (exact list totals, 404 for hidden, pruned trees)`.

### Task 3b: one choke point for process reads (`src/process-reads.ts`, added 2026-10-13)

The owner's review: the rule is declared once, so inside optio it must not depend on each route
remembering to apply it. Spec: "One choke point" and decision 6. Task 3 is already committed
(it applies `andVisibility` in `handlers.ts` directly); this task moves that into the choke point.

**Files:**
- Create: `packages/optio-api/src/process-reads.ts`
- Create: `packages/optio-api/src/__tests__/process-reads.test.ts`
- Create: `packages/optio-api/src/__tests__/process-reads-guard.test.ts`
- Modify:
  - `src/handlers.ts` and `src/access-scope.ts`;
  - `src/widget-proxy-core.ts`, `src/agent-input.ts`, `src/discovery.ts` and
    `src/process-change-hub.ts` (each uses `processCollection(db, prefix)` instead of naming the
    collection);
  - `src/index.ts`.
- Docs: both AGENTS.md files (the choke point, for custom-adapter authors and for anyone adding
  a route).

**Interfaces:**
- Consumes:
  - `andVisibility`, `pruneOrphans` (Task 1);
  - `Access.visibility`, `findReadableProcess`, `gateProcess` (Task 2).
- Produces, exactly as in the spec's "One choke point":
  - `processCollection`, `ProcessReads`, `processReads` and `processReadsOf`, all exported from
    `index.ts`.
  - `ProcessReads.unfiltered`, for `access-scope.ts` alone.
  - The `access-scope.ts` lookups (`findScopedProcess`, `findReadableProcess`,
    `findActionTarget`, `inScope`, `gateProcess`) accept `Collection | ProcessReads` as their
    first parameter. A `Collection` is wrapped with `processReadsOf(col, null)` where only
    unfiltered reads are needed. Raw reads inside access-scope go through `.unfiltered`, each
    followed by its existing explicit visibility check.
  - The guard test's `PENDING` list holds `stream-poller.ts` and `adapters/*.ts` for now.
    Task 4 and Task 5 empty it.

- [ ] **Step 1: Write the failing tests.**
  - **`process-reads.test.ts`, on real Mongo, with its own `DB_NAME`.** With visibility
    `eq('ownerId', 'u1')`:
    - `find({})` returns only the u1 processes;
    - `findOne({ _id: <u2 id> })` gives null;
    - `countDocuments({})` counts only u1;
    - a caller filter is ANDed, not replaced: `find({ 'metadata.ownerId': 'u2' })` is empty.
    
    With `null` visibility, `find`, `findOne` and `countDocuments` receive the caller's filter
    object itself. Use a collection stub that records the argument and assert identity.
  - **`process-reads-guard.test.ts`.** Read every `.ts` file under `src`, excluding
    `__tests__`, and assert the spec's three rules:
    1. no file but `process-reads.ts` matches `/\$\{[^}]*\}_processes/`;
    2. `processCollection(` appears only in `process-reads.ts`, `process-change-hub.ts`,
       `discovery.ts`, `widget-proxy-core.ts`, `agent-input.ts` and the `PENDING` files. The
       allowlist is a `Record<file, reason>` with the spec's reasons;
    3. `.unfiltered` appears only in `access-scope.ts`, `process-reads.ts` and the `PENDING`
       files.
    
    `PENDING` = `['stream-poller.ts', 'adapters/fastify.ts', 'adapters/express.ts', 'adapters/nextjs-app.ts', 'adapters/nextjs-pages.ts']`,
    commented "migrated by Task 4 / Task 5". The `PENDING` files are exempt from rules 1–3.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/vis-tests/t3b-red.log`. Expected:
  `process-reads.js` is missing. The guard flags `handlers.ts`, `widget-proxy-core.ts`,
  `agent-input.ts`, `discovery.ts` and `process-change-hub.ts`.

- [ ] **Step 3: Implement.**
  - **`process-reads.ts`:**
    - `processReads(db, prefix, v)` is `processReadsOf(processCollection(db, prefix), v)`;
    - each method calls `andVisibility(filter, v)` and the collection's own method;
    - with `v` null, it passes the filter object through.
  - **`handlers.ts`:**
    - drop its `col()` helper;
    - each handler builds `const reads = processReads(db, prefix, access.visibility)` once and
      uses it for every read;
    - remove every direct `andVisibility` call. The list filter is `andScope(...)` plus the
      `rootId`, `state` and cursor keys; `reads` adds the visibility;
    - `buildTree` takes `reads` instead of `db, prefix, visibility`;
    - the tree log prunes when `reads.visibility` is set;
    - gates and lookups are passed `reads`.
  - **`access-scope.ts`:** accept `Collection | ProcessReads` as described above. The lookups'
    behaviour does not change. All Task 2 tests must keep passing untouched.
  - **The other four modules:** replace the template with `processCollection(db, prefix)`.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/vis-tests/t3b-green.log`, together
  with `handlers-access.test.ts`, `handlers.test.ts`, `access-scope.test.ts`,
  `visibility.test.ts`, the widget-proxy-core and agent-input tests if they exist, and
  `process-change-hub.test.ts`. Then run `tsc`. Expected: all pass.

- [ ] **Step 5: Docs and commit.**
  - In both AGENTS.md files, document the choke point: every read that can reach a response
    goes through `processReads`; the guard test enforces it; a new read path either uses it
    or is allowlisted with a reason.
  - Commit with the message: `refactor(optio-api): one choke point for process reads (processReads + guard test)`.

### Task 4: the streams (`stream-poller.ts`, `process-change-relevance.ts`)

**Files:**
- Modify: `packages/optio-api/src/process-change-relevance.ts`
- Modify: `packages/optio-api/src/stream-poller.ts`
- Test: `packages/optio-api/src/__tests__/process-change-relevance.test.ts`
- Create: `packages/optio-api/src/__tests__/stream-poller-visibility.test.ts` (copy the setup and
  `waitUntil` from `stream-poller-scope.test.ts`; its own `DB_NAME`)
- Docs: the "Stream Poller" sections of `packages/optio-api/AGENTS.md` and the root `AGENTS.md`
  (the option lists)

**Interfaces:**
- Consumes: `pruneOrphans` (Task 1); `processReads` (Task 3b). The pollers never call
  `andVisibility` themselves.
- Produces:
  - `stream-poller.ts` is removed from the guard test's `PENDING` list. After this task it
    passes the guard's rules.
  - `treeRelevant(c, roots, members, opts?: { metadataChanges?: boolean })`. With
    `metadataChanges`, an `update` whose `fields` include `metadata` is relevant even for a
    non-member.
  - `StreamPollerOptions`, and through it `TreePollerOptions`, gains
    `visibility?: ProcessMetadataFilter | null`.
  - `MultiTreePollerOptions` and `SessionEventsPollerOptions` gain
    `visibility?: ProcessMetadataFilter | null`.

- [ ] **Step 1: Write the failing tests.**
  - **`process-change-relevance.test.ts`.** A metadata update of a non-member is relevant with
    `{ metadataChanges: true }`, and irrelevant without it, as today. A status update of a
    non-member stays irrelevant with the flag.
  - **`stream-poller-visibility.test.ts`**, with predicate `eq('ownerId', 'u1')`:
    - **List poller:** emits only visible processes.
    - **Session-events poller:** emits only visible processes' events.
    - **Tree poller (`rootId` = root, `baseDepth` 0)**, on the fixture root (u1), child (u2),
      grandchild (u1):
      - the first update holds only the root;
      - the log events carry only the root's lines;
      - after `updateOne` sets the child's `metadata.ownerId` to `'u1'`, an update containing
        the child and the grandchild arrives (the relevance fix);
      - after the root's `ownerId` is set to `'u2'`, an update with `processes: []` arrives.
    - **Multi-tree poller:** a tree root and a flat id, one hidden. Only the visible one's
      processes are emitted.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/vis-tests/t4-red.log`.

- [ ] **Step 3: Implement.**
  - **Every poller** builds `const reads = processReads(db, prefix, visibility ?? null)` at
    creation and does all its reads through it. The list and session-events filters stay
    `andScope(...)`.
  - **Tree poller and multi-tree poller:**
    - every read goes through `reads`;
    - when `visibility` is set, the fetched docs go through `pruneOrphans` before the snapshot,
      the update and the log scan. Multi-tree passes `keep` = the flat ids that were fetched;
    - `members` is computed from the pruned set;
    - the rule becomes `treeRelevant(c, roots, members, { metadataChanges: visibility != null })`.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/vis-tests/t4-green.log`, together
  with `stream-poller.test.ts` and `stream-poller-scope.test.ts`. Then run `tsc`. Expected:
  all pass.

- [ ] **Step 5: Docs and commit.** Document the new poller options and the mid-stream behaviour
  (spec, "Streams") in both AGENTS.md files. Commit with the message:
  `feat(optio-api): streams honour visibility (filtered, pruned, re-read on metadata changes)`.

### Task 5: the adapters

**Files:**
- Modify: `packages/optio-api/src/adapters/fastify.ts`, `express.ts`, `nextjs-app.ts`,
  `nextjs-pages.ts`
- Test: `packages/optio-api/src/__tests__/fastify-access.test.ts`,
  `src/__tests__/adapters-access.test.ts`, `src/adapters/__tests__/fastify-multi-stream.test.ts`
- Docs: the `OptioApiOptions` sections of `packages/optio-api/AGENTS.md` and the root
  `AGENTS.md`; the access example in `packages/optio-api/README.md` (around line 214)

**Interfaces:**
- Consumes: `VisibilityCallback`, `hasAccessHooks`, `findReadableProcess` (Task 2);
  `processReads` and `ProcessReads` (Task 3b); the poller `visibility` options (Task 4).
- Produces:
  - Each adapter's options gain `visibility?: VisibilityCallback<Req>`, documented "Hide
    processes from a request (see auth.ts)".
  - The adapters are removed from the guard test's `PENDING` list, which ends empty. Remove the
    constant and its exemption.

- [ ] **Step 1: Write the failing tests.** Extend the `hooks()` of `fastify-access.test.ts` and
  `adapters-access.test.ts` with
  `visibility: (req) => (customerOf(req) === 0 ? null : or(not(eq('kind', 'data-chat')), eq('ownerId', String(header(req, 'x-user') ?? ''))))`.
  Add a hidden chat (`{ customerId: 1, kind: 'data-chat', ownerId: 'other' }`) to the fixtures.
  Per adapter:
  - the list omits the hidden chat;
  - `GET /api/processes/<id>` gives 404 for it;
  - the tree-stream open gives 404;
  - `POST /api/processes/<id>/cancel` gives 404, and the engine's `cancel` is not called;
  - a visibility hook returning `{ AND: [] }` makes `GET /api/processes` fail:
    - **fastify, express:** status 500;
    - **nextjs-app, nextjs-pages:** first check what the adapter does today when a `scope` hook
      throws, then assert the same for the invalid predicate: either the handler call rejects
      (Next.js then answers 500) or it answers 500.
  - **fastify:** the widget proxy, widget-control and widget-upload give 404 for the hidden
    chat.
  - **`fastify-multi-stream.test.ts`:** a hidden id appears in the `resolution` event's
    `missing` list.
  
  All existing tests keep passing unchanged. Their processes carry no `kind`, so they stay
  visible.

- [ ] **Step 2: Run the tests and see them fail**, into `/tmp/vis-tests/t5-red.log`.

- [ ] **Step 3: Implement, in each adapter:**
  - Add the `visibility` option and put it in the `hooks` object.
  - **Reads:** every process read goes through
    `const reads = processReads(db, prefix, access.visibility)`. Gates get `reads`.
  - **Tree-stream open and multi-tree `resolveOne`:**
    - fastify and express use `findReadableProcess(reads, id, access)`;
    - nextjs-app and nextjs-pages keep their `_id`-only lookup, as `reads.findOne({ _id })`,
      followed by `inScope(reads, found, access.scope)`.
  - Pass `visibility: access.visibility` to every poller they create: list, tree, multi-tree,
    session-events.
  - **nextjs-app and nextjs-pages:** `currentAccess()` uses `hasAccessHooks(hooks)`.

- [ ] **Step 4: Run the tests and see them pass**, into `/tmp/vis-tests/t5-green.log`, together
  with the adapter suites (`express.test.ts`, `nextjs-app.test.ts`, `nextjs-pages.test.ts`,
  `fastify.test.ts`). Then run `tsc`. Expected: all pass.

- [ ] **Step 5: Docs and commit.**
  - Add `visibility` to the options listings in both AGENTS.md files.
  - Add `visibility` to the README's example, built with the re-exported builders.
  - Commit with the message: `feat(optio-api): all four adapters accept the visibility hook`.

### Task 6: the final documentation pass and verification

**Files:**
- Modify: `packages/optio-api/AGENTS.md`, the root `AGENTS.md`, `packages/optio-api/README.md`,
  as needed

- [ ] **Step 1: Check the documentation against the spec.** In both AGENTS.md files, the
  "Access scope and authorization" section must state the spec's "Where it applies" rules:
  - the list is exact;
  - single reads, tree entry points and actions give 404;
  - trees are pruned of orphans, with the divergent-metadata edge case;
  - the streams, including mid-stream transitions;
  - what is unaffected (resync, instances, no-hook hosts);
  - that the hook is declared once, at init, and per-group rules are clauses of the one
    predicate, with the spec's per-`kind` example (spec, "Semantics");
  - the choke point (spec, "One choke point").
  
  Fill any gap, and remove any remaining mention of the `'read'` action:
  `command grep -rn "'read'" packages/optio-api AGENTS.md`.
- [ ] **Step 2: Run the verification.**
  - **Tests:** every test file touched in Tasks 1–5, plus `handlers.test.ts`,
    `stream-poller.test.ts` and the four adapter suites, with full logs into
    `/tmp/vis-tests/final.log` (with junit: `--reporter=junit --outputFile.junit=/tmp/vis-tests/final.xml`).
  - **Types and lint:** `./node_modules/.bin/tsc --noEmit -p .` and
    `./node_modules/.bin/eslint src --max-warnings=0`.
  
  Expected: every test passes (279 baseline tests plus the new ones), with no type or lint
  errors.
- [ ] **Step 3: Commit** any doc fixes, with the message:
  `docs(optio-api): visibility hook reference (where it applies, edge cases)`.
