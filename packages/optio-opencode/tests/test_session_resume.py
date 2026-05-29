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
from optio_host.paths import task_dir
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
def _supply_scenario(monkeypatch):
    """Substitute fake_opencode.py for the real opencode binary.

    Mirrors the substitution in test_session_local.py — see comments
    there for why this is the right shape post-host-split.
    """
    from optio_opencode import host_actions
    orig_launch = host_actions.launch_opencode
    holder = {"name": "happy"}

    async def _launch(host, password, *, ready_timeout_s=30.0, opencode_executable="opencode", hostname="127.0.0.1", extra_env=None):
        del opencode_executable
        return await orig_launch(
            host, password,
            ready_timeout_s=ready_timeout_s,
            opencode_executable=(
                f"{sys.executable} {FAKE_OPENCODE} --scenario {holder['name']}"
            ),
            hostname=hostname,
            extra_env=extra_env,
        )
    monkeypatch.setattr(host_actions, "launch_opencode", _launch)

    async def _ensure(hook_ctx, install_if_missing=True, *, install_dir=None):
        return "opencode"
    monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)

    async def _version(host, *, opencode_executable="opencode"):
        return None
    monkeypatch.setattr(host_actions, "opencode_version", _version)

    orig_export = host_actions.opencode_export

    async def _export(host, opencode_db_path, session_id, *, opencode_executable="opencode"):
        return await orig_export(
            host, opencode_db_path, session_id,
            opencode_executable=f"{sys.executable} {FAKE_OPENCODE}",
        )
    monkeypatch.setattr(host_actions, "opencode_export", _export)

    orig_import = host_actions.opencode_import

    async def _import(host, opencode_db_path, session_json, *, opencode_executable="opencode"):
        return await orig_import(
            host, opencode_db_path, session_json,
            opencode_executable=f"{sys.executable} {FAKE_OPENCODE}",
        )
    monkeypatch.setattr(host_actions, "opencode_import", _import)

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

    wd = Path(task_dir(ssh=None, process_id=pid, consumer_name="optio-opencode")) / "workdir"
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


async def test_resume_appends_second_line_to_resume_log(mongo_db, task_root):
    """After one resume cycle, resume.log in the latest snapshot has exactly 2 lines."""
    import io
    import re
    import tarfile
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    pid = "oc_resume_log_growth"
    await _run_one_cycle(mongo_db, pid, resume=False)  # first launch
    await _run_one_cycle(mongo_db, pid, resume=True)   # resume

    snap = await load_latest_snapshot(mongo_db, prefix="test", process_id=pid)
    assert snap is not None

    # The workdir blob is a gzipped tar. Extract resume.log and read it.
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    stream = await bucket.open_download_stream(snap["workdirBlobId"])
    workdir_bytes = await stream.read()

    with tarfile.open(fileobj=io.BytesIO(workdir_bytes), mode="r:gz") as tar:
        member = tar.getmember("resume.log")
        contents = tar.extractfile(member).read().decode("utf-8")

    lines = [line for line in contents.splitlines() if line]
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}: {contents!r}"

    # Line format: `<ISO 8601 timestamp>[ REFRESHED:<comma-separated names>]`.
    # This test exercises the no-refresh path (no on_resume_refresh hook),
    # so every line is a bare timestamp; the regex still accepts the
    # extended form to stay valid as the format evolves.
    line_re = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z(?: REFRESHED:\S+)?$"
    )
    for line in lines:
        assert line_re.match(line), f"unrecognized resume.log line: {line!r}"
    # Timestamps (the leading token of each line) are monotonic.
    timestamps = [line.split()[0] for line in lines]
    assert timestamps[0] <= timestamps[1], f"timestamps not monotonic: {timestamps!r}"
