"""In-process upload-writer registry round-trip.

ctx.register_upload_writer stores a writer on the owning Optio keyed by the
process ObjectId; Optio.materialize_upload resolves it by process_id (OID hex
or processId, like cancel) and invokes it. clear_upload_writer removes it.
"""

import asyncio

import pytest

from optio_core.context import ProcessContext
from optio_core.exceptions import NoUploadWriter
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import upsert_process


async def _dummy(ctx):
    pass


def _make_ctx(optio: Optio, mongo_db, proc) -> ProcessContext:
    ctx = ProcessContext(
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
    # Mirror the executor's post-construction wiring so the context can reach
    # its owning Optio (where the registry lives).
    ctx._executor = optio._executor
    return ctx


async def test_register_and_materialize(mongo_db):
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")
    task = TaskInstance(execute=_dummy, process_id="up_1", name="U")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(optio, mongo_db, proc)

    seen = []

    async def writer(filename, data):
        seen.append((filename, data))
        return f"uploads/{filename}"

    ctx.register_upload_writer(writer)
    rel = await optio.materialize_upload(ctx.process_id, b"x", "f.txt")
    assert rel == "uploads/f.txt"
    assert seen == [("f.txt", b"x")]


async def test_materialize_resolves_by_oid_hex(mongo_db):
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")
    task = TaskInstance(execute=_dummy, process_id="up_oid", name="U")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(optio, mongo_db, proc)

    async def writer(filename, data):
        return f"uploads/{filename}"

    ctx.register_upload_writer(writer)
    rel = await optio.materialize_upload(str(proc["_id"]), b"x", "g.txt")
    assert rel == "uploads/g.txt"


async def test_materialize_unknown_process_raises(mongo_db):
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")
    with pytest.raises(NoUploadWriter):
        await optio.materialize_upload("does-not-exist", b"x", "f.txt")


async def test_clear_removes_writer(mongo_db):
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")
    task = TaskInstance(execute=_dummy, process_id="up_2", name="U2")
    proc = await upsert_process(mongo_db, "test", task)
    ctx = _make_ctx(optio, mongo_db, proc)

    async def writer(filename, data):
        return "uploads/x"

    ctx.register_upload_writer(writer)
    ctx.clear_upload_writer()
    with pytest.raises(NoUploadWriter):
        await optio.materialize_upload(ctx.process_id, b"x", "f.txt")
