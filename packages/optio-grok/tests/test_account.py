"""Unit tests for optio_grok.account.analyze_account.

Maps the real (PII-scrubbed) grok live capture into the shared
``optio_agents.account.AccountInfo``. The HTTP fetch seam (``account._fetch``)
is monkeypatched to the committed fixture -- these tests never hit the network.

Grok is accounts-only: usage/rate-limits is unreachable with the CLI OAuth
token (POST grok.com/rest/rate-limits → 403 oauth2-auth-forbidden), so
``windows`` is always the empty tuple and ``is_limited`` is always False.
"""

from __future__ import annotations

import json
from pathlib import Path

from optio_agents.account import EMPTY, AccountInfo
from optio_grok import account
from optio_grok.account import analyze_account

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "account_capture.json").read_text()
)


def _patch_fetch(monkeypatch, mapping: dict) -> None:
    """Route account._fetch(url, token) to fixture payloads by URL."""
    async def _fake_fetch(url, token):
        return mapping.get(url)

    monkeypatch.setattr(account, "_fetch", _fake_fetch)


async def test_maps_fixture_to_account_info(monkeypatch):
    _patch_fetch(monkeypatch, {
        account._USERINFO_URL: _FIXTURE["userinfo"],
        account._V1_ME_URL: _FIXTURE["v1_me"],
        account._SUBSCRIPTIONS_URL: _FIXTURE["subscriptions"],
    })

    info = await analyze_account({"key": "TOKEN"})

    assert info.name == "Test User"
    assert info.email == "user@example.com"
    assert info.account_id == "00000000-0000-0000-0000-000000000001"  # userinfo.sub
    # Active subscription row is the SECOND one (status ACTIVE); the first is
    # INACTIVE. tier SUBSCRIPTION_TIER_GROK_PRO → "Grok Pro".
    assert info.plan == "Grok Pro"

    # Grok exposes no usage source → always empty windows.
    assert info.windows == ()

    # raw escape hatch carries all three payloads.
    assert info.raw["userinfo"]["sub"] == "00000000-0000-0000-0000-000000000001"
    assert info.raw["v1_me"]["user_id"] == "00000000-0000-0000-0000-000000000001"
    assert info.raw["subscriptions"] is _FIXTURE["subscriptions"]

    assert info.summary == "Plan: Grok Pro for Test User <user@example.com>"


def test_prettify_tier_strips_prefix_and_title_cases():
    assert account._prettify_tier("SUBSCRIPTION_TIER_GROK_PRO") == "Grok Pro"
    assert account._prettify_tier("SUBSCRIPTION_TIER_GROK_HEAVY") == "Grok Heavy"
    assert account._prettify_tier("SUBSCRIPTION_TIER_FREE") == "Free"
    assert account._prettify_tier("") is None
    assert account._prettify_tier(None) is None


async def test_no_active_subscription_plan_none(monkeypatch):
    # Only an INACTIVE row → no active plan.
    lapsed = {"subscriptions": [
        {"tier": "SUBSCRIPTION_TIER_GROK_PRO", "status": "SUBSCRIPTION_STATUS_INACTIVE"},
    ]}
    _patch_fetch(monkeypatch, {
        account._USERINFO_URL: _FIXTURE["userinfo"],
        account._V1_ME_URL: _FIXTURE["v1_me"],
        account._SUBSCRIPTIONS_URL: lapsed,
    })

    info = await analyze_account({"key": "TOKEN"})
    assert info.plan is None
    assert info.name == "Test User"      # identity still populated
    assert info.windows == ()


async def test_userinfo_failure_falls_back_to_creds(monkeypatch):
    # The userinfo GET raises → identity must still fill from creds, NOT EMPTY.
    async def _boom(url, token):
        if url == account._USERINFO_URL:
            raise RuntimeError("userinfo down")
        return None

    monkeypatch.setattr(account, "_fetch", _boom)

    creds = {
        "key": "TOKEN",
        "email": "creds@example.com",
        "first_name": "Creds Name",
        "user_id": "creds-uid-42",
    }
    info = await analyze_account(creds)

    assert info != EMPTY
    assert info.name == "Creds Name"          # creds.first_name fallback
    assert info.email == "creds@example.com"  # creds.email fallback
    assert info.account_id == "creds-uid-42"  # creds.user_id fallback
    assert info.plan is None                  # subscriptions GET returned None
    assert info.windows == ()


async def test_empty_creds_is_empty(monkeypatch):
    # Truly empty creds → EMPTY, and no network is attempted.
    async def _must_not_fetch(url, token):
        raise AssertionError("must not fetch for empty creds")

    monkeypatch.setattr(account, "_fetch", _must_not_fetch)

    assert await analyze_account({}) == EMPTY
    assert await analyze_account(None) == EMPTY
    assert isinstance(await analyze_account({}), AccountInfo)


async def test_all_http_fails_but_creds_carry_identity(monkeypatch):
    # Every GET returns None (transport failure) → degrade to creds identity,
    # never EMPTY when creds carry name/email/account_id.
    _patch_fetch(monkeypatch, {})  # every url → None

    creds = {
        "key": "TOKEN",
        "email": "creds@example.com",
        "first_name": "Creds Name",
        "user_id": "creds-uid-42",
    }
    info = await analyze_account(creds)
    assert info != EMPTY
    assert info.name == "Creds Name"
    assert info.email == "creds@example.com"
    assert info.account_id == "creds-uid-42"
