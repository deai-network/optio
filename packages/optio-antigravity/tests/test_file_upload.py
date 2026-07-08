"""Conversation-mode file-upload config validation.

Uploads no longer flow through a listener ``POST /upload`` endpoint; the widget
POSTs to the generic optio-api ``/api/widget-upload`` route, which stages the
bytes in GridFS and calls the ``materializeUpload`` clamator RPC. The engine
only registers a per-task upload writer via ``ctx.register_upload_writer`` (the
session wiring is covered by the session-conversation tests). What remains
engine-local here is ``AntigravityTaskConfig.show_file_upload`` validation.
"""

import pytest

from optio_antigravity.types import AntigravityTaskConfig


def _cfg(**kw):
    base = dict(consumer_instructions="do things", delivery_type="audit")
    base.update(kw)
    return AntigravityTaskConfig(**base)


def test_show_file_upload_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_file_upload"):
        _cfg(mode="conversation", conversation_ui=False, show_file_upload=True)


def test_show_file_upload_ok_in_conversation_ui():
    cfg = _cfg(mode="conversation", conversation_ui=True, show_file_upload=True)
    assert cfg.show_file_upload is True
    assert cfg.max_upload_bytes == 10_000_000
