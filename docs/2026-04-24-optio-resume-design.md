# Optio Resume Feature — Design

**Base revision:** `a3c79b40b5404959d3b5aeb2367fe38b98104d34` on branch `main`, later updated to reflect `39d1692bebf021057fccde67af22ff1a58b53d23` (as of 2026-04-25T00:49:28Z)

## Summary

Add a platform-wide "resume" capability to Optio: any task executor can declare that it supports resuming, persist per-process state at terminal, and on next launch pick up where the prior run left off. `optio-opencode` is the first consumer, using this mechanism to preserve opencode session history and workdir contents across relaunches.

The feature threads a generic resume signal through every layer (task-definition → UI → API → Redis → executor) without committing the platform to any particular notion of "state." Executors decide what to persist and how; optio-core provides two generic affordances — a `hasSavedState` flag on the process document and GridFS-backed blob storage — and otherwise stays out of the way.

## Motivation

Today, each run of a task starts fresh. For `optio-opencode` in particular, this means every relaunch discards the LLM conversation history and the workdir — a substantial regression from using `opencode` directly, which preserves both automatically via its local SQLite DB. Users doing iterative development with an LLM lose all context on each relaunch, forcing them to re-explain what they're doing.

A resume capability lets users continue a conversation across sessions, machines, and even after the worker restarts. The same mechanism is usable by other executors that have meaningful per-run state — training jobs with checkpoints, long-running crawls with progress markers, etc. — though MVP scopes actual implementation to `optio-opencode`.

## Scope

**In scope:**
- Generic `supports_resume` flag on `TaskInstance`, plumbed through the API/UI/Redis/executor contract.
- Generic `hasSavedState` flag on the process document, written only by optio-core via new `ProcessContext` methods.
- Generic GridFS-backed blob storage helpers on `ProcessContext`, scoped per process.
- UI split-button affordance (Resume / Restart) on processes that support resume and have saved state.
- `optio-opencode`-specific implementation: per-task SQLite DB, `opencode export`/`import`, workdir tar.gz persistence, MongoDB snapshot collection with last-5 retention.
- Migration for pre-existing process documents.

**Out of scope:**
- Child opencode sessions across resume (`opencode export <id>` exports a single session; UI-spawned children are not preserved).
- Workdir portability across machines (the tar.gz is portable; the on-disk `workdir/` reused across runs on the *same* machine is not synchronized cross-host).
- Automatic cleanup of per-task directories on the filesystem.
- Resume from older snapshots (only "latest" is selectable; older snapshots exist for audit/debug only).
- Transactional guarantees between snapshot doc and GridFS blob (accepted best-effort on single-node Mongo).

## Architecture Overview

Two orthogonal layers:

**Layer A — platform-generic (optio-core, optio-api, optio-ui, optio-contracts):**
A task can declare `supports_resume=True` in its `TaskInstance`. The UI exposes a split button on processes whose definition declares this AND whose process document shows `hasSavedState=true`. The button's primary action is Resume; the dropdown item is Restart. Both dispatch to the existing `/api/processes/:id/launch` endpoint, differing only in a new `resume: boolean` request body field. The flag rides the Redis launch payload into optio-core, which surfaces it to the task's `execute()` as `ctx.resume: bool`. Two new `ProcessContext` methods (`mark_has_saved_state` / `clear_has_saved_state`) let the executor report whether durable state exists; they are the only writers of the `hasSavedState` field. Generic GridFS helpers (`store_blob` / `load_blob` / `delete_blob`) give executors a place to put large blobs scoped to their process.

**Layer B — optio-opencode executor implementation:**
Sets `supports_resume=True` on its `TaskInstance`. On launch, wipes the per-task SQLite DB; if `ctx.resume=True` and a snapshot exists in Mongo, fetches it, restores workdir from GridFS, imports session into SQLite via `opencode import`, and launches opencode against it. On every terminal state, exports the session via `opencode export`, tars+gzips the workdir, stores both as blobs in GridFS, writes a snapshot metadata doc, prunes to last 5, wipes the on-disk workdir, and calls `mark_has_saved_state()`.

