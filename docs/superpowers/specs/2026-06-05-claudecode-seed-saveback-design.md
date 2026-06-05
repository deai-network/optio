# Claude Code Seed Credential Save-Back

This spec was written against the following baseline:

**Base revision:** `c542dec0669fdc40ed83a8ae95801704ff2ba02b` on branch `main` (as of 2026-06-05T12:31:08Z)

## Summary

Claude Code OAuth refresh tokens are **single-use / rotating**: every successful
refresh issues a new refresh token and immediately invalidates the one used
(verified empirically 2026-06-05 — reusing a spent refresh token returns
`400 invalid_grant`). A claudecode seed stores a captured credential pair; once
the in-session claude refreshes, the token stored in the seed is dead. Today the
free-style analyze task **never writes refreshed credentials back to the seed**,
so a seed dies after the first refresh and every later session fails with
`401 / Please run /login`.

This spec adds **credential save-back**: the seed becomes the single source of
truth for credentials, kept current by writing refreshed credentials back into
the existing seed (in place) whenever the in-session credentials file changes,
plus a final backstop at teardown. On resume, current credentials are overlaid
from the seed onto the snapshot-restored home, so a stale snapshot can never
carry a dead token.

**Out of scope (deferred to a later spec):** concurrent use of one seed across
parallel sessions. Rotation makes that unfixable by save-back alone — two
concurrent refreshes race, the first wins, the rest get `invalid_grant`. That
requires a seed pool + exclusive lease manager ("guardian of seeds"), specced
separately.

## Background — current lifecycle

- `customers.agentConfig.claudeCode` = a single seed id per customer.
- **Setup task** (`engine/agents/claudecode_setup.py`): user logs in via ttyd;
  on exit `capture_seed` mints a **new** immutable seed doc and `on_seed_saved`
  repoints `agentConfig.claudeCode`. Unchanged by this spec.
- **Free-style task** (`engine/free_style/claudecode_task.py`): reads `seed_id`
  from the customer, passes it as `config.seed_id`, sets **no** `on_seed_saved`.
  `session.py:350` guards capture on `on_seed_saved is not None`, so free-style
  **never re-captures** → no save-back. This is the bug.
- **Seeds** live in `gm_claudecode_seeds`; the encrypted blob (age via
  `engine.credentials`) is a tar.gz of the `home/.claude` environment subset
  described by `CLAUDE_SEED_MANIFEST`. Capture = `optio_agents.seeds.capture_seed`,
  restore = `merge_seed`.
- **Resume**: free-style sets `supports_resume=True` and uses encrypted **session
  snapshots** (a separate collection) that *also* contain `.credentials.json`.
  On resume the home is restored from the snapshot and `seed_id` is currently
  **ignored** (`session.py:135`).

## Goals

1. Refreshed credentials are persisted back to the seed within ~10s of the
   refresh, surviving a process crash.
2. The seed is the single source of truth for credentials on **both** fresh
   start and resume.
3. No engine changes, no caller callback — optio-claudecode handles save-back
   internally; the seed id never changes.
4. Never corrupt a seed with empty/half-written credentials.

## Non-goals

- Concurrency / pool / leasing (separate spec).
- Saving back anything other than credentials (`.claude.json` project state,
  settings, etc. — explicitly excluded; only `.credentials.json` rotates).
- Resurrecting an already-dead seed (that needs the re-seed/relogin path,
  separate work).

## Design

### Two manifests (optio-claudecode)

The narrow manifest is the single definition of "what is a credential", composed
into the full manifest so the credential path is never duplicated.

```python
# narrow — credentials only; no rekey transform (creds need no rekey)
CLAUDE_CRED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[".claude/.credentials.json"],
    version=CLAUDE_SEED_MANIFEST_VERSION,
)

# full — credentials + everything needed to hit the road running
CLAUDE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=CLAUDE_CRED_MANIFEST.include + [
        ".claude/settings.json",
        ".claude/mcp-needs-auth-cache.json",
        ".claude/plugins",
        ".claude.json",
    ],
    version=CLAUDE_SEED_MANIFEST_VERSION,
    consume_transform=_rekey_claude_json_projects,
)
```

Usage:
- **Seed creation** (setup capture): full manifest.
- **Fresh start** (`merge_seed`): full manifest.
- **Save-back** (`refresh_seed`): narrow manifest.
- **Resume credential injection** (`merge_seed`, overlay): narrow manifest.

### optio-agents — generic, agent-agnostic changes

**1. Extraction respects `manifest.include`.** Today `_extract_seed` untars the
whole blob; `manifest.include` is only consulted at capture. Change `_extract_seed`
to extract exactly the members in `manifest.include` (full manifest → all stored
members, since they match what was captured; narrow manifest → only credentials).
Implemented by passing the member paths to `tar -xzf <f> -C <home> <members…>`
(a directory member like `.claude/plugins` extracts recursively). Extraction is
**overlay** — it overwrites listed members and never deletes others, so a narrow
overlay onto a resumed home only rewrites `.credentials.json`.

`merge_seed` passes `manifest.include` through to `_extract_seed`. `consume_transform`
still runs after extract (None for the narrow manifest).

**2. `refresh_seed` — in-place credential merge into an existing seed.**

