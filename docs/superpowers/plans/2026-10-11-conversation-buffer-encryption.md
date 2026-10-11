# Conversation buffer encryption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** claudecode's conversation replay buffer is no longer written into the plaintext workdir snapshot. It lives in its own GridFS blob, encrypted with the task's session cipher, and resumes prime from it. Existing snapshots are migrated at engine start.

**Architecture:**
- **optio-core:**
  - `TaskInstance.session_crypto`, which carries a task's session cipher to code that runs for processes that are not running;
  - a deferred-migration registry, which `Lifecycle.init` runs after the initial sync with a task lookup.
- **optio-claudecode:**
  - capture: write the buffer as a `conversation` blob, referenced from the snapshot document;
  - resume: read it back;
  - a deferred migration moves buffers out of existing workdir blobs.

**Tech Stack:** Python 3.13, motor/GridFS, quaestor, pytest (+pytest-asyncio), MongoDB 7.

**Spec:** `docs/2026-10-11-conversation-buffer-encryption-design.md`. Read it first; section numbers below are the spec's.

## Global Constraints

- **Worktree:** `/home/csillag/chat/wt-suspended/optio`, branch `csillag/suspended-view`. Never push. Never touch `~/deai`, `~/wt` or any excavator host path.
- **Python:** the venv is `/home/csillag/chat/wt-suspended/optio/.venv`. Run tests from the package dir:
  `cd packages/<pkg> && MONGO_URL=mongodb://localhost:27317 REDIS_URL=redis://localhost:6679 ../../.venv/bin/pytest <paths> -q`.
  These are private test containers `turks-optio-mongo` / `turks-optio-redis`. Do NOT use the `resurrect-*` containers or port 27017.
- **Tests:** measure each touched test file's baseline before changing code. Existing assertions stay unchanged unless the spec changes the behaviour they pin; say so in the report if one must change.
- **Commits:** optio has NO Co-Authored-By and NO self-credit lines (AGENTS.md). Prefixes `feat(optio-core): …`, `feat(optio-claudecode): …`, `docs: …`.
- **Names and values fixed by the spec:**
  - blob name `conversation`;
  - snapshot field `conversationBlobId`;
  - migration marker `conversationBufferMigrated: true`;
  - migration name `claudecode_conversation_buffer_out_of_workdir`;
  - tracking collection `{prefix}_fw_deferred_migrations` (quaestor prefix `f"{prefix}_fw_deferred"`);
  - buffer file name `.optio-conversation-buffer.json` (the existing `_CONV_BUFFER_FILE`).
- **Encryption:** use the task's `session_blob_encrypt` / `session_blob_decrypt`, the same pair as the session blob. None means plaintext.

## Review Focus

