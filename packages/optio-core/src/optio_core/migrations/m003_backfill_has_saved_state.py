"""Backfill hasSavedState=False on pre-existing process docs.

supportsResume is handled by upsert_process ($set on sync), but hasSavedState
lives in $setOnInsert so pre-existing docs never receive it. Backfill once
via the migration system.
"""

from optio_core.migrations import fw_migrations


@fw_migrations.register(
    "backfill_has_saved_state",
    depends_on=["backfill_child_metadata"],
)
async def backfill_has_saved_state(db):
    collection_names = await db.list_collection_names()
    process_collections = [n for n in collection_names if n.endswith("_processes")]
    for coll_name in process_collections:
        await db[coll_name].update_many(
            {"hasSavedState": {"$exists": False}},
            {"$set": {"hasSavedState": False}},
        )
