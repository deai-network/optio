# optio-claudecode Seed Support — Design

This spec was written against the following baseline:

**Base revision:** `4f2f00a3a7136bf66f728751152f29ec4dfb89d0` on branch `main` (as of 2026-05-29T02:01:10Z)

## Summary

Adds **seed support** to `optio-claudecode`: a way to start a *fresh* claude
session that is already logged-in and configured, without continuing any prior
conversation. This is the mode the existing resume feature could not provide.

A **seed** is a stored, encryptable tarball of the *environment* subset of a
claude HOME — credentials, settings, MCP auth, installed plugins, and global
config — with no conversation/session data. A new task launched with a
`seed_id` merges that environment into its fresh workdir, writes its own
per-task AGENTS.md, and launches claude normally (no `--continue`). Seeds are
created by a dedicated capture: launch a vanilla session, log in / configure
interactively, then stop it — on teardown the framework captures the
environment-only files and returns a generated `seed_id` via a callback.

Seeds are keyed by an opaque, optio-generated id so the system can serve
many users (each with a personal login). optio stays owner-agnostic; the
consuming application owns the `user → seed_id` mapping and seed lifecycle.

## Motivation

Diagnosis from live testing of the resume feature established that none of the
existing launch modes is fit for spawning real work on new tasks:

1. **Vanilla fresh start** — no credentials, no login, no config. Unfit for
   work unless the consumer plants everything via config, and plain config
   cannot reproduce an interactive OAuth login, installed plugins, or MCP auth.
2. **Resume of a session saved *before* login** — `claude --continue` launches
   with no auth and nothing to continue, exits immediately, and (because ttyd
   respawns the pty program per client connect) the frontend flaps
   reconnecting/connecting forever.
3. **Resume of a session saved *after* login** — works, but it is the *same*
   conversation: `--continue` restores the agent's belief that it already read
   AGENTS.md at session start, so a new per-task AGENTS.md is not re-read. Wrong
   tool for starting a *new* task.

The missing capability is **"seeded environment, fresh conversation"**: an
authenticated, configured claude that begins a brand-new conversation and reads
the task's AGENTS.md correctly. The structural insight: the resume feature
conflated two independent axes — **identity/environment** (wanted for any real
work) and **conversation** (wanted only for true same-task resume). Seeds
provide the identity axis without the conversation axis.

This builds on `docs/2026-05-29-optio-claudecode-resume-design.md` and the fixes
made on `feat/optio-claudecode-resume` (workdir-snapshot junk exclusion and
process-group reaping on cancel).

## Goals

- Start a fresh, logged-in, configured claude session from a stored seed,
  beginning a new conversation that reads the per-task AGENTS.md.
- Capture a seed from an interactively-configured session, returning a
  generated id via callback so the consuming app can record it.
- Per-user seeds: opaque ids, optio owner-agnostic, app owns the
  `user → seed_id` mapping and garbage collection.
- Encrypt seeds at rest using the existing `session_blob_encrypt` /
  `session_blob_decrypt` hooks (seeds carry OAuth credentials).
- Capture-time file triage: the seed blob contains only environment files —
  never a conversation transcript.

## Non-goals (v1 of seed support)

- A live command channel into running tasks. Seed capture is triggered by
  declaring intent at launch and stopping the task — no new channel. (`clamator`
  queued-RPC-over-Redis exists in the codebase but is intentionally not used
  here.)
- Caller-supplied seed ids / in-place overwrite. Every capture mints a new id;
  a *new* id is the unambiguous success signal for the app. (See
  "Seed id generation".)
- Optio-side seed retention/GC. optio has no owner concept; the app GCs via
  `delete_seed`.
- Cross-host seed portability testing. Expected to work given matching SSH
  targets; not covered by automated tests in v1.
- Fixing the resume-flap-on-startup-exit (**D2**) at the framework level — noted
  as a follow-up. The seed model removes the common cause (new tasks no longer
  `--continue`), and the in-scope D3 safety below removes the other.

## Launch mode matrix

A single `run_claudecode_session` resolves the mode from `ctx.resume`,
`config.seed_id`, and `config.on_seed_saved`:

