"""Migrate process records from flat status string to status sub-document.

Handles the transition from the old schema:
    { status: "in_progress", ... }
to the new schema:
    { status: { state: "running", error: null, ... }, ... }

Also maps old state names to new ones.
"""

from optio_core.migrations import fw_migrations

STATE_MAP = {
    "pending": "idle",
    "queued": "scheduled",
    "in_progress": "running",
    "complete": "done",
    "failed": "failed",
    "cancel_requested": "cancel_requested",
    "cancelled": "cancelled",
}


@fw_migrations.register("status_subdocument")
async def status_subdocument(db):
    """Convert flat status string to status sub-document on all process collections."""
    # Find all collections that look like process collections (end with _processes)
    collection_names = await db.list_collection_names()
    process_collections = [n for n in collection_names if n.endswith("_processes")]

    for coll_name in process_collections:
        coll = db[coll_name]

        # Find documents with old flat status (string instead of object)
        async for doc in coll.find({"status": {"$type": "string"}}):
            old_state = doc["status"]
            new_state = STATE_MAP.get(old_state, old_state)

            await coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "status": {
                        "state": new_state,
                        "error": None,
                        "runningSince": None,
                        "doneAt": None,
                        "duration": None,
                        "failedAt": None,
                        "stoppedAt": None,
                    },
                }},
            )

        # Add cancellable field where missing
        await coll.update_many(
            {"cancellable": {"$exists": False}},
            {"$set": {"cancellable": True}},
        )

        # Add progress.message where missing
        await coll.update_many(
            {"progress.message": {"$exists": False}},
            {"$set": {"progress.message": None}},
        )
