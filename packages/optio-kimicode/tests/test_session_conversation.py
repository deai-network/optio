"""End-to-end conversation-mode session tests (local host, fake ACP kimi).

Each test bootstraps a real optio engine (Mongo via Docker), defines the task
via ``adhoc_define``, and obtains the live ``KimiCodeConversation`` through
``launch_and_await_result``. The shim fixtures point the session at
``kimi-shim.sh`` → ``fake_kimi.py``, which runs its ACP ``kimi acp`` stdio
responder when argv contains ``acp`` (no ``kimi server``/SPA is launched in
this mode).

Ported from optio-grok's test_session_conversation.py; the kimi deltas:
``kimi acp`` (not ``grok agent stdio``), no ttyd/tmux, and the fake exits via
``FAKE_KIMI_EXIT_AFTER`` for the unexpected-exit case.
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

from optio_kimicode import KimiCodeTaskConfig, create_kimicode_task


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


async def _wait_widget_upstream(optio: Optio, process_id: str, timeout: float = 60.0) -> dict:
    """Poll the process doc until widgetUpstream is set; return the doc."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetUpstream"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetUpstream in {timeout}s")


async def _wait_widget_data(optio: Optio, process_id: str, timeout: float = 60.0) -> dict:
    """Poll the process doc until widgetData is set; return the widgetData dict."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetData"):
            return proc["widgetData"]
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetData in {timeout}s")


async def _wait_port_refused(port: int, timeout: float = 60.0) -> None:
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


def _conversation_config(shim_install_dir: pathlib.Path, **kw) -> KimiCodeTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        install_dir=str(shim_install_dir),
        auto_start=False,
        supports_resume=False,
        # fs-isolation (claustrum wrap) lands in a later task; keep it off so the
        # conversation launches the fake `kimi acp` directly.
        fs_isolation=False,
    )
    base.update(kw)
    return KimiCodeTaskConfig(**base)


@pytest.mark.asyncio
async def test_publish_send_receive_and_pending(shim_install_dir, task_root, mongo_db):
    """launch_and_await_result hands out the live conversation; one full
    send → reply turn works and is_pending flips around it."""
    optio = await _make_optio(mongo_db, "kkconv1")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-roundtrip",
            name="Conversation roundtrip",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "kk-conv-roundtrip", session_id=None, timeout=60,
        )
        assert optio.get_published_result("kk-conv-roundtrip") is conv

        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        assert not conv.is_pending()
        await conv.send("hello")
        assert conv.is_pending()
        reply = await asyncio.wait_for(msgs.get(), 60)
        assert reply == "reply-1"
        await _wait_for(lambda: not conv.is_pending())

        await conv.close()
        proc = await _wait_terminal(optio, "kk-conv-roundtrip")
        assert proc["status"]["state"] == "done"
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_permission_gate_denies_when_configured(shim_install_dir, task_root, mongo_db):
    """permission_gate=True publishes a conversation whose caller-registered
    handler answers session/request_permission; a deny yields 'tool-denied'."""
    optio = await _make_optio(mongo_db, "kkconv2")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-perm",
            name="Conversation permission",
            config=_conversation_config(shim_install_dir, permission_gate=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "kk-conv-perm", session_id=None, timeout=60,
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
        assert seen["tool"]  # the handler saw the request

        await conv.close()
        proc = await _wait_terminal(optio, "kk-conv-perm")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_unexpected_exit_fails_task(shim_install_dir, task_root, mongo_db, monkeypatch):
    """The fake exits (7) after its first prompt turn → task 'failed' with the
    'exited unexpectedly' message; the conversation flips closed."""
    monkeypatch.setenv("FAKE_KIMI_EXIT_AFTER", "1")
    optio = await _make_optio(mongo_db, "kkconv3")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-dies",
            name="Conversation dies",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "kk-conv-dies", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("trigger the last turn")
        assert await asyncio.wait_for(msgs.get(), 60) == "reply-1"

        proc = await _wait_terminal(optio, "kk-conv-dies")
        assert proc["status"]["state"] == "failed"
        assert "exited unexpectedly" in (proc["status"]["error"] or "")
        await _wait_for(lambda: conv.closed)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_launch_failure_surfaces_stderr(
    shim_install_dir, ctx_and_captures, task_root, monkeypatch,
):
    """A hard exit at launch — ``kimi acp`` writes a diagnostic to stderr and
    exits non-zero BEFORE the ACP handshake — surfaces that stderr in the raised
    RuntimeError, instead of a bare 'process ended' with no reason. The launch
    uses ``merge_stderr=False``, so without the drain the diagnostic is lost."""
    ctx, *_ = ctx_and_captures
    monkeypatch.setenv("FAKE_KIMI_ACP_FAIL_LAUNCH", "boom: kimi could not start")
    task = create_kimicode_task(
        process_id="kk-conv-fail",
        name="Conversation launch failure",
        config=_conversation_config(shim_install_dir),
    )
    with pytest.raises(RuntimeError) as exc:
        await task.execute(ctx)
    msg = str(exc.value)
    assert "kimi acp failed to start" in msg
    assert "boom: kimi could not start" in msg


@pytest.mark.asyncio
async def test_auto_start_sends_kickoff_first(shim_install_dir, task_root, mongo_db):
    """auto_start=True → the body sends the kickoff prompt first, so the
    caller's first send returns 'reply-2' (kickoff was prompt #1)."""
    optio = await _make_optio(mongo_db, "kkconv4")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-kickoff",
            name="Conversation kickoff",
            config=_conversation_config(shim_install_dir, auto_start=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "kk-conv-kickoff", session_id=None, timeout=60,
        )
        msgs: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        await conv.send("ping")
        seen: list[str] = []
        while "reply-2" not in seen:
            seen.append(await asyncio.wait_for(msgs.get(), 60))

        await conv.close()
        proc = await _wait_terminal(optio, "kk-conv-kickoff")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_listener_relays_events_over_sse(
    shim_install_dir, task_root, mongo_db,
):
    """conversation_ui=True starts the per-task listener and registers it as
    widgetUpstream with a per-task basic-auth inner credential; the listener
    relays the live conversation's ACP events over SSE, and DONE/close teardown
    stops the listener (its port then refuses connections)."""
    optio = await _make_optio(mongo_db, "kkconv5")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-ui",
            name="Conversation UI",
            config=_conversation_config(shim_install_dir, conversation_ui=True),
        )
        assert task.ui_widget == "conversation"
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "kk-conv-ui", session_id=None, timeout=60,
        )

        # The listener registers itself right after publish_result; the process
        # doc carries widgetUpstream with the basic-auth inner credential the
        # widget proxy injects.
        proc = await _wait_widget_upstream(optio, "kk-conv-ui")
        upstream = proc["widgetUpstream"]
        url = upstream["url"]
        assert url.startswith("http://")
        inner = upstream["innerAuth"]
        assert inner is not None
        assert inner["username"] == "optio"
        assert inner["password"]
        assert proc["uiWidget"] == "conversation"

        auth = {
            "Authorization": "Basic "
            + base64.b64encode(f"optio:{inner['password']}".encode()).decode(),
        }

        # Open the SSE stream, then drive a turn and confirm the ACP events
        # reach the stream (the listener bridges the live conversation).
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{url}/events", headers=auth) as resp:
                assert resp.status == 200
                await conv.send("hello over sse")
                events = await _read_sse(resp, want_final=True)

        kinds = [
            (e.get("params") or {}).get("update", {}).get("sessionUpdate")
            for e in events
            if e.get("method") == "session/update"
        ]
        assert "agent_message_chunk" in kinds

        await conv.close()
        proc = await _wait_terminal(optio, "kk-conv-ui")
        assert proc["status"]["state"] == "done"
        await _wait_for(lambda: conv.closed)

        # Teardown stopped the listener: the port refuses connections.
        port = int(url.rsplit(":", 1)[1])
        await _wait_port_refused(port)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_forwards_frontend_parity_widget_data(
    shim_install_dir, task_root, mongo_db,
):
    """The frontend-parity four-touch: config → set_widget_data. The listener
    task publishes widgetData with camelCase keys the shared ConversationView
    reads, gated by config: the model picker sources its options from the ACP
    configOptions surface (fake_kimi advertises kimi-k2 / kimi-k2-thinking),
    config.model overrides the picker's initial value, and tool/thinking
    verbosity + file transfer bounds are mirrored through."""
    optio = await _make_optio(mongo_db, "kkconv6")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-wd",
            name="Conversation widgetData",
            config=_conversation_config(
                shim_install_dir,
                conversation_ui=True,
                show_session_controls=True,
                model="kimi-k2-thinking",
                tool_verbosity="verbose",
                thinking_verbosity="visible",
                show_file_upload=True,
                max_upload_bytes=4242,
                file_download=True,
                max_download_bytes=8484,
            ),
        )
        await optio.adhoc_define(task)
        await optio.launch_and_await_result("kk-conv-wd", session_id=None, timeout=60)

        wd = await _wait_widget_data(optio, "kk-conv-wd")
        assert wd["protocol"] == "kimicode"
        assert wd["toolVerbosity"] == "verbose"
        assert wd["thinkingVerbosity"] == "visible"
        assert wd["showSessionControls"] is True
        assert wd["showFileUpload"] is True
        assert wd["maxUploadBytes"] == 4242
        assert wd["fileDownload"] is True
        assert wd["maxDownloadBytes"] == 8484
        # widgetData advertises the generic upload route (resolved relative to
        # {widgetProxyUrl} by the client) with this task's db/prefix/pid.
        assert wd["uploadUrl"] == (
            "{widgetProxyUrl}../../../../widget-upload/"
            f"{mongo_db.name}/kkconv6/kk-conv-wd"
        )
        # The model picker is now the id="model" SessionControl; its options
        # come from the live ACP configOptions surface and config.model
        # overrides the control's initial value.
        model = next(c for c in wd["controls"] if c["id"] == "model")
        assert model["kind"] == "select" and model["value"] == "kimi-k2-thinking"
        ids = [o["value"] for o in model["options"]]
        assert ids == ["kimi-k2", "kimi-k2-thinking"]
        assert all(set(o) == {"value", "label", "disabled"} for o in model["options"])
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_widget_data_defaults_when_ungated(
    shim_install_dir, task_root, mongo_db,
):
    """With only conversation_ui on (no parity opt-ins), the parity flags carry
    their config defaults: selectors off, verbosity at the type defaults, and
    the model picker's current value is the live ACP current model."""
    optio = await _make_optio(mongo_db, "kkconv7")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-wd-def",
            name="Conversation widgetData defaults",
            config=_conversation_config(shim_install_dir, conversation_ui=True),
        )
        await optio.adhoc_define(task)
        await optio.launch_and_await_result("kk-conv-wd-def", session_id=None, timeout=60)

        wd = await _wait_widget_data(optio, "kk-conv-wd-def")
        assert wd["showSessionControls"] is False
        assert wd["nativeSpinner"] is False
        assert wd["showFileUpload"] is False
        assert wd["fileDownload"] is False
        assert wd["toolVerbosity"] == "description-only"
        assert wd["thinkingVerbosity"] == "hidden"
        # No config.model override → the model control shows the live ACP current model.
        model = next(c for c in wd["controls"] if c["id"] == "model")
        assert model["value"] == "kimi-k2"
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

    optio = await _make_optio(mongo_db, "kkconv8")
    try:
        task = create_kimicode_task(
            process_id="kk-conv-files",
            name="Conversation upload",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True,
                show_file_upload=True, on_upload=_on_upload,
            ),
        )
        await optio.adhoc_define(task)
        await optio.launch_and_await_result(
            "kk-conv-files", session_id=None, timeout=60,
        )

        wd = await _wait_widget_data(optio, "kk-conv-files")
        assert wd["showFileUpload"] is True
        assert wd["uploadUrl"] == (
            "{widgetProxyUrl}../../../../widget-upload/"
            f"{mongo_db.name}/kkconv8/kk-conv-files"
        )
        # Upload flows through the generic materialize path (NOT the listener):
        # Optio.materialize_upload resolves the registered writer, which lands
        # the bytes under uploads/ and fires on_upload with the same relpath.
        rel = await optio.materialize_upload(
            "kk-conv-files", b"hello-bytes", "note.txt",
        )
        assert rel == "uploads/note.txt"
        assert seen == ["uploads/note.txt"]
        assert landed["uploads/note.txt"] == b"hello-bytes"
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_ui_widget_per_mode():
    """A conversation task without conversation_ui carries no widget; iframe
    tasks keep 'iframe'; conversation_ui carries the 'conversation' widget."""
    conv_task = create_kimicode_task(
        process_id="kk-widget-conv",
        name="Widget conv",
        config=KimiCodeTaskConfig(
            consumer_instructions="x", mode="conversation", delivery_type="audit",
        ),
    )
    assert conv_task.ui_widget is None

    iframe_task = create_kimicode_task(
        process_id="kk-widget-iframe",
        name="Widget iframe",
        config=KimiCodeTaskConfig(consumer_instructions="x", delivery_type="audit"),
    )
    assert iframe_task.ui_widget == "iframe"


async def _read_sse(resp, want_final: bool, timeout: float = 60.0) -> list[dict]:
    """Parse SSE ``data:`` frames until an end_turn response is seen (or a few
    events land). Returns the parsed JSON objects."""
    out: list[dict] = []
    buf = b""

    async def _go() -> None:
        nonlocal buf
        while True:
            chunk = await resp.content.read(1024)
            if not chunk:
                return
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [l[5:] for l in frame.split(b"\n") if l.startswith(b"data:")]
                if not data:
                    continue
                try:
                    obj = json.loads(b"".join(data).strip())
                except ValueError:
                    continue
                out.append(obj)
                if want_final and obj.get("result", {}).get("stopReason") == "end_turn":
                    return

    await asyncio.wait_for(_go(), timeout)
    return out
