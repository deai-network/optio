"""End-to-end tests for partial task regeneration via Optio.resync()."""

import pytest
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance
from optio_core.store import get_process_by_process_id


async def _noop(ctx):
    pass


def _t(pid: str, *, group: str, schedule: str | None = None) -> TaskInstance:
    return TaskInstance(
        execute=_noop, process_id=pid, name=pid,
        metadata={"group": group}, schedule=schedule,
    )


@pytest.mark.asyncio
async def test_full_resync_unchanged(mongo_db):
    tasks = [_t("a", group="ingest"), _t("b", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        return tasks

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    # Drop one task and resync — full sync removes the missing one.
    tasks.pop(1)
    await optio.resync()

    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "a") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "b") is None


@pytest.mark.asyncio
async def test_partial_resync_only_touches_in_scope(mongo_db):
    initial = [_t("ing1", group="ingest"), _t("ing2", group="ingest"), _t("etl1", group="etl")]
    state = {"tasks": list(initial), "filter_seen": None}

    async def get_tasks(services, metadata_filter=None):
        state["filter_seen"] = metadata_filter
        if not metadata_filter:
            return state["tasks"]
        return [t for t in state["tasks"] if all(t.metadata.get(k) == v for k, v in metadata_filter.items())]

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    # Caller updates only the ingest group: drop ing2, add ing3.
    state["tasks"] = [
        _t("ing1", group="ingest"),
        _t("ing3", group="ingest"),
        _t("etl1", group="etl"),
    ]
    await optio.resync(metadata_filter={"group": "ingest"})

    assert state["filter_seen"] == {"group": "ingest"}
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing1") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing3") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing2") is None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "etl1") is not None


@pytest.mark.asyncio
async def test_partial_resync_clean_scopes_delete(mongo_db):
    tasks = [_t("ing1", group="ingest"), _t("etl1", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        if not metadata_filter:
            return tasks
        return [t for t in tasks if all(t.metadata.get(k) == v for k, v in metadata_filter.items())]

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    await optio.resync(clean=True, metadata_filter={"group": "ingest"})

    # Re-imported in-scope row + preserved out-of-scope row.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing1") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "etl1") is not None


@pytest.mark.asyncio
async def test_partial_resync_drops_out_of_scope_returned_by_callback(mongo_db):
    """Callback ignores the filter and returns its full list. Framework
    must drop out-of-scope tasks before upsert/register/schedule.
    """
    full_set = [_t("ing1", group="ingest"), _t("etl1", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        # Deliberately ignore metadata_filter — return everything.
        return full_set

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    # Drop ing1 from the source list, then partial-resync the ingest group.
    full_set.pop(0)
    await optio.resync(metadata_filter={"group": "ingest"})

    # ing1 is in-scope and was absent from the (over-returned) list -> deleted.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "ing1") is None
    # etl1 is out of scope; even though the callback returned it, framework
    # must NOT have re-upserted, re-registered, or re-scheduled it.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "etl1") is not None
    # And it should still be in the executor registry (not dropped, not
    # re-added: register_tasks was called with [] under the ingest filter).
    assert "etl1" in optio._executor._task_registry


@pytest.mark.asyncio
async def test_empty_filter_treated_as_full_sync(mongo_db):
    tasks = [_t("a", group="ingest"), _t("b", group="etl")]

    async def get_tasks(services, metadata_filter=None):
        return tasks

    optio = Optio()
    await optio.init(mongo_db=mongo_db, get_task_definitions=get_tasks)

    tasks.pop(1)
    await optio.resync(metadata_filter={})

    # `{}` collapses to None -> full sweep removes b.
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "a") is not None
    assert await get_process_by_process_id(mongo_db, optio._config.prefix, "b") is None
