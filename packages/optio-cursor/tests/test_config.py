import pytest

from optio_cursor.types import CursorTaskConfig
# Harmonization C1: the shared aliases must stay importable from
# ``optio_cursor.types`` (re-exported) so existing import sites keep working.
from optio_cursor.types import (  # noqa: F401
    AllowedDir,
    CallerMessageCallback,
    ConversationMode,
    SeedProvider,
    SeedUnavailableError,
    ThinkingVerbosity,
    ToolVerbosity,
)


def test_defaults_and_validation():
    c = CursorTaskConfig(consumer_instructions="do it", delivery_type="audit")
    assert c.mode == "iframe" and c.host_protocol is True and c.force is False
    # auto_start defaults False (parity) — a task must opt in to the kickoff,
    # else an interactive/conversation task auto-fires and blocks the first chat.
    assert c.auto_start is False
    with pytest.raises(ValueError):
        CursorTaskConfig(
            consumer_instructions="x", sandbox="nope", delivery_type="audit",
        )


def test_install_dirs_must_be_absolute():
    with pytest.raises(ValueError):
        CursorTaskConfig(
            consumer_instructions="x", install_dir="rel/path", delivery_type="audit",
        )
    with pytest.raises(ValueError):
        CursorTaskConfig(
            consumer_instructions="x", ttyd_install_dir="rel/path",
            delivery_type="audit",
        )
    CursorTaskConfig(
        consumer_instructions="x",
        install_dir="/opt/cursor",
        ttyd_install_dir="~/bin",
        delivery_type="audit",
    )


def test_shared_alloweddir_is_reexported_and_validates():
    # Re-exported from optio_agents; validates at construction.
    assert AllowedDir("/w", "rox").mode == "rox"
    with pytest.raises(ValueError):
        AllowedDir("/w", "wx")


def test_harmonized_core_defaults():
    # C2 install_dir field present (per-engine cursor_install_dir gone), C3
    # single `model` field, P1/P2/P3 fields wired with the expected defaults.
    c = CursorTaskConfig(consumer_instructions="x", delivery_type="audit")
    assert hasattr(c, "install_dir") and c.install_dir is None
    assert not hasattr(c, "cursor_install_dir")
    assert c.model is None and not hasattr(c, "default_model")
    # P3 caller-message channel.
    assert c.use_client_messages is False
    assert c.on_caller_message is None
    # P1 session-blob encryption pair.
    assert c.session_blob_encrypt is None and c.session_blob_decrypt is None
    # P2 resume-refresh hook defaults to the identity recompose (not None).
    assert c.on_resume_refresh is not None
    assert c.on_resume_refresh(c) is c


def test_session_blob_pair_must_be_symmetric():
    ident = lambda b: b  # noqa: E731
    # Only one of the pair set → config error.
    with pytest.raises(ValueError, match="session_blob"):
        CursorTaskConfig(
            consumer_instructions="x", session_blob_encrypt=ident,
            delivery_type="audit",
        )
    with pytest.raises(ValueError, match="session_blob"):
        CursorTaskConfig(
            consumer_instructions="x", session_blob_decrypt=ident,
            delivery_type="audit",
        )
    # Both set → accepted.
    c = CursorTaskConfig(
        consumer_instructions="x",
        session_blob_encrypt=ident,
        session_blob_decrypt=ident,
        delivery_type="audit",
    )
    assert c.session_blob_encrypt is ident and c.session_blob_decrypt is ident
