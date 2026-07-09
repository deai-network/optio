"""analyze_account unit tests — antigravity (Google Antigravity / agy).

Pure, network-free: the three read-only vendor fetchers
(``_fetch_userinfo`` / ``_fetch_load_code_assist`` / ``_fetch_quota_summary``)
are monkeypatched to return the committed real (PII-scrubbed) fixture payloads,
so the whole map-into-``AccountInfo`` path runs with zero network.

Fixtures (docs/2026-07-09-antigravity-account-research.md): identity from
Google ``oauth2/v1/userinfo`` (name/email/id); plan from Cloud Code Assist
``:loadCodeAssist`` (``currentTier.id`` → prettified); per-model usage windows
from ``:retrieveUserQuotaSummary`` (one ``UsageWindow`` per bucket).

Fail-soft contract:
  * ``loadCodeAssist``/quota failure → identity-only ``AccountInfo`` (NOT EMPTY).
  * userinfo failure (no identity) → ``EMPTY``.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

from optio_agents.account import EMPTY

from optio_antigravity import account

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _install_fetchers(monkeypatch, *, userinfo, load, quota):
    """Wire the three async vendor fetchers. Each arg is either a fixture dict
    (returned) or an Exception instance (raised) to drive a failure path."""

    def _mk(value):
        async def _f(access_token):
            if isinstance(value, Exception):
                raise value
            return value
        return _f

    monkeypatch.setattr(account, "_fetch_userinfo", _mk(userinfo))
    monkeypatch.setattr(account, "_fetch_load_code_assist", _mk(load))
    monkeypatch.setattr(account, "_fetch_quota_summary", _mk(quota))


async def test_maps_all_three_fixtures():
    # The happy path: all three reads succeed → full AccountInfo.
    import pytest

    userinfo = _load("account_userinfo.json")
    load = _load("account_loadCodeAssist.json")
    quota = _load("account_quotaSummary.json")

    monkeypatch = pytest.MonkeyPatch()
    try:
        _install_fetchers(monkeypatch, userinfo=userinfo, load=load, quota=quota)
        info = await account.analyze_account("ya29.TESTTOKEN")
    finally:
        monkeypatch.undo()

    # Identity from userinfo.
    assert info.name == "Test User"
    assert info.email == "user@example.com"
    assert info.account_id == "000000000000000000000"
    # Plan from loadCodeAssist.currentTier.id ("free-tier" → "Free"), NOT
    # currentTier.name ("Antigravity", the product label).
    assert info.plan == "Free"

    # One UsageWindow per quota bucket; all real buckets present.
    bucket_ids = {b["bucketId"] for b in quota["groups"][0]["buckets"]}
    assert len(info.windows) == len(bucket_ids)
    by_model = {w.model: w for w in info.windows}
    assert set(by_model) == bucket_ids  # model == bucketId (per-model scope)

    # Fresh free-tier seed: remainingFraction == 1 → pct == 0 for every bucket.
    w = by_model["gemini-pro-agent"]
    assert w.pct == 0.0
    assert w.model == "gemini-pro-agent"
    assert w.label == "Gemini 3.1 Pro (High)"
    # resetTime parsed to a tz-aware datetime.
    assert w.resets_at == datetime(2026, 7, 16, 8, 21, 36, tzinfo=timezone.utc)
    assert w.resets_at.tzinfo is not None

    # summary needs plan + email (both present) → non-None.
    assert info.summary is not None
    assert "Free" in info.summary
    assert "user@example.com" in info.summary

    # raw carries all three payloads.
    assert info.raw["userinfo"] == userinfo
    assert info.raw["loadCodeAssist"] == load
    assert info.raw["quotaSummary"] == quota


async def test_pct_from_partial_remaining_fraction():
    # A partially-consumed bucket → pct = (1 - remainingFraction) * 100.
    import pytest

    userinfo = _load("account_userinfo.json")
    quota = {
        "groups": [
            {
                "displayName": "All Models",
                "buckets": [
                    {
                        "bucketId": "claude-opus-4-6-thinking",
                        "displayName": "Claude Opus 4.6 (Thinking)",
                        "resetTime": "2026-07-16T08:21:36Z",
                        "remainingFraction": 0.25,
                    },
                    {
                        # Missing remainingFraction → treated as 1.0 → 0% used.
                        "bucketId": "gemini-pro-agent",
                        "displayName": "Gemini 3.1 Pro (High)",
                        "resetTime": "2026-07-16T08:21:36Z",
                    },
                ],
            }
        ]
    }
    monkeypatch = pytest.MonkeyPatch()
    try:
        _install_fetchers(monkeypatch, userinfo=userinfo, load=None, quota=quota)
        info = await account.analyze_account("ya29.TESTTOKEN")
    finally:
        monkeypatch.undo()

    by_model = {w.model: w for w in info.windows}
    assert by_model["claude-opus-4-6-thinking"].pct == 75.0
    assert by_model["gemini-pro-agent"].pct == 0.0


async def test_graceful_degrade_quota_failure_is_identity_only():
    # A quota POST that raises must NOT sink the whole analysis: identity (and
    # plan, since loadCodeAssist succeeded) survive; windows are empty; the
    # result is NOT EMPTY.
    import pytest

    userinfo = _load("account_userinfo.json")
    load = _load("account_loadCodeAssist.json")

    monkeypatch = pytest.MonkeyPatch()
    try:
        _install_fetchers(
            monkeypatch, userinfo=userinfo, load=load,
            quota=RuntimeError("quota POST 500"),
        )
        info = await account.analyze_account("ya29.TESTTOKEN")
    finally:
        monkeypatch.undo()

    assert info is not EMPTY
    assert info.name == "Test User"
    assert info.email == "user@example.com"
    assert info.account_id == "000000000000000000000"
    assert info.plan == "Free"      # loadCodeAssist still succeeded
    assert info.windows == ()       # quota failed → no windows


async def test_graceful_degrade_loadcodeassist_failure_keeps_identity():
    # loadCodeAssist raises → no plan, but identity + windows survive (NOT EMPTY).
    import pytest

    userinfo = _load("account_userinfo.json")
    quota = _load("account_quotaSummary.json")

    monkeypatch = pytest.MonkeyPatch()
    try:
        _install_fetchers(
            monkeypatch, userinfo=userinfo,
            load=RuntimeError("loadCodeAssist 500"), quota=quota,
        )
        info = await account.analyze_account("ya29.TESTTOKEN")
    finally:
        monkeypatch.undo()

    assert info is not EMPTY
    assert info.email == "user@example.com"
    assert info.plan is None
    assert len(info.windows) == len(quota["groups"][0]["buckets"])
    assert info.summary is None  # no plan → no summary


async def test_full_failsoft_userinfo_failure_is_empty():
    # Identity is the floor: if userinfo (the sole identity source) fails, there
    # is nothing to stamp → EMPTY.
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        _install_fetchers(
            monkeypatch, userinfo=RuntimeError("userinfo 401"),
            load=None, quota=None,
        )
        info = await account.analyze_account("ya29.TESTTOKEN")
    finally:
        monkeypatch.undo()

    assert info is EMPTY


async def test_userinfo_none_is_empty():
    # A fail-soft fetcher returning None (HTTP/parse error) → EMPTY.
    import pytest

    monkeypatch = pytest.MonkeyPatch()
    try:
        _install_fetchers(monkeypatch, userinfo=None, load=None, quota=None)
        info = await account.analyze_account("ya29.TESTTOKEN")
    finally:
        monkeypatch.undo()

    assert info is EMPTY


async def test_blank_token_is_empty():
    info = await account.analyze_account("")
    assert info is EMPTY


# --- resolve_capture_account (live-workdir token store) ----------------------


class _FakeHost:
    """Minimal host stub: serves bytes for one absolute path, 404s the rest."""

    def __init__(self, workdir: str, files: dict[str, bytes]):
        self.workdir = workdir
        self._files = files

    async def fetch_bytes_from_host(self, path: str) -> bytes:
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]


async def test_resolve_capture_account_reads_token_store(monkeypatch):
    # The live workdir carries agy's nested token store under
    # home/.gemini/antigravity-cli/antigravity-oauth-token; resolve extracts
    # store["token"]["access_token"] and hands it to analyze_account.
    from optio_antigravity.seed_manifest import _TOKEN_STORE_RELPATH

    workdir = "/task/wd"
    token_path = f"{workdir}/home/{_TOKEN_STORE_RELPATH}"
    store = {
        "auth_method": "consumer",
        "token": {"access_token": "ya29.LIVE", "token_type": "Bearer"},
    }
    host = _FakeHost(workdir, {token_path: json.dumps(store).encode("utf-8")})

    seen = {}

    async def fake_analyze(access_token):
        seen["token"] = access_token
        return EMPTY

    monkeypatch.setattr(account, "analyze_account", fake_analyze)
    info = await account.resolve_capture_account(host)
    assert seen["token"] == "ya29.LIVE"
    assert info is EMPTY


async def test_resolve_capture_account_missing_file_is_empty():
    host = _FakeHost("/task/wd", {})  # no token store on disk
    info = await account.resolve_capture_account(host)
    assert info is EMPTY


async def test_resolve_capture_account_no_access_token_is_empty():
    from optio_antigravity.seed_manifest import _TOKEN_STORE_RELPATH

    workdir = "/task/wd"
    token_path = f"{workdir}/home/{_TOKEN_STORE_RELPATH}"
    store = {"auth_method": "consumer", "token": {"token_type": "Bearer"}}
    host = _FakeHost(workdir, {token_path: json.dumps(store).encode("utf-8")})
    info = await account.resolve_capture_account(host)
    assert info is EMPTY
