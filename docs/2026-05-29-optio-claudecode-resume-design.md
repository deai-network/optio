# optio-claudecode Resume Support — Design

This spec was written against the following baseline:

**Base revision:** `af2311e45964cbc8d1c4083a5b4375b4c3ad57a4` on branch `feat/optio-claudecode` (as of 2026-05-29T00:00:00Z)

## Summary

Adds resume support to `optio-claudecode`. A task that has terminated (done, failed, or cancelled) can be relaunched later and pick up the agent's prior conversation, working directory, and authenticated state — across host restarts, after engine cancellation, or following an operator-triggered reset. The mechanism mirrors `optio-opencode`'s resume surface so consumer code can choose either agent package and get the same `supports_resume` / `on_resume_refresh` / encrypt-hook semantics, with one structural advantage: HOME-isolation places all sensitive claude state under a single subtree (`<workdir>/home/.claude/`), letting us encrypt only that subset while keeping the bulk workdir tar plaintext (and Mongo-compressible).

## Goals

- Resume a previously-terminated claudecode task by `processId`, restoring claude's full conversation context, credentials, settings, and workdir contents.
- Mirror `optio-opencode`'s public-API resume surface (`supports_resume`, `workdir_exclude`, `on_resume_refresh`, encrypt/decrypt hooks) so consumers can switch between the two packages.
- Encrypt only the sensitive subset (`home/.claude/`) at rest when the consumer supplies crypto hooks. Keep the bulk workdir tar plaintext for compression and speed.
- Reuse `optio-host`'s existing primitives (`archive_workdir`, `restore_workdir`, `run_command`, `fetch_bytes_from_host`, `put_file_to_host`). No new primitives in `optio-host`.

## Non-goals (v1 of resume support)

- Bulk workdir encryption. Out of scope — consumers requiring it use Mongo encryption-at-rest or filesystem-level encryption (LUKS) beneath GridFS.
- Cross-host migration testing. Capture-on-host-A / restore-on-host-B is expected to Just Work given matching SSH targets but is not explicitly covered by automated tests in v1.
- A separate "fork session" config knob. Claude's `--fork-session` flag exists; we always pass `--continue` (no `--fork-session`) so the agent observes a continuous identity. A fork knob may land later.
- macOS auto-install for ttyd. Same status as v1 — Linux-only.

## Architecture

Sensitive claude state — credentials, settings, and the conversation transcript at `home/.claude/projects/<encoded-cwd>/*.jsonl` — lives entirely under `<workdir>/home/.claude/` because of HOME-isolation. Bulk operational state (files the agent edited, deliverables, optio.log, AGENTS.md, etc.) lives at the workdir top level.

A snapshot is two GridFS blobs:

```
session blob   — tar.gz of <workdir>/home/.claude/, encrypted by the consumer hook
workdir blob   — tar.gz of the workdir minus home/.claude, plaintext
```

This split mirrors opencode's `session blob` (encryptable) + `workdir blob` (plaintext) layout exactly. The Mongo collection shape is identical to opencode's `{prefix}_opencode_session_snapshots` minus the `sessionId` field — claude finds the latest session itself via `--continue`, so optio does not have to record or pass a session UUID.

Resume rehydrates the workdir in two passes (plaintext first to establish the directory tree, encrypted blob on top of `home/.claude/` last), rotates `optio.log` so the tail-driver does not re-fire the previous run's DONE/ERROR, and appends `--continue` to the claude argv at launch.

## Mongo schema

Collection: `{prefix}_claudecode_session_snapshots`. One document per terminal run per process-id.

```python
{
  "_id":                 ObjectId,
  "processId":           str,
  "capturedAt":          datetime,    # UTC
  "endState":            str,         # "done" | "failed" | "cancelled"
  "sessionBlobId":       ObjectId,    # GridFS — encrypted tar.gz of <workdir>/home/.claude/
  "workdirBlobId":       ObjectId,    # GridFS — plaintext tar.gz of workdir minus home/.claude
  "deliverablesEmitted": list,        # audit metadata only; not replayed
}
```

Index: `(processId, capturedAt desc)`, named `by_processId_capturedAt_desc`. Created idempotently by `insert_snapshot`.

Retention: keep the latest `SNAPSHOT_RETENTION = 5` per `processId`. Older documents are deleted by `prune_snapshots`, which returns the list of `{sessionBlobId, workdirBlobId}` pairs so the caller can remove the corresponding GridFS blobs.

