# Pooled / Leased Claude Code Seeds -- optio-claudecode Plumbing (Spec B1)

This spec was written against the following baseline:

**Base revision:** `c542dec0669fdc40ed83a8ae95801704ff2ba02b` on branch `main` (as of 2026-06-05T17:47:55Z)

## Summary

This is **Spec B1** of the "guardian of seeds" integration -- the optio-claudecode
(library) plumbing that lets a claudecode session draw its seed from a leased
pool and verify/refresh it before use. It is the consumer-side foundation that
the excavator app (**Spec B2**: pool-uuid + migration, the `gimme` provider,
stats RPC + actions, UI) builds on.

It builds on two shipped pieces: the generic pool/lease layer (`acquire`/
`renew_lease`/`release`/`mark_seed_status`/`assign_to_pool`/`list_pool` +
`LEASE_TTL_SECONDS=60`, commit `695918c`) and credential save-back (the
`cred_watcher` keepalive loop + in-place `refresh_seed`).

Three pieces of plumbing:
1. **Interface widening** -- a task may provide a *seed provider* instead of a
   fixed `seed_id`; the session resolves it at launch.
2. **Lease lifecycle in the session** -- renew the lease on every keepalive tick,
   abort on lease-loss, release at teardown.
3. **Host-free seed verification** -- `verify_and_refresh_seed`: validate /
   refresh-and-save-back / fetch account + usage / stamp metadata, all from the
   decrypted seed blob with no session host. Reused by B2's `gimme` and its
   verify-free action.

## Background (current state)

- `ClaudeCodeTaskConfig.seed_id: str | None` (`types.py:95`). On a fresh seeded
  session, `session.py` calls `merge_seed(seed_id, CLAUDE_SEED_MANIFEST)`; on
  resume it overlays `CLAUDE_CRED_MANIFEST`.
- The save-back keepalive is `cred_watcher.run_credential_watcher` (polls every
  `CRED_WATCH_INTERVAL_S=10.0`, saving rotated creds back to the seed). It runs
  while `seed_id` is set, started before the tmux-alive loop and cancelled after,
  with a final backstop in teardown.
- `account.py` has `_fetch_profile_sync(access_token)` (GET `/api/oauth/profile`),
  `format_account_summary(profile)`, and `resolve_account_summary(host)` (reads
  creds from a session host; no refresh).
- OAuth facts (verified 2026-06-05): refresh tokens rotate single-use;
  `POST /api/oauth/validate` (Bearer) -> `{valid, account_uuid, organization_uuid}`
  / `401` invalid; `GET /api/oauth/usage` -> per-bucket `{utilization (percent),
  resets_at}` for `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`,
  ... ; `GET /api/oauth/profile` -> account name/email/plan. All need the
  `claude-cli/<ver> (external, cli)` User-Agent.

## Goals

1. A task can hand the session a provider that yields a seed_id per launch (or
   signals unavailability), without the session knowing about pools.
2. While a pooled session runs, its lease is kept alive; if the lease is lost,
   the session aborts; at teardown the seat is released.
3. A seed's liveness, account, and usage can be checked + refreshed from the
   seed blob alone (no host), saving rotated creds back, and stamping the raw
   results as seed metadata.
4. No behavior change for the existing fixed-`seed_id` (setup / legacy / test)
   path.

## Non-goals (Spec B2 or deferred)

- The `gimme` provider itself, pool-uuid/migration, setup enrollment, stats RPC,
  the add/bury/verify actions, and all UI -- **Spec B2**.
- In-session mid-run detection of a seed going dead/limited while a session is
  live (e.g. parsing claude output for 401/rate-limit) -- **deferred**; gimme
  verifies only at checkout.
