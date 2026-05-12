"""Integration tests for run_opencode_session over LocalHost + fake_opencode."""

import asyncio
import os
import sys
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_core.models import BasicAuth
from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig


FAKE_OPENCODE = os.path.join(os.path.dirname(__file__), "fake_opencode.py")


@dataclass
class Captured:
    progress: list[tuple[float | None, str | None]] = field(default_factory=list)
    widget_upstream: list[tuple[str, object]] = field(default_factory=list)
    widget_data: list[object] = field(default_factory=list)


@pytest_asyncio.fixture
async def ctx_and_captures(mongo_db, monkeypatch):
    """A ProcessContext backed by a real mongo + capture hooks."""
    # Insert a minimal process doc so store writes succeed.
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

    # Intercept progress + widget calls.
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


def _config(scenario: str, deliverable_cb=None, raises: bool = False) -> OpencodeTaskConfig:
    return OpencodeTaskConfig(
        consumer_instructions=f"(scenario: {scenario})",
        on_deliverable=deliverable_cb,
    )


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """Substitute fake_opencode.py for the real opencode binary.

    fake_opencode.py uses argparse.parse_known_args, so the trailing
    ``web --port=0 --hostname=127.0.0.1`` from launch_opencode is harmless
    — only ``--scenario <name>`` is required. We bake the scenario into
    the opencode_executable string itself.
    """
    from optio_opencode import host_actions
    orig_launch = host_actions.launch_opencode
    scenario_holder: dict = {"name": "happy"}

    async def _launch(host, password, *, ready_timeout_s=30.0, opencode_executable="opencode"):
        del opencode_executable  # we substitute fully
        return await orig_launch(
            host, password,
            ready_timeout_s=ready_timeout_s,
            opencode_executable=(
                f"{sys.executable} {FAKE_OPENCODE} "
                f"--scenario {scenario_holder['name']}"
            ),
        )
    monkeypatch.setattr(host_actions, "launch_opencode", _launch)

    # Also short-circuit ensure_opencode_installed and opencode_version
    # so we don't try to invoke a real `opencode` binary.
    async def _ensure(hook_ctx, install_if_missing=True):
        return "opencode"
    monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)

    async def _version(host, *, opencode_executable="opencode"):
        return None
    monkeypatch.setattr(host_actions, "opencode_version", _version)

    # Snapshot capture calls opencode_export — patch it to use the fake.
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


# ---- scenarios --------------------------------------------------------

