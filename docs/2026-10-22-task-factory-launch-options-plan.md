# TaskFactory launch options: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a launch's `ephemeral` chosen in the form (and by programs), overrides of the launch form (hook, button, a map on the widgets), removing a kept launch, the detail view's last view of a deleted process, and the process filters' state in a pluggable store.

**Architecture:** optio-core takes `ephemeral` on `launch` and deletes an ended launch on dismiss; the wire carries `ephemeral` and `ttlSeconds`; optio-ui builds the form from merged vultus definitions, adds `useTaskFactory`/`TaskFactoryLaunchButton`/`launchOverrides`, a remove button, a module-level cache in `ProcessDetailView`, and keeps the filters in an `ArgStore` (vultus-core's `useStoredFields` gains `values`).

**Tech Stack:** Python 3.13 (optio-core, pytest, asyncio auto), TypeScript (zod, ts-rest, clamator codegen, React 19, antd 6, vultus-core/vultus-antd, quaesitor-textus, vitest).

**Spec:** `/home/csillag/praeceptor-dev/optio/docs/2026-10-22-task-factory-launch-options-design.md` (and the TaskFactory design it extends, `docs/2026-10-20-task-factory-design.md`).

## Global Constraints

- Repositories (the common development folder, siblings): optio `/home/csillag/praeceptor-dev/optio` on branch `csillag/launch-options`; unitas `/home/csillag/praeceptor-dev/unitas` on branch `csillag/stored-field-values` (Task 1 makes it from `main`). Never `pnpm install` inside a sibling: their packages' `node_modules` belong to Praeceptor's install.
- optio's and unitas's commits: **no `Co-Authored-By` or other self-credit lines**. A package's `AGENTS.md`/README updated in the same commit as its public API change. **No test depends on wall-clock time** (wait on conditions with the existing helpers). optio-api production code never writes to MongoDB.
- Shell: one plain command per Bash call; absolute paths; no `cd`, `sed`, pipes, loops, shell variables, `$(…)`, heredocs or env-var assignments; edit files with the Read/Edit/Write tools only; scratch under `<repo>/.superpowers/`, never `/tmp`. Never download or run a binary that is not a distro package, PyPI or npm package (superego's rule).
- Commands:
  - optio-core tests: `/home/csillag/praeceptor-dev/optio/.venv/bin/pytest <abs test path> -q -p no:cacheprovider`. MongoDB answers at `localhost:27017` (a shared test container: never start, stop or reconfigure containers). There is no Redis on superego: three Redis-bound tests fail before any change (`test_listener_starts_before_rpc_server`, `test_heartbeat_key_set_during_run` and one more); they are not regressions.
  - TS tests, through Praeceptor's install: `corepack pnpm@12.9.1 --dir /home/csillag/praeceptor-dev/praeceptor --filter <package> exec vitest run [<file relative to the package>]` (`optio-ui`, `optio-api`, `optio-contracts`, `vultus-core`). For optio-api always add `--exclude '**/metadata-filter-mongo-integration.test.ts'` (it would download a MongoDB binary).
  - Type checks: `corepack pnpm@12.9.1 --dir /home/csillag/praeceptor-dev/praeceptor --filter <package> exec tsc --noEmit -p tsconfig.json`.
  - Codegen: `make -C /home/csillag/praeceptor-dev/optio codegen CLAMATOR=/home/csillag/praeceptor-dev/praeceptor/node_modules/.bin/clamator-codegen`; commit the regenerated `_generated/` files with their source.
  - optio's packages that ship `dist/` (contracts, api) are rebuilt for Praeceptor with `make -C /home/csillag/praeceptor-dev/praeceptor siblings`.
- Texts: optio-ui ships no translations; every new text is `t(key, 'English default')`, its key added to optio-ui's `AGENTS.md` key list.

## Review Focus

1. A hidden field's value is still sent: a form whose `dry_run` is hidden and preset launches with it (Task 4 test `a hidden field is not shown and its value is sent`).
2. An override for a field the form lacks, or one changing `kind`/`id`, fails loudly at render, not silently (Task 4 `mergeOverrides` tests).
3. An ephemeral launch's record never shows a remove button, a kept one only once ended (Task 5 test `the remove button only on an ended launch`).
4. A process opened in the detail view, then deleted, still shows its last log; one never shown says "not found" (Task 6 tests).
5. A filter value written by the URL (a store change from outside) shows in the field and the list at once, and a default never appears in the store (Task 7 test `a value written to the store from outside shows in the filters`).

---

### Task 1: vultus-core: `useStoredFields(...).values` (unitas)

**Files:**
- Modify: `/home/csillag/praeceptor-dev/unitas/packages/vultus-core/src/useStoredField.ts` (`StoredFields`, `useStoredFields`)
- Test: `/home/csillag/praeceptor-dev/unitas/packages/vultus-core/src/__tests__/useStoredField.test.tsx`
- Docs: `/home/csillag/praeceptor-dev/unitas/packages/vultus-core/README.md` (the stored fields' section)

**Interfaces:**
- Produces: `StoredFields.values: Readonly<Record<string, unknown>>` — each definition's id to its value as `useStoredField(definition).options.value` gives it (parsed, sanitized with `sanitizeStored`, the default where the key is absent), silently (no warning: the fields warn); an entry keeps its identity while it is `sameValue` to the previous one, and the object keeps its identity while no entry changed.

- [ ] **Step 1:** `git -C /home/csillag/praeceptor-dev/unitas switch -c csillag/stored-field-values`
- [ ] **Step 2: Failing tests** in `useStoredField.test.tsx`, `describe('useStoredFields values')`: with a `memoryStore({ level: 'b' })` and definitions `[{ kind: 'oneOf', id: 'level', choices: [{value:'a'},{value:'b'}] }, { kind: 'manyOf', id: 'tags', choices: [...] }]`: `values` is `{ level: 'b', tags: [] }`; after `store.write([{ key: 'tags', raw: <the manyOf raw of ['x']> }], 'replace')` it is `{ level: 'b', tags: ['x'] }` (write the raw through the field's own `onChange` in a `useStoredField` beside it, or `argOf(...).valueType` to format it); a stored `level` no longer among the choices reads as the default `'a'`; `values.tags` keeps its identity across a re-render that changed nothing, and `values` too.
- [ ] **Step 3:** Run `corepack pnpm@12.9.1 --dir /home/csillag/praeceptor-dev/praeceptor --filter vultus-core exec vitest run src/__tests__/useStoredField.test.tsx` — FAIL (`values` undefined).
- [ ] **Step 4:** Implement in `useStoredFields`, from the same snapshot (one subscription), reusing `argOf`/`sanitizeStored` per field and the per-field reading cache.
- [ ] **Step 5:** The test file passes; then the whole vultus-core suite (`... --filter vultus-core exec vitest run`) and its type check.
- [ ] **Step 6:** Commit (`feat(vultus-core): useStoredFields also returns the fields' values`), README included.

### Task 2: optio-core: a launch's `ephemeral`, the reserved id, dismissing an ended launch

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (`launch` 491-, `launch_and_wait` 634-, `launch_and_await_result` 664-, `_factory_child` 585-618, `dismiss` 1288-1309), `packages/optio-core/src/optio_core/task_factory.py` (`check_parameters`)
- Test: `packages/optio-core/tests/test_task_factory.py`
- Docs: `packages/optio-core/AGENTS.md` (and the root `AGENTS.md`'s optio-core reference), `packages/optio-core/README.md` where TaskFactory is described

**Interfaces:**
- Produces: `Optio.launch(process_id, resume=False, *, session_id, params=None, ephemeral: bool | None = None) -> LaunchOutcome` (same keyword on `launch_and_wait`, `launch_and_await_result`); `dismiss` of an ended factory launch deletes it with its descendants and answers `ok=True`.

- [ ] **Step 1: Failing tests** in `test_task_factory.py`:
  - `test_launch_ephemeral_default_follows_ttl`: a factory without `ttl_seconds` → the child's record has `ephemeral: True`; with `ttl_seconds=60` → `ephemeral: False`.
  - `test_launch_ephemeral_true_and_false_override_the_default`: `ephemeral=False` on a factory without TTL → `False`; `ephemeral=True` on one with a TTL → `True` (the record gone once its run ends).
  - `test_ephemeral_on_a_task_is_not_a_factory`: `launch(<a TaskInstance's id>, session_id=None, ephemeral=True)` → `ok=False, reason='not-a-factory'`.
  - `test_parameter_id_ephemeral_is_reserved`: `check_parameters([{ 'kind': 'boolean', 'id': 'ephemeral' }])` raises `ValueError` whose text contains `parameters[0]: id 'ephemeral' is reserved for the launch's own flag`.
  - `test_dismiss_of_an_ended_kept_launch_deletes_it`: a kept launch run to `done` → `dismiss` ok, the record and its children gone; `test_dismiss_of_a_running_launch_is_refused` → `not-dismissable`; `test_dismiss_of_a_task_still_resets_it` (an ordinary done task → idle, as before).
- [ ] **Step 2:** Run the file — FAIL.
- [ ] **Step 3:** Implement: `_factory_child(..., ephemeral)` uses `factory.ttl_seconds is None if ephemeral is None else ephemeral`; the not-a-factory branch refuses `ephemeral is not None` as it does `params`; the reserved-id check in `check_parameters`; in `dismiss`, an ended factory launch takes the deletion path an ephemeral launch's end takes (`delete_descendants` and the record's own deletion, the same helper), a running one stays refused.
- [ ] **Step 4:** The file passes; then the whole suite (`/home/csillag/praeceptor-dev/optio/.venv/bin/pytest /home/csillag/praeceptor-dev/optio/packages/optio-core/tests -q -p no:cacheprovider`): only the three Redis-bound failures.
- [ ] **Step 5:** Commit (`feat(optio-core): a launch's ephemeral; dismissing an ended launch deletes it`), docs included.

### Task 3: the wire: `ephemeral` and `ttlSeconds`

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts` (`ttlSeconds: z.number().nullable().optional()`), `packages/optio-contracts/src/optio-engine-to-api.ts` (launch params `ephemeral: z.boolean().optional()`), `packages/optio-contracts/src/api-to-frontend.ts` (launch body `ephemeral: z.boolean().optional()`); regenerate (codegen); `packages/optio-core/src/optio_core/_engine_service.py` (pass `ephemeral`; `ttlSeconds` in `_PROCESS_WIRE_KEYS`); `packages/optio-api/src/handlers.ts` (`launchProcess` passes `ephemeral`) and the adapters that call it (`adapters/fastify.ts` 533-538, the Express and Next.js ones); `packages/optio-api/src/process-change-relevance.ts` (`LIST_PROJECTION`), `packages/optio-api/src/stream-poller.ts` (the list snapshot and payload 115-159, `treeSnapshotOf` 170-186, `treeUpdateOf` 189-217: `ttlSeconds`)
- Test: `packages/optio-contracts/src/__tests__/process-schema.test.ts`, `optio-engine-contract.test.ts`; `packages/optio-core/tests/test_engine_service.py`; `packages/optio-api/src/__tests__/handlers.test.ts`, `stream-poller.test.ts`
- Docs: the packages' `AGENTS.md`/READMEs where the launch body and the process schema are listed

**Interfaces:**
- Consumes: Task 2's `launch(..., ephemeral=)`.
- Produces: `POST /processes/:id/launch` body `{ resume?, sessionId?, params?, ephemeral? }`; every process record on the wire and in the streams carries `ttlSeconds` (number or null).

- [ ] **Step 1: Failing tests:** the schema parses `ttlSeconds` (60 and null) and the launch params/body `ephemeral`; the engine service passes `ephemeral` to `Optio.launch` and returns `ttlSeconds` in a record; optio-api's handler passes `ephemeral` to `engine.launch`; the list and tree streams' snapshots and updates carry `ttlSeconds`.
- [ ] **Step 2:** Run them — FAIL.
- [ ] **Step 3:** Implement; then codegen (Global Constraints) and commit the regenerated files with the contracts in the same commit.
- [ ] **Step 4:** `optio-contracts` and `optio-api` suites pass (`... --filter optio-contracts exec vitest run`, `... --filter optio-api exec vitest run --exclude '**/metadata-filter-mongo-integration.test.ts'`), both type checks, the optio-core suite (Task 2's rule), and `make -C /home/csillag/praeceptor-dev/optio lint-no-direct-writes`.
- [ ] **Step 5:** `make -C /home/csillag/praeceptor-dev/praeceptor siblings` (rebuilds the contracts' and the API's `dist/`), then commit (`feat(wire): a launch's ephemeral; ttlSeconds on the wire`).

### Task 4: optio-ui: the form's `ephemeral` field and overrides

**Files:**
- Create: `packages/optio-ui/src/task-factory-overrides.ts` (`TaskFactoryOverrides`, `mergeOverrides`)
- Modify: `packages/optio-ui/src/task-factory.ts` (the built-in field; the TTL wording), `packages/optio-ui/src/components/TaskFactoryLaunchModal.tsx` (fields from merged definitions; `overrides?` prop; label/visibility/initial value resolved; hidden items; the refusal on a hidden field), `packages/optio-ui/src/hooks/useTaskFactoryLaunch.ts` (send `ephemeral` apart), `packages/optio-ui/src/hooks/useProcessActions.ts` (`LaunchOptions.ephemeral?`), `packages/optio-ui/src/index.ts`
- Test: `packages/optio-ui/src/__tests__/task-factory.test.ts`, `TaskFactoryLaunchModal.test.tsx`, `useProcessActions.test.tsx`
- Docs: `packages/optio-ui/AGENTS.md` (the keys, the API), README

**Interfaces:**
- Consumes: Task 3's body `ephemeral`, records' `ttlSeconds`.
- Produces:
  - `type TaskFactoryOverrides = Readonly<Record<string, Partial<FieldDefinition>>>`;
  - `mergeOverrides(definitions: readonly FieldDefinition[], overrides: TaskFactoryOverrides | undefined): FieldDefinition[]` (pure; throws `Error("Task factory overrides: no field '<id>' in the form")` and `Error("Task factory overrides: field '<id>' cannot change its kind or id")`);
  - `EPHEMERAL_FIELD_ID = 'ephemeral'`; `launchFormDefinitions(record: { parameters?, ttlSeconds? }, language, t): FieldDefinition[]` (the parameters' definitions, then the built-in checkbox with label, description and `initialValue: ttlSeconds == null`);
  - `TaskFactoryLaunchModalProps.overrides?: TaskFactoryOverrides`;
  - `useProcessActions().launch/launchAsync(id, { resume?, params?, ephemeral? })`.

- [ ] **Step 1: Failing tests:**
  - `mergeOverrides`: an override merges objects key by key (`{ choices: [...] }` replaces the array; `{ visible: false, initialValue: true }` merges); a function value replaces; an unknown id throws the message above; `kind`/`id` overrides throw.
  - `launchFormDefinitions`: the checkbox last, `initialValue` true without `ttlSeconds`, false with 3600; its description with a TTL says "1 hour" (`duration` = the largest of days/hours/minutes/seconds that divides it), without one the no-TTL text.
  - The modal: the checkbox renders (label `Remove when finished`); a launch sends `{ params: { …without ephemeral }, ephemeral: <checked> }`; with `overrides={{ dry_run: { visible: false, initialValue: false } }}` the `dry_run` item (label included) is not in the form and `params.dry_run === false` is sent (Review Focus 1, test `a hidden field is not shown and its value is sent`); an `invalid-parameters` refusal `dry_run: must be true or false` on that hidden field shows `<its label>: must be true or false` in the form.
- [ ] **Step 2:** Run them — FAIL.
- [ ] **Step 3:** Implement. The modal builds its items from `mergeOverrides(launchFormDefinitions(...), overrides)`, resolving `label`, `visible`/`hidden` and the initial value with vultus-core's `resolveIn`/`initialValueOf` and `useFieldContext()`; a not-visible item is antd's `<Form.Item hidden>` (kept in the form's values). The submit splits `ephemeral` from the params.
- [ ] **Step 4:** optio-ui's suite (`... --filter optio-ui exec vitest run`) and type check pass.
- [ ] **Step 5:** Commit (`feat(optio-ui): the launch form's ephemeral checkbox and overrides`).

### Task 5: optio-ui: `useTaskFactory`, `TaskFactoryLaunchButton`, `launchOverrides`, the remove button

**Files:**
- Create: `packages/optio-ui/src/hooks/useTaskFactory.tsx`, `packages/optio-ui/src/components/TaskFactoryLaunchButton.tsx`
- Modify: `packages/optio-ui/src/components/LaunchControls.tsx` (the play button on the hook; `launchOverrides`; the remove button), `ProcessItem.tsx`, `ProcessList.tsx`, `ProcessFilter.tsx` (`FilteredProcessList`), `ProcessTreeView.tsx`, `ProcessDetailView.tsx` (thread `launchOverrides` as `onLaunched`), `src/index.ts`
- Test: `packages/optio-ui/src/__tests__/useTaskFactory.test.tsx` (new), `LaunchControls.test.tsx`, `ProcessItem.test.tsx`, `ProcessDetailView.test.tsx`
- Docs: optio-ui `AGENTS.md`, README

**Interfaces:**
- Consumes: Task 4's modal (`overrides`), `TaskFactoryOverrides`.
- Produces:
  - `useTaskFactory(factoryId: string, opts?: { overrides?: TaskFactoryOverrides; onLaunched?: (child: Process) => void }): { launch: ActionStatus; isPending: boolean; dialog: ReactNode }` (the record via `useProcess(factoryId)`; `launch` disabled with a reason while loading, when not a factory, while pending);
  - `TaskFactoryLaunchButton(props: { factoryId: string; overrides?; onLaunched?; size? })`;
  - `launchOverrides?: Readonly<Record<string, TaskFactoryOverrides>>` on `ProcessItem`, `ProcessList`, `FilteredProcessList`, `ProcessTreeView`, `ProcessDetailView` (keyed by the factory's `processId`).

- [ ] **Step 1: Failing tests:** `useTaskFactory`: disabled with a reason while the record loads and for a record that is no factory; firing opens the dialog (rendered from `dialog`); a submit makes `isPending` true until the launch answers; `onLaunched` gets the child. `TaskFactoryLaunchButton` renders an `ActionButton` and the dialog. A `ProcessItem` given `launchOverrides={{ [factory.processId]: { dry_run: { visible: false } } }}` opens a form without `dry_run`. Review Focus 3 (`the remove button only on an ended launch`): an ended launch (`metadata.taskFactory` set, state `done`) shows `Remove`, which calls `dismiss`; a running launch, a factory and an ordinary done task show none.
- [ ] **Step 2:** Run them — FAIL.
- [ ] **Step 3:** Implement. The remove button is a vultus action (`useAction`, label `t('processes.removeLaunch', 'Remove')`, `fire` → `useProcessActions().dismiss`), an icon `ActionButton`, its refusal through `optioErrorRoutes`.
- [ ] **Step 4:** optio-ui's suite and type check pass.
- [ ] **Step 5:** Commit (`feat(optio-ui): useTaskFactory, TaskFactoryLaunchButton, launchOverrides, removing an ended launch`).

### Task 6: optio-ui: the detail view's last view of a deleted process

**Files:**
- Create: `packages/optio-ui/src/components/lastSeenProcesses.ts` (the module-level cache)
- Modify: `packages/optio-ui/src/components/ProcessDetailView.tsx`
- Test: `packages/optio-ui/src/__tests__/ProcessDetailView.test.tsx`

**Interfaces:**
- Produces: `rememberProcess(id: string, snapshot: { tree: unknown; logs: LogEntry[] }): void`, `lastSeenProcess(id: string): { tree; logs } | undefined`, `forgetAllProcesses(): void` (tests); capacity 5, least recently shown dropped.

- [ ] **Step 1: Failing tests:** a process shown (stream with a tree and a log line), then `processNotFound` → the notice `Process deleted.`, its tree read-only (no Cancel/Launch/Remove) and its log line; a process never shown, `processNotFound` → `Process not found.`; after six processes shown, the first is gone from the cache (not found); showing one again moves it to the front.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement: remember on every stream update with a tree; on `processNotFound`, the cached snapshot read-only under the notice, else the not-found text (both `t(...)` with the defaults).
- [ ] **Step 4:** optio-ui's suite passes.
- [ ] **Step 5:** Commit (`feat(optio-ui): ProcessDetailView shows a deleted process's last state`).

### Task 7: optio-ui: the process filters' state in a store

**Files:**
- Modify: `packages/optio-ui/src/components/ProcessFilter.tsx` (`WithFilteredProcessesProps.store?`; state from the store; stored fields; controlled search). Do not touch the vultus-core range: the sibling is still 0.5.5, and a range it does not meet would send pnpm to the registry; it is raised when vultus-core is released (spec 5).
- Test: `packages/optio-ui/src/__tests__/ProcessFilter.test.tsx`
- Docs: optio-ui `AGENTS.md`, README (Process filters)

**Interfaces:**
- Consumes: Task 1's `useStoredFields(...).values`.
- Produces: `WithFilteredProcessesProps.store?: ArgStore`; `useProcessFilter()` unchanged in shape.

- [ ] **Step 1: Failing tests:** with `store={memoryStore()}`: choosing the group `Errors` writes `filterGroup=errors`; typing `gam` writes `search=gam`; a store holding `module` (an app filter) and `showDetails=true` renders them chosen and `metadataFilter`/`filterFn` follow; Review Focus 5 (`a value written to the store from outside shows in the filters`): `store.write([{ key: 'filterGroup', raw: 'errors' }], 'replace')` after mount narrows the list; going back to `All` removes the key. Without a store, every existing test passes unchanged.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement: `WithFilteredProcesses` takes `store ?? useState(() => memoryStore())[0]`, provides it (`ArgStoreProvider`), reads every filter's value with `useStoredFields(<all definitions>).values`; `ProcessFilters` renders `<Field stored>`; `WithSearch` gets `query`/`onSetQuery` from the store (its key `search`).
- [ ] **Step 4:** optio-ui's suite and type check pass.
- [ ] **Step 5:** Commit (`feat(optio-ui): the process filters keep their state in a store (an app's, e.g. the URL)`).

### Task 8 (controller): finish

- [ ] The whole optio-core suite, optio-contracts, optio-api (with the exclusion), optio-ui, vultus-core; the type checks; `make -C /home/csillag/praeceptor-dev/praeceptor siblings`.
- [ ] Praeceptor's plan (`/home/csillag/praeceptor-dev/praeceptor/docs/superpowers/plans/2026-10-22-task-factory-overrides.md`) runs on top.
