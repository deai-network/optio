"""Tests for ProcessContext resume plumbing."""

import asyncio
import logging

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process


async def _dummy(ctx):
    pass


def _make_context(mongo_db, prefix, proc, *, resume: bool = False) -> ProcessContext:
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix=prefix,
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        metadata={},
        resume=resume,
    )


async def test_process_context_resume_attribute_defaults_false(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_a", name="A")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)
    assert ctx.resume is False


async def test_process_context_resume_attribute_passes_through(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_b", name="B", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc, resume=True)
    assert ctx.resume is True


async def test_mark_has_saved_state_writes_when_supported(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_c", name="C", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc, resume=False)

    await ctx.mark_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is True


async def test_mark_has_saved_state_warns_and_noops_when_unsupported(mongo_db, caplog):
    task = TaskInstance(execute=_dummy, process_id="r_d", name="D", supports_resume=False)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    with caplog.at_level(logging.WARNING, logger="optio_core.context"):
        await ctx.mark_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is False
    assert any("supports_resume" in rec.getMessage().lower() or "resume" in rec.getMessage().lower()
               for rec in caplog.records)


async def test_mark_has_saved_state_idempotent(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_e", name="E", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    await ctx.mark_has_saved_state()
    # Second call should not raise or touch the doc when value is unchanged.
    await ctx.mark_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is True


async def test_clear_has_saved_state_writes_when_supported(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="r_f", name="F", supports_resume=True)
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"hasSavedState": True}},
    )
    ctx = _make_context(mongo_db, "test", proc)

    await ctx.clear_has_saved_state()
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is False


async def test_clear_has_saved_state_warns_when_unsupported(mongo_db, caplog):
    task = TaskInstance(execute=_dummy, process_id="r_g", name="G", supports_resume=False)
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_context(mongo_db, "test", proc)

    with caplog.at_level(logging.WARNING, logger="optio_core.context"):
        await ctx.clear_has_saved_state()
    # No write happened, state unchanged from its $setOnInsert value (False).
    updated = await mongo_db["test_processes"].find_one({"_id": proc["_id"]})
    assert updated["hasSavedState"] is False
    assert any("resume" in rec.getMessage().lower() for rec in caplog.records)
