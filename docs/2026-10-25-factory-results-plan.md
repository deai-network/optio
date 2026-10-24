# Task results, launch stamps and factory concurrency: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** optio gains a task's result file served by optio-api, a server-side launch stamp, and
a task factory concurrency limit.

**Architecture:** the contracts change first (process schema, the result route, the launch's
`metadata`, `invalid-metadata`) with the generated Python models; then optio-core's three
features; then optio-api's result route and `launchMetadata` hook in all four adapters.

**Tech Stack:** Python 3.13 (optio-core: motor, GridFS), TypeScript (optio-contracts zod +
ts-rest, optio-api Fastify/Express/Next, vitest), clamator codegen.

**Spec:** `docs/2026-10-25-factory-results-design.md` (this repo). Consumer: Praeceptor's
`docs/superpowers/specs/2026-10-25-printing-design.md` on its branch `printing`.

## Global Constraints

- Branch `csillag/factory-results`, worktree `/home/csillag/praeceptor-worktrees/printing/optio`.
  Never touch `/home/csillag/praeceptor-dev` or another session's checkout.
- No `Co-Authored-By` or other self-credit line in commits (AGENTS.md:25). Commit subjects
  `type(scope): summary`. Commit each task by explicit path.
- Update the AGENTS.md / README sections a change affects in the same commit (AGENTS.md:31-36).
- No wall-clock-dependent tests: poll with a hang ceiling (`_wait_until`, test_task_factory.py
  38-47).
- Shell: one plain command per call; no `&&`, `;`, `|`, `cd`, `$(...)`, variables, globs,
  heredocs, `python -c`. Use absolute paths, `git -C`, `pnpm -C`, `make -C`.
- Tests, one file at a time (the host is shared):
  - optio-core: `/home/csillag/praeceptor-worktrees/printing/praeceptor/.venv/bin/pytest /home/csillag/praeceptor-worktrees/printing/optio/packages/optio-core/tests/<file>.py`
    (live MongoDB on 127.0.0.1:27017; superego has no Redis: do not run tests that need
    `redis_url`).
  - optio-contracts: `pnpm -C /home/csillag/praeceptor-worktrees/printing/optio/packages/optio-contracts build`, then
    `pnpm -C /home/csillag/praeceptor-worktrees/printing/optio/packages/optio-contracts exec vitest run src/__tests__/<file>`.
  - optio-api (rebuild contracts first): `pnpm -C /home/csillag/praeceptor-worktrees/printing/optio/packages/optio-api exec vitest run src/<path>`.
- Never `pnpm install` inside the optio worktree (its `node_modules` belong to Praeceptor's
  workspace).
- Names fixed here: `store_result`, `result` (`{filename, contentType, size}`; `fileId` only in
  MongoDB), `OptioAction` `'result'`, reasons `no-result` and `invalid-metadata`, hook
  `launchMetadata`, `TaskFactory.max_concurrency`, GridFS `metadata.kind = "result"`.

## Review Focus

1. A waiting launch cancelled just as its slot frees: it never runs, stays `cancelled` (an
   ephemeral one deleted), and the slot goes to the next waiter. (Task 5.)
2. `result.fileId` reaching a client through any path: REST get/list/commands, the list and
   tree streams, the engine's command replies. (Tasks 2 and 6.)
3. A record removed by the TTL monitor or a bulk delete: its result file swept, never served.
   (Task 3.)
4. A plain (non-factory) launch with `launchMetadata` configured: the hook is not called, no
   `metadata` is sent. (Task 7.)
5. A result file name with non-ASCII, quotes, `;` or a newline: `Content-Disposition` stays a
   single valid header (ASCII fallback with those replaced, RFC 5987 `filename*`). (Task 6.)

---

### Task 1: The contracts and the generated models

**Files:**
- Modify: `packages/optio-contracts/src/schemas/process.ts` (ProcessSchema 45-107: optional
  `result: z.object({filename: z.string(), contentType: z.string(), size: z.number().int().nonnegative()}).optional()`)
- Modify: `packages/optio-contracts/src/api-to-frontend.ts` (a `getResult` route after `get`,
  83-93: `GET /processes/:id/result`, `pathParams`, `InstanceQuerySchema`, responses
  `200: c.otherResponse({contentType: 'application/octet-stream', body: z.unknown()})` and
  `404: z.object({reason: z.enum(['not-found', 'no-result']), message: z.string()})`)
