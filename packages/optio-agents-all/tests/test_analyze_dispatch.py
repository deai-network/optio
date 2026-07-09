import pytest
from optio_agents.account import EMPTY, AccountInfo
from optio_agents_all import analyze_account, analyze_accounts


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


# --- plural: analyze_accounts -------------------------------------------------


async def test_analyze_accounts_wraps_single_in_one_element_list(monkeypatch):
    from optio_agents_all import factory

    async def fake(creds):
        return AccountInfo(email="j@x.com", plan="P")

    monkeypatch.setitem(
        factory._ANALYZE_ACCOUNTS_REGISTRY, "claudecode", factory._single(fake)
    )
    infos = await analyze_accounts("claudecode", "tok")
    assert [i.plan for i in infos] == ["P"]


async def test_analyze_accounts_drops_empty(monkeypatch):
    from optio_agents_all import factory

    async def empty(creds):
        return EMPTY

    monkeypatch.setitem(
        factory._ANALYZE_ACCOUNTS_REGISTRY, "claudecode", factory._single(empty)
    )
    assert await analyze_accounts("claudecode", "tok") == []


async def test_analyze_accounts_all_single_engines_registered():
    from optio_agents_all import factory

    for engine in ("claudecode", "codex", "cursor", "kimicode", "antigravity", "grok"):
        assert engine in factory._ANALYZE_ACCOUNTS_REGISTRY


async def test_analyze_accounts_opencode_dispatches_to_meta_analyzer():
    # opencode's real per-provider meta-analyzer: a configured provider yields
    # exactly one AccountInfo. An unknown provider maps to a placeholder — no
    # vendor network, so the dispatch stays hermetic — proving the dispatcher
    # routes opencode to the real analyzer (not the old []-returning stub).
    auth = {"futureai": {"type": "oauth", "access": "tok", "accountId": "fa-1"}}
    infos = await analyze_accounts("opencode", auth)
    assert len(infos) == 1
    assert infos[0].raw.get("provider") == "futureai"
    assert infos[0].raw.get("unanalyzed") is True


async def test_analyze_accounts_unknown_raises():
    with pytest.raises(ValueError):
        await analyze_accounts("nope", "tok")
