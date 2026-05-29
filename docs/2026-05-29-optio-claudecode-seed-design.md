# Seed Support — Generic Engine (optio-host) + optio-claudecode Adopter

This spec was written against the following baseline:

**Base revision:** `4f2f00a3a7136bf66f728751152f29ec4dfb89d0` on branch `main` (as of 2026-05-29T02:01:10Z)

## Summary

Adds **seed support**: a way to start a *fresh* agent session that is already
logged-in and configured, without continuing any prior conversation — the mode
the resume feature could not provide.

A **seed** is a stored, encryptable tarball of the *environment* subset of an
agent's isolated HOME — credentials, settings, auth caches, installed plugins,
global config — with no conversation/session data. A new task launched with a
`seed_id` merges that environment into its fresh workdir, writes its own
per-task AGENTS.md, and launches the agent normally (no continue/resume). Seeds
are created by a dedicated capture: launch a vanilla session, log in / configure
interactively, then stop it — on teardown the framework captures the
environment-only files and returns a generated `seed_id` via a callback.

The seed **mechanism is generic and lives in `optio-host`**, parameterized by a
small agent **adapter** (HOME layout + file manifest + a consume-time
transform). **optio-claudecode is the first adopter.** **optio-opencode will
adopt it in the next PR** (first per-task HOME/XDG isolation, then seeding) —
opencode has the same multi-user need but is currently blocked on isolation
(see "opencode parity").

Seeds are keyed by an opaque, optio-generated id so the system can serve many
users (each with a personal login). optio stays owner-agnostic; the consuming
application owns the `user → seed_id` mapping and seed lifecycle.

## Motivation

Live testing of the resume feature established that none of the existing launch
modes is fit for spawning real work on new tasks:

1. **Vanilla fresh start** — no credentials, no login, no config. Unfit unless
   the consumer plants everything via config, and plain config cannot reproduce
   an interactive OAuth login, installed plugins, or MCP auth.
2. **Resume of a session saved *before* login** — `claude --continue` launches
   with no auth and nothing to continue, exits immediately, and (because ttyd
   respawns the pty program per client connect) the frontend flaps forever.
3. **Resume of a session saved *after* login** — works, but it is the *same*
   conversation: `--continue` restores the agent's belief that it already read
   AGENTS.md at session start, so a new per-task AGENTS.md is not re-read. Wrong
   tool for a *new* task.

The missing capability is **"seeded environment, fresh conversation"**: an
authenticated, configured agent that begins a brand-new conversation and reads
the task's AGENTS.md correctly. The structural insight: resume conflated two
independent axes — **identity/environment** (wanted for any real work) and
**conversation** (wanted only for true same-task resume). Seeds provide the
identity axis without the conversation axis.

This builds on `docs/2026-05-29-optio-claudecode-resume-design.md` and the fixes
on `feat/optio-claudecode-resume` (workdir-snapshot junk exclusion;
process-group reaping on cancel).

## Goals

- Start a fresh, logged-in, configured session from a stored seed, beginning a
  new conversation that reads the per-task AGENTS.md.
- Capture a seed from an interactively-configured session, returning a generated
  id via callback so the consuming app can record it.
- **Generic mechanism in `optio-host`**, agent-specific behavior supplied by a
  small adapter — reusable by opencode after it gains HOME isolation.
- Per-user seeds: opaque ids, optio owner-agnostic; app owns `user → seed_id`
  and GC.
- Encrypt seeds at rest using the existing `session_blob_encrypt` /
  `session_blob_decrypt` hooks (seeds carry credentials).
- Capture-time file triage: the seed blob contains only environment files —
  never a conversation transcript.

## Non-goals (v1)

- A live command channel into running tasks. Capture is triggered by declaring
  intent at launch and stopping the task. (`clamator` queued-RPC exists but is
  intentionally not used here.)
- Caller-supplied / overwrite seed ids. Every capture mints a new id; a *new* id
  is the unambiguous success signal.
- Optio-side seed retention/GC. optio has no owner concept; the app GCs via
  `delete_seed`.
- opencode adoption itself (next PR — needs HOME isolation first). This spec
  only ensures the engine is built generic enough to adopt.
- **D2** (agent exiting at startup should fail the task, not flap) — follow-up;
  the seed model + D3 remove its causes.

