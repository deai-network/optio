"""Tests for the sweep of result files whose process record is gone, and the
default bucket's index the result files' queries use.

Spec: docs/2026-10-25-factory-results-design.md §1 "Cleanup"
"""

import asyncio
import logging
import time

from bson import ObjectId

from optio_core import lifecycle, store
from optio_core.context import ProcessContext
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import (
    upsert_process, create_child_process, purge_processes, remove_stale_processes,
    sweep_orphan_results, ensure_result_file_index,
)

RESULT_FILE_INDEX_KEYS = [("metadata.kind", 1), ("metadata.prefix", 1), ("metadata.processId", 1)]


async def _dummy(ctx):
    pass


async def _wait_until(pred, timeout=60.0):
    """Poll `pred` until it returns something truthy; the timeout only bounds
    a hang."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = await pred()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached within the hang ceiling")


def _make_ctx(mongo_db, proc, prefix) -> ProcessContext:
    ctx = ProcessContext(
        process_oid=proc["_id"],
        process_id=proc["processId"],
        root_oid=proc.get("rootId", proc["_id"]),
        depth=proc.get("depth", 0),
        params={},
        services={},
        db=mongo_db,
        prefix=prefix,
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        metadata={},
    )
    # The executor gives each run its token (executor._execute_process).
    ctx._run_token = object()
    return ctx


async def _store(ctx, data: bytes = b"%PDF") -> ObjectId:
    async with ctx.store_result(filename="a.pdf", content_type="application/pdf") as writer:
        await writer.write(data)
    return writer.file_id


async def _with_result(mongo_db, prefix, process_id, **task_kwargs):
    """A root record with a result file; returns (record, file id)."""
    proc = await upsert_process(
        mongo_db, prefix, TaskInstance(execute=_dummy, process_id=process_id, name="P", **task_kwargs),
    )
    return proc, await _store(_make_ctx(mongo_db, proc, prefix))


async def _orphan(mongo_db, prefix, process_id) -> ObjectId:
    """A result file whose record was removed by a raw delete, as MongoDB's
    TTL monitor removes one: no optio code runs. Returns the file id."""
    proc, file_id = await _with_result(mongo_db, prefix, process_id)
    await mongo_db[f"{prefix}_processes"].delete_one({"_id": proc["_id"]})
    return file_id


async def _file_gone(mongo_db, file_id) -> bool:
    """Neither the GridFS file document nor any of its chunks is left."""
    return (
        await mongo_db["fs.files"].find_one({"_id": file_id}) is None
        and await mongo_db["fs.chunks"].count_documents({"files_id": file_id}) == 0
    )


async def _file_kept(mongo_db, file_id) -> bool:
    return (
        await mongo_db["fs.files"].find_one({"_id": file_id}) is not None
        and await mongo_db["fs.chunks"].count_documents({"files_id": file_id}) > 0
    )


async def test_sweep_deletes_results_of_missing_records(mongo_db):
    _, kept = await _with_result(mongo_db, "test", "res_keep")
    gone = await upsert_process(mongo_db, "test", TaskInstance(execute=_dummy, process_id="res_gone", name="G"))
    gone_ctx = _make_ctx(mongo_db, gone, "test")
    orphan = await _store(gone_ctx)
    # A store_blob file (no `kind`) of the same record: not a result file.
    async with gone_ctx.store_blob("snapshot") as writer:
        await writer.write(b"state")
    blob = writer.file_id
    await mongo_db["test_processes"].delete_one({"_id": gone["_id"]})
    other_prefix = await _orphan(mongo_db, "other", "res_other")

    assert await sweep_orphan_results(mongo_db, "test") == 1

    assert await _file_gone(mongo_db, orphan)
    assert await _file_kept(mongo_db, kept)
    assert await _file_kept(mongo_db, blob)
    assert await _file_kept(mongo_db, other_prefix)


async def test_sweep_deletes_what_bulk_deletions_leave(mongo_db):
    """purge_processes deletes its roots' descendants' result files, not the
    roots' own; remove_stale_processes deletes no file."""
    _, kept = await _with_result(mongo_db, "test", "res_keep")
    purged, purged_file = await _with_result(mongo_db, "test", "res_purged", metadata={"owner": "gone"})
    child = await create_child_process(
        mongo_db, "test", purged["_id"], purged["_id"], "res_purged.c", "C", {}, 1, 0,
    )
    await _store(_make_ctx(mongo_db, child, "test"))
    _, stale_file = await _with_result(mongo_db, "test", "res_stale")

    await purge_processes(mongo_db, "test", {"owner": "gone"})
    await remove_stale_processes(mongo_db, "test", {"res_keep"})

    assert await sweep_orphan_results(mongo_db, "test") == 2
    assert await _file_gone(mongo_db, purged_file)
    assert await _file_gone(mongo_db, stale_file)
    assert await _file_kept(mongo_db, kept)


