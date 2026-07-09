import pytest
from optio_agents.account import AccountInfo
from optio_agents_all import analyze_account


async def test_dispatch_claudecode(monkeypatch):
    from optio_agents_all import factory

    async def fake(creds):
        return AccountInfo(email="j@x.com", plan="P")

    monkeypatch.setitem(factory._ANALYZE_REGISTRY, "claudecode", fake)
    info = await analyze_account("claudecode", "tok")
    assert info.plan == "P"


async def test_dispatch_unknown_raises():
    with pytest.raises(ValueError):
        await analyze_account("nope", "tok")
