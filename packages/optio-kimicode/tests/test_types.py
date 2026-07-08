"""Tests for KimiCodeTaskConfig validation (Appendix D surface).

Ported from optio-grok's type tests; kimi deltas: effort is a validated
enum (low|medium|high|xhigh|max), reasoning_effort is a separate validated
enum (off|low|medium|high|xhigh|max) seeding the live graded thinking slider,
and model stays an unvalidated alias string.
"""

import pytest

from optio_kimicode.types import AllowedDir, KimiCodeTaskConfig


def _cfg(**kw) -> KimiCodeTaskConfig:
    """Build a config for the validation tests below.

    fs_isolation defaults ON, which makes delivery_type mandatory (inherited from
    ClaustrumConfigMixin); supply a default so these tests exercise the OTHER
    validators. The claustrum-triad contract itself is covered directly in
    test_claustrum_mixin.py. Callers may override delivery_type / fs_isolation."""
    kw.setdefault("delivery_type", "audit")
    return KimiCodeTaskConfig(**kw)


# --- happy path / defaults -------------------------------------------------


def test_minimal_construction_and_defaults():
    cfg = _cfg(consumer_instructions="do the thing")
    assert cfg.consumer_instructions == "do the thing"
    # kimi-specific / parity defaults
    assert cfg.mode == "iframe"
    assert cfg.auto_start is False
    assert cfg.thinking_verbosity == "hidden"
    assert cfg.tool_verbosity == "description-only"
    assert cfg.fs_isolation is True
    assert cfg.host_protocol is True
    assert cfg.supports_resume is True
    assert cfg.conversation_ui is False


def test_model_is_an_unvalidated_alias():
    # kimi models are aliases, not raw ids, and are NOT enum-validated.
    cfg = _cfg(consumer_instructions="x", model="kimi-k2-anything")
    assert cfg.model == "kimi-k2-anything"


# --- mode ------------------------------------------------------------------


def test_bad_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        _cfg(consumer_instructions="x", mode="tui")  # type: ignore[arg-type]


def test_valid_modes_accepted():
    for m in ("iframe", "conversation"):
        assert _cfg(consumer_instructions="x", mode=m).mode == m


# --- effort enum (kimi delta) ---------------------------------------------


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_valid_effort_accepted(effort):
    cfg = _cfg(consumer_instructions="x", effort=effort)
    assert cfg.effort == effort


def test_effort_none_ok():
    assert _cfg(consumer_instructions="x").effort is None


def test_bad_effort_rejected():
    with pytest.raises(ValueError, match="effort"):
        _cfg(consumer_instructions="x", effort="ultra")


# --- reasoning_effort enum (live graded thinking slider seed) ---------------


@pytest.mark.parametrize(
    "level", ["off", "low", "medium", "high", "xhigh", "max"]
)
def test_valid_reasoning_effort_accepted(level):
    cfg = _cfg(consumer_instructions="x", reasoning_effort=level)
    assert cfg.reasoning_effort == level


def test_reasoning_effort_none_ok():
    assert _cfg(consumer_instructions="x").reasoning_effort is None


def test_bad_reasoning_effort_rejected():
    with pytest.raises(ValueError, match="reasoning_effort"):
        _cfg(consumer_instructions="x", reasoning_effort="ultra")


def test_reasoning_effort_independent_of_effort():
    # the two effort fields are orthogonal: launch --effort has no 'off',
    # the live slider seed does.
    cfg = _cfg(
        consumer_instructions="x", effort="high", reasoning_effort="off"
    )
    assert cfg.effort == "high" and cfg.reasoning_effort == "off"


# --- permission_mode -------------------------------------------------------


def test_bad_permission_mode_rejected():
    # kimi's modes are yolo/manual/auto — claudecode values are NOT valid here.
    with pytest.raises(ValueError, match="permission_mode"):
        _cfg(consumer_instructions="x", permission_mode="bypassPermissions")  # type: ignore[arg-type]


def test_valid_permission_mode_accepted():
    cfg = _cfg(consumer_instructions="x", permission_mode="yolo")
    assert cfg.permission_mode == "yolo"


# --- conversation-only flags require mode=conversation ---------------------


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        _cfg(consumer_instructions="x", mode="iframe", conversation_ui=True)


def test_conversation_ui_ok_in_conversation_mode():
    cfg = _cfg(
        consumer_instructions="x", mode="conversation", conversation_ui=True
    )
    assert cfg.conversation_ui is True


def test_permission_gate_requires_conversation_mode():
    with pytest.raises(ValueError, match="permission_gate"):
        _cfg(consumer_instructions="x", mode="iframe", permission_gate=True)


def test_host_protocol_false_requires_conversation_mode():
    with pytest.raises(ValueError, match="host_protocol"):
        _cfg(consumer_instructions="x", mode="iframe", host_protocol=False)


def test_host_protocol_false_ok_in_conversation_mode():
    cfg = _cfg(
        consumer_instructions="x", mode="conversation", host_protocol=False
    )
    assert cfg.host_protocol is False


# --- single model field (C3: default_model dropped) ------------------------


def test_no_default_model_field():
    # C3: default_model was dropped; `model` is the single model field and is
    # valid in every mode (the conversation picker sources its initial value
    # from it — no separate conversation_ui gate).
    import dataclasses

    names = {f.name for f in dataclasses.fields(KimiCodeTaskConfig)}
    assert "default_model" not in names
    assert "model" in names


def test_model_valid_in_any_mode():
    for mode in ("iframe", "conversation"):
        cfg = _cfg(
            consumer_instructions="x", mode=mode, model="kimi-k2"
        )
        assert cfg.model == "kimi-k2"


# --- frontend-parity flags require conversation UI -------------------------


