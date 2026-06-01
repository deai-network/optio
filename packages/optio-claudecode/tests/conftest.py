"""Shared pytest fixtures for optio-claudecode integration tests.

Fixtures:

* ``shim_install_dir`` — a tmp_path subdir containing symlinks named
  ``claude`` and ``ttyd`` pointing at the package-shipped shim scripts.
  Pass this as both ``claude_install_dir`` and ``ttyd_install_dir`` in
  ``ClaudeCodeTaskConfig`` to bypass real binary detection.
* ``mongo_db`` — a per-test isolated Mongo db (matches opencode's
  conftest verbatim).
* ``ctx_and_captures`` — a ``ProcessContext`` backed by ``mongo_db`` with
  ``report_progress`` / ``set_widget_upstream`` / ``set_widget_data``
  intercepted into a ``Captured`` dataclass so tests can assert on
  observed state.

The fixture body is a direct port of opencode's pattern. Update both in
lockstep if the ProcessContext constructor signature changes.
"""

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
    """Tmp dir containing symlinks to the claude + ttyd shims."""
    target = tmp_path / "shims"
    target.mkdir()
    for name, source in (
        ("claude", TESTS_DIR / "claude-shim.sh"),
        ("ttyd", TESTS_DIR / "ttyd-shim.sh"),
    ):
        link = target / name
        os.symlink(source, link)
        os.chmod(source, 0o755)
    return target


@pytest.fixture
def tmp_workdir():
    """A temporary directory removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-claudecode-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def task_root(monkeypatch):
    """Point ``OPTIO_CLAUDECODE_TASK_ROOT`` at a SHORT temp dir.

    Session-flow tests run claude inside a detached tmux session whose control
    socket is ``<taskdir>/workdir/tmux.sock``. Unix domain socket paths are
    capped at ~104 bytes (``sun_path``), and pytest's ``tmp_path`` is far too
    deep — a socket under it overflows the limit and tmux fails with "File name
    too long". A short ``/tmp/cctr-*`` root keeps the socket path well within
    the cap, mirroring the short production taskdir (``~/.local/share/...``).
    """
    path = tempfile.mkdtemp(prefix="cctr-")
    monkeypatch.setenv("OPTIO_CLAUDECODE_TASK_ROOT", path)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def mongo_db():
    """Per-test MongoDB database, dropped after each test."""
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
    )
    db_name = f"optio_claudecode_test_{os.getpid()}"
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
    """ProcessContext bound to ``mongo_db`` with capture hooks.

    Yields ``(ctx, captured, cancellation_flag)``.
    """
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


@pytest.fixture
def claude_cache_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A pre-populated fake claude version cache.

    Contains a single version file ``9.9.9`` symlinked to the claude shim, so
    ``ensure_claude_installed`` takes the cache-hit path (points
    home/.local/bin/claude at it) and never runs the real install.sh. Pass as
    ``claude_install_dir`` in ClaudeCodeTaskConfig.
    """
    cache = tmp_path / "claude-cache"
    cache.mkdir()
    version_file = cache / "9.9.9"
    os.symlink(TESTS_DIR / "claude-shim.sh", version_file)
    os.chmod(TESTS_DIR / "claude-shim.sh", 0o755)
    return cache