```python
async def refresh_seed(ctx, host, *, seed_id, manifest, suffix, encrypt, decrypt) -> None:
    # 1. load doc (KeyError if unknown); read + decrypt current blob
    # 2. archive manifest.include from the live host home (_archive_include)
    # 3. in memory: open old tar.gz, overwrite the archived members, re-tar.gz
    # 4. encrypt; store as a NEW blob (ctx.store_blob)
    # 5. atomically update doc.blobId -> new blob; set updatedAt
    # 6. delete the OLD blob
```

Needs **both** `encrypt` and `decrypt` (capture needs only encrypt, merge only
decrypt). The member overwrite is in-memory Python `tarfile`; only the
host-archive step touches the host (small — just the credentials file).

**Crash-safe blob swap order:** store new blob fully → update `doc.blobId` →
delete old blob. Any crash leaves at worst an orphan GridFS blob (harmless),
never a doc pointing at a half-written blob. (A future blob reaper can GC
orphans; out of scope.)

A new internal helper `update_seed_blob(db, …, seed_id, new_blob_id)` performs
the doc `$set` of `blobId` + `updatedAt`.

### optio-claudecode — credential watcher + resume overlay

**Credential watcher.** When `config.seed_id` is present (something to refresh),
a watcher coroutine runs alongside claude for the life of the session:

- **Baseline:** right after the home is seeded (fresh `merge_seed` or resume
  overlay), record the SHA-256 of `home/.claude/.credentials.json`.
- **Poll:** every 10s, read that file from the host, hash it. On change **and**
  validity (parseable JSON with a non-empty `claudeAiOauth.refreshToken`), call
  `refresh_seed(manifest=CLAUDE_CRED_MANIFEST)` and update the baseline hash.
  Skip on missing/empty/malformed/logged-out (guards against the empty-seed
  corruption seen in seed creation).
- **Final backstop:** in `finally`, before cleanup, run one last check-and-
  save-back (catches a refresh in the last <10s). Then the watcher is cancelled.

The watcher is cancel-safe and torn down in `finally`; a `refresh_seed` failure
is logged and never disturbs teardown. The watcher works identically on
LocalHost and RemoteHost (only needs `read_file`; no inotify).

**Resume credential overlay (behavioral change).** Today resume ignores
`seed_id` (`session.py:135`). New rule: resume still skips the **full** merge,
but when `seed_id` is present it performs a **narrow** `merge_seed(
manifest=CLAUDE_CRED_MANIFEST)` overlay *after* the snapshot is restored. This
overwrites the snapshot's `.credentials.json` with the seed's current
credentials, so a stale snapshot can never carry a dead token. The
warning at `session.py:135` is updated to reflect that `seed_id` is now used
for the credential overlay (only `on_seed_saved` remains ignored on resume).

### End-to-end flows

| Path | Credentials laid down by | Save-back during session |
|------|--------------------------|--------------------------|
| Fresh start | `merge_seed(full)` | watcher → `refresh_seed(narrow)` + final |
| Resume | snapshot restore + `merge_seed(narrow)` overlay | watcher → `refresh_seed(narrow)` + final |

Single token lineage stays consistent: whichever path runs, the seed is the
authority for credentials, and the watcher keeps the seed current.

## Error handling

- `refresh_seed` on unknown `seed_id` → `KeyError` (no silent fallback, matching
  `merge_seed`).
- Decrypt failure (tamper / key rotation) propagates.
- Invalid/empty/missing live credentials → watcher skips (no save-back), logs at
  debug; never overwrites a good seed with garbage.
- `refresh_seed` exceptions during the poll or final backstop are logged and
  swallowed — save-back is best-effort and must never crash the session or
  block teardown.
- Crash during blob swap → orphan blob only; seed remains readable.

## Testing

optio-agents (unit, with a Mongo + GridFS test fixture):
- `merge_seed` with the narrow manifest extracts only `.credentials.json` and
  leaves other home files untouched (overlay semantics).
- `merge_seed` with the full manifest still extracts every stored member.
- `refresh_seed` replaces credentials in an existing seed: re-`merge_seed`
  yields the new credentials; `seed_id` unchanged; old blob deleted, new blob
  present; `updatedAt` set.
- `refresh_seed` crash-safety: simulate failure between store-new and doc-update
  → doc still points at a valid (old) blob.
- `refresh_seed` unknown seed → `KeyError`.

optio-claudecode (unit + a local-host integration test):
- Watcher saves back when the credentials file content changes; no save-back
  when unchanged (baseline hash).
- Watcher skips on empty / malformed / missing / no-refresh-token credentials.
- Final backstop saves back a change made after the last poll.
- Watcher does not run when `seed_id` is None.
- Resume overlay: a seed with newer credentials overwrites a snapshot's stale
  `.credentials.json`; non-credential home files from the snapshot are
  preserved.

No engine tests required — engine is unchanged.

## Affected files

- `optio-agents/src/optio_agents/seeds.py` — `_extract_seed` member filter,
  `merge_seed` passthrough, new `refresh_seed`, `update_seed_blob` helper.
- `optio-claudecode/src/optio_claudecode/seed_manifest.py` — `CLAUDE_CRED_MANIFEST`,
  compose full manifest from it.
- `optio-claudecode/src/optio_claudecode/session.py` — credential watcher
  lifecycle, final backstop, resume narrow overlay, `session.py:135` rule update.
- Tests under `optio-agents/tests` and `optio-claudecode/tests`.
- **No excavator/engine changes.**
