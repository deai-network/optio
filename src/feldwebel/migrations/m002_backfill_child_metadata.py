"""Backfill metadata on child processes from their root process.

Child processes created before metadata inheritance was added have no metadata
field. This migration copies the root process's metadata onto all children
that are missing it.
"""

from feldwebel.migrations import fw_migrations


@fw_migrations.register("backfill_child_metadata", depends_on=["status_subdocument"])
async def backfill_child_metadata(db):
    """Copy root process metadata to children missing metadata."""
    collection_names = await db.list_collection_names()
    process_collections = [n for n in collection_names if n.endswith("_processes")]

    for coll_name in process_collections:
        coll = db[coll_name]

        # Find all children without metadata (or with empty metadata)
        async for doc in coll.find({
            "parentId": {"$ne": None},
            "$or": [
                {"metadata": {"$exists": False}},
                {"metadata": {}},
            ],
        }):
            root = await coll.find_one({"_id": doc["rootId"]})
            if root and root.get("metadata"):
                await coll.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"metadata": root["metadata"]}},
                )
