# Conversation buffer out of the plaintext workdir snapshot

Design, agreed with the owner on 2026-10-11. This is step 1 of "read suspended agent tasks":
1. this leak fix;
2. the suspended read-only view, which will read the same record;
3. the session/workdir split for codex, grok, cursor and antigravity (an efficiency cleanup).

## Problem (security)

At a conversation-mode teardown, optio-claudecode writes the conversation listener's replay
buffer to `<workdir>/.optio-conversation-buffer.json` (`session.py`, before `conv_listener.stop()`).
The snapshot then stores the workdir as a **plaintext** GridFS blob. So the conversation's last
~1000 events end up unencrypted in MongoDB, even when the task encrypts its session blob
(`session_blob_encrypt`, e.g. Excavator's dataspace vault):
- user and assistant messages;
- tool calls and their results.

The split layout exists to keep exactly this kind of content out of the plaintext workdir.

Only claudecode writes such a file; the other engines rebuild their history from their own session
stores.

## Decisions (owner, 2026-10-11)

1. **The buffer stays resume state.** Resumes keep priming the listener from it, so a viewer sees the
   prior conversation. Only its storage moves, to an encrypted blob next to the snapshot.
2. **It is encrypted like the session blob:**
   - with the task's `session_blob_encrypt` / `session_blob_decrypt`;
   - plaintext only when the task configures no session encryption (as its session blob is).
3. **Existing snapshots are migrated: all retained snapshots of every claudecode process.** The
   buffer moves into the encrypted record, and the workdir blob is rewritten without it.
4. **Processes without a task definition** (no cipher available): the buffer is stripped and dropped.
5. **The migration is automatic at engine start, and blocking is acceptable.** It needs the task
   definitions (the cipher lives in them), so it runs right after the initial sync, as a *deferred
   migration*. A plain `fw_migrations` entry runs before the sync and gets only the database.

## Design

### 1. Storage

- **Content:** the buffer is the JSON the listener already exports (`ConversationListener.export_buffer()`:
  `[[seq, event], ...]`, no `stream_event`, no `x-optio-closed`).
- **Blob:** it is stored as its own GridFS blob (`ctx.store_blob`, name `conversation`), transformed
  with `session_blob_encrypt` when set.
- **Snapshot document:** `{prefix}_claudecode_session_snapshots` documents gain an optional
  `conversationBlobId`.
- **Pruning:** `prune_snapshots` returns it with the other blob ids, so the caller deletes it.
- **Documentation:** the module docstring layout and the `writing-agent-wrappers.md` notes on
  claudecode's history are updated.

### 2. Write path (teardown)

The listener runs in the engine process, so the export never touches the agent host.

- In the conversation teardown, the engine calls `conv_listener.export_buffer()` before `stop()` (as
  today) and keeps the JSON bytes in memory. The `host.write_text(_CONV_BUFFER_FILE, ...)` call is
  removed.
- `_capture_snapshot(..., conversation: bytes | None = None)`:
  - after the session blob, when `conversation` is given, it stores the conversation blob
    (encrypted) and passes `conversation_blob_id` to `_store_workdir_snapshot` / `insert_snapshot`;
  - the credentials guard still comes first, and a refused capture stores nothing.
- **Defense in depth:** the workdir tar's effective exclude list always contains
  `.optio-conversation-buffer.json`, also when a task supplies its own `workdir_exclude`. This covers
  workdirs restored from snapshots taken before the fix, and the rescue capture of a failed run.
- **The crash-rescue capture** (`end_state="rescued"`) has no live listener. It copies the latest
  snapshot's conversation record forward: it re-stores the bytes as a new blob, since pruning deletes
  per snapshot. A rescued session so keeps the history of its last suspend. Pre-fix, that history
  rode along in the restored workdir file.
- **A resumed run's teardown without an export** (the export raised, or the run failed after claude
  came up but before the listener started) copies the latest record forward the same way, so the
  new snapshot keeps the history the run resumed from.

### 3. Resume path

- `_load_conversation_buffer` reads the latest snapshot's `conversationBlobId` from GridFS instead of
  the restored workdir file, and decrypts it with `session_blob_decrypt`.
- It keeps today's parsing and filtering:
  - entries must be `[seq, dict]`;
  - `x-optio-closed` is dropped;
  - malformed or absent data means an empty buffer, never fatal.
- Its result still feeds the listener's `initial_events`.
- If a restored workdir still contains `.optio-conversation-buffer.json`, it is deleted after the
  restore (defense in depth; after the migration none should).

### 4. Cipher access for processes that are not running (optio-core)

- `TaskInstance` gains an optional `session_crypto: SessionCrypto | None` (a small dataclass holding
  `encrypt` and `decrypt`, each `Callable[[bytes], bytes] | None`).
