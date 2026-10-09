"""MongoDB operations for process records."""

import logging
import re as _re
from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure

from optio_core.models import (
    TaskInstance, ProcessStatus, Progress, InnerAuth, ProcessMetadataFilter,
)


logger = logging.getLogger(__name__)

_OBJECTID_RE = _re.compile(r"^[a-fA-F0-9]{24}$")


def _is_objectid(s: str) -> bool:
    return bool(_OBJECTID_RE.match(s))


def _collection(db: AsyncIOMotorDatabase, prefix: str):
    """Get the process collection for a given prefix."""
    return db[f"{prefix}_processes"]


def compute_expire_at(
    ttl_seconds: int | None, now: datetime | None = None,
) -> datetime | None:
    """Return absolute expiry time given a TTL in seconds, or None if no TTL.

    Used by every terminal-state writer that respects TaskInstance.ttl_seconds.
    Centralized here so all writers share one definition of "what does
    ttl_seconds mean" — clock-skew handling, type coercion, etc.
    """
    if ttl_seconds is None:
        return None
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=ttl_seconds)


# The process collection's indexes: (name, keys, options). Every query optio-core
# and optio-api run on {prefix}_processes filters on one of these, except the
# application-specific metadata.* filters.
# Spec: docs/2026-10-09-process-indexes-design.md
PROCESS_INDEXES: list[tuple[str, list[tuple[str, int]], dict]] = [
    # _id -1 covers the newest-first sort of the processId lookups; with
    # processId alone the planner may walk _id_ instead and filter.
    ("processId_1__id_-1", [("processId", 1), ("_id", -1)], {}),
    ("parentId_1_order_1", [("parentId", 1), ("order", 1)], {}),
    ("rootId_1_depth_1_order_1", [("rootId", 1), ("depth", 1), ("order", 1)], {}),
    ("status.state_1", [("status.state", 1)], {}),
    ("originatingSessionId_1", [("originatingSessionId", 1)], {}),
    # Same name and options as migration m004's.
    ("expireAt_ttl", [("expireAt", 1)], {"expireAfterSeconds": 0}),
]

# IndexOptionsConflict, IndexKeySpecsConflict: an index with these keys exists
# under another name or with other options.
_INDEX_CONFLICT_CODES = (85, 86)


async def ensure_process_indexes(db: AsyncIOMotorDatabase, prefix: str) -> None:
    """Create PROCESS_INDEXES on `{prefix}_processes`; a no-op for the ones
    that exist. An index that conflicts with an existing one is logged and
    skipped; any other error propagates."""
    coll = _collection(db, prefix)
    for name, keys, options in PROCESS_INDEXES:
        try:
            await coll.create_index(keys, name=name, **options)
        except OperationFailure as e:
            if e.code not in _INDEX_CONFLICT_CODES:
                raise
            logger.warning(
                "Index %s not created on %s: an existing index conflicts (%s)",
                name, coll.name, e.details.get("errmsg") if e.details else e,
            )


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
                "supportsResume": task.supports_resume,
                "supportsResurrect": getattr(task, "resurrect", None) is not None,
                "ttlSeconds": task.ttl_seconds,
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
                "hasSavedState": False,
                "hasUnsavedWork": False,
                "autoResumeScheduled": False,
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
    db: AsyncIOMotorDatabase,
    prefix: str,
    valid_process_ids: set[str],
    metadata_filter: ProcessMetadataFilter | None = None,
) -> int:
    """Remove process records whose processId is not in the valid set.

    Only removes root processes (parentId is None). When `metadata_filter`
    is provided, the deletion is scoped to records whose stored `metadata`
    matches every key/value in the filter (flat AND-equality).
    """
    coll = _collection(db, prefix)
    query: dict[str, Any] = {
        "processId": {"$nin": list(valid_process_ids)},
        "parentId": None,
    }
    if metadata_filter:
        for k, v in metadata_filter.items():
            query[f"metadata.{k}"] = v
    result = await coll.delete_many(query)
    return result.deleted_count


