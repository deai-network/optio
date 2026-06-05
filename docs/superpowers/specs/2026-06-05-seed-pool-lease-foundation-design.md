# Generic Seed-Pool / Lease Layer (Foundation)

This spec was written against the following baseline:

**Base revision:** `c542dec0669fdc40ed83a8ae95801704ff2ba02b` on branch `main` (as of 2026-06-05T16:25:29Z)

## Summary

This is **Spec A (foundation)** of the "guardian of seeds" work -- the generic,
agent-agnostic pool/lease mechanics in `optio-agents`. It adds exclusive,
crash-safe leasing over the existing seed collection so that a pool of N seeds
per owner can be shared across concurrent sessions without two sessions ever
using the same seed at once.

This matters because Claude Code OAuth refresh tokens are single-use / rotating
(see the credential-save-back spec and `[[project_claude_oauth_refresh_rotation]]`):
two concurrent sessions sharing one seed both refresh, the first wins, the rest
get `401 / invalid_grant`. Exclusive leasing is the only safe way to share a
credential lineage across parallel work.

**Scope:** this spec is the pure mechanism only -- schema + lease operations,
fully unit-testable with no excavator/claude dependency. The consumer side
(single->pool migration, lease wiring into the claudecode session lifecycle,
pool provisioning, dead-detection, the Check/Renew action and UI) is **Spec B**,
written next against this layer's API.

## Background

`optio_agents/seeds.py` already owns the generic, agent-agnostic seed store:
seed docs live in a `{prefix}{suffix}` Mongo collection (claudecode uses suffix
`_claudecode_seeds`), each with an encrypted GridFS blob, minted with an opaque
ObjectId-hex id. `capture_seed` / `merge_seed` / `refresh_seed` / `insert_seed`
/ `load_seed` / `delete_seed` / `list_seeds` / `update_seed_blob` already exist.
claudecode binds the suffix via thin wrappers in `seed_manifest.py`
(`delete_seed`, `list_seeds`, `purge_seed`). This spec extends that module in
the same agent-agnostic style.

## Goals

1. Exclusive leasing: at most one live holder per seed at any instant.
2. Crash-safe: a crashed or restarted holder's seed is reclaimed automatically,
   with no daemon and no manual intervention.
3. Host-agnostic: works whether the session runs locally or over SSH (no PID or
   host-local liveness probe).
4. Pure data layer: every operation is a single atomic Mongo op; no background
   sweeper, no multi-doc transaction.
5. Agent-agnostic: no claude/excavator knowledge; `poolKey` and `status`
   semantics are supplied by the consumer.

## Non-goals

- Lease wiring into any session lifecycle, the keepalive/renewal caller, pool
  provisioning, migration, dead-detection, Check/Renew, UI -- all **Spec B**.
- Blocking/queueing when no seed is free: `acquire` returns `None` and the
  caller's policy decides what to do.
- A background reaper as a correctness dependency (reclaim is lazy, in
  `acquire`).
- Multi-engine coordination beyond what single-doc atomicity already gives.

## Leasing model (decisions)

Self-renewing TTL lease (model "A1"), decided during brainstorming:

- A lease is `{holder, expiresAt}`. `expiresAt` is absolute; a sliding window
  re-stamped to `now + LEASE_TTL_SECONDS` on acquire and on each renewal.
- `LEASE_TTL_SECONDS = 60`. **The TTL is a fixed policy constant of this layer**
  -- never a per-call argument (a caller-set TTL is redundant across acquire/
  renew and would let a caller pin a seed). The keepalive renewal cadence (~10s)
  is the *consumer's* concern (Spec B), not this layer's; the pool only knows the
  TTL. TTL is ~6x the cadence so transient GC/SSH stalls don't cause a
  false reclaim (the expensive error: reclaiming a still-alive holder rotates the
  token out from under it; slow crash-reclaim is merely a delay).
- **Self-renewal** removes the restart-identity problem without a per-launch id:
  the specific incarnation holding the lease renews it from inside its own run.
  A crash stops renewal -> TTL reclaims. A stop+restart re-runs `acquire` fresh
  and never inherits the old in-memory lease, so it cannot wrongly renew it.