- optio-claudecode's task factory fills it from the task config's `session_blob_encrypt` /
  `session_blob_decrypt`. Other engines may fill it later; step 2 needs it for the read path.
- The engine resolves a process's task as `resurrect` does, via the executor's task registry keyed
  by process id.

### 5. Deferred migrations (optio-core)

- **Registry:** `optio_core.deferred_migrations`, built on quaestor and parallel to `fw_migrations`.
  It has the same `register(name, depends_on=...)` decorator and run-once semantics.
- **What a function receives:** a `DeferredMigrationContext` with `db`, `prefix` and
  `task_for(process_id) -> TaskInstance | None`.
- **Tracking:** applied migrations are recorded in `{prefix}_fw_deferred_migrations`.
- **When it runs:** `Lifecycle.init` runs the registry right after the initial sync, before the
  scheduler starts and before the auto-resume sweep. Startup blocks until it finishes, so nothing can
  launch or resume a process while its snapshots are rewritten.
- **Failure:** a migration that raises fails the engine start, as a failing `fw_migrations` entry
  does, and is retried on the next start. Migrations must be idempotent per item.
- **Registration:** packages register at import time, as with `fw_migrations`. optio-claudecode
  registers its migration in its package `__init__`, so any app that builds claudecode tasks has it.

### 6. The migration: `claudecode_conversation_buffer_out_of_workdir`

It visits every document in `{prefix}_claudecode_session_snapshots` without the marker
`conversationBufferMigrated: true`, oldest first:

1. **Rewrite.** Stream the workdir blob (gzip tar) from GridFS into a new GridFS blob holding a gzip
   tar of the same members, except any member whose name is `.optio-conversation-buffer.json` (with or
   without a leading `./`). Keep that member's bytes aside. It streams with bounded memory; tar
   metadata (modes, mtimes, links) is preserved.
2. **If the buffer was present:**
   - the task is found and has a `session_crypto` → store the bytes as the `conversation` blob,
     transformed with its `session_crypto.encrypt` when set; only `SessionCrypto(encrypt=None)` (a
     task that genuinely has no session encryption) gives a plaintext record;
   - no task definition, or a task whose `session_crypto` is None (cipher unknown) → drop the bytes
     (decision 4).
3. **If the buffer was absent:** no new workdir blob is kept (the stream is discarded); only the
   marker is set.
4. **Update the document in one write:** the new `workdirBlobId` (when rewritten), `conversationBlobId`
   (when stored) and `conversationBufferMigrated: true`.
5. **Delete the old workdir blob** (its plaintext chunks go with it).

**Crash safety:** new blobs are written first, then the document is updated, then the old blob is
deleted. An interruption before the update leaves only orphan new blobs, which are harmless; the
document is redone on the next start. An interruption after it leaves the old blob orphaned. The
migration is recorded as applied only after every document is marked, so an interrupted run
continues next start.

Snapshot documents already carrying `conversationBlobId` (written by the new capture) are marked
without a rewrite.

### 7. Out of scope

- **MongoDB backups** taken before the migration still contain the plaintext. That is outside optio;
  the operator rotates them if needed.
- **`.optio-todo.json`:** the agent's checklist stays in the workdir; it is also mirrored in plaintext
  in `widgetData.todos`.
- **The suspended read-only view** (step 2) and the **split layout for the other engines** (step 3).

## Tests

- **Capture:**
  - with a buffer, it stores a `conversation` blob encrypted with the task's transform, and the
    snapshot references it;
  - the workdir tar never contains `.optio-conversation-buffer.json`, with default and with custom
    `workdir_exclude`;
  - a refused capture (credentials guard) stores no conversation blob;
  - teardown no longer writes the file to the host.
- **Resume:** the listener is primed from the decrypted blob; `x-optio-closed` is filtered; a missing
  or corrupt blob gives an empty buffer.
- **Rescue:** a rescue capture copies the previous record into a new blob.
- **Prune:** pruned snapshots' conversation blobs are deleted.
- **`TaskInstance.session_crypto`:** filled from the claudecode config.
- **Deferred migrations:**
  - run after the initial sync and before the scheduler;
  - recorded once; not re-run;
  - a raising migration fails start and is retried.
- **The migration**, on fixture snapshots:
  - with the buffer and a task with encryption → encrypted record, stripped workdir, old blob gone;
  - a task without encryption (`SessionCrypto(encrypt=None)`) → plaintext record;
  - no task, or a task with `session_crypto` None → stripped, no record;
  - without the buffer → marked only;
  - an interrupted run resumes;
  - member metadata is preserved.

## Rollout

The optio-claudecode and optio-core releases ship together. An app's first engine start after
upgrading runs the migration once per database and prefix, and blocks until it finishes; time is
dominated by re-gzipping each workdir blob.
