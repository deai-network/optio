"""Explicit session restore: config validation + capture/restore session flows.

Spec: docs/2026-06-10-claudecode-session-restore-design.md
"""
from __future__ import annotations

import pytest
from bson import ObjectId

from optio_claudecode import ClaudeCodeTaskConfig


def _conv(**kw) -> ClaudeCodeTaskConfig:
    base = dict(
        consumer_instructions="x",
        mode="conversation",
        permission_mode="bypassPermissions",
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_restore_fields_default_off_and_valid_combo():
    cfg = _conv(
        session_restore_from=ObjectId(),
        session_restore_until="some-uuid",
        on_session_saved=lambda blob_id, end_state: None,
        model="claude-opus-4-8",
    )
    assert cfg.session_restore_until == "some-uuid"
    plain = _conv()
    assert plain.session_restore_from is None
    assert plain.session_restore_until is None
    assert plain.on_session_saved is None
    assert plain.model is None


def test_restore_until_requires_restore_from():
    with pytest.raises(ValueError, match="session_restore_until"):
        _conv(session_restore_until="some-uuid")


def test_restore_from_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation"):
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            mode="iframe",
            session_restore_from=ObjectId(),
        )


def test_restore_from_incompatible_with_auto_start():
    with pytest.raises(ValueError, match="auto_start"):
        _conv(session_restore_from=ObjectId(), auto_start=True)
