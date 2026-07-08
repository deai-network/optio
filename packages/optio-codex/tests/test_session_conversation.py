"""End-to-end conversation-mode session tests (local host, fake app-server).

Each test bootstraps a real optio engine (Mongo via Docker), defines the task
via ``adhoc_define``, and obtains the live ``CodexConversation`` through
``launch_and_await_result``. The shim fixtures point the session at
``codex-shim.sh`` → ``fake_codex.py``, which runs its app-server stdio
responder when argv contains ``app-server`` (no tmux/ttyd in this mode).
"""

from __future__ import annotations

import asyncio
import pathlib
import time as _time

import pytest

from optio_core.lifecycle import Optio

from optio_codex import CodexTaskConfig, create_codex_task


_TERMINAL = {"done", "failed", "cancelled"}


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 60.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def _wait_for(predicate, timeout: float = 60.0) -> None:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")


async def _wait_widget_data(optio: Optio, process_id: str, timeout: float = 60.0) -> dict:
    """Poll the process doc until widgetData is set; return the widgetData dict."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetData"):
            return proc["widgetData"]
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetData in {timeout}s")


def _conversation_config(shim_install_dir: pathlib.Path, **kw) -> CodexTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        auto_start=False,
        # Plan B landed: keep these tests snapshot-free.
        supports_resume=False,
    )
    base.update(kw)
    return CodexTaskConfig(**base)


@pytest.mark.asyncio
async def test_publish_send_receive_and_pending(shim_install_dir, task_root, mongo_db):
    """launch_and_await_result hands out the live conversation; one full
    send → reply turn works and is_pending flips around it."""
    optio = await _make_optio(mongo_db, "cxconv1")
    try:
        task = create_codex_task(
            process_id="cx-conv-roundtrip",
            name="Conversation roundtrip",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-roundtrip", session_id=None, timeout=60,
        )
        assert optio.get_published_result("cx-conv-roundtrip") is conv
        assert conv.thread_id  # Plan B's snapshot sessionId seam

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        assert not conv.is_pending()
        await conv.send("hello")
        assert conv.is_pending()
        reply = await asyncio.wait_for(msgs.get(), 60)
        assert reply == "reply-1"
        await _wait_for(lambda: not conv.is_pending())

        await conv.close()
        proc = await _wait_terminal(optio, "cx-conv-roundtrip")
        assert proc["status"]["state"] == "done"
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_permission_gate_denies_when_configured(shim_install_dir, task_root, mongo_db):
    """permission_gate=True publishes a conversation whose caller-registered
    handler answers requestApproval; a deny yields 'tool-denied'."""
    optio = await _make_optio(mongo_db, "cxconv2")
    try:
        task = create_codex_task(
            process_id="cx-conv-perm",
            name="Conversation permission",
            config=_conversation_config(shim_install_dir, permission_gate=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-perm", session_id=None, timeout=60,
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
        reply = await asyncio.wait_for(msgs.get(), 60)
        assert reply == "tool-denied"
        assert seen["tool"] == "echo hi"  # the handler saw the command

        await conv.close()
        proc = await _wait_terminal(optio, "cx-conv-perm")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_unexpected_exit_fails_task(shim_install_dir, task_root, mongo_db, monkeypatch):
    """The fake exits (7) after its first prompt turn → task 'failed' with the
    'exited unexpectedly' message; the conversation flips closed."""
    monkeypatch.setenv("FAKE_CODEX_EXIT_AFTER", "1")
    optio = await _make_optio(mongo_db, "cxconv3")
    try:
        task = create_codex_task(
            process_id="cx-conv-dies",
            name="Conversation dies",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-dies", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("trigger the last turn")
        assert await asyncio.wait_for(msgs.get(), 60) == "reply-1"

        proc = await _wait_terminal(optio, "cx-conv-dies")
        assert proc["status"]["state"] == "failed"
        assert "exited unexpectedly" in (proc["status"]["error"] or "")
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_auto_start_sends_kickoff_first(shim_install_dir, task_root, mongo_db):
    """auto_start=True → the body sends the kickoff prompt first, so the
    caller's first send returns 'reply-2' (kickoff was turn #1)."""
    optio = await _make_optio(mongo_db, "cxconv4")
    try:
        task = create_codex_task(
            process_id="cx-conv-kickoff",
            name="Conversation kickoff",
            config=_conversation_config(shim_install_dir, auto_start=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-kickoff", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        seen: list[str] = []
        while "reply-2" not in seen:
            seen.append(await asyncio.wait_for(msgs.get(), 60))

        await conv.close()
        proc = await _wait_terminal(optio, "cx-conv-kickoff")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_publishes_widget(shim_install_dir, task_root, mongo_db):
    """conversation_ui=True starts the listener and publishes protocol=codex
    widget data with the model list from the fake's model/list."""
    optio = await _make_optio(mongo_db, "cxconv5")
    try:
        task = create_codex_task(
            process_id="cx-conv-ui",
            name="Conversation UI",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True,
                show_session_controls=True, tool_verbosity="verbose",
            ),
        )
        assert task.ui_widget == "conversation"
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-ui", session_id=None, timeout=60,
        )

        async def _widget_data():
            proc = await optio.get_process("cx-conv-ui")
            return (proc or {}).get("widgetData") or {}

        # Poll until the widget data reports the codex protocol. Generous
        # ceiling only bounds a true hang — a tight window would flake when the
        # CPU is starved and the listener simply hasn't published yet.
        end = _time.monotonic() + 60
        wd: dict = {}
        while _time.monotonic() < end:
            wd = await _widget_data()
            if wd.get("protocol") == "codex":
                break
            await asyncio.sleep(0.05)
        assert wd.get("protocol") == "codex"
        assert wd.get("toolVerbosity") == "verbose"
        assert wd.get("showSessionControls") is True
        assert wd.get("nativeSpinner") is False
        # The model picker is now the generic id="model" SessionControl.
        controls = wd.get("controls", [])
        model_ctrl = next((c for c in controls if c.get("id") == "model"), None)
        assert model_ctrl is not None
        assert model_ctrl["kind"] == "select"
        assert [o["value"] for o in model_ctrl["options"]] == ["gpt-5.5", "gpt-5.4-mini"]
        assert model_ctrl["value"] == "gpt-5.5"

        await conv.close()
        await _wait_terminal(optio, "cx-conv-ui")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_file_upload_materialize(
    shim_install_dir, task_root, mongo_db,
):
    """conversation_ui migrates file upload to the generic materialize path: the
    session registers an in-process upload writer (resolved by
    ``Optio.materialize_upload``) that lands the bytes under ``<workdir>/uploads``
    and fires ``on_upload`` with that relpath; widgetData advertises the generic
    ``uploadUrl``."""
    seen: list[str] = []
    landed: dict[str, bytes] = {}

    async def _on_upload(hook_ctx, path):
        seen.append(path)
        landed[path] = await hook_ctx.read_from_host(path)

    optio = await _make_optio(mongo_db, "cxconvupload")
    try:
        task = create_codex_task(
            process_id="cx-conv-files",
            name="Conversation upload",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True,
                show_file_upload=True, on_upload=_on_upload,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-files", session_id=None, timeout=60,
        )

        wd = await _wait_widget_data(optio, "cx-conv-files")
        assert wd["showFileUpload"] is True
        assert wd["uploadUrl"] == (
            "{widgetProxyUrl}../../../../widget-upload/"
            f"{mongo_db.name}/cxconvupload/cx-conv-files"
        )
        # Upload flows through the generic materialize path (NOT the listener):
        # Optio.materialize_upload resolves the registered writer, which lands
        # the bytes under uploads/ and fires on_upload with the same relpath.
        rel = await optio.materialize_upload(
            "cx-conv-files", b"hello-bytes", "note.txt",
        )
        assert rel == "uploads/note.txt"
        assert seen == ["uploads/note.txt"]
        assert landed["uploads/note.txt"] == b"hello-bytes"

        await conv.close()
        await _wait_terminal(optio, "cx-conv-files")
    finally:
        await optio.shutdown(grace_seconds=1.0)


