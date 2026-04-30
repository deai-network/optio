"""Persistent launch-block store — pure async helpers over a Mongo collection.

Spec: docs/2026-04-30-persistent-launch-blocks-design.md
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import ProcessMetadataFilter


@dataclass
class StoredLaunchBlock:
    """One row of the persistent launch-blocks collection."""
    filter: ProcessMetadataFilter
    created_at: datetime
    reason: str | None


def collection(db: AsyncIOMotorDatabase, prefix: str):
    """Return the persistent launch-blocks collection for `prefix`."""
    return db[f"{prefix}_launch_blocks"]


async def load_all(coll) -> list[StoredLaunchBlock]:
    """Load every record. Empty/missing collection -> []."""
    rows: list[StoredLaunchBlock] = []
    async for doc in coll.find({}):
        rows.append(StoredLaunchBlock(
            filter=doc["filter"],
            created_at=doc["createdAt"],
            reason=doc.get("reason"),
        ))
    return rows


async def upsert_block(
    coll,
    launch_filter: ProcessMetadataFilter,
    reason: str | None,
) -> None:
    """Insert a new record OR dedupe-update an existing one.

    Existing record matched by exact filter equality. On dedupe, when both
    the existing reason and `reason` are non-null, the stored reason is
    set to ``f"{existing} AND {reason}"``. Otherwise the existing reason is
    left unchanged.
    """
    existing = await coll.find_one({"filter": launch_filter})
    if existing is None:
        await coll.insert_one({
            "filter": launch_filter,
            "createdAt": datetime.now(timezone.utc),
            "reason": reason,
        })
        return

    # Dedupe path
    existing_reason = existing.get("reason")
    if existing_reason is not None and reason is not None:
        new_reason = f"{existing_reason} AND {reason}"
        await coll.update_one(
            {"_id": existing["_id"]},
            {"$set": {"reason": new_reason}},
        )
    # else: keep existing record's reason untouched


async def delete_by_filter(
    coll,
    launch_filter: ProcessMetadataFilter,
) -> int:
    """Delete every record whose filter equals `launch_filter` exactly.

    Returns the number of rows deleted.
    """
    result = await coll.delete_many({"filter": launch_filter})
    return result.deleted_count
