import pytest

from optio_opencode.session import conversation_widget_data
from optio_opencode.types import OpencodeTaskConfig


def test_defaults_are_off():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task", mode="conversation", conversation_ui=True
    )
    assert cfg.default_model is None
    assert cfg.show_session_controls is False


def test_fields_accepted_in_conversation_ui():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task",
        mode="conversation",
        conversation_ui=True,
        default_model="opencode/big-pickle",
        show_session_controls=True,
    )
    assert cfg.default_model == "opencode/big-pickle"
    assert cfg.show_session_controls is True


def test_show_session_controls_requires_conversation_ui():
    with pytest.raises(ValueError, match="conversation_ui=True"):
        OpencodeTaskConfig(
            consumer_instructions="task",
            mode="conversation",
            conversation_ui=False,
            show_session_controls=True,
        )


def test_default_model_requires_conversation_ui():
    with pytest.raises(ValueError, match="conversation_ui=True"):
        OpencodeTaskConfig(
            consumer_instructions="task",
            mode="conversation",
            conversation_ui=False,
            default_model="opencode/big-pickle",
        )


def test_widget_data_carries_model_fields():
    cfg = OpencodeTaskConfig(
        consumer_instructions="task",
        mode="conversation",
        conversation_ui=True,
        default_model="opencode/big-pickle",
        show_session_controls=True,
        tool_verbosity="verbose",
    )
    wd = conversation_widget_data(cfg, session_id="s1", directory="/wd")
    assert wd == {
        "protocol": "opencode",
        "sessionID": "s1",
        "directory": "/wd",
        "toolVerbosity": "verbose",
        "thinkingVerbosity": "hidden",
        "showSessionControls": True,
        "defaultModel": "opencode/big-pickle",
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
    assert wd["defaultModel"] is None
