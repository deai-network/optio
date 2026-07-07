"""Generic upload path: a conversation_ui opencode task registers an
in-process upload writer that materializes a file into <workdir>/uploads/<name>
and fires config.on_upload, and publishes widgetData.uploadUrl.

Mirrors the fixture pattern of test_conversation_ui_session.py (copied per the
repo's no-cross-test-import style).
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_opencode import OpencodeTaskConfig
from optio_opencode.session import run_opencode_session


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@dataclass
class Captured:
    progress: list = field(default_factory=list)
    widget_upstream: list = field(default_factory=list)
    widget_data: list = field(default_factory=list)
    upload_writers: list = field(default_factory=list)
    upload_cleared: int = 0


@pytest_asyncio.fixture
async def ctx_and_captures(mongo_db):
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

    orig_data = ctx.set_widget_data
    async def _data(payload):
        cap.widget_data.append(payload)
        return await orig_data(payload)
    ctx.set_widget_data = _data  # type: ignore[method-assign]

    # The real register_upload_writer requires an attached executor (absent in
    # tests); capture the writer instead so the test can drive it directly.
    def _register(writer):
        cap.upload_writers.append(writer)
    ctx.register_upload_writer = _register  # type: ignore[method-assign]

    def _clear():
        cap.upload_cleared += 1
    ctx.clear_upload_writer = _clear  # type: ignore[method-assign]

    ctx.published_results = []
    def _publish(obj):
        ctx.published_results.append(obj)
    ctx.publish_result = _publish  # type: ignore[method-assign]

    yield ctx, cap, cancellation_flag


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    from optio_opencode import host_actions
    orig_launch = host_actions.launch_opencode

    async def _launch_oc(host, password, *, ready_timeout_s=30.0, opencode_executable="opencode", hostname="127.0.0.1", extra_env=None, env_remove=None):
        del opencode_executable
        return await orig_launch(
            host, password,
            ready_timeout_s=ready_timeout_s,
            opencode_executable=(
                f"{sys.executable} {FAKE_OPENCODE} --scenario conversation"
            ),
            hostname=hostname,
            extra_env=extra_env,
        )
    monkeypatch.setattr(host_actions, "launch_opencode", _launch_oc)

    async def _ensure(host, **kwargs):
        return "opencode"
    monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)

    async def _version(host, *, opencode_executable="opencode"):
        return None
    monkeypatch.setattr(host_actions, "opencode_version", _version)


async def _launch(ctx, cfg):
    sess = asyncio.create_task(run_opencode_session(ctx, cfg))
    for _ in range(200):
        if ctx.published_results:
            return sess, ctx.published_results[0]
        await asyncio.sleep(0.05)
    sess.cancel()
    raise AssertionError("conversation was never published")


async def test_registers_writer_that_materializes_and_fires_on_upload(ctx_and_captures):
    ctx, cap, _ = ctx_and_captures
    seen: list = []
    async def _on_upload(hook_ctx, path):
        seen.append(path)
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
        conversation_ui=True, show_file_upload=True, on_upload=_on_upload,
    )
    sess, conv = await _launch(ctx, cfg)

    # A writer was registered while the conversation was live.
    assert len(cap.upload_writers) == 1
    writer = cap.upload_writers[0]

    # widgetData carries the generic upload route for this process.
    [data] = cap.widget_data
    assert "uploadUrl" in data
    assert f"{ctx._db.name}/{ctx._prefix}/{ctx.process_id}" in data["uploadUrl"]
    directory = data["directory"]

    # Driving a fake upload through the writer lands the file at
    # uploads/<name> and fires on_upload with the same relpath.
    rel = await writer("notes.md", b"hello")
    assert rel == "uploads/notes.md"
    with open(os.path.join(directory, "uploads/notes.md"), "rb") as f:
        assert f.read() == b"hello"
    assert seen == ["uploads/notes.md"]

    await conv.close()
    await asyncio.wait_for(sess, timeout=30)
    # Writer dropped on teardown.
    assert cap.upload_cleared >= 1