async def test_happy_path(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    received: list[tuple[str, str]] = []
    async def on_d(hook_ctx, path, text):
        received.append((path, text))

    cfg = _config("happy", deliverable_cb=on_d)
    await run_opencode_session(ctx, cfg)

    assert len(received) == 1
    p, text = received[0]
    assert p == "out.txt"
    assert text == "hello 42 blue"


async def test_status_percent_is_reported(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "status_percent"
    await run_opencode_session(ctx, _config("status_percent"))

    percentages = [p for (p, _m) in cap.progress if p is not None]
    assert 10 in percentages
    assert 100 in percentages


async def test_error_triggers_failure(ctx_and_captures, _supply_scenario):
    ctx, _cap, _ = ctx_and_captures
    _supply_scenario["name"] = "error"
    with pytest.raises(RuntimeError, match="auth failed"):
        await run_opencode_session(ctx, _config("error"))


async def test_subprocess_exit_before_done_is_failure(ctx_and_captures, _supply_scenario):
    ctx, _cap, _ = ctx_and_captures
    _supply_scenario["name"] = "no_done_then_exit"
    with pytest.raises(RuntimeError, match=r"body returned before DONE"):
        await run_opencode_session(ctx, _config("no_done_then_exit"))


async def test_invalid_deliverable_path_is_skipped(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "escape_path"

    received: list = []
    async def on_d(hook_ctx, path, text):
        received.append((path, text))

    await run_opencode_session(ctx, _config("escape_path", deliverable_cb=on_d))
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("invalid deliverable path" in m for m in messages)


async def test_deliverable_outside_deliverables_dir_is_skipped(
    ctx_and_captures, _supply_scenario,
):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "inside_workdir_not_deliverables"

    received: list = []
    async def on_d(hook_ctx, path, text):
        received.append((path, text))

    await run_opencode_session(
        ctx,
        _config("inside_workdir_not_deliverables", deliverable_cb=on_d),
    )
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("not under deliverables/" in m for m in messages)


async def test_non_utf8_deliverable_is_skipped(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "non_utf8"

    received: list = []
    async def on_d(hook_ctx, path, text):
        received.append((path, text))

    await run_opencode_session(ctx, _config("non_utf8", deliverable_cb=on_d))
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("not valid UTF-8" in m for m in messages)


async def test_callback_raises_does_not_fail_task(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    async def on_d(hook_ctx, path, text):
        raise RuntimeError("boom")

    await run_opencode_session(ctx, _config("happy", deliverable_cb=on_d))
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("on_deliverable callback raised" in m for m in messages)


async def test_cancellation_triggers_aggressive_teardown(ctx_and_captures, _supply_scenario):
    ctx, cap, cancellation_flag = ctx_and_captures
    _supply_scenario["name"] = "sleep_forever"

    async def _cancel_soon():
        await asyncio.sleep(0.2)
        cancellation_flag.set()

    asyncio.create_task(_cancel_soon())
    # run_opencode_session should return (not raise) when cancelled — optio-core
    # observes the cancellation flag separately and transitions to cancelled.
    await run_opencode_session(ctx, _config("sleep_forever"))


async def test_widget_upstream_and_data_are_set(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"
    await run_opencode_session(ctx, _config("happy"))

    assert len(cap.widget_upstream) == 1
    url, inner_auth = cap.widget_upstream[0]
    assert url.startswith("http://127.0.0.1:")
    assert isinstance(inner_auth, BasicAuth)
    assert inner_auth.username == "opencode"
    assert len(inner_auth.password) > 16

    assert len(cap.widget_data) == 1
    payload = cap.widget_data[0]
    assert payload["localStorageOverrides"]["opencode.settings.dat:defaultServerUrl"] == "{widgetProxyUrl}"
    # iframeSrc points at the URL-safe-base64-encoded workdir + /session/<id>
    # where <id> comes from a pre-created opencode session (fake_opencode's
    # test double returns "fake-session-id" for POST /session).  This skips
    # both the project picker and opencode's "new session" default, which
    # means concurrent viewers of the same optio process share live state.
    import base64 as _b64
    assert payload["iframeSrc"].startswith("{widgetProxyUrl}")
    assert payload["iframeSrc"].endswith("/session/fake-session-id")
    middle = payload["iframeSrc"][
        len("{widgetProxyUrl}"):-len("/session/fake-session-id")
    ]
    # Pad back to multiple of 4 and decode — should round-trip to a workdir
    # path that begins with our optio-opencode tempdir prefix.
    padded = middle + "=" * (-len(middle) % 4)
    decoded = _b64.urlsafe_b64decode(padded).decode("utf-8")
    assert decoded.startswith("/") and "optio-opencode" in decoded


# ---- resume log -----------------------------------------------------------


async def test_append_resume_log_entry_writes_iso_timestamp(tmp_workdir):
    """Calling _append_resume_log_entry once writes one ISO 8601 line."""
    import os
    import re
    import sys
    from optio_host.host import LocalHost
    from optio_opencode.session import _append_resume_log_entry

    host = LocalHost(taskdir=tmp_workdir)
    await host.setup_workdir()

    await _append_resume_log_entry(host)

    resume_log = os.path.join(host.workdir, "resume.log")
    assert os.path.isfile(resume_log)
    with open(resume_log) as f:
        content = f.read()
    lines = [line for line in content.splitlines() if line]
    assert len(lines) == 1
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", lines[0])


async def test_append_resume_log_entry_appends_on_repeat_call(tmp_workdir):
    """Two calls produce two lines (append, not overwrite)."""
    import asyncio
    import os
    import sys
    from optio_host.host import LocalHost
    from optio_opencode.session import _append_resume_log_entry

    host = LocalHost(taskdir=tmp_workdir)
    await host.setup_workdir()

    await _append_resume_log_entry(host)
    # Sleep just over a second so the second timestamp differs
    # (seconds-precision format).
    await asyncio.sleep(1.1)
    await _append_resume_log_entry(host)

    resume_log = os.path.join(host.workdir, "resume.log")
    with open(resume_log) as f:
        lines = [line for line in f.read().splitlines() if line]
    assert len(lines) == 2
    assert lines[0] != lines[1]


async def test_session_local_supports_resume_false_skips_resume_log(
    ctx_and_captures, _supply_scenario, tmp_workdir, monkeypatch,
):
    """With supports_resume=False, no resume.log is created during the session.

    We verify by patching _append_resume_log_entry and asserting it isn't called.
    """
    from unittest.mock import AsyncMock
    import optio_opencode.session as session_mod

    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = OpencodeTaskConfig(
        consumer_instructions="(scenario: happy)",
        supports_resume=False,
    )

    spy = AsyncMock()
    monkeypatch.setattr(session_mod, "_append_resume_log_entry", spy)
    await run_opencode_session(ctx, cfg)

    spy.assert_not_called()


async def test_session_local_supports_resume_true_calls_append(
    ctx_and_captures, _supply_scenario, tmp_workdir, monkeypatch,
):
    """With supports_resume=True (default), _append_resume_log_entry IS called."""
    from unittest.mock import AsyncMock
    import optio_opencode.session as session_mod

    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = _config("happy")  # default supports_resume=True

    spy = AsyncMock()
    monkeypatch.setattr(session_mod, "_append_resume_log_entry", spy)
    await run_opencode_session(ctx, cfg)

    assert spy.await_count == 1


async def test_session_local_supports_resume_false_skips_snapshot_capture(
    ctx_and_captures, _supply_scenario, tmp_workdir,
):
    """With supports_resume=False, no entry is added to the snapshots collection."""
    from optio_opencode.snapshots import SESSION_SNAPSHOT_COLLECTION_SUFFIX
    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = OpencodeTaskConfig(
        consumer_instructions="(scenario: happy)",
        supports_resume=False,
    )
    await run_opencode_session(ctx, cfg)

    coll_name = f"{ctx._prefix}{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"
    snapshots_coll = ctx._db[coll_name]
    count = await snapshots_coll.count_documents({"processId": "p"})
    assert count == 0


async def test_session_local_supports_resume_true_captures_snapshot(
    ctx_and_captures, _supply_scenario, tmp_workdir,
):
    """With supports_resume=True (default), a snapshot IS captured."""
    from optio_opencode.snapshots import SESSION_SNAPSHOT_COLLECTION_SUFFIX
    ctx, _, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    cfg = _config("happy")  # default supports_resume=True
    await run_opencode_session(ctx, cfg)

    coll_name = f"{ctx._prefix}{SESSION_SNAPSHOT_COLLECTION_SUFFIX}"
    snapshots_coll = ctx._db[coll_name]
    count = await snapshots_coll.count_documents({"processId": "p"})
    assert count == 1
