# Task results, launch stamps and factory concurrency

Date: 2026-10-25. Status: approved design (the owner, in the Praeceptor printing brainstorm).
Branch: `csillag/factory-results`; merged into `main` and pushed when ready (the owner).

## Why

Praeceptor brings its printing back as an optio task factory, PrintMaster: the browser
launches a print, follows it, and opens the PDF it made. Three things optio does not have
today stand in the way, and each is generic:

1. A finished task cannot hand a file back to the frontend. `ctx.publish_result` is held in
   memory for in-engine callers only; `ctx.store_blob` writes GridFS, but no optio-api route
   serves a blob; widget data and client messages carry JSON, not a multi-megabyte PDF.
2. A launch carries no trusted identity of who made it. Its metadata is the factory's plus
   `taskFactory`; `sessionId` is minted by the browser. So `visibility` and `authorize` cannot
   tell one user's launches from another's, and the task cannot see who launched it.
3. A factory's launches all start at once: each is its own `asyncio.create_task`
   (`lifecycle.py` `_launch_record`). `ctx.parallel_group(max_concurrency=…)` limits the
   children a running parent spawns; a factory's record never runs, so nothing counts its
   launches.

The three features below close these gaps. None is Praeceptor-specific.

## 1. A task's result file

**The task's side (optio-core).** A new context method, beside `store_blob`:

```python
async with ctx.store_result(filename="Feladat.pdf", content_type="application/pdf") as writer:
    await writer.write(chunk)
```

- It writes one GridFS file (the default bucket, as `store_blob`), tagged
  `metadata = {processId, prefix, name: filename, kind: "result"}`, where `processId` is the
  record's `_id` as hex, as `store_blob` tags it.
- On a clean exit of the block, the process record gets
  `result: {filename, contentType, size}` (size in bytes). The GridFS id is kept on the record
  for optio's own use (`result.fileId`) and is not part of the API's response.
- One result per run: a second `store_result` in the same run raises `RuntimeError`, as a
  second `publish_result` does. An exception inside the block leaves no result (the partial
  file is deleted).
- The record's reset (`store.relaunch_reset`, run by a relaunch and by a dismiss) deletes the
  previous run's result file and removes `result`.

**Serving it (optio-api).** `GET /processes/:id/result`:

- 200 with the file's bytes, streamed from GridFS: `Content-Type` its `contentType`,
  `Content-Length` its size, `Content-Disposition: inline` with the file name (an ASCII
  fallback `filename=` and RFC 5987 `filename*=UTF-8''…`).