## Architecture: generic engine + agent adapter

The reusable mechanism lives in **`optio-host`**; agents supply an adapter.

**`optio_host/seeds.py` — generic engine:**

```python
@dataclass(frozen=True)
class SeedManifest:
    home_subdir: str                       # HOME relative to workdir, e.g. "home"
    include: list[str]                     # env paths relative to home_subdir
    # consume-time fixup applied after extract (e.g. rekey config to new cwd).
    # None = no transform. Receives the Host (host.workdir is the new cwd).
    consume_transform: "Callable[[Host], Awaitable[None]] | None" = None

# Mongo helpers, parameterized by the agent-specific collection suffix:
async def insert_seed(db, *, prefix, suffix, blob_id, manifest_version) -> str   # seed_id (hex)
async def load_seed(db, *, prefix, suffix, seed_id) -> dict | None
async def delete_seed(db, *, prefix, suffix, seed_id) -> ObjectId | None         # returns blobId to remove
async def list_seeds(db, *, prefix, suffix) -> list[dict]                        # {seedId, createdAt}

# Engine (operate via ctx for blob store + db):
async def capture_seed(ctx, host, *, manifest, suffix, encrypt) -> str           # tar include -> encrypt -> store -> insert; returns seed_id
async def merge_seed(ctx, host, *, seed_id, manifest, suffix, decrypt) -> None   # load -> decrypt -> extract include -> consume_transform
```

`capture_seed` tars only `manifest.include` (paths under
`<workdir>/<home_subdir>/`) that exist, encrypts, stores the blob, inserts the
Mongo doc, returns the generated `seed_id`. `merge_seed` loads/decrypts,
extracts over `<workdir>/<home_subdir>/`, then runs `manifest.consume_transform`.
The engine knows nothing about claude or opencode — only the manifest does.

**optio-claudecode — the adapter and wiring:**

- Defines `CLAUDE_SEED_MANIFEST` and `CLAUDE_SEED_SUFFIX = "_claudecode_seeds"`.
- Provides the consume transform `_rekey_claude_json_projects`.
- Wires `capture_seed` / `merge_seed` into `run_claudecode_session`.
- Adds the config fields and the `--continue`-suppression for seeded fresh.
- Re-exports thin `delete_seed(db, prefix, seed_id)` / `list_seeds(db, prefix)`
  wrappers that bind `CLAUDE_SEED_SUFFIX`, so the consuming app has ergonomic
  GC calls without knowing the suffix.

This keeps agent-specific knowledge (manifest, transform, collection name,
config surface, session wiring) in the agent package, and the reusable
tar/encrypt/store/load/extract/GC engine in `optio-host`.

## Launch mode matrix (claudecode)

`run_claudecode_session` resolves the mode from `ctx.resume`, the resolved
`seed_id`, and `config.on_seed_saved`:

| Condition | Mode | Behavior |
|---|---|---|
| `ctx.resume` and a snapshot exists | **Resume** | restore full `home/.claude` + workdir; `--continue`. seed inputs ignored (logged). **D3 safety:** if the restored snapshot has no transcript, launch **without** `--continue`. |
| fresh, `seed_id` resolved | **Seeded fresh** | `merge_seed` env into fresh workdir; rekey `.claude.json` projects → new cwd; per-task AGENTS.md; launch **without** `--continue`. |
| fresh, no `seed_id` | **Vanilla fresh** | blank; per-task AGENTS.md; no `--continue`. Operator logs in here. |
| any fresh, `on_seed_saved` set | **+ seed capture** | orthogonal: on stop/teardown, `capture_seed`, then fire `on_seed_saved(seed_id)`. |

Seed capture and resume snapshotting are independent: `supports_resume` gates
snapshotting; `on_seed_saved` gates seed capture.

## Config surface and seed_id resolution

`ClaudeCodeTaskConfig` additions:

```python
seed_id: str | None = None                                          # consume (default/fallback)
on_seed_saved: Callable[[str], Awaitable[None] | None] | None = None  # capture intent + checkpoint
```

