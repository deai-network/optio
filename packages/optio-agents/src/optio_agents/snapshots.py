"""Snapshot history of an engine wrapper: list a process's valid snapshots, and discard the
ones newer than a chosen snapshot (their documents and their own GridFS blobs).

Every wrapper keeps its snapshots in `{prefix}{suffix}`, one document per capture with
`processId`, `capturedAt` (a BSON datetime), `endState` and one field per GridFS blob (bucket
`fs`, same database). A wrapper declares that layout as a `SnapshotLayout` and wires
`snapshot_hooks(layout)` into its TaskInstance's `list_snapshots` and
`discard_snapshots_after` (optio-core calls them; see optio-core's AGENTS.md, "TaskInstance.
list_snapshots / discard_snapshots_after").

- *Valid*: every required blob field is set and names a blob that has an `fs.files`
  document. Optional blob fields do not count.
- `capturedAt` goes out as UTC (a naive datetime is UTC), `YYYY-MM-DDTHH:MM:SS.mmmZ`: BSON
  datetimes hold milliseconds. A `captured_at` coming back in may be any ISO 8601 spelling of
  that instant that carries an offset; a string without an offset, with a non-zero digit
  below the millisecond, or that is not ISO 8601 matches nothing. Instants are compared in
  Python on aware UTC datetimes, never in a query, so naive and tz-aware clients agree.
- Discarding deletes every document newer than the chosen one, valid or not (an invalid
  newest document would otherwise be the one a plain Resume reads), then the blobs those
  documents name that no remaining document of the process names, documents first as in
  prune. The helper writes nothing to the process log.

Spec: docs/2026-11-19-resume-from-snapshot-design.md
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, NamedTuple, Sequence

from optio_core.exceptions import CapturePending, SnapshotNotFound

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

    from optio_core.context import ProcessContext
    from optio_core.conversation_archive import ReadOnlyProcessContext


_log = logging.getLogger(__name__)

# The fraction of the seconds, when it has more digits than datetime keeps (six):
# fromisoformat drops the rest, so they are checked here. An ISO date part holds no
# "." or ",", so the first match is the time's fraction.
_LONG_FRACTION = re.compile(r"[.,](\d{7,})")


@dataclass(frozen=True)
class SnapshotLayout:
    """A wrapper's snapshot collection (`{prefix}{suffix}`) and the GridFS blob fields of
    its documents: the required ones decide validity, the optional ones are only deleted
    with their document."""

    suffix: str
    required_blob_fields: tuple[str, ...]
    optional_blob_fields: tuple[str, ...] = ()

    def collection(self, prefix: str) -> str:
        return f"{prefix}{self.suffix}"


class SnapshotHooks(NamedTuple):
    """The two TaskInstance hooks, with optio-core's signatures."""

    list_snapshots: Callable[["ReadOnlyProcessContext"], Awaitable[list[dict]]]
    discard_snapshots_after: Callable[["ProcessContext", str], Awaitable[int]]