- Pool-layer API growth beyond the one generic op B1 needs: B1 adds
  `declare_metadata(seed_id, dict)` (a `$set`-merge into the seed's `metadata`
  subdoc) to optio-agents, because `verify_and_refresh_seed` calls it. Extending
  `list_pool` to return the `metadata` subdoc (for B2's stats aggregation) is
  **Spec B2**. No `metadata_filter` is ever added (the C-cache approach keeps
  `acquire` unchanged).

## Design

### 1. Seed provider interface

`ClaudeCodeTaskConfig.seed_id` widens to accept a provider:

```python
SeedProvider = Callable[[str], Awaitable[str]]   # (process_id) -> seed_id
seed_id: str | SeedProvider | None = None
```

At launch the session resolves it:
- `None` -> unseeded (unchanged).
- `str` -> a fixed seed_id; **no lease management** (setup / legacy / tests).
- callable -> `await provider(ctx.process_id)`; the returned `str` is the
  seed_id and the session treats it as **leased** (the provider already holds
  the lease under `holder = ctx.process_id`, and already verified/refreshed it).

**Unavailability is an exception, not a sentinel return.** When no usable seed
exists, the provider raises `SeedUnavailableError(message)` with a failure-mode-
specific message (B2's `gimme` distinguishes "no free seeds" from "all under
limitation"). The session lets it propagate, so the process dies with that exact
message. (This realizes the "process dies with a different message per failure
mode" intent more cleanly than overloading the return string.)

A resolved provider seed is treated as leased: the session records
`leased_seed_id = <resolved>` and `holder = ctx.process_id`.

### 2. Lease lifecycle in the session

The lease is acquired by the provider (B2); the session owns only renewal and
release, gated on `leased_seed_id is not None`:

- **Renew** -- the `cred_watcher` keepalive loop already polls every
  `CRED_WATCH_INTERVAL_S` (10s, well under `LEASE_TTL_SECONDS`=60). Extend it to
  also call `renew_lease(seed_id, holder)` each tick. If `renew_lease` returns
  `False`, the lease was lost (a >TTL stall let another holder steal it; the
  token may now be rotating under that holder) -> the watcher signals the
  session to **abort** (cooperative cancel), logging "lease lost". The keepalive
  loop thus does three jobs for a leased session: save-back, renew, lost-lease
  abort.
- **Release** -- in teardown, after the watcher is cancelled and claude is gone,
  `release(seed_id, holder)` frees the seat immediately. TTL is the backstop if
  release is skipped (crash). Holder-guarded, so it never frees a seat a new
  holder now owns.

The fixed-`str` path does none of this (no lease exists).

### 3. Host-free seed verification (`verify_and_refresh_seed`)

A new optio-claudecode function operating on the seed blob (no host), reused by
B2's `gimme` (per checkout) and its verify-free action:

```python
async def verify_and_refresh_seed(
    db, *, prefix, suffix, seed_id, encrypt, decrypt,
) -> VerifyResult
```

`VerifyResult` carries `alive: bool`, the raw `usage: dict | None`, and
`account: dict | None` (`{uuid, summary}`). Logic:

1. Decrypt the seed blob, read `claudeAiOauth` from `.claude/.credentials.json`.
2. **Liveness/refresh** (the decision tree fixed during brainstorming):
   - access `expiresAt` in the future -> `validate(access)`: valid -> alive, no
     refresh; invalid -> refresh.
   - access expired -> refresh.
   - `refresh(refresh_token)`: `200` -> **save the new creds back into the seed
     blob immediately** (host-free, see below) -> alive; `4xx invalid_grant` ->
     `alive = False` (dead).
3. If alive: `fetch_usage(access)` (raw) and `profile`/account summary, in
   parallel.
4. `declare_metadata(seed_id, {"usage": <raw>, "usageFetchedAt": now,
   "account": {uuid, summary}})` -- stamp the **raw** usage JSON (no parsing at
   store time; all interpretation is the caller's, test-side) plus account.
5. Return `VerifyResult`. The function never decides "limited" -- the caller
   interprets `usage` against its `models_required` (limited = a relevant bucket
   `utilization >= 100` with `resets_at > now`).

Supporting primitives (optio-claudecode unless noted):
- `validate_token(access_token) -> bool` -- POST `/api/oauth/validate`.
- `fetch_usage(access_token) -> dict` -- GET `/api/oauth/usage`.
- `refresh_oauth_token(refresh_token) -> dict | None` -- POST `/v1/oauth/token`;
  `None` on `invalid_grant`. (The save-back work proved the wire shape.)
- `account.py` `_fetch_profile_sync` / `format_account_summary` reused for the
  account summary; factor a host-free `summarize_profile(access_token)` from the
  existing host-bound `resolve_account_summary`.
- **Host-free credential write into a seed** -- the save-back `refresh_seed`
  re-archives from a *host*; here the new creds come from the OAuth response, so
  add a host-free path that rewrites the `.credentials.json` member in the seed
  blob from given bytes: decrypt -> overlay the member via the existing
  `_merge_tar_members` -> re-encrypt -> swap blob (the same crash-safe
  store-new / repoint / delete-old ordering as `refresh_seed`). It reads/writes
  the GridFS blob from `db` directly (no `ProcessContext`), since the verify
  path runs engine-side without a session. The generic member-overwrite belongs
  in optio-agents next to `refresh_seed`; the claudecode wrapper supplies the
  `.claude/.credentials.json` path.

All HTTP uses the `claude-cli/<ver> (external, cli)` User-Agent and runs sync
`urllib` in an executor (matching `account.py`); every call is best-effort with
typed failure (network/HTTP error during usage/profile -> that field is `None`,
never fatal; a failed *refresh* is the only thing that marks a seed dead).

## Error handling

- Provider raises `SeedUnavailableError` -> propagates as the process failure
  with its message; no lease to release (none acquired).
- `renew_lease` False mid-session -> cooperative abort (the session's existing
  cancellation path), logged; teardown still runs (release is a holder-guarded
  no-op since the seat is no longer ours).
- `verify_and_refresh_seed`: a refresh `invalid_grant` -> `alive=False` (caller
  marks dead); usage/profile/network errors -> those fields `None`, `alive`
  unaffected; decrypt failure propagates (tamper/key-rotation), as elsewhere.
- The host-free credential write uses the crash-safe blob swap (store new ->
  repoint doc -> delete old): a crash leaves at most an orphan blob.

## Testing (unit; existing Mongo + LocalHost fixtures)

- Provider resolution: a `str` seed_id behaves as today (no lease calls); a
  callable is awaited with the process_id and its result used as the seed_id; a
  provider raising `SeedUnavailableError` aborts the session with that message
  and acquires/releases nothing.
- Keepalive renews the lease each tick for a leased session; a `renew_lease`
  returning False aborts the session; teardown calls `release` with the holder;
  the fixed-`str` path makes no renew/release calls.
- `verify_and_refresh_seed`: with a fresh access token + a stubbed `validate`
  returning valid -> alive, no refresh, usage+account stamped via
  `declare_metadata`; with an expired token + a stubbed refresh `200` -> creds
  saved back into the seed (re-merge yields the new token) and alive; with a
  refresh `invalid_grant` -> `alive=False` and no creds change; usage/profile
  network error -> those fields None, alive unaffected.
- Host-free credential write: overwrites `.credentials.json` in an existing seed
  blob from given bytes, leaves other members intact, swaps the blob crash-safely
  (seed_id stable, old blob deleted), and is reflected by a subsequent
  `merge_seed`.

## Affected files

- `packages/optio-claudecode/src/optio_claudecode/types.py` -- `seed_id` type +
  `SeedProvider` alias.
- `packages/optio-claudecode/src/optio_claudecode/session.py` -- provider
  resolution, leased-seed tracking, lease renew/release/abort wiring,
  `SeedUnavailableError` propagation.
- `packages/optio-claudecode/src/optio_claudecode/cred_watcher.py` -- the
  keepalive tick also renews the lease and signals abort on lost lease.
- `packages/optio-claudecode/src/optio_claudecode/account.py` (or a new
  `oauth.py`) -- `validate_token`, `fetch_usage`, `refresh_oauth_token`,
  `summarize_profile`, and `verify_and_refresh_seed`.
- `packages/optio-agents/src/optio_agents/seeds.py` -- `declare_metadata`
  (generic `$set`-merge into `metadata`) + the generic host-free member-overwrite
  (sibling to `refresh_seed`), `db`-based blob I/O.
- Tests under `packages/optio-claudecode/tests` and `packages/optio-agents/tests`.
- No excavator changes (Spec B2).