async def find_stale_process_ids(
    db: AsyncIOMotorDatabase,
    prefix: str,
    valid_process_ids: set[str],
    metadata_filter: ProcessMetadataFilter | None = None,
) -> list[tuple[ObjectId, str, str]]:
    """Find root process records that are stale (not in the valid set).

    Returns a list of (oid, processId, state) tuples. Mirrors the query
    used by `remove_stale_processes` but reads instead of deletes. Used by
    the resync flow to cooperatively cancel stale non-terminal tasks
    before their records are deleted (see lifecycle._cancel_stale_processes).
    """
    coll = _collection(db, prefix)
    query: dict[str, Any] = {
        "processId": {"$nin": list(valid_process_ids)},
        "parentId": None,
    }
    if metadata_filter:
        for k, v in metadata_filter.items():
            query[f"metadata.{k}"] = v
    out: list[tuple[ObjectId, str, str]] = []
    async for doc in coll.find(query, {"processId": 1, "status.state": 1}):
        out.append((doc["_id"], doc["processId"], doc["status"]["state"]))
    return out


async def get_process_by_id(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> dict | None:
    """Get a process record by MongoDB _id."""
    return await _collection(db, prefix).find_one({"_id": process_oid})


async def get_process_by_process_id(
    db: AsyncIOMotorDatabase, prefix: str, process_id: str,
) -> dict | None:
    """Get a process record by processId or by OID hex (dual-form).

    If the string is a 24-hex ObjectId, looks up by _id (unambiguous).
    Otherwise looks up by processId. ProcessId fallback returns the
    NEWEST doc by _id (orphan-resilient: when multiple docs share the
    same processId from prior task re-registrations, the live one — the
    one with the newest OID — wins).
    """
    coll = _collection(db, prefix)
    if _is_objectid(process_id):
        doc = await coll.find_one({"_id": ObjectId(process_id)})
        if doc is not None:
            return doc
    return await coll.find_one({"processId": process_id}, sort=[("_id", -1)])


async def update_status(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, status: ProcessStatus,
    expire_at: datetime | None = None,
    *,
    log: tuple[str, str] | None = None,
    set_fields: dict[str, Any] | None = None,
) -> None:
    """Update the status sub-document of a process.

    When `expire_at` is provided, also $set `expireAt` alongside the status.
    The TTL index on `expireAt` (created by migration m004) will then evict
    the record after `expire_at` passes. Callers in terminal-state writers
    pass `now + ttl_seconds` when the process record carries a `ttlSeconds`
    field.

    `log` is a `(level, message)` entry appended and `set_fields` are further
    top-level fields set, both in the same update: a state change and its log
    line are one write. Spec: docs/2026-10-09-fewer-process-writes-design.md
    """
    update: dict[str, Any] = {"status": status.to_dict()}
    if expire_at is not None:
        update["expireAt"] = expire_at
    if set_fields:
        update.update(set_fields)
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        _with_log({"$set": update}, log),
    )


async def finalize_if_active(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
    status: ProcessStatus, *, expected_state: str | None = None,
) -> bool:
    """Conditionally write a terminal status onto a row that is still active.

    Matches only while `status.state` is in ACTIVE_STATES -- or, with
    `expected_state`, only while it is exactly that state -- so a writer that
    already moved the row on is never overwritten. Also clears
    `widgetUpstream`, clears `autoResumeScheduled` for `failed`, and sets
    `expireAt` from the row's `ttlSeconds`. Returns True if the row was
    updated.

    Used to record a final state late, and to settle a row whose final write
    was lost. Spec: docs/2026-10-03-lost-final-state-writes-design.md
    """
    from optio_core.state_machine import ACTIVE_STATES

    coll = _collection(db, prefix)
    now = datetime.now(timezone.utc)
    ttl_doc = await coll.find_one({"_id": process_oid}, {"ttlSeconds": 1})
    expire_at = compute_expire_at((ttl_doc or {}).get("ttlSeconds"), now=now)
    set_doc: dict[str, Any] = {"status": status.to_dict(), "widgetUpstream": None}
    if status.state == "failed":
        set_doc["autoResumeScheduled"] = False
    if expire_at is not None:
        set_doc["expireAt"] = expire_at
    state_match: Any = (
        expected_state if expected_state is not None
        else {"$in": list(ACTIVE_STATES)}
    )
    result = await coll.update_one(
        {"_id": process_oid, "status.state": state_match},
        {"$set": set_doc},
    )
    return bool(result.modified_count)


async def set_auto_resume_scheduled(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, value: bool,
) -> None:
    """Set the `autoResumeScheduled` stamp on a process.

    The stamp marks a cancelled, state-saved top-level process for automatic
    resume after the next engine start. Set True at shutdown (for eligible
    processes), cleared on resume / manual launch / any transition to failed.
    """
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"autoResumeScheduled": value}},
    )


