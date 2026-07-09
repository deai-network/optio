"""Capture-time account resolution.

``resolve_capture_account(host)`` reads the seeded OAuth token from the isolated
HOME's ``.claude/.credentials.json`` and returns a normalized
``optio_agents.account.AccountInfo`` (fail-soft to ``EMPTY``). The session
finally-block stamps ``metadata.account = info.to_dict()`` from it and hands
``info.summary`` to ``on_seed_saved`` — the full session wiring is covered in
``test_session_seed_capture``; here we unit-test the resolver in isolation
(no network: ``analyze_account`` is monkeypatched)."""

import json

from optio_agents.account import EMPTY, AccountInfo

from optio_claudecode import account


class _FakeHost:
    def __init__(self, workdir, creds_bytes):
        self.workdir = workdir
        self._creds_bytes = creds_bytes

    async def fetch_bytes_from_host(self, path):
        assert path == f"{self.workdir}/home/.claude/.credentials.json", path
        if self._creds_bytes is None:
            raise FileNotFoundError(path)
        return self._creds_bytes


async def test_resolve_reads_token_and_analyzes(monkeypatch):
    creds = json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}}).encode()
    host = _FakeHost("/wd", creds)

    seen = {}

    async def fake_analyze(token):
        seen["token"] = token
        return AccountInfo(name="Jane Doe", email="jane@x.com", plan="Claude Max 20x")

    monkeypatch.setattr(account, "analyze_account", fake_analyze)
    info = await account.resolve_capture_account(host)

    assert seen["token"] == "tok-123"
    assert isinstance(info, AccountInfo)
    assert info.email == "jane@x.com"
    assert info.summary == "Plan: Claude Max 20x for Jane Doe <jane@x.com>"


async def test_resolve_empty_when_no_credentials():
    host = _FakeHost("/wd", None)  # fetch raises FileNotFoundError
    assert await account.resolve_capture_account(host) == EMPTY


async def test_resolve_empty_when_no_token(monkeypatch):
    creds = json.dumps({"claudeAiOauth": {}}).encode()  # no accessToken
    host = _FakeHost("/wd", creds)

    called = False

    async def fake_analyze(token):
        nonlocal called
        called = True
        return AccountInfo(email="x@y.com", plan="P")

    monkeypatch.setattr(account, "analyze_account", fake_analyze)
    assert await account.resolve_capture_account(host) == EMPTY
    assert called is False  # no token -> analyze never invoked
