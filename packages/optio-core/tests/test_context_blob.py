"""Tests for ProcessContext GridFS blob helpers."""

import asyncio
import hashlib
import os

from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process


async def _dummy(ctx):
    pass


def _make_ctx(mongo_db, proc) -> ProcessContext:
    return ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        metadata={},
    )


async def test_store_and_load_blob_small(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="blob_a", name="A")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    payload = b"hello blob"
    async with ctx.store_blob("session") as writer:
        await writer.write(payload)
        file_id = writer.file_id

    assert isinstance(file_id, ObjectId)

    async with ctx.load_blob(file_id) as reader:
        got = await reader.read()
    assert got == payload


async def test_store_blob_records_metadata(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="blob_b", name="B")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    async with ctx.store_blob("workdir") as writer:
        await writer.write(b"x")
        file_id = writer.file_id

    files = mongo_db["fs.files"]
    meta = await files.find_one({"_id": file_id})
    assert meta is not None
    assert meta["metadata"]["processId"] == str(proc["_id"])
    assert meta["metadata"]["prefix"] == "test"
    assert meta["metadata"]["name"] == "workdir"


async def test_delete_blob_removes_it(mongo_db):
    task = TaskInstance(execute=_dummy, process_id="blob_c", name="C")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    async with ctx.store_blob("session") as writer:
        await writer.write(b"data")
        file_id = writer.file_id

    await ctx.delete_blob(file_id)

    files = mongo_db["fs.files"]
    assert await files.find_one({"_id": file_id}) is None


async def test_store_blob_large_roundtrip(mongo_db):
    """100 MB payload — shakes out chunking."""
    task = TaskInstance(execute=_dummy, process_id="blob_d", name="D")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(mongo_db, proc)

    chunk = os.urandom(1 << 20)  # 1 MiB of random data
    total = 100
    hasher = hashlib.sha256()

    async with ctx.store_blob("big") as writer:
        for _ in range(total):
            await writer.write(chunk)
            hasher.update(chunk)
        file_id = writer.file_id
    expected_digest = hasher.hexdigest()

    got_hasher = hashlib.sha256()
    async with ctx.load_blob(file_id) as reader:
        while True:
            block = await reader.read(1 << 20)
            if not block:
                break
            got_hasher.update(block)
    assert got_hasher.hexdigest() == expected_digest