This is the same surface as opencode's snapshots module. The implementation is a direct port — sibling module under `packages/optio-claudecode/src/optio_claudecode/snapshots.py` — not a shared library, because the collection name is package-specific and the doc shape diverges by one field.

## Capture flow

Runs inside `run_claudecode_session`'s `finally` block, after `after_execute` has fired and before workdir cleanup. Gated on `config.supports_resume` and the session having reached a terminal state on a connected host.

Pseudo-code:

```python
1. # tar the sensitive subtree, gather into bytes
   session_bytes = await _archive_home_claude(host)

2. # encrypt if hooks supplied, else plaintext fallthrough
   encrypt = config.session_blob_encrypt or (lambda b: b)
   payload = encrypt(session_bytes)

3. # write blob to GridFS
   async with ctx.store_blob("session") as w:
       await w.write(payload)
       session_blob_id = w.file_id

4. # defensive wipe — workdir tar must not carry a copy of sensitive state
   await host.run_command(f"rm -rf {workdir}/home/.claude")

5. # stream the plaintext workdir tar.gz to GridFS
   async with ctx.store_blob("workdir") as w:
       async for chunk in host.archive_workdir(config.workdir_exclude):
           await w.write(chunk)
       workdir_blob_id = w.file_id

6. await insert_snapshot(
       db, prefix, processId, endState, session_blob_id, workdir_blob_id, ...)

7. pruned = await prune_snapshots(db, prefix, processId)
   for p in pruned:
       await ctx.delete_blob(p["sessionBlobId"])
       await ctx.delete_blob(p["workdirBlobId"])

8. await ctx.mark_has_saved_state()   # surfaces the Resume affordance in the dashboard
```

`_archive_home_claude` is a small claudecode-local helper that uses existing `optio-host` primitives:

```python
async def _archive_home_claude(host) -> bytes:
    tmpfile = f"{host.workdir}/.optio-claudecode-session.tar.gz"
    r = await host.run_command(
        f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(host.workdir)} home/.claude"
    )
    if r.exit_code != 0:
        raise RuntimeError(
            f"tar home/.claude failed (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    try:
        return await host.fetch_bytes_from_host(tmpfile)
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")
```

The defensive `rm -rf` at step 4 exists because relying on `archive_workdir`'s exclude pattern to suppress `home/.claude` is one config change away from leaking sensitive state into the plaintext blob. Removing the directory before the workdir tar runs makes the suppression invariant rather than pattern-dependent.

Failure semantics: any step inside the capture catches its own exception, logs via `report_progress`, and does not propagate. A failed snapshot leaves the consumer without a resumable state but does not mask the original session error or block workdir cleanup. Mirrors opencode's `_capture_snapshot` style.

## Resume flow

Runs at the start of `run_claudecode_session`, BEFORE the protocol-driver session begins. Gated on `getattr(ctx, "resume", False)` and a snapshot existing for this `processId`.

Pseudo-code:

```python
1. snapshot = await load_latest_snapshot(db, prefix, processId)
   if not (resume_requested and snapshot):
       resuming = False
       claude_flags = build_claude_flags(...)
       return

2. await host.connect()
   await host.setup_workdir()

3. # plaintext workdir first — establishes the directory tree (incl. home/)
   await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))

4. # decrypt (or plaintext-pass) and extract home/.claude over the top
   payload = await _read_blob_bytes(ctx, snapshot["sessionBlobId"])
   decrypt = config.session_blob_decrypt or (lambda b: b)
   try:
       plain = decrypt(payload)
   except Exception:
       # Decrypt failure = tampering or key change. Fail loud — do NOT
       # silently drop to fresh-start.
       raise

   await _extract_home_claude(host, plain)

5. # rotate optio.log so the protocol tail driver does not replay
   # last run's DONE / ERROR
   await _rotate_optio_log(host)

6. resuming = True
   claude_flags = build_claude_flags(..., resuming=True)
   # build_claude_flags appends "--continue" when resuming=True
```

`_extract_home_claude` is the mirror of `_archive_home_claude`:

```python
async def _extract_home_claude(host, plain: bytes) -> None:
    tmpfile = f"{host.workdir}/.optio-claudecode-restore.tar.gz"
    await host.put_file_to_host(plain, tmpfile)
    try:
        r = await host.run_command(
            f"tar -xzf {shlex.quote(tmpfile)} -C {shlex.quote(host.workdir)}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"tar -x home/.claude failed (exit {r.exit_code}): "
                f"{r.stderr.strip()[:200]}"
            )
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")
```