- 404 `{reason: 'no-result'}` for a process that has no result; 404 `{reason: 'not-found'}`
  for one that does not exist or the request may not see (`visibility`), as
  `GET /processes/:id/conversation` answers (the plain read routes' 404 has no reason).
- Access: a read, so `viewer` suffices; it goes through `gateProcess` with a new action,
  `'result'` (added to `OptioAction`), so `scope`, `visibility` and `authorize` apply as for
  the conversation route.
- All four adapters serve it (Fastify, Express, Next app router, Next pages router): ts-rest
  leaves a contract route without an implementation unregistered, so each adapter wires it,
  the Next ones as a raw match before ts-rest as their SSE routes are. The handler layer reads
  GridFS through injected dependencies (as the upload's `upload-forward.ts`); adapters do no
  database work (optio-api's layer rules).
- optio-contracts: the route in the frontend contract; `result` (optional
  `{filename, contentType, size}`) on the process schema. The streams copy their fields from
  fixed lists (`stream-poller.ts`'s list and tree snapshots and updates, `LIST_PROJECTION`), so
  `result` is added to each, and to optio-core's engine wire keys (`_PROCESS_WIRE_KEYS`).
  `result.fileId` is stripped from every response (`stripServerSideFields`, `buildTree`, the
  streams), as `widgetUpstream` is.

**Cleanup.** A result file lives exactly as long as its process record:

- The paths where optio deletes one record (and its descendants) delete their result files
  too: `store.delete_process` (an ephemeral run's deletion at its end, `adhoc_delete`, the
  dismiss of an ended factory launch) and `store.delete_ephemeral_processes` at `init()`.
- Records removed in bulk (`purge_processes`, `remove_stale_processes`, `resync(clean=True)`)
  and by MongoDB's TTL monitor (`expireAt`, which runs no optio code) leave their files to the
  sweep: the engine deletes result files whose process record is gone (GridFS files with
  `metadata.kind = "result"` and this prefix, whose `metadata.processId` is no record's `_id`)
  at `init()`, and then every 10 minutes from `run()`'s loops, beside the supervisor.

## 2. A launch stamp

**optio-api.** A new optional hook beside `authenticate`, `scope`, `visibility` and
`authorize`:

```ts
launchMetadata?: (req, input: { process: ProcessRef; params?: Record<string, unknown> })
  => Record<string, string | number | boolean | null> | null | Promise<…>
```

- Called for every launch of a task factory, after `authorize` allowed it. Its map goes with
  the launch to the engine (`metadata` in the engine contract's launch params; the generated
  Python model is regenerated, `make codegen`). With no access hooks, `gateProcess` does no
  lookup, so the launch handler reads the target itself when `launchMetadata` is set, to know
  whether it is a factory (`taskFactory` on its record; `ProcessRef` gains `taskFactory`).
  The hook is not one of the access hooks: it does not make access restricted.
- The launch request's body cannot carry metadata: only server-side code stamps it, so the
  stamp is trusted.
- For a launch that is not a factory's (a plain process re-run), the hook is not called: such
  a launch makes no new record to stamp.

**optio-core.** `Optio.launch(…, metadata=…)`, `launch_and_wait` and `launch_and_await_result`
(and the engine RPC's launch) take the stamp, and `_factory_child` merges it into the new
child's metadata (after `child_task` composed the factory's). A stamp key that collides with
the factory's own metadata or with the reserved `taskFactory` key refuses the launch:
`invalid-metadata`, with the colliding keys in the message. It is a new `LaunchFailureReason`
(optio-contracts' enum, optio-api's status and message tables, whose message pass-through it
shares with `invalid-parameters`, and the `errors.processes.launch.invalid-metadata` string of
optio-ui and of apps that translate optio's errors). The task reads the stamp in
`ctx.metadata`, which holds its record's metadata already.

**What it enables.** An app's `visibility` and `authorize` can match the stamp (Praeceptor:
`{user: <id>}`; a user sees and acts on only their own launches), and the task knows who
launched it.

## 3. A factory's concurrency limit

**optio-core.** `TaskFactory(max_concurrency: int | None = None)`:

- `None` (the default): as today, every launch starts at once.
- `N` (an integer ≥ 1; `check_factory` refuses anything else): at most N launches of that
  factory run at once in this engine. A launch beyond the limit is written `scheduled` (with a
  log line saying it waits for a free slot and how many run) and starts when a running launch
  of the same factory reaches a terminal state, oldest first.
- It holds for every way a launch of the factory starts (`launch`, `launch_and_wait`,
  `launch_and_await_result`, the resume of a kept launch), so it lives in the executor, where
  they all start a run.
- A waiting launch can be cancelled: it ends `cancelled` without running, and frees nothing; an
  ephemeral one is then deleted, as at the end of a run. Its start is conditional: a launch
  that is no longer `scheduled` when its slot comes (cancelled meanwhile) does not run.
- An engine restart needs nothing new: reconciliation already sets a `scheduled` record to
  `failed` ("interrupted by server restart") and deletes ephemeral ones.
- The limit is per engine process (in memory), as `parallel_group`'s; it is not shared between
  engines.
- Its time limits (an app's own deadline inside `execute`) count from when a launch starts
  running, since `execute` is only called then.

## Out of scope

- More than one result file per run; results of non-terminal runs served while running (the
  route serves whatever the record has; apps fetch after `done`).
- optio-ui changes: the frontend fetches the result route itself; `ProcessDetailView` already
  shows a launch's state, progress and message.
- A cluster-wide factory limit.

## Tests

- optio-core: `store_result` (record field, one per run, exception leaves none, relaunch
  deletes, every deletion path deletes the file, the orphan sweep); the stamp (merged into the
  child, collisions refused, absent when no hook); `max_concurrency` (N run, the rest wait in
  `scheduled` and start in order, cancel of a waiting one, `check_factory` refusals).
- optio-api: the result route (200 with headers and bytes, `no-result`, visibility 404,
  `authorize` sees `'result'`); `launchMetadata` (called for factory launches only, its map
  reaches the engine, not callable from the body).
- optio-contracts: the route and the schema field.