def test_show_session_controls_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_session_controls"):
        _cfg(
            consumer_instructions="x", mode="conversation", show_session_controls=True
        )


def test_native_spinner_requires_conversation_ui():
    with pytest.raises(ValueError, match="native_spinner"):
        _cfg(
            consumer_instructions="x", mode="conversation", native_spinner=True
        )


def test_show_file_upload_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_file_upload"):
        _cfg(
            consumer_instructions="x", mode="conversation", show_file_upload=True
        )


def test_file_download_requires_conversation_ui():
    with pytest.raises(ValueError, match="file_download"):
        _cfg(
            consumer_instructions="x", mode="conversation", file_download=True
        )


def test_frontend_parity_flags_ok_with_conversation_ui():
    cfg = _cfg(
        consumer_instructions="x",
        mode="conversation",
        conversation_ui=True,
        model="kimi-k2",
        show_session_controls=True,
        show_file_upload=True,
        file_download=True,
    )
    assert cfg.show_session_controls is True


# --- verbosity enums -------------------------------------------------------


def test_bad_tool_verbosity_rejected():
    with pytest.raises(ValueError, match="tool_verbosity"):
        _cfg(consumer_instructions="x", tool_verbosity="loud")  # type: ignore[arg-type]


def test_bad_thinking_verbosity_rejected():
    with pytest.raises(ValueError, match="thinking_verbosity"):
        _cfg(consumer_instructions="x", thinking_verbosity="loud")  # type: ignore[arg-type]


# --- AllowedDir / extra_allowed_dirs --------------------------------------


def test_allowed_dir_bad_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        AllowedDir(path="/data", mode="wx")  # type: ignore[arg-type]


def test_allowed_dir_valid_modes():
    # Shared 4-value superset (kimi is Landlock-only; rox≡ro, rwx≡rw).
    for m in ("ro", "rw", "rox", "rwx"):
        assert AllowedDir(path="/data", mode=m).mode == m


def test_extra_allowed_dirs_accepted():
    cfg = _cfg(
        consumer_instructions="x",
        extra_allowed_dirs=[
            AllowedDir(path="/opt/tools", mode="ro"),
            AllowedDir(path="/opt/venv", mode="rox"),
        ],
    )
    assert cfg.extra_allowed_dirs[0].path == "/opt/tools"
    assert cfg.extra_allowed_dirs[1].mode == "rox"


# --- at-rest session-blob cipher (both-or-none) ----------------------------


def _cipher(b: bytes) -> bytes:
    return b[::-1]


def test_session_blob_cipher_defaults_none():
    cfg = _cfg(consumer_instructions="x")
    assert cfg.session_blob_encrypt is None
    assert cfg.session_blob_decrypt is None


def test_session_blob_cipher_pair_accepted():
    cfg = _cfg(
        consumer_instructions="x",
        session_blob_encrypt=_cipher,
        session_blob_decrypt=_cipher,
    )
    assert cfg.session_blob_encrypt is _cipher
    assert cfg.session_blob_decrypt is _cipher


def test_session_blob_encrypt_without_decrypt_rejected():
    with pytest.raises(ValueError, match="session_blob"):
        _cfg(consumer_instructions="x", session_blob_encrypt=_cipher)


def test_session_blob_decrypt_without_encrypt_rejected():
    with pytest.raises(ValueError, match="session_blob"):
        _cfg(consumer_instructions="x", session_blob_decrypt=_cipher)


# --- install-dir override must be absolute (C2: renamed from kimi_install_dir)


def test_install_dir_must_be_absolute():
    with pytest.raises(ValueError, match="install_dir"):
        _cfg(consumer_instructions="x", install_dir="relative/path")


def test_install_dir_absolute_ok():
    cfg = _cfg(consumer_instructions="x", install_dir="/opt/kimi")
    assert cfg.install_dir == "/opt/kimi"


# --- C1: shared config vocabulary re-exported from types.py ----------------


def test_shared_aliases_reexported():
    # C1: the engine-neutral vocabulary is owned by optio_agents and re-exported
    # here so `from optio_kimicode.types import ...` sites keep working, and it
    # is the SAME object as the top-level optio_agents export.
    import optio_agents
    from optio_kimicode import types as kt

    assert kt.AllowedDir is optio_agents.AllowedDir
    assert kt.SeedProvider is optio_agents.SeedProvider
    assert kt.SeedUnavailableError is optio_agents.SeedUnavailableError
    assert kt.ConversationMode is optio_agents.ConversationMode
    assert kt.ToolVerbosity is optio_agents.ToolVerbosity
    assert kt.ThinkingVerbosity is optio_agents.ThinkingVerbosity


# --- P3: caller-message channel --------------------------------------------


def test_caller_message_fields_default_off():
    cfg = _cfg(consumer_instructions="x")
    assert cfg.use_client_messages is False
    assert cfg.on_caller_message is None


def test_caller_message_fields_settable():
    async def _cb(hook_ctx, message):  # pragma: no cover - identity callback
        return None

    cfg = _cfg(
        consumer_instructions="x",
        use_client_messages=True,
        on_caller_message=_cb,
    )
    assert cfg.use_client_messages is True
    assert cfg.on_caller_message is _cb


# --- P2: resume-refresh hook -----------------------------------------------


def test_on_resume_refresh_defaults_to_identity():
    from optio_kimicode.types import _identity_resume_refresh

    cfg = _cfg(consumer_instructions="x")
    assert cfg.on_resume_refresh is _identity_resume_refresh
    # identity returns the same config unchanged
    assert cfg.on_resume_refresh(cfg) is cfg


def test_on_resume_refresh_can_be_disabled():
    cfg = _cfg(consumer_instructions="x", on_resume_refresh=None)
    assert cfg.on_resume_refresh is None