## Contract changes (wire-level)

**`TaskInstance`** (`optio-core/src/optio_core/models.py`):

```python
@dataclass
class TaskInstance:
    # ... existing fields ...
    supports_resume: bool = False
```

**Process document + ts-rest `ProcessSchema`** (`optio-contracts/src/contract.ts`):

Two new fields on the process document and on the REST schema:
- `supportsResume: boolean` — refreshed on every sync via `$set`.
- `hasSavedState: boolean` — default `false` on `$setOnInsert`, mutated only by optio-core in response to `ctx.mark_has_saved_state()` / `ctx.clear_has_saved_state()`.

Both are exposed on the existing `GET /api/processes/:id` response and on the SSE process-update stream (which already emits the full process document, so no stream-shape change).

**Launch endpoint** (`POST /api/processes/:id/launch`):

Request body (previously empty) gains:

```json
{ "resume": true }
```

`resume` is optional; missing or `false` means fresh start. Response shape unchanged.

**Handler validation** (`optio-api/src/handlers.ts`):
- `resume=true` AND process doc shows `supportsResume=false` → reject with HTTP 400.
- `resume=true` AND `supportsResume=true` AND `hasSavedState=false` → accept and forward; the executor handles the "resume requested but nothing to resume" fallback.

**Redis launch payload** (published by `optio-api/src/publisher.ts` on `{db}/{prefix}:commands`):

```json
{ "processId": "...", "resume": true }
```

Added only to the `launch` command; `cancel`/`dismiss`/`resync` payloads unaffected. Missing `resume` is treated as `false` for forward compatibility with in-flight messages from older publishers.

## optio-core changes

### `models.py`
Add `supports_resume: bool = False` to `TaskInstance`.

### `store.py` — `upsert_process`
- Add `supportsResume: task.supports_resume` to the `$set` block (always refreshed on sync).
- Add `hasSavedState: False` to the `$setOnInsert` block (initialized once on first insert, preserved thereafter).
- Do NOT add either field to `clear_result_fields` — re-launching a terminal process must not wipe resume state.

### `context.py` — `ProcessContext`
Three additions:

```python
# Read, set once at dispatch
resume: bool  # attribute, like process_id / params

# Write, async methods, idempotent (no-op when value unchanged)
async def mark_has_saved_state(self) -> None: ...
async def clear_has_saved_state(self) -> None: ...
```

Behavior of `mark_has_saved_state` / `clear_has_saved_state`:
- Reads the process document's `supportsResume` field.
- If `False`: emit `logger.warning(...)`, do nothing else (no DB write, no exception).
- If `True`: `update_one({_id: ...}, {$set: {hasSavedState: <bool>}})` only if the value differs from current.

Blob-storage additions (thin GridFS wrappers, scoped via metadata):

```python
async def store_blob(self, name: str) -> AsyncContextManager[StreamWriter]:
    """Open a GridFS upload stream tagged with processId + prefix.
    Returns the file_id on context exit."""

async def load_blob(self, file_id: ObjectId) -> AsyncContextManager[StreamReader]:
    """Open a GridFS download stream."""

async def delete_blob(self, file_id: ObjectId) -> None:
    """Delete a GridFS file."""
```

Implementation uses `motor.motor_asyncio.AsyncIOMotorGridFSBucket` scoped to `db`. GridFS file metadata records `{processId, prefix, name}` so operators can audit/clean; `name` is a free-form label for debugging only.

### `consumer.py` / `lifecycle.py` / `executor.py`
- Consumer decodes `resume` from the launch payload (default `false` if absent).
- `_handle_launch` / `Executor.launch_process` forward `resume` through to `_execute_process`, which stores it on the constructed `ProcessContext`.

### `migrations/m003_backfill_has_saved_state.py` (new)

