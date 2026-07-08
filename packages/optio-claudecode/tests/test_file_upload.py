"""Conversation-mode file-upload tests (generic materialize path).

Uploads no longer flow through a listener ``POST /upload`` endpoint; the widget
POSTs to the generic optio-api ``/api/widget-upload`` route, which stages the
bytes in GridFS and calls the ``materializeUpload`` clamator RPC. The engine's
only job is to (1) register a per-task upload writer via
``ctx.register_upload_writer`` and (2) publish ``widgetData.uploadUrl``.

Units here:
  * the listener no longer exposes ``/upload`` (route removed);
  * ClaudeCodeTaskConfig.show_file_upload validation (mirrors test_model_switch);
  * end-to-end: a ``show_file_upload`` conversation task registers a writer, and
    driving a fake upload through ``Optio.materialize_upload`` lands the bytes at
    ``<workdir>/uploads/<name>`` and fires ``on_upload`` with the same relpath.
"""

from __future__ import annotations

import asyncio
import base64
import pathlib
import time as _time

import aiohttp
import pytest

from optio_core.lifecycle import Optio

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task
from optio_claudecode.conversation_listener import ConversationListener


_TERMINAL = {"done", "failed", "cancelled"}


class FakeConversation:
    def __init__(self):
        self.handlers = []
        self.perm_handler = None

    def on_event(self, h):
        self.handlers.append(h)
        return lambda: self.handlers.remove(h)

    def on_permission_request(self, h):
        self.perm_handler = h
        return lambda: None

    async def send(self, text):
        pass

    async def interrupt(self):
        pass


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _cfg(**kw):
    # Mirror the model-switch tests' construction: only consumer_instructions
    # is required; fs_isolation=False keeps the config valid without a host.
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


# --- listener no longer exposes /upload -----------------------------------


async def test_listener_has_no_upload_route():
    """The old ``POST /upload`` endpoint is gone — the generic optio-api route
    now owns uploads. A POST to /upload must 404 (route not registered)."""
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")
    port = await lst.start("127.0.0.1")
    try:
        form = aiohttp.FormData()
        form.add_field("file", b"hi", filename="x.txt", content_type="text/plain")
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                f"http://127.0.0.1:{port}/upload", data=form, headers=_auth("pw"),
            )
            assert r.status == 404
    finally:
        await lst.stop()


# --- config validation -----------------------------------------------------


def test_show_file_upload_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_file_upload"):
        _cfg(
            mode="conversation",
            permission_gate=True,
            conversation_ui=False,
            show_file_upload=True,
        )


def test_show_file_upload_ok_in_conversation_ui():
    cfg = _cfg(
        mode="conversation",
        permission_gate=True,
        conversation_ui=True,
        show_file_upload=True,
    )
    assert cfg.show_file_upload is True
    assert cfg.max_upload_bytes == 10_000_000


# --- end-to-end writer registration + materialize -------------------------


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 60.0) -> dict:
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def _wait_widget_data(optio: Optio, process_id: str, timeout: float = 60.0) -> dict:
    """Poll until the task publishes widgetData (set last in the conversation-ui
    branch, after the upload writer is registered)."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetData"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetData in {timeout}s")


@pytest.mark.asyncio
async def test_upload_writer_lands_file_and_fires_on_upload(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """A show_file_upload conversation task registers an upload writer; driving
    a fake upload through Optio.materialize_upload lands the bytes at
    <workdir>/uploads/<name> and fires on_upload with that same relpath. The
    original filename (spaces preserved) survives; on_upload can read the file
    back off the host, proving it actually landed before the callback ran."""
    seen: list[str] = []
    landed: dict[str, bytes] = {}

    async def _on_upload(hook_ctx, path):
        seen.append(path)
        landed[path] = await hook_ctx.read_from_host(path)

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="ccup")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-upload",
            name="Conversation upload",
            config=ClaudeCodeTaskConfig(
                consumer_instructions="Converse with the test.",
                mode="conversation",
                conversation_ui=True,
                permission_mode="bypassPermissions",
                fs_isolation=False,
                show_file_upload=True,
                on_upload=_on_upload,
                install_dir=str(claude_cache_dir),
                ttyd_install_dir=str(shim_install_dir),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-upload", session_id=None, timeout=60,
        )
        # Wait until the conversation-ui branch has registered the writer +
        # published widgetData (both happen after publish_result).
        proc = await _wait_widget_data(optio, "cc-conv-upload")
        assert proc["widgetData"]["uploadUrl"] == (
            "{widgetProxyUrl}../../../../widget-upload/"
            f"{mongo_db.name}/ccup/cc-conv-upload"
        )

        rel = await optio.materialize_upload(
            "cc-conv-upload", b"hello upload", "My Notes.md",
        )
        assert rel == "uploads/My Notes.md"
        assert seen == ["uploads/My Notes.md"]
        assert landed["uploads/My Notes.md"] == b"hello upload"

        await conv.close()
        term = await _wait_terminal(optio, "cc-conv-upload")
        assert term["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)
