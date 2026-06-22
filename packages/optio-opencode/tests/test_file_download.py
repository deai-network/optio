import pytest

from optio_opencode.prompt import compose_agents_md
from optio_opencode.session import conversation_widget_data
from optio_opencode.types import OpencodeTaskConfig


# --- config validation -------------------------------------------------


def test_file_download_requires_conversation_ui():
    with pytest.raises(ValueError, match="conversation_ui=True"):
        OpencodeTaskConfig(
            consumer_instructions="t",
            mode="conversation",
            conversation_ui=False,
            file_download=True,
        )


def test_file_download_ok():
    cfg = OpencodeTaskConfig(
        consumer_instructions="t",
        mode="conversation",
        conversation_ui=True,
        file_download=True,
    )
    assert cfg.file_download is True
    assert cfg.max_download_bytes == 10_000_000


def test_file_download_defaults_off():
    cfg = OpencodeTaskConfig(
        consumer_instructions="t", mode="conversation", conversation_ui=True
    )
    assert cfg.file_download is False
    assert cfg.max_download_bytes == 10_000_000


# --- widgetData --------------------------------------------------------


def test_widget_data_carries_download_flags():
    cfg = OpencodeTaskConfig(
        consumer_instructions="t",
        mode="conversation",
        conversation_ui=True,
        file_download=True,
    )
    wd = conversation_widget_data(cfg, session_id="s", directory="/w")
    assert wd["fileDownload"] is True
    assert wd["maxDownloadBytes"] == 10_000_000


def test_widget_data_download_flags_default_off():
    cfg = OpencodeTaskConfig(
        consumer_instructions="t", mode="conversation", conversation_ui=True
    )
    wd = conversation_widget_data(cfg, session_id="s", directory="/w")
    assert wd["fileDownload"] is False
    assert wd["maxDownloadBytes"] == 10_000_000


# --- prompt injection --------------------------------------------------


def test_prompt_includes_downloadables_block_when_enabled():
    out = compose_agents_md(
        "talk to me",
        workdir_exclude=None,
        host_protocol=True,
        file_download=True,
    )
    assert "optio-file:" in out
    assert "Deliverables" in out


def test_prompt_omits_downloadables_block_by_default():
    out = compose_agents_md("talk to me", workdir_exclude=None)
    assert "optio-file:" not in out
