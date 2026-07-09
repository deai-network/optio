import asyncio
from datetime import datetime
import pytest
from optio_core.models import TaskInstance
from optio_core.store import (
    upsert_process,
    append_browser_open_request,
    append_session_event,
)
from optio_core.context import ProcessContext
from optio_core.executor import Executor


def _ctx(db, proc) -> ProcessContext:
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={},
        metadata={},
    )


@pytest.mark.asyncio
async def test_append_browser_open_request(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="b1", name="B1"))
    rid = await append_browser_open_request(mongo_db, "test", proc["_id"], "https://x")
    assert isinstance(rid, str) and len(rid) == 32
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert len(doc["browserOpenRequests"]) == 1
    entry = doc["browserOpenRequests"][0]
    assert entry["requestId"] == rid and entry["url"] == "https://x"
    assert isinstance(entry["createdAt"], datetime)  # stamped for staleness expiry


@pytest.mark.asyncio
async def test_append_session_event_attention_and_client(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="s1", name="S1"))
    r1 = await append_session_event(mongo_db, "test", proc["_id"], {"type": "attention", "reason": "help"})
    r2 = await append_session_event(mongo_db, "test", proc["_id"], {"type": "client", "keyword": "k", "data": {"n": 1}})
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["sessionEvents"] == [
        {"requestId": r1, "type": "attention", "reason": "help"},
        {"requestId": r2, "type": "client", "keyword": "k", "data": {"n": 1}},
    ]


@pytest.mark.asyncio
async def test_ctx_request_browser_open(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="c1", name="C1"))
    ctx = _ctx(mongo_db, proc)
    rid = await ctx.request_browser_open("https://repo")
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    entry = doc["browserOpenRequests"][0]
    assert entry["requestId"] == rid and entry["url"] == "https://repo"
    assert isinstance(entry["createdAt"], datetime)


@pytest.mark.asyncio
async def test_ctx_need_attention_and_client_message(mongo_db):
    async def noop(ctx):
        pass
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=noop, process_id="c2", name="C2"))
    ctx = _ctx(mongo_db, proc)
    ra = await ctx.need_attention("look here")
    rd = await ctx.client_message("alert", {"level": "high"})
    doc = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert doc["sessionEvents"] == [
        {"requestId": ra, "type": "attention", "reason": "look here"},
        {"requestId": rd, "type": "client", "keyword": "alert", "data": {"level": "high"}},
    ]


@pytest.mark.asyncio
async def test_launch_writes_originating_session_id(mongo_db):
    async def noop(ctx):
        pass
    task = TaskInstance(execute=noop, process_id="o1", name="O1")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    state = await executor.launch_process("o1", session_id="tok-123")
    assert state == "done"
    doc = await mongo_db["test_processes"].find_one({"processId": "o1"})
    assert doc["originatingSessionId"] == "tok-123"


@pytest.mark.asyncio
async def test_launch_none_session_id_writes_null(mongo_db):
    async def noop(ctx):
        pass
    task = TaskInstance(execute=noop, process_id="o2", name="O2")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("o2", session_id=None)
    doc = await mongo_db["test_processes"].find_one({"processId": "o2"})
    assert doc["originatingSessionId"] is None


@pytest.mark.asyncio
async def test_child_inherits_parent_session_id(mongo_db):
    async def child(ctx):
        pass
    async def parent(ctx):
        await ctx.run_child(execute=child, process_id="kid", name="Kid")
    task = TaskInstance(execute=parent, process_id="root-si", name="RootSI")
    await upsert_process(mongo_db, "test", task)
    executor = Executor(mongo_db, "test", {})
    executor.register_tasks([task])
    await executor.launch_process("root-si", session_id="parent-tok")
    child_doc = await mongo_db["test_processes"].find_one({"processId": "kid"})
    assert child_doc["originatingSessionId"] == "parent-tok"
