"""Shared helper for writing the canonical 'force-cancelled' terminal state.

Imported by both Executor.force_cancel and Optio.shutdown. Kept in its own
module to avoid a circular import between executor.py and lifecycle.py.

Spec: docs/2026-04-29-deadline-driven-cancel-design.md
"""
import logging as _logging
import os as _os
import time as _time
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import ProcessStatus
from optio_core.state_machine import ACTIVE_STATES
from optio_core.store import append_log, compute_expire_at


_trace_logger = _logging.getLogger("optio_core.cancel_trace")
_CANCEL_TRACE = _os.environ.get("OPTIO_CANCEL_TRACE", "0").lower() in ("1", "true", "yes")


def _trace(fmt: str, *args: object) -> None:
    if _CANCEL_TRACE:
        _trace_logger.warning("[%.3f] _force_cancel " + fmt, _time.monotonic(), *args)


FORCE_CANCEL_ERROR = "Task did not unwind within cancellation grace period"


async def _write_force_cancelled_state(
    db: AsyncIOMotorDatabase, prefix: str, oid: ObjectId,
) -> bool:
    """Conditionally flip an active process to terminal 'failed' state.

    Only updates rows whose current state is in ACTIVE_STATES. A task that
    won the race to a terminal state owns its own transition and is left
    alone. Returns True if the row was updated, False otherwise.

    If the row carries a `ttlSeconds` field, also $set `expireAt = now + ttl`
    so the TTL index evicts it at the same point a cooperative-cancel
    record would have been evicted (B2 invariant: every terminal-state
    writer honours TTL).
    """
    coll = db[f"{prefix}_processes"]
    now = datetime.now(timezone.utc)
    status_doc = ProcessStatus(
        state="failed", error=FORCE_CANCEL_ERROR, failed_at=now,
    ).to_dict()

    # Read ttlSeconds so we can compute expireAt for the TTL index.
    ttl_doc = await coll.find_one({"_id": oid}, {"ttlSeconds": 1})
    expire_at = compute_expire_at((ttl_doc or {}).get("ttlSeconds"), now=now)
    set_doc: dict = {"status": status_doc, "widgetUpstream": None}
    if expire_at is not None:
        set_doc["expireAt"] = expire_at

    result = await coll.update_one(
        {"_id": oid, "status.state": {"$in": list(ACTIVE_STATES)}},
        {"$set": set_doc},
    )
    if result.modified_count:
        _trace("oid=%s WROTE state=failed reason=grace-exceeded", oid)
        await append_log(
            db, prefix, oid,
            "event",
            "State forced: running -> failed (cancellation grace period exceeded)",
        )
        return True
    _trace("oid=%s no-op: row already in terminal state (lost race)", oid)
    return False
