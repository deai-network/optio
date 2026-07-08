import pytest
from optio_opencode.types import OpencodeTaskConfig


def test_show_file_upload_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_file_upload"):
        OpencodeTaskConfig(consumer_instructions="t", mode="conversation", fs_isolation=False,
                           conversation_ui=False, show_file_upload=True)


def test_show_file_upload_ok():
    cfg = OpencodeTaskConfig(consumer_instructions="t", mode="conversation", fs_isolation=False,
                             conversation_ui=True, show_file_upload=True)
    assert cfg.show_file_upload is True
    assert cfg.max_upload_bytes == 10_000_000


def test_widget_data_has_upload_flags():
    from optio_opencode.session import conversation_widget_data
    cfg = OpencodeTaskConfig(consumer_instructions="t", mode="conversation", fs_isolation=False,
                             conversation_ui=True, show_file_upload=True)
    wd = conversation_widget_data(cfg, session_id="s", directory="/w")
    assert wd["showFileUpload"] is True
    assert wd["maxUploadBytes"] == 10_000_000