Decrypt-failure handling diverges from a generic exception path: an exception in `session_blob_decrypt` is treated as evidence of tampering or operator key rotation and is propagated to the caller. We deliberately do NOT fall back to fresh-start on decrypt failure, because that would mask a security-relevant signal. Mirrors opencode's behavior.

`_rotate_optio_log` is copied verbatim from opencode: append the workdir's `optio.log` (which the snapshot tar restored) onto `optio.log.old`, then truncate `optio.log` to empty. Without rotation, `tail -F -n +1` re-emits the previous run's terminal lines and the resumed task ends within a tail-flush.

`task_dir` is deterministic per `(process_id, consumer_name)`, so the workdir path is stable across resumes and `home/.claude/projects/<encoded-cwd>/` accumulates session jsonl files for the same `<encoded-cwd>`. Claude's `--continue` picks the most recent. We do not pass `--session-id` or `--fork-session`.

## Prompt additions (divergences from opencode)

`resume.log` mechanism is identical to opencode: one line per session start, optional `REFRESHED:<files>` suffix tagging resumes where the framework rewrote files.

`RESUME_SECTION` text is byte-identical to opencode's, with one added bullet: the agent is told that `home/.claude/` is preserved across resumes (opencode mentions only the workdir; claudecode needs the extra clause because credentials, settings, and the conversation transcript all live there and matter to the agent's continuity model). Lives in `optio_claudecode/prompt.py` alongside the v1 wrapper. Not shared with opencode.

The composer dispatch follows the existing pattern: `compose_agents_md(consumer_instructions, resume_section=...)`. The `optio_host.agents.compose_agents_md` signature already accepts a pre-rendered `resume_section`; claudecode renders it when `supports_resume=True`.

`_render_resume_section(workdir_exclude)` interpolates the effective exclude list into the prompt so the agent's mental model matches what the snapshot mechanism actually preserves. Same shape as opencode's renderer.

## Config surface (`ClaudeCodeTaskConfig` additions)

Three new fields plus a fourth that already exists in v1 but flips meaning:

```python
@dataclass
class ClaudeCodeTaskConfig:
    # ... existing v1 fields ...

    supports_resume: bool = True   # was hardcoded False in v1
    workdir_exclude: list[str] | None = None
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    on_resume_refresh: Callable[[ClaudeCodeTaskConfig], ClaudeCodeTaskConfig] | None = None
```

`supports_resume` flips the v1 hardcoded `False` to a default of `True`. The propagated `TaskInstance.supports_resume` value tracks the config field. Callers who do not want resume must pass `supports_resume=False` explicitly.

Validation in `__post_init__` (identical to opencode — asymmetric is the only configuration error):

```python
e = self.session_blob_encrypt is not None
d = self.session_blob_decrypt is not None
if e != d:
    raise ValueError(
        "ClaudeCodeTaskConfig: session_blob_encrypt and "
        "session_blob_decrypt must be set together (both callables) "
        "or both left as None; one without the other is a config error."
    )
```

Both-None means plaintext fallthrough — the same fallback behavior opencode uses. Capture: skip the encrypt callable, write the raw tar bytes. Resume: skip the decrypt callable, read the raw tar bytes. Consumers who need at-rest encryption supply the callable pair. Consumers who don't (dev, demo, single-tenant deploys with Mongo-side encryption) leave them None.

This matches opencode's surface 1:1 — no divergence on validation rules. Forcing an identity-pair on every demo/dev path adds boilerplate without preventing plaintext storage (any consumer who wants plaintext just supplies `lambda b: b`); the explicit-None form is the cleaner expression of intent.

`workdir_exclude` semantics are unchanged from opencode: `None` = framework default (`optio_host.archive.DEFAULT_WORKDIR_EXCLUDES`); `[]` = no excludes; non-empty list = used verbatim. The framework does NOT add `home/.claude` to this list — the defensive `rm -rf` at capture step 4 is the actual suppression mechanism.

`on_resume_refresh` is identical to opencode's: invoked only on resume, receives the original config, returns a possibly-mutated config; if AGENTS.md re-renders to different content, the harness writes it back and tags the next `resume.log` line with `REFRESHED:AGENTS.md`. Lives in claudecode's session.py as `_maybe_refresh_on_resume`.

## Edge cases

- **Restore order.** Workdir tar must be applied before the encrypted `home/.claude` tar. Reversed order risks the workdir's tree clobbering the freshly-extracted state.

- **Decrypt failure on resume.** Raised exception is propagated; the session fails. No fresh-start fallback. Documented above and asserted in tests.

