"""Optio.list_snapshots and Optio.launch(..., resume_from=): the hook dispatch,
the refusals (decided before the discard hook runs), the order of the checks,
the guarded discard window and the log line the launch keeps.
Spec: docs/2026-11-19-resume-from-snapshot-design.md"""

import asyncio
import inspect
import time

import pytest

from optio_core import (
    CapturePending, ListSnapshotsOutcome, SnapshotNotFound,
)
from optio_core.context import ProcessContext
from optio_core.lifecycle import Optio
from optio_core.models import TaskFactory, TaskInstance

TS = "2026-11-19T13:05:12.345Z"
LINE_2 = "Resuming from the snapshot of 2026-11-19T13:05:12.345Z: 2 newer snapshots discarded"


async def _noop(ctx):
    pass


async def _wait_until(pred, timeout=60.0):
    """Poll `pred` (sync or async) until it is truthy; the timeout only bounds
    a hang."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = pred()
        if inspect.isawaitable(value):
            value = await value
        if value:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached within the hang ceiling")


async def _setup(
    mongo_db, prefix, *, list_hook=None, discard_hook=None, state="cancelled",
    saved=True, supports_resume=True, metadata=None, execute=_noop, spy=True,
    resurrect=None, unsaved=False, extra_logs=None,
):
    """An ad-hoc process "r1" with the given hooks and record state. With
    `spy`, the executor's launch_process is a spy; it and the discard hook
    record into the shared `events` list: ("launch", resume) and
    ("discard", captured_at). The spy adds the launch's extra log entries to
    `extra_logs` when one is given."""
    events: list = []
    discard = None
    if discard_hook is not None:
        async def discard(ctx, captured_at):
            events.append(("discard", captured_at))
            return await discard_hook(ctx, captured_at)
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix)
    proc = await fw.adhoc_define(TaskInstance(
        execute=execute, process_id="r1", name="R1", supports_resume=supports_resume,
        metadata=metadata or {}, list_snapshots=list_hook,
        discard_snapshots_after=discard, resurrect=resurrect,
    ))
    coll = mongo_db[f"{prefix}_processes"]
    await coll.update_one({"_id": proc["_id"]}, {"$set": {
        "status.state": state, "hasSavedState": saved,
        "supportsResume": supports_resume, "hasUnsavedWork": unsaved,
    }})

    if spy:
        async def _spy(oid, *, resume, session_id, extra_log=()):
            events.append(("launch", resume))
            if extra_logs is not None:
                extra_logs.extend(extra_log)
        fw._executor.launch_process = _spy
    return fw, coll, proc, events


async def _discard_none(ctx, captured_at):
    return 0


def _messages(doc):
    return [e["message"] for e in doc["log"]]


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
    assert fw._resuming_from == {}


@pytest.mark.asyncio
async def test_no_hook_is_no_snapshot_support(mongo_db):
    async def list_hook(ctx):
        return []
    fw, _, proc, events = await _setup(mongo_db, "rfs5", list_hook=list_hook)
    out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.ok is False and out.reason == "no-snapshot-support"
    assert events == []
    assert fw._resuming_from == {}


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
    assert fw._resuming_from == {}
    doc = await coll.find_one({"_id": proc["_id"]})
    assert not any(m.startswith("Resum") for m in _messages(doc))


@pytest.mark.asyncio
async def test_another_hook_exception_propagates_and_is_logged(mongo_db):
    async def hook(ctx, captured_at):
        raise RuntimeError("disk full")
    fw, coll, proc, events = await _setup(mongo_db, "rfs6-error", discard_hook=hook)
    with pytest.raises(RuntimeError, match="disk full"):
        await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert events == [("discard", TS)]
    assert fw._resuming_from == {}
    doc = await coll.find_one({"_id": proc["_id"]})
    assert not any("discarded" in m for m in _messages(doc))
    assert {
        "level": "error",
        "message": "Resume from the snapshot of 2026-11-19T13:05:12.345Z failed: disk full",
    } in [{"level": e["level"], "message": e["message"]} for e in doc["log"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("discarded, line", [
    (2, LINE_2),
    (1, "Resuming from the snapshot of 2026-11-19T13:05:12.345Z: 1 newer snapshot discarded"),
])
async def test_success_discards_then_resumes(mongo_db, discarded, line):
    contexts = []
    extra: list = []

    async def hook(ctx, captured_at):
        contexts.append(ctx)
        return discarded
    fw, coll, proc, events = await _setup(
        mongo_db, f"rfs7-{discarded}", discard_hook=hook, extra_logs=extra,
    )
    out = await fw.launch("r1", resume=True, session_id="s1", resume_from=TS)
    assert out.ok
    await _wait_until(lambda: ("launch", True) in events)
    assert events == [("discard", TS), ("launch", True)]
    assert len(contexts) == 1
    assert isinstance(contexts[0], ProcessContext)
    assert contexts[0].process_id == "r1"
    assert contexts[0].session_id == "s1"
    assert contexts[0].resume is False
    # The line goes to the launch, which writes it after resetting the log.
    assert extra == [("event", line)]
    assert fw._resuming_from == {}


@pytest.mark.asyncio
async def test_the_line_survives_the_real_launch(mongo_db):
    release = asyncio.Event()

    async def execute(ctx):
        await release.wait()

    async def hook(ctx, captured_at):
        return 2
    fw, coll, proc, events = await _setup(
        mongo_db, "rfs10", discard_hook=hook, execute=execute, spy=False,
    )

    async def state_is(state):
        return (await coll.find_one({"_id": proc["_id"]}))["status"]["state"] == state
    try:
        out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
        assert out.ok
        await _wait_until(lambda: state_is("running"))
        messages = _messages(await coll.find_one({"_id": proc["_id"]}))
        assert LINE_2 in messages
        assert messages.index("State changed to scheduled") < messages.index(LINE_2)
    finally:
        release.set()
    await _wait_until(lambda: state_is("done"))


@pytest.mark.asyncio
async def test_the_discard_window_refuses_a_launch_and_a_resurrect(mongo_db):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hook(ctx, captured_at):
        entered.set()
        await release.wait()
        return 1
    fw, _, proc, events = await _setup(
        mongo_db, "rfs11", discard_hook=hook, resurrect=_noop, unsaved=True,
    )
    pending = asyncio.create_task(
        fw.launch("r1", resume=True, session_id=None, resume_from=TS),
    )
    try:
        await asyncio.wait_for(entered.wait(), 60)
        assert fw._resume_from_in_progress(proc)
        plain = await fw.launch("r1", resume=True, session_id=None)
        assert plain.ok is False and plain.reason == "not-launchable"
        again = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
        assert again.ok is False and again.reason == "not-launchable"
        resurrected = await fw.resurrect("r1", session_id=None)
        assert resurrected.ok is False and resurrected.reason == "not-launchable"
        assert events == [("discard", TS)]
    finally:
        release.set()
    out = await asyncio.wait_for(pending, 60)
    assert out.ok
    await _wait_until(lambda: ("launch", True) in events)
    assert events == [("discard", TS), ("launch", True)]
    assert fw._resuming_from == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("called_with", ["process-id", "upper-object-id"])
async def test_a_launch_during_the_resume_froms_first_await_never_both_proceed(
    mongo_db, called_with,
):
    """The resume_from is held at its first await (the record's read); a plain
    launch (by the record's ObjectId hex) and a resurrect arrive meanwhile.
    Exactly one launch runs, and no snapshot is discarded after a competing
    launch won. An upper-case ObjectId resolves as the lower-case one does,
    and matches it."""
    fw, _, proc, events = await _setup(
        mongo_db, f"rfs14-{called_with}", discard_hook=_discard_none,
        resurrect=_noop, unsaved=True,
    )
    target = "r1" if called_with == "process-id" else str(proc["_id"]).upper()
    real_resolve = fw._resolve
    gate, at_gate = asyncio.Event(), asyncio.Event()
    calls = []

    async def gated_resolve(id_str):
        calls.append(id_str)
        if len(calls) == 1:
            at_gate.set()
            await gate.wait()
        return await real_resolve(id_str)
    fw._resolve = gated_resolve
    pending = asyncio.create_task(
        fw.launch(target, resume=True, session_id=None, resume_from=TS),
    )
    try:
        await asyncio.wait_for(at_gate.wait(), 60)
        plain = await fw.launch(str(proc["_id"]), resume=True, session_id=None)
        resurrected = await fw.resurrect(str(proc["_id"]), session_id=None)
    finally:
        gate.set()
    out = await asyncio.wait_for(pending, 60)
    await _wait_until(lambda: any(e[0] == "launch" for e in events))
    launches = [e for e in events if e[0] == "launch"]
    assert len(launches) == 1
    assert not (plain.ok and out.ok)
    if plain.ok:
        assert not any(e[0] == "discard" for e in events)
    # The marker was set before the read: the plain launch is the one refused.
    assert plain.ok is False and plain.reason == "not-launchable"
    assert resurrected.ok is False and resurrected.reason == "not-launchable"
    assert out.ok
    assert events == [("discard", TS), ("launch", True)]
    assert fw._resuming_from == {}


@pytest.mark.asyncio
async def test_a_launch_in_flight_refuses_the_resume_from(mongo_db):
    """A plain launch scheduled just before the resume_from, whose run has not
    written its state yet: the resume_from is refused and discards nothing."""
    fw, _, proc, events = await _setup(mongo_db, "rfs15", discard_hook=_discard_none)
    hold = asyncio.Event()

    async def _slow_spy(oid, *, resume, session_id, extra_log=()):
        events.append(("launch", resume))
        await hold.wait()
    fw._executor.launch_process = _slow_spy
    try:
        plain = await fw.launch("r1", resume=True, session_id=None)
        assert plain.ok
        out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
        assert out.ok is False and out.reason == "not-launchable"
        assert events == [("launch", True)]
    finally:
        hold.set()
    await _wait_until(lambda: proc["_id"] not in fw._launching)
    assert fw._resuming_from == {}


@pytest.mark.asyncio
async def test_an_upper_case_object_id_is_matched_during_the_hook(mongo_db):
    """A resume_from called with the upper-case ObjectId (which _resolve
    accepts) holds the record's canonical id while its hook runs: a plain
    launch by processId and a resurrect are refused."""
    entered, release = asyncio.Event(), asyncio.Event()

    async def hook(ctx, captured_at):
        entered.set()
        await release.wait()
        return 1
    fw, _, proc, events = await _setup(
        mongo_db, "rfs16", discard_hook=hook, resurrect=_noop, unsaved=True,
    )
    pending = asyncio.create_task(fw.launch(
        str(proc["_id"]).upper(), resume=True, session_id=None, resume_from=TS,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 60)
        plain = await fw.launch("r1", resume=True, session_id=None)
        assert plain.ok is False and plain.reason == "not-launchable"
        resurrected = await fw.resurrect("r1", session_id=None)
        assert resurrected.ok is False and resurrected.reason == "not-launchable"
    finally:
        release.set()
    out = await asyncio.wait_for(pending, 60)
    assert out.ok
    await _wait_until(lambda: ("launch", True) in events)
    assert events == [("discard", TS), ("launch", True)]


@pytest.mark.asyncio
async def test_a_launch_in_flight_refuses_a_resurrect(mongo_db):
    """A launch scheduled whose run has not written its state yet (e.g. the
    resume a resume_from just scheduled): a resurrect is refused."""
    fw, _, proc, events = await _setup(
        mongo_db, "rfs17", discard_hook=_discard_none, resurrect=_noop, unsaved=True,
    )
    hold = asyncio.Event()

    async def _slow_spy(oid, *, resume, session_id, extra_log=()):
        events.append(("launch", resume))
        await hold.wait()
    fw._executor.launch_process = _slow_spy
    try:
        out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
        assert out.ok
        assert fw._resuming_from == {}
        resurrected = await fw.resurrect("r1", session_id=None)
        assert resurrected.ok is False and resurrected.reason == "not-launchable"
        assert proc["_id"] not in fw._resurrecting
    finally:
        hold.set()
    await _wait_until(lambda: proc["_id"] not in fw._launching)
    assert events == [("discard", TS), ("launch", True)]


@pytest.mark.asyncio
async def test_shutdown_before_the_hook_deletes_nothing(mongo_db):
    """A shutdown begun while the resume_from waits before its hook (here at
    its record's read) refuses it before the hook runs."""
    fw, _, _, events = await _setup(mongo_db, "rfs18", discard_hook=_discard_none)
    real_resolve = fw._resolve
    gate, at_gate = asyncio.Event(), asyncio.Event()
    calls = []

    async def gated_resolve(id_str):
        calls.append(id_str)
        if len(calls) == 1:
            at_gate.set()
            await gate.wait()
        return await real_resolve(id_str)
    fw._resolve = gated_resolve
    pending = asyncio.create_task(
        fw.launch("r1", resume=True, session_id=None, resume_from=TS),
    )
    try:
        await asyncio.wait_for(at_gate.wait(), 60)
        await fw.shutdown(grace_seconds=0.1)
    finally:
        gate.set()
    out = await asyncio.wait_for(pending, 60)
    assert out.ok is False and out.reason == "shutting-down"
    assert events == []
    assert fw._resuming_from == {}


@pytest.mark.asyncio
async def test_a_failing_failure_log_keeps_the_hooks_error(mongo_db, monkeypatch, caplog):
    """When the failure line cannot be written, that is logged, and the hook's
    own error propagates."""
    import logging
    from optio_core import lifecycle as L

    async def hook(ctx, captured_at):
        raise RuntimeError("disk full")
    fw, _, _, events = await _setup(mongo_db, "rfs19", discard_hook=hook)

    async def _boom(*args, **kwargs):
        raise OSError("mongo down")
    monkeypatch.setattr(L, "append_log", _boom)
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError, match="disk full"):
        await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert events == [("discard", TS)]
    assert fw._resuming_from == {}
    assert any(
        r.name == "optio_core_core" and r.exc_info and "mongo down" in str(r.exc_info[1])
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_shutdown_during_the_discard_is_shutting_down(mongo_db):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hook(ctx, captured_at):
        entered.set()
        await release.wait()
        return 2
    fw, coll, proc, events = await _setup(mongo_db, "rfs12", discard_hook=hook)
    pending = asyncio.create_task(
        fw.launch("r1", resume=True, session_id=None, resume_from=TS),
    )
    try:
        await asyncio.wait_for(entered.wait(), 60)
        await fw.shutdown(grace_seconds=0.1)
    finally:
        release.set()
    out = await asyncio.wait_for(pending, 60)
    assert out.ok is False and out.reason == "shutting-down"
    assert events == [("discard", TS)]
    assert fw._resuming_from == {}
    # No launch resets the log: the deletion is recorded there at once.
    assert LINE_2 in _messages(await coll.find_one({"_id": proc["_id"]}))


@pytest.mark.asyncio
async def test_check_order(mongo_db):
    # invalid-request comes before every state check.
    fw, _, _, events = await _setup(mongo_db, "rfs8a", state="running")
    out = await fw.launch("r1", resume=False, session_id=None, resume_from=TS)
    assert out.reason == "invalid-request"
    out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.reason == "not-launchable"
    # invalid-request comes before not-found.
    out = await fw.launch("missing", resume=False, session_id=None, resume_from=TS)
    assert out.reason == "invalid-request"
    assert events == []
    # launch-blocked comes before no-snapshot-support.
    fw, _, _, events = await _setup(
        mongo_db, "rfs8b", state="idle", saved=True, metadata={"project": "p1"},
    )
    async with fw.block_launches({"project": "p1"}):
        out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.reason == "launch-blocked"
    assert events == []
    # no saved state (not-launchable) comes before launch-blocked.
    fw, _, _, events = await _setup(
        mongo_db, "rfs8c", state="idle", saved=False, metadata={"project": "p1"},
        discard_hook=_discard_none,
    )
    async with fw.block_launches({"project": "p1"}):
        out = await fw.launch("r1", resume=True, session_id=None, resume_from=TS)
    assert out.reason == "not-launchable"
    assert events == []
    # shutting-down comes before invalid-request.
    await fw.shutdown(grace_seconds=0.1)
    out = await fw.launch("r1", resume=False, session_id=None, resume_from=TS)
    assert out.reason == "shutting-down"
    assert events == []


@pytest.mark.asyncio
async def test_resume_from_a_kept_launch(mongo_db):
    """A TaskFactory's kept launch (a task_for instance with the hook) resumes
    from a snapshot through _relaunch_refusal and _launch_record; without saved
    state it is refused before the hook."""
    events: list = []

    async def discard(ctx, captured_at):
        events.append(("discard", ctx.process_id, captured_at))
        return 1

    def task_for(values, process_id):
        return TaskInstance(
            execute=_noop, process_id=process_id, name="Kept",
            supports_resume=True, discard_snapshots_after=discard,
        )
    factory = TaskFactory(
        process_id="fac", name="Fac", ttl_seconds=3600, task_for=task_for,
        parameters=[{"kind": "boolean", "id": "dry", "label": "Dry run", "required": False}],
    )

    async def get_defs(_services, metadata_filter=None):
        return [factory]
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="rfs13", get_task_definitions=get_defs)

    async def _spy(oid, *, resume, session_id, extra_log=()):
        events.append(("launch", resume))
    fw._executor.launch_process = _spy
    first = await fw.launch("fac", session_id=None, params={"dry": True})
    assert first.ok
    pid = first.proc["processId"]
    await _wait_until(lambda: ("launch", False) in events)
    coll = mongo_db["rfs13_processes"]
    await coll.update_one({"processId": pid}, {"$set": {
        "status.state": "cancelled", "hasSavedState": False,
    }})
    events.clear()
    out = await fw.launch(pid, resume=True, session_id=None, resume_from=TS)
    assert out.ok is False and out.reason == "not-launchable"
    assert events == []
    await coll.update_one({"processId": pid}, {"$set": {"hasSavedState": True}})
    out = await fw.launch(pid, resume=True, session_id=None, resume_from=TS)
    assert out.ok
    await _wait_until(lambda: ("launch", True) in events)
    assert events == [("discard", pid, TS), ("launch", True)]


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
