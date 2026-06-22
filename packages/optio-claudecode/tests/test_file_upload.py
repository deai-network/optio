"""Conversation-mode file-upload tests (Phase-3 pinned interfaces).

Two file-disjoint units that don't need a live claude:
  * the listener's POST /upload endpoint, driven over real HTTP with a fake
    upload_writer (no Host) — mirrors test_conversation_listener's harness;
  * ClaudeCodeTaskConfig.show_file_upload validation (mirrors test_model_switch).

The end-to-end (bytes landing in the workdir, the agent Read-ing them) is
verified manually — see plan Task V4.
"""

import base64

import aiohttp
import pytest

from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.types import ClaudeCodeTaskConfig


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


# --- listener POST /upload -------------------------------------------------


@pytest.fixture
async def upload_listener():
    conv = FakeConversation()
    calls: list[tuple[str, bytes]] = []

    async def writer(name: str, data: bytes) -> str:
        calls.append((name, data))
        return f"uploads/{name}"

    lst = ConversationListener(
        conv, password="pw", upload_writer=writer, max_upload_bytes=16
    )
    port = await lst.start("127.0.0.1")
    yield calls, f"http://127.0.0.1:{port}"
    await lst.stop()


async def test_upload_calls_writer_and_returns_path(upload_listener):
    calls, url = upload_listener
    form = aiohttp.FormData()
    form.add_field("file", b"hello", filename="note.txt",
                   content_type="text/plain")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("pw"))
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["files"] == [{"filename": "note.txt", "path": "uploads/note.txt"}]
    assert calls == [("note.txt", b"hello")]


async def test_upload_multiple_files(upload_listener):
    calls, url = upload_listener
    form = aiohttp.FormData()
    form.add_field("file", b"a", filename="a.txt", content_type="text/plain")
    form.add_field("file", b"bb", filename="b.txt", content_type="text/plain")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("pw"))
        assert r.status == 200
        body = await r.json()
    assert [f["filename"] for f in body["files"]] == ["a.txt", "b.txt"]
    assert calls == [("a.txt", b"a"), ("b.txt", b"bb")]


async def test_upload_too_large_returns_413(upload_listener):
    calls, url = upload_listener
    form = aiohttp.FormData()
    # cap is 16 bytes (fixture); exceed it.
    form.add_field("file", b"x" * 100, filename="big.bin",
                   content_type="application/octet-stream")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("pw"))
        assert r.status == 413
        body = await r.json()
    assert body == {"ok": False, "reason": "too-large"}


async def test_upload_unauthorized_returns_401(upload_listener):
    _calls, url = upload_listener
    form = aiohttp.FormData()
    form.add_field("file", b"hi", filename="x.txt", content_type="text/plain")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("WRONG"))
        assert r.status == 401


async def test_upload_no_writer_returns_409():
    conv = FakeConversation()
    lst = ConversationListener(conv, password="pw")  # no upload_writer
    port = await lst.start("127.0.0.1")
    try:
        form = aiohttp.FormData()
        form.add_field("file", b"hi", filename="x.txt",
                       content_type="text/plain")
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                f"http://127.0.0.1:{port}/upload", data=form, headers=_auth("pw")
            )
            assert r.status == 409
            body = await r.json()
        assert body == {"ok": False, "reason": "no-writer"}
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
