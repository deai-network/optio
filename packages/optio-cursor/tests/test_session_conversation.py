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
