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
- Consumes: `andVisibility`, `pruneOrphans` (Task 1).
- Produces:
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
  - **List poller:** its filter becomes `andVisibility(andScope(...), visibility ?? null)`.
  - **Session-events poller:** the same change to its filter.
  - **Tree poller and multi-tree poller:**
    - every read's query is wrapped with `andVisibility(…, visibility ?? null)`;
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
  `isVisible` (Task 1); the poller `visibility` options (Task 4).
- Produces: each adapter's options gain `visibility?: VisibilityCallback<Req>`, documented
  "Hide processes from a request (see auth.ts)".

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
  - **Tree-stream open and multi-tree `resolveOne`:**
    - fastify and express use `findReadableProcess(col, id, access)`;
    - nextjs-app and nextjs-pages keep their `findOne({ _id })` and add `&& await isVisible(col, found, access.visibility)`.
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
  - what is unaffected (resync, instances, no-hook hosts).
  
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
