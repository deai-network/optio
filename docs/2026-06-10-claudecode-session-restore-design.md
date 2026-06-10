# optio-claudecode Explicit Session Restore — Design

This spec was written against the following baseline:

**Base revision:** `ae4dab115f3f3abc3b44c04d51be3b9d13001ed5` on branch `csillag/convo-scripter` (as of 2026-06-10T07:32:22Z)

## Summary

New `ClaudeCodeTaskConfig` options governing how Claude Code is executed on **fresh
runs only**: plant an explicitly-referenced Claude session (optionally truncated at a
turn boundary) into the new session's `home/.claude` before launch, capture the session
to an addressable blob at teardown, and select the model. Work items D1 / D1b / D3 from
the conversation-scripter spec (`2026-06-10-conversation-scripter-design.md`), which
consumes them to implement fork-and-reflow. Optio-level resume is untouched by
definition — resume continues whatever lives in the restored directory.

## Decisions (settled during brainstorming)

1. **Session refs are raw GridFS file ids** — no new metadata collection. The caller
   owns all metadata and blob lifecycle (the scripter records refs on its rendering
   docs; a claudecode-side registry would shadow it entry-for-entry). Rejected:
   claudecode-owned session collection (drift-prone duplicate registry); reusing the
   snapshots collection (processId-keyed, retention-pruned — would eat rendering
   history — and carries workdir blobs).
2. **Capture opt-in = callback presence.** `on_session_saved` set → teardown captures
   and reports; unset → today's behavior byte-identical. Capture runs on **all** end
   states (done / failed / cancelled) — `cancelled` is the engine-restart graceful
   path; `failed` still preserves the transcript up to the crash, and the end-state
   argument lets the caller decide what to trust.
3. **Restore directives are conversation-mode only** (validated). The iframe variant
   would need kickoff/resume-notice decisions nobody currently needs; one validation
   line to lift later. `model` and `on_session_saved` work in both modes.
4. **`session_restore_until` is a transcript-entry uuid; truncation is a positional
   prefix cut with loud failure.** Verified against a real transcript (994 entries):
   all conversational entries (`user`/`assistant`/`system`/`attachment`) carry
   `uuid`/`parentUuid`/`isSidechain`; interleaved uuid-less bookkeeping entries exist
   (`last-prompt` with a `leafUuid` leaf pointer, `mode`, `permission-mode`,
   `ai-title`, `file-history-snapshot`, `queue-operation`), so truncation includes a
   bookkeeping-repair step for trailing pointer entries. A uuid not found in the
   newest transcript **fails the task at prepare** — never launch with silently-wrong
   context. Rejected: positional turn counts (ambiguous around interleaved tool
   results and sidechains).
5. **Truncation runs engine-side** on the decrypted blob bytes (in-memory tarfile
   rewrite) before planting — no host-side tooling dependency, identical local/remote.
6. **Cancel propagation**: the scripter relies on optio's default
   `auto_cancel_children=True`; no self-propagation opt-out. A cooperatively-cancelled
   child runs the same teardown bracket as a graceful `close()` (capture included, per
   Decision 2), and `on_session_saved` persists the ref regardless of the parent's
   progress. Noted for the scripter spec: configure a generous `cancel_grace_seconds`
   so child teardown (tar + GridFS upload) fits the shared grace budget.
7. **Kickoff on restore launches is silent**: no `AUTO_START_PROMPT`, no resume
   notice. The caller's first `send()` is the next user message — seamless
   continuation. `auto_start=True` with `session_restore_from` is a config error.

## 1. Config surface (`types.py`)

All additive, defaults preserve current behavior:

```python
session_restore_from: ObjectId | None = None
    # GridFS blob id of an encrypted home/.claude tar (as produced by
    # on_session_saved or the snapshot machinery); planted on fresh start.
session_restore_until: str | None = None
    # Transcript entry uuid: keep everything up to and including this entry,
    # drop everything after. None = full history.
on_session_saved: Callable[[ObjectId, str], Awaitable[None] | None] | None = None
    # (new_blob_id, end_state) fired at teardown after the session blob is
    # stored. Presence opts in to capture. Sync or async (per on_seed_saved).
model: str | None = None
    # Passed through as `--model <value>`. Not validated (vendor strings).
```

