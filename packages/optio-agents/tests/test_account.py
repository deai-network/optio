from datetime import datetime, timezone

from optio_agents.account import AccountInfo, UsageWindow, EMPTY, is_limited


def _dt(h): return datetime(2026, 7, 9, h, 0, tzinfo=timezone.utc)


def test_summary_full():
    info = AccountInfo(name="Jane Doe", email="jane@x.com", plan="Claude Max 20x")
    assert info.summary == "Plan: Claude Max 20x for Jane Doe <jane@x.com>"


def test_summary_no_name():
    info = AccountInfo(email="jane@x.com", plan="Claude Max 20x")
    assert info.summary == "Plan: Claude Max 20x for <jane@x.com>"


def test_summary_plan_only_when_no_identity():
    # An identity-less account (plan known, no name/email — e.g. kimicode)
    # summarizes by plan alone, NOT None: friendlier than the opaque account id.
    assert AccountInfo(plan="Basic").summary == "Plan: Basic"


def test_summary_none_when_no_plan():
    assert AccountInfo(email="jane@x.com").summary is None  # no plan
    assert EMPTY.summary is None


def test_next_reset_soonest_maxed_only():
    info = AccountInfo(windows=[
        UsageWindow("five_hour", 100.0, _dt(15), None),
        UsageWindow("seven_day", 100.0, _dt(12), None),
        UsageWindow("seven_day_opus", 40.0, _dt(9), "opus"),  # not maxed -> ignored
    ])
    assert info.next_reset() == _dt(12)


def test_next_reset_none_when_nothing_maxed():
    info = AccountInfo(windows=[UsageWindow("five_hour", 50.0, _dt(15), None)])
    assert info.next_reset() is None


def test_roundtrip_to_from_dict():
    info = AccountInfo(name="Jane", email="j@x.com", plan="P", account_id="u1",
                       windows=[UsageWindow("five_hour", 100.0, _dt(15), None)],
                       raw={"k": "v"})
    assert AccountInfo.from_dict(info.to_dict()) == info


def test_empty_roundtrips():
    assert AccountInfo.from_dict(EMPTY.to_dict()) == EMPTY


def test_to_dict_includes_derived_summary():
    # JSON/TS consumers read metadata.account.summary directly; it must be stamped.
    info = AccountInfo(name="Jane Doe", email="jane@x.com", plan="Claude Max 20x")
    assert info.to_dict()["summary"] == "Plan: Claude Max 20x for Jane Doe <jane@x.com>"
    assert EMPTY.to_dict()["summary"] is None


def test_limited_global_maxed_unreset():
    info = AccountInfo(windows=[UsageWindow("seven_day", 100.0, _dt(15), None)])
    assert is_limited(info, _dt(12)) is True          # resets in future


def test_not_limited_when_reset_passed():
    info = AccountInfo(windows=[UsageWindow("seven_day", 100.0, _dt(9), None)])
    assert is_limited(info, _dt(12)) is False          # window already reset


def test_maxed_no_reset_time_is_limited():
    info = AccountInfo(windows=[UsageWindow("seven_day", 100.0, None, None)])
    assert is_limited(info, _dt(12)) is True


def test_not_limited_below_100():
    info = AccountInfo(windows=[UsageWindow("seven_day", 99.9, _dt(15), None)])
    assert is_limited(info, _dt(12)) is False


def test_per_model_gated_only_when_requested():
    info = AccountInfo(windows=[UsageWindow("seven_day_opus", 100.0, _dt(15), "opus")])
    assert is_limited(info, _dt(12)) is False               # opus not required
    assert is_limited(info, _dt(12), ["opus"]) is True       # opus required
    assert is_limited(info, _dt(12), ["sonnet"]) is False    # different model


def test_empty_not_limited():
    assert is_limited(EMPTY, _dt(12)) is False


def test_public_exports():
    import optio_agents
    assert optio_agents.AccountInfo is not None
    assert optio_agents.is_limited is not None


from optio_agents.account import accounts_from_metadata, accounts_to_metadata, any_usable


def test_accounts_from_metadata_prefers_plural_falls_back_to_singular():
    a = AccountInfo(plan="P", email="e@x.com")
    assert accounts_from_metadata({"accounts": [a.to_dict()]}) == [a]
    assert accounts_from_metadata({"account": a.to_dict()}) == [a]   # legacy
    assert accounts_from_metadata({}) == []


def test_accounts_to_metadata_roundtrip():
    a = AccountInfo(plan="P", email="e@x.com")
    assert accounts_from_metadata({"accounts": accounts_to_metadata([a])}) == [a]


def test_any_usable(_now=None):
    from datetime import datetime, timezone
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
    maxed = AccountInfo(windows=(UsageWindow("w", 100.0, None, None),))
    ok = AccountInfo(plan="P")
    assert any_usable([maxed, ok], now) is True
    assert any_usable([maxed], now) is False
    assert any_usable([], now) is False


def test_account_dicts_from_metadata_keeps_stamped_summary():
    from optio_agents.account import account_dicts_from_metadata
    # stamped summary is preserved (from_dict would recompute it to None here)
    d = {"account_id": "A", "summary": "Plan A"}
    assert account_dicts_from_metadata({"accounts": [d]}) == [d]
    assert account_dicts_from_metadata({"account": d}) == [d]   # legacy singular
    assert account_dicts_from_metadata({}) == []


def test_summary_provider_prefix():
    # provider attribution (opencode meta-analyzer): the provider prefixes the
    # summary so a multi-provider seed is unambiguous. None (single-vendor) = no prefix.
    assert AccountInfo(provider="openai", plan="Plus", email="a@b.com").summary == "openai · Plan: Plus for <a@b.com>"
    assert AccountInfo(provider="groq").summary == "groq · unknown account"
    assert AccountInfo(plan="Max", email="a@b.com").summary == "Plan: Max for <a@b.com>"
    # provider survives the metadata roundtrip
    a = AccountInfo(provider="xai", plan="Grok Pro", account_id="u1")
    assert AccountInfo.from_dict(a.to_dict()) == a