```python
from optio_core.migrations import fw_migrations


@fw_migrations.register(
    "backfill_has_saved_state",
    depends_on=["backfill_child_metadata"],
)
async def backfill_has_saved_state(db):
    """Default hasSavedState to False on pre-existing process docs.

    supportsResume is handled by upsert_process ($set on sync), but
    hasSavedState lives in $setOnInsert so pre-existing docs never receive
    it. Backfill once via the migration system.
    """
    collection_names = await db.list_collection_names()
    process_collections = [n for n in collection_names if n.endswith("_processes")]
    for coll_name in process_collections:
        await db[coll_name].update_many(
            {"hasSavedState": {"$exists": False}},
            {"$set": {"hasSavedState": False}},
        )
```

Register in `migrations/__init__.py` alongside the existing imports.

## UI changes

### New component: `LaunchControls` (`packages/optio-ui/src/components/LaunchControls.tsx`)

Encapsulates the resume/restart decision so `ProcessList` and `ProcessDetailView` share the same rendering logic.

Rendering rules (top-down):
1. State not in `LAUNCHABLE_STATES` (`idle`, `done`, `failed`, `cancelled`) → render nothing.
2. `supportsResume === false` OR `hasSavedState === false` → render single `PlayCircleOutlined` button. Click launches with no body (current behavior).
3. `supportsResume === true` AND `hasSavedState === true` → render Ant Design `Dropdown.Button`:
   - Primary click: launch with `{ resume: true }`. Icon `PlayCircleOutlined`, tooltip "Resume".
   - Dropdown menu item: icon `ReloadOutlined`, label "Restart (discard saved state)", click launches with `{ resume: false }`.

Defensive defaults: treat missing `supportsResume` / `hasSavedState` on the process document as `false`.

### `ProcessList` (`components/ProcessList.tsx`)

Replace the inline `PlayCircleOutlined` rendering at lines 28-41 of `ProcessItem` with `<LaunchControls size="small" process={...} onLaunch={...} />`.

### `ProcessDetailView` (`components/ProcessDetailView.tsx`)

Add a top-right header action area that renders `<LaunchControls size="middle" process={tree} onLaunch={...} />` whenever `tree` is loaded. This gives users a launch affordance from the detail view without navigating back to the list.

### `ProcessTreeView`

No change. Will be redesigned separately.

### `useProcessActions` hook

Change signature from `launch(processId)` to `launch(processId, opts?: { resume?: boolean })`. When `opts?.resume === true`, request body is `{ resume: true }`; otherwise empty body. `LaunchControls` always calls with an explicit `{ resume: boolean }`.

## optio-opencode executor changes

### Per-task filesystem layout

A stable per-task directory (`<task_dir>`) lives on the host where opencode runs. It is identified by `process_id` alone — host-independent — so the same task always lands in the same path on a given host.

```
<task_dir>/
  workdir/        # opencode's cwd; wiped at terminal, repopulated from
                  # GridFS on resume; not authoritative between runs
  opencode.db     # per-task SQLite; passed to opencode via OPENCODE_DB.
                  # Always deleted at start of run; Mongo blob is authoritative
  snapshot.json   # transient scratch used by opencode_import/export helpers
```

**Resolution:**

- Local (worker filesystem): `${OPTIO_OPENCODE_TASK_ROOT:-/tmp/optio-opencode}/<process_id>/`
- Remote (SSH target):       `${OPTIO_OPENCODE_REMOTE_TASK_ROOT:-/tmp/optio-opencode}/<process_id>/`

`process_id` is validated against `^[A-Za-z0-9._-]+$` to prevent path traversal; non-conforming ids raise `ValueError` before any filesystem operation.

**Persistence is not required between runs.** workdir is wiped at terminal (server-side hygiene), `opencode.db` is always deleted at start of the next run, and the resume flow always reconstructs both from GridFS — there is nothing on disk that resume reads. `/tmp` is the default because it "just works"; the env-var override exists for capacity reasons (operators expecting very large workdirs may want a dedicated mount), not durability.

### Host abstraction additions

The five operations resume needs on the host are added to the existing `Host` protocol so `session.py` stays host-agnostic. Both `LocalHost` (subprocess + os.* + tarfile) and `RemoteHost` (asyncssh exec + SFTP + remote `tar`/`opencode`) implement them.

