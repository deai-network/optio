"""Clamator RPC implementation for the optio-engine contract."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from bson import ObjectId

from optio_core._generated.optio_engine import (
    OptioEngineService as OptioEngineServiceBase,
    LaunchParams, LaunchResult,
    CancelParams, CancelResult,
    DismissParams, DismissResult,
    GroupCancelParams, GroupCancelResult,
    GroupCancelAndWaitParams, GroupCancelAndWaitResult,
    BlockLaunchesParams, BlockLaunchesResult,
    UnblockLaunchesParams, UnblockLaunchesResult,
    MaterializeUploadParams, MaterializeUploadResult,
    ResyncParams,
)

if TYPE_CHECKING:
    from optio_core.lifecycle import Optio


_UTC = datetime.timezone.utc

# Allowed top-level keys in the Process wire model (by-alias names).
_PROCESS_WIRE_KEYS = frozenset({
    "_id", "processId", "name", "params", "metadata", "parentId", "rootId",
    "depth", "order", "cancellable", "special", "warning", "description",
    "status", "progress", "log", "uiWidget", "widgetData", "supportsResume",
    "hasSavedState", "autoResumeScheduled", "createdAt", "browserOpenRequests",
    "sessionEvents",
})


def _fix_value(v: object) -> object:
    """Recursively normalize a Mongo value to a wire-safe value."""
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime.datetime) and v.tzinfo is None:
        return v.replace(tzinfo=_UTC)
    if isinstance(v, dict):
        return {k2: _fix_value(v2) for k2, v2 in v.items()}
    if isinstance(v, list):
        return [_fix_value(item) for item in v]
    return v


def _to_process_dict(doc: dict) -> dict:
    """Render a Mongo process doc as the wire-shape Process payload.

    Returns a dict that LaunchResult1.process / CancelResult1.process /
    DismissResult1.process etc. can validate. Generated Process model uses
    by-alias field names (e.g. _id, processId, supportsResume).

    Strips fields unknown to the contract (e.g. adhoc, ephemeral, ttlSeconds
    stored by the scheduler), stringifies ObjectIds, and makes naive datetimes
    UTC-aware so Pydantic AwareDatetime validation passes.
    """
    out = {
        k: _fix_value(v)
        for k, v in doc.items()
        if k in _PROCESS_WIRE_KEYS
    }
    return out


class OptioEngineService(OptioEngineServiceBase):
    """Concrete OptioEngineService backing the clamator optio-engine contract."""

    def __init__(self, optio: "Optio") -> None:
        self._optio = optio

    # --------------------------------------------------------------- launch
    async def launch(self, params: LaunchParams) -> LaunchResult:
        outcome = await self._optio.launch(
            params.process_id, resume=bool(params.resume),
            session_id=params.session_id,
        )
        if not outcome.ok:
            return LaunchResult.model_validate(
                {"ok": False, "reason": outcome.reason}
            )
        return LaunchResult.model_validate(
            {"ok": True, "process": _to_process_dict(outcome.proc)}
        )

    # --------------------------------------------------------------- cancel
    async def cancel(self, params: CancelParams) -> CancelResult:
        outcome = await self._optio.cancel(params.process_id)
        if not outcome.ok:
            return CancelResult.model_validate(
                {"ok": False, "reason": outcome.reason}
            )
        return CancelResult.model_validate(
            {"ok": True, "process": _to_process_dict(outcome.proc)}
        )

    # --------------------------------------------------------------- dismiss
    async def dismiss(self, params: DismissParams) -> DismissResult:
        outcome = await self._optio.dismiss(params.process_id)
        if not outcome.ok:
            return DismissResult.model_validate(
                {"ok": False, "reason": outcome.reason}
            )
        return DismissResult.model_validate(
            {"ok": True, "process": _to_process_dict(outcome.proc)}
        )

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
            purge_records=bool(params.purge_records),
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
            purge_records=bool(params.purge_records),
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

    # --------------------------------------------------------- materialize_upload
    async def materialize_upload(
        self, params: MaterializeUploadParams
    ) -> MaterializeUploadResult:
        """Read the GridFS-staged upload blob and hand it to the task's writer.

        The API streamed the file into GridFS (bytes never crossed Redis) and
        passes only the ``blobId``. We read those bytes and call
        ``Optio.materialize_upload``, which resolves the process's in-process
        writer and writes into its workdir. Any failure (unknown process, no
        registered writer, write error) is returned as ``ok=False`` with a
        reason rather than raised over the wire.
        """
        try:
            data = await self._optio.read_blob_bytes(ObjectId(params.blob_id))
            path = await self._optio.materialize_upload(
                params.process_id, data, params.filename,
            )
            return MaterializeUploadResult.model_validate({"ok": True, "path": path})
        except Exception as exc:
            return MaterializeUploadResult.model_validate(
                {"ok": False, "reason": repr(exc)}
            )