1. **A task with a custom `workdir_exclude`** (an explicit list, even `[]`) must still never put `.optio-conversation-buffer.json` in the workdir tar. Test in Task 2.
2. **An old snapshot restored before the migration** (or a failed run's workdir that still contains the file) must not leak the file into the next capture or the resurrect capture. Test in Tasks 2 and 3.
3. **A corrupt, undecryptable or missing conversation blob** on resume gives an empty buffer, never a failed launch. Test in Task 3.
4. **Migration interrupted mid-run** (the process killed after storing new blobs but before the document update, or after the update but before deleting the old blob): the next start completes without duplicating records or losing the buffer. Test in Task 4.
5. **tar members with the `./` prefix** (CLI `tar`) and without it (the Python `tarfile` fallback) are both recognised; other members' metadata (modes, mtimes, symlinks) is preserved. Test in Task 4.

---

### Task 1: optio-core, `session_crypto` and deferred migrations

**Files:**
- Modify: `packages/optio-core/src/optio_core/models.py` (`SessionCrypto`, `TaskInstance.session_crypto`)
- Create: `packages/optio-core/src/optio_core/deferred_migrations.py`
- Modify: `packages/optio-core/src/optio_core/lifecycle.py` (run after `await self._sync_definitions()` at the end of `init`, ~line 270)
- Modify: `packages/optio-core/src/optio_core/__init__.py` (export `SessionCrypto`, `deferred_migrations`, `DeferredMigrationContext`)
- Modify: `packages/optio-core/AGENTS.md` (a short "Deferred migrations" note next to the migrations section)
- Test: `packages/optio-core/tests/test_deferred_migrations.py`

**Interfaces:**
- Produces:
  - `SessionCrypto(encrypt: Callable[[bytes], bytes] | None = None, decrypt: Callable[[bytes], bytes] | None = None)`, a frozen dataclass in `optio_core.models`.
  - `TaskInstance.session_crypto: SessionCrypto | None = None`.
  - `DeferredMigrationContext(db: AsyncIOMotorDatabase, prefix: str, task_for: Callable[[str], TaskInstance | None])`, a dataclass.
  - `deferred_migrations: DeferredMigrationRegistry`, a module global, with:
    - `register(name: str, depends_on: list[str] | None = None)`, a decorator for `async def fn(ctx: DeferredMigrationContext) -> None`;
    - `async run(db, prefix: str, task_for) -> list[str]`, which wraps each fn as a quaestor `MigrationDefinition` whose execute is `lambda db: fn(ctx)`, and calls `quaestor.runner.run_migrations(db, f"{prefix}_fw_deferred", defs)`.
  - `Lifecycle.init` calls `await deferred_migrations.run(mongo_db, prefix, lambda pid: self._executor._task_registry.get(pid))` right after the initial `_sync_definitions()`.

- [ ] **Step 1: Write failing tests** in `tests/test_deferred_migrations.py`. Use a fresh registry instance per test (`DeferredMigrationRegistry()`), not the global, except for the init-integration test:
  - `test_runs_with_task_lookup`: a registered migration receives a ctx whose `task_for("p1")` returns the given TaskInstance and `task_for("nope")` returns None. After `run`, `{prefix}_fw_deferred_migrations` holds one doc with `name == "m"`.
  - `test_runs_once`: a second `run` does not call the fn again.
  - `test_raising_migration_is_retried`: the fn raises on the first run. `run` raises, nothing is recorded, and a second `run` with a working fn records it.
  - `test_init_runs_after_initial_sync`: register on the global registry (and unregister in teardown). A migration that records `ctx.task_for(<pid of a task the test's lifecycle defines>)` is non-None when `Lifecycle.init` returns, i.e. the definitions were already synced. Follow the existing init tests' setup (see `tests/conftest.py`).
  - `test_task_instance_session_crypto_default_none`: `TaskInstance(...).session_crypto is None`.
- [ ] **Step 2: Run them; they fail** (import errors).
- [ ] **Step 3: Implement** `SessionCrypto` + the field, `deferred_migrations.py`, the init call and the exports. Docstrings point at the spec §4-5.
- [ ] **Step 4: Run the new tests and the optio-core suite** (`../../.venv/bin/pytest -q -m "not serial"`, then `-m serial`); compare with the baseline.
- [ ] **Step 5: Commit** `feat(optio-core): TaskInstance.session_crypto and deferred migrations (run after the initial sync)`.

### Task 2: optio-claudecode, the conversation blob at capture

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/snapshots.py`:
  - docstring layout;
  - `insert_snapshot(..., conversation_blob_id: ObjectId | None = None)`, stored as `conversationBlobId` only when given;
  - `prune_snapshots` also projects and returns `conversationBlobId`.
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`:
  - the teardown buffer block (~1146-1154): keep `conv_listener.export_buffer()` in a local, remove `host.write_text(_CONV_BUFFER_FILE…)`;
  - `_capture_snapshot` (~1811): a new kwarg `conversation: bytes | None = None`;
  - `_store_workdir_snapshot` (~1883): a new kwarg `conversation_blob_id: ObjectId | None = None`, passed to `insert_snapshot`; also deletes `conversationBlobId` of pruned records next to the other blobs;
  - the effective workdir excludes everywhere a workdir tar is made (normal capture ~1294 and `_excludes_with_rescue_marker` ~1605) always include `_CONV_BUFFER_FILE`;
  - `create_claudecode_task` (~2106): `TaskInstance(..., session_crypto=SessionCrypto(encrypt=config.session_blob_encrypt, decrypt=config.session_blob_decrypt))`.
- Modify: `docs/writing-agent-wrappers.md` (claudecode's history persistence paragraph, ~1012-1024).
- Test: `packages/optio-claudecode/tests/test_conversation_record.py` (new), plus updates where existing capture/prune tests construct snapshot records.

**Interfaces:**
- Consumes: `SessionCrypto`, `TaskInstance.session_crypto` (Task 1).
- Produces:
  - `_capture_snapshot(ctx, host, *, end_state, workdir_exclude, session_blob_encrypt=None, conversation: bytes | None = None) -> bool`;
  - `_store_workdir_snapshot(..., conversation_blob_id: ObjectId | None = None)`;
  - `async def _store_conversation_blob(ctx, data: bytes, *, encrypt: Callable[[bytes], bytes] | None) -> ObjectId`, which applies `encrypt` when set and writes `ctx.store_blob("conversation")`;
  - `def _effective_workdir_excludes(workdir_exclude: list[str] | None) -> list[str]`, which returns the given list, or `DEFAULT_WORKDIR_EXCLUDES` when None, plus `_CONV_BUFFER_FILE` if missing;
  - snapshot docs carry an optional `conversationBlobId`.

- [ ] **Step 1: Write failing tests** in `tests/test_conversation_record.py`. Use the existing capture test helpers and fakes (see `test_capture_bookkeeping.py`, `test_capture_stamp.py`, `conftest.py`):
  - `test_capture_stores_encrypted_conversation_blob`: `_capture_snapshot(..., session_blob_encrypt=lambda b: b"E"+b, conversation=b'[[1,{"type":"user"}]]')` stores a GridFS file named `conversation` whose bytes are `b'E[[1,{"type":"user"}]]'`, and the inserted snapshot doc's `conversationBlobId` is that file's id.
  - `test_capture_plaintext_when_no_cipher`: without `session_blob_encrypt`, the blob holds the JSON as given.
  - `test_capture_without_conversation_has_no_field`: no `conversation` gives no `conversationBlobId` and no `conversation` file.
  - `test_refused_capture_stores_no_conversation`: with the credentials guard refusing (no `.credentials.json`), no `conversation` file exists.
  - `test_effective_excludes_always_has_buffer_file`: given `None`, `[]` and `["build"]`, `_CONV_BUFFER_FILE` is in each result, and the given entries (or the defaults for None) are kept.
  - `test_workdir_tar_never_contains_buffer_file`: a workdir holding `.optio-conversation-buffer.json` and `keep.txt`, captured with `workdir_exclude=[]`, gives a workdir blob tar listing `keep.txt` but not the buffer file (Review Focus 1, 2).
  - `test_prune_deletes_conversation_blob`: after `SNAPSHOT_RETENTION + 1` captures, the oldest snapshot's `conversation` file is gone.
  - `test_task_factory_sets_session_crypto`: `create_claudecode_task(...)` with a config holding encrypt/decrypt callables yields `task.session_crypto.encrypt is enc` and `.decrypt is dec`.
  - `test_teardown_does_not_write_buffer_to_host`: a conversation-mode session teardown (existing conversation session tests show how to drive one) leaves no `.optio-conversation-buffer.json` in the host workdir before capture, and passes the export to the capture.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement** per the Interfaces, and update the docs paragraph.
- [ ] **Step 4: Run the new file and the full optio-claudecode suite** (`-m "not serial"`, then `-m serial`); compare with the baseline.
- [ ] **Step 5: Commit** `feat(optio-claudecode): the conversation buffer is a separate (encrypted) blob, not a workdir file`.

### Task 3: optio-claudecode, resume from the blob; rescue and resurrect carry the record forward

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py`:
  - `_load_conversation_buffer` (~1330) and its call (~718): read the latest snapshot's `conversationBlobId` from GridFS and decrypt with `config.session_blob_decrypt`;
  - after the workdir restore (~302): `rm -f <workdir>/.optio-conversation-buffer.json`;
  - the rescue capture (~1702): carry forward.
- Modify: `packages/optio-claudecode/src/optio_claudecode/resurrect.py` (~126, ~154, the `_capture_snapshot` / `_store_workdir_snapshot` calls): carry forward.
- Test: `packages/optio-claudecode/tests/test_conversation_record.py` (extend).

**Interfaces:**
- Consumes: Task 2's `conversationBlobId`, `_store_workdir_snapshot(conversation_blob_id=…)`, `_capture_snapshot(conversation=…)`.
- Produces:
  - `async def _load_conversation_buffer(ctx, *, decrypt: Callable[[bytes], bytes] | None) -> list[tuple[int, dict]] | None`. It keeps today's entry filtering (`[seq, dict]` pairs, drops `x-optio-closed`), and returns None on absent, undecryptable or malformed data, logging and never raising.
  - `async def _latest_conversation_bytes(ctx) -> bytes | None`: the raw stored bytes (still encrypted) of the latest snapshot's conversation blob, or None.
  - Rescue and resurrect captures pass those raw bytes through unchanged as a new blob (no decrypt or re-encrypt; the cipher stays the task's).

  `_capture_snapshot` gains no second path for this. Either `conversation_raw: bytes | None` is stored as-is, or the callers call `_store_conversation_blob(ctx, raw, encrypt=None)` and pass `conversation_blob_id`. Pick one and keep it consistent in both call sites.

- [ ] **Step 1: Write failing tests**:
  - `test_resume_primes_from_encrypted_blob`: a snapshot whose conversation blob is `encrypt(json)` → `_load_conversation_buffer(ctx, decrypt=dec)` returns the entries; an `x-optio-closed` entry is dropped.
  - `test_resume_without_record_is_empty`: no `conversationBlobId` gives None.
  - `test_resume_with_undecryptable_or_corrupt_blob_is_empty`: a decrypt that raises gives None; non-JSON gives None; no exception escapes (Review Focus 3).
  - `test_restore_deletes_stale_buffer_file`: a restored workdir containing the file no longer has it after the restore step.
  - `test_rescue_capture_carries_record_forward`: the previous snapshot has a conversation blob with bytes X, and a rescue capture creates a new snapshot whose `conversationBlobId` is a different file holding exactly X.
  - `test_resurrect_carries_record_forward`: the same for the resurrect capture path.
  - `test_resurrect_capture_excludes_stale_buffer_file`: a failed run's workdir holding the file gives a resurrect workdir blob without it (Review Focus 2).
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the new tests and the full optio-claudecode suite**; compare with the baseline.
- [ ] **Step 5: Commit** `feat(optio-claudecode): resume primes the replay buffer from the conversation blob; rescue/resurrect carry it forward`.

### Task 4: optio-claudecode, the deferred migration

**Files:**
- Create: `packages/optio-claudecode/src/optio_claudecode/tar_strip.py`, a pure streaming helper with no Mongo.
- Create: `packages/optio-claudecode/src/optio_claudecode/migrations.py`, which registers the migration on `optio_core.deferred_migrations`.
- Modify: `packages/optio-claudecode/src/optio_claudecode/__init__.py` (`import optio_claudecode.migrations  # noqa: F401`, so registration happens on import).
- Modify: `packages/optio-claudecode/AGENTS.md` (one paragraph: the migration, what it does, the marker).
- Test: `packages/optio-claudecode/tests/test_tar_strip.py`, `packages/optio-claudecode/tests/test_buffer_migration.py`.

**Interfaces:**
- Consumes:
  - `deferred_migrations`, `DeferredMigrationContext`, `TaskInstance.session_crypto` (Task 1);
  - the snapshot fields (Task 2).
- Produces:
  - `async def strip_member(src: AsyncIterator[bytes], sink: Callable[[bytes], Awaitable[None]], *, name: str) -> bytes | None`.
    - It reads a gzip tar stream and writes a gzip tar of every member except those whose name, without a leading `./`, equals `name`. It returns the stripped member's bytes, or None if absent.
    - It preserves member headers (mode, mtime, uid/gid, link targets, type).
    - Memory is bounded: no whole-archive buffering. Run `tarfile` in stream modes (`r|gz` / `w|gz`) on a thread via `asyncio.to_thread` with queue-backed file objects; the exact plumbing is yours.
  - The migration `claudecode_conversation_buffer_out_of_workdir(ctx: DeferredMigrationContext)`, per spec §6:
    - iterate `{prefix}_claudecode_session_snapshots` without `conversationBufferMigrated: true`, sorted by `capturedAt` ascending;
    - a doc with `conversationBlobId` already gets only the marker;
    - otherwise:
      1. stream the workdir blob through `strip_member` into a new GridFS blob, metadata `{processId: <process doc _id as str, or the snapshot's processId if no process doc>, prefix, name: "workdir"}`;
      2. if the buffer is present:
         - `task = ctx.task_for(doc["processId"])`;
         - a task → store a `conversation` blob, `task.session_crypto.encrypt(bytes)` when `task.session_crypto and task.session_crypto.encrypt`, else plaintext;
         - no task → drop the bytes;
      3. update the doc in one `update_one`: `workdirBlobId` (new), `conversationBlobId` (if stored), marker;
      4. delete the old workdir blob.
    - If the buffer is absent: delete the new blob, set only the marker, keep the old workdir blob.

- [ ] **Step 1: Write failing tests**:
  - `test_tar_strip.py` (no Mongo):
    - `test_strips_dot_slash_and_plain_names`: archives made with members `./.optio-conversation-buffer.json` and with `.optio-conversation-buffer.json` both return the member bytes, and the output lacks it.
    - `test_preserves_other_members_and_metadata`: a regular file (mode 0640, a fixed mtime), a directory, a symlink, and a 3 MB file come through with identical names, modes, mtimes, link targets and contents.
    - `test_absent_returns_none`: no such member gives None, and the output holds all members.
  - `test_buffer_migration.py` (real Mongo, GridFS):
    - `test_migrates_with_task_cipher`: the task's `session_crypto.encrypt = lambda b: b"E"+b` → the doc gets `conversationBlobId` pointing at `b"E"+buffer`, the new workdir tar lacks the file, the old workdir blob is gone, and the doc is marked.
    - `test_plaintext_when_task_has_no_cipher`.
    - `test_no_task_strips_and_drops`: no `conversationBlobId`, the workdir is stripped, marked.
    - `test_absent_buffer_marks_only`: `workdirBlobId` is unchanged, marked, no new files left.
    - `test_new_style_snapshot_marked_only`: a doc that already has `conversationBlobId` keeps its blobs untouched.
    - `test_interrupted_run_completes` (Review Focus 4): simulate a crash after the new blobs are stored but before the doc update (monkeypatch `update_one` to raise once). The rerun yields exactly one `conversationBlobId` per doc and no lost buffer; the orphan blobs from the failed attempt may remain.
    - `test_registered_on_import`: `"claudecode_conversation_buffer_out_of_workdir"` is in `optio_core.deferred_migrations` after `import optio_claudecode`.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement** `tar_strip.py` and `migrations.py`; register on import.
- [ ] **Step 4: Run the new tests and the full optio-claudecode suite**; compare with the baseline.
- [ ] **Step 5: Commit** `feat(optio-claudecode): deferred migration moves conversation buffers out of workdir snapshots`.
