"""Conversation-mode file-download tests (Phase-3 pinned interfaces).

Three file-disjoint units that don't need a live claude:
  * the listener's GET /download endpoint, driven over real HTTP with a fake
    download_reader (no Host) — mirrors test_file_upload's harness;
  * ClaudeCodeTaskConfig.file_download validation (mirrors test_model_switch);
  * compose_agents_md injecting the downloadables block when file_download.

The end-to-end (bytes fetched from the workdir, the link rendering as a
download control) is verified manually — see plan Task V5.
"""

import base64

import aiohttp
import pytest

from optio_claudecode.conversation_listener import ConversationListener
from optio_claudecode.prompt import compose_agents_md
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
    # Mirror the file-upload tests' construction: only consumer_instructions
    # is required; fs_isolation=False keeps the config valid without a host.
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


# --- listener GET /download ------------------------------------------------


def _make_reader(result=None, exc=None):
    """A fake download_reader: relpath -> (bytes, mime). Records the calls."""
    calls: list[str] = []

    async def reader(relpath: str) -> tuple[bytes, str]:
        calls.append(relpath)
        if exc is not None:
            raise exc
        return result

    return calls, reader


async def _serve(reader=None, max_download_bytes=10_000_000):
    """Start a listener with the given (optional) reader; caller stops it."""
    conv = FakeConversation()
    kw = {}
    if reader is not None:
        kw["download_reader"] = reader
        kw["max_download_bytes"] = max_download_bytes
    lst = ConversationListener(conv, password="pw", **kw)
    port = await lst.start("127.0.0.1")
    return lst, f"http://127.0.0.1:{port}"


async def test_download_returns_bytes_and_disposition():
    calls, reader = _make_reader(result=(b"report-body", "text/markdown"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{url}/download", params={"path": "out/r.md"}, headers=_auth("pw")
            )
            assert r.status == 200
            assert r.headers["Content-Type"] == "text/markdown"
            assert r.headers["Content-Disposition"] == 'attachment; filename="r.md"'
            body = await r.read()
        assert body == b"report-body"
        assert calls == ["out/r.md"]
    finally:
        await lst.stop()


async def test_download_missing_path_returns_400():
    _calls, reader = _make_reader(result=(b"x", "text/plain"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{url}/download", headers=_auth("pw"))
            assert r.status == 400
            body = await r.json()
        assert body == {"ok": False, "reason": "bad-path"}
    finally:
        await lst.stop()


async def test_download_no_reader_returns_409():
    lst, url = await _serve(reader=None)  # no download_reader
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{url}/download", params={"path": "out/r.md"}, headers=_auth("pw")
            )
            assert r.status == 409
            body = await r.json()
        assert body == {"ok": False, "reason": "no-reader"}
    finally:
        await lst.stop()


async def test_download_not_found_returns_404():
    _calls, reader = _make_reader(exc=FileNotFoundError())
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{url}/download", params={"path": "out/gone.md"}, headers=_auth("pw")
            )
            assert r.status == 404
            body = await r.json()
        assert body == {"ok": False, "reason": "not-found"}
    finally:
        await lst.stop()


async def test_download_forbidden_returns_403():
    _calls, reader = _make_reader(exc=ValueError("forbidden"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{url}/download", params={"path": "../etc/passwd"}, headers=_auth("pw")
            )
            assert r.status == 403
            body = await r.json()
        assert body == {"ok": False, "reason": "forbidden"}
    finally:
        await lst.stop()


async def test_download_too_large_returns_413():
    _calls, reader = _make_reader(exc=ValueError("too-large"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{url}/download", params={"path": "out/big.bin"}, headers=_auth("pw")
            )
            assert r.status == 413
            body = await r.json()
        assert body == {"ok": False, "reason": "too-large"}
    finally:
        await lst.stop()


async def test_download_unauthorized_returns_401():
    _calls, reader = _make_reader(result=(b"x", "text/plain"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(
                f"{url}/download", params={"path": "out/r.md"}, headers=_auth("WRONG")
            )
            assert r.status == 401
    finally:
        await lst.stop()


# --- config validation -----------------------------------------------------


def test_file_download_requires_conversation_ui():
    with pytest.raises(ValueError, match="file_download"):
        _cfg(
            mode="conversation",
            permission_gate=True,
            conversation_ui=False,
            file_download=True,
        )


def test_file_download_ok_in_conversation_ui():
    cfg = _cfg(
        mode="conversation",
        permission_gate=True,
        conversation_ui=True,
        file_download=True,
    )
    assert cfg.file_download is True
    assert cfg.max_download_bytes == 10_000_000


# --- prompt injection ------------------------------------------------------


def test_compose_injects_downloadables_when_file_download():
    body = compose_agents_md(
        "Do the thing.", file_download=True, host_protocol=False
    )
    assert "optio-file:" in body


def test_compose_omits_downloadables_by_default():
    body = compose_agents_md("Do the thing.", host_protocol=False)
    assert "optio-file:" not in body
