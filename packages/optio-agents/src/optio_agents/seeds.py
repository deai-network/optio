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
    doc = await load_seed(ctx._db, prefix=ctx._prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        raise KeyError(f"unknown seed_id: {seed_id!r}")
    payload = await _read_blob_bytes(ctx, doc["blobId"])
    dec = decrypt or (lambda b: b)
    plain = dec(payload)
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
