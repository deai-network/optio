# Generic Seed-Pool / Lease Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. **This plan is PARALLEL-SHAPED** (per the project's standing execution convention): the two implementation tasks are **edit-only** and run concurrently as a single wave -- they do NOT run tests, lint, or git. A single final **Verify phase** runs all tests, fixes any cross-task breakage, lints, and commits. Do not add per-task test/commit steps.

**Goal:** Add a generic, agent-agnostic exclusive seed-leasing layer to `optio-agents` so a pool of N seeds per owner can be shared across concurrent sessions without two ever using the same seed.

**Architecture:** Extend `optio_agents/seeds.py` (already keyed by `prefix`+`suffix`) with a `LEASE_TTL_SECONDS=60` constant and seven operations (`acquire`, `renew_lease`, `release`, `mark_seed_status`, `assign_to_pool`, `list_pool`, `reap_expired_leases`). Leasing is a self-renewing TTL using the Mongo server clock (`$$NOW`); `acquire`/`renew_lease`/`reap` are single atomic ops. Lazy reclaim (no sweeper), round-robin selection via `lastLeasedAt`.

**Tech Stack:** Python 3, asyncio, motor (Mongo), pymongo `ReturnDocument`, pytest + pytest-asyncio. Spec: `docs/superpowers/specs/2026-06-05-seed-pool-lease-foundation-design.md`.

**Test runner:** `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py -v` (needs local Mongo at `mongodb://localhost:27017`).

---

## File Structure

- `packages/optio-agents/src/optio_agents/seeds.py` -- add the constant + 7 ops (owner: Task 1).
- `packages/optio-agents/tests/test_seeds.py` -- add the pool/lease unit tests (owner: Task 2).

Disjoint files -> the two tasks run concurrently. Both transcribe the exact signatures below, so they align without a barrier; the Verify phase reconciles any drift.

**Shared contract (both tasks code to this verbatim):**

```python
LEASE_TTL_SECONDS = 60

async def acquire(db, *, prefix, suffix, poolKey, holder) -> str | None
async def renew_lease(db, *, prefix, suffix, seed_id, holder) -> bool
async def release(db, *, prefix, suffix, seed_id, holder) -> None
async def mark_seed_status(db, *, prefix, suffix, seed_id, status) -> None
async def assign_to_pool(db, *, prefix, suffix, seed_id, poolKey) -> None
async def list_pool(db, *, prefix, suffix, poolKey) -> list[dict]
async def reap_expired_leases(db, *, prefix, suffix) -> int
```

