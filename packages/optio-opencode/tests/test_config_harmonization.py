"""Session-level tests for the config-harmonization additions:

- the inert ``fs_isolation`` runtime warning (change 6: NOT-YET-ENFORCED), and
- the ``allowed_tools``/``disallowed_tools`` fold into opencode.json's
  ``permission`` map (T2: REACHABLE → wired).

Pure/unit — no subprocess, no host, xdist-safe.
"""

import logging

from optio_opencode.session import (
    _fold_tool_permissions,
    _warn_if_fs_isolation_unenforced,
)
from optio_opencode.types import OpencodeTaskConfig


# --- fs_isolation inert warning ----------------------------------------------


def test_fs_isolation_true_emits_not_enforced_warning(caplog):
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=True)
    with caplog.at_level(logging.WARNING, logger="optio_opencode.session"):
        _warn_if_fs_isolation_unenforced(cfg)
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("fs_isolation" in m and "not yet enforced" in m for m in msgs), msgs


def test_fs_isolation_false_is_silent(caplog):
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    with caplog.at_level(logging.WARNING, logger="optio_opencode.session"):
        _warn_if_fs_isolation_unenforced(cfg)
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


# --- allowed_tools / disallowed_tools fold -----------------------------------


def test_fold_noop_when_no_tool_lists():
    base = {"model": "x/y"}
    out = _fold_tool_permissions(base, allowed_tools=None, disallowed_tools=None)
    assert out is base  # untouched, same object


def test_fold_allow_and_deny_land_in_permission_map():
    out = _fold_tool_permissions(
        {}, allowed_tools=["read", "list"], disallowed_tools=["bash"]
    )
    assert out["permission"] == {"read": "allow", "list": "allow", "bash": "deny"}


def test_fold_merges_without_clobbering_operator_permission():
    base = {"permission": {"edit": "deny", "read": "allow"}}
    out = _fold_tool_permissions(
        base, allowed_tools=["list"], disallowed_tools=["bash"]
    )
    # Operator entries preserved; new ones added.
    assert out["permission"] == {
        "edit": "deny",
        "read": "allow",
        "list": "allow",
        "bash": "deny",
    }
    # Input not mutated.
    assert base["permission"] == {"edit": "deny", "read": "allow"}


def test_fold_deny_wins_over_allow_for_same_tool():
    out = _fold_tool_permissions(
        {}, allowed_tools=["bash"], disallowed_tools=["bash"]
    )
    assert out["permission"] == {"bash": "deny"}


def test_fold_allowed_overrides_operator_default_for_named_tool():
    base = {"permission": {"bash": "deny"}}
    out = _fold_tool_permissions(base, allowed_tools=["bash"], disallowed_tools=None)
    assert out["permission"] == {"bash": "allow"}