- `holder` is an opaque caller-supplied string, stable for the holder's lifetime
  (the claudecode consumer uses the session's `process_id`).
- All time stamping and comparison use the **Mongo server clock** (`$$NOW`), so
  nothing depends on the application clock and it stays correct if multi-engine
  ever happens.
- Reclaim is **lazy**: `acquire` treats `lease == null OR lease.expiresAt <= now`
  as available and atomically takes it. No sweeper is required. The engine's
  existing startup process-reconciliation is an unrelated redundant safety net,
  not a dependency.
- Selection among free seeds is **round-robin** (least-recently-leased first),
  via a `lastLeasedAt` field stamped on each acquire and an ascending sort.

## Schema additions

Added to each seed doc (existing fields: `createdAt`, `updatedAt`, `blobId`,
`manifestVersion`). All optional/back-compatible -- legacy seeds simply lack them
and read as the documented defaults.

| Field | Type | Meaning / default |
|-------|------|-------------------|
| `poolKey` | string \| absent | Pool membership (consumer-supplied; claudecode = customer id as string). Absent = unassigned/legacy -- not eligible for `acquire`. |
| `lease` | `{holder: string, expiresAt: date}` \| null | Current exclusive lease; null/expired = free. |
| `status` | `"alive"` \| `"dead"` | Leasable only when `"alive"`. Default `"alive"` on capture. Consumer sets `"dead"`. |
| `lastLeasedAt` | date \| absent | Stamped on each `acquire`; drives round-robin. Absent sorts first (never leased). |
| `lastRefreshedAt` | date \| null | Set when credentials are saved back. Today `refresh_seed` stamps `updatedAt`; Spec B decides whether to add a distinct `lastRefreshedAt` or reuse `updatedAt`. This spec only surfaces it in `list_pool` (may map to `updatedAt`). |
| `lastVerifiedAt` | date \| null | Set by Check (Spec B). |

NB: the access-token `expiresAt` is **never** stored on the seed doc for callers
-- only inside the encrypted blob -- to preserve save-back transparency (a caller
that learned the access expiry could start managing refresh timing, re-coupling
what save-back decoupled).

## API (all in `optio_agents/seeds.py`, keyed by `prefix` + `suffix`)

```python
LEASE_TTL_SECONDS = 60

async def acquire(db, *, prefix, suffix, poolKey, holder) -> str | None:
    """Atomically lease the least-recently-leased free, alive seed in the pool.
    Returns its seed_id, or None if none is available. Lazy reclaim: a seed
    whose lease is null or expired counts as free."""

async def renew_lease(db, *, prefix, suffix, seed_id, holder) -> bool:
    """Slide this holder's lease forward by LEASE_TTL_SECONDS. CAS on holder:
    returns True if renewed, False if the lease was lost (expired and
    re-acquired by someone else) -- the caller MUST then stop using the seed."""

async def release(db, *, prefix, suffix, seed_id, holder) -> None:
    """Clear this holder's lease (holder-guarded; a no-op if not held by
    `holder`). Liveness/TTL is the backstop, so a missed release is harmless."""

async def mark_seed_status(db, *, prefix, suffix, seed_id, status) -> None:
    """Set status ('alive' | 'dead'). Consumer policy decides when a seed is
    dead (e.g. on invalid_grant). A dead seed is never handed out by acquire."""

async def assign_to_pool(db, *, prefix, suffix, seed_id, poolKey) -> None:
    """Set a seed's poolKey (membership). Also the single->pool migration
    primitive used by Spec B."""

async def list_pool(db, *, prefix, suffix, poolKey) -> list[dict]:
    """Return [{seedId, status, leased: bool, lastRefreshedAt, lastVerifiedAt}]
    for the pool (UI/observability). `leased` = lease present and not expired."""

async def reap_expired_leases(db, *, prefix, suffix) -> int:
    """Clear all expired leases; return the count. Optional explicit cleanup /
    observability -- NOTHING depends on it running (acquire reclaims lazily)."""
```

### `acquire` mechanics

A single atomic `find_one_and_update`:
- **filter:** `{ "poolKey": poolKey, "status": "alive", "$expr": { "$or": [
  {"$eq": ["$lease", None]},
  {"$not": {"$gt": ["$lease.expiresAt", "$$NOW"]}} ] } }`
  (i.e. no lease, or its `expiresAt` is not in the future).
- **sort:** `{ "lastLeasedAt": 1 }` (ascending; missing sorts first) -- round-robin.
- **update (pipeline, so it can use `$$NOW`):**
  `[{ "$set": { "lease": { "holder": holder, "expiresAt": { "$add": ["$$NOW",
  LEASE_TTL_SECONDS*1000] } }, "lastLeasedAt": "$$NOW" } }]`
- returns the matched doc's id as a str, or None.

### `renew_lease` mechanics

`update_one({"_id": ObjectId(seed_id), "lease.holder": holder},
[{"$set": {"lease.expiresAt": {"$add": ["$$NOW", LEASE_TTL_SECONDS*1000]}}}])`.
Return `result.matched_count == 1`. A mismatch means the lease was lost.

### `release` mechanics

`update_one({"_id": ObjectId(seed_id), "lease.holder": holder},
{"$set": {"lease": None}})`. Holder-guarded so a stale holder cannot wipe a
lease a new holder now owns.

## Error handling

- Unknown / malformed `seed_id` in `renew_lease`/`release`/`mark_seed_status`/
  `assign_to_pool`: no match -> no-op (`renew_lease` -> False); consistent with
  the existing `load_seed`/`delete_seed` tolerance of bad ids.
- `acquire` on an empty/all-dead/all-leased pool: returns `None` (not an error).
- No locking beyond single-document atomic ops; concurrent `acquire`s for the
  same pool cannot both win the same seed (one `find_one_and_update` wins; the
  other matches the next free seed or `None`).

## Testing (unit, with the existing Mongo test fixture)

- acquire returns a seed and stamps `lease.holder`, future `expiresAt`,
  `lastLeasedAt`; a second concurrent-style acquire takes a *different* seed,
  and returns `None` once all are leased.
- acquire skips `status:"dead"` seeds and seeds with another pool's `poolKey`.
- acquire reclaims a seed whose `lease.expiresAt` is in the past (lazy reclaim),
  and prefers the least-recently-leased free seed (round-robin order).
- renew_lease by the holder slides `expiresAt` forward and returns True; renew
  by a non-holder (or after expiry+steal) returns False and does not change the
  current lease.
- release by the holder frees the seed; release by a non-holder is a no-op.
- mark_seed_status('dead') removes the seed from acquire eligibility.
- assign_to_pool sets poolKey and makes a previously-unassigned seed eligible.
- list_pool reports status / leased / timestamps correctly, including a
  not-leased result once a lease has expired.
- reap_expired_leases clears expired leases and returns the count; leaves live
  leases untouched.
- All time logic verified against the Mongo server clock (insert an expired
  lease via a past `expiresAt`, confirm acquire reclaims it).

## Affected files

- `packages/optio-agents/src/optio_agents/seeds.py` -- the constant + seven
  operations above; reuse `_collection`.
- `packages/optio-agents/tests/test_seeds.py` -- the unit tests above.
- No claudecode / excavator changes (those are Spec B).
