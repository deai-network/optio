"""Unit tests for optio_cursor.account.analyze_account.

Maps the real (PII-scrubbed) cursor dashboard capture into the shared
``optio_agents.account.AccountInfo``. The HTTP fetch seam (``account._fetch``)
is monkeypatched to the committed fixture -- these tests never hit the network.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from optio_agents.account import EMPTY, AccountInfo
from optio_cursor import account
from optio_cursor.account import analyze_account

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "account_capture.json").read_text()
)


def _make_token(sub: str = "google-oauth2|user_01TESTTESTTESTTESTTESTTEST") -> str:
    """A minimal HS256-shaped JWT whose middle segment decodes to ``{"sub":…}``.
    Signature segment is irrelevant (the analyzer decodes, never verifies)."""
    def _seg(obj: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")  # base64url, no padding (as real JWTs)

    return f"{_seg({'alg': 'HS256'})}.{_seg({'sub': sub})}.sig"


def _patch_fetch(monkeypatch, mapping: dict) -> None:
    """Route account._fetch(url, cookie) to the fixture payloads by URL."""
    async def _fake_fetch(url, cookie):
        return mapping.get(url)

    monkeypatch.setattr(account, "_fetch", _fake_fetch)


async def test_maps_fixture_to_account_info(monkeypatch):
    _patch_fetch(monkeypatch, {
        account._ME_URL: _FIXTURE["auth_me"],
        account._STRIPE_URL: _FIXTURE["auth_stripe"],
        account._USAGE_URL: _FIXTURE["usage_summary"],
    })

    info = await analyze_account(_make_token())

    assert info.name == "Test User"
    assert info.email == "user@example.com"
    assert info.plan == "Free"                    # "free" prettified
    assert info.account_id == "user_01TESTTESTTESTTESTTESTTEST"  # bare me.sub

    # Three plan-bucket windows, all model=None, sharing billingCycleEnd.
    by_label = {w.label: w for w in info.windows}
    assert set(by_label) == {"total", "auto", "api"}
    assert by_label["total"].pct == 100.0
    assert by_label["auto"].pct == 100.0
    assert by_label["api"].pct == 82.0
    reset = datetime(2026, 7, 10, 9, 16, 18, 140000, tzinfo=timezone.utc)
    for w in info.windows:
        assert w.model is None
        assert w.resets_at == reset

    # raw escape hatch carries all three payloads.
    assert info.raw["me"]["sub"] == "user_01TESTTESTTESTTESTTESTTEST"
    assert info.raw["stripe"]["membershipType"] == "free"
    assert info.raw["usage"]["membershipType"] == "free"

    # Derived summary (plan + name + email).
    assert info.summary == "Plan: Free for Test User <user@example.com>"


async def test_stripe_failure_falls_back_to_usage_plan(monkeypatch):
    # stripe GET returns None → plan sourced from usage-summary.membershipType.
    _patch_fetch(monkeypatch, {
        account._ME_URL: _FIXTURE["auth_me"],
        account._STRIPE_URL: None,
        account._USAGE_URL: _FIXTURE["usage_summary"],
    })

    info = await analyze_account(_make_token())

    assert info.plan == "Free"
    assert info.raw["stripe"] is None
    assert len(info.windows) == 3


async def test_fetch_raises_is_failsoft(monkeypatch):
    # Any exception from the fetch seam → EMPTY, never propagates.
    async def _boom(url, cookie):
        raise RuntimeError("network down")

    monkeypatch.setattr(account, "_fetch", _boom)

    info = await analyze_account(_make_token())
    assert info == EMPTY
    assert isinstance(info, AccountInfo)


async def test_undecodable_token_is_empty(monkeypatch):
    # No decodable JWT sub → no cookie → EMPTY, and _fetch never called.
    async def _must_not_call(url, cookie):
        raise AssertionError("must not fetch without a cookie")

    monkeypatch.setattr(account, "_fetch", _must_not_call)

    assert await analyze_account("not-a-jwt") == EMPTY


async def test_missing_me_is_empty(monkeypatch):
    # Identity GET failing (None) → EMPTY (no usable account without me).
    _patch_fetch(monkeypatch, {
        account._ME_URL: None,
        account._STRIPE_URL: _FIXTURE["auth_stripe"],
        account._USAGE_URL: _FIXTURE["usage_summary"],
    })

    assert await analyze_account(_make_token()) == EMPTY
