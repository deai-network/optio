"""End-to-end conversation-mode session tests (local host, fake ACP grok).

Each test bootstraps a real optio engine (Mongo via Docker), defines the task
via ``adhoc_define``, and obtains the live ``GrokConversation`` through
``launch_and_await_result``. The shim fixtures point the session at
``grok-shim.sh`` → ``fake_grok.py``, which runs its ACP ``agent stdio``
responder when argv contains ``agent`` and ``stdio`` (no tmux/ttyd is launched
in this mode).
"""

from __future__ import annotations

import asyncio
import pathlib
import time as _time

import pytest

from optio_core.lifecycle import Optio

from optio_grok import GrokTaskConfig, create_grok_task


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


def _conversation_config(shim_install_dir: pathlib.Path, **kw) -> GrokTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        grok_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        auto_start=False,
        supports_resume=False,
    )
    base.update(kw)
    return GrokTaskConfig(**base)


@pytest.mark.asyncio
async def test_publish_send_receive_and_pending(shim_install_dir, task_root, mongo_db):
    """launch_and_await_result hands out the live conversation; one full
    send → reply turn works and is_pending flips around it."""
    optio = await _make_optio(mongo_db, "gkconv1")
    try:
        task = create_grok_task(
            process_id="gk-conv-roundtrip",
            name="Conversation roundtrip",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "gk-conv-roundtrip", session_id=None, timeout=60,
        )
        assert optio.get_published_result("gk-conv-roundtrip") is conv

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        assert not conv.is_pending()
        await conv.send("hello")
        assert conv.is_pending()
        reply = await asyncio.wait_for(msgs.get(), 10)
        assert reply == "reply-1"
        await _wait_for(lambda: not conv.is_pending())

        await conv.close()
        proc = await _wait_terminal(optio, "gk-conv-roundtrip")
        assert proc["status"]["state"] == "done"
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_permission_gate_denies_when_configured(shim_install_dir, task_root, mongo_db):
    """permission_gate=True publishes a conversation whose caller-registered
    handler answers session/request_permission; a deny yields 'tool-denied'."""
    optio = await _make_optio(mongo_db, "gkconv2")
    try:
        task = create_grok_task(
            process_id="gk-conv-perm",
            name="Conversation permission",
            config=_conversation_config(shim_install_dir, permission_gate=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "gk-conv-perm", session_id=None, timeout=60,
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
        proc = await _wait_terminal(optio, "gk-conv-perm")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_unexpected_exit_fails_task(shim_install_dir, task_root, mongo_db, monkeypatch):
    """The fake exits (7) after its first prompt turn → task 'failed' with the
    'exited unexpectedly' message; the conversation flips closed."""
    monkeypatch.setenv("FAKE_GROK_EXIT_AFTER", "1")
    optio = await _make_optio(mongo_db, "gkconv3")
    try:
        task = create_grok_task(
            process_id="gk-conv-dies",
            name="Conversation dies",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "gk-conv-dies", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("trigger the last turn")
        assert await asyncio.wait_for(msgs.get(), 10) == "reply-1"

        proc = await _wait_terminal(optio, "gk-conv-dies")
        assert proc["status"]["state"] == "failed"
        assert "exited unexpectedly" in (proc["status"]["error"] or "")
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_auto_start_sends_kickoff_first(shim_install_dir, task_root, mongo_db):
    """auto_start=True → the body sends the kickoff prompt first, so the
    caller's first send returns 'reply-2' (kickoff was prompt #1)."""
    optio = await _make_optio(mongo_db, "gkconv4")
    try:
        task = create_grok_task(
            process_id="gk-conv-kickoff",
            name="Conversation kickoff",
            config=_conversation_config(shim_install_dir, auto_start=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "gk-conv-kickoff", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        seen: list[str] = []
        while "reply-2" not in seen:
            seen.append(await asyncio.wait_for(msgs.get(), 10))

        await conv.close()
        proc = await _wait_terminal(optio, "gk-conv-kickoff")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_ui_widget_per_mode():
    """Conversation tasks carry no widget; iframe tasks use 'iframe-input'."""
    conv_task = create_grok_task(
        process_id="gk-widget-conv",
        name="Widget conv",
        config=GrokTaskConfig(consumer_instructions="x", mode="conversation"),
    )
    assert conv_task.ui_widget is None

    iframe_task = create_grok_task(
        process_id="gk-widget-iframe",
        name="Widget iframe",
        config=GrokTaskConfig(consumer_instructions="x"),
    )
    assert iframe_task.ui_widget == "iframe-input"


def test_config_validation_conversation_fields():
    """__post_init__ mirrors claudecode's conversation validations."""
    # permission_gate requires conversation mode
    with pytest.raises(ValueError):
        GrokTaskConfig(consumer_instructions="x", permission_gate=True)
    # conversation_ui requires conversation mode
    with pytest.raises(ValueError):
        GrokTaskConfig(consumer_instructions="x", conversation_ui=True)
    # iframe mode requires host_protocol
    with pytest.raises(ValueError):
        GrokTaskConfig(consumer_instructions="x", mode="iframe", host_protocol=False)
    # bad tool_verbosity
    with pytest.raises(ValueError):
        GrokTaskConfig(consumer_instructions="x", mode="conversation", tool_verbosity="loud")
    # bad thinking_verbosity
    with pytest.raises(ValueError):
        GrokTaskConfig(consumer_instructions="x", mode="conversation", thinking_verbosity="loud")
    # thinking_verbosity defaults to hidden
    assert GrokTaskConfig(consumer_instructions="x", mode="conversation").thinking_verbosity == "hidden"
    # host_protocol=False is allowed in conversation mode
    cfg = GrokTaskConfig(consumer_instructions="x", mode="conversation", host_protocol=False)
    assert cfg.host_protocol is False
