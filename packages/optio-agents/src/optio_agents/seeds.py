"""Generic, agent-agnostic seed engine.

A *seed* is a stored, optionally-encrypted tar.gz of the *environment*
subset of an agent's isolated HOME (credentials, settings, plugins,
global config) — no conversation/session data. The mechanism here knows
nothing about claude or opencode; agent-specific behavior is supplied via
a `SeedManifest`.

Seeds are stored in a Mongo collection `{prefix}{suffix}` (the agent
package owns `suffix`) with the encrypted blob in GridFS. Each capture
mints a new, opaque, optio-generated id (an `ObjectId` hex string).

This is agent-coordination work, so it lives in optio-agents; it drives
the optio-host `Host` transport (tar/extract/fetch/put). optio-agents
depends on optio-core and optio-host, so importing `ProcessContext` and
`Host` for typing is allowed; we keep `bson`/`motor` as local/
TYPE_CHECKING imports to keep the hard import surface minimal.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

from pymongo import ReturnDocument

if TYPE_CHECKING:
    from bson import ObjectId
    from motor.motor_asyncio import AsyncIOMotorDatabase

    from optio_core.context import ProcessContext
    from optio_host.host import Host


@dataclass(frozen=True)
class SeedManifest:
    """Agent-specific description of what a seed contains.

    - `home_subdir`: HOME relative to the workdir (e.g. "home").
    - `include`: environment paths relative to `home_subdir` (files or
      directories); only those that exist at capture time are tarred.
    - `version`: manifest ruleset version, recorded on each seed doc.
    - `consume_transform`: optional async fixup applied after extract
      (e.g. rekey config to the new cwd). Receives the Host
      (`host.workdir` is the new cwd). None = no transform.
    """

    home_subdir: str
    include: list[str]
    version: int = 1
    consume_transform: "Callable[[Host], Awaitable[None]] | None" = None


# --- Mongo helpers ---------------------------------------------------------


def _collection(db: "AsyncIOMotorDatabase", prefix: str, suffix: str):
    return db[f"{prefix}{suffix}"]


async def insert_seed(
    db: "AsyncIOMotorDatabase",
    *,
    prefix: str,
    suffix: str,
    blob_id: "ObjectId",
    manifest_version: int,
) -> str:
    """Insert one seed doc; return the generated seed_id (ObjectId hex)."""
    doc = {
        "createdAt": datetime.now(timezone.utc),
        "blobId": blob_id,
        "manifestVersion": manifest_version,
    }
    result = await _collection(db, prefix, suffix).insert_one(doc)
    return str(result.inserted_id)


async def update_seed_blob(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
    new_blob_id: "ObjectId",
) -> None:
    """Point an existing seed doc at a new blob and stamp `updatedAt`.

    Used by `refresh_seed` for in-place credential save-back; the seed id is
    stable, only the blob changes."""
    from bson import ObjectId

    await _collection(db, prefix, suffix).update_one(
        {"_id": ObjectId(seed_id)},
        {"$set": {"blobId": new_blob_id, "updatedAt": datetime.now(timezone.utc)}},
    )


async def load_seed(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
) -> dict | None:
    """Look up a seed doc by id. Returns None for unknown or malformed id."""
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(seed_id)
    except (InvalidId, TypeError):
        return None
    return await _collection(db, prefix, suffix).find_one({"_id": oid})


async def delete_seed(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
) -> "ObjectId | None":
    """Delete a seed doc; return its blobId so the caller removes the
    GridFS blob (mirrors the snapshot-prune contract). None if absent."""
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(seed_id)
    except (InvalidId, TypeError):
        return None
    doc = await _collection(db, prefix, suffix).find_one_and_delete({"_id": oid})
    return doc["blobId"] if doc else None


async def list_seeds(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str,
) -> list[dict]:
    """Return [{seedId, createdAt}, ...] for all seeds in the collection."""
    out: list[dict] = []
    cursor = _collection(db, prefix, suffix).find({}, projection={"createdAt": 1})
    async for d in cursor:
        out.append({"seedId": str(d["_id"]), "createdAt": d.get("createdAt")})
    return out


async def purge_seed(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
) -> None:
    """Fully expunge a seed: remove its Mongo doc AND its GridFS blob.

    Raises KeyError if no seed with that id exists. The blob is deleted
    from the default GridFS bucket (the same one capture_seed/ctx blob
    I/O use); a missing/already-deleted blob is tolerated since the doc
    (the queryable record) is what matters."""
    blob_id = await delete_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if blob_id is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
    try:
        await AsyncIOMotorGridFSBucket(db).delete(blob_id)
    except Exception:
        pass


# --- engine ----------------------------------------------------------------


async def _read_blob_bytes(ctx: "ProcessContext", blob_id) -> bytes:
    out = bytearray()
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            out.extend(chunk)
    return bytes(out)


async def _archive_include(host: "Host", *, home_subdir: str, include: list[str]) -> bytes:
    """tar.gz the include paths (those that exist) relative to home_subdir."""
    workdir = host.workdir.rstrip("/")
    home_abs = f"{workdir}/{home_subdir}"
    tmpfile = f"{workdir}/.optio-seed-capture.tar.gz"

    existing: list[str] = []
    for rel in include:
        probe = await host.run_command(f"test -e {shlex.quote(home_abs + '/' + rel)}")
        if probe.exit_code == 0:
            existing.append(rel)

    if existing:
        paths = " ".join(shlex.quote(p) for p in existing)
        cmd = f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(home_abs)} {paths}"
    else:
        # No env files yet (e.g. a brand-new vanilla session). Produce a
        # valid, empty tar so capture still succeeds.
        cmd = f"tar -czf {shlex.quote(tmpfile)} -C {shlex.quote(home_abs)} -T /dev/null"

    r = await host.run_command(cmd)
    if r.exit_code != 0:
        raise RuntimeError(
            f"seed tar failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
        )
    try:
        return await host.fetch_bytes_from_host(tmpfile)
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


async def _extract_seed(
    host: "Host", *, home_subdir: str, plain: bytes, include: list[str] | None = None,
) -> None:
    """Extract the decrypted seed tar over <workdir>/<home_subdir>.

    When `include` is given, extract ONLY the archive members that match one
    of those paths (exact file, or a directory prefix); members absent from the
    archive are silently skipped. Extraction is overlay — it overwrites the
    listed members and never deletes others. `include=None` extracts everything.
    """
    workdir = host.workdir.rstrip("/")
    home_abs = f"{workdir}/{home_subdir}"
    tmpfile = f"{workdir}/.optio-seed-restore.tar.gz"
    await host.run_command(f"mkdir -p {shlex.quote(home_abs)}")
    await host.put_file_to_host(plain, tmpfile)
    try:
        members_arg = ""
        if include is not None:
            listing = await host.run_command(f"tar -tzf {shlex.quote(tmpfile)}")
            if listing.exit_code != 0:
                raise RuntimeError(
                    f"seed list failed (exit {listing.exit_code}): "
                    f"{listing.stderr.strip()[:200]}"
                )
            names = [n for n in listing.stdout.splitlines() if n]
            matched = [
                n for n in names
                if any(
                    n == rel or n.rstrip("/") == rel or n.startswith(rel + "/")
                    for rel in include
                )
            ]
            # A matched directory member (e.g. ".claude/plugins/") extracts
            # recursively, so passing its children as separate args makes tar
            # report them "not found in archive". Keep only top-level matches:
            # drop any member that is a descendant of another matched member.
            prefixes = sorted(
                {n for n in matched if n.endswith("/")}, key=len,
            )
            wanted = [
                n for n in matched
                if not any(n != p and n.startswith(p) for p in prefixes)
            ]
            if not wanted:
                return  # nothing in the archive matches the requested members
            members_arg = " " + " ".join(shlex.quote(n) for n in wanted)
        r = await host.run_command(
            f"tar -xzf {shlex.quote(tmpfile)} -C {shlex.quote(home_abs)}{members_arg}"
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"seed untar failed (exit {r.exit_code}): {r.stderr.strip()[:200]}"
            )
    finally:
        await host.run_command(f"rm -f {shlex.quote(tmpfile)}")


def _merge_tar_members(base_gz: bytes, overlay_gz: bytes) -> bytes:
    """Return a new tar.gz = base with `overlay`'s members overwriting any
    same-named base member; all other base members are preserved. Pure
    in-memory; no host access."""
    import io
    import tarfile

    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(overlay_gz), mode="r:gz") as ov:
        ov_members = ov.getmembers()
        overlay_names = {m.name for m in ov_members}
        with tarfile.open(fileobj=out, mode="w:gz") as w:
            with tarfile.open(fileobj=io.BytesIO(base_gz), mode="r:gz") as base:
                for m in base.getmembers():
                    if m.name in overlay_names:
                        continue
                    w.addfile(m, base.extractfile(m) if m.isfile() else None)
            for m in ov_members:
                w.addfile(m, ov.extractfile(m) if m.isfile() else None)
    return out.getvalue()


async def capture_seed(
    ctx: "ProcessContext",
    host: "Host",
    *,
    manifest: SeedManifest,
    suffix: str,
    encrypt: "Callable[[bytes], bytes] | None",
) -> str:
    """tar include -> encrypt -> store blob -> insert doc. Returns seed_id."""
    raw = await _archive_include(
        host, home_subdir=manifest.home_subdir, include=manifest.include,
    )
    enc = encrypt or (lambda b: b)
    payload = enc(raw)
    async with ctx.store_blob("seed") as writer:
        await writer.write(payload)
        blob_id = writer.file_id
    return await insert_seed(
        ctx._db, prefix=ctx._prefix, suffix=suffix,
        blob_id=blob_id, manifest_version=manifest.version,
    )


async def merge_seed(
    ctx: "ProcessContext",
    host: "Host",
    *,
    seed_id: str,
    manifest: SeedManifest,
    suffix: str,
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """load doc -> load blob -> decrypt -> extract -> consume_transform.

    Raises KeyError if `seed_id` is unknown (no silent fallback). Decrypt
    failure propagates (tampering / key rotation).
    """
    await plant_seed(
        ctx._db, host, prefix=ctx._prefix, seed_id=seed_id,
        manifest=manifest, suffix=suffix, decrypt=decrypt,
    )


async def plant_seed(
    db: "AsyncIOMotorDatabase",
    host: "Host",
    *,
    prefix: str,
    seed_id: str,
    manifest: SeedManifest,
    suffix: str,
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """Host-free-engine variant of merge_seed: same load -> decrypt ->
    extract -> consume_transform, but db-first (no ProcessContext). Reads
    the blob from GridFS directly — the same bucket ctx.store_blob writes
    (cf. overwrite_seed_member). Raises KeyError if `seed_id` is unknown.
    """
    import io
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(db).download_to_stream(doc["blobId"], buf)
    dec = decrypt or (lambda b: b)
    plain = dec(buf.getvalue())
    await _extract_seed(
        host, home_subdir=manifest.home_subdir, plain=plain, include=manifest.include,
    )
    if manifest.consume_transform is not None:
        await manifest.consume_transform(host)


async def refresh_seed(
    ctx: "ProcessContext",
    host: "Host",
    *,
    seed_id: str,
    manifest: SeedManifest,
    suffix: str,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """Merge the live host's `manifest.include` files INTO an existing seed,
    in place: the seed id is stable, only the blob is replaced.

    Crash-safe ordering: store the new blob fully, then atomically repoint the
    doc, then delete the old blob. A crash at any point leaves at worst an
    orphan GridFS blob; the doc never points at a half-written blob.

    Raises KeyError if `seed_id` is unknown.
    """
    doc = await load_seed(ctx._db, prefix=ctx._prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    old_blob_id = doc["blobId"]

    dec = decrypt or (lambda b: b)
    enc = encrypt or (lambda b: b)
    base = dec(await _read_blob_bytes(ctx, old_blob_id))
    overlay = await _archive_include(
        host, home_subdir=manifest.home_subdir, include=manifest.include,
    )
    merged = _merge_tar_members(base, overlay)
    payload = enc(merged)

    async with ctx.store_blob("seed") as writer:
        await writer.write(payload)
        new_blob_id = writer.file_id

    await update_seed_blob(
        ctx._db, prefix=ctx._prefix, suffix=suffix,
        seed_id=seed_id, new_blob_id=new_blob_id,
    )
    await ctx.delete_blob(old_blob_id)


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
    `lastRefreshedAt` falls back to `updatedAt` when not separately tracked.

    `leased` is computed on the Mongo server clock (`$$NOW`) via the same
    `$gt: [lease.expiresAt, $$NOW]` predicate that `acquire`/`reap` use, so the
    notion of "leased" is identical across all ops and does not depend on the
    client's tz-awareness."""
    out: list[dict] = []
    cursor = _collection(db, prefix, suffix).aggregate([
        {"$match": {"poolKey": poolKey}},
        {"$project": {
            "status": 1,
            "lastRefreshedAt": 1,
            "lastVerifiedAt": 1,
            "updatedAt": 1,
            "createdAt": 1,
            "metadata": 1,
            "leased": {"$gt": [{"$ifNull": ["$lease.expiresAt", None]}, "$$NOW"]},
        }},
    ])
    async for d in cursor:
        out.append({
            "seedId": str(d["_id"]),
            "status": d.get("status", "alive"),
            "leased": bool(d.get("leased")),
            "lastRefreshedAt": d.get("lastRefreshedAt") or d.get("updatedAt"),
            "lastVerifiedAt": d.get("lastVerifiedAt"),
            "createdAt": d.get("createdAt"),
            "metadata": d.get("metadata") or {},
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


async def declare_metadata(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str, metadata: dict,
) -> None:
    """$set-merge opaque `metadata.<k>` keys onto a seed doc. The pool stores
    metadata opaquely; consumers own its meaning."""
    oid = _oid_or_none(seed_id)
    if oid is None:
        return
    sets = {f"metadata.{k}": v for k, v in metadata.items()}
    sets["updatedAt"] = datetime.now(timezone.utc)
    await _collection(db, prefix, suffix).update_one({"_id": oid}, {"$set": sets})


def _single_member_targz(member_path: str, content: bytes) -> bytes:
    """A tar.gz containing exactly one file member."""
    import io
    import tarfile

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=member_path)
        info.size = len(content)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


async def overwrite_seed_member(
    db: "AsyncIOMotorDatabase", *, prefix: str, suffix: str, seed_id: str,
    member_path: str, content: bytes,
    encrypt: "Callable[[bytes], bytes] | None",
    decrypt: "Callable[[bytes], bytes] | None",
) -> None:
    """Host-free: overwrite one member of a seed's tar blob with `content`,
    in place. Crash-safe blob swap (store new -> repoint doc -> delete old).
    Reads/writes GridFS from `db` directly (no ProcessContext). Raises KeyError
    if the seed is unknown."""
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    old_blob_id = doc["blobId"]
    bucket = AsyncIOMotorGridFSBucket(db)

    import io
    buf = io.BytesIO()
    await bucket.download_to_stream(old_blob_id, buf)
    dec = decrypt or (lambda b: b)
    enc = encrypt or (lambda b: b)
    base = dec(buf.getvalue())
    overlay = _single_member_targz(member_path, content)
    merged = _merge_tar_members(base, overlay)
    new_blob_id = await bucket.upload_from_stream("seed", enc(merged))

    await update_seed_blob(
        db, prefix=prefix, suffix=suffix, seed_id=seed_id, new_blob_id=new_blob_id,
    )
    try:
        await bucket.delete(old_blob_id)
    except Exception:
        pass
