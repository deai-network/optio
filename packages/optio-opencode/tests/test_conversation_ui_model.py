import pytest

from optio_opencode.session import conversation_widget_data
from optio_opencode.types import OpencodeTaskConfig


def test_defaults_are_off():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task", mode="conversation", conversation_ui=True
    )
    assert cfg.model is None
    assert cfg.show_session_controls is False


def test_fields_accepted_in_conversation_ui():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task",
        mode="conversation",
        conversation_ui=True,
        model="opencode/big-pickle",
        show_session_controls=True,
    )
    assert cfg.model == "opencode/big-pickle"
    assert cfg.show_session_controls is True


def test_show_session_controls_requires_conversation_ui():
    with pytest.raises(ValueError, match="conversation_ui=True"):
        OpencodeTaskConfig(
            consumer_instructions="task",
            mode="conversation",
            conversation_ui=False,
            show_session_controls=True,
        )


def test_native_spinner_requires_conversation_ui():
    with pytest.raises(ValueError, match="conversation_ui=True"):
        OpencodeTaskConfig(
            consumer_instructions="task",
            mode="conversation",
            conversation_ui=False,
            native_spinner=True,
        )


def test_model_valid_without_conversation_ui():
    # The conversation_ui gate was dropped: model is now valid in every mode
    # (it also feeds the launch opencode.json default, not just the widget).
    cfg = OpencodeTaskConfig(
        consumer_instructions="task",
        mode="iframe",
        conversation_ui=False,
        model="opencode/big-pickle",
    )
    assert cfg.model == "opencode/big-pickle"


def test_widget_data_carries_model_fields():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task",
        mode="conversation",
        conversation_ui=True,
        model="opencode/big-pickle",
        show_session_controls=True,
        tool_verbosity="verbose",
        reasoning_effort="high",
    )
    wd = conversation_widget_data(cfg, session_id="s1", directory="/wd")
    assert wd == {
        "protocol": "opencode",
        "sessionID": "s1",
        "directory": "/wd",
        "toolVerbosity": "verbose",
        "thinkingVerbosity": "hidden",
        "showSessionControls": True,
        "nativeSpinner": False,
        "defaultModel": "opencode/big-pickle",
        "defaultEffort": "high",
        "showFileUpload": False,
        "maxUploadBytes": 10_000_000,
        "fileDownload": False,
        "maxDownloadBytes": 10_000_000,
    }


def test_widget_data_defaults():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task", mode="conversation", conversation_ui=True
    )
    wd = conversation_widget_data(cfg, session_id="s1", directory="/wd")
    assert wd["showSessionControls"] is False
    assert wd["nativeSpinner"] is False
    assert wd["defaultModel"] is None
    # No effort configured ⇒ the widget seeds nothing (opencode's per-model
    # default stands).
    assert wd["defaultEffort"] is None


def test_reasoning_effort_accepted():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task",
        mode="conversation",
        conversation_ui=True,
        reasoning_effort="medium",
    )
    assert cfg.reasoning_effort == "medium"


def test_reasoning_effort_defaults_none():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task", mode="conversation", conversation_ui=True
    )
    assert cfg.reasoning_effort is None


def test_reasoning_effort_rejects_bad_value():
    with pytest.raises(ValueError, match="reasoning_effort"):
        OpencodeTaskConfig(
            consumer_instructions="task",
            mode="conversation",
            conversation_ui=True,
            reasoning_effort="turbo",
        )