**seed_id is supplied via `config.seed_id`, baked at task-creation time.** The
launch RPC (`launch(processId, resume)`) deliberately carries no per-launch
params, and it does not need to: a task that should run on a given seed is
*created* with that seed in its config. Multi-user apps create a per-user task
(via `adhoc_define`) whose `ClaudeCodeTaskConfig(seed_id=…)` is the user's seed;
the demo regenerates its task list so each stored seed yields its own task with
the seed baked in (see "Demo usage"). No `ctx.params` channel is involved.

`on_seed_saved` is a Python callable (cannot be a launch param); its presence is
the capture intent. Both are ignored on resume (logged). Both default None, so
existing consumers are unaffected. Seed-blob encryption reuses the existing
`session_blob_encrypt` / `session_blob_decrypt` hooks (both-None → plaintext).

## Seed id generation

Every capture mints a **new, opaque, optio-generated** id (`ObjectId` hex).
Rationale: the app records `user → seed_id`; on re-seed (token expiry), a
*different* id via `on_seed_saved` is an unambiguous success signal, whereas an
in-place overwrite delivers data identical to what the app already stored and
can't be distinguished from a no-op. Consequence: re-seeding orphans the prior
seed; optio can't prune it (no owner concept), so the app deletes the old seed
via `delete_seed` after recording the new id.

## Seed store

Collection `{prefix}{suffix}` (claudecode: `{prefix}_claudecode_seeds`). One doc
per seed:

```python
{
  "_id":             ObjectId,    # seed_id is its hex form
  "createdAt":       datetime,    # UTC
  "blobId":          ObjectId,    # GridFS — encrypted tar.gz of the env subset
  "manifestVersion": int,         # which manifest ruleset was baked in
}
```

Lookups are by id; no extra index. No auto-retention. `delete_seed` removes the
doc and returns its `blobId` so the caller removes the GridFS blob (mirrors the
snapshot-prune contract); the blob is removed via the same GridFS bucket the
snapshots use.

## Seed manifest (claudecode, capture-time triage)

Include/exclude is applied **at capture** — the seed blob is environment-only,
never a transcript. `CLAUDE_SEED_MANIFEST.home_subdir = "home"`; `include`
paths are relative to that:

**INCLUDE (environment):**

| Path (under `home/`) | Notes |
|---|---|
| `.claude/.credentials.json` | OAuth token |
| `.claude/settings.json` | user settings |
| `.claude/mcp-needs-auth-cache.json` | MCP auth state |
| `.claude/plugins/` | installed plugins + marketplaces (the bulk; included for a self-contained, deterministic environment with no network dependency at launch) |
| `.claude.json` | global identity/config (`oauthAccount`, `userID`, onboarding flags, …); captured verbatim — the `projects`-key rekey happens at **consume** |

**EXCLUDE (session / regenerable):** `.claude/projects/**` (transcript +
`memory/`), `.claude/sessions/**`, `.claude/history.jsonl`, `.claude/cache/**`,
`.claude/backups/**`, `.claude/.last-update-result.json`,
`.claude/{telemetry,todos,shell-snapshots}/**`. All per-task workdir files
(AGENTS.md, optio.log, resume.log, context.txt, deliverables/) are excluded —
a seed is HOME-only.

Naming-collision note: the **`.claude.json` file's top-level `projects` JSON
key** (a map `cwd → {trust, allowedTools, mcp…}`) is included (inside
`.claude.json`) and rekeyed at consume; the **`home/.claude/projects/`
directory** (transcripts) is excluded. Different things.

## Capture flow (claudecode wiring of `capture_seed`)

Runs in `run_claudecode_session`'s `finally`, before `cleanup_taskdir` (same
bracket as snapshot capture), gated on `config.on_seed_saved is not None`.
Failure semantics mirror snapshot capture (catch, log, don't propagate, don't
block teardown — callback not fired on failure, so the app sees no seed):

```python
seed_id = await optio_host.seeds.capture_seed(
    ctx, host,
    manifest=CLAUDE_SEED_MANIFEST,
    suffix=CLAUDE_SEED_SUFFIX,
    encrypt=config.session_blob_encrypt,
)
await _call_maybe_async(config.on_seed_saved, seed_id)
```

## Consume flow (claudecode wiring of `merge_seed`)

On seeded-fresh, before launch, after the fresh workdir + HOME skeleton exist
(consumer-provided `credentials_json`/`claude_config` are planted first; the
seed merge overlays them, so seed-provided env wins):

