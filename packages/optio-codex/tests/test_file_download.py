"""Conversation-mode file-download tests (Stage 7).

File-disjoint units that don't need a live codex:
  * the listener's GET /download endpoint (real HTTP, fake download_reader);
  * CodexTaskConfig.file_download validation;
  * compose_agents_md injecting the optio-file: downloadables block.

The workdir-confinement guard itself lives in the session's _read_download; the
listener maps its ValueError("forbidden")/("too-large") to 403/413.
"""

import base64

import aiohttp
import pytest

from optio_codex.conversation_listener import ConversationListener
from optio_codex.prompt import compose_agents_md
from optio_codex.types import CodexTaskConfig


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
    base = dict(consumer_instructions="do things", delivery_type="audit")
    base.update(kw)
    return CodexTaskConfig(**base)


def _make_reader(result=None, exc=None):
    calls: list[str] = []

    async def reader(relpath: str) -> tuple[bytes, str]:
        calls.append(relpath)
        if exc is not None:
            raise exc
        return result

    return calls, reader


async def _serve(reader=None, max_download_bytes=10_000_000):
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
            r = await s.get(f"{url}/download", params={"path": "out/r.md"}, headers=_auth("pw"))
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
            assert (await r.json()) == {"ok": False, "reason": "bad-path"}
    finally:
        await lst.stop()


async def test_download_no_reader_returns_409():
    lst, url = await _serve(reader=None)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{url}/download", params={"path": "out/r.md"}, headers=_auth("pw"))
            assert r.status == 409
            assert (await r.json()) == {"ok": False, "reason": "no-reader"}
    finally:
        await lst.stop()


async def test_download_not_found_returns_404():
    _calls, reader = _make_reader(exc=FileNotFoundError())
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{url}/download", params={"path": "out/gone.md"}, headers=_auth("pw"))
            assert r.status == 404
            assert (await r.json()) == {"ok": False, "reason": "not-found"}
    finally:
        await lst.stop()


async def test_download_forbidden_returns_403():
    _calls, reader = _make_reader(exc=ValueError("forbidden"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{url}/download", params={"path": "../etc/passwd"}, headers=_auth("pw"))
            assert r.status == 403
            assert (await r.json()) == {"ok": False, "reason": "forbidden"}
    finally:
        await lst.stop()


async def test_download_too_large_returns_413():
    _calls, reader = _make_reader(exc=ValueError("too-large"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{url}/download", params={"path": "out/big.bin"}, headers=_auth("pw"))
            assert r.status == 413
            assert (await r.json()) == {"ok": False, "reason": "too-large"}
    finally:
        await lst.stop()


async def test_download_unauthorized_returns_401():
    _calls, reader = _make_reader(result=(b"x", "text/plain"))
    lst, url = await _serve(reader)
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{url}/download", params={"path": "out/r.md"}, headers=_auth("WRONG"))
            assert r.status == 401
    finally:
        await lst.stop()


# --- config validation -----------------------------------------------------


def test_file_download_requires_conversation_ui():
    with pytest.raises(ValueError, match="file_download"):
        _cfg(mode="conversation", conversation_ui=False, file_download=True)


def test_file_download_ok_in_conversation_ui():
    cfg = _cfg(mode="conversation", conversation_ui=True, file_download=True)
    assert cfg.file_download is True
    assert cfg.max_download_bytes == 10_000_000


# --- prompt injection ------------------------------------------------------


def test_compose_injects_downloadables_when_file_download():
    body = compose_agents_md("Do the thing.", file_download=True, host_protocol=False)
    assert "optio-file:" in body
    assert "System:" in body        # host_protocol=False explainer intact


def test_compose_omits_downloadables_by_default():
    body = compose_agents_md("Do the thing.", host_protocol=False)
    assert "optio-file:" not in body


def test_compose_downloadables_comparative_with_host_protocol():
    # With the keyword protocol active the block contrasts DELIVERABLE vs
    # downloadable (comparative wording from the optio-agents SSOT).
    body = compose_agents_md("Do the thing.", file_download=True, host_protocol=True)
    assert "optio-file:" in body
    assert "DELIVERABLE" in body
