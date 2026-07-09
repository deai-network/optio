"""claudecode ``analyze_account`` — maps a live profile+usage payload into the
shared ``optio_agents.account.AccountInfo`` (identity/plan + ``limits[]``-derived
usage windows), fail-soft to ``EMPTY`` on any fetch error."""

import json
import pathlib

from optio_agents.account import EMPTY, AccountInfo

from optio_claudecode import account as acct

FIX = pathlib.Path(__file__).parent / "fixtures"


async def test_maps_profile_and_usage(monkeypatch):
    profile = json.loads((FIX / "claude_profile.json").read_text())
    usage = json.loads((FIX / "claude_usage.json").read_text())

    async def fake_profile(_tok):
        return profile

    async def fake_usage(_tok):
        return usage

    monkeypatch.setattr(acct, "_fetch_profile", fake_profile)
    monkeypatch.setattr(acct, "_fetch_usage", fake_usage)

    info = await acct.analyze_account("tok")
    assert isinstance(info, AccountInfo)
    assert info.email == profile["account"]["email"]
    assert info.plan == "Claude Max 20x"                       # from rate_limit_tier
    assert info.account_id == profile["account"]["uuid"]
    # windows come from limits[]: at least the global session/weekly windows
    labels = {w.label for w in info.windows}
    assert labels & {"session", "weekly_all"}
    # any window whose source limit had a model scope carries a model tag;
    # global limits (scope=None) have model=None
    assert any(w.model is None for w in info.windows)          # global window present
    scoped = [l for l in usage.get("limits", []) if (l.get("scope") or {}).get("model")]
    if scoped:
        assert any(w.model is not None for w in info.windows)


async def test_failsoft_on_fetch_error(monkeypatch):
    async def boom(_tok):
        raise OSError("network")

    monkeypatch.setattr(acct, "_fetch_profile", boom)
    monkeypatch.setattr(acct, "_fetch_usage", boom)
    assert await acct.analyze_account("tok") == EMPTY