```python
await optio_host.seeds.merge_seed(
    ctx, host,
    seed_id=effective_seed_id,
    manifest=CLAUDE_SEED_MANIFEST,
    suffix=CLAUDE_SEED_SUFFIX,
    decrypt=config.session_blob_decrypt,
)   # raises if seed_id unknown; decrypt failure propagates (no fallback)
# then write per-task AGENTS.md, launch without --continue
```

`merge_seed`'s `consume_transform` = `_rekey_claude_json_projects`: read
`home/.claude.json`; if its `projects` map has exactly one entry under some old
cwd, rewrite that key to `host.workdir`, preserving the value (trust flags,
`allowedTools`, MCP enablement) so an autonomous task isn't blocked by claude's
trust prompt. Empty/unexpected shape → left as-is (fresh trust prompt is the
safe fallback; `--permission-mode` may already bypass it).

## Resume path changes

Unchanged except the **D3 safety**: after restoring the snapshot, if
`home/.claude/projects/` has no `*.jsonl`, launch without `--continue` (nothing
to continue; passing it makes claude exit at startup). `seed_id` /
`on_seed_saved` ignored on resume (snapshot already carries full environment),
with a logged warning.

## Vanilla session

Fresh launch, no `seed_id`, typically `on_seed_saved` set: the bootstrap session.
Claude starts blank; operator logs in / configures via the ttyd TUI; stops the
task; `capture_seed` runs on teardown. No credentials need be planted; "Not
logged in" is the expected starting state for this mode.

## opencode parity

opencode has the **same multi-user need**: it supports in-app provider
connection (the `opencode auth login` / TUI flow), and the current single
`.env` API key is one shared identity — unfit for a multi-user web app where
each user logs into their own provider account.

opencode is, however, **blocked on a prerequisite**: it is **not HOME/XDG
isolated** — its credentials/keyring live in the host's real `~/.config/opencode`,
shared across all tasks. So per-user identities are impossible today. Seeding
opencode requires **first** adding per-task HOME/XDG isolation (the way
claudecode isolates `HOME=<workdir>/home`), **then** defining an opencode seed
manifest and wiring `capture_seed`/`merge_seed`.

Plan: **opencode adoption is the immediately following PR** — (1) HOME/XDG
isolation, (2) seeding via the same `optio_host.seeds` engine with an
`OPENCODE_SEED_MANIFEST` + `_opencode_seeds` suffix. This spec builds the engine
generic specifically so that adoption is manifest + wiring, not a rewrite.

Interchangeability note: the resume surface stays mirrored 1:1; the seed surface
is additive. Until opencode adopts, a consumer that needs per-user identities
uses optio-claudecode; opencode consumers keep using `opencode_config` for the
single-tenant case.

## Demo usage

The demo drives the full seed lifecycle through optio's existing surface — no
launch-param channel, no env-var hand-off, no optio change. The bake-params-at-
creation-time model plus task-list regeneration is sufficient.

**Demo-owned registry.** A demo-owned Mongo collection `{prefix}_demo_claude_seeds`
holds one record per created seed: `{seedId, name, createdAt}`. This is the
demo acting as "the app" that owns the human-facing mapping; optio's
`{prefix}_claudecode_seeds` engine store holds the actual (encrypted) seed blob.

**Setup task.** A static **"Setup Claude Code seed"** task — vanilla (no
`seed_id`), `on_seed_saved` wired. The operator launches it, logs in / configures
in the ttyd TUI, then stops it. On teardown the seed is captured and the
callback fires.

**`on_seed_saved` callback (in the demo):**
1. Compute `name = f"Config #{count + 1}"` where `count` is the current size of
   `{prefix}_demo_claude_seeds` (cosmetic numbering; a concurrent-save race may
   reuse a number — acceptable, the `seedId` is the real key).
2. Insert `{seedId, name, createdAt}` into `{prefix}_demo_claude_seeds`.
3. Trigger task-list regeneration via a **direct in-process Python call** to
   optio-core's exposed `resync` (the demo runs in-process with the engine, so
   no RPC client is needed).

