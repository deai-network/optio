"""Conversation-mode session body: publish_result, close-driven teardown,
keywords on/off, opencode.json question-tool merge."""

import asyncio
import json
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

# See test_session_local: spawn-heavy, timing-fragile under concurrent load.
# Marked `serial` so the harness runs it in a final, non-parallel phase.
pytestmark = pytest.mark.serial


# ctx_and_captures / _supply_scenario fixtures: same pattern as
# tests/test_session_local.py (copied, per the repo's no-cross-test-import
# style). ctx_and_captures additionally captures ctx.publish_result calls
# in ctx.published_results.


@dataclass
class Captured:
    progress: list = field(default_factory=list)
    widget_upstream: list = field(default_factory=list)
    widget_data: list = field(default_factory=list)


@pytest_asyncio.fixture
async def ctx_and_captures(mongo_db, monkeypatch):
    """A ProcessContext backed by a real mongo + capture hooks."""
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

    # Capture published conversation objects. The real publish_result
    # requires an attached executor (absent in tests), so the wrapper
    # only records.
    ctx.published_results = []
    def _publish(obj):
        ctx.published_results.append(obj)
    ctx.publish_result = _publish  # type: ignore[method-assign]

    yield ctx, cap, cancellation_flag


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """Substitute fake_opencode.py for the real opencode binary."""
    from optio_opencode import host_actions
    orig_launch = host_actions.launch_opencode
    scenario_holder: dict = {"name": "happy"}

    async def _launch_oc(host, password, *, ready_timeout_s=30.0, opencode_executable="opencode", hostname="127.0.0.1", extra_env=None, env_remove=None, claustrum_wrap=None):
        del opencode_executable  # we substitute fully
        return await orig_launch(
            host, password,
            ready_timeout_s=ready_timeout_s,
            opencode_executable=(
                f"{sys.executable} {FAKE_OPENCODE} "
                f"--scenario {scenario_holder['name']}"
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

    return scenario_holder


@pytest.fixture
def tmp_workdir_peek(monkeypatch):
    """Return a closure peeking at files the session wrote into its workdir.

    Captures content at host.write_text time (keyed by the workdir-relative
    name) — the session's teardown wipes the taskdir, so a post-session
    disk read would find nothing.
    """
    from optio_host.host import LocalHost

    written: dict[str, str] = {}
    orig_write = LocalHost.write_text

    async def _capture(self, relative_path, text):
        written[relative_path] = text
        return await orig_write(self, relative_path, text)

    monkeypatch.setattr(LocalHost, "write_text", _capture)

    def _peek(name: str) -> str:
        return written[name]

    return _peek


async def _launch(ctx, cfg):
    """Run the session as a task; wait until publish_result was called."""
    sess = asyncio.create_task(run_opencode_session(ctx, cfg))
    for _ in range(1200):
        if ctx.published_results:           # captured by the fixture wrapper
            return sess, ctx.published_results[0]
        await asyncio.sleep(0.05)
    sess.cancel()
    raise AssertionError("conversation was never published")


async def test_conversation_published_and_close_ends_session(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False, fs_isolation=False,
    )
    sess, conv = await _launch(ctx, cfg)
    assert not conv.closed
    await conv.close()
    await asyncio.wait_for(sess, timeout=60)
    assert conv.closed                      # _finish ran during teardown


async def test_conversation_with_host_protocol_done_keyword_also_ends(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation_then_done"   # scenario emits DONE after a delay
    cfg = OpencodeTaskConfig(
        consumer_instructions="chat", mode="conversation", host_protocol=True, fs_isolation=False,
    )
    sess, conv = await _launch(ctx, cfg)
    await asyncio.wait_for(sess, timeout=60)              # DONE from the keyword channel ends it
    assert conv.closed


async def test_question_tool_disabled_in_conversation_opencode_json(ctx_and_captures, _supply_scenario, tmp_workdir_peek):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False, fs_isolation=False,
        opencode_config={"theme": "dark", "tools": {"webfetch": True}},
    )
    sess, conv = await _launch(ctx, cfg)
    written = json.loads(tmp_workdir_peek("opencode.json"))
    assert written["tools"] == {"webfetch": True, "question": False}
    assert written["theme"] == "dark"
    await conv.close()
    await asyncio.wait_for(sess, timeout=60)


async def test_iframe_mode_opencode_json_untouched(ctx_and_captures, _supply_scenario, tmp_workdir_peek):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"
    cfg = OpencodeTaskConfig(consumer_instructions="task", opencode_config={"theme": "dark"}, fs_isolation=False)
    await run_opencode_session(ctx, cfg)
    assert json.loads(tmp_workdir_peek("opencode.json")) == {"theme": "dark"}


async def test_premature_server_exit_fails_session(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation_early_exit"  # scenario: short sleep then ("exit", 1)
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False, fs_isolation=False,
    )
    sess, conv = await _launch(ctx, cfg)
    with pytest.raises(Exception):
        await asyncio.wait_for(sess, timeout=60)
    assert conv.closed
