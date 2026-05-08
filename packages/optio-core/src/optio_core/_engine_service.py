"""EngineService — clamator RPC implementation for the optio engine.

Phase 2 of the engine-RPC migration. Co-exists with the legacy
${prefix}:commands stream consumer; HTTP handlers still route through
the legacy stream until phase 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bson import ObjectId

from optio_core._generated.engine import (
    EngineService as EngineServiceBase,
    LaunchParams, LaunchResult,
    CancelParams, CancelResult,
    DismissParams, DismissResult,
    GroupCancelParams, GroupCancelResult,
    GroupCancelAndWaitParams, GroupCancelAndWaitResult,
    BlockLaunchesParams, BlockLaunchesResult,
    UnblockLaunchesParams, UnblockLaunchesResult,
    ResyncParams,
)
from optio_core.models import LaunchBlocked

if TYPE_CHECKING:
    from optio_core.lifecycle import Optio


# State allowlists from packages/optio-api/src/handlers.ts. Mirrored here so
# the engine — not the API — owns the rule (parent spec authority statement).
LAUNCHABLE_STATES = {"idle", "done", "failed", "cancelled"}
CANCELLABLE_STATES = {"scheduled", "running", "cancel_requested"}
DISMISSABLE_STATES = {"done", "failed", "cancelled"}

_OBJECTID_RE = __import__("re").compile(r"^[a-fA-F0-9]{24}$")


def _is_objectid(s: str) -> bool:
    return bool(_OBJECTID_RE.match(s))


def _to_process_dict(doc: dict) -> dict:
    """Render a Mongo process doc as the wire-shape Process payload.

    Returns a dict that LaunchResult1.process / CancelResult1.process /
    DismissResult1.process etc. can validate. Generated Process model uses
    by-alias field names (e.g. _id, processId, supportsResume), so we just
    pass the doc through after stringifying the ObjectId.
    """
    out = dict(doc)
    if "_id" in out and isinstance(out["_id"], ObjectId):
        out["_id"] = str(out["_id"])
    return out


class EngineService(EngineServiceBase):
    """Concrete EngineService backing the clamator engine contract."""

    def __init__(self, optio: "Optio") -> None:
        self._optio = optio

    # --------------------------------------------------------------- launch
    async def launch(self, params: LaunchParams) -> LaunchResult:
        proc = await self._resolve(params.process_id)
        if proc is None:
            return LaunchResult.model_validate({"ok": False, "reason": "not-found"})
        if proc["status"]["state"] not in LAUNCHABLE_STATES:
            return LaunchResult.model_validate({"ok": False, "reason": "not-launchable"})
        if params.resume and not proc.get("supportsResume", False):
            return LaunchResult.model_validate({"ok": False, "reason": "no-resume-support"})

        try:
            await self._optio.launch(proc["processId"], resume=bool(params.resume))
        except LaunchBlocked:
            return LaunchResult.model_validate({"ok": False, "reason": "launch-blocked"})

        updated = await self._resolve(proc["processId"])
        return LaunchResult.model_validate({"ok": True, "process": _to_process_dict(updated)})

    # ------------------------------------------------------------- internals
    async def _resolve(self, id_str: str) -> dict | None:
        """Accept ObjectId hex or processId string; return the doc or None."""
        coll = self._optio._config.mongo_db[
            f"{self._optio._config.prefix}_processes"
        ]
        if _is_objectid(id_str):
            doc = await coll.find_one({"_id": ObjectId(id_str)})
            if doc:
                return doc
        return await coll.find_one({"processId": id_str})

    # --------------------------------------------------------------- cancel
    async def cancel(self, params: CancelParams) -> CancelResult:
        proc = await self._resolve(params.process_id)
        if proc is None:
            return CancelResult.model_validate({"ok": False, "reason": "not-found"})
        if not proc.get("cancellable", True) or proc["status"]["state"] not in CANCELLABLE_STATES:
            return CancelResult.model_validate({"ok": False, "reason": "not-cancellable"})

        await self._optio.cancel(proc["processId"])

        updated = await self._resolve(proc["processId"])
        return CancelResult.model_validate({"ok": True, "process": _to_process_dict(updated)})

    # --------------------------------------------------------------- dismiss
    async def dismiss(self, params: DismissParams) -> DismissResult:
        proc = await self._resolve(params.process_id)
        if proc is None:
            return DismissResult.model_validate({"ok": False, "reason": "not-found"})
        if proc["status"]["state"] not in DISMISSABLE_STATES:
            return DismissResult.model_validate({"ok": False, "reason": "not-dismissable"})

        await self._optio.dismiss(proc["processId"])

        updated = await self._resolve(proc["processId"])
        return DismissResult.model_validate({"ok": True, "process": _to_process_dict(updated)})

    # --------------------------------------------------------------- resync
    async def resync(self, params: ResyncParams) -> None:
        await self._optio.resync(
            clean=bool(params.clean),
            metadata_filter=params.metadata_filter,
        )

    # --------------------------------------------------------------- group_cancel / group_cancel_and_wait
    async def group_cancel(self, params: GroupCancelParams) -> GroupCancelResult:
        if params.persist and not params.block_new_launches:
            return GroupCancelResult.model_validate(
                {"ok": False, "reason": "invalid-persist-without-block"}
            )
        count = await self._optio.group_cancel(
            metadata_filter=params.metadata_filter,
            block_new_launches=bool(params.block_new_launches),
            persist=bool(params.persist),
            reason=params.reason,
        )
        return GroupCancelResult.model_validate({"ok": True, "cancelledCount": count})

    async def group_cancel_and_wait(
        self, params: GroupCancelAndWaitParams
    ) -> GroupCancelAndWaitResult:
        if params.persist and not params.block_new_launches:
            return GroupCancelAndWaitResult.model_validate(
                {"ok": False, "reason": "invalid-persist-without-block"}
            )
        count = await self._optio.group_cancel_and_wait(
            metadata_filter=params.metadata_filter,
            block_new_launches=bool(params.block_new_launches),
            persist=bool(params.persist),
            reason=params.reason,
        )
        return GroupCancelAndWaitResult.model_validate({"ok": True, "cancelledCount": count})

    # --------------------------------------------------------------- block_launches / unblock_launches
    async def block_launches(self, params: BlockLaunchesParams) -> BlockLaunchesResult:
        from optio_core import _launch_block_store as _lb_store
        coll = _lb_store.collection(
            self._optio._config.mongo_db,
            self._optio._config.prefix,
        )
        await _lb_store.upsert_block(coll, params.launch_filter, params.reason)
        await self._optio._load_persisted_blocks()
        return BlockLaunchesResult.model_validate({"ok": True})

    async def unblock_launches(
        self, params: UnblockLaunchesParams
    ) -> UnblockLaunchesResult:
        removed = await self._optio.unblock_launches(params.launch_filter)
        return UnblockLaunchesResult(removed=removed)
