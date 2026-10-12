"""Localized text in agent configuration.

A ``Text`` is a plain string, or a map of language code (e.g. "en", "hu",
"de") to a non-empty string. Configuration keeps it unresolved and publishes
it as is (a map as a plain dict), so the viewer picks the language;
``resolve_text`` is that pick: the language's entry, else "en", else the
first entry. ``check_text`` validates a configured value."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# A plain string, or a map of language code (e.g. "en", "hu", "de") to a
# non-empty string.
Text = str | Mapping[str, str]


def check_text(value: Any, *, what: str) -> Text:
    """Return ``value`` after checking it is a non-empty string or a non-empty
    map of language code to non-empty string (a map is copied into a plain
    dict, so the caller's map cannot change a frozen definition). Raise
    ValueError naming ``what`` otherwise."""
    if isinstance(value, str):
        if not value:
            raise ValueError(f"{what} must not be empty.")
        return value
    if isinstance(value, Mapping):
        if not value:
            raise ValueError(f"{what} must have at least one language entry.")
        for language, text in value.items():
            if not isinstance(language, str) or not language:
                raise ValueError(
                    f"{what}: language codes must be non-empty strings, got {language!r}."
                )
            if not isinstance(text, str) or not text:
                raise ValueError(f"{what}[{language!r}] must be a non-empty string.")
        return dict(value)
    raise ValueError(
        f"{what} must be a string or a map of language code to string, "
        f"got {type(value).__name__}."
    )


def resolve_text(text: Text, language: str | None) -> str:
    """``text`` in ``language``: a plain string as is; from a language map, the
    language's entry, else the ``en`` entry, else the first entry."""
    if isinstance(text, str):
        return text
    if language is not None and language in text:
        return text[language]
    if "en" in text:
        return text["en"]
    for first in text.values():
        return first
    raise ValueError("resolve_text: an empty language map has no text.")