def _utc(value: Any) -> datetime | None:
    """A stored capturedAt as an aware UTC datetime; None when it is not a datetime."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format(at: datetime) -> str:
    return f"{at:%Y-%m-%dT%H:%M:%S}.{at.microsecond // 1000:03d}Z"


def _parse_instant(captured_at: Any) -> datetime | None:
    """`captured_at` as an aware UTC datetime at millisecond precision, or None when it
    names no such instant (no offset, a non-zero digit below the millisecond, not ISO)."""
    if not isinstance(captured_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(captured_at)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    if parsed.microsecond % 1000:
        return None
    long_fraction = _LONG_FRACTION.search(captured_at)
    if long_fraction is not None and long_fraction.group(1)[6:].strip("0"):
        return None
    return parsed.astimezone(timezone.utc)


def _named_ids(doc: dict, fields: Sequence[str]) -> list:
    return [doc[f] for f in fields if doc.get(f) is not None]


async def _existing_blob_ids(db: "AsyncIOMotorDatabase", ids: set) -> set:
    """The ids among `ids` that have an `fs.files` document, in one `$in` query."""
    if not ids:
        return set()
    found = await db["fs.files"].find(
        {"_id": {"$in": list(ids)}}, projection={"_id": 1},
    ).to_list(None)
    return {d["_id"] for d in found}


async def _load(
    db: "AsyncIOMotorDatabase", collection: str, process_id: str,
    required_blob_fields: Sequence[str], fields: Sequence[str],
) -> list[tuple[dict, datetime | None, bool]]:
    """Every document of the process as (document, capturedAt in UTC, valid)."""
    projection = {"capturedAt": 1, "endState": 1, **{f: 1 for f in fields}}
    docs = await db[collection].find(
        {"processId": process_id}, projection=projection,
    ).to_list(None)
    required_ids = {i for d in docs for i in _named_ids(d, required_blob_fields)}
    existing = await _existing_blob_ids(db, required_ids)
    out = []
    for doc in docs:
        valid = all(
            doc.get(f) is not None and doc[f] in existing for f in required_blob_fields
        )
        out.append((doc, _utc(doc.get("capturedAt")), valid))
    return out


async def list_snapshots(
    db: "AsyncIOMotorDatabase",
    collection: str,
    process_id: str,
    required_blob_fields: Sequence[str],
) -> list[dict]:
    """The process's valid snapshots, `[{"capturedAt": str, "endState": str}]`, newest
    first, the newest included. `endState` is as stored, "" when absent. Reads only."""
    required = tuple(required_blob_fields)
    loaded = await _load(db, collection, process_id, required, required)
    listed = [(doc, at) for doc, at, valid in loaded if valid and at is not None]
    listed.sort(key=lambda item: item[1], reverse=True)
    out = []
    for doc, at in listed:
        end_state = doc.get("endState")
        out.append({"capturedAt": _format(at), "endState": "" if end_state is None else end_state})
    return out


async def discard_snapshots_after(
    db: "AsyncIOMotorDatabase",
    collection: str,
    process_id: str,
    captured_at: str,
    required_blob_fields: Sequence[str],
    optional_blob_fields: Sequence[str] = (),
) -> int:
    """Delete every snapshot of the process newer than the valid one whose capturedAt is
    the instant `captured_at` names, valid or not, then the blobs they name that no
    remaining document of the process names. Returns how many documents it deleted.
    Raises SnapshotNotFound(captured_at), having deleted nothing, when no valid snapshot
    has exactly that capturedAt (millisecond precision)."""
    instant = _parse_instant(captured_at)
    if instant is None:
        raise SnapshotNotFound(captured_at)
    required = tuple(required_blob_fields)
    blob_fields = tuple(dict.fromkeys((*required, *optional_blob_fields)))
    loaded = await _load(db, collection, process_id, required, blob_fields)
    if not any(valid and at == instant for _, at, valid in loaded):
        raise SnapshotNotFound(captured_at)
    newer = [doc for doc, at, _ in loaded if at is not None and at > instant]
    if not newer:
        return 0

    coll = db[collection]
    newer_ids = [doc["_id"] for doc in newer]
    await coll.delete_many({"_id": {"$in": newer_ids}})

    remaining = await coll.find(
        {"processId": process_id}, projection={f: 1 for f in blob_fields},
    ).to_list(None)
    kept = {i for d in remaining for i in _named_ids(d, blob_fields)}
    doomed = [
        i for i in dict.fromkeys(i for d in newer for i in _named_ids(d, blob_fields))
        if i not in kept
    ]
    if doomed:
        import gridfs.errors
        from motor.motor_asyncio import AsyncIOMotorGridFSBucket

        bucket = AsyncIOMotorGridFSBucket(db)
        for blob_id in doomed:
            try:
                await bucket.delete(blob_id)
            except gridfs.errors.NoFile:
                pass
            except Exception:
                _log.warning(
                    "discard_snapshots_after(%s): deleting blob %s failed",
                    process_id, blob_id, exc_info=True,
                )
    return len(newer)


def snapshot_hooks(
    layout: SnapshotLayout,
    *,
    capture_pending: "Callable[[ProcessContext], Awaitable[bool]] | None" = None,
) -> SnapshotHooks:
    """A wrapper's `list_snapshots` and `discard_snapshots_after` hooks for `layout`. The
    discard hook first awaits `capture_pending(ctx)` (when given) and raises
    CapturePending(process_id) when it is true, before any read or deletion. Neither hook
    reports progress or writes the process log."""

    async def _list(ctx: "ReadOnlyProcessContext") -> list[dict]:
        return await list_snapshots(
            ctx._db, layout.collection(ctx._prefix), ctx.process_id,
            layout.required_blob_fields,
        )

    async def _discard(ctx: "ProcessContext", captured_at: str) -> int:
        if capture_pending is not None and await capture_pending(ctx):
            raise CapturePending(ctx.process_id)
        return await discard_snapshots_after(
            ctx._db, layout.collection(ctx._prefix), ctx.process_id, captured_at,
            layout.required_blob_fields, layout.optional_blob_fields,
        )

    return SnapshotHooks(_list, _discard)
