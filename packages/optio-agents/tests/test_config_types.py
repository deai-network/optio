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


# --- WelcomeConfigMixin: the card atop the conversation transcript ----------

def _welcome_config():
    from dataclasses import dataclass
    from optio_agents import WelcomeConfigMixin

    @dataclass(frozen=True, kw_only=True)
    class _EngineConfig(WelcomeConfigMixin):
        consumer_instructions: str

        def __post_init__(self):
            self._validate_welcome()

    return _EngineConfig


def test_welcome_mixin_defaults_to_no_card():
    cfg = _welcome_config()(consumer_instructions="x")
    assert cfg.welcome is None
    assert cfg.welcome_widget_data is None


def test_welcome_card_needs_only_a_title():
    from optio_agents import WelcomeCard
    card = WelcomeCard(title="Hi")
    assert card.description is None and card.icon is None and card.min_height is None
    # None values stay None (null in widgetData).
    assert card.to_widget_data() == {
        "title": "Hi", "description": None, "icon": None, "minHeight": None,
    }


@pytest.mark.parametrize("title", [
    "", "   ", "\n\t", {}, {"en": ""}, {"en": "Hi", "hu": "  "}, {"": "Hi"}, 5, None,
], ids=["empty", "spaces", "whitespace", "empty-map", "empty-value", "blank-value",
        "empty-language", "int", "none"])
def test_welcome_card_rejects_a_blank_or_malformed_title(title):
    from optio_agents import WelcomeCard
    with pytest.raises(ValueError, match="WelcomeCard.title"):
        WelcomeCard(title=title)


@pytest.mark.parametrize("description", ["", {}, {"en": ""}, 5])
def test_welcome_card_rejects_a_malformed_description(description):
    from optio_agents import WelcomeCard
    with pytest.raises(ValueError, match="WelcomeCard.description"):
        WelcomeCard(title="Hi", description=description)


def test_welcome_texts_may_be_language_maps():
    # Published unresolved: the widget picks the language.
    from optio_agents import WelcomeCard
    title = {"en": "Hello", "hu": "Szia", "de": "Hallo"}
    card = WelcomeCard(title=title, description={"en": "Try **this**.", "hu": "Próbáld **ezt**."})
    title["en"] = "Changed"  # the card keeps its own copy
    assert card.to_widget_data() == {
        "title": {"en": "Hello", "hu": "Szia", "de": "Hallo"},
        "description": {"en": "Try **this**.", "hu": "Próbáld **ezt**."},
        "icon": None,
        "minHeight": None,
    }


def test_welcome_card_rejects_a_blank_icon():
    from optio_agents import WelcomeCard
    with pytest.raises(ValueError, match="icon"):
        WelcomeCard(title="Hi", icon=" ")


@pytest.mark.parametrize("icon, published", [
    # Relative to the web app root: a bare path gets the leading "/".
    ("img/a.png", "/img/a.png"),
    ("welcome.png", "/welcome.png"),
    # Already rooted, or absolute: published as given.
    ("/img/a.png", "/img/a.png"),
    ("https://cdn.example.com/a.png", "https://cdn.example.com/a.png"),
])
def test_welcome_icon_is_relative_to_the_web_app_root(icon, published):
    from optio_agents import WelcomeCard
    assert WelcomeCard(title="Hi", icon=icon).to_widget_data()["icon"] == published


def test_welcome_min_height_is_published_in_css_pixels():
    from optio_agents import WelcomeCard
    assert WelcomeCard(title="Hi", min_height=200).to_widget_data()["minHeight"] == 200


@pytest.mark.parametrize("min_height", [0, -5, True, False, 1.5, "200"])
def test_welcome_min_height_must_be_a_positive_int(min_height):
    from optio_agents import WelcomeCard
    with pytest.raises(ValueError, match="WelcomeCard.min_height"):
        WelcomeCard(title="Hi", min_height=min_height)


def test_welcome_widget_data_carries_the_card():
    from optio_agents import WelcomeCard
    cfg = _welcome_config()(
        consumer_instructions="x",
        welcome=WelcomeCard(title="Hi", description="**x**", icon="img/a.png", min_height=120),
    )
    assert cfg.welcome_widget_data == {
        "title": "Hi", "description": "**x**", "icon": "/img/a.png", "minHeight": 120,
    }


def test_the_mixin_rejects_a_welcome_that_is_not_a_card():
    cfg = _welcome_config()
    with pytest.raises(ValueError, match="_EngineConfig.*welcome"):
        cfg(consumer_instructions="x", welcome="nope")
    with pytest.raises(ValueError, match="welcome"):
        cfg(consumer_instructions="x", welcome={"title": "Hi"})
# --- ResumeConfigMixin: how a suspended session resumes, shared by engines -----

def _resume_config():
    from dataclasses import dataclass
    from optio_agents import ResumeConfigMixin

    @dataclass(frozen=True, kw_only=True)
    class _EngineConfig(ResumeConfigMixin):
        consumer_instructions: str

    return _EngineConfig


def test_resume_mixin_is_exported_at_the_top_level():
    import optio_agents
    from optio_agents.config_types import ResumeConfigMixin

    assert optio_agents.ResumeConfigMixin is ResumeConfigMixin
    assert "ResumeConfigMixin" in optio_agents.__all__


def test_smooth_resume_is_off_by_default():
    # A message to a suspended session asks before it resumes it.
    assert _resume_config()(consumer_instructions="x").smooth_resume is False


def test_smooth_resume_can_be_turned_on():
    cfg = _resume_config()(consumer_instructions="x", smooth_resume=True)
    assert cfg.smooth_resume is True
