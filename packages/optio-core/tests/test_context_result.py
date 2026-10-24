"""Tests for ProcessContext.store_result: a task's result file.

Spec: docs/2026-10-25-factory-results-design.md §1
"""

import asyncio

import pytest
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance, ProcessStatus
from optio_core.store import (
    upsert_process, create_child_process, relaunch_reset, delete_process,
    delete_ephemeral_processes,
)


async def _dummy(ctx):
    pass


def _make_ctx(mongo_db, proc) -> ProcessContext:
    ctx = ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc.get("rootId", proc["_id"]),
        depth=proc.get("depth", 0),
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        metadata={},
    )
    # The executor gives each run its token (executor._execute_process).
    ctx._run_token = object()
    return ctx


async def _record(mongo_db, oid):
    return await mongo_db["test_processes"].find_one({"_id": oid})


async def _file_gone(mongo_db, file_id) -> bool:
    """Neither the GridFS file document nor any of its chunks is left."""
    return (
        await mongo_db["fs.files"].find_one({"_id": file_id}) is None
        and await mongo_db["fs.chunks"].count_documents({"files_id": file_id}) == 0
    )


async def _store(ctx, data: bytes, filename: str = "a.pdf") -> ObjectId:
    async with ctx.store_result(filename=filename, content_type="application/pdf") as writer:
        await writer.write(data)
    return writer.file_id


async def test_store_result_sets_the_records_result(mongo_db):
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=_dummy, process_id="res_a", name="A"))
    ctx = _make_ctx(mongo_db, proc)

    async with ctx.store_result(filename="a.pdf", content_type="application/pdf") as writer:
        await writer.write(b"%PDF")
        # The record gets its result on the block's clean exit, not before.
        assert "result" not in await _record(mongo_db, proc["_id"])

    result = (await _record(mongo_db, proc["_id"]))["result"]
    assert isinstance(result["fileId"], ObjectId)
    assert result == {
        "fileId": result["fileId"], "filename": "a.pdf",
        "contentType": "application/pdf", "size": 4,
    }
    gridfs_file = await mongo_db["fs.files"].find_one({"_id": result["fileId"]})
    assert gridfs_file["metadata"] == {
        "processId": str(proc["_id"]), "prefix": "test", "name": "a.pdf", "kind": "result",
    }
    async with ctx.load_blob(result["fileId"]) as reader:
        assert await reader.read() == b"%PDF"


async def test_a_second_store_result_raises(mongo_db):
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=_dummy, process_id="res_b", name="B"))
    ctx = _make_ctx(mongo_db, proc)
    await _store(ctx, b"first")
    first = (await _record(mongo_db, proc["_id"]))["result"]

    with pytest.raises(RuntimeError, match="already stored a result"):
        async with ctx.store_result(filename="b.pdf", content_type="application/pdf") as writer:
            await writer.write(b"second")

    assert (await _record(mongo_db, proc["_id"]))["result"] == first
    files = await mongo_db["fs.files"].find({"metadata.processId": str(proc["_id"])}).to_list(None)
    assert [f["_id"] for f in files] == [first["fileId"]]


@pytest.mark.parametrize("exc_type", [ValueError, asyncio.CancelledError])
async def test_an_exception_inside_leaves_no_result(mongo_db, exc_type):
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=_dummy, process_id="res_c", name="C"))
    ctx = _make_ctx(mongo_db, proc)

    with pytest.raises(exc_type):
        async with ctx.store_result(filename="a.pdf", content_type="application/pdf") as writer:
            # pymongo buffers chunk inserts up to 48 MB: past that, chunks
            # of the partial file are on the server.
            for _ in range(50):
                await writer.write(bytes(1 << 20))
            file_id = writer.file_id
            assert await mongo_db["fs.chunks"].count_documents({"files_id": file_id}) > 0
            raise exc_type()

    assert "result" not in await _record(mongo_db, proc["_id"])
    assert await _file_gone(mongo_db, file_id)


async def test_after_a_failed_block_the_run_may_store_its_result(mongo_db):
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=_dummy, process_id="res_d", name="D"))
    ctx = _make_ctx(mongo_db, proc)
    with pytest.raises(ValueError):
        async with ctx.store_result(filename="a.pdf", content_type="application/pdf"):
            raise ValueError()

    file_id = await _store(ctx, b"%PDF")

    assert (await _record(mongo_db, proc["_id"]))["result"]["fileId"] == file_id


async def test_reset_deletes_the_result(mongo_db):
    proc = await upsert_process(mongo_db, "test", TaskInstance(execute=_dummy, process_id="res_e", name="E"))
    ctx = _make_ctx(mongo_db, proc)
    file_id = await _store(ctx, b"%PDF")

    await relaunch_reset(
        mongo_db, "test", proc["_id"], ProcessStatus(state="scheduled"),
        ("event", "State changed to scheduled"),
    )

    assert "result" not in await _record(mongo_db, proc["_id"])
    assert await _file_gone(mongo_db, file_id)


async def _tree_with_results(mongo_db, process_id, *, ephemeral=False):
    """A record and its child, each with a result file; returns the two file ids."""
    parent = await upsert_process(mongo_db, "test", TaskInstance(execute=_dummy, process_id=process_id, name="P"))
    if ephemeral:
        await mongo_db["test_processes"].update_one({"_id": parent["_id"]}, {"$set": {"ephemeral": True}})
    child = await create_child_process(
        mongo_db, "test", parent["_id"], parent["_id"], f"{process_id}.c", "C", {}, 1, 0,
    )
    return (
        await _store(_make_ctx(mongo_db, parent), b"parent"),
        await _store(_make_ctx(mongo_db, child), b"child"),
    )


async def test_deleting_a_record_deletes_its_result_files(mongo_db):
    bystander = await _tree_with_results(mongo_db, "res_keep")

    deleted = await _tree_with_results(mongo_db, "res_del")
    await delete_process(mongo_db, "test", "res_del")
    for file_id in deleted:
        assert await _file_gone(mongo_db, file_id)

    ephemeral = await _tree_with_results(mongo_db, "res_eph", ephemeral=True)
    await delete_ephemeral_processes(mongo_db, "test")
    for file_id in ephemeral:
        assert await _file_gone(mongo_db, file_id)

    for file_id in bystander:
        assert await mongo_db["fs.files"].find_one({"_id": file_id}) is not None
