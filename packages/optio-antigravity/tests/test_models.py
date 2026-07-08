"""Conversation-mode model list tests (Stage 7 Task 7.1).

File-disjoint units that don't need a live ``agy``:
  * ``models.py`` parse helper (the ``agy models`` CLI text);
  * ``fetch_available_models`` source precedence (CLI → static fallback);
  * ``AntigravityTaskConfig.show_session_controls`` validation.

Antigravity has NO live transport (design §1) — unlike grok there is no ACP
``session/new`` model block, so the model catalog's only live source is the
``agy models`` subcommand. The restart-based switch itself (set the next turn's
``--model``) is covered at the conversation level in test_conversation_controls.py.

TODO(S3): the exact ``agy models`` output format is pinned by the S3 transcript/
CLI spike (not yet run). ``parse_agy_models_text`` is written against the most
likely shape — one model id per line, optional ``(default)`` marker — and must
be reconciled once the real output is captured.
"""

import pytest

from optio_antigravity.models import (
    FALLBACK_MODELS,
    fetch_available_models,
    parse_agy_models_text,
)
from optio_antigravity.types import AntigravityTaskConfig


def _cfg(**kw):
    base = dict(consumer_instructions="do things", delivery_type="audit")
    base.update(kw)
    return AntigravityTaskConfig(**base)


# --- `agy models` CLI text -------------------------------------------------

# The fake agy's canned `agy models` output — one id per line (Gemini + BYO).
_AGY_MODELS_TEXT = """gemini-2.5-pro
gemini-2.5-flash
claude-sonnet-4
gpt-oss-120b
"""


def test_parse_agy_models_text_one_id_per_line():
    out = parse_agy_models_text(_AGY_MODELS_TEXT)
    assert [m["id"] for m in out["models"]] == [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "claude-sonnet-4",
        "gpt-oss-120b",
    ]
    assert all(m["disabled"] is False for m in out["models"])
    # No explicit default marker → the first listed id is the default.
    assert out["default"] == "gemini-2.5-pro"


def test_parse_agy_models_text_honors_default_marker():
    text = "gemini-2.5-pro\ngemini-2.5-flash (default)\nclaude-sonnet-4\n"
    out = parse_agy_models_text(text)
    assert [m["id"] for m in out["models"]] == [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "claude-sonnet-4",
    ]
    assert out["default"] == "gemini-2.5-flash"


def test_parse_agy_models_text_empty_is_empty_list():
    out = parse_agy_models_text("")
    assert out["models"] == []
    assert out["default"] is None


# --- source precedence -----------------------------------------------------


class _FakeHost:
    def __init__(self, text, exit_code=0):
        self._text = text
        self._exit = exit_code
        self.calls = []

    async def run_command(self, cmd):
        self.calls.append(cmd)

        class R:
            pass

        r = R()
        r.exit_code = self._exit
        r.stdout = self._text
        return r


@pytest.mark.asyncio
async def test_fetch_uses_agy_models_cli():
    host = _FakeHost(_AGY_MODELS_TEXT)
    out = await fetch_available_models(host=host, agy_path="/bin/agy")
    assert [m["id"] for m in out["models"]] == [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "claude-sonnet-4",
        "gpt-oss-120b",
    ]
    assert host.calls and "models" in host.calls[0]


@pytest.mark.asyncio
async def test_fetch_falls_back_to_static_list_without_source():
    out = await fetch_available_models()
    assert out == FALLBACK_MODELS


@pytest.mark.asyncio
async def test_fetch_falls_back_when_cli_fails():
    host = _FakeHost("boom", exit_code=1)
    out = await fetch_available_models(host=host, agy_path="/bin/agy")
    assert out == FALLBACK_MODELS


@pytest.mark.asyncio
async def test_fetch_falls_back_when_cli_lists_nothing():
    host = _FakeHost("", exit_code=0)
    out = await fetch_available_models(host=host, agy_path="/bin/agy")
    assert out == FALLBACK_MODELS


# --- config validation -----------------------------------------------------


def test_show_session_controls_requires_conversation_ui():
    with pytest.raises(ValueError, match="show_session_controls"):
        _cfg(mode="conversation", conversation_ui=False, show_session_controls=True)


def test_show_session_controls_ok_in_conversation_ui():
    cfg = _cfg(mode="conversation", conversation_ui=True, show_session_controls=True)
    assert cfg.show_session_controls is True