- Modify: `packages/optio-contracts/src/optio-engine-to-api.ts` (launch params 62-78: optional
  `metadata: z.record(z.union([z.string(), z.number(), z.boolean(), z.null()]))`)
- Modify: `packages/optio-contracts/src/engine-failure-reasons.ts` (10-22: `'invalid-metadata'`)
- Regenerate: `packages/optio-core/src/optio_core/_generated/optio_engine.py` (`make -C /home/csillag/praeceptor-worktrees/printing/optio codegen`)
- Test: `packages/optio-contracts/src/__tests__/process-schema.test.ts`, `optio-engine-contract.test.ts`
- Docs: `packages/optio-contracts/AGENTS.md` (116-140, 195-227, 232-249, 252-262), root `AGENTS.md` (876-906, 932-970)

**Interfaces:**
- Produces: `Process.result?: {filename, contentType, size}`; route `processes.getResult`;
  `LaunchParams.metadata?`; `LaunchFailureReason` includes `'invalid-metadata'`; the generated
  Python `Process`/`Process2` carry `result`, `LaunchParams` carries `metadata`.

- [ ] **Step 1: Write the failing tests**: in process-schema.test.ts, `it('a process may carry its result')`
  parsing a record with `result: {filename: 'a.pdf', contentType: 'application/pdf', size: 3}`
  and rejecting `size: -1`; in optio-engine-contract.test.ts, extend the exact reason list
  (39-44) with `'invalid-metadata'` and `it('launch params may carry metadata')`.
- [ ] **Step 2: Run them: FAIL.**
- [ ] **Step 3: Implement the four contract changes.**
- [ ] **Step 4: Build contracts, run both test files: PASS.**
- [ ] **Step 5: Regenerate the Python models** (`make codegen`; the pre-commit hook fails on
  drift under `_generated/`). Check the diff touches only `result`, `metadata` and the reason.
- [ ] **Step 6: Docs, then commit** (`feat(contracts): process result, the result route, launch metadata, invalid-metadata`).

### Task 2: optio-core: a task's result file

**Files:**
- Modify: `packages/optio-core/src/optio_core/context.py` (beside `store_blob`, 479-504)
- Modify: `packages/optio-core/src/optio_core/store.py` (`relaunch_reset` 561-591,
  `_reset_fields` 531-544, `delete_process` 484-492, `delete_descendants` 467-481,
  `delete_ephemeral_processes` 495-507)
- Modify: `packages/optio-core/src/optio_core/_engine_service.py` (`_PROCESS_WIRE_KEYS` 31-38:
  `result`, without `fileId`)
- Test: create `packages/optio-core/tests/test_context_result.py`; extend `tests/test_store.py`
- Docs: `packages/optio-core/AGENTS.md` (219-236, 265-284, 720-738, 787-802), root `AGENTS.md`
  (559-569, 677-732), `packages/optio-core/README.md` (210-303, 724-764)

**Interfaces:**
- Produces: `ProcessContext.store_result(self, filename: str, content_type: str)`, an
  `@asynccontextmanager` yielding `_GridInWrapper`; `store.delete_result_files(db, prefix,
  process_oids: Sequence[ObjectId]) -> int`; record field `result = {fileId, filename,
  contentType, size}`.

- [ ] **Step 1: Write the failing tests** in test_context_result.py (build the context as
  test_context_blob.py `_make_ctx` 18-31 does):
  - `test_store_result_sets_the_records_result`: write `b"%PDF"`; the record's `result` is
    `{fileId: <ObjectId>, filename: "a.pdf", contentType: "application/pdf", size: 4}`; the
    GridFS file's metadata is `{processId: str(oid), prefix, name: "a.pdf", kind: "result"}`.
  - `test_a_second_store_result_raises`: `RuntimeError`, the first result kept.
  - `test_an_exception_inside_leaves_no_result`: no `result` field, no GridFS file left.
  - `test_reset_deletes_the_result`: after `store_result`, `relaunch_reset` removes `result`
    and the file.
  - `test_deleting_a_record_deletes_its_result_files`: `delete_process` on a parent whose child
    has a result deletes both files; `delete_ephemeral_processes` likewise.
- [ ] **Step 2: Run: FAIL.**
- [ ] **Step 3: Implement.** The record write follows `mark_ephemeral` (context 275-282). The
  once-per-run guard keys on the run (`self._run_token`), as `publish_result`'s. A failed
  block deletes the partial file (`delete_blob`). The deletion functions collect the deleted
  records' `_id`s before deleting, then call `delete_result_files`.