| Condition | Mode | Behavior |
|---|---|---|
| `ctx.resume` and a snapshot exists | **Resume** | restore full `home/.claude` + workdir; append `--continue`. `seed_id` / `on_seed_saved` ignored (logged). **D3 safety:** if the restored snapshot has no conversation transcript, launch **without** `--continue`. |
| fresh, `seed_id` set | **Seeded fresh** | merge the seed's environment files into the fresh workdir, rekey `.claude.json` `projects` to the new cwd, write per-task AGENTS.md, launch **without** `--continue`. |
| fresh, no `seed_id` | **Vanilla fresh** | blank state; write per-task AGENTS.md; launch without `--continue`. The operator logs in / configures here. |
| any fresh mode, `on_seed_saved` set | **+ seed capture** | orthogonal: on stop/teardown, capture the environment-only subset, store it, fire `on_seed_saved(seed_id)`. |

"Fresh" means `ctx.resume` is false or no snapshot exists. Seed capture and
resume snapshotting are independent: `supports_resume` controls snapshotting;
`on_seed_saved` controls seed capture.

## Config surface (`ClaudeCodeTaskConfig` additions)

```python
# Consume: when set on a fresh launch, merge this seed's environment into the
# new workdir before launching. Ignored on resume. Unknown id → error
# (fail loud; do not silently fall back to vanilla).
seed_id: str | None = None

# Capture intent: when set, on stop/teardown capture the environment-only
# subset of home/, store it as a new seed, and call this with the generated
# seed id. Its presence IS the intent (no separate boolean). Ignored on resume.
on_seed_saved: Callable[[str], Awaitable[None] | None] | None = None
```

No `__post_init__` validation couples the two — they are independent (a session
may consume seed X and save a new seed Y). On the resume path both are ignored
with a logged warning. `seed_id` and `on_seed_saved` are unset by default, so
existing consumers are unaffected.

Encryption of the seed blob reuses the existing `session_blob_encrypt` /
`session_blob_decrypt` hooks already on the config. Both-None → plaintext seed
(same fallthrough policy as snapshots).

## Seed id generation

Every capture mints a **new, opaque, optio-generated** id (an `ObjectId` hex
string). Rationale: the consuming app records `user → seed_id`; when a user
re-seeds (e.g. token expiry), a *different* id arriving via `on_seed_saved` is
an unambiguous success signal, whereas an in-place overwrite would deliver
data identical to what the app already stored and could not be distinguished
from a no-op.

Consequence: re-seeding orphans the previous seed. optio cannot prune it
(no owner concept — it does not know which seed the new one supersedes), so the
app deletes the old seed via `delete_seed` after recording the new id.

## Seed store

New collection `{prefix}_claudecode_seeds`. One document per seed:

```python
{
  "_id":            ObjectId,     # also the seed_id (hex string form)
  "createdAt":      datetime,     # UTC
  "blobId":         ObjectId,     # GridFS — encrypted tar.gz of the env subset
  "manifestVersion": int,         # which include/exclude ruleset was baked in
}
```

No index beyond `_id` is required (lookups are by id). No auto-retention.

New module `packages/optio-claudecode/src/optio_claudecode/seeds.py`:

```python
SEED_COLLECTION_SUFFIX = "_claudecode_seeds"
SEED_MANIFEST_VERSION = 1

async def insert_seed(db, *, prefix, blob_id) -> str          # returns seed_id (hex)
async def load_seed(db, *, prefix, seed_id) -> dict | None
async def delete_seed(db, *, prefix, seed_id) -> ObjectId | None   # returns blobId to delete, or None
async def list_seeds(db, *, prefix) -> list[dict]             # {seedId, createdAt} — ops/debug
```

`delete_seed` removes the Mongo doc and returns its `blobId` so the caller
removes the GridFS blob (mirrors the snapshot prune contract). `delete_seed` /
`list_seeds` are called by the consuming app directly (owner-agnostic admin
ops). The blob itself is removed via the same GridFS bucket the snapshots use.

## Seed manifest (capture-time triage)

The include/exclude decision is applied **at capture** — the seed blob contains
only environment files, never a transcript. Defined once in `seeds.py` and
shared conceptually by capture (positive selection) and documented for
consume.

**INCLUDE (environment):**

| Path (under the task workdir) | Notes |
|---|---|
| `home/.claude/.credentials.json` | OAuth token |
| `home/.claude/settings.json` | user settings |
| `home/.claude/mcp-needs-auth-cache.json` | MCP auth state |
| `home/.claude/plugins/**` | installed plugins + marketplaces (the bulk; included for a self-contained, deterministic environment with no network dependency at launch) |
| `home/.claude.json` | global identity/config: `oauthAccount`, `userID`, onboarding flags, etc. Captured verbatim; the `projects`-key transform is applied at **consume**, not capture. |

