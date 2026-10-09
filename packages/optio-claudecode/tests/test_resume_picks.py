"""The operator's session-control picks kept across an optio resume.

Pure units: the picks file (picks.loads/dumps), which saved permission mode a
resumed task may restore (picks.restorable_permission_mode), and which model
a saved pick resumes on against the CLI's list now (models.restore_model):
the saved value while the list still offers it, else the newest entry of its
family (claude-<family>-<version>), keeping its [variant] when one offers
it, never the floating ``default``.
"""
from __future__ import annotations

from optio_claudecode import models as cc_models
from optio_claudecode import picks as cc_picks

from .fake_claude import CLI_MODELS

NOW = cc_models.parse_cli_models(CLI_MODELS)


def _entry(value: str, resolved: str, **kw) -> dict:
    return {"value": value, "resolvedModel": resolved, "displayName": value, **kw}


def _catalog(*entries: dict) -> list[dict]:
    return cc_models.parse_cli_models(list(entries))


# --- models.restore_model ---------------------------------------------------


def test_a_saved_alias_the_list_still_offers_is_kept():
    assert cc_models.restore_model(NOW, saved="opus[1m]", resolved="claude-opus-5[1m]") == "opus[1m]"


def test_a_saved_full_id_an_alias_resolves_to_is_kept():
    assert cc_models.restore_model(NOW, saved="claude-sonnet-5", resolved="claude-sonnet-5") == "claude-sonnet-5"


def test_a_saved_alias_no_longer_offered_moves_to_its_family():
    later = _catalog(
        _entry("default", "claude-opus-6"),
        _entry("opus", "claude-opus-6"),
        _entry("sonnet", "claude-sonnet-5"),
    )
    assert cc_models.restore_model(later, saved="opus[1m]", resolved="claude-opus-5[1m]") == "opus"


def test_the_family_match_takes_the_newest_version():
    later = _catalog(
        _entry("opus-legacy", "claude-opus-5"),
        _entry("opus", "claude-opus-6"),
    )
    assert cc_models.restore_model(later, saved="claude-opus-4-8", resolved="claude-opus-4-8") == "opus"


def test_the_family_match_keeps_the_variant_when_one_offers_it():
    both = _catalog(
        _entry("opus", "claude-opus-5"),
        _entry("opus[1m]", "claude-opus-5[1m]"),
    )
    assert cc_models.restore_model(both, saved="claude-opus-4-8[1m]", resolved="claude-opus-4-8[1m]") == "opus[1m]"
    assert cc_models.restore_model(both, saved="claude-opus-4-8", resolved="claude-opus-4-8") == "opus"


def test_the_family_match_never_picks_default():
    later = _catalog(
        _entry("default", "claude-opus-6[1m]"),
        _entry("sonnet", "claude-sonnet-5"),
    )
    assert cc_models.restore_model(later, saved="opus[1m]", resolved="claude-opus-5[1m]") is None


def test_an_older_version_listed_as_its_own_entry_is_offered():
    # A logged-in CLI lists older versions as entries resolving to themselves.
    listed = _catalog(*CLI_MODELS, _entry("claude-opus-4-8", "claude-opus-4-8"))
    assert cc_models.restore_model(listed, saved="claude-opus-4-8", resolved="claude-opus-4-8") == "claude-opus-4-8"


def test_an_older_version_no_longer_listed_moves_to_its_family():
    assert cc_models.restore_model(NOW, saved="claude-opus-4-8", resolved="claude-opus-4-8") == "opus[1m]"


def test_an_alias_saved_without_its_full_id_still_finds_its_family():
    later = _catalog(_entry("opus", "claude-opus-6"), _entry("fable", "claude-fable-1"))
    assert cc_models.restore_model(later, saved="opus[1m]", resolved=None) == "opus"


def test_no_entry_of_the_family_gives_none():
    assert cc_models.restore_model(NOW, saved="fable", resolved=None) is None
    assert cc_models.restore_model(NOW, saved="claude-fable-1", resolved="claude-fable-1") is None


# --- the picks file ---------------------------------------------------------


def test_picks_round_trip():
    p = cc_picks.Picks(model="sonnet", model_resolved="claude-sonnet-5", effort="low",
                       permission_mode="acceptEdits")
    assert cc_picks.loads(cc_picks.dumps(p)) == p


def test_an_unreadable_picks_file_is_no_picks():
    assert cc_picks.loads("") is None
    assert cc_picks.loads("{not json") is None
    assert cc_picks.loads("[1, 2]") is None


def test_picks_of_the_wrong_type_are_dropped_unknown_keys_ignored():
    assert cc_picks.loads('{"model": 3, "effort": "low", "colour": "red"}') == cc_picks.Picks(effort="low")


def test_a_saved_permission_mode_is_restored_only_where_the_task_offers_it():
    offered = ["acceptEdits", "auto", "dontAsk"]
    assert cc_picks.restorable_permission_mode("acceptEdits", offered) == "acceptEdits"
    assert cc_picks.restorable_permission_mode("bypassPermissions", offered) is None
    assert cc_picks.restorable_permission_mode(None, offered) is None
