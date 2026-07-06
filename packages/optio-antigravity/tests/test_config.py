import pytest

from optio_antigravity.types import AntigravityTaskConfig


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        AntigravityTaskConfig(
            consumer_instructions="x", mode="iframe", conversation_ui=True
        )


def test_default_mode_is_iframe_and_auto_start_false():
    c = AntigravityTaskConfig(consumer_instructions="x")
    assert c.mode == "iframe"
    assert c.auto_start is False  # a conversation task must not auto-fire


def test_invalid_permission_mode_rejected():
    with pytest.raises(ValueError):
        AntigravityTaskConfig(consumer_instructions="x", permission_mode="bogus")


def test_native_spinner_requires_conversation_ui():
    with pytest.raises(ValueError, match="native_spinner"):
        AntigravityTaskConfig(
            consumer_instructions="x", mode="iframe", native_spinner=True
        )


def test_native_spinner_accepted_in_conversation_ui():
    c = AntigravityTaskConfig(
        consumer_instructions="x",
        mode="conversation",
        conversation_ui=True,
        native_spinner=True,
    )
    assert c.native_spinner is True
