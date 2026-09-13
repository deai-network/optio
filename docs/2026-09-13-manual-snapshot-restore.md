# Manually restoring a claudecode task's state after a failed snapshot capture

Date: 2026-09-13. Used twice for excavator's plakomm analysis
(`6aa42dea1b791783f5a01e83`); scripts in `excavator:~/excavator-forensics/`.

## When this applies

A remote claudecode task was stopped (cancel or engine restart), its snapshot
capture exceeded the cancel grace, and optio force-failed it ("Task did not
unwind within cancellation grace period"). No new snapshot record exists, so a
resume would restore the previous one and lose everything since.

**Do not resume or relaunch the process first**: a resume empties and replaces
the remote workdir, which is the only complete copy of the latest state.

## What survives a failed capture

- The remote workdir (the unwind never reached `cleanup_taskdir`), at
  `/tmp/optio-claudecode/<processId>/workdir` on the agent host. It survives
  only until that host reboots (`/tmp`).
- If `home/.claude` is **gone** from that workdir, the capture already stored
  the session blob (encrypted tar of `home/.claude`) and then deleted it: the
  newest GridFS file with `metadata.processId = <process oid>` and
  `metadata.name = "session"` is the matching one. If `home/.claude` is still
  there, the capture died earlier; the session blob must be made fresh (tar
  `home/.claude`, encrypt with the vault cipher of `ds-<dataspace>`), which this
  procedure does not cover.

## Procedure

1. **Back up the remote workdir** on the agent host:
   `cp -a <workdir> ~/excavator-forensics-plaplay/<name>`.
2. **Find the session blob id** (engine host, app Mongo `excavator`):
   `db.getCollection("fs.files").find({"metadata.processId": "<oid>", "metadata.name": "session"}).sort({uploadDate: -1}).limit(1)`.
3. **Build the workdir archive exactly like the capture** on the agent host,
   with the task's `workdir_exclude` (excavator analysis: `.env`). Output must be
   gzip: `(cd <workdir> && tar cf - --exclude=.env . | pigz -1) > restore.tar.gz`
   (`tar czf -` also works, ~6× slower); `sha256sum restore.tar.gz`.
4. **Copy it to the engine host** (`scp -3 agent:… engine:~/excavator-forensics/`)
   and verify the checksum there.
5. **Upload + insert the snapshot record** with
   `excavator:~/excavator-forensics/restore_snapshot.py` (run with excavator's
   venv from `~/deai/excavator`; edit its `TAR`, `SHA`, `SESSION` constants). It:
   - asserts the session blob's metadata is `{processId: <oid>, prefix: "gm", name: "session"}` and the process is in a launchable state;
   - uploads the archive to the default GridFS bucket as filename `workdir`, metadata `{processId: <oid>, prefix: "gm", name: "workdir"}`, and verifies the read-back sha256;
   - calls `optio_claudecode.snapshots.insert_snapshot(db, prefix="gm", process_id=<processId>, end_state="cancelled", session_blob_id=…, workdir_blob_id=…, deliverables_emitted=[])`;
   - asserts `load_latest_snapshot` now returns the new record (resume picks the newest `capturedAt`), and saves the record as `restore-snapshot-<id>.json`.
6. **Delete orphaned GridFS chunks** left by the aborted upload (chunk groups
   whose `files_id` has no `fs.files` document), e.g. in mongosh:
   group `fs.chunks` by `files_id`, keep ids missing from `fs.files.distinct("_id")`,
   `deleteMany` them.
7. **Kill leftovers on the agent host** from the failed stop: the stalled
   `tar czf -`/`gzip`, `tail -F … /workdir/optio.log`, and the old `sftp-server`.
8. **Resume** from the process page. The process must be in a launchable state
   (`idle`, `done`, `failed`, `cancelled`) with `hasSavedState: true` and
   `supportsResume: true`.

## Reading the transcript out of a session blob

`excavator:~/excavator-forensics/extract_session.py <blob id> ds-<dataspace> <out>`
decrypts a session blob in memory (engine venv, `engine.credential_vault.prepare_blob_cipher`)
and writes only the `home/.claude/projects/**/*.jsonl` transcript; credentials
never touch disk.
