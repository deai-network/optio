"""conversation_ui=True wiring: ui_widget type, upstream, widgetData."""

import asyncio
import os
import sys
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth  # match the import used in session.py
from optio_opencode import OpencodeTaskConfig, create_opencode_task
from optio_opencode.session import run_opencode_session


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


# ctx_and_captures / _supply_scenario fixtures: same pattern as
# tests/test_conversation_session.py (copied, per the repo's
# no-cross-test-import style).


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

    async def _launch_oc(host, password, *, ready_timeout_s=30.0, opencode_executable="opencode", hostname="127.0.0.1", extra_env=None, env_remove=None):
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


async def _launch(ctx, cfg):
    """Run the session as a task; wait until publish_result was called."""
    sess = asyncio.create_task(run_opencode_session(ctx, cfg))
    for _ in range(200):
        if ctx.published_results:           # captured by the fixture wrapper
            return sess, ctx.published_results[0]
        await asyncio.sleep(0.05)
    sess.cancel()
    raise AssertionError("conversation was never published")


def test_create_opencode_task_ui_widget_matrix():
    base = dict(consumer_instructions="x")
    t_iframe = create_opencode_task("p1", "n", OpencodeTaskConfig(**base))
    assert t_iframe.ui_widget == "iframe"
    t_conv = create_opencode_task(
        "p2", "n", OpencodeTaskConfig(**base, mode="conversation", conversation_ui=True),
    )
    assert t_conv.ui_widget == "conversation"
    t_headless = create_opencode_task(
        "p3", "n", OpencodeTaskConfig(**base, mode="conversation"),
    )
    assert t_headless.ui_widget is None


async def test_conversation_ui_sets_upstream_and_widget_data(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
        conversation_ui=True, tool_verbosity="verbose",
    )
    sess, conv = await _launch(ctx, cfg)          # helper as in Task 5
    assert len(cap.widget_upstream) == 1          # opencode server is the upstream
    url, auth = cap.widget_upstream[0]
    assert url.startswith("http://127.0.0.1:")
    assert auth.username == "opencode"            # iframe-mode auth model unchanged
    [data] = cap.widget_data
    assert data["protocol"] == "opencode"
    assert data["sessionID"] == "fake-session-id"
    assert data["directory"]                      # the task workdir
    assert data["toolVerbosity"] == "verbose"
    assert "iframeSrc" not in data                # no SPA iframe fields
    await conv.close()
    await asyncio.wait_for(sess, timeout=30)


async def test_headless_conversation_sets_no_widget(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
    )
    sess, conv = await _launch(ctx, cfg)
    assert cap.widget_upstream == []
    assert cap.widget_data == []
    await conv.close()
    await asyncio.wait_for(sess, timeout=30)
