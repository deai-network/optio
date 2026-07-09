"""deepseek provider handler — api-key balance → AccountInfo.

DeepSeek exposes no identity endpoint; the account label is its prepaid
balance. The handler fetches ``GET /user/balance`` (Bearer <key>) and maps the
first ``balance_infos`` row into ``plan``. Tests monkeypatch the module's
``_fetch`` seam — never the real network — so every case is hermetic.
"""

import pytest

from optio_agents.account import AccountInfo
from optio_opencode.providers import deepseek

# NOTE: synthetic — live-verify pending a real seed. Shape mirrors the documented
# DeepSeek ``GET https://api.deepseek.com/user/balance`` response.
_SYNTHETIC_BALANCE = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "USD",
            "total_balance": "5.00",
            "granted_balance": "0.00",
            "topped_up_balance": "5.00",
        }
    ],
}


@pytest.fixture
def patched_fetch(monkeypatch):
    """Patch the _fetch seam to return the synthetic balance body, asserting the
    key flows through."""

    async def _fetch(key):
        assert key == "sk-deepseek-1"
        return _SYNTHETIC_BALANCE

    monkeypatch.setattr(deepseek, "_fetch", _fetch)


async def test_api_entry_maps_balance_to_plan(patched_fetch):
    info = await deepseek.handle({"type": "api", "key": "sk-deepseek-1"})
    assert isinstance(info, AccountInfo)
    # Identity-less: the balance IS the label.
    assert info.name is None
    assert info.email is None
    assert info.account_id is None
    assert info.plan == "Balance 5.00 USD"
    assert info.windows == ()
    assert info.raw == {"balance": _SYNTHETIC_BALANCE}
    # The plain summary renders the balance as the account label.
    assert info.summary == "Plan: Balance 5.00 USD"


async def test_non_api_entry_declines_without_network(monkeypatch):
    # An oauth entry must never reach the network — it declines to None.
    async def _boom(key):
        raise AssertionError("must not fetch for a non-api entry")

    monkeypatch.setattr(deepseek, "_fetch", _boom)
    assert await deepseek.handle({"type": "oauth", "access": "tok"}) is None


async def test_missing_key_declines_without_network(monkeypatch):
    async def _boom(key):
        raise AssertionError("must not fetch without a key")

    monkeypatch.setattr(deepseek, "_fetch", _boom)
    assert await deepseek.handle({"type": "api"}) is None


async def test_empty_balance_infos_is_none(monkeypatch):
    async def _fetch(key):
        return {"is_available": True, "balance_infos": []}

    monkeypatch.setattr(deepseek, "_fetch", _fetch)
    assert await deepseek.handle({"type": "api", "key": "k"}) is None


async def test_fetch_returning_none_is_none(monkeypatch):
    async def _fetch(key):
        return None

    monkeypatch.setattr(deepseek, "_fetch", _fetch)
    assert await deepseek.handle({"type": "api", "key": "k"}) is None


async def test_fetch_raising_is_fail_soft_none(monkeypatch):
    async def _fetch(key):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(deepseek, "_fetch", _fetch)
    assert await deepseek.handle({"type": "api", "key": "k"}) is None
