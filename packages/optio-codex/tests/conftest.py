"""Shared pytest fixtures for optio-codex integration tests."""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import tempfile
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from optio_core.context import ProcessContext


TESTS_DIR = pathlib.Path(__file__).parent


@pytest.fixture
def shim_install_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    target = tmp_path / "shims"
    target.mkdir()
    for name, source in (
        ("codex", TESTS_DIR / "codex-shim.sh"),
        ("ttyd", TESTS_DIR / "ttyd-shim.sh"),
    ):
        link = target / name
        os.symlink(source, link)
        os.chmod(source, 0o755)
    return target


@pytest.fixture
def task_root(monkeypatch):
    path = tempfile.mkdtemp(prefix="cxtr-")
    monkeypatch.setenv("OPTIO_CODEX_TASK_ROOT", path)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
    )
    db_name = f"optio_codex_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@dataclass
class Captured:
    progress: list[tuple[float | None, str | None]] = field(default_factory=list)
    widget_upstream: list[tuple[str, object]] = field(default_factory=list)
    widget_data: list[object] = field(default_factory=list)


@pytest_asyncio.fixture
async def ctx_and_captures(mongo_db, monkeypatch):
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({
        "_id": oid,
        "processId": "p",
        "name": "P",
        "params": {},
        "metadata": {},
        "parentId": None,
        "rootId": None,
        "depth": 0,
        "order": 0,
        "adhoc": False,
        "ephemeral": False,
        "status": {"state": "running"},
        "progress": {"percent": None, "message": None},
        "log": [],
    })
    cancellation_flag = asyncio.Event()
    ctx = ProcessContext(
        process_oid=oid,
        process_id="p",
        root_oid=oid,
        depth=0,
        params={},
        services={},
        db=mongo_db,
        prefix="test",
        cancellation_flag=cancellation_flag,
        child_counter={"next": 0},
    )
    cap = Captured()

    original_report = ctx.report_progress
    def _report(percent, message=None):
        cap.progress.append((percent, message))
        return original_report(percent, message)
    ctx.report_progress = _report  # type: ignore[method-assign]

    orig_upstream = ctx.set_widget_upstream
    async def _upstream(url, inner_auth=None):
        cap.widget_upstream.append((url, inner_auth))
        return await orig_upstream(url, inner_auth)
    ctx.set_widget_upstream = _upstream  # type: ignore[method-assign]

    orig_data = ctx.set_widget_data
    async def _data(payload):
        cap.widget_data.append(payload)
        return await orig_data(payload)
    ctx.set_widget_data = _data  # type: ignore[method-assign]

    yield ctx, cap, cancellation_flag