"""Optio.list_snapshots and Optio.launch(..., resume_from=): the hook dispatch,
the refusals (decided before the discard hook runs) and the order of the checks.
Spec: docs/2026-11-19-resume-from-snapshot-design.md"""

import asyncio
import time

import pytest

from optio_core import (
    CapturePending, ListSnapshotsOutcome, SnapshotNotFound,
)
from optio_core.context import ProcessContext
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance

TS = "2026-11-19T13:05:12.345Z"


async def _noop(ctx):
    pass


async def _wait_until(pred, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached within the hang ceiling")


async def _setup(
    mongo_db, prefix, *, list_hook=None, discard_hook=None, state="cancelled",
    saved=True, supports_resume=True, metadata=None,
):
    """An ad-hoc process "r1" with the given hooks and record state. The
    executor's launch_process is a spy; it and the discard hook record into
    the shared `events` list: ("launch", resume) and ("discard", captured_at)."""
    events: list = []
    discard = None
    if discard_hook is not None:
        async def discard(ctx, captured_at):
            events.append(("discard", captured_at))
            return await discard_hook(ctx, captured_at)
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix)
    proc = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="r1", name="R1", supports_resume=supports_resume,
        metadata=metadata or {}, list_snapshots=list_hook,
        discard_snapshots_after=discard,
    ))
    coll = mongo_db[f"{prefix}_processes"]
    await coll.update_one({"_id": proc["_id"]}, {"$set": {
        "status.state": state, "hasSavedState": saved,
        "supportsResume": supports_resume,
    }})

    async def _spy(oid, *, resume, session_id):
        events.append(("launch", resume))
    fw._executor.launch_process = _spy
    return fw, coll, proc, events


async def _discard_none(ctx, captured_at):
    return 0


@pytest.mark.asyncio
async def test_list_snapshots_not_found(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="rfs0")
    assert await fw.list_snapshots("missing") == ListSnapshotsOutcome(ok=False, reason="not-found")


@pytest.mark.asyncio
async def test_list_snapshots_without_hook(mongo_db):
    fw, *_ = await _setup(mongo_db, "rfs1")
    out = await fw.list_snapshots("r1")
    assert out == ListSnapshotsOutcome(ok=False, reason="no-snapshot-support")


@pytest.mark.asyncio
async def test_list_snapshots_runs_the_hook_on_a_read_only_context(mongo_db):
    seen = []
    listed = [{"capturedAt": TS, "endState": "done"}]

    async def hook(ctx):
        seen.append(ctx)
        return listed
    fw, *_ = await _setup(mongo_db, "rfs2", list_hook=hook)
    out = await fw.list_snapshots("r1")
    assert out == ListSnapshotsOutcome(ok=True, snapshots=[{"capturedAt": TS, "endState": "done"}])
    assert len(seen) == 1
    assert seen[0].process_id == "r1"
    with pytest.raises(AttributeError):
        seen[0].report_progress


@pytest.mark.asyncio
async def test_resume_from_without_resume_is_invalid_request(mongo_db):
    fw, _, _, events = await _setup(mongo_db, "rfs3", discard_hook=_discard_none)
    out = await fw.launch("r1", resume=False, session_id=None, resume_from=TS)
    assert out.ok is False and out.reason == "invalid-request"
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case, setup, expected", [
    ("running", {"state": "running"}, "not-launchable"),
    ("resurrecting", {}, "not-launchable"),
    ("no-resume-support", {"supports_resume": False}, "no-resume-support"),
    ("no-saved-state", {"saved": False}, "not-launchable"),
    ("blocked", {"metadata": {"project": "p1"}}, "launch-blocked"),
])
async def test_refusals_before_the_hook(mongo_db, case, setup, expected):
    fw, _, proc, events = await _setup(
        mongo_db, f"rfs4-{case}", discard_hook=_discard_none, **setup,
    )
    if case == "resurrecting":
        fw._resurrecting[proc["_id"]] = object()
    if case == "blocked":
        async with fw.block_launches({"project": "p1"}):
            out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    else:
        out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    fw._resurrecting.pop(proc["_id"], None)
    assert out.ok is False and out.reason == expected
    assert events == []


