"""Tests for OptioEngineService — phase 2 of engine RPC migration."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from bson import ObjectId

from optio_core._generated.optio_engine import (
    LaunchParams, LaunchResult,
    CancelParams, CancelResult,
    DismissParams, DismissResult,
    GroupCancelParams, GroupCancelResult,
    GroupCancelAndWaitParams, GroupCancelAndWaitResult,
    BlockLaunchesParams, BlockLaunchesResult,
    UnblockLaunchesParams, UnblockLaunchesResult,
    ResyncParams,
)
from optio_core.models import LaunchBlocked


@pytest.fixture
def sample_idle_proc():
    """Return a sample idle process doc as Mongo would store it.

    Includes all fields required by the generated Process model so that
    _to_process_dict output passes Pydantic validation inside LaunchResult,
    CancelResult, etc.
    """
    oid = ObjectId()
    return {
        "_id": oid,
        "processId": "p1",
        "name": "Test process",
        "supportsResume": False,
        "cancellable": True,
        "status": {"state": "idle", "error": None, "runningSince": None, "doneAt": None, "duration": None, "failedAt": None, "stoppedAt": None},
        "progress": {"percent": None, "message": None},
        "log": [],
        "rootId": str(oid),
        "depth": 0,
        "order": 0,
        "createdAt": "2026-05-08T00:00:00+00:00",
        "metadata": {"tag": "demo"},
    }


@pytest.fixture
def sample_running_proc(sample_idle_proc):
    proc = dict(sample_idle_proc)
    proc["status"] = {"state": "running"}
    return proc


@pytest.fixture
def fake_optio(sample_idle_proc):
    """A MagicMock Optio with the methods OptioEngineService calls."""
    optio = MagicMock()
    optio._config = MagicMock()
    coll = AsyncMock()
    db = MagicMock()
    db.__getitem__.return_value = coll
    optio._config.mongo_db = db
    optio._config.prefix = "test"

    # Default: collection returns the idle proc for any find_one.
    coll.find_one = AsyncMock(return_value=sample_idle_proc)

    optio.launch = AsyncMock(return_value=None)
    optio.cancel = AsyncMock(return_value=None)
    optio.dismiss = AsyncMock(return_value=None)
    optio.resync = AsyncMock(return_value=None)
    optio.group_cancel = AsyncMock(return_value=3)
    optio.group_cancel_and_wait = AsyncMock(return_value=2)
    optio.unblock_launches = AsyncMock(return_value=1)
    optio._load_persisted_blocks = AsyncMock()
    optio.collection = coll
    return optio


@pytest.mark.asyncio
async def test_launch_by_objectid_hex(fake_optio, sample_idle_proc):
    """Verify _resolve accepts a 24-char hex ObjectId and queries _id, not processId."""
    from optio_core._engine_service import OptioEngineService
    running = dict(sample_idle_proc)
    running["status"] = {"state": "scheduled"}
    fake_optio.collection.find_one = AsyncMock(side_effect=[sample_idle_proc, running])

    svc = OptioEngineService(fake_optio)
    hex_id = str(sample_idle_proc["_id"])
    result = await svc.launch(LaunchParams.model_validate({"processId": hex_id}))

    assert result.root.ok is True
    # First find_one query mentions _id (ObjectId branch), not processId (string branch).
    first_query = fake_optio.collection.find_one.call_args_list[0][0][0]
    assert "_id" in first_query


@pytest.mark.asyncio
async def test_launch_success(fake_optio, sample_idle_proc):
    """launch on an idle process returns ok=True with the post-mutation doc."""
    from optio_core._engine_service import OptioEngineService

    # Sequence: first find_one returns idle, second (post-launch) returns running.
    running = dict(sample_idle_proc)
    running["status"] = {"state": "scheduled"}
    fake_optio.collection.find_one = AsyncMock(side_effect=[sample_idle_proc, running])

    svc = OptioEngineService(fake_optio)
    result = await svc.launch(LaunchParams.model_validate({"processId": "p1"}))

    assert isinstance(result, LaunchResult)
    assert result.root.ok is True
    assert result.root.process.process_id == "p1"
    fake_optio.launch.assert_awaited_once_with("p1", resume=False)


@pytest.mark.asyncio
async def test_launch_not_found(fake_optio):
    from optio_core._engine_service import OptioEngineService
    fake_optio.collection.find_one = AsyncMock(return_value=None)
    svc = OptioEngineService(fake_optio)
    result = await svc.launch(LaunchParams.model_validate({"processId": "missing"}))
    assert result.root.ok is False
    assert result.root.reason == "not-found"
    fake_optio.launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_launch_not_launchable(fake_optio, sample_running_proc):
    from optio_core._engine_service import OptioEngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_running_proc)
    svc = OptioEngineService(fake_optio)
    result = await svc.launch(LaunchParams.model_validate({"processId": "p1"}))
    assert result.root.ok is False
    assert result.root.reason == "not-launchable"
    fake_optio.launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_launch_no_resume_support(fake_optio, sample_idle_proc):
    from optio_core._engine_service import OptioEngineService
    sample_idle_proc["supportsResume"] = False
    fake_optio.collection.find_one = AsyncMock(return_value=sample_idle_proc)
    svc = OptioEngineService(fake_optio)
    result = await svc.launch(LaunchParams.model_validate({"processId": "p1", "resume": True}))
    assert result.root.ok is False
    assert result.root.reason == "no-resume-support"


@pytest.mark.asyncio
async def test_launch_blocked(fake_optio, sample_idle_proc):
    from optio_core._engine_service import OptioEngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_idle_proc)
    fake_optio.launch = AsyncMock(side_effect=LaunchBlocked("blocked by test"))
    svc = OptioEngineService(fake_optio)
    result = await svc.launch(LaunchParams.model_validate({"processId": "p1"}))
    assert result.root.ok is False
    assert result.root.reason == "launch-blocked"


@pytest.mark.asyncio
async def test_cancel_success(fake_optio, sample_running_proc):
    from optio_core._engine_service import OptioEngineService
    cancelled = dict(sample_running_proc)
    cancelled["status"] = {"state": "cancelled"}
    fake_optio.collection.find_one = AsyncMock(side_effect=[sample_running_proc, cancelled])
    svc = OptioEngineService(fake_optio)
    result = await svc.cancel(CancelParams.model_validate({"processId": "p1"}))
    assert result.root.ok is True
    assert result.root.process.status.state == "cancelled"


@pytest.mark.asyncio
async def test_cancel_not_found(fake_optio):
    from optio_core._engine_service import OptioEngineService
    fake_optio.collection.find_one = AsyncMock(return_value=None)
    svc = OptioEngineService(fake_optio)
    result = await svc.cancel(CancelParams.model_validate({"processId": "missing"}))
    assert result.root.ok is False
    assert result.root.reason == "not-found"


@pytest.mark.asyncio
async def test_cancel_not_cancellable_by_state(fake_optio, sample_idle_proc):
    from optio_core._engine_service import OptioEngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_idle_proc)
    svc = OptioEngineService(fake_optio)
    result = await svc.cancel(CancelParams.model_validate({"processId": "p1"}))
    assert result.root.ok is False
    assert result.root.reason == "not-cancellable"


@pytest.mark.asyncio
async def test_cancel_not_cancellable_by_flag(fake_optio, sample_running_proc):
    from optio_core._engine_service import OptioEngineService
    sample_running_proc["cancellable"] = False
    fake_optio.collection.find_one = AsyncMock(return_value=sample_running_proc)
    svc = OptioEngineService(fake_optio)
    result = await svc.cancel(CancelParams.model_validate({"processId": "p1"}))
    assert result.root.ok is False
    assert result.root.reason == "not-cancellable"


@pytest.mark.asyncio
async def test_dismiss_success(fake_optio, sample_idle_proc):
    from optio_core._engine_service import OptioEngineService
    sample_idle_proc["status"] = {"state": "done"}
    after = dict(sample_idle_proc)
    after["status"] = {"state": "idle"}
    fake_optio.collection.find_one = AsyncMock(side_effect=[sample_idle_proc, after])
    svc = OptioEngineService(fake_optio)
    result = await svc.dismiss(DismissParams.model_validate({"processId": "p1"}))
    assert result.root.ok is True


@pytest.mark.asyncio
async def test_dismiss_not_found(fake_optio):
    from optio_core._engine_service import OptioEngineService
    fake_optio.collection.find_one = AsyncMock(return_value=None)
    svc = OptioEngineService(fake_optio)
    result = await svc.dismiss(DismissParams.model_validate({"processId": "missing"}))
    assert result.root.ok is False
    assert result.root.reason == "not-found"


@pytest.mark.asyncio
async def test_dismiss_not_dismissable(fake_optio, sample_running_proc):
    from optio_core._engine_service import OptioEngineService
    fake_optio.collection.find_one = AsyncMock(return_value=sample_running_proc)
    svc = OptioEngineService(fake_optio)
    result = await svc.dismiss(DismissParams.model_validate({"processId": "p1"}))
    assert result.root.ok is False
    assert result.root.reason == "not-dismissable"


@pytest.mark.asyncio
async def test_resync_default(fake_optio):
    from optio_core._engine_service import OptioEngineService
    svc = OptioEngineService(fake_optio)
    result = await svc.resync(ResyncParams())
    assert result is None
    fake_optio.resync.assert_awaited_once_with(clean=False, metadata_filter=None)


@pytest.mark.asyncio
async def test_resync_with_filter(fake_optio):
    from optio_core._engine_service import OptioEngineService
    svc = OptioEngineService(fake_optio)
    result = await svc.resync(ResyncParams.model_validate(
        {"clean": True, "metadataFilter": {"tag": "demo"}}
    ))
    assert result is None
    fake_optio.resync.assert_awaited_once_with(clean=True, metadata_filter={"tag": "demo"})


@pytest.mark.asyncio
async def test_group_cancel_success(fake_optio):
    from optio_core._engine_service import OptioEngineService
    svc = OptioEngineService(fake_optio)
    result = await svc.group_cancel(GroupCancelParams.model_validate(
        {"metadataFilter": {"tag": "demo"}}
    ))
    assert result.root.ok is True
    assert result.root.cancelled_count == 3


@pytest.mark.asyncio
async def test_group_cancel_invalid_persist(fake_optio):
    from optio_core._engine_service import OptioEngineService
    svc = OptioEngineService(fake_optio)
    result = await svc.group_cancel(GroupCancelParams.model_validate(
        {"metadataFilter": {"tag": "demo"}, "persist": True}
    ))
    assert result.root.ok is False
    assert result.root.reason == "invalid-persist-without-block"
    fake_optio.group_cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_cancel_and_wait_success(fake_optio):
    from optio_core._engine_service import OptioEngineService
    svc = OptioEngineService(fake_optio)
    result = await svc.group_cancel_and_wait(GroupCancelAndWaitParams.model_validate(
        {"metadataFilter": {"tag": "demo"}}
    ))
    assert result.root.ok is True
    assert result.root.cancelled_count == 2


@pytest.mark.asyncio
async def test_group_cancel_and_wait_invalid_persist(fake_optio):
    from optio_core._engine_service import OptioEngineService
    svc = OptioEngineService(fake_optio)
    result = await svc.group_cancel_and_wait(GroupCancelAndWaitParams.model_validate(
        {"metadataFilter": {"tag": "demo"}, "persist": True}
    ))
    assert result.root.ok is False
    assert result.root.reason == "invalid-persist-without-block"
    fake_optio.group_cancel_and_wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_block_launches_success(fake_optio, monkeypatch):
    from optio_core._engine_service import OptioEngineService

    fake_coll = AsyncMock()

    def fake_collection(db, prefix):
        return fake_coll

    upsert_called = AsyncMock()

    import optio_core._launch_block_store as lb_store
    monkeypatch.setattr(lb_store, "collection", fake_collection)
    monkeypatch.setattr(lb_store, "upsert_block", upsert_called)

    svc = OptioEngineService(fake_optio)
    result = await svc.block_launches(BlockLaunchesParams.model_validate(
        {"launchFilter": {"tag": "demo"}, "reason": "x"}
    ))
    assert result.root.ok is True
    upsert_called.assert_awaited_once_with(fake_coll, {"tag": "demo"}, "x")
    fake_optio._load_persisted_blocks.assert_awaited()


@pytest.mark.asyncio
async def test_unblock_launches_returns_count(fake_optio):
    from optio_core._engine_service import OptioEngineService
    svc = OptioEngineService(fake_optio)
    result = await svc.unblock_launches(
        UnblockLaunchesParams.model_validate({"launchFilter": {"tag": "demo"}})
    )
    assert result.removed == 1


@pytest.mark.asyncio
async def test_launch_redelivery_returns_not_launchable(fake_optio, sample_idle_proc):
    """First launch transitions idle→scheduled; redelivered launch sees scheduled and returns not-launchable."""
    from optio_core._engine_service import OptioEngineService

    after = dict(sample_idle_proc)
    after["status"] = {"state": "scheduled"}

    # Two find_one's per call (pre-check + post-mutation re-read).
    # First call: idle, scheduled. Second call: scheduled (redelivery sees the scheduled doc).
    fake_optio.collection.find_one = AsyncMock(side_effect=[
        sample_idle_proc, after,  # first call
        after,                    # second call's pre-check sees scheduled
    ])

    svc = OptioEngineService(fake_optio)
    first = await svc.launch(LaunchParams.model_validate({"processId": "p1"}))
    second = await svc.launch(LaunchParams.model_validate({"processId": "p1"}))

    assert first.root.ok is True
    assert second.root.ok is False
    assert second.root.reason == "not-launchable"


@pytest.mark.asyncio
async def test_init_redis_url_and_rpc_server_mutually_exclusive(mongo_db):
    from optio_core.lifecycle import Optio
    fw = Optio()
    fake_server = MagicMock()
    with pytest.raises(ValueError, match="mutually exclusive"):
        await fw.init(mongo_db=mongo_db, redis_url="redis://x", rpc_server=fake_server)


@pytest.mark.asyncio
async def test_module_rpc_server_attribute_reflects_instance(mongo_db):
    """optio_core.rpc_server should reflect the singleton's runtime state."""
    import optio_core
    # Pre-init the singleton state may be None; we test the forwarding.
    optio_core._instance.rpc_server = "sentinel"
    try:
        assert optio_core.rpc_server == "sentinel"
    finally:
        optio_core._instance.rpc_server = None
