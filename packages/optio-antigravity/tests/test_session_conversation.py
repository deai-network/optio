"""End-to-end conversation-mode session tests (local host, fake ``agy``).

Each test bootstraps a real optio engine (Mongo via Docker), defines the task
via ``adhoc_define``, and obtains the live ``AntigravityConversation`` through
``launch_and_await_result``. The shim fixtures point the session at
``agy-shim.sh`` → ``fake_agy.py``, whose ``-p`` print mode appends a canned turn
in the real ``agy`` layout under ``~/.gemini/antigravity-cli/brain/<uuid>/…``
(no tmux/ttyd runs in this mode).

Antigravity has no live transport, so a conversation is synthetic: each
``conv.send`` awaits one whole ``agy -p`` turn under a PTY, then emits one
coalesced ``on_message`` (design §1/§5). Mirrors optio-grok's
test_session_conversation, adapted to the transcript-driven driver + the
claudecode conversation_ui lifecycle assertions.
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

from optio_antigravity import AntigravityTaskConfig, create_antigravity_task


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


async def _wait_widget_upstream(optio: Optio, process_id: str, timeout: float = 10.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetUpstream"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetUpstream in {timeout}s")


async def _wait_widget_data(optio: Optio, process_id: str, timeout: float = 10.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetData"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetData in {timeout}s")


async def _read_until(resp, predicate, timeout: float = 10.0) -> dict:
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


def _conversation_config(shim_install_dir: pathlib.Path, **kw) -> AntigravityTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        agy_install_dir=str(shim_install_dir),
        ttyd_install_dir=str(shim_install_dir),
        auto_start=False,
        supports_resume=False,
        fs_isolation=False,
    )
    base.update(kw)
    return AntigravityTaskConfig(**base)


@pytest.mark.asyncio
async def test_publish_send_receive(shim_install_dir, task_root, mongo_db):
    """launch_and_await_result hands out the live conversation; one full
    send → coalesced-answer turn works and the task ends cleanly on close."""
    optio = await _make_optio(mongo_db, "agconv1")
    try:
        task = create_antigravity_task(
            process_id="ag-conv-roundtrip",
            name="Conversation roundtrip",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "ag-conv-roundtrip", session_id=None, timeout=60,
        )
        assert optio.get_published_result("ag-conv-roundtrip") is conv

        msgs: asyncio.Queue = asyncio.Queue()
        conv.on_message(msgs.put_nowait)
        assert not conv.is_pending()
        # The fake replies with the text after "say " → one coalesced answer.
        await conv.send("say hello")
        msg = await asyncio.wait_for(msgs.get(), 10)
        assert msg.text.strip() == "hello"
        assert not conv.is_pending()

        await conv.close()
        proc = await _wait_terminal(optio, "ag-conv-roundtrip")
        assert proc["status"]["state"] == "done"
        assert conv.closed
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_second_turn_resumes_conversation_id(shim_install_dir, task_root, mongo_db):
    """Turn 1 mints a conversation id; turn 2 resumes it via --conversation."""
    optio = await _make_optio(mongo_db, "agconv2")
    try:
        task = create_antigravity_task(
            process_id="ag-conv-resume-id",
            name="Conversation id",
            config=_conversation_config(shim_install_dir),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "ag-conv-resume-id", session_id=None, timeout=60,
        )
        await conv.send("first")
        cid = conv.conversation_id
        assert cid
        await conv.send("second")
        assert conv.last_argv_contains(f"--conversation {cid}")

        await conv.close()
        proc = await _wait_terminal(optio, "ag-conv-resume-id")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_host_protocol_false_body_returns(shim_install_dir, task_root, mongo_db):
    """With host_protocol=False the keyword driver is off, so a caller close
    ends the task through the body's normal return (no DONE echo needed)."""
    optio = await _make_optio(mongo_db, "agconv3")
    try:
        task = create_antigravity_task(
            process_id="ag-conv-noproto",
            name="Conversation no-proto",
            config=_conversation_config(shim_install_dir, host_protocol=False),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "ag-conv-noproto", session_id=None, timeout=60,
        )
        await conv.send("say ok")
        await conv.close()
        proc = await _wait_terminal(optio, "ag-conv-noproto")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_session_lifecycle(shim_install_dir, task_root, mongo_db):
    """conversation_ui=True end to end: widgetUpstream + innerAuth registered,
    widgetData primed with the model session-control (from `agy models`),
    uiWidget set, the listener replays the turn's assistant event over SSE, and
    it stops with the task."""
    optio = await _make_optio(mongo_db, "agconv4")
    try:
        task = create_antigravity_task(
            process_id="ag-conv-ui",
            name="Conversation UI",
            config=_conversation_config(shim_install_dir, conversation_ui=True),
        )
        assert task.ui_widget == "conversation"
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "ag-conv-ui", session_id=None, timeout=60,
        )

        # The listener registers itself right after publish_result.
        proc = await _wait_widget_upstream(optio, "ag-conv-ui")
        upstream = proc["widgetUpstream"]
        assert upstream["url"].startswith("http://")
        inner = upstream["innerAuth"]
        assert inner is not None
        assert inner["username"] == "optio"
        assert inner["password"]
        assert proc["widgetData"] == {
            "protocol": "antigravity",
            "toolVerbosity": "description-only",
            "thinkingVerbosity": "hidden",
            "showSessionControls": False,
            "nativeSpinner": False,
            "controls": [{
                "id": "model",
                "kind": "select",
                "label": "Model",
                "value": "gemini-2.5-pro",
                "category": "model",
                "disabled": False,
                "options": [
                    {"value": "gemini-2.5-pro", "label": "gemini-2.5-pro", "disabled": False},
                    {"value": "gemini-2.5-flash", "label": "gemini-2.5-flash", "disabled": False},
                    {"value": "claude-sonnet-4", "label": "claude-sonnet-4", "disabled": False},
                    {"value": "gpt-oss-120b", "label": "gpt-oss-120b", "disabled": False},
                ],
            }],
            "showFileUpload": False,
            "maxUploadBytes": 10_000_000,
            "fileDownload": False,
            "maxDownloadBytes": 10_000_000,
            "uploadUrl": (
                "{widgetProxyUrl}../../../../widget-upload/"
                f"{mongo_db.name}/agconv4/ag-conv-ui"
            ),
        }
        assert proc["uiWidget"] == "conversation"

        # Drive one turn, then hit the listener directly (authenticating with the
        # inner credential the widget proxy would inject); the replay buffer must
        # carry the turn's raw PLANNER_RESPONSE transcript line (real schema).
        await conv.send("say hi")
        token = base64.b64encode(f"optio:{inner['password']}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{upstream['url']}/events", headers=headers) as resp:
                assert resp.status == 200
                answer = await _read_until(
                    resp,
                    lambda e: e.get("type") == "PLANNER_RESPONSE" and e.get("content"),
                )
                assert answer["content"].strip() == "hi"

        await conv.close()
        proc = await _wait_terminal(optio, "ag-conv-ui")
        assert proc["status"]["state"] == "done"

        port = int(upstream["url"].rsplit(":", 1)[1])
        await _wait_port_refused(port)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_conversation_ui_file_upload_download(shim_install_dir, task_root, mongo_db):
    """conversation_ui migrates file upload to the generic materialize path: the
    session registers an in-process upload writer (resolved by
    ``Optio.materialize_upload``) that lands the bytes under ``<workdir>/uploads``
    and fires ``on_upload`` with that relpath; widgetData advertises the generic
    ``uploadUrl``. file_download still serves the just-uploaded file through the
    workdir-confined listener reader; a ``../`` escape is refused with 403."""
    seen: list[str] = []
    landed: dict[str, bytes] = {}

    async def _on_upload(hook_ctx, path):
        seen.append(path)
        landed[path] = await hook_ctx.read_from_host(path)

    optio = await _make_optio(mongo_db, "agconv5")
    try:
        task = create_antigravity_task(
            process_id="ag-conv-files",
            name="Conversation files",
            config=_conversation_config(
                shim_install_dir, conversation_ui=True,
                show_file_upload=True, file_download=True,
                on_upload=_on_upload,
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "ag-conv-files", session_id=None, timeout=60,
        )
        proc = await _wait_widget_data(optio, "ag-conv-files")
        upstream = proc["widgetUpstream"]
        # widgetData advertises the file features + the generic upload route.
        assert proc["widgetData"]["showFileUpload"] is True
        assert proc["widgetData"]["fileDownload"] is True
        assert proc["widgetData"]["uploadUrl"] == (
            "{widgetProxyUrl}../../../../widget-upload/"
            f"{mongo_db.name}/agconv5/ag-conv-files"
        )
        # Upload flows through the generic materialize path (NOT the listener):
        # Optio.materialize_upload resolves the registered writer, which lands
        # the bytes under uploads/ and fires on_upload with the same relpath.
        rel = await optio.materialize_upload(
            "ag-conv-files", b"hello-bytes", "note.txt",
        )
        assert rel == "uploads/note.txt"
        assert seen == ["uploads/note.txt"]
        assert landed["uploads/note.txt"] == b"hello-bytes"

        inner = upstream["innerAuth"]
        token = base64.b64encode(f"optio:{inner['password']}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        async with aiohttp.ClientSession() as session:
            # Download the just-uploaded file back through the confined reader.
            async with session.get(
                f"{upstream['url']}/download", params={"path": rel}, headers=headers,
            ) as r:
                assert r.status == 200
                data = await r.read()
            assert data == b"hello-bytes"
            # A workdir escape is refused.
            async with session.get(
                f"{upstream['url']}/download",
                params={"path": "../../etc/passwd"}, headers=headers,
            ) as r:
                assert r.status == 403

        await conv.close()
        proc = await _wait_terminal(optio, "ag-conv-files")
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)


def test_ui_widget_per_mode():
    """Conversation tasks carry no widget unless conversation_ui; iframe tasks
    keep 'iframe'."""
    conv_task = create_antigravity_task(
        process_id="ag-widget-conv",
        name="Widget conv",
        config=AntigravityTaskConfig(consumer_instructions="x", mode="conversation"),
    )
    assert conv_task.ui_widget is None

    ui_task = create_antigravity_task(
        process_id="ag-widget-conv-ui",
        name="Widget conv ui",
        config=AntigravityTaskConfig(
            consumer_instructions="x", mode="conversation", conversation_ui=True,
        ),
    )
    assert ui_task.ui_widget == "conversation"

    iframe_task = create_antigravity_task(
        process_id="ag-widget-iframe",
        name="Widget iframe",
        config=AntigravityTaskConfig(consumer_instructions="x"),
    )
    assert iframe_task.ui_widget == "iframe-input"


@pytest.mark.asyncio
async def test_conversation_supports_resume_captures_snapshot(
    shim_install_dir, task_root, mongo_db,
):
    """A conversation-mode task with supports_resume captures a snapshot on
    teardown — its reached-live gate is a PUBLISHED conversation, not a ttyd
    launched_handle (conversation mode has no persistent process) — so the
    dashboard can mark it resumable (hasSavedState)."""
    from optio_antigravity.snapshots import load_latest_snapshot
    optio = await _make_optio(mongo_db, "agconvsnap")
    try:
        task = create_antigravity_task(
            process_id="ag-conv-snap",
            name="Conversation snapshot",
            config=_conversation_config(shim_install_dir, supports_resume=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "ag-conv-snap", session_id=None, timeout=60,
        )
        await conv.send("say hi")   # a turn writes the transcript into the workdir
        await conv.close()
        proc = await _wait_terminal(optio, "ag-conv-snap")
        assert proc["status"]["state"] == "done"
        # Reached-live via the published conversation → a snapshot was captured.
        snap = await load_latest_snapshot(mongo_db, "agconvsnap", "ag-conv-snap")
        assert snap is not None, "conversation-mode session captured no snapshot"
        fresh = await mongo_db["agconvsnap_processes"].find_one({"processId": "ag-conv-snap"})
        assert fresh["hasSavedState"] is True
    finally:
        await optio.shutdown(grace_seconds=1.0)