@pytest.mark.asyncio
async def test_no_hook_is_no_snapshot_support(mongo_db):
    async def list_hook(ctx):
        return []
    fw, _, _, events = await _setup(mongo_db, "rfs5", list_hook=list_hook)
    out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.ok is False and out.reason == "no-snapshot-support"
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raised, expected", [
    (SnapshotNotFound(TS), "snapshot-not-found"),
    (CapturePending("p"), "capture-pending"),
])
async def test_hook_refusals_map_to_reasons(mongo_db, raised, expected):
    async def hook(ctx, captured_at):
        raise raised
    fw, coll, proc, events = await _setup(mongo_db, f"rfs6-{expected}", discard_hook=hook)
    out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.ok is False and out.reason == expected
    assert events == [("discard", TS)]
    assert not any(e[0] == "launch" for e in events)
    doc = await coll.find_one({"_id": proc["_id"]})
    assert not any(e["message"].startswith("Resuming from the snapshot") for e in doc["log"])


@pytest.mark.asyncio
@pytest.mark.parametrize("discarded, line", [
    (2, "Resuming from the snapshot of 2026-11-19T13:05:12.345Z: 2 newer snapshots discarded"),
    (1, "Resuming from the snapshot of 2026-11-19T13:05:12.345Z: 1 newer snapshot discarded"),
])
async def test_success_discards_then_resumes(mongo_db, discarded, line):
    contexts = []

    async def hook(ctx, captured_at):
        contexts.append(ctx)
        return discarded
    fw, coll, proc, events = await _setup(
        mongo_db, f"rfs7-{discarded}", discard_hook=hook,
    )
    out = await fw.launch("r1", resume=True, session_id="s1", resume_from=TS)
    assert out.ok
    await _wait_until(lambda: ("launch", True) in events)
    assert events == [("discard", TS), ("launch", True)]
    assert len(contexts) == 1
    assert isinstance(contexts[0], ProcessContext)
    assert contexts[0].process_id == "r1"
    doc = await coll.find_one({"_id": proc["_id"]})
    assert line in [e["message"] for e in doc["log"]]


@pytest.mark.asyncio
async def test_check_order(mongo_db):
    # invalid-request comes before every state check.
    fw, _, _, events = await _setup(mongo_db, "rfs8a", state="running")
    out = await fw.launch("r1", resume=False, session_id=None, resume_from=TS)
    assert out.reason == "invalid-request"
    out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.reason == "not-launchable"
    assert events == []
    # launch-blocked comes before no-snapshot-support.
    fw, _, _, events = await _setup(
        mongo_db, "rfs8b", state="idle", saved=True, metadata={"project": "p1"},
    )
    async with fw.block_launches({"project": "p1"}):
        out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.reason == "launch-blocked"
    assert events == []


@pytest.mark.asyncio
async def test_plain_resume_does_not_call_the_hook(mongo_db):
    fw, _, _, events = await _setup(mongo_db, "rfs9a", discard_hook=_discard_none)
    out = await fw.launch("r1", resume=True, session_id=None)
    assert out.ok
    await _wait_until(lambda: bool(events))
    assert events == [("launch", True)]
    # Without saved state a plain resume is still downgraded to a fresh run.
    fw, _, _, events = await _setup(
        mongo_db, "rfs9b", discard_hook=_discard_none, saved=False,
    )
    out = await fw.launch("r1", resume=True, session_id=None)
    assert out.ok
    await _wait_until(lambda: bool(events))
    assert events == [("launch", False)]


def test_exports():
    import optio_core
    from optio_core import (  # noqa: F401
        SnapshotNotFound, CapturePending, ListSnapshotsOutcome, list_snapshots,
    )
    assert optio_core.list_snapshots == optio_core._instance.list_snapshots
    for name in ("SnapshotNotFound", "CapturePending", "ListSnapshotsOutcome", "list_snapshots"):
        assert name in optio_core.__all__