`__post_init__` additions:

- `session_restore_until` without `session_restore_from` → `ValueError`.
- `session_restore_from` with `mode="iframe"` → `ValueError`.
- `session_restore_from` with `auto_start=True` → `ValueError`.

`session_blob_encrypt` / `session_blob_decrypt` apply to the new blobs in both
directions (capture encrypts, restore decrypts), exactly as for snapshot blobs.

## 2. Restore flow

In `_prepare` (`session.py`), immediately after the existing resume-restore block:

- If `ctx.resume` restored a snapshot: **skip** the restore directives with a logged
  notice (fresh-runs-only rule).
- Otherwise, when `session_restore_from` is set:
  1. Fetch blob bytes from GridFS (`ctx.load_blob`); missing blob → task fails.
  2. Decrypt engine-side (`session_blob_decrypt` or identity); failure → loud
     (existing semantics).
  3. Transform via `rebase_session_blob(plain, new_workdir=..., until_uuid=...)` (§4):
     always rekeys the transcript directory to the new workdir's slug; truncates when
     `session_restore_until` is set. `ValueError` → task fails at prepare.
  4. Plant via the existing `_extract_home_claude(host, plain)`.
  5. `_has_transcript(host)` must return True; otherwise the task fails
     ("restored session blob contains no transcript"). On success the launch uses the
     existing continue-style resumption flag path.

Kickoff (conversation-mode body): when `session_restore_from` is set, send **nothing**
— no auto-start (already excluded by validation), no resume notice. Prompt composition
is unchanged beyond existing conversation-mode behavior (CLAUDE.md is freshly composed
by the caller-supplied instructions; the restored transcript's references to its prior
CLAUDE.md are the caller's concern — the scripter composes identical instructions per
conversation).

## 3. Capture flow

At teardown (the existing `finally` bracket, alongside — not replacing — snapshot
capture), when `on_session_saved` is set:

1. Tar `home/.claude` on the host (the same capture code used by `_capture_snapshot`,
   factored so it is callable independently of the snapshots collection).
2. Encrypt engine-side (`session_blob_encrypt` or identity).
3. Store as a standalone GridFS blob (`ctx.store_blob`).
4. Fire `on_session_saved(blob_id, end_state)` with end_state ∈
   {"done", "failed", "cancelled"}.

Runs on all end states. Orthogonal to `supports_resume`: both mechanisms may be active
on one task; the scripter's children use `supports_resume=False` + this callback.
Storage/callback failure: logged, teardown continues (matching today's
snapshot-failure behavior); the callback is not fired for a blob that failed to store.

## 4. Blob transform (`optio_claudecode/transcript.py`, new module)

```python
def rebase_session_blob(
    plain_tar: bytes, *, new_workdir: str, until_uuid: str | None = None,
) -> bytes
```

Pure function, no host or Mongo dependency:

1. Untar in memory; locate the **newest** `*.jsonl` under `home/.claude/projects/**`
   (mtime; tar extraction preserves mtimes, so `--continue`'s newest-session
   selection matches).
2. **Projects-dir rekey** (always): Claude Code stores transcripts under
   `home/.claude/projects/<workdir-slug>/`, the slug derived from the session cwd
   (`/` and `.` → `-`; confirmed against two real samples). A restored blob carries
   the **old** workdir's slug while the new session runs in a fresh workdir, so
   without the rekey `--continue` finds nothing. Rename the transcript's parent dir
   to `slugify(new_workdir)` during the tar rewrite. (The analogous `.claude.json`
   projects rekey already exists in the seed machinery and is handled by the
   existing fresh-start seed merge.)
3. Truncation (when `until_uuid` set): positional prefix cut — keep every line up to
   and including the line whose JSON carries `"uuid" == until_uuid`; drop the rest.
   Uuid absent → `ValueError` with a clear message.
4. Bookkeeping repair: kept pointer entries (`last-prompt`/`leafUuid`) must not
   reference dropped entries — v1 rewrites a dangling `leafUuid` to the boundary
   uuid; the exact required shape is confirmed by live verification (§7).
5. Retar (other files in the blob pass through unchanged).

## 5. Flag building (`host_actions.py`)

