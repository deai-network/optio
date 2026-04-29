"""Shared helper for writing the canonical 'force-cancelled' terminal state.

Imported by both Executor.force_cancel and Optio.shutdown. Kept in its own
module to avoid a circular import between executor.py and lifecycle.py.

Spec: docs/2026-04-29-deadline-driven-cancel-design.md
"""
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from optio_core.models import ProcessStatus
from optio_core.state_machine import ACTIVE_STATES
from optio_core.store import append_log


FORCE_CANCEL_ERROR = "Task did not unwind within cancellation grace period"


async def _write_force_cancelled_state(
    db: AsyncIOMotorDatabase, prefix: str, oid: ObjectId,
) -> bool:
    """Conditionally flip an active process to terminal 'failed' state.

    Only updates rows whose current state is in ACTIVE_STATES. A task that
    won the race to a terminal state owns its own transition and is left
    alone. Returns True if the row was updated, False otherwise.
    """
    coll = db[f"{prefix}_processes"]
    now = datetime.now(timezone.utc)
    status_doc = ProcessStatus(
        state="failed", error=FORCE_CANCEL_ERROR, failed_at=now,
    ).to_dict()
    result = await coll.update_one(
        {"_id": oid, "status.state": {"$in": list(ACTIVE_STATES)}},
        {"$set": {"status": status_doc, "widgetUpstream": None}},
    )
    if result.modified_count:
        await append_log(
            db, prefix, oid,
            "event",
            "State forced: running -> failed (cancellation grace period exceeded)",
        )
        return True
    return False