**EXCLUDE (session / regenerable):**

`home/.claude/projects/**` (conversation transcript + `memory/`),
`home/.claude/sessions/**`, `home/.claude/history.jsonl`,
`home/.claude/cache/**`, `home/.claude/backups/**`,
`home/.claude/.last-update-result.json`, and `home/.claude/{telemetry,todos,shell-snapshots}/**`
if present. All per-task workdir files (AGENTS.md, optio.log, resume.log,
context.txt, deliverables/) are likewise excluded — a seed is HOME-only.

Note the `projects` naming collision: the **`.claude.json` file's top-level
`projects` JSON key** (a map of `cwd → {trust, allowedTools, mcp…}`) is
included (inside `.claude.json`) and rekeyed at consume; the
**`home/.claude/projects/` directory** (transcripts) is excluded. They are
different things.

## Capture flow (`_capture_seed`)

Runs inside `run_claudecode_session`'s `finally`, before `cleanup_taskdir`
(same bracket as snapshot capture), gated on `config.on_seed_saved is not None`.
Mirrors `_capture_snapshot`'s failure semantics (catches, logs via
`report_progress`, does not propagate or block teardown).

Pseudo-code:

```python
1. # tar ONLY the INCLUDE manifest paths from home/ that exist
   seed_bytes = await _archive_seed_env(host)        # tar -czf … <include paths…>

2. encrypt = config.session_blob_encrypt or (lambda b: b)
   payload = encrypt(seed_bytes)

3. async with ctx.store_blob("seed") as w:
       await w.write(payload); blob_id = w.file_id

4. seed_id = await insert_seed(ctx._db, prefix=ctx._prefix, blob_id=blob_id)

5. await config.on_seed_saved(seed_id)               # await if coroutine, else call
```

`_archive_seed_env` builds the tar from the INCLUDE list, skipping paths that
do not exist (a vanilla session that installed no plugins simply omits
`plugins/`). The callback fires only after the blob and Mongo doc are durably
written — so a failed capture never fires it, and the app correctly concludes
no seed exists.

## Consume flow (`_merge_seed`)

Runs on the **seeded fresh** path, before launch, after the fresh workdir and
HOME skeleton exist. The seed extract is deliberately dumb; the only transform
is the cwd rekey.

Pseudo-code:

```python
1. seed = await load_seed(ctx._db, prefix=ctx._prefix, seed_id=config.seed_id)
   if seed is None:
       raise RuntimeError(f"seed_id {config.seed_id!r} not found")   # fail loud

2. payload = await _read_blob_bytes(ctx, seed["blobId"])
   decrypt = config.session_blob_decrypt or (lambda b: b)
   plain = decrypt(payload)                  # decrypt failure propagates (no fallback)

3. await _extract_seed(host, plain)          # untar over <workdir>/home/

4. await _rekey_claude_json_projects(host)   # rewrite .claude.json projects key → new cwd
```

`_rekey_claude_json_projects` reads `home/.claude.json`, and if its `projects`
map has exactly one entry under some old cwd, rewrites that key to the new
task's cwd (`host.workdir`), preserving the value (trust flags, `allowedTools`,
MCP enablement). This carries the trust-dialog acceptance and tool permissions
to the new directory so an autonomous task is not blocked by claude's
trust prompt. If the map is empty or has unexpected shape, it is left as-is
(a fresh trust prompt is the safe fallback, and `--permission-mode` may already
bypass it).

Ordering with existing fresh-start steps: any consumer-provided
`credentials_json` / `claude_config` are still planted first (current
behavior); the seed merge then overlays them, so seed-provided environment
wins. AGENTS.md is written after the merge, then claude launches without
`--continue`.

## Resume path changes

Resume is unchanged except the **D3 safety**: after restoring the snapshot,
if `home/.claude/projects/` contains no `*.jsonl` transcript, launch without
`--continue` (there is nothing to continue; passing `--continue` would make
claude exit at startup). `seed_id` and `on_seed_saved` are ignored on resume
(the snapshot already carries the full environment) with a logged warning.

## Vanilla session

