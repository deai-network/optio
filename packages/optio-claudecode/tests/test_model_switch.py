"""Unit tests for conversation-mode model switching (written against the
pinned interfaces in the Phase-2 plan).

These cover the file-disjoint units that don't need a live claude:
config validation and the conversation model-change signal. The model list
(the CLI's own, from initialize) is covered in test_cli_models.py; the restart
loop in test_conversation_ui_session.py.
"""

import pytest

from optio_claudecode.types import ClaudeCodeTaskConfig


def _cfg(**kw):
    # Mirror the existing conversation-config tests' construction: the only
    # required field is consumer_instructions, and fs_isolation=False keeps
    # the config valid without a live host.
    base = dict(consumer_instructions="do things", fs_isolation=False)
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


def test_show_session_controls_requires_conversation_ui():
    # A valid conversation permission setup (permission_gate=True) gets us past
    # the unrelated conversation-mode validation so the show_session_controls
    # check is the one that fires.
    with pytest.raises(ValueError, match="show_session_controls"):
        _cfg(
            mode="conversation",
            permission_gate=True,
            conversation_ui=False,
            show_session_controls=True,
        )


def test_show_session_controls_ok_in_conversation_ui():
    cfg = _cfg(
        mode="conversation",
        permission_gate=True,
        conversation_ui=True,
        show_session_controls=True,
    )
    assert cfg.show_session_controls is True


def test_native_spinner_requires_conversation_ui():
    # A valid conversation permission setup (permission_gate=True) gets us past
    # the unrelated conversation-mode validation so the native_spinner check is
    # the one that fires.
    with pytest.raises(ValueError, match="native_spinner"):
        _cfg(
            mode="conversation",
            permission_gate=True,
            conversation_ui=False,
            native_spinner=True,
        )


@pytest.mark.asyncio
async def test_set_control_model_sets_restart_signal():
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    await conv.set_control("model", "sonnet")
    assert conv.requested_model == "sonnet"
    assert conv.model_change_requested.is_set()


@pytest.mark.asyncio
async def test_set_control_ignores_unknown_id():
    # claudecode exposes only the model control; any other id is a no-op and
    # never triggers a restart.
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    await conv.set_control("thinking", "high")
    assert conv.requested_model is None
    assert not conv.model_change_requested.is_set()


@pytest.mark.asyncio
async def test_restart_keeps_conversation_open_and_emits_no_close():
    """A model-swap process EOF must not close the conversation or emit
    x-optio-closed (which would gray the widget input)."""
    from optio_claudecode.conversation import ClaudeCodeConversation
    conv = ClaudeCodeConversation()
    events: list = []
    conv.on_event(lambda e: events.append(e))

    conv.begin_restart()
    await conv._finish("process ended")          # old process EOF during swap
    assert not conv._closed.is_set()
    assert not any(e.get("type") == "x-optio-closed" for e in events)

    class _FakeHandle:
        stdin = object()
    conv.attach(_FakeHandle())                    # relaunched process wired in
    await conv._finish("process ended")           # a real later EOF
    assert conv._closed.is_set()
    assert any(e.get("type") == "x-optio-closed" for e in events)
