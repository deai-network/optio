"""Session-level tests for the config-harmonization additions:

- the ``allowed_tools``/``disallowed_tools`` fold into opencode.json's
  ``permission`` map (T2: REACHABLE → wired).

(The former ``fs_isolation`` inert-warning tests were removed when claustrum
was wired for opencode; the fs-isolation triad is covered by test_claustrum.py.)

Pure/unit — no subprocess, no host, xdist-safe.
"""

from optio_opencode.session import _fold_tool_permissions


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