```python
async def launch_opencode(
    self,
    password: str,
    ready_timeout_s: float,
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> LaunchedProcess:
    """As before, plus an optional `env` mapping injected into opencode's
    environment. LocalHost: merged into the subprocess env. RemoteHost:
    prepended as `KEY1=value1 KEY2=value2 …` to the SSH-exec'd command line."""

async def opencode_import(
    self, opencode_db_path: str, session_json: bytes,
) -> None:
    """Materialize `session_json` as a file on the host (LocalHost: write to
    disk; RemoteHost: SFTP to a path under `<task_dir>`) and run
    `opencode import <file>` against `opencode_db_path`. Raises on non-zero
    exit. Cleans up the temporary file on success and failure."""

async def opencode_export(
    self, opencode_db_path: str, session_id: str,
) -> bytes:
    """Run `opencode export <session-id>` against `opencode_db_path` on the
    host and return stdout as bytes. Raises on non-zero exit."""

async def archive_workdir(
    self, exclude: list[str] | None,
) -> AsyncIterator[bytes]:
    """Yield gzipped tar chunks of the workdir contents.

    LocalHost: in-process tarfile streamed in 1 MiB chunks.
    RemoteHost: `tar czf - --exclude=PAT … .` over SSH; stdout streamed
    back as it arrives (no full-buffer in worker memory).

    `exclude`:
      * None       → use DEFAULT_WORKDIR_EXCLUDES (.git, node_modules,
                     __pycache__, .venv, *.pyc, .DS_Store)
      * []         → no excludes; capture everything verbatim
      * non-empty  → use verbatim (no merge with defaults)"""

async def restore_workdir(
    self, stream: AsyncIterator[bytes],
) -> None:
    """Empty the workdir, then ingest the gzipped tar stream into it.

    LocalHost: rmtree(workdir); tarfile.open(..., mode="r:gz").extractall().
    RemoteHost: `rm -rf workdir/*` over SSH; pipe stream bytes into
    `tar xzf -` on stdin."""
```

The existing methods (`connect`, `setup_workdir`, `write_text`, `ensure_opencode_installed`, `establish_tunnel`, `tail_log`, `fetch_deliverable_text`, `terminate_opencode`, `cleanup_workdir`, `disconnect`) are unchanged.

### TaskInstance declaration

`create_opencode_task()` sets `supports_resume=True` on the returned `TaskInstance` unconditionally. (If a future consumer needs opt-out, expose `OpencodeTaskConfig.supports_resume` — not needed for MVP.)

### `OpencodeTaskConfig` additions

```python
@dataclass
class OpencodeTaskConfig:
    # ... existing fields ...
    workdir_exclude: list[str] | None = None
```

- `None` (default) → use the built-in default excludes: `.git`, `node_modules`, `__pycache__`, `.venv`, `*.pyc`, `.DS_Store`.
- `[]` (empty list) → no excludes; the entire workdir is captured as-is.
- Non-empty list → use the supplied patterns verbatim (no merge with defaults).

### Mongo collection: `{prefix}_opencode_session_snapshots`

```
{
  _id:             ObjectId,
  processId:       str,
  capturedAt:      datetime,
  endState:        str,          # "done" | "failed" | "cancelled"
  sessionId:       str,          # opencode session id (preserved across export→import)
  sessionBlobId:   ObjectId,     # GridFS file id for the session JSON
  workdirBlobId:   ObjectId,     # GridFS file id for the workdir tar.gz
  deliverablesEmitted: list,     # audit metadata only; not replayed
}
```

Compound index: `{ processId: 1, capturedAt: -1 }`.

### Launch flow

1. Compute `task_dir`; ensure it exists. Ensure `workdir/` exists (mkdir -p; contents are not authoritative).
2. Always delete any existing `opencode.db` at the start of the run. The Mongo blob is authoritative; any disk residue is discarded.
3. Branch on `ctx.resume`:
   - **`resume=True` AND latest snapshot exists in Mongo:**
     1. Fetch latest snapshot (`findOne({processId}, sort {capturedAt: -1})`).
     2. `await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))` — empties `workdir/` on the host and repopulates it from the GridFS tar.
     3. Read `snapshot["sessionBlobId"]` from GridFS into memory; `await host.opencode_import(opencode_db_path, session_json_bytes)`.
     4. `await host.launch_opencode(password, READY_TIMEOUT_S, env={"OPENCODE_DB": opencode_db_path})`.
     5. Publish the preserved `sessionId` in `widgetData` (via the existing widget machinery), so the iframe URL selects the resumed session rather than creating a new one.
     6. **Skip** rewriting `AGENTS.md` / `opencode.json` — they are already in the restored workdir from the tar, and rewriting would clobber any in-flight edits the LLM made.
   - **`resume=False` OR no snapshot exists:**
     1. `await ctx.clear_has_saved_state()` (belt-and-braces).
     2. `host.write_text("AGENTS.md", compose_agents_md(consumer_instructions))` and `host.write_text("opencode.json", json.dumps(opencode_config))`.
     3. `await host.launch_opencode(password, READY_TIMEOUT_S, env={"OPENCODE_DB": opencode_db_path})`.
     4. Pre-create session via REST (existing flow).

### Terminal flow (on `done`, `failed`, `cancelled`)

1. Graceful opencode shutdown (existing teardown).
2. `session_json = await host.opencode_export(opencode_db_path, session_id)` — captures the session as JSON bytes.
3. Open `ctx.store_blob("workdir")` for upload; iterate `host.archive_workdir(config.workdir_exclude)` and pipe each chunk into the writer → `workdir_blob_id`.
4. Open `ctx.store_blob("session")` for upload; write `session_json` → `session_blob_id`.
5. Insert snapshot metadata doc (processId, capturedAt, endState, sessionId, sessionBlobId, workdirBlobId, deliverablesEmitted).
6. Prune: find all snapshot docs for this processId ordered by `capturedAt` desc; for everything beyond index 5, delete the doc AND call `ctx.delete_blob()` on both `sessionBlobId` and `workdirBlobId`.
7. `await host.cleanup_workdir(aggressive=cancelled)` — server-side wipe; unconditional regardless of capture success above.
8. `await ctx.mark_has_saved_state()`.

**On any failure during steps 2–5:** log the error, do not touch `hasSavedState` (previous value stands), continue with teardown (including step 7 — workdir wipe is unconditional). The task's end state is unaffected.

### Deliverables

Prior-run deliverables are not replayed on resume. The executor only forwards deliverables emitted during the current run to the `on_deliverable` callback. Snapshot metadata field `deliverablesEmitted` records what was emitted (for audit) but is never used to re-invoke the callback.

### Error modes

| Condition | Behavior |
|---|---|
| `host.opencode_import` fails on resume (non-zero exit, SSH stream drop, etc.) | Log; delete `opencode.db`; fall back to fresh-start path (`clear_has_saved_state`, write `AGENTS.md`/`opencode.json`). Mongo blob preserved for inspection. |
| `host.opencode_export` fails at terminal | Log; skip snapshot insert and `mark_has_saved_state`; continue with workdir wipe. |
| `host.archive_workdir` interrupted, or GridFS upload fails | Log; skip snapshot insert and `mark_has_saved_state`; continue with workdir wipe. Any partially-uploaded blobs are best-effort deleted (ignore errors). |
| Mongo snapshot insert fails after successful uploads | Log; explicitly delete the uploaded blobs to avoid orphans; do not mark flag. |
| Stale flag (hasSavedState=true but Mongo has no snapshot) | Resume lookup returns none → fall back to fresh-start path. Self-healing. |

## Testing

### Unit tests (Python)

- `ProcessContext.mark_has_saved_state()` / `clear_has_saved_state()`:
  - Mongo write happens when `supportsResume=true`.
  - Warning emitted and no write when `supportsResume=false`.
  - Idempotent: second call with the same value does not issue a redundant update.
- `ProcessContext.store_blob` / `load_blob` / `delete_blob`:
  - Round-trip correctness for small and large payloads (e.g., 100 MB).
  - Metadata includes processId + prefix.
  - Isolation: one process cannot delete another process's blobs via API (scoped listing at minimum; enforcement via ObjectId unguessable).
- `upsert_process`:
  - `supportsResume` refreshed via `$set` on re-sync.
  - `hasSavedState=false` set via `$setOnInsert` on first insert.
  - Existing `hasSavedState=true` preserved across re-sync (the key invariant).
  - `clear_result_fields` does not touch either new field.
- Migration `backfill_has_saved_state`:
  - Fills missing `hasSavedState` across all `*_processes` collections.
  - Does not overwrite existing values.
  - Idempotent on repeat runs.
- Snapshot retention pruning (optio-opencode):
  - After the 6th capture, oldest snapshot doc AND its two GridFS blobs are gone.
  - No orphaned blobs after pruning.

### Unit tests (TS)

- `LaunchControls.test.tsx`:
  - Non-launchable state → renders nothing.
  - `supportsResume=false` → single play button, clicks dispatch empty body.
  - `supportsResume=true, hasSavedState=false` → single play button, clicks dispatch empty body.
  - `supportsResume=true, hasSavedState=true` → split button; primary click dispatches `{resume: true}`; dropdown item dispatches `{resume: false}`.
- API handler tests:
  - `launch` with `resume=true` on `supportsResume=false` process → HTTP 400.
  - `launch` with `resume=true` on `supportsResume=true, hasSavedState=false` → HTTP 200, Redis payload includes `resume: true`.
  - `launch` with no body → HTTP 200, Redis payload has `resume: false` (or absent).

### Integration tests

- **optio-core end-to-end:** Declare a `supports_resume=true` task with a test `execute()` that calls `mark_has_saved_state()`. Launch with `resume=false`. Verify `hasSavedState=true` in Mongo. Relaunch with `resume=true`. Verify `ctx.resume=True` delivered to `execute()`.
- **optio-opencode full cycle against a fake opencode CLI:** A subprocess stub that accepts `import <file>` / `export <session-id>` / `web` and emits canned JSON. Test: run → capture → wipe workdir → relaunch with resume → verify DB + workdir restored, session ID preserved, no re-send of `consumer_instructions`.

### Fixtures

- Canned `opencode export` JSON — a minimal realistic session with 2–3 messages and parts.
- Canned tar.gz of a small workdir (~5 files, ~1 KB total).

## Migration

- Backfill handled by `m003_backfill_has_saved_state` (see optio-core section above). Runs through the existing `quaestor` migration framework on startup, gated by `depends_on=["backfill_child_metadata"]` so it runs after existing migrations.
- UI defensive defaults (missing `supportsResume`/`hasSavedState` treated as `false`) cover the brief window between a UI-only refresh and the migration completing.
- `supportsResume` fills in via `$set` on the first sync after deployment — no migration required.
- No migration for `{prefix}_opencode_session_snapshots` — created on first capture.
- No migration for GridFS collections — created on first blob upload.
- Interaction with `clear_result_fields` (`dismiss` + re-launch): does not touch `supportsResume` or `hasSavedState`. Both are preserved across dismiss.

## Open interaction notes (not requiring design decisions)

- **A process in a non-launchable state** (e.g., `running`) renders no `LaunchControls` at all, so there is no way to toggle resume mid-run. This matches the existing model: you cancel, then re-launch.
- **`supports_resume` flipping** (e.g., task author toggles it off in code): refreshed via `$set` on next sync. UI immediately stops showing the split button. Existing `hasSavedState=true` survives but is inert (cannot be resumed because UI won't offer Resume, and the API rejects `resume=true` on non-supporting tasks). If the author later flips support back on, the stale state is accessible again. Acceptable; no special handling.
- **Restart on a resumable task** (`resume=false` click): executor receives `ctx.resume=False` and executes its fresh-start path, which for optio-opencode includes `clear_has_saved_state()`. So Restart eventually flips the flag to false (after the executor wipes). Until then, the flag stays true — a transient inconsistency that is invisible to the UI because the process is in a non-launchable state during execution.
