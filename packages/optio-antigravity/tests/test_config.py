import dataclasses

import pytest

from optio_antigravity.types import AllowedDir, AntigravityTaskConfig


def _field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(AntigravityTaskConfig)}


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


# --- harmonization: renamed / dropped / added fields -----------------------


def test_install_dir_renamed_and_validated():
    names = _field_names()
    assert "install_dir" in names
    assert "agy_install_dir" not in names
    # absolute-path validation carried over to the renamed field.
    with pytest.raises(ValueError, match="install_dir"):
        AntigravityTaskConfig(consumer_instructions="x", install_dir="relative/bin")
    c = AntigravityTaskConfig(consumer_instructions="x", install_dir="/opt/agy")
    assert c.install_dir == "/opt/agy"


def test_dead_fields_removed():
    names = _field_names()
    # T2: agy exposes no per-tool allow/deny grammar → the fields are gone.
    assert "allowed_tools" not in names
    assert "disallowed_tools" not in names
    # effort/reasoning_effort were unreachable (agy bakes thinking into the model
    # id) → removed.
    assert "effort" not in names
    assert "reasoning_effort" not in names
    # C3: single `model` field; the Stage-7 default_model picker knob is gone.
    assert "default_model" not in names
    assert "model" in names


def test_ported_feature_fields_present_with_defaults():
    names = _field_names()
    for f in (
        "session_blob_encrypt", "session_blob_decrypt", "on_resume_refresh",
        "use_client_messages", "on_caller_message",
    ):
        assert f in names, f
    c = AntigravityTaskConfig(consumer_instructions="x")
    assert c.session_blob_encrypt is None
    assert c.session_blob_decrypt is None
    assert c.use_client_messages is False
    assert c.on_caller_message is None
    # on_resume_refresh defaults to the identity hook (recompose on resume).
    assert c.on_resume_refresh is not None
    assert c.on_resume_refresh(c) is c


def test_session_blob_transforms_must_be_paired():
    with pytest.raises(ValueError, match="session_blob"):
        AntigravityTaskConfig(
            consumer_instructions="x", session_blob_encrypt=lambda b: b,
        )
    with pytest.raises(ValueError, match="session_blob"):
        AntigravityTaskConfig(
            consumer_instructions="x", session_blob_decrypt=lambda b: b,
        )
    # both set together is fine.
    c = AntigravityTaskConfig(
        consumer_instructions="x",
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )
    assert c.session_blob_encrypt is not None and c.session_blob_decrypt is not None


def test_extra_allowed_dirs_accept_shared_superset():
    # Shared AllowedDir superset: rox/rwx are accepted (Landlock treats them as
    # ro/rw); junk is rejected at AllowedDir construction.
    for m in ("ro", "rw", "rox", "rwx"):
        c = AntigravityTaskConfig(
            consumer_instructions="x",
            extra_allowed_dirs=[AllowedDir("/data", m)],
        )
        assert c.extra_allowed_dirs[0].mode == m
    with pytest.raises(ValueError):
        AllowedDir("/data", "wx")