- **Tar failure during capture.** Raised inside the helper; caught one level up, logged via `report_progress`, skips snapshot. Workdir cleanup still runs. Original session error is preserved.

- **Encryption hook crash.** Same as tar failure — caught, logged, skip snapshot, do not mask.

- **First resume after `supports_resume` toggled False→True.** No prior snapshots exist; `load_latest_snapshot` returns `None`; we fall back to fresh-start cleanly.

- **HasSavedState flag.** `mark_has_saved_state` is called only at successful snapshot capture. If snapshot fails, the dashboard correctly reflects no resumable state. Resume's "snapshot lookup returns None" branch is the self-healing fallback when the flag is somehow stale.

- **Multiple worker processes racing on the same `processId`.** Out of scope — assumed to be prevented by the engine. The Mongo retention/prune logic is per-processId so concurrent capture would race but not corrupt data; latest wins.

## Testing

All tests under `packages/optio-claudecode/tests/`. Existing shim infrastructure (`fake_claude.py`, `claude-shim.sh`, `ttyd-shim.sh`, conftest fixtures) is reused. Two new fake-claude scenarios:

- `long_then_signaled` — stays alive indefinitely so cancellation paths can be exercised mid-run.
- `idempotent_done` — emits the same DONE line twice across separated runs; verifies that the agent's perspective of continuity is preserved across capture+restore.

New test modules:

- `tests/test_snapshots.py` — Mongo helpers: `insert_snapshot`, `load_latest_snapshot`, `prune_snapshots`, index creation. Direct port of opencode's parallel test file with the schema diff (no `sessionId`).

- `tests/test_session_resume.py` — end-to-end local round-trip:
  - Run a session with resume-enabled config, identity encrypt/decrypt callables (`lambda b: b`), and `happy` scenario. Assert Mongo doc written with the right shape and both blobs present.
  - Launch a second instance with the same `processId` and `ctx.resume=True`. Assert: workdir restored, `home/.claude/.credentials.json` present, `optio.log` rotated, claude argv contains `--continue`.
  - Encryption identity test ensures the round-trip works without depending on any specific crypto. Real encryption is the consumer's responsibility.

- `tests/test_session_resume_decrypt_failure.py` — corrupt `sessionBlobId` (overwrite content with garbage). Resume attempt raises with a message that mentions decrypt. Crucially asserts the session does NOT silently fall back to a fresh start.

- `tests/test_resume_prompt.py` — `_render_resume_section` produces the expected text under default and custom `workdir_exclude`, and mentions `home/.claude/` preservation.

- `tests/test_on_resume_refresh.py` — hook fires only on resume; mutated config rewrites AGENTS.md; `REFRESHED:AGENTS.md` line appears in `resume.log`.

- `tests/test_session_blob_hooks.py` — config validation: asymmetric (one set, the other None) raises; both-None and both-set both accepted.

The local session tests run against MongoDB-via-Docker, same as v1.

## Out of scope (deferred)

- **Bulk workdir encryption.** Consumers requiring it use Mongo encryption-at-rest or LUKS.
- **Cross-host snapshot migration.** Should work; not automated in v1 tests.
- **`--fork-session` knob.** Not exposed yet. Always `--continue` for now.
- **Automated SSH-in-Docker remote resume test.** Same status as v1's remote test infrastructure — manual smoke; full automation tracked separately.
- **Snapshot compression-vs-encryption ordering knob.** Fixed as "tar.gz first, then encrypt"; no plan to make this tunable.

## Demo task update

`packages/optio-demo/src/optio_demo/tasks/claudecode.py` is updated alongside the implementation to exercise the resume path:

- `supports_resume=True` (explicit, even though it's the new default).
- `session_blob_encrypt` and `session_blob_decrypt` left as `None`. Claudecode takes the plaintext fallthrough — same shape as the opencode demo. Operators who fork the demo for a real deployment supply both hooks pointing at actual crypto.
- `workdir_exclude` left at default (`None`).
- No `on_resume_refresh` hook in the demo. Default behavior — AGENTS.md is reused verbatim on resume.

The demo update is part of the implementation plan, not a separate follow-up.

## Follow-up referenced from v1

The original optio-claudecode spec (`docs/2026-05-28-optio-claudecode-design.md`) listed "Resume support" under follow-ups. This spec is that follow-up.

When implemented, the existing v1 type-test for `supports_resume` being absent from `ClaudeCodeTaskConfig` will need to be updated. Listed explicitly so the implementation plan does not forget.
