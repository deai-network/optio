"""Best-effort Anthropic account summary for seeded Claude Code logins.

``on_seed_saved`` receives a human-readable 2nd arg like
``"Plan: Claude Max 20x for Jane Doe <jane@x.com>"``, derived from the OAuth
token the operator just saved (the /api/oauth/profile endpoint, same as the
claude-usage tool). Entirely best-effort: any failure → None.
"""

import json

import pytest

from optio_claudecode import account


# --- format_account_summary: pure formatter --------------------------------

def _profile(tier="default_claude_max_20x", full_name="Jane Doe", email="jane@x.com"):
    p = {"organization": {"rate_limit_tier": tier}, "account": {}}
    if full_name is not None:
        p["account"]["full_name"] = full_name
    if email is not None:
        p["account"]["email"] = email
    return p


def test_format_full_plan_name_email():
    s = account.format_account_summary(_profile())
    assert s == "Plan: Claude Max 20x for Jane Doe <jane@x.com>"


def test_format_strips_default_prefix_and_prettifies_tokens():
    s = account.format_account_summary(_profile(tier="default_claude_pro"))
    assert s == "Plan: Claude Pro for Jane Doe <jane@x.com>"


def test_format_missing_full_name_uses_email_only():
    s = account.format_account_summary(_profile(full_name=None))
    assert s == "Plan: Claude Max 20x for <jane@x.com>"


def test_format_none_when_no_email():
    assert account.format_account_summary(_profile(email=None)) is None


def test_format_none_when_no_plan_tier():
    p = {"organization": {}, "account": {"email": "jane@x.com"}}
    assert account.format_account_summary(p) is None


# --- resolve_account_summary: reads creds via host, fetches, formats --------

class _FakeHost:
    def __init__(self, workdir, creds_bytes):
        self.workdir = workdir
        self._creds_bytes = creds_bytes

    async def fetch_bytes_from_host(self, path):
        assert path == f"{self.workdir}/home/.claude/.credentials.json", path
        if self._creds_bytes is None:
            raise FileNotFoundError(path)
        return self._creds_bytes


async def test_resolve_reads_token_and_formats(monkeypatch):
    import urllib.request

    creds = json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}}).encode()
    host = _FakeHost("/wd", creds)

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(_profile()).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["beta"] = req.get_header("Anthropic-beta")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = await account.resolve_account_summary(host)

    assert out == "Plan: Claude Max 20x for Jane Doe <jane@x.com>"
    assert captured["url"].endswith("/api/oauth/profile")
    assert captured["auth"] == "Bearer tok-123"
    assert captured["beta"] == "oauth-2025-04-20"


async def test_resolve_none_when_no_credentials():
    host = _FakeHost("/wd", None)  # fetch raises FileNotFoundError
    assert await account.resolve_account_summary(host) is None


async def test_resolve_none_on_network_error(monkeypatch):
    import urllib.request
    from urllib.error import URLError

    creds = json.dumps({"claudeAiOauth": {"accessToken": "tok"}}).encode()
    host = _FakeHost("/wd", creds)

    def boom(req, timeout=None):
        raise URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert await account.resolve_account_summary(host) is None
