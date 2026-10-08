"""Tests for count_processes: counting process documents by state and metadata
without loading them."""

import optio_core
from optio_core.models import ProcessStatus, TaskInstance
from optio_core.store import count_processes, update_status, upsert_process


async def dummy_execute(ctx):
    pass


async def _add(db, process_id, *, state="idle", **metadata):
    task = TaskInstance(
        execute=dummy_execute, process_id=process_id, name=process_id,
        metadata=metadata,
    )
    proc = await upsert_process(db, "test", task)
    if state != "idle":
        await update_status(db, "test", proc["_id"], ProcessStatus(state=state))
    return proc


async def test_count_without_filters_counts_everything(mongo_db):
    await _add(mongo_db, "a")
    await _add(mongo_db, "b", state="running")
    assert await count_processes(mongo_db, "test") == 2


async def test_count_by_any_of_several_states(mongo_db):
    await _add(mongo_db, "idle")
    await _add(mongo_db, "sched", state="scheduled")
    await _add(mongo_db, "run", state="running")
    await _add(mongo_db, "done", state="done")
    assert await count_processes(
        mongo_db, "test", states=["scheduled", "running"],
    ) == 2
    assert await count_processes(mongo_db, "test", states={"done"}) == 1


async def test_count_by_scalar_metadata(mongo_db):
    await _add(mongo_db, "s1-a", sourceId="s1", kind="entity-sync")
    await _add(mongo_db, "s1-b", sourceId="s1", kind="entity-sync-dry")
    await _add(mongo_db, "s2-a", sourceId="s2", kind="entity-sync")
    assert await count_processes(mongo_db, "test", metadata={"sourceId": "s1"}) == 2
    assert await count_processes(
        mongo_db, "test", metadata={"sourceId": "s1", "kind": "entity-sync"},
    ) == 1


async def test_count_list_metadata_value_matches_any_item(mongo_db):
    await _add(mongo_db, "sync", sourceId="s1", kind="entity-sync")
    await _add(mongo_db, "dry", sourceId="s1", kind="entity-sync-dry")
    await _add(mongo_db, "check", sourceId="s1", kind="entity-sync-check")
    await _add(mongo_db, "beat", sourceId="s1", kind="entity-sync-heartbeat")
    assert await count_processes(
        mongo_db, "test",
        metadata={"sourceId": "s1", "kind": ["entity-sync", "entity-sync-dry"]},
    ) == 2


async def test_count_combines_states_and_metadata(mongo_db):
    await _add(mongo_db, "t1-run", state="running", targetId="t1")
    await _add(mongo_db, "t1-idle", targetId="t1")
    await _add(mongo_db, "t2-run", state="running", targetId="t2")
    assert await count_processes(
        mongo_db, "test", states=["running"], metadata={"targetId": "t1"},
    ) == 1


async def test_count_with_no_match_is_zero(mongo_db):
    await _add(mongo_db, "a", sourceId="s1")
    assert await count_processes(mongo_db, "test", metadata={"sourceId": "nope"}) == 0
    assert await count_processes(mongo_db, "test", states=["cancelling"]) == 0


def test_count_processes_is_exported():
    assert "count_processes" in optio_core.__all__
    assert callable(optio_core.count_processes)


async def test_roots_only_leaves_out_children(mongo_db):
    """Children inherit their parent's metadata, so a metadata count alone
    would count a running parent once per child; roots_only counts the
    top-level processes."""
    from optio_core.store import create_child_process
    parent = await _add(mongo_db, "sync", state="running", sourceId="s1", kind="entity-sync")
    for i in range(3):
        child = await create_child_process(
            mongo_db, "test", parent_oid=parent["_id"], root_oid=parent["_id"],
            process_id=f"child-{i}", name=f"child {i}", params={}, depth=1, order=i,
            metadata=parent["metadata"],  # what the executor passes a child
        )
        await update_status(mongo_db, "test", child["_id"], ProcessStatus(state="running"))
    filters = dict(states=["running"], metadata={"sourceId": "s1", "kind": "entity-sync"})
    assert await count_processes(mongo_db, "test", **filters) == 4
    assert await count_processes(mongo_db, "test", roots_only=True, **filters) == 1
