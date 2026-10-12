"""Localized text shared by agent configuration (``optio_agents.text``): a
plain string or a map of language code to string, its validation
(``check_text``) and its resolution for one language (``resolve_text``)."""
from types import MappingProxyType

import pytest

import optio_agents
from optio_agents import check_text, resolve_text


def test_top_level_exports():
    for name in ("Text", "check_text", "resolve_text"):
        assert hasattr(optio_agents, name), name
        assert name in optio_agents.__all__, name


# --- resolve_text ----------------------------------------------------------------

def test_resolve_a_plain_string_in_any_language():
    assert resolve_text("Fast", "hu") == "Fast"
    assert resolve_text("Fast", None) == "Fast"


def test_resolve_the_languages_entry():
    assert resolve_text({"en": "Model", "hu": "Modell"}, "hu") == "Modell"


def test_resolve_falls_back_to_en():
    assert resolve_text({"hu": "Modell", "en": "Model"}, "fr") == "Model"
    assert resolve_text({"hu": "Modell", "en": "Model"}, None) == "Model"


def test_resolve_falls_back_to_the_first_entry_without_en():
    assert resolve_text({"hu": "Modell", "de": "Modell (de)"}, "fr") == "Modell"


def test_resolve_an_empty_map_raises():
    with pytest.raises(ValueError, match="empty language map"):
        resolve_text({}, "en")


# --- check_text ------------------------------------------------------------------

def test_a_plain_string_is_returned_as_is():
    assert check_text("Fast", what="label") == "Fast"


def test_a_language_map_comes_back_as_a_plain_dict_copy():
    labels = {"en": "Fast", "hu": "Gyors"}
    checked = check_text(labels, what="label")
    assert checked == {"en": "Fast", "hu": "Gyors"}
    labels["en"] = "Changed"
    assert checked == {"en": "Fast", "hu": "Gyors"}
    proxied = check_text(MappingProxyType({"en": "Fast"}), what="label")
    assert type(proxied) is dict and proxied == {"en": "Fast"}


@pytest.mark.parametrize("value, message", [
    ("", "label must not be empty"),
    ({}, "label must have at least one language entry"),
    ({"en": ""}, "label\\['en'\\] must be a non-empty string"),
    ({"": "Fast"}, "label: language codes must be non-empty strings"),
    ({1: "Fast"}, "label: language codes must be non-empty strings"),
    ({"en": 5}, "label\\['en'\\] must be a non-empty string"),
    (5, "label must be a string or a map of language code to string, got int"),
    (None, "label must be a string or a map of language code to string, got NoneType"),
], ids=["empty-str", "empty-map", "empty-value", "empty-key", "non-str-key",
        "non-str-value", "int", "none"])
def test_bad_text_is_refused_naming_what(value, message):
    with pytest.raises(ValueError, match=message):
        check_text(value, what="label")
