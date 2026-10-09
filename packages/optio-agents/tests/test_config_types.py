import pytest
from optio_agents import (AllowedDir, ConversationMode, SeedProvider,
                          SeedUnavailableError, ThinkingVerbosity, ToolVerbosity)

def test_alloweddir_accepts_superset_and_rejects_junk():
    for m in ("ro", "rw", "rox", "rwx"):
        assert AllowedDir("/w", m).mode == m
    with pytest.raises(ValueError):
        AllowedDir("/w", "wx")

def test_aliases_importable_from_top_level():
    # smoke: the Literals/aliases are exported for wrappers to import
    assert SeedProvider is not None and issubclass(SeedUnavailableError, Exception)


# --- SessionControlsConfigMixin: the session-controls bar, shared by engines --

def _controls_config():
    from dataclasses import dataclass
    from optio_agents.config_types import SessionControlsConfigMixin

    @dataclass(frozen=True, kw_only=True)
    class _EngineConfig(SessionControlsConfigMixin):
        consumer_instructions: str

        def __post_init__(self):
            self._validate_session_controls()

    return _EngineConfig


def test_session_controls_mixin_defaults_hide_the_bar():
    cfg = _controls_config()(consumer_instructions="x")
    assert cfg.show_session_controls is False and cfg.session_controls is None


def test_settable_controls_is_empty_while_the_bar_is_off():
    # Nothing is settable through /control when the widget shows no controls.
    assert _controls_config()(consumer_instructions="x").settable_controls == []


def test_settable_controls_is_every_control_or_the_allowlist():
    cfg = _controls_config()
    assert cfg(consumer_instructions="x", show_session_controls=True).settable_controls is None
    assert cfg(consumer_instructions="x", show_session_controls=True,
               session_controls=["model"]).settable_controls == ["model"]


def test_the_mixin_validates_its_allowlist():
    cfg = _controls_config()
    with pytest.raises(ValueError, match="_EngineConfig.*session_controls"):
        cfg(consumer_instructions="x", session_controls=["model"])
    with pytest.raises(ValueError, match="session_controls"):
        cfg(consumer_instructions="x", show_session_controls=True, session_controls="model")
