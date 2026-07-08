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

from optio_codex import host_actions


TESTS_DIR = pathlib.Path(__file__).parent


@pytest.fixture(autouse=True)
def fake_claustrum(monkeypatch):
    """Default-on fs_isolation without a real Landlock build (Stage 9).

    ``CodexTaskConfig.fs_isolation`` defaults True, so every session-flow test
    provisions claustrum. Stub ``ensure_claustrum_installed`` to return the
    package's ``claustrum-shim.sh`` (which execs its wrapped command, enforcing
    nothing) instead of cross-compiling the real binary on the engine — this is
    what makes the fast suite EXERCISE the default-on wiring end-to-end. Real
    kernel enforcement is the env-gated test_sandbox_enforce.py. Also stub
    ``claustrum_newer_tag`` to None so no live ``git ls-remote`` runs (and no
    spurious update notice fires) during the fake suite. Autouse + harmless for
    the unit tests that never call it; a test needing fail-closed behaviour
    re-monkeypatches ``ensure_claustrum_installed`` to raise (last setattr
    wins)."""
    shim = str(TESTS_DIR / "claustrum-shim.sh")
    (TESTS_DIR / "claustrum-shim.sh").chmod(0o755)

    async def _fake_install(hook_ctx, *, install_dir=None):
        return shim

    async def _fake_newer():
        return None

    monkeypatch.setattr(host_actions, "ensure_claustrum_installed", _fake_install)
    monkeypatch.setattr(host_actions, "claustrum_newer_tag", _fake_newer)
    return shim


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