- [ ] **Step 4: Run test_context_result.py, test_store.py, test_engine_service.py: PASS**
  (the wire-key test 543-556 checks `result` against the generated model).
- [ ] **Step 5: Docs, then commit** (`feat(optio-core): a task's result file`).

### Task 3: optio-core: the orphan sweep

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (`init()` after
  `_delete_ephemeral_leftovers` 286; `run()` beside `_supervisor_loop` 1536; `shutdown` stops it
  as it stops the supervisor 1673-1680)
- Modify: `packages/optio-core/src/optio_core/store.py`
- Test: create `packages/optio-core/tests/test_result_sweep.py`
- Docs: `packages/optio-core/AGENTS.md` (720-738)

**Interfaces:**
- Produces: `store.sweep_orphan_results(db, prefix) -> int` (deletes `kind: "result"` files of
  this prefix whose `metadata.processId` is no record's `_id`); `RESULT_SWEEP_SECONDS = 600`.

- [ ] **Step 1: Write the failing tests**: `test_sweep_deletes_results_of_missing_records`
  (one file whose record exists stays, one whose record was deleted with a raw `delete_one`
  goes, a `store_blob` file without `kind` stays, another prefix's stays);
  `test_init_sweeps` (a leftover orphan is gone after `Optio.init`).
- [ ] **Step 2: Run: FAIL.**
- [ ] **Step 3: Implement**: the sweep reads `fs.files` by `metadata.kind` and
  `metadata.prefix`, checks the ids against the processes collection in batches; the loop
  sleeps `RESULT_SWEEP_SECONDS` between sweeps and logs, never raises.
- [ ] **Step 4: Run test_result_sweep.py: PASS.**
- [ ] **Step 5: Docs, then commit** (`feat(optio-core): sweep result files whose record is gone`).

### Task 4: optio-core: the launch stamp

**Files:**
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (`launch` 508-562,
  `_factory_child` 631-665, `launch_and_wait` 681-, `launch_and_await_result` 732-745)
- Modify: `packages/optio-core/src/optio_core/_engine_service.py` (`launch` 92-104 forwards
  `params.metadata`)
- Modify: `packages/optio-core/src/optio_core/models.py` (`LaunchOutcome.reason` 21-25:
  `"invalid-metadata"`)
- Test: extend `packages/optio-core/tests/test_task_factory.py`, `tests/test_engine_service.py`
  (the exact kwargs at 115-117, 135-137, 148-150, 308-310 gain `metadata`)
- Docs: `packages/optio-core/AGENTS.md` (288-317, 423-445, 504-509), root `AGENTS.md` (161-178)

**Interfaces:**
- Consumes: Task 1's `LaunchParams.metadata`.
- Produces: `Optio.launch(process_id, resume=False, *, session_id, params=None,
  ephemeral=None, metadata: Mapping[str, str | int | float | bool | None] | None = None)`, the
  same keyword on `launch_and_wait` and `launch_and_await_result`; a factory child's metadata
  `{**factory metadata, **stamp, "taskFactory": factory id}`; refusal `invalid-metadata` naming
  the colliding keys.

- [ ] **Step 1: Write the failing tests** in test_task_factory.py:
  `test_a_launch_stamp_reaches_the_childs_metadata` (`ctx.metadata["user"] == "u1"` inside the
  task); `test_a_stamp_colliding_with_the_factorys_metadata_is_refused` (reason
  `invalid-metadata`, message names `kind`; also for `taskFactory`); `test_a_plain_launch_ignores_metadata`
  (a non-factory record's metadata unchanged). In test_engine_service.py,
  `test_launch_forwards_metadata`.
- [ ] **Step 2: Run both files: FAIL.**
- [ ] **Step 3: Implement**: the merge happens in `_factory_child`, after `child_task`;
  the collision check runs before `_adhoc_define` (so a refused launch defines nothing).
- [ ] **Step 4: Run both files: PASS.**
- [ ] **Step 5: Docs, then commit** (`feat(optio-core): a launch stamp on factory launches`).

### Task 5: optio-core: `TaskFactory.max_concurrency`

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py` (`TaskFactory` 152-224: field
  `max_concurrency: int | None = None`, last)
- Modify: `packages/optio-core/src/optio_core/task_factory.py` (`check_factory` 73-105)
- Modify: `packages/optio-core/src/optio_core/executor.py` (`launch_process` 177-218,
  `_execute_process` 220-452: the `running` write 269-274 becomes conditional on `scheduled`)
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (`cancel`'s scheduled arm
  1019-1035 wakes a waiting launch and deletes an ephemeral one)
- Test: extend `packages/optio-core/tests/test_task_factory.py` (the field list 97-104,
  `test_check_factory_raises_the_syncs_refusals` 619-640)
- Docs: `packages/optio-core/AGENTS.md` (326-345, 385-421), root `AGENTS.md` (301-403),
  `packages/optio-core/README.md` (549-583, 670-695)

**Interfaces:**
- Produces: `TaskFactory.max_concurrency`; the executor admits at most N running launches per
  factory (`metadata.taskFactory` names it), the rest wait FIFO in `scheduled` with one log
  line `Waiting for a free slot (N running)`.

- [ ] **Step 1: Write the failing tests**:
  - `test_max_concurrency_runs_n_and_queues_the_rest`: limit 1, three launches whose tasks
    wait on an `asyncio.Event`; one `running`, two `scheduled`; set the first's event: the
    second runs, then the third; order of starts = order of launches.
  - `test_a_waiting_launch_cancelled_never_runs`: limit 1, cancel the second while the first
    runs: `cancelled`, its execute never called; the third runs after the first.
  - `test_a_cancelled_waiting_ephemeral_launch_is_deleted`.
  - `test_launch_and_wait_counts_against_the_limit`.
  - `test_no_limit_starts_every_launch` (`max_concurrency=None`, two launches both `running`).
  - `check_factory` refuses `0`, `-1`, `1.5`, `True` (bool) for `max_concurrency`.
- [ ] **Step 2: Run test_task_factory.py: FAIL.**
- [ ] **Step 3: Implement**: per-factory slot state in the executor (limit, running count,
  FIFO of waiters); a run releases its slot in the `finally` of its own task (covers every
  end, force-cancel included); a waiter woken by cancel exits without the `running` write;
  the `running` write is a conditional update on `status.state == "scheduled"`, and a run whose
  update matched nothing releases its slot and ends.
- [ ] **Step 4: Run test_task_factory.py, test_parallel.py, test_task_ttl.py: PASS.**
- [ ] **Step 5: Docs, then commit** (`feat(optio-core): TaskFactory.max_concurrency`).

### Task 6: optio-api: the result route

**Files:**
- Create: `packages/optio-api/src/result-download.ts` (the handler's GridFS dependency, as
  `upload-forward.ts` 23-28)
- Modify: `packages/optio-api/src/handlers.ts` (`getProcessResult` beside
  `getProcessConversation` 249-268; `stripServerSideFields` 22-27 and `buildTree` 116-124 strip
  `result.fileId`)
- Modify: `packages/optio-api/src/auth.ts` (`OptioAction` 35-38: `'result'`)
- Modify: `packages/optio-api/src/stream-poller.ts` (115-134, 138-162, 172-189, 192-221),
  `packages/optio-api/src/process-change-relevance.ts` (`LIST_PROJECTION` 14-21): `result`
  without `fileId`
- Modify: the four adapters: `adapters/fastify.ts`, `adapters/express.ts`,
  `adapters/nextjs-app.ts`, `adapters/nextjs-pages.ts` (the Next ones as a raw match before
  ts-rest)
- Test: create `packages/optio-api/src/__tests__/handlers-result.test.ts`; extend
  `src/__tests__/handlers.test.ts` (62-116), `src/adapters/__tests__/fastify-access.test.ts`,
  `src/adapters/__tests__/adapters-access.test.ts` (113-162, a binary-reading driver)
- Docs: `packages/optio-api/AGENTS.md` (145-177, 240-250, 444-459, 474-602, 794-830),
  `packages/optio-api/README.md` (149-167), root `AGENTS.md` (1146-1152, 1211-1249)

**Interfaces:**
- Consumes: Task 1's route; Task 2's record field and file metadata.
- Produces: `getProcessResult(ctx, query, id, access, deps: ResultDownloadDeps) ->
  {status: 200, headers, body: Readable} | {status: 404, body: {reason: 'not-found' | 'no-result', message}}`;
  `contentDisposition(filename: string): string`.

- [ ] **Step 1: Write the failing tests**:
  - handlers-result.test.ts (real MongoDB + GridFS): 200 with the bytes, `Content-Type`,
    `Content-Length`, `Content-Disposition: inline; filename="a.pdf"; filename*=UTF-8''a.pdf`;
    `no-result`; `not-found` for an unknown id and for one hidden by `visibility`; `authorize`
    called with `action: 'result'`.
  - `contentDisposition('Feladat "1"; ő\n.pdf')` gives one line whose ASCII `filename` has
    `"`, `;`, the newline and `ő` replaced by `_`, and a `filename*` that percent-encodes them.
  - handlers.test.ts: get, list and command replies carry `result` without `fileId`; the list
    and tree streams' snapshot and update carry it without `fileId`.
  - fastify-access.test.ts and adapters-access.test.ts: each adapter serves the route (bytes
    equal; 404 bodies).
- [ ] **Step 2: Rebuild contracts; run the four files: FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them: PASS;** also `src/__tests__/process-reads-guard.test.ts`.
- [ ] **Step 5: Docs, then commit** (`feat(optio-api): GET /processes/:id/result`).

### Task 7: optio-api: `launchMetadata`

**Files:**
- Modify: `packages/optio-api/src/auth.ts` (`ProcessRef` 41-48: `taskFactory?: boolean`; the
  hook's type `LaunchMetadataCallback<TRequest>`)
- Modify: `packages/optio-api/src/access-scope.ts` (`toProcessRef` 111-120 sets `taskFactory`)
- Modify: `packages/optio-api/src/handlers.ts` (`launchProcess` 356-374; `LAUNCH_STATUS`
  301-310 and `MESSAGES` 322-337 gain `invalid-metadata` 400; `launchFail` 341-346 passes its
  message through as for `invalid-parameters`)
- Modify: the four adapters' options and launch calls (fastify 436-446/533-538, express
  33-42/168-173, nextjs-app 48-57/134-139, nextjs-pages 48-57/122-127)
- Modify: `packages/optio-ui/src/` i18n resources: `errors.processes.launch.invalid-metadata`
  in every language optio-ui ships (find the existing `not-a-factory` key)
- Test: extend `src/__tests__/handlers-access.test.ts` (157-199), `src/__tests__/handlers.test.ts`
  (118-276), `src/adapters/__tests__/fastify-access.test.ts`
- Docs: `packages/optio-api/AGENTS.md` (89-110, 296-305, 517-526, 585-602),
  `packages/optio-api/README.md` (234-271), root `AGENTS.md` (1094-1184, 1233-1239, 1895)

**Interfaces:**
- Consumes: Task 1's `LaunchParams.metadata`, Task 4's engine side.
- Produces: `launchMetadata?: (req, input: {process: ProcessRef; params?: Record<string, unknown>}) => Record<string, string | number | boolean | null> | null | Promise<…>`
  on every adapter's options; `launchProcess(…, stamp?: (process: ProcessRef) => Promise<Record<…> | null>)`.

- [ ] **Step 1: Write the failing tests**:
  - the hook's map reaches `engine.launch` as `metadata` for a factory record (with and
    without access hooks: unrestricted access looks the target up itself);
  - a plain record: the hook is never called, no `metadata` key sent;
  - a launch body carrying `metadata` sends none (zod strips it);
  - `authorize` refusing: the hook is not called;
  - `invalid-metadata` from the engine: 400 with the engine's message.
- [ ] **Step 2: Run: FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the three files: PASS.**
- [ ] **Step 5: Docs, then commit** (`feat(optio-api): launchMetadata, the server-side launch stamp`).

### Task 8: Verification and merge

- [ ] **Step 1:** In a throwaway clone of the branch under
  `/home/csillag/praeceptor-worktrees/printing/scratch/optio-check` (its own `pnpm install`
  with optio's lockfile and its own venv per optio's Makefile), run optio's checks: contracts,
  optio-api and optio-core test suites (the Redis-free part on superego; the rest is CI's),
  `make codegen` with no drift. Remove the clone after.
- [ ] **Step 2:** Final whole-branch review on the strongest model.
- [ ] **Step 3:** When Praeceptor's integration works on it (its plan's live check), rebase on
  `origin/main`, merge into `main` (fast-forward), push `main` (the owner: "merge to main and
  push when ready"). Then tell the optio sessions in `topics.log`.