`list_pool` returns dicts shaped `{"seedId", "status", "leased", "lastRefreshedAt", "lastVerifiedAt"}`. "Leasable" = `status != "dead"` (default-alive-by-absence, realizing the spec's "default alive"). Time uses the Mongo server clock `$$NOW`.

---

## Task 1 (WAVE, edit-only): implement the pool/lease ops in `seeds.py`

**Owner files:** `packages/optio-agents/src/optio_agents/seeds.py`
**Do NOT** run tests, lint, or git. Edit only.

- [ ] **Step 1: Add the `ReturnDocument` import**

At the top of `seeds.py`, the module already imports `shlex`, `datetime`/`timezone`, etc. Add (near the other stdlib/3rd-party imports, outside the `TYPE_CHECKING` block so it is available at runtime):

```python
from pymongo import ReturnDocument
```

- [ ] **Step 2: Append the constant + seven operations**

Append at the end of `seeds.py`. Note `_collection(db, prefix, suffix)`, `datetime`, and `timezone` already exist in this module.

```python
# --- pool / lease layer ----------------------------------------------------
#
# Generic exclusive leasing over the seed collection so a pool of N seeds per
# owner can be shared across concurrent sessions without two using one seed at
# once. Self-renewing TTL lease on the Mongo server clock ($$NOW); acquire/
# renew/reap are single atomic ops; lazy reclaim (no sweeper). See
# docs/superpowers/specs/2026-06-05-seed-pool-lease-foundation-design.md.

LEASE_TTL_SECONDS = 60


def _oid_or_none(seed_id: str):
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        return ObjectId(seed_id)
    except (InvalidId, TypeError):
        return None


async def acquire(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, poolKey: str, holder: str,
) -> str | None:
    """Atomically lease the least-recently-leased free, non-dead seed in the
    pool. Returns its seed_id, or None if none is available. Lazy reclaim: a
    seed whose lease is null or whose lease.expiresAt is not in the future
    counts as free. Selection is round-robin via `lastLeasedAt` (missing sorts
    first)."""
    coll = _collection(db, prefix, suffix)
    doc = await coll.find_one_and_update(
        {
            "poolKey": poolKey,
            "status": {"$ne": "dead"},
            "$expr": {
                "$or": [
                    {"$eq": [{"$ifNull": ["$lease", None]}, None]},
                    {"$not": {"$gt": ["$lease.expiresAt", "$$NOW"]}},
                ]
            },
        },
        [
            {
                "$set": {
                    "lease": {
                        "holder": holder,
                        "expiresAt": {"$add": ["$$NOW", LEASE_TTL_SECONDS * 1000]},
                    },
                    "lastLeasedAt": "$$NOW",
                }
            }
        ],
        sort=[("lastLeasedAt", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return str(doc["_id"]) if doc else None


async def renew_lease(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str, holder: str,
) -> bool:
    """Slide this holder's lease forward by LEASE_TTL_SECONDS. CAS on holder:
    True if renewed, False if the lease was lost (expired + re-acquired by
    someone else) -- the caller MUST then stop using the seed."""
    oid = _oid_or_none(seed_id)
    if oid is None:
        return False
    res = await _collection(db, prefix, suffix).update_one(
        {"_id": oid, "lease.holder": holder},
        [{"$set": {"lease.expiresAt": {"$add": ["$$NOW", LEASE_TTL_SECONDS * 1000]}}}],
    )
    return res.matched_count == 1


async def release(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str, holder: str,
) -> None:
    """Clear this holder's lease (holder-guarded; no-op if not held by
    `holder`). TTL/liveness is the backstop, so a missed release is harmless."""
    oid = _oid_or_none(seed_id)
    if oid is None:
        return
    await _collection(db, prefix, suffix).update_one(
        {"_id": oid, "lease.holder": holder}, {"$set": {"lease": None}},
    )


async def mark_seed_status(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str, status: str,
) -> None:
    """Set status ('alive' | 'dead'). A 'dead' seed is never handed out by
    acquire. Consumer policy decides when a seed is dead (e.g. invalid_grant)."""
    oid = _oid_or_none(seed_id)
    if oid is None:
        return
    await _collection(db, prefix, suffix).update_one(
        {"_id": oid},
        {"$set": {"status": status, "updatedAt": datetime.now(timezone.utc)}},
    )


async def assign_to_pool(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str, poolKey: str,
) -> None:
    """Set a seed's poolKey (membership). Also the single->pool migration
    primitive used by Spec B."""
    oid = _oid_or_none(seed_id)
    if oid is None:
        return
    await _collection(db, prefix, suffix).update_one(
        {"_id": oid},
        {"$set": {"poolKey": poolKey, "updatedAt": datetime.now(timezone.utc)}},
    )


async def list_pool(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, poolKey: str,
) -> list[dict]:
    """Return [{seedId, status, leased, lastRefreshedAt, lastVerifiedAt}] for
    the pool (UI/observability). `leased` = a lease present and not yet expired.
    `lastRefreshedAt` falls back to `updatedAt` when not separately tracked."""
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    cursor = _collection(db, prefix, suffix).find(
        {"poolKey": poolKey},
        {"status": 1, "lease": 1, "lastRefreshedAt": 1,
         "lastVerifiedAt": 1, "updatedAt": 1},
    )
    async for d in cursor:
        lease = d.get("lease")
        expires = lease.get("expiresAt") if isinstance(lease, dict) else None
        leased = bool(expires is not None and expires > now)
        out.append({
            "seedId": str(d["_id"]),
            "status": d.get("status", "alive"),
            "leased": leased,
            "lastRefreshedAt": d.get("lastRefreshedAt") or d.get("updatedAt"),
            "lastVerifiedAt": d.get("lastVerifiedAt"),
        })
    return out


async def reap_expired_leases(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str,
) -> int:
    """Clear all expired leases; return the count. Optional explicit cleanup /
    observability -- NOTHING depends on it running (acquire reclaims lazily)."""
    res = await _collection(db, prefix, suffix).update_many(
        {
            "$expr": {
                "$and": [
                    {"$ne": [{"$ifNull": ["$lease", None]}, None]},
                    {"$not": {"$gt": ["$lease.expiresAt", "$$NOW"]}},
                ]
            }
        },
        {"$set": {"lease": None}},
    )
    return res.modified_count
```

---

## Task 2 (WAVE, edit-only): pool/lease unit tests in `test_seeds.py`

**Owner files:** `packages/optio-agents/tests/test_seeds.py`
**Do NOT** run tests, lint, or git. Edit only. (Runs concurrently with Task 1; the functions under test won't exist yet in your working copy -- that's expected.)

- [ ] **Step 1: Append the pool/lease tests**

Append at the end of `test_seeds.py`. The module already imports `seeds`, `ObjectId`, `pytest`, and has the `mongo_db` fixture and `SUFFIX = "_fake_seeds"`.

```python
# --- pool / lease layer ----------------------------------------------------

import datetime as _dt


async def _pooled_seed(mongo_db, pool_key):
    """Insert a seed and assign it to `pool_key`; return its seed_id."""
    sid = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=ObjectId(), manifest_version=1,
    )
    await seeds.assign_to_pool(
        mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, poolKey=pool_key,
    )
    return sid


def _coll(mongo_db):
    return mongo_db[f"t{SUFFIX}"]


async def test_acquire_leases_a_free_seed(mongo_db):
    sid = await _pooled_seed(mongo_db, "p1")
    got = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p1", holder="h1")
    assert got == sid
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid)
    assert doc["lease"]["holder"] == "h1"
    assert doc["lease"]["expiresAt"] > _dt.datetime.now(_dt.timezone.utc)
    assert "lastLeasedAt" in doc


async def test_acquire_is_exclusive_and_returns_none_when_exhausted(mongo_db):
    a = await _pooled_seed(mongo_db, "p2")
    b = await _pooled_seed(mongo_db, "p2")
    first = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p2", holder="h1")
    second = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p2", holder="h2")
    assert {first, second} == {a, b}
    third = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p2", holder="h3")
    assert third is None


async def test_acquire_skips_dead_seeds(mongo_db):
    sid = await _pooled_seed(mongo_db, "p3")
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="dead")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p3", holder="h1") is None


async def test_acquire_ignores_other_pools(mongo_db):
    await _pooled_seed(mongo_db, "pA")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="pB", holder="h1") is None


async def test_acquire_reclaims_expired_lease(mongo_db):
    sid = await _pooled_seed(mongo_db, "p4")
    past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=120)
    await _coll(mongo_db).update_one(
        {"_id": ObjectId(sid)}, {"$set": {"lease": {"holder": "stale", "expiresAt": past}}},
    )
    got = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p4", holder="h2")
    assert got == sid
    doc = await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid)
    assert doc["lease"]["holder"] == "h2"


async def test_acquire_does_not_reclaim_live_lease(mongo_db):
    sid = await _pooled_seed(mongo_db, "p5")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p5", holder="h1")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p5", holder="h2") is None


async def test_acquire_round_robin(mongo_db):
    a = await _pooled_seed(mongo_db, "p6")
    b = await _pooled_seed(mongo_db, "p6")
    first = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p6", holder="h1")
    await seeds.release(mongo_db, prefix="t", suffix=SUFFIX, seed_id=first, holder="h1")
    # the never-leased seed (no lastLeasedAt) sorts first -> the OTHER seed wins
    second = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p6", holder="h2")
    assert second != first
    assert {first, second} == {a, b}


async def test_renew_extends_for_holder_and_cas_rejects_others(mongo_db):
    sid = await _pooled_seed(mongo_db, "p7")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p7", holder="h1")
    before = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid))["lease"]["expiresAt"]
    ok = await seeds.renew_lease(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="h1")
    assert ok is True
    after = (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid))["lease"]["expiresAt"]
    assert after >= before
    # a non-holder cannot renew
    assert await seeds.renew_lease(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="other") is False


async def test_renew_false_after_steal(mongo_db):
    sid = await _pooled_seed(mongo_db, "p8")
    # h1 holds, lease expires, h2 steals it
    past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=120)
    await _coll(mongo_db).update_one(
        {"_id": ObjectId(sid)}, {"$set": {"lease": {"holder": "h1", "expiresAt": past}}},
    )
    stolen = await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p8", holder="h2")
    assert stolen == sid
    # the original holder's renewal now fails
    assert await seeds.renew_lease(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="h1") is False


async def test_release_frees_and_is_holder_guarded(mongo_db):
    sid = await _pooled_seed(mongo_db, "p9")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p9", holder="h1")
    # wrong holder cannot release
    await seeds.release(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="other")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p9", holder="h2") is None
    # right holder releases
    await seeds.release(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, holder="h1")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p9", holder="h2") == sid


async def test_mark_status_alive_again_revives(mongo_db):
    sid = await _pooled_seed(mongo_db, "p10")
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="dead")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p10", holder="h1") is None
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="alive")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p10", holder="h1") == sid


async def test_assign_to_pool_makes_seed_eligible(mongo_db):
    sid = await seeds.insert_seed(
        mongo_db, prefix="t", suffix=SUFFIX, blob_id=ObjectId(), manifest_version=1,
    )
    # unassigned -> not acquirable
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p11", holder="h1") is None
    await seeds.assign_to_pool(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, poolKey="p11")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p11", holder="h1") == sid


async def test_list_pool_reports_status_leased_and_timestamps(mongo_db):
    a = await _pooled_seed(mongo_db, "p12")
    b = await _pooled_seed(mongo_db, "p12")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p12", holder="h1")  # leases one
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=b, status="dead")
    listed = {d["seedId"]: d for d in await seeds.list_pool(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p12")}
    assert set(listed) == {a, b}
    assert listed[a]["leased"] is True
    assert listed[b]["status"] == "dead"
    assert listed[b]["leased"] is False
    assert "lastVerifiedAt" in listed[a]


async def test_reap_expired_leases(mongo_db):
    live = await _pooled_seed(mongo_db, "p13")
    expired = await _pooled_seed(mongo_db, "p13")
    await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p13", holder="h1")  # leases `live`
    past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=120)
    await _coll(mongo_db).update_one(
        {"_id": ObjectId(expired)}, {"$set": {"lease": {"holder": "old", "expiresAt": past}}},
    )
    n = await seeds.reap_expired_leases(mongo_db, prefix="t", suffix=SUFFIX)
    assert n == 1
    assert (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=expired))["lease"] is None
    # the live lease is untouched
    assert (await seeds.load_seed(mongo_db, prefix="t", suffix=SUFFIX, seed_id=live))["lease"] is not None
```

---

## Verify phase (single agent, after the wave): test, fix, lint, commit

- [ ] **Step 1: Run the seed tests**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests/test_seeds.py -v`
Expected: all pass (the new pool/lease tests plus the pre-existing seed tests).

- [ ] **Step 2: Fix any cross-task drift**

If the two wave agents diverged on a signature/name, or a `$$NOW` aggregation expression needs a tweak for the installed Mongo version, fix it -- faithful to the spec (`docs/superpowers/specs/2026-06-05-seed-pool-lease-foundation-design.md`). Do not weaken/delete tests to pass; fix the real cause. Re-run until green. If round-robin (`test_acquire_round_robin`) proves flaky on same-millisecond `lastLeasedAt`, the robust assertion is `second != first` -- keep that; do not loosen the exclusivity/reclaim tests.

- [ ] **Step 3: Run the full optio-agents suite (no regressions)**

Run: `cd ~/deai/optio && .venv/bin/python -m pytest packages/optio-agents/tests -v`
Expected: PASS.

- [ ] **Step 4: Lint the touched files**

Run: `cd ~/deai/optio && .venv/bin/ruff check packages/optio-agents/src/optio_agents/seeds.py packages/optio-agents/tests/test_seeds.py` (or `uvx ruff check ...` if ruff is not in the venv). Fix lint in the touched files only.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/optio
git add packages/optio-agents/src/optio_agents/seeds.py packages/optio-agents/tests/test_seeds.py
git commit -m "feat(seeds): generic pool/lease layer (acquire/renew/release/status/membership)"
```

(The repo BANS the `Co-Authored-By` trailer -- do not add one.)

---

## Self-Review Notes

- **Spec coverage:** schema additions (`poolKey`/`lease`/`status`/`lastLeasedAt`/`lastRefreshedAt`/`lastVerifiedAt`) surfaced via the ops + `list_pool`; all 7 operations implemented (Task 1) and tested (Task 2); TTL is a constant, not a caller arg; server-clock `$$NOW` used in acquire/renew/reap; lazy reclaim (no sweeper); round-robin via `lastLeasedAt`; renew CAS returns False on lost lease. No claudecode/excavator changes (Spec B). 
- **Placeholder scan:** none -- every op and test has full code.
- **Type/name consistency:** the 7 signatures in the shared contract match Task 1's defs and Task 2's calls verbatim; `list_pool` dict keys (`seedId/status/leased/lastRefreshedAt/lastVerifiedAt`) match between impl and test.
- **Leasable semantics:** implemented as `status != "dead"` (default-alive-by-absence), realizing the spec's "default alive"; both tasks rely on this (a freshly inserted seed has no `status` and is leasable).
