import dataclasses

import pytest

from optio_agents import AllowedDir as SharedAllowedDir, get_protocol

from optio_grok import GrokTaskConfig


# fs_isolation defaults ON (claustrum), which makes delivery_type mandatory;
# the config-shape tests below are not about isolation, so they pass a
# delivery_type to satisfy the shared ClaustrumConfigMixin validation and reach
# the engine-specific checks under test.
def _mk(**kw):
    kw.setdefault("consumer_instructions", "x")
    kw.setdefault("delivery_type", "audit")
    return GrokTaskConfig(**kw)


def test_defaults_and_validation():
    c = _mk(consumer_instructions="do it")
    assert c.mode == "iframe" and c.no_leader is True and c.host_protocol is True
    assert c.reasoning_effort is None
    with pytest.raises(ValueError):
        _mk(permission_mode="nope")


def test_reasoning_effort_validation():
    # Graded reasoning-effort is a validated Literal (unlike the free-form
    # ``effort`` passthrough, which stays unvalidated).
    for level in ("low", "medium", "high", "xhigh"):
        assert _mk(reasoning_effort=level).reasoning_effort == level
    with pytest.raises(ValueError, match="reasoning_effort"):
        _mk(reasoning_effort="ludicrous")
    # The free-form ``effort`` field remains unvalidated (untouched by Spec B).
    assert _mk(effort="whatever").effort == "whatever"


# --- claustrum triad (inherited from ClaustrumConfigMixin) -------------------


def test_fs_isolation_defaults_on_and_requires_delivery_type():
    # Default-ON claustrum: delivery_type is mandatory.
    with pytest.raises(ValueError, match="delivery_type"):
        GrokTaskConfig(consumer_instructions="x")
    c = GrokTaskConfig(consumer_instructions="x", delivery_type="audit")
    assert c.fs_isolation is True and c.delivery_type == "audit"
    # Opting out drops the requirement.
    off = GrokTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert off.fs_isolation is False and off.delivery_type is None


# --- C1: shared config vocabulary is re-exported from optio_grok.types -------


def test_shared_aliases_reexported_and_identical():
    from optio_grok.types import (
        AllowedDir,
        ConversationMode,
        SeedProvider,
        SeedUnavailableError,
        ThinkingVerbosity,
        ToolVerbosity,
    )

    # The re-export is the very object owned by optio_agents (not a local copy).
    assert AllowedDir is SharedAllowedDir
    assert issubclass(SeedUnavailableError, Exception)
    # Landlock-only grok accepts the 4-value superset; execute variants fold.
    assert AllowedDir("/x", "rwx").mode == "rwx"
    assert ConversationMode is not None
    assert ToolVerbosity is not None and ThinkingVerbosity is not None
    assert SeedProvider is not None


# --- C2 / C3: install_dir rename + single model field ------------------------


def test_config_field_surface_renames_and_drops():
    names = {f.name for f in dataclasses.fields(GrokTaskConfig)}
    assert "install_dir" in names
    assert "grok_install_dir" not in names
    assert "default_model" not in names
    assert "model" in names
    # The claustrum triad is inherited (top-level, no nesting).
    assert {"fs_isolation", "extra_allowed_dirs", "delivery_type"} <= names


def test_install_dir_absolute_path_validation():
    with pytest.raises(ValueError, match="install_dir"):
        _mk(install_dir="relative/dir")
    c = _mk(install_dir="/opt/grok/bin")
    assert c.install_dir == "/opt/grok/bin"


def test_extra_allowed_dirs_accepts_superset_modes():
    c = _mk(
        extra_allowed_dirs=[SharedAllowedDir("/a", "rox"), SharedAllowedDir("/b", "rwx")],
    )
    assert [d.mode for d in c.extra_allowed_dirs] == ["rox", "rwx"]


# --- P1: session-blob encryption pairing -------------------------------------


def test_new_core_field_defaults():
    c = _mk()
    assert c.session_blob_encrypt is None and c.session_blob_decrypt is None
    assert c.on_resume_refresh is not None  # identity default, not None
    assert c.use_client_messages is False
    assert c.on_caller_message is None


def test_session_blob_transforms_must_be_paired():
    with pytest.raises(ValueError, match="session_blob"):
        _mk(session_blob_encrypt=lambda b: b)
    with pytest.raises(ValueError, match="session_blob"):
        _mk(session_blob_decrypt=lambda b: b)
    c = _mk(
        session_blob_encrypt=lambda b: b,
        session_blob_decrypt=lambda b: b,
    )
    assert c.session_blob_encrypt is not None and c.session_blob_decrypt is not None


# --- P3: caller-message channel drives the protocol build --------------------


def test_caller_message_toggles_protocol_feature():
    # The exact idiom run_grok_session uses to build the protocol.
    cfg_on = _mk(on_caller_message=lambda *a: None)
    proto_on = get_protocol(
        browser="redirect",
        client_messages=cfg_on.use_client_messages,
        caller_messages=cfg_on.on_caller_message is not None,
    )
    assert proto_on.features.caller_messages is True

    cfg_off = _mk()
    proto_off = get_protocol(
        browser="redirect",
        client_messages=cfg_off.use_client_messages,
        caller_messages=cfg_off.on_caller_message is not None,
    )
    assert proto_off.features.caller_messages is False