def _read_records(path: pathlib.Path) -> list[dict]:
    import json
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.mark.asyncio
async def test_conversation_sandbox_wired(
    shim_install_dir, task_root, mongo_db, tmp_path, monkeypatch,
):
    """Stage 8: a default (fs_isolation=True) conversation task launches
    ``codex app-server`` with the ``-c sandbox_workspace_write.*`` overrides
    derived from the config, and thread/start carries the kebab-case mode.

    The app-server has no ``--sandbox`` flag, so the mode rides thread/start's
    ``sandbox`` field while writable_roots/network_access ride ``-c`` at launch
    — both from the one resolved SandboxSettings.
    """
    from optio_codex import AllowedDir

    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))
    optio = await _make_optio(mongo_db, "cxconv6")
    try:
        task = create_codex_task(
            process_id="cx-conv-sandbox",
            name="Conversation sandbox",
            config=_conversation_config(
                shim_install_dir,
                extra_allowed_dirs=[AllowedDir("/scratch", "rw")],
                network_access=True,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-sandbox", session_id=None, timeout=60,
        )

        recs = _read_records(record)
        launches = [r for r in recs if "argv" in r]
        assert launches, "app-server launch was not recorded"
        argv = launches[-1]["argv"]
        assert argv[0] == "app-server"
        assert "--sandbox" not in argv  # app-server has no --sandbox flag
        assert 'sandbox_workspace_write.writable_roots=["/scratch"]' in argv
        assert "sandbox_workspace_write.network_access=true" in argv

        starts = [r for r in recs if "thread_start_params" in r]
        assert starts, "thread/start params were not recorded"
        assert starts[-1]["thread_start_params"]["sandbox"] == "workspace-write"

        await conv.close()
        await _wait_terminal(optio, "cx-conv-sandbox")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_unconfined_when_fs_isolation_off(
    shim_install_dir, task_root, mongo_db, tmp_path, monkeypatch,
):
    """fs_isolation=False → app-server launch carries no
    ``sandbox_workspace_write.*`` overrides and thread/start requests
    danger-full-access."""
    record = tmp_path / "codex_record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))
    optio = await _make_optio(mongo_db, "cxconv7")
    try:
        task = create_codex_task(
            process_id="cx-conv-unconfined",
            name="Conversation unconfined",
            config=_conversation_config(shim_install_dir, fs_isolation=False),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cx-conv-unconfined", session_id=None, timeout=60,
        )

        recs = _read_records(record)
        launches = [r for r in recs if "argv" in r]
        assert launches, "app-server launch was not recorded"
        argv = launches[-1]["argv"]
        assert not any(a.startswith("sandbox_workspace_write.") for a in argv)

        starts = [r for r in recs if "thread_start_params" in r]
        assert starts, "thread/start params were not recorded"
        assert (
            starts[-1]["thread_start_params"]["sandbox"] == "danger-full-access"
        )

        await conv.close()
        await _wait_terminal(optio, "cx-conv-unconfined")
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_ui_widget_per_mode():
    """Conversation tasks carry no widget unless conversation_ui; iframe
    tasks use the 'iframe-input' widget (TUI + operator input box)."""
    conv_task = create_codex_task(
        process_id="cx-widget-conv",
        name="Widget conv",
        config=CodexTaskConfig(consumer_instructions="x", mode="conversation"),
    )
    assert conv_task.ui_widget is None

    ui_task = create_codex_task(
        process_id="cx-widget-conv-ui",
        name="Widget conv ui",
        config=CodexTaskConfig(
            consumer_instructions="x", mode="conversation", conversation_ui=True,
        ),
    )
    assert ui_task.ui_widget == "conversation"

    iframe_task = create_codex_task(
        process_id="cx-widget-iframe",
        name="Widget iframe",
        config=CodexTaskConfig(consumer_instructions="x"),
    )
    assert iframe_task.ui_widget == "iframe-input"
