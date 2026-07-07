"""End-to-end conversation-mode session tests (local host, fake ACP cursor).

Each test bootstraps a real optio engine (Mongo via Docker), defines the task
via ``adhoc_define``, and obtains the live ``CursorConversation`` through
``launch_and_await_result``. The shim fixtures point the session at
``cursor-shim.sh`` → ``fake_cursor.py``, which runs its ACP responder when
argv contains the ``acp`` subcommand (no tmux/ttyd is launched in this mode).

Adapted from optio-grok's test_session_conversation.py.
"""

from __future__ import annotations

import asyncio
import pathlib
import time as _time

import pytest

from optio_core.lifecycle import Optio

from optio_cursor import CursorTaskConfig, create_cursor_task


_TERMINAL = {"done", "failed", "cancelled"}


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 30.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def _wait_for(predicate, timeout: float = 10.0) -> None:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")


async def _wait_widget_data(optio: Optio, process_id: str, timeout: float = 10.0) -> dict:
    """Poll the process doc until widgetData is set; return the widgetData dict."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetData"):
            return proc["widgetData"]
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetData in {timeout}s")


def _conversation_config(shim_install_dir: pathlib.Path, **kw) -> CursorTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        cursor_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        auto_start=False,
        supports_resume=False,
    )
    base.update(kw)
    return CursorTaskConfig(**base)


# Spawn-heavy conversation tests, timing-fragile under concurrent load. Marked
# `serial` for the final non-parallel phase.
pytestmark = pytest.mark.serial


@pytest.mark.asyncio
async def test_publish_send_receive_and_pending(shim_install_dir, task_root, mongo_db):
    """launch_and_await_result hands out the live conversation; one full
    send → reply turn works and is_pending flips around it."""
    optio = await _make_optio(mongo_db, "cuconv1")
    try:
        task = create_cursor_task(
            process_id="cu-conv-roundtrip",
            name="Conversation roundtrip",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cu-conv-roundtrip", session_id=None, timeout=60,
        )
        assert optio.get_published_result("cu-conv-roundtrip") is conv

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        assert not conv.is_pending()
        await conv.send("hello")
        assert conv.is_pending()
        reply = await asyncio.wait_for(msgs.get(), 10)
        assert reply == "reply-1"
        await _wait_for(lambda: not conv.is_pending())

        await conv.close()
        proc = await _wait_terminal(optio, "cu-conv-roundtrip")
        assert proc["status"]["state"] == "done"
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_permission_gate_denies_when_configured(shim_install_dir, task_root, mongo_db):
    """permission_gate=True publishes a conversation whose caller-registered
    handler answers session/request_permission; a deny yields 'tool-denied'."""
    optio = await _make_optio(mongo_db, "cuconv2")
    try:
        task = create_cursor_task(
            process_id="cu-conv-perm",
            name="Conversation permission",
            config=_conversation_config(shim_install_dir, permission_gate=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cu-conv-perm", session_id=None, timeout=60,
        )

        from optio_agents.conversation import PermissionDecision
        seen: dict = {}

        async def deny_handler(req):
            seen["tool"] = req.tool_name
            return PermissionDecision(behavior="deny", message="not allowed")

        conv.on_permission_request(deny_handler)

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("please use a TOOL to do it")
        reply = await asyncio.wait_for(msgs.get(), 10)
        assert reply == "tool-denied"
        assert seen["tool"]  # the handler saw the request

        await conv.close()
        proc = await _wait_terminal(optio, "cu-conv-perm")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_unexpected_exit_fails_task(shim_install_dir, task_root, mongo_db, monkeypatch):
    """The fake exits (7) after its first prompt turn → task 'failed' with the
    'exited unexpectedly' message; the conversation flips closed."""
    monkeypatch.setenv("FAKE_CURSOR_EXIT_AFTER", "1")
    optio = await _make_optio(mongo_db, "cuconv3")
    try:
        task = create_cursor_task(
            process_id="cu-conv-dies",
            name="Conversation dies",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cu-conv-dies", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("trigger the last turn")
        assert await asyncio.wait_for(msgs.get(), 10) == "reply-1"

        proc = await _wait_terminal(optio, "cu-conv-dies")
        assert proc["status"]["state"] == "failed"
        assert "exited unexpectedly" in (proc["status"]["error"] or "")
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_auto_start_sends_kickoff_first(shim_install_dir, task_root, mongo_db):
    """auto_start=True → the body sends the kickoff prompt first, so the
    caller's first send returns 'reply-2' (kickoff was prompt #1)."""
    optio = await _make_optio(mongo_db, "cuconv4")
    try:
        task = create_cursor_task(
            process_id="cu-conv-kickoff",
            name="Conversation kickoff",
            config=_conversation_config(shim_install_dir, auto_start=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cu-conv-kickoff", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        seen: list[str] = []
        while "reply-2" not in seen:
            seen.append(await asyncio.wait_for(msgs.get(), 10))

        await conv.close()
        proc = await _wait_terminal(optio, "cu-conv-kickoff")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_model_probe_disables_gated_models(
    shim_install_dir, task_root, mongo_db, monkeypatch,
):
    """The startup probe drives the live ACP: a plan-gated model answers
    'Upgrade your plan to continue' (unusable); a working model answers Budapest
    (usable). The originally-active model is restored afterwards."""
    from optio_cursor import model_probe

    monkeypatch.setenv("FAKE_CURSOR_ACP_MODELS", "m-good,m-gated")
    monkeypatch.setenv("FAKE_CURSOR_GATED_MODELS", "m-gated")
    optio = await _make_optio(mongo_db, "cuprobe")
    try:
        task = create_cursor_task(
            process_id="cu-probe", name="Probe",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cu-probe", session_id=None, timeout=60,
        )
        assert conv.current_model_id == "m-good"  # from session/new
        usable = await model_probe.probe_models(
            conv, ["m-good", "m-gated"], per_model_timeout=10,
        )
        assert usable == {"m-good": True, "m-gated": False}
        assert conv.current_model_id == "m-good"  # restored after probe

        # reset_session drops the probe's turns (fresh session/new), returns the
        # abandoned session id (so the caller purges its on-disk records), and the
        # conversation stays usable for the operator.
        prev_sid = conv._session_id
        abandoned = await conv.reset_session()
        assert abandoned == prev_sid
        assert conv._session_id != prev_sid
        assert conv.current_model_id == "m-good"
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("hello")
        assert await asyncio.wait_for(msgs.get(), 10)
        await conv.close()
        await _wait_terminal(optio, "cu-probe")
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_ui_widget_per_mode():
    """Conversation tasks carry no widget; iframe tasks use 'iframe-input'."""
    conv_task = create_cursor_task(
        process_id="cu-widget-conv",
        name="Widget conv",
        config=CursorTaskConfig(consumer_instructions="x", mode="conversation"),
    )
    assert conv_task.ui_widget is None

    iframe_task = create_cursor_task(
        process_id="cu-widget-iframe",
        name="Widget iframe",
        config=CursorTaskConfig(consumer_instructions="x"),
    )
    assert iframe_task.ui_widget == "iframe-input"


@pytest.mark.asyncio
async def test_conversation_resume_snapshot_records_session_id(
    shim_install_dir, task_root, mongo_db,
):
    """A conversation-mode task with supports_resume captures a snapshot whose
    sessionId is the live ACP session id — the seam resume reads back to call
    session/load DIRECTLY (skipping the session/list heuristic) and replay the
    prior conversation. (The fake ACP cursor's session/new returns
    ``fake-cursor-session-1`` on the first — and only — new session here.)"""
    from optio_cursor.snapshots import load_latest_snapshot
    optio = await _make_optio(mongo_db, "cuconvsid")
    try:
        task = create_cursor_task(
            process_id="cu-conv-sid",
            name="Conversation session-id snapshot",
            config=_conversation_config(shim_install_dir, supports_resume=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cu-conv-sid", session_id=None, timeout=60,
        )
        await conv.send("hello")
        await conv.close()
        proc = await _wait_terminal(optio, "cu-conv-sid")
        assert proc["status"]["state"] == "done"
        snap = await load_latest_snapshot(mongo_db, "cuconvsid", "cu-conv-sid")
        assert snap is not None, "conversation-mode session captured no snapshot"
        assert snap["sessionId"] == "fake-cursor-session-1"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_resume_replays_prior_history_via_persisted_id(
    shim_install_dir, task_root, mongo_db, tmp_path, monkeypatch,
):
    """conversation_ui resume: the fresh run records its ACP sessionId in the
    snapshot; the resumed run reads it back and calls ``session/load`` with THAT
    persisted id (AFTER the listener subscribes) — directly, never via
    session/list. The fake cursor records every session/load id to a durable path
    outside the wiped workdir, so we assert the resumed run loaded the persisted
    id (not a heuristic pick)."""
    import json as _json
    from optio_cursor.snapshots import load_latest_snapshot
    record = tmp_path / "cursor_acp_load.jsonl"
    monkeypatch.setenv("FAKE_CURSOR_ACP_RECORD", str(record))
    # Populate the ACP session/new models block so the conversation_ui model
    # picker reads it directly (session_models path) instead of falling back to
    # the `cursor-agent models` CLI (which the shim does not answer). Distinct
    # from show_session_controls (default off), so no model probe / reset runs —
    # the fresh session id stays fake-cursor-session-1.
    monkeypatch.setenv("FAKE_CURSOR_ACP_MODELS", "m1")
    optio = await _make_optio(mongo_db, "cuconvreplay")
    try:
        task = create_cursor_task(
            process_id="cu-conv-replay",
            name="Conversation resume replay",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True, supports_resume=True,
            ),
        )
        await optio.adhoc_define(task)

        # Fresh run: one turn writes the session store; teardown snapshots the
        # live ACP sessionId.
        conv = await optio.launch_and_await_result(
            "cu-conv-replay", session_id=None, timeout=60,
        )
        await conv.send("hello")
        await conv.close()
        proc = await _wait_terminal(optio, "cu-conv-replay")
        assert proc["status"]["state"] == "done"
        snap = await load_latest_snapshot(mongo_db, "cuconvreplay", "cu-conv-replay")
        persisted = snap["sessionId"]
        assert persisted

        # Resumed run: restores the workdir, reads the persisted sessionId, and
        # (conversation_ui + resuming) calls session/load with it to replay.
        conv2 = await optio.launch_and_await_result(
            "cu-conv-replay", resume=True, session_id=None, timeout=60,
        )
        await conv2.close()
        await _wait_terminal(optio, "cu-conv-replay")

        lines = [l for l in record.read_text().splitlines() if l.strip()]
        loads = [_json.loads(l)["session_load"] for l in lines]
        assert persisted in loads, f"session/load not called with persisted id: {loads}"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_resume_session_load_reject_falls_back(
    shim_install_dir, task_root, mongo_db, tmp_path, monkeypatch,
):
    """Graceful fallback: when cursor rejects session/load on resume (unknown id /
    no loadable store after restore), the wrapper keeps the fresh session — no
    exception escapes, and the resumed run still reaches 'done' and is usable."""
    monkeypatch.setenv("FAKE_CURSOR_ACP_LOAD", "reject")
    # See the replay test: populate the ACP models block so the picker skips the
    # unanswered `cursor-agent models` CLI fallback.
    monkeypatch.setenv("FAKE_CURSOR_ACP_MODELS", "m1")
    optio = await _make_optio(mongo_db, "cuconvreject")
    try:
        task = create_cursor_task(
            process_id="cu-conv-reject",
            name="Conversation resume reject",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True, supports_resume=True,
            ),
        )
        await optio.adhoc_define(task)

        conv = await optio.launch_and_await_result(
            "cu-conv-reject", session_id=None, timeout=60,
        )
        await conv.send("hello")
        await conv.close()
        assert (await _wait_terminal(optio, "cu-conv-reject"))["status"]["state"] == "done"

        # Resume: session/load is rejected -> fall back to the fresh session; the
        # conversation is still usable (a turn round-trips) and the run completes.
        conv2 = await optio.launch_and_await_result(
            "cu-conv-reject", resume=True, session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv2.on_message(msgs.put_nowait)
        await conv2.send("still working after fallback")
        assert await asyncio.wait_for(msgs.get(), 10)  # a reply arrives
        await conv2.close()
        assert (await _wait_terminal(optio, "cu-conv-reject"))["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_file_upload_materialize(
    shim_install_dir, task_root, mongo_db, monkeypatch,
):
    """conversation_ui migrates file upload to the generic materialize path: the
    session registers an in-process upload writer (resolved by
    ``Optio.materialize_upload``) that lands the bytes under ``<workdir>/uploads``
    and fires ``on_upload`` with that relpath; widgetData advertises the generic
    ``uploadUrl``."""
    # Populate the ACP session/new models block so the conversation_ui model
    # picker reads it directly instead of falling back to the `cursor-agent
    # models` CLI (which the shim does not answer).
    monkeypatch.setenv("FAKE_CURSOR_ACP_MODELS", "m1")
    seen: list[str] = []
    landed: dict[str, bytes] = {}

    async def _on_upload(hook_ctx, path):
        seen.append(path)
        landed[path] = await hook_ctx.read_from_host(path)

    optio = await _make_optio(mongo_db, "cuconvupload")
    try:
        task = create_cursor_task(
            process_id="cu-conv-files",
            name="Conversation upload",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True,
                show_file_upload=True, on_upload=_on_upload,
            ),
        )
        await optio.adhoc_define(task)
        await optio.launch_and_await_result(
            "cu-conv-files", session_id=None, timeout=60,
        )

        wd = await _wait_widget_data(optio, "cu-conv-files")
        assert wd["showFileUpload"] is True
        assert wd["uploadUrl"] == (
            "{widgetProxyUrl}../../../../widget-upload/"
            f"{mongo_db.name}/cuconvupload/cu-conv-files"
        )
        # Upload flows through the generic materialize path (NOT the listener):
        # Optio.materialize_upload resolves the registered writer, which lands
        # the bytes under uploads/ and fires on_upload with the same relpath.
        rel = await optio.materialize_upload(
            "cu-conv-files", b"hello-bytes", "note.txt",
        )
        assert rel == "uploads/note.txt"
        assert seen == ["uploads/note.txt"]
        assert landed["uploads/note.txt"] == b"hello-bytes"
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_config_validation_conversation_fields():
    """__post_init__ mirrors grok's conversation validations."""
    # permission_gate requires conversation mode
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", permission_gate=True)
    # conversation_ui requires conversation mode
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", conversation_ui=True)
    # iframe mode requires host_protocol
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", mode="iframe", host_protocol=False)
    # bad tool_verbosity
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", mode="conversation", tool_verbosity="loud")
    # host_protocol=False is allowed in conversation mode
    cfg = CursorTaskConfig(consumer_instructions="x", mode="conversation", host_protocol=False)
    assert cfg.host_protocol is False