async def set_has_unsaved_work(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, value: bool,
) -> None:
    """Set `hasUnsavedWork`: the host workdir holds work newer than the last
    snapshot. Agents set it through ProcessContext; optio-core clears it when
    a launch starts and when a resurrect saved the work or found nothing."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"hasUnsavedWork": value}},
    )


async def set_resurrecting(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, value: bool,
) -> None:
    """Set `resurrecting`: transient, True while a resurrect's background run
    is in progress. Set and cleared by optio-core around the run; engine
    startup clears any marker a dead engine left behind."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"resurrecting": value}},
    )


async def update_progress(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, progress: Progress,
    *,
    log: tuple[str, str] | None = None,
) -> None:
    """Update the progress of a process; `log` is a `(level, message)` entry
    appended in the same update."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        _with_log({"$set": {"progress": progress.to_dict()}}, log),
    )


def _log_entry(level: str, message: str, data: dict | None = None) -> dict:
    """A log entry, timestamped now."""
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
    }
    if data:
        entry["data"] = data
    return entry


def _with_log(update: dict, log: tuple[str, str] | None) -> dict:
    """`update` plus a `$push` of the `(level, message)` entry, if any."""
    if log is not None:
        update["$push"] = {"log": _log_entry(*log)}
    return update


async def append_log(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
    level: str, message: str, data: dict | None = None,
) -> None:
    """Append a log entry to a process."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$push": {"log": _log_entry(level, message, data)}},
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
    ttl_seconds: int | None = None,
) -> dict:
    """Create a child process record. `ttl_seconds` is the parent's, so a
    child expires with its parent instead of outliving it."""
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
        "ttlSeconds": ttl_seconds,
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
    """Delete a process and its descendants by processId OR OID hex. No-op if not found."""
    proc = await get_process_by_process_id(db, prefix, process_id)
    if proc is None:
        return
    await delete_descendants(db, prefix, proc["_id"])
    await _collection(db, prefix).delete_one({"_id": proc["_id"]})


async def purge_processes(
    db: AsyncIOMotorDatabase,
    prefix: str,
    metadata_filter: dict,
) -> int:
    """Delete every process record whose metadata matches `metadata_filter`
    (flat AND-equality), plus all descendants of the matched records. Used by
    group-cancel teardown when the caller owns the matched processes wholesale
    (dataspace / customer deletion). Returns the number of docs deleted."""
    coll = _collection(db, prefix)
    query = {f"metadata.{k}": v for k, v in metadata_filter.items()}
    matched = await coll.find(query, {"_id": 1}).to_list(None)
    deleted = 0
    for m in matched:
        deleted += await delete_descendants(db, prefix, m["_id"])
    if matched:
        result = await coll.delete_many({"_id": {"$in": [m["_id"] for m in matched]}})
        deleted += result.deleted_count
    return deleted


