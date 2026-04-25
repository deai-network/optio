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
def _patch_localhost_to_use_fake(monkeypatch):
    """Point LocalHost at fake_opencode.py for the duration of the test."""
    import optio_opencode.host as host_mod
    orig_init = host_mod.LocalHost.__init__

    def _init(self, workdir: str, opencode_cmd=None):
        return orig_init(
            self,
            workdir=workdir,
            opencode_cmd=[sys.executable, FAKE_OPENCODE],
        )

    monkeypatch.setattr(host_mod.LocalHost, "__init__", _init)


@pytest.fixture(autouse=True)
def _supply_scenario(monkeypatch):
    """fake_opencode expects --scenario; inject via launch_opencode's extra_args."""
    import optio_opencode.host as host_mod
    orig_launch = host_mod.LocalHost.launch_opencode
    scenario_holder: dict = {"name": "happy"}

    async def _launch(self, password, ready_timeout_s, extra_args=None, env=None):
        return await orig_launch(
            self, password, ready_timeout_s,
            extra_args=["--scenario", scenario_holder["name"]],
            env=env,
        )
    monkeypatch.setattr(host_mod.LocalHost, "launch_opencode", _launch)
    return scenario_holder


# ---- scenarios --------------------------------------------------------

async def test_happy_path(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    received: list[tuple[str, str]] = []
    async def on_d(path, text):
        received.append((path, text))

    cfg = _config("happy", deliverable_cb=on_d)
    await run_opencode_session(ctx, cfg)

    assert len(received) == 1
    p, text = received[0]
    assert "deliverables/out.txt" in p
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
    with pytest.raises(RuntimeError, match=r"exited with code 0 before DONE"):
        await run_opencode_session(ctx, _config("no_done_then_exit"))


async def test_invalid_deliverable_path_is_skipped(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "escape_path"

    received: list = []
    async def on_d(path, text):
        received.append((path, text))

    await run_opencode_session(ctx, _config("escape_path", deliverable_cb=on_d))
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("invalid deliverable path" in m for m in messages)


async def test_non_utf8_deliverable_is_skipped(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "non_utf8"

    received: list = []
    async def on_d(path, text):
        received.append((path, text))

    await run_opencode_session(ctx, _config("non_utf8", deliverable_cb=on_d))
    assert received == []
    messages = [m for (_p, m) in cap.progress if m is not None]
    assert any("not valid UTF-8" in m for m in messages)


async def test_callback_raises_does_not_fail_task(ctx_and_captures, _supply_scenario):
    ctx, cap, _ = ctx_and_captures
    _supply_scenario["name"] = "happy"

    async def on_d(path, text):
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
