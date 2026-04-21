"""MongoDB operations for process records."""

from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import TaskInstance, ProcessStatus, Progress, InnerAuth


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    """Get the process collection for a given prefix."""
    return db[f"{prefix}_processes"]


async def upsert_process(db: AsyncIOMotorDatabase, prefix: str, task: TaskInstance) -> dict:
    """Upsert a process record from a task instance.

    Creates the record if it doesn't exist (with idle status).
    Updates metadata fields if it does exist (preserves runtime state).
    """
    coll = _collection(db, prefix)
    now = datetime.now(timezone.utc)

    result = await coll.find_one_and_update(
        {"processId": task.process_id},
        {
            "$set": {
                "processId": task.process_id,
                "name": task.name,
                "params": task.params,
                "metadata": task.metadata,
                "cancellable": task.cancellable,
                "description": task.description,
                "special": task.special,
                "warning": task.warning,
                "uiWidget": task.ui_widget,
            },
            "$setOnInsert": {
                "parentId": None,
                "rootId": None,
                "depth": 0,
                "order": 0,
                "adhoc": False,
                "ephemeral": False,
                "status": ProcessStatus().to_dict(),
                "progress": Progress().to_dict(),
                "log": [],
                "createdAt": now,
            },
        },
        upsert=True,
        return_document=True,
    )

    # Fix rootId to point to self for new records
    if result.get("rootId") is None:
        await coll.update_one(
            {"_id": result["_id"]},
            {"$set": {"rootId": result["_id"]}},
        )
        result["rootId"] = result["_id"]

    return result


async def remove_stale_processes(
    db: AsyncIOMotorDatabase, prefix: str, valid_process_ids: set[str],
) -> int:
    """Remove process records whose processId is not in the valid set.
    Only removes root processes (parentId is None).
    """
    coll = _collection(db, prefix)
    result = await coll.delete_many({
        "processId": {"$nin": list(valid_process_ids)},
        "parentId": None,
    })
    return result.deleted_count


async def get_process_by_id(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> dict | None:
    """Get a process record by MongoDB _id."""
    return await _collection(db, prefix).find_one({"_id": process_oid})


async def get_process_by_process_id(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> dict | None:
    """Get a process record by processId string."""
    return await _collection(db, prefix).find_one({"processId": process_id})


async def update_status(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, status: ProcessStatus,
) -> None:
    """Update the status sub-document of a process."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"status": status.to_dict()}},
    )


async def update_progress(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, progress: Progress,
) -> None:
    """Update the progress of a process."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"progress": progress.to_dict()}},
    )


async def append_log(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
    level: str, message: str, data: dict | None = None,
) -> None:
    """Append a log entry to a process."""
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
    }
    if data:
        entry["data"] = data
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$push": {"log": entry}},
    )


async def create_child_process(
    db: AsyncIOMotorDatabase,
    prefix: str,
    parent_oid: ObjectId,
    root_oid: ObjectId,
    process_id: str,
    name: str,
    params: dict,
    depth: int,
    order: int,
    cancellable: bool = True,
    description: str | None = None,
    initial_state: str = "idle",
    metadata: dict | None = None,
    adhoc: bool = False,
    ephemeral: bool = False,
) -> dict:
    """Create a child process record."""
    coll = _collection(db, prefix)
    now = datetime.now(timezone.utc)
    doc = {
        "processId": process_id,
        "name": name,
        "params": params,
        "metadata": metadata or {},
        "parentId": parent_oid,
        "rootId": root_oid,
        "depth": depth,
        "order": order,
        "cancellable": cancellable,
        "description": description,
        "adhoc": adhoc,
        "ephemeral": ephemeral,
        "special": False,
        "warning": None,
        "status": ProcessStatus(state=initial_state).to_dict(),
        "progress": Progress().to_dict(),
        "log": [],
        "createdAt": now,
    }
    result = await coll.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def delete_descendants(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> int:
    """Recursively delete all descendants of a process."""
    coll = _collection(db, prefix)
    children = await coll.find(
        {"parentId": process_oid}, {"_id": 1},
    ).to_list(None)
    deleted = 0
    for child in children:
        deleted += await delete_descendants(db, prefix, child["_id"])
    if children:
        result = await coll.delete_many({"parentId": process_oid})
        deleted += result.deleted_count
    return deleted


async def delete_process(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> None:
    """Delete a process and its descendants by processId. No-op if not found."""
    proc = await get_process_by_process_id(db, prefix, process_id)
    if proc is None:
        return
    await delete_descendants(db, prefix, proc["_id"])
    await _collection(db, prefix).delete_one({"_id": proc["_id"]})


async def clear_result_fields(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear previous run's result fields (for re-launch).

    Also deletes all descendant processes from previous runs.
    """
    await delete_descendants(db, prefix, process_oid)
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {
            "$set": {
                "status.error": None,
                "status.runningSince": None,
                "status.doneAt": None,
                "status.duration": None,
                "status.failedAt": None,
                "status.stoppedAt": None,
                "progress": Progress().to_dict(),
                "log": [],
                "widgetData": None,
                "widgetUpstream": None,
            }
        },
    )


async def get_children(
    db: AsyncIOMotorDatabase, prefix: str, parent_oid: ObjectId,
) -> list[dict]:
    """Get all direct children of a process, sorted by order."""
    return await _collection(db, prefix).find(
        {"parentId": parent_oid}
    ).sort("order", 1).to_list(None)


async def cancel_children(
    db: AsyncIOMotorDatabase, prefix: str, parent_oid: ObjectId,
) -> None:
    """Cancel all scheduled/running children of a process (DB state only)."""
    coll = _collection(db, prefix)
    await coll.update_many(
        {
            "parentId": parent_oid,
            "status.state": {"$in": ["scheduled", "running", "cancel_requested"]},
        },
        {"$set": {"status.state": "cancel_requested"}},
    )


async def list_processes(
    db: AsyncIOMotorDatabase,
    prefix: str,
    state: str | None = None,
    root_id: ObjectId | None = None,
    metadata: dict[str, str] | None = None,
) -> list[dict]:
    """List processes with optional filters."""
    coll = _collection(db, prefix)
    filter: dict = {}
    if state is not None:
        filter["status.state"] = state
    if root_id is not None:
        filter["rootId"] = root_id
    if metadata is not None:
        for key, value in metadata.items():
            filter[f"metadata.{key}"] = value

    return await coll.find(filter).sort([
        ("depth", 1), ("order", 1), ("_id", 1),
    ]).to_list(None)


async def update_widget_upstream(
    db: AsyncIOMotorDatabase,
    prefix: str,
    process_oid: ObjectId,
    url: str,
    inner_auth: InnerAuth | None = None,
) -> None:
    """Set widgetUpstream on a process (used by the proxy for forwarding)."""
    entry: dict = {"url": url}
    if inner_auth is not None:
        entry["innerAuth"] = inner_auth.to_dict()
    else:
        entry["innerAuth"] = None
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetUpstream": entry}},
    )


async def clear_widget_upstream(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear widgetUpstream on a process."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetUpstream": None}},
    )


async def update_widget_data(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, data,
) -> None:
    """Overwrite widgetData with an arbitrary JSON-serializable value."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetData": data}},
    )


async def clear_widget_data(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear widgetData (sets to null)."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetData": None}},
    )
