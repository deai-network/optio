"""opencode account meta-analyzer: per-provider dispatch + placeholder + reuse.

The three OAuth reuse handlers call the extracted vendor map helpers
(``optio_claudecode.account.account_from_oauth_token`` etc). Tests monkeypatch
those helpers *where the provider modules import them* — never the real
vendor network — so every case is hermetic.
"""

import json
import pathlib

import pytest

from optio_agents.account import EMPTY, AccountInfo
from optio_opencode import account as acct
from optio_opencode.providers import anthropic, openai, xai

FIX = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def patched_vendors(monkeypatch):
    """Patch the three vendor map helpers to return distinct known accounts."""

    async def _anthropic(access_token):
        assert access_token == "anthropic-access-tok"
        return AccountInfo(
            plan="Claude Max", email="claude@x.com", account_id="anthropic-1",
            raw={"provider": "anthropic"},
        )

    async def _openai(access_token, account_id):
        assert access_token == "openai-access-tok"
        return AccountInfo(
            plan="ChatGPT Pro", account_id=account_id, raw={"provider": "openai"},
        )

    async def _xai(access_token):
        assert access_token == "xai-access-tok"
        return AccountInfo(
            plan="xAI Team", account_id="xai-1", raw={"provider": "xai"},
        )

    monkeypatch.setattr(anthropic, "account_from_oauth_token", _anthropic)
    monkeypatch.setattr(openai, "account_from_openai", _openai)
    monkeypatch.setattr(xai, "account_from_xai", _xai)


async def test_analyze_accounts_dispatches_and_placeholders(patched_vendors):
    auth = json.loads((FIX / "opencode_auth.json").read_text())
    infos = await acct.analyze_accounts(auth)

    # Every configured provider yields exactly one AccountInfo — nothing dropped.
    assert len(infos) == 5
    by_provider = {i.raw.get("provider"): i for i in infos}
    assert set(by_provider) == {"openai", "xai", "anthropic", "groq", "unknownprov"}

    # Analyzed ones carry the vendor identity.
    assert by_provider["anthropic"].plan == "Claude Max"
    assert by_provider["openai"].plan == "ChatGPT Pro"
    assert by_provider["xai"].plan == "xAI Team"
    # openai's accountId flows from the auth entry into the vendor call.
    assert by_provider["openai"].account_id == "openai-acct-1"

    # groq (type=api → the oauth-only handler declines) and unknownprov (no
    # handler registered) are placeholders.
    for pid in ("groq", "unknownprov"):
        ph = by_provider[pid]
        assert ph.raw.get("unanalyzed") is True
        assert ph.summary.endswith("· unknown account")
        assert pid in ph.summary
    # placeholder carries the accountId when the entry has one.
    assert by_provider["unknownprov"].account_id == "unknown-acct-9"


async def test_handler_raising_yields_placeholder_others_unaffected(monkeypatch):
    auth = json.loads((FIX / "opencode_auth.json").read_text())

    async def _boom(access_token):
        raise RuntimeError("vendor exploded")

    async def _openai(access_token, account_id):
        return AccountInfo(plan="ChatGPT Pro", raw={"provider": "openai"})

    async def _xai(access_token):
        return AccountInfo(plan="xAI Team", raw={"provider": "xai"})

    monkeypatch.setattr(anthropic, "account_from_oauth_token", _boom)
    monkeypatch.setattr(openai, "account_from_openai", _openai)
    monkeypatch.setattr(xai, "account_from_xai", _xai)

    infos = await acct.analyze_accounts(auth)
    assert len(infos) == 5
    by_provider = {i.raw.get("provider"): i for i in infos}
    # The raising provider degrades to a placeholder.
    assert by_provider["anthropic"].raw.get("unanalyzed") is True
    assert by_provider["anthropic"].summary.endswith("· unknown account")
    # The others are untouched.
    assert by_provider["openai"].plan == "ChatGPT Pro"
    assert by_provider["xai"].plan == "xAI Team"


async def test_empty_vendor_result_becomes_placeholder(monkeypatch):
    auth = {"anthropic": {"type": "oauth", "access": "tok"}}

    async def _empty(access_token):
        return EMPTY

    monkeypatch.setattr(anthropic, "account_from_oauth_token", _empty)
    infos = await acct.analyze_accounts(auth)
    assert len(infos) == 1
    assert infos[0].raw.get("unanalyzed") is True
    assert infos[0].summary.endswith("· unknown account")


async def test_missing_access_token_declines_without_network():
    # An oauth entry with no access token (e.g. only a refresh) must not reach
    # the vendor network — it declines to a placeholder.
    auth = {"xai": {"type": "oauth", "refresh": "only-refresh"}}
    infos = await acct.analyze_accounts(auth)
    assert len(infos) == 1
    assert infos[0].raw.get("unanalyzed") is True


async def test_non_dict_entry_skipped_empty_and_non_dict_auth():
    # A non-dict entry is skipped (not even a placeholder); the sibling stays.
    infos = await acct.analyze_accounts(
        {"openai": "not-a-dict", "unknownprov": {"type": "oauth"}}
    )
    assert len(infos) == 1
    assert infos[0].raw.get("provider") == "unknownprov"

    # Empty / non-dict auth → [].
    assert await acct.analyze_accounts({}) == []
    assert await acct.analyze_accounts(None) == []
    assert await acct.analyze_accounts("garbage") == []


class _FakeHost:
    def __init__(self, workdir, data):
        self.workdir = workdir
        self._data = data

    async def fetch_bytes_from_host(self, path):
        assert path.endswith("home/.local/share/opencode/auth.json")
        if self._data is None:
            raise FileNotFoundError(path)
        return self._data


async def test_resolve_capture_accounts_reads_live_auth(patched_vendors):
    auth = (FIX / "opencode_auth.json").read_bytes()
    host = _FakeHost("/wd/", auth)
    infos = await acct.resolve_capture_accounts(host)
    assert len(infos) == 5


async def test_resolve_capture_accounts_missing_file_is_empty():
    host = _FakeHost("/wd/", None)
    assert await acct.resolve_capture_accounts(host) == []
