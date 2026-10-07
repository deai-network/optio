"""Resurrect: process fields, context methods, launch clears hasUnsavedWork."""

import pytest
from bson import ObjectId

from optio_core import NothingToResurrect, ResurrectOutcome
from optio_core.context import ProcessContext
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance


async def _noop(ctx):
    pass


async def _hook(ctx):
    pass


def _ctx(mongo_db, prefix, oid, process_id="p"):
    import asyncio
    return ProcessContext(
        process_oid=oid, process_id=process_id, root_oid=oid, depth=0,
        params={}, services={}, db=mongo_db, prefix=prefix,
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


def test_exports():
    assert issubclass(NothingToResurrect, Exception)
    out = ResurrectOutcome(ok=False, reason="not-resurrectable")
    assert out.ok is False and out.reason == "not-resurrectable" and out.proc is None


@pytest.mark.asyncio
async def test_task_sync_sets_supports_resurrect(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="rflags1")
    with_hook = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="with", name="With",
        supports_resume=True, resurrect=_hook,
    ))
    without = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="without", name="Without",
    ))
    coll = mongo_db["rflags1_processes"]
    assert (await coll.find_one({"_id": with_hook["_id"]}))["supportsResurrect"] is True
    assert (await coll.find_one({"_id": with_hook["_id"]}))["hasUnsavedWork"] is False
    assert (await coll.find_one({"_id": without["_id"]}))["supportsResurrect"] is False


@pytest.mark.asyncio
async def test_resurrect_requires_supports_resume_at_sync(mongo_db):
    fw = Optio()

    async def tasks(_services, _metadata_filter):
        return [TaskInstance(
            execute=_noop, process_id="bad", name="Bad", resurrect=_hook,
        )]

    with pytest.raises(ValueError, match="resurrect requires supports_resume=True"):
        await fw.init(mongo_db=mongo_db, prefix="rflags2", get_task_definitions=tasks)


@pytest.mark.asyncio
async def test_mark_and_clear_unsaved_work(mongo_db):
    oid = ObjectId()
    coll = mongo_db["rflags3_processes"]
    await coll.insert_one({"_id": oid, "processId": "p", "supportsResurrect": True})
    ctx = _ctx(mongo_db, "rflags3", oid)
    await ctx.mark_unsaved_work()
    assert (await coll.find_one({"_id": oid}))["hasUnsavedWork"] is True
    await ctx.clear_unsaved_work()
    assert (await coll.find_one({"_id": oid}))["hasUnsavedWork"] is False


@pytest.mark.asyncio
async def test_mark_unsaved_work_ignored_without_resurrect_support(mongo_db, caplog):
    oid = ObjectId()
    coll = mongo_db["rflags4_processes"]
    await coll.insert_one({"_id": oid, "processId": "p", "supportsResurrect": False})
    ctx = _ctx(mongo_db, "rflags4", oid)
    await ctx.mark_unsaved_work()
    assert "hasUnsavedWork" not in await coll.find_one({"_id": oid})
    assert "supportsResurrect" in caplog.text or "resurrect" in caplog.text


@pytest.mark.asyncio
async def test_clear_unsaved_work_silent_when_already_clear(mongo_db, caplog):
    oid = ObjectId()
    coll = mongo_db["rflags5_processes"]
    await coll.insert_one({"_id": oid, "processId": "p"})
    ctx = _ctx(mongo_db, "rflags5", oid)
    await ctx.clear_unsaved_work()
    assert "hasUnsavedWork" not in await coll.find_one({"_id": oid})
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_store_blob_with_given_file_id(mongo_db):
    oid = ObjectId()
    await mongo_db["rflags6_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = _ctx(mongo_db, "rflags6", oid)
    wanted = ObjectId()
    async with ctx.store_blob("workdir", file_id=wanted) as w:
        await w.write(b"abc")
    assert w.file_id == wanted
    f = await mongo_db["fs.files"].find_one({"_id": wanted})
    assert f["metadata"] == {"processId": str(oid), "prefix": "rflags6", "name": "workdir"}


@pytest.mark.asyncio
async def test_launch_clears_unsaved_work(mongo_db):
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix="rflags7")
    proc = await fw.adhoc_define(TaskInstance(
        execute=_noop, process_id="p7", name="P7",
        supports_resume=True, resurrect=_hook,
    ))
    coll = mongo_db["rflags7_processes"]
    await coll.update_one({"_id": proc["_id"]}, {"$set": {"hasUnsavedWork": True}})
    await fw._executor.launch_process(str(proc["_id"]), session_id=None)
    assert (await coll.find_one({"_id": proc["_id"]}))["hasUnsavedWork"] is False
