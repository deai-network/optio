"""End-to-end conversation-mode session tests (local host, fake claude).

Each test bootstraps a real optio engine (Mongo via Docker, mirroring
``optio-core/tests/test_publish_result.py``), defines the task via
``adhoc_define``, and obtains the live ``ClaudeCodeConversation`` through
``launch_and_await_result``. The shim fixtures from ``conftest.py`` point
the session at ``claude-shim.sh`` → ``fake_claude.py``, which switches to
its bidirectional stream-json mode when ``--input-format`` is in argv (no
tmux/ttyd is launched in this mode).

Covered scenarios:
  1. launch_and_await_result returns a live Conversation; send()/on_message()
     round-trip against the fake; is_pending flips around the turn.
  2. close() → task reaches 'done'; conversation closed after teardown;
     snapshot capture ran (hasSavedState set) when supports_resume=True.
  3. fake exits unexpectedly (FAKE_CLAUDE_EXIT_AFTER=1) → task 'failed',
     error message contains 'exited unexpectedly'; conversation.closed.
  4. auto_start=True → the kickoff prompt is the first stdin message
     (asserted via the fake's turn counter: our explicit send is turn 2).
  5. host_protocol=False → CLAUDE.md on disk lacks 'Log channel'; task still
     completes via close().
  6. ui_widget is None on the TaskInstance returned by create_claudecode_task
     for conversation mode, 'iframe-input' for iframe mode (pure unit assert).
"""

from __future__ import annotations

import asyncio
import pathlib
import time as _time

import pytest

from optio_core.lifecycle import Optio

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task


_TERMINAL = {"done", "failed", "cancelled"}


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