`build_claude_flags` gains `model: str | None = None` → emits `--model <value>`.
The restore path reuses the existing `resuming=True` → `--continue` mechanism.
Documented fallback if live verification shows `--continue` mis-selecting the session:
`--resume <session-id>` with the id taken from the newest transcript's filename.

## 6. Error handling summary

| Failure | Behavior |
|---|---|
| `session_restore_from` blob missing in GridFS | Task fails at prepare |
| Decrypt failure | Loud failure (existing semantics) |
| `session_restore_until` uuid not found | Task fails at prepare (`ValueError` surfaced) |
| Planted blob has no transcript | Task fails at prepare |
| Capture tar/store failure at teardown | Logged; teardown continues; callback not fired |
| `on_session_saved` callback raises | Logged; teardown continues (per existing callback idiom) |
| Directives present on an optio-level resume | Skipped with logged notice |
| Invalid config combinations (§1) | `ValueError` at construction |

## 7. Testing

- **Config validation matrix** (`test_types.py` or equivalent): the three new
  `ValueError` combinations + valid forms.
- **Blob-transform unit tests** (no host, fixture transcripts in real format):
  projects-dir rekey to the new workdir slug (incl. slugify rule); boundary
  mid-file; boundary at last conversational entry; uuid absent; bookkeeping tail
  after boundary; dangling `leafUuid` rewritten; sidechain entries interleaved;
  newest-of-several transcripts selected; non-transcript blob files pass through;
  rekey-only call (`until_uuid=None`) leaves content untouched.
- **Session-flow tests** (existing fake-claude / stream-json shim harness): planted
  blob → argv carries the resumption flag and no kickoff message is sent; planted
  blob without transcript → task fails; capture fires `on_session_saved` with a blob
  that round-trips (restore it into a second session); capture-on-cancel; `model`
  flag pass-through; directives skipped on optio resume.
- **Live verifications — ALL CONFIRMED** (real headless sessions, claude 2.1.170,
  isolated HOME, 2026-06-10; codeword-probe methodology — turn 1 plants a codeword,
  turn 2 is sacrificial, the trimmed resume must know the codeword and have no
  memory of turn 2):
  1. ✅ `--continue` accepts a `rebase_session_blob`-truncated transcript: same
     session id continues, turn-1 context intact, truncated turn absent from model
     memory. Bookkeeping repair shape: the trailing `last-prompt` entry falls to the
     prefix cut and the CLI accepts a transcript ending at an assistant entry — no
     `leafUuid` rewrite was needed in practice (the defensive rewrite stays).
     `--continue` **appends in place** (same file, same session id — no per-resume
     fork in headless print mode), so a conversation lineage is a single growing
     transcript and "newest file" selection is trivially correct.
  2. ✅ Headless transcript format matches the TUI samples: `uuid`/`parentUuid`
     chains on user/attachment/assistant entries; uuid-less `queue-operation` /
     `ai-title` / `last-prompt` bookkeeping interleaved.
  3. ✅ Turn-boundary uuid: `assistant` stream-json events carry the transcript
     entry uuid verbatim — the boundary for `restore_until` is the uuid of the
     **last assistant event before the turn's `result`** (the `result` event's own
     uuid does not appear in the transcript). No extra reporting machinery needed.
  4. ✅ Slug rule holds headless: `/tmp/sr-live-verify/work1` →
     `-tmp-sr-live-verify-work1`.
  5. ✅ Per-entry `cwd` fields (which still point at the old workdir after rekey) do
     not block resumption — directory rename suffices; the cwd-field rewrite
     fallback is not needed.

## 8. File map

| File | Change |
|---|---|
| `optio-claudecode/src/optio_claudecode/types.py` | four new config fields + validation |
| `optio-claudecode/src/optio_claudecode/transcript.py` | new: `rebase_session_blob` (rekey + truncate) |
| `optio-claudecode/src/optio_claudecode/session.py` | restore slot in `_prepare`; capture-to-ref at teardown; silent-kickoff branch |
| `optio-claudecode/src/optio_claudecode/host_actions.py` | `--model` in `build_claude_flags` |
| `optio-claudecode/tests/...` | validation matrix, truncation units, session-flow tests |
