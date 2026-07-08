"""conversation_ui=True wiring: ui_widget type, upstream, widgetData."""

import asyncio
import os
import sys
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth, ChildHandle  # match the import used in session.py
from optio_opencode import OpencodeTaskConfig, create_opencode_task, model_probe
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

    # The real register/clear_upload_writer reach the owning Optio via the
    # executor back-reference (absent in tests); stub them so the conversation
    # branch's writer registration doesn't raise.
    ctx.register_upload_writer = lambda writer: None  # type: ignore[method-assign]
    ctx.clear_upload_writer = lambda: None  # type: ignore[method-assign]

    # Capture published conversation objects. The real publish_result
    # requires an attached executor (absent in tests), so the wrapper
    # only records.
    ctx.published_results = []
    def _publish(obj):
        ctx.published_results.append(obj)
    ctx.publish_result = _publish  # type: ignore[method-assign]

    # The fresh model probe runs as a CHILD subtask (run_probe_child). This real
    # ProcessContext has no attached executor, so run the child's execute
    # in-process on a shim ctx that delegates to `ctx` but captures the child's
    # publish_result LOCALLY — routing it into ctx.published_results would fool
    # _launch, which waits for the conversation object to be published.
    async def _run_child_task_with_result(task, **kw):
        box: dict = {}

        class _ChildCtx:
            def report_progress(self, percent, message=None):
                return ctx.report_progress(percent, message)

            def publish_result(self, obj):
                box["value"] = obj

            def __getattr__(self, name):
                return getattr(ctx, name)

        await task.execute(_ChildCtx())
        return ChildHandle(result=box.get("value"), task=None)
    ctx.run_child_task_with_result = _run_child_task_with_result  # type: ignore[method-assign]

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
    """Run the session as a task; wait until publish_result was called.

    Event-driven with a generous hang-ceiling. The previous fixed
    200 x 0.05s = 10s poll budget was load-blind: under heavy parallel-worker
    CPU contention the pre-publish chain (subprocess spawns + HTTP round-trips
    to fake_opencode) legitimately exceeds 10s, so the poller cancelled a
    still-progressing session ("conversation was never published"). Wait on an
    event fired by publish_result instead, and race it against the session task
    so a session that dies before publishing surfaces its real exception rather
    than the misleading generic assertion. The 60s ceiling only bounds a true
    hang; it is far beyond the real work time even under oversubscription.
    """
    published = asyncio.Event()
    orig_publish = ctx.publish_result

    def _pub(obj):
        orig_publish(obj)                   # keep the fixture's recording behavior
        published.set()

    ctx.publish_result = _pub  # type: ignore[method-assign]

    sess = asyncio.create_task(run_opencode_session(ctx, cfg))
    waiter = asyncio.create_task(published.wait())
    done, _pending = await asyncio.wait(
        {sess, waiter}, timeout=60, return_when=asyncio.FIRST_COMPLETED,
    )
    if waiter in done:
        return sess, ctx.published_results[0]
    waiter.cancel()
    if sess in done:
        sess.result()                       # session ended first -> re-raise real cause
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


async def test_probe_disabled_models_ride_widget_data(
    ctx_and_captures, _supply_scenario, monkeypatch,
):
    """With show_session_controls, the startup probe enumerates models from the
    server's /config/providers and injects the unusable ones as
    widgetData.disabledModels (id → reason) for OpencodeView's picker."""
    from optio_opencode import session as sess

    seen_ids: dict = {}

    async def _fake_run(port, password, directory, model_ids, report):
        seen_ids["ids"] = sorted(model_ids)
        # xAI's grok-5 is "unusable" for this account; the rest work.
        return {mid: (mid != "xai/grok-5") for mid in model_ids}

    monkeypatch.setattr(sess, "_run_model_probe", _fake_run)

    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
        conversation_ui=True, show_session_controls=True,
    )
    sess_task, conv = await _launch(ctx, cfg)
    [data] = cap.widget_data
    # ids came from the fake server's real /config/providers response
    assert seen_ids["ids"] == [
        "opencode/big-pickle", "opencode/deepseek-v4-flash", "xai/grok-5",
    ]
    assert data["disabledModels"] == {
        "xai/grok-5": model_probe.DISABLED_REASON,
    }
    await conv.close()
    await asyncio.wait_for(sess_task, timeout=30)


async def test_no_disabled_models_without_session_controls(
    ctx_and_captures, _supply_scenario, monkeypatch,
):
    """No picker (show_session_controls off) → no probe, no disabledModels key."""
    from optio_opencode import session as sess

    async def _boom(*a, **k):
        raise AssertionError("probe must not run without session controls")

    monkeypatch.setattr(sess, "_run_model_probe", _boom)

    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "conversation"
    cfg = OpencodeTaskConfig(
        consumer_instructions="", mode="conversation", host_protocol=False,
        conversation_ui=True,
    )
    sess_task, conv = await _launch(ctx, cfg)
    [data] = cap.widget_data
    assert "disabledModels" not in data
    await conv.close()
    await asyncio.wait_for(sess_task, timeout=30)


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
