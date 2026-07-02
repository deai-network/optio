"""Session-level + validation tests for the opt-in conversation UI.

Three layers (Phase II spec, docs/2026-06-10-claudecode-conversation-ui-design.md):

  1. Validation matrix (pure unit): ``conversation_ui=True`` requires
     ``mode="conversation"``; default is False.
  2. argv (pure unit): ``build_conversation_argv`` emits
     ``--include-partial-messages`` / ``--replay-user-messages`` only when
     asked for them.
  3. Session integration (same bootstrap as ``test_conversation_session.py``
     — real engine, claude shim): a ``conversation_ui=True`` task registers
     a reachable ConversationListener as widgetUpstream (per-task basic-auth
     inner credential, ``widgetData`` carrying ``protocol``/``toolVerbosity``
     plus the model-picker keys (``showModelSelector``/``models``/``currentModel``),
     ``uiWidget == "conversation"``), the SSE replay carries the
     fake's ``system/init`` event, and the listener port is closed once the
     task reaches its terminal state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import time as _time

import aiohttp
import pytest

from optio_core.lifecycle import Optio

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task
from optio_claudecode import host_actions


_TERMINAL = {"done", "failed", "cancelled"}


# --- 1. validation matrix (pure unit) ------------------------------------


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            mode="iframe",
            conversation_ui=True,
            fs_isolation=False,
        )


def test_conversation_ui_ok_in_conversation_mode():
    config = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        mode="conversation",
        permission_mode="bypassPermissions",
        conversation_ui=True,
        fs_isolation=False,
    )
    assert config.conversation_ui is True


def test_conversation_ui_defaults_false():
    config = ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert config.conversation_ui is False


# --- 2. argv flags (pure unit) --------------------------------------------


def test_argv_includes_ui_flags_when_requested():
    argv = host_actions.build_conversation_argv(
        "/opt/claude", claude_flags=["--model", "m"], permission_gate=False,
        include_partial_messages=True,
        replay_user_messages=True,
    )
    assert "--include-partial-messages" in argv
    assert "--replay-user-messages" in argv


def test_argv_defaults_omit_ui_flags():
    argv = host_actions.build_conversation_argv(
        "/opt/claude", claude_flags=[], permission_gate=True,
    )
    assert "--include-partial-messages" not in argv
    assert "--replay-user-messages" not in argv


def test_include_partial_messages_standalone_knob():
    from optio_claudecode import session as session_mod

    on = ClaudeCodeTaskConfig(
        consumer_instructions="x", mode="conversation",
        permission_mode="bypassPermissions",
        include_partial_messages=True, fs_isolation=False,
    )
    off = ClaudeCodeTaskConfig(
        consumer_instructions="x", mode="conversation",
        permission_mode="bypassPermissions", fs_isolation=False,
    )
    ui = ClaudeCodeTaskConfig(
        consumer_instructions="x", mode="conversation",
        permission_mode="bypassPermissions", conversation_ui=True,
        fs_isolation=False,
    )
    assert off.include_partial_messages is False  # default stays off
    assert session_mod._partials_enabled(on) is True
    assert session_mod._partials_enabled(off) is False
    # conversation_ui still implies partials (behavior unchanged).
    assert session_mod._partials_enabled(ui) is True


def test_partials_knob_flag_reaches_argv():
    argv = host_actions.build_conversation_argv(
        "/opt/claude", claude_flags=[], permission_gate=False,
        include_partial_messages=True,
    )
    assert "--include-partial-messages" in argv
    # The knob does not drag user-message replay along.
    assert "--replay-user-messages" not in argv


# --- 3. session integration ------------------------------------------------


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 30.0) -> dict:
    """Poll until process_id reaches a terminal state or timeout."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def _wait_widget_upstream(
    optio: Optio, process_id: str, timeout: float = 10.0,
) -> dict:
    """Poll the process doc until widgetUpstream is set; return the doc."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetUpstream"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetUpstream in {timeout}s")


async def _read_until(resp, predicate, timeout: float = 10.0) -> dict:
    """Parse SSE data frames from an open aiohttp response until one
    satisfies ``predicate``; return it. Keep-alive comment frames carry no
    data line and are skipped."""
    buf = b""

    async def _go():
        nonlocal buf
        while True:
            chunk = await resp.content.read(1024)
            if not chunk:
                raise AssertionError("SSE stream ended before a match")
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [l[5:] for l in frame.split(b"\n") if l.startswith(b"data:")]
                if not data:
                    continue
                event = json.loads(b"".join(data).strip())
                if predicate(event):
                    return event

    return await asyncio.wait_for(_go(), timeout)


async def _wait_port_refused(port: int, timeout: float = 10.0) -> None:
    """Poll until connecting to 127.0.0.1:<port> is refused."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"port {port} still accepting connections after {timeout}s")


def _ui_config(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    **kw,
) -> ClaudeCodeTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        conversation_ui=True,
        permission_mode="bypassPermissions",
        fs_isolation=False,
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


@pytest.mark.asyncio
async def test_conversation_ui_session_lifecycle(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """conversation_ui=True end to end: widgetUpstream + innerAuth registered,
    widgetData primed, uiWidget set, listener reachable (SSE replay carries
    the system/init event), and the listener stops with the task."""
    optio = await _make_optio(mongo_db, "ccui1")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-ui",
            name="Conversation UI",
            config=_ui_config(shim_install_dir, claude_cache_dir),
        )
        assert task.ui_widget == "conversation"
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-ui", session_id=None, timeout=60,
        )

        # The listener registers itself right after publish_result.
        proc = await _wait_widget_upstream(optio, "cc-conv-ui")
        upstream = proc["widgetUpstream"]
        assert upstream["url"].startswith("http://")
        inner = upstream["innerAuth"]
        assert inner is not None
        assert inner["username"] == "optio"
        assert inner["password"]
        assert proc["widgetData"] == {
            "protocol": "claudecode",
            "toolVerbosity": "description-only",
            "thinkingVerbosity": "hidden",
            "showModelSelector": False,
            "models": [
                {"id": "claude-opus-4-8", "label": "Claude Opus 4.8", "disabled": False},
                {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "disabled": False},
                {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "disabled": False},
            ],
            "currentModel": None,
            "showFileUpload": False,
            "maxUploadBytes": 10_000_000,
            "fileDownload": False,
            "maxDownloadBytes": 10_000_000,
        }
        assert proc["uiWidget"] == "conversation"

        # Hit the listener directly, authenticating with the inner credential
        # the widget proxy would inject; the replay buffer must already carry
        # the fake claude's system/init event.
        token = base64.b64encode(
            f"optio:{inner['password']}".encode(),
        ).decode()
        headers = {"Authorization": f"Basic {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{upstream['url']}/events", headers=headers,
            ) as resp:
                assert resp.status == 200
                init = await _read_until(
                    resp, lambda e: e.get("type") == "system",
                )
                assert init["subtype"] == "init"

        await conv.close()
        proc = await _wait_terminal(optio, "cc-conv-ui")
        assert proc["status"]["state"] == "done"

        # Teardown stopped the listener: the port refuses connections.
        port = int(upstream["url"].rsplit(":", 1)[1])
        await _wait_port_refused(port)
    finally:
        await optio.shutdown(grace_seconds=1.0)