**Task generation.** The demo's task-definition code reads
`{prefix}_demo_claude_seeds` and emits, per record, a task named
**"Claude Code demo — {name}"** whose `ClaudeCodeTaskConfig` has `seed_id`
**baked in**. After `resync`, these tasks appear in the dashboard.

**Operator flow:** run "Setup Claude Code seed" → log in → stop → a new
"Claude Code demo — Config #N" task appears (seed baked in) → launch it →
observe a logged-in, configured, **fresh** session. Crypto hooks left None
(plaintext), matching the existing demo.

## Edge cases

- **Unknown `seed_id`** → `load_seed` None → `merge_seed` raises → session fails
  loudly (no silent vanilla fallback; a missing seed is a consumer error).
- **Seed decrypt failure** → propagated, no fallback (tampering / key rotation).
- **Capture failure** → caught, logged, `on_seed_saved` not fired, teardown
  continues.
- **Vanilla session with nothing installed** → `capture_seed` omits absent
  include paths; captures whatever exists once logged in.
- **`.claude.json` projects map empty / multi-entry** → rekey leaves it
  untouched; fresh trust prompt is the safe fallback.
- **Resume + seed inputs both set** → resume wins; seed inputs ignored (logged).

## Testing

`optio-host` tests (`packages/optio-host/tests/`):
- `test_seeds.py` — engine + Mongo helpers against a fake manifest + a
  temp/local host: `insert_seed` returns hex; `load_seed` round-trips;
  `delete_seed` removes doc + returns blobId; `list_seeds` lists; `capture_seed`
  tars only `manifest.include` (asserts excluded paths absent); `merge_seed`
  extracts include paths and runs `consume_transform`; non-identity
  encrypt/decrypt round-trips.

`optio-claudecode` tests (`packages/optio-claudecode/tests/`, reusing the shim
infra; MongoDB-via-Docker). New `fake_claude` behavior: plant a few INCLUDE +
EXCLUDE files under `$HOME/.claude` and a `$HOME/.claude.json` with a `projects`
map keyed to the run's cwd.
- `test_session_seed_capture.py` — fresh session + `on_seed_saved`; assert the
  callback fired with a hex id, a seed doc + blob exist, and the seed tar
  contains only INCLUDE paths.
- `test_session_seed_consume.py` — capture, then a second fresh session
  (different `process_id`) with that `seed_id`; assert (via `before_execute`
  probe) credentials/settings/plugins present in the new workdir, `.claude.json`
  `projects` rekeyed to the new cwd, and `home/.claude/projects/` NOT restored.
- `test_session_seed_unknown_id.py` — bogus `seed_id` raises, no vanilla
  fallback.
- `test_seed_config.py` — `seed_id`/`on_seed_saved` default None; no validation
  break.
- D3: a snapshot with no transcript launches without `--continue` (extend
  `test_session_resume.py` or add `test_session_resume_no_transcript.py`).

## File structure

- Create `packages/optio-host/src/optio_host/seeds.py` — generic engine +
  `SeedManifest` + Mongo helpers.
- Modify `packages/optio-claudecode/src/optio_claudecode/`:
  - new `seed_manifest.py` (or in `seeds.py`) — `CLAUDE_SEED_MANIFEST`,
    `CLAUDE_SEED_SUFFIX`, `_rekey_claude_json_projects`, and the
    `delete_seed`/`list_seeds` ergonomic wrappers.
  - `types.py` — add `seed_id`, `on_seed_saved`.
  - `session.py` — seed_id resolution, capture/merge wiring, seeded-fresh
    no-`--continue`, D3 safety.
- Modify `packages/optio-demo/src/optio_demo/` — add the "Setup Claude Code seed"
  task with the `on_seed_saved` callback (writes `{prefix}_demo_claude_seeds`,
  calls `resync` in-process), and make the claudecode task-definition code emit
  one seed-baked task per record in `{prefix}_demo_claude_seeds`.

## Out of scope (deferred)

- opencode HOME/XDG isolation + seed adoption (immediately following PR).
- **D2** — agent exiting at startup should fail the task, not flap.
- Caller-supplied / overwrite seed ids; optio-side seed GC/retention.
- Cross-host seed migration tests.
- Moving `.claude.json` out of the plaintext snapshot workdir blob into the
  encrypted session blob (pre-existing snapshot nit, unrelated to seeds).
```
