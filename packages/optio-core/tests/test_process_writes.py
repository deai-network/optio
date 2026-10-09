"""Each step of a process's life is one write on its document.

Spec: docs/2026-10-09-fewer-process-writes-design.md
"""

import asyncio
import time

import pytest

from optio_core.executor import Executor
from optio_core.models import TaskInstance
from optio_core.store import upsert_process
from process_write_counter import CountingDb

# A hang ceiling only: every wait below ends on an event, not on time.
_HANG = 60


def _entries(doc):
    return [(e["level"], e["message"]) for e in doc["log"]]


def _messages(doc):
    return [e["message"] for e in doc["log"]]


async def _run(mongo_db, task, *, register=True, session_id=None):
    """Launch `task` once through a fresh executor on a counting database."""
    oid = (await upsert_process(mongo_db, "test", task))["_id"]
    db = CountingDb(mongo_db, "test")
    executor = Executor(db, "test", {})
    if register:
        executor.register_tasks([task])
    state = await executor.launch_process(task.process_id, session_id=session_id)
    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    return db, oid, state, doc


async def _noop(ctx):  # noqa: ARG001
    return None


async def test_top_level_run_without_messages_is_three_updates(mongo_db):
    task = TaskInstance(execute=_noop, process_id="w.plain", name="Plain")

    db, oid, state, doc = await _run(mongo_db, task)

    assert state == "done"
    assert [(w.op, w.oid) for w in db.writes] == [("update", oid)] * 3
    assert _messages(doc) == [
        "State changed to scheduled", "State changed to running", "State changed to done",
    ]


async def test_start_update_carries_session_id_and_running_line(mongo_db):
    task = TaskInstance(execute=_noop, process_id="w.session", name="Session")

    db, oid, _, doc = await _run(mongo_db, task, session_id="tok-1")

    start = db.updates_on(oid)[1]
    assert start["$set"]["status"]["state"] == "running"
    assert start["$set"]["originatingSessionId"] == "tok-1"
    assert start["$push"]["log"]["message"] == "State changed to running"
    assert doc["originatingSessionId"] == "tok-1"


_END_LINES = {
    "done": ("done", ("event", "State changed to done")),
    "failed": ("failed", ("error", "boom")),
    "cancelled_returned": ("cancelled", ("event", "State changed to cancelled")),
    "cancelled_raised": (
        "cancelled", ("event", "State changed to cancelled (raised CancelledError)"),
    ),
}


@pytest.mark.parametrize("arm", list(_END_LINES))
async def test_end_update_carries_status_line_and_widget_clear(mongo_db, arm):
    running = asyncio.Event()

    async def body(ctx):
        await ctx.set_widget_upstream("http://w")
        if arm == "failed":
            raise ValueError("boom")
        if arm.startswith("cancelled"):
            running.set()
            await ctx.cancellation_flag.wait()
            if arm == "cancelled_raised":
                raise asyncio.CancelledError()

    task = TaskInstance(execute=body, process_id=f"w.end.{arm}", name="End")
    oid = (await upsert_process(mongo_db, "test", task))["_id"]
    db = CountingDb(mongo_db, "test")
    executor = Executor(db, "test", {})
    executor.register_tasks([task])
    run = asyncio.create_task(executor.launch_process(task.process_id, session_id=None))
    if arm.startswith("cancelled"):
        await asyncio.wait_for(running.wait(), _HANG)
        executor.request_cancel_with_deadline(oid, deadline=time.monotonic() + 3600)
    if arm == "cancelled_raised":
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, _HANG)
    else:
        await asyncio.wait_for(run, _HANG)

    state, line = _END_LINES[arm]
    end = db.updates_on(oid)[-1]
    assert end["$set"]["status"]["state"] == state
    assert end["$set"]["widgetUpstream"] is None
    assert (end["$push"]["log"]["level"], end["$push"]["log"]["message"]) == line
    doc = await mongo_db["test_processes"].find_one({"_id": oid})
    assert doc["widgetUpstream"] is None
    assert _entries(doc)[-1] == line


async def test_end_update_sets_expire_at_when_the_task_has_a_ttl(mongo_db):
    with_ttl = TaskInstance(execute=_noop, process_id="w.ttl", name="TTL", ttl_seconds=3600)
    without = TaskInstance(execute=_noop, process_id="w.nottl", name="No TTL")

    db, oid, _, doc = await _run(mongo_db, with_ttl)
    db2, oid2, _, _ = await _run(mongo_db, without)

    assert "expireAt" in db.updates_on(oid)[-1]["$set"]
    assert doc["expireAt"] is not None
    assert "expireAt" not in db2.updates_on(oid2)[-1]["$set"]


async def test_no_execute_function_ends_in_one_update_with_widget_clear(mongo_db):
    task = TaskInstance(execute=_noop, process_id="w.noexec", name="No exec")

    db, oid, state, _ = await _run(mongo_db, task, register=False)

    assert state == "failed"
    updates = db.updates_on(oid)
    assert len(updates) == 3
    end = updates[-1]
    assert end["$set"]["status"]["error"] == "No execute function found"
    assert end["$set"]["widgetUpstream"] is None
    assert "$push" not in end


async def test_two_messages_add_one_update_each(mongo_db):
    async def body(ctx):
        ctx.report_progress(None, "a")
        ctx.report_progress(50, "b", level="warning")

    task = TaskInstance(execute=body, process_id="w.msgs", name="Messages")

    db, oid, _, doc = await _run(mongo_db, task)

    assert len(db.updates_on(oid)) == 5
    assert _entries(doc) == [
        ("event", "State changed to scheduled"), ("event", "State changed to running"),
        ("info", "a"), ("warning", "b"), ("event", "State changed to done"),
    ]


async def test_percent_only_progress_writes_no_log_line(mongo_db):
    async def body(ctx):
        ctx.report_progress(30)

    task = TaskInstance(execute=body, process_id="w.pct", name="Percent")

    db, oid, _, doc = await _run(mongo_db, task)

    updates = db.updates_on(oid)
    assert len(updates) == 4
    assert updates[2] == {"$set": {"progress": {"percent": 30, "message": None}}}
    assert len(doc["log"]) == 3


async def _child(ctx):
    ctx.report_progress(None, "c1")


async def _parent(ctx):
    await ctx.run_child(_child, "w.parent.c", "C")


async def test_child_run_is_insert_and_three_updates_plus_one_on_the_parent(mongo_db):
    task = TaskInstance(execute=_parent, process_id="w.parent", name="Parent")

    db, oid, state, doc = await _run(mongo_db, task)

    assert state == "done"
    child = await mongo_db["test_processes"].find_one({"parentId": oid})
    assert [w.oid for w in db.writes if w.op == "insert"] == [child["_id"]]
    assert len(db.updates_on(child["_id"])) == 3  # start, "c1", end
    assert len(db.updates_on(oid)) == 4
    assert "Spawned child: C" in _messages(doc)
    assert _messages(child) == ["State changed to running", "c1", "State changed to done"]


async def test_relaunch_deletes_the_previous_runs_children(mongo_db):
    task = TaskInstance(execute=_parent, process_id="w.again", name="Again")
    oid = (await upsert_process(mongo_db, "test", task))["_id"]
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])

    await executor.launch_process("w.again", session_id=None)
    await executor.launch_process("w.again", session_id=None)

    assert await mongo_db["test_processes"].count_documents({"parentId": oid}) == 1