def _reset_fields() -> dict[str, Any]:
    """The fields a re-launch resets: the previous run's results."""
    return {
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


async def clear_result_fields(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear previous run's result fields (for re-launch).

    Also deletes all descendant processes from previous runs.
    """
    await delete_descendants(db, prefix, process_oid)
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": _reset_fields()},
    )


async def relaunch_reset(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
    status: ProcessStatus, log: tuple[str, str] | None,
) -> None:
    """Reset the previous run's result fields and set `status`, in one update.

    The log becomes `[<log entry>]`, or empty without `log`. Unlike
    clear_result_fields this does not delete descendants: the caller runs
    delete_descendants first. Spec: docs/2026-10-09-fewer-process-writes-design.md

    `expireAt` is removed: the TTL monitor must not delete a running (or
    dismissed) record; a terminal state sets it again.
    Spec: docs/2026-10-09-process-indexes-design.md
    """
    # The whole `status` replaces the `status.*` resets: to_dict() writes every
    # field, and one $set may not hold both `status` and `status.<x>`.
    fields = {k: v for k, v in _reset_fields().items() if not k.startswith("status.")}
    fields["status"] = status.to_dict()
    fields["log"] = [_log_entry(*log)] if log is not None else []
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": fields, "$unset": {"expireAt": ""}},
    )


async def get_children(
    db: AsyncIOMotorDatabase, prefix: str, parent_oid: ObjectId,
) -> list[dict]:
    """Get all direct children of a process, sorted by order."""
    return await _collection(db, prefix).find(
        {"parentId": parent_oid}
    ).sort("order", 1).to_list(None)


async def list_direct_children(
    db: AsyncIOMotorDatabase,
    prefix: str,
    parent_oid: ObjectId,
    *,
    states: set[str] | None = None,
) -> list[dict]:
    """Return direct children of `parent_oid`, optionally filtered by status.state.

    Sorted by `order` ascending, then `_id` ascending for stable ordering.
    `states=None` returns all direct children regardless of state.
    """
    filt: dict = {"parentId": parent_oid}
    if states is not None:
        filt["status.state"] = {"$in": list(states)}
    return await _collection(db, prefix).find(filt).sort(
        [("order", 1), ("_id", 1)]
    ).to_list(None)


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


async def count_processes(
    db: AsyncIOMotorDatabase,
    prefix: str,
    *,
    states: Collection[str] | None = None,
    metadata: dict[str, str | list[str]] | None = None,
    roots_only: bool = False,
) -> int:
    """Count processes matching the filters, without loading them.

    ``states``: the process is in any of these states. ``metadata``: each
    key-value pair matches ``metadata.{key}``, combined with AND; a list value
    matches any of its items. ``roots_only``: only top-level processes --
    children inherit their parent's metadata, so without it a running parent
    counts once per matching child."""
    filter: dict = {"parentId": None} if roots_only else {}
    if states is not None:
        filter["status.state"] = {"$in": list(states)}
    for key, value in (metadata or {}).items():
        filter[f"metadata.{key}"] = (
            {"$in": list(value)} if isinstance(value, (list, tuple, set, frozenset))
            else value
        )
    return await _collection(db, prefix).count_documents(filter)


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


async def update_control_upstream(
    db: AsyncIOMotorDatabase,
    prefix: str,
    process_oid: ObjectId,
    url: str,
    inner_auth: InnerAuth | None = None,
) -> None:
    """Set controlUpstream (the session's in-process input listener) — used by
    the agent-input proxy route to forward human messages into the session."""
    entry: dict = {"url": url}
    entry["innerAuth"] = inner_auth.to_dict() if inner_auth is not None else None
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"controlUpstream": entry}},
    )


async def clear_control_upstream(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear controlUpstream on a process (teardown)."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"controlUpstream": None}},
    )


async def update_widget_data(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, data,
) -> None:
    """Overwrite widgetData with an arbitrary JSON-serializable value."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetData": data}},
    )


async def update_widget_todos(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, items,
) -> None:
    """Set widgetData.todos and leave every sibling key alone.

    No-op when widgetData is missing, null, or not an object. A dotted
    ``$set`` against null raises in Mongo ("Cannot create field 'todos'").
    """
    await _collection(db, prefix).update_one(
        {"_id": process_oid, "widgetData": {"$type": "object"}},
        {"$set": {"widgetData.todos": items}},
    )


async def clear_widget_data(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId,
) -> None:
    """Clear widgetData (sets to null)."""
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$set": {"widgetData": None}},
    )


async def append_browser_open_request(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, url: str,
) -> str:
    """$push a {requestId, url, createdAt} record onto browserOpenRequests; return requestId.

    createdAt lets consumers auto-expire stale requests: browser-open is an
    ephemeral "open this now" signal, but the requests accumulate on the doc and
    the poller replays the full array on every (re)connection's first update. A
    consumer that opened them once dedups by requestId within its app instance,
    but a full page reload starts with an empty seen-set and would re-open the
    whole history. Consumers ignore requests older than a short window instead.
    """
    request_id = uuid4().hex
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$push": {"browserOpenRequests": {
            "requestId": request_id, "url": url,
            "createdAt": datetime.now(timezone.utc),
        }}},
    )
    return request_id


async def append_session_event(
    db: AsyncIOMotorDatabase, prefix: str, process_oid: ObjectId, event: dict,
) -> str:
    """$push a session event onto sessionEvents; return its requestId.

    `event` is one of:
      {"type": "attention", "reason": <str>}
      {"type": "client", "keyword": <str>, "data": <json>}
    A fresh requestId is minted and merged into the stored record.
    """
    request_id = uuid4().hex
    record = {"requestId": request_id, **event}
    await _collection(db, prefix).update_one(
        {"_id": process_oid},
        {"$push": {"sessionEvents": record}},
    )
    return request_id