async def _wait_for(predicate, timeout: float = 10.0) -> None:
    """Poll a sync predicate until truthy or timeout."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")


def _conversation_config(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    **kw,
) -> ClaudeCodeTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        permission_mode="bypassPermissions",
        claude_install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
        fs_isolation=False,
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


@pytest.mark.asyncio
async def test_publish_send_receive_and_pending(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """launch_and_await_result hands out the live conversation; one full
    send → reply turn works and is_pending flips around it."""
    optio = await _make_optio(mongo_db, "ccconv1")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-roundtrip",
            name="Conversation roundtrip",
            config=_conversation_config(shim_install_dir, claude_cache_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-roundtrip", session_id=None, timeout=60,
        )
        # The registry serves the same live object while the task runs.
        assert optio.get_published_result("cc-conv-roundtrip") is conv

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        assert not conv.is_pending()
        await conv.send("hello")
        assert conv.is_pending()
        reply = await asyncio.wait_for(msgs.get(), 10)
        assert reply == "reply-1"
        await _wait_for(lambda: not conv.is_pending())

        await conv.close()
        proc = await _wait_terminal(optio, "cc-conv-roundtrip")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_close_completes_task_and_captures_snapshot(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """close() → cooperative shutdown → 'done'; with supports_resume=True and
    planted credentials the teardown snapshot capture runs (hasSavedState)."""
    optio = await _make_optio(mongo_db, "ccconv2")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-close",
            name="Conversation close",
            config=_conversation_config(
                shim_install_dir, claude_cache_dir,
                # Snapshot capture refuses to run without credentials on disk.
                credentials_json={"oauth_token": "test-token"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-close", session_id=None, timeout=60,
        )
        await conv.close()
        proc = await _wait_terminal(optio, "cc-conv-close")
        assert proc["status"]["state"] == "done"
        assert proc.get("hasSavedState") is True
        # Teardown terminated the subprocess → reader hit EOF → closed.
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_unexpected_exit_fails_task(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    monkeypatch,
):
    """The fake dies (exit 7) after its first result → task 'failed' with the
    'exited unexpectedly' message; the conversation flips closed."""
    monkeypatch.setenv("FAKE_CLAUDE_EXIT_AFTER", "1")
    optio = await _make_optio(mongo_db, "ccconv3")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-dies",
            name="Conversation dies",
            config=_conversation_config(
                shim_install_dir, claude_cache_dir, supports_resume=False,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-dies", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("trigger the last turn")
        assert await asyncio.wait_for(msgs.get(), 10) == "reply-1"

        proc = await _wait_terminal(optio, "cc-conv-dies")
        assert proc["status"]["state"] == "failed"
        assert "exited unexpectedly" in (proc["status"]["error"] or "")
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_auto_start_sends_kickoff_first(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """auto_start=True → the session body sends the kickoff prompt before any
    caller message. The fake numbers its replies per stdin user message, so
    our first explicit send coming back as 'reply-2' proves the kickoff was
    stdin message number one (without auto_start it would be 'reply-1', and
    'reply-2' would never appear — see test_publish_send_receive_and_pending).
    """
    optio = await _make_optio(mongo_db, "ccconv4")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-kickoff",
            name="Conversation kickoff",
            config=_conversation_config(
                shim_install_dir, claude_cache_dir, auto_start=True,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-kickoff", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        seen: list[str] = []
        while "reply-2" not in seen:
            seen.append(await asyncio.wait_for(msgs.get(), 10))

        await conv.close()
        proc = await _wait_terminal(optio, "cc-conv-kickoff")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_host_protocol_off_omits_keyword_docs_from_claude_md(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """host_protocol=False plants a CLAUDE.md without the keyword-protocol
    docs; the task still completes cleanly via close().

    CLAUDE.md is read inside ``before_execute`` (after planting, before
    launch) because the session's ``finally`` removes the workdir.
    """
    observed: dict[str, str] = {}

    async def capture_claude_md(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        observed["claude_md"] = (workdir / "CLAUDE.md").read_text()

    optio = await _make_optio(mongo_db, "ccconv5")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-noproto",
            name="Conversation no protocol",
            config=_conversation_config(
                shim_install_dir, claude_cache_dir,
                host_protocol=False,
                before_execute=capture_claude_md,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-noproto", session_id=None, timeout=60,
        )
        await conv.close()
        proc = await _wait_terminal(optio, "cc-conv-noproto")
        assert proc["status"]["state"] == "done"

        assert "Log channel" not in observed["claude_md"]
        assert "Converse with the test." in observed["claude_md"]
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_ui_widget_per_mode():
    """Conversation tasks carry no widget; iframe tasks keep iframe-input."""
    conv_task = create_claudecode_task(
        process_id="cc-widget-conv",
        name="Widget conv",
        config=ClaudeCodeTaskConfig(
            consumer_instructions="x",
            mode="conversation",
            permission_mode="bypassPermissions",
            fs_isolation=False,
        ),
    )
    assert conv_task.ui_widget is None

    iframe_task = create_claudecode_task(
        process_id="cc-widget-iframe",
        name="Widget iframe",
        config=ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False),
    )
    assert iframe_task.ui_widget == "iframe-input"


@pytest.mark.asyncio
async def test_conversation_launch_is_claustrum_wrapped(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, monkeypatch,
):
    """fs_isolation=True must wrap the headless conversation launch with
    claustrum (mirrors the iframe path)."""
    from optio_claudecode import host_actions
    from optio_host.host import LocalHost

    async def _fake_install(hook_ctx, *, install_dir=None):
        return "/fake/bin/claustrum"
    monkeypatch.setattr(host_actions, "ensure_claustrum_installed", _fake_install)
    async def _no_newer():
        return None
    monkeypatch.setattr(host_actions, "claustrum_newer_tag", _no_newer)

    captured: dict = {}
    orig = LocalHost.launch_subprocess
    async def _capture(self, cmd, **kw):
        captured["cmd"] = cmd
        raise RuntimeError("captured-launch")
    monkeypatch.setattr(LocalHost, "launch_subprocess", _capture)

    optio = await _make_optio(mongo_db, "ccconvfs")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-fs",
            name="Conversation fs-isolation",
            config=_conversation_config(
                shim_install_dir, claude_cache_dir,
                fs_isolation=True, delivery_type="t",
            ),
        )
        await optio.adhoc_define(task)
        try:
            await optio.launch_and_await_result("cc-conv-fs", session_id=None, timeout=60)
        except Exception:
            pass
        await _wait_terminal(optio, "cc-conv-fs")

        cmd = captured.get("cmd", "")
        assert "/fake/bin/claustrum --best-effort --abi-min 1 " in cmd, cmd
        assert cmd.index("/fake/bin/claustrum") < cmd.index("--input-format")
        assert " -- " in cmd
    finally:
        monkeypatch.setattr(LocalHost, "launch_subprocess", orig)
        await optio.shutdown(grace_seconds=1.0)
