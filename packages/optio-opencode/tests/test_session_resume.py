"""Full-cycle resume test for optio-opencode against fake_opencode.py."""

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_core.store import upsert_process

from optio_opencode import OpencodeTaskConfig
from optio_opencode.paths import local_taskdir
from optio_opencode.session import run_opencode_session
from optio_opencode.snapshots import (
    SESSION_SNAPSHOT_COLLECTION_SUFFIX,
    load_latest_snapshot,
)


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_test_resume_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _patch_localhost_to_use_fake(monkeypatch):
    """Mirror test_session_local.py: redirect LocalHost at fake_opencode.py."""
    import optio_opencode.host as host_mod
    orig_init = host_mod.LocalHost.__init__

    def _init(self, taskdir, opencode_cmd=None):
        return orig_init(
            self,
            taskdir=taskdir,
            opencode_cmd=[sys.executable, FAKE_OPENCODE],
        )

    monkeypatch.setattr(host_mod.LocalHost, "__init__", _init)


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """Inject `--scenario happy` into LocalHost.launch_opencode."""
    import optio_opencode.host as host_mod
    orig_launch = host_mod.LocalHost.launch_opencode
    holder = {"name": "happy"}

    async def _launch(self, password, ready_timeout_s, extra_args=None, env=None):
        return await orig_launch(
            self, password, ready_timeout_s,
            extra_args=["--scenario", holder["name"]],
            env=env,
        )
    monkeypatch.setattr(host_mod.LocalHost, "launch_opencode", _launch)
    return holder


async def _make_ctx(mongo_db, process_id: str, *, resume: bool):
    """Insert a process doc with supportsResume=True, build a ProcessContext."""
    task = TaskInstance(
        execute=lambda c: None,  # type: ignore[arg-type, return-value]
        process_id=process_id, name=process_id, supports_resume=True,
    )
    proc = await upsert_process(mongo_db, "test", task)
    await mongo_db["test_processes"].update_one(
        {"_id": proc["_id"]}, {"$set": {"status": {"state": "running"}}},
    )
    ctx = ProcessContext(
        process_oid=proc["_id"],
        process_id=process_id,
        root_oid=proc["_id"],
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
        resume=resume,
    )
    return ctx, proc["_id"]


async def _run_one_cycle(mongo_db, process_id: str, resume: bool) -> None:
    ctx, _ = await _make_ctx(mongo_db, process_id, resume=resume)
    cfg = OpencodeTaskConfig(consumer_instructions=f"(scenario: happy {process_id})")
    await run_opencode_session(ctx, cfg)


async def test_terminal_flow_captures_snapshot_and_wipes_workdir(mongo_db, task_root):
    pid = "oc_terminal_1"
    await _run_one_cycle(mongo_db, pid, resume=False)

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None
    assert snap["endState"] == "done"

    proc = await mongo_db["test_processes"].find_one({"processId": pid})
    assert proc["hasSavedState"] is True

    wd = Path(local_taskdir(pid)) / "workdir"
    assert not wd.exists() or not any(wd.iterdir())


async def test_resume_creates_second_snapshot(mongo_db, task_root):
    pid = "oc_resume_1"
    await _run_one_cycle(mongo_db, pid, resume=False)
    await _run_one_cycle(mongo_db, pid, resume=True)
    count = await mongo_db[f"test{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"].count_documents(
        {"processId": pid}
    )
    assert count == 2


async def test_resume_with_no_prior_snapshot_falls_back_to_fresh_launch(mongo_db, task_root):
    pid = "oc_resume_no_prior"
    await _run_one_cycle(mongo_db, pid, resume=True)  # nothing to resume; takes fresh path
    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None  # the fresh-start cycle still captures a terminal snapshot
