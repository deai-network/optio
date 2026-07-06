"""Conversation-mode file-upload tests (Stage 7 Task 7.2).

Two file-disjoint units that don't need a live ``agy``:
  * the listener's POST /upload endpoint, driven over real HTTP with a fake
    upload_writer (no Host) — mirrors test_conversation_listener's harness;
  * AntigravityTaskConfig.show_file_upload validation.

The end-to-end (bytes landing in <workdir>/uploads via the session-wired
upload_writer) is covered by test_session_conversation's
test_conversation_ui_file_upload_download. Mirrors optio-grok's
test_file_upload (grok ← agy renames).
"""

import base64

import aiohttp
import pytest

from optio_antigravity.conversation_listener import ConversationListener
from optio_antigravity.types import AntigravityTaskConfig


class FakeConversation:
    def on_event(self, h):
        return lambda: None

    def on_permission_request(self, h):
        return lambda: None

    async def send(self, text):
        pass

    async def interrupt(self):
        pass


def _auth(pw):
    token = base64.b64encode(f"optio:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _cfg(**kw):
    base = dict(consumer_instructions="do things")
    base.update(kw)
    return AntigravityTaskConfig(**base)


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
    form.add_field("file", b"hello", filename="note.txt", content_type="text/plain")
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{url}/upload", data=form, headers=_auth("pw"))
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["files"] == [{"filename": "note.txt", "path": "uploads/note.txt"}]
    assert calls == [("note.txt", b"hello")]


async def test_upload_too_large_returns_413(upload_listener):
    _calls, url = upload_listener
    form = aiohttp.FormData()
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
        form.add_field("file", b"hi", filename="x.txt", content_type="text/plain")
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
        _cfg(mode="conversation", conversation_ui=False, show_file_upload=True)


def test_show_file_upload_ok_in_conversation_ui():
    cfg = _cfg(mode="conversation", conversation_ui=True, show_file_upload=True)
    assert cfg.show_file_upload is True
    assert cfg.max_upload_bytes == 10_000_000