async def test_sweep_checks_every_batch(mongo_db, monkeypatch):
    monkeypatch.setattr(store, "_RESULT_SWEEP_BATCH", 2)
    orphans, kept = [], []
    for i in range(5):
        if i % 2 == 0:
            orphans.append(await _orphan(mongo_db, "test", f"res_{i}"))
        else:
            kept.append((await _with_result(mongo_db, "test", f"res_{i}"))[1])

    assert await sweep_orphan_results(mongo_db, "test") == 3

    for file_id in orphans:
        assert await _file_gone(mongo_db, file_id)
    for file_id in kept:
        assert await _file_kept(mongo_db, file_id)


async def test_init_sweeps(mongo_db):
    _, kept = await _with_result(mongo_db, "sweepinit", "res_keep")
    orphan = await _orphan(mongo_db, "sweepinit", "res_gone")

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="sweepinit")
    try:
        assert await _file_gone(mongo_db, orphan)
        assert await _file_kept(mongo_db, kept)
    finally:
        await optio.shutdown()


async def test_run_sweeps_every_interval_and_survives_a_failed_sweep(mongo_db, monkeypatch, caplog):
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="test")
    real_sweep = lifecycle.sweep_orphan_results
    calls = []

    async def failing_once(db, prefix):
        calls.append(prefix)
        if len(calls) == 1:
            raise RuntimeError("sweep broke")
        return await real_sweep(db, prefix)

    monkeypatch.setattr(lifecycle, "sweep_orphan_results", failing_once)
    monkeypatch.setattr(lifecycle, "RESULT_SWEEP_SECONDS", 0.01)
    caplog.set_level(logging.ERROR, logger="optio_core_core")
    orphan = await _orphan(mongo_db, "test", "res_late")

    run_task = asyncio.create_task(optio.run())
    try:
        await _wait_until(lambda: _file_gone(mongo_db, orphan))
        assert len(calls) >= 2
        assert any(
            r.exc_info and str(r.exc_info[1]) == "sweep broke" for r in caplog.records
        )
    finally:
        sweeper = optio._result_sweep_task
        await optio.shutdown()
        await run_task
    assert sweeper.done()
    assert optio._result_sweep_task is None


async def _index_keys(coll) -> dict[str, list[tuple[str, int]]]:
    return {
        ix["name"]: [(k, int(v)) for k, v in ix["key"].items()]
        async for ix in coll.list_indexes()
    }


async def test_init_creates_the_result_file_index(mongo_db):
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="sweepidx")
    try:
        indexes = await _index_keys(mongo_db["fs.files"])
        assert indexes["metadata.kind_1_metadata.prefix_1_metadata.processId_1"] == RESULT_FILE_INDEX_KEYS
    finally:
        await optio.shutdown()


async def test_a_conflicting_result_file_index_is_a_warning(mongo_db, caplog):
    files = mongo_db["fs.files"]
    await files.create_index(RESULT_FILE_INDEX_KEYS, name="my_result_ix")
    caplog.set_level(logging.WARNING, logger="optio_core.store")

    await ensure_result_file_index(mongo_db)
    await ensure_result_file_index(mongo_db)

    warnings = [r for r in caplog.records
                if r.name == "optio_core.store" and r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "metadata.kind_1_metadata.prefix_1_metadata.processId_1" in warnings[0].getMessage()
    indexes = await _index_keys(files)
    assert indexes["my_result_ix"] == RESULT_FILE_INDEX_KEYS
    assert "metadata.kind_1_metadata.prefix_1_metadata.processId_1" not in indexes