A fresh launch with no `seed_id` and (typically) `on_seed_saved` set is the
seed-bootstrap session: claude starts blank, the operator logs in and configures
via the interactive ttyd TUI, then stops the task; `_capture_seed` runs on
teardown. No credentials need be planted; "Not logged in" in the TUI is the
expected starting state for this mode.

## Edge cases

- **Unknown `seed_id`.** `load_seed` returns None → `_merge_seed` raises →
  the session fails loudly. We do not silently fall back to vanilla; a missing
  seed is a consumer error worth surfacing.
- **Seed decrypt failure.** Propagated, no fallback — same policy as snapshots
  (evidence of tampering or key rotation).
- **Capture failure.** Caught, logged, `on_seed_saved` not fired, teardown
  continues. The app sees no new id → no seed recorded.
- **Vanilla session with nothing installed.** `_archive_seed_env` omits absent
  paths; the seed still captures whatever exists (at least `.credentials.json`
  + `.claude.json` once logged in).
- **`.claude.json` projects map empty or multi-entry.** Rekey leaves it
  untouched; fresh trust prompt is the safe fallback.
- **Resume + `seed_id` both set.** Resume wins; `seed_id` ignored (logged).

## Testing

All tests under `packages/optio-claudecode/tests/`, reusing the existing shim
infrastructure (`fake_claude.py`, `claude-shim.sh`, `ttyd-shim.sh`, conftest).
MongoDB-via-Docker, same as the resume tests. New `fake_claude` behavior:
write a few INCLUDE and EXCLUDE files under `$HOME/.claude` (and a
`$HOME/.claude.json` with a `projects` map keyed to the run's cwd) so capture
and merge can be asserted without a real claude.

New test modules:

- `tests/test_seeds.py` — Mongo helpers: `insert_seed` returns a hex id;
  `load_seed` round-trips; `delete_seed` removes the doc and returns the
  `blobId`; `list_seeds` lists `{seedId, createdAt}`.
- `tests/test_session_seed_capture.py` — run a fresh session with
  `on_seed_saved` set against `fake_claude` that planted env + session files;
  assert the callback fired with a hex id, a seed doc + blob exist, and the
  seed tar contains **only** INCLUDE paths (no `projects/`, `sessions/`,
  `history.jsonl`, `cache/`).
- `tests/test_session_seed_consume.py` — capture a seed, then launch a second
  fresh session (different `process_id`) with that `seed_id`; assert (via a
  `before_execute` probe) that `home/.claude/.credentials.json`,
  `settings.json`, and `plugins/` are present in the new workdir, that
  `home/.claude.json`'s `projects` map is rekeyed to the new cwd, and that
  `home/.claude/projects/` (transcript) was NOT restored.
- `tests/test_session_seed_unknown_id.py` — fresh launch with a bogus
  `seed_id` raises and does not fall back to vanilla.
- `tests/test_seed_blob_encryption.py` — capture with a non-identity
  `session_blob_encrypt`; assert the stored blob is the transformed bytes and
  consume round-trips with the matching `decrypt`.
- `tests/test_seed_config.py` — `seed_id` / `on_seed_saved` default to None;
  setting them does not break existing validation.
- Extend `tests/test_session_resume.py` (or add
  `test_session_resume_no_transcript.py`) for the **D3** safety: a snapshot
  with no transcript launches without `--continue`.

## Demo task update

`packages/optio-demo/src/optio_demo/tasks/claudecode.py` gains a second task (or
a parameterization) demonstrating the seed lifecycle: a "setup" task with
`on_seed_saved` wired to a callback that prints/records the id, and the main
demo task accepting an optional `seed_id` (from an env var) so an operator can:
run setup → log in → stop → copy the printed `seed_id` → relaunch the demo with
it and observe a logged-in, configured, fresh session. Crypto hooks left None
(plaintext), matching the existing demo. Part of the implementation plan, not a
follow-up.

## Out of scope (deferred)

- **D2** — claude exiting at startup should fail the task rather than letting
  ttyd respawn forever. Framework-level; tracked separately.
- Caller-supplied / overwrite seed ids.
- Optio-side seed GC / retention.
- Cross-host seed migration tests.
- Moving `.claude.json` out of the plaintext snapshot workdir blob into the
  encrypted session blob (a pre-existing snapshot placement nit, unrelated to
  seeds).
```
