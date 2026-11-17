"""ClaudeCodeRequestyTaskConfig: the claudecode_requesty agent type."""

import dataclasses

import pytest

from optio_claudecode import (
    REQUESTY_SEED_SUFFIX,
    ClaudeCodeRequestyTaskConfig,
    create_claudecode_requesty_task,
)
from optio_claudecode.types import ClaudeCodeTaskConfig


def _cfg(**kw):
    return ClaudeCodeRequestyTaskConfig(
        consumer_instructions="x", fs_isolation=False, permission_mode="dontAsk", **kw,
    )


async def _provider(process_id: str) -> str:
    return "seed"


def test_defaults():
    cfg = _cfg()
    assert cfg.agent_type == "claudecode_requesty"
    assert cfg.mode == "conversation"
    assert cfg.gateway_provider == "requesty"
    assert cfg.gateway_seed_suffix == REQUESTY_SEED_SUFFIX == "_claudecode_requesty_seeds"
    assert isinstance(cfg, ClaudeCodeTaskConfig)
    assert ClaudeCodeTaskConfig.gateway_provider is None
    assert ClaudeCodeTaskConfig.gateway_seed_suffix is None
    # Class-level switches, not constructor fields: a plain config cannot be
    # put into gateway mode by passing them.
    names = {f.name for f in dataclasses.fields(cfg)}
    assert "gateway_provider" not in names
    assert "gateway_seed_suffix" not in names


def test_iframe_mode_is_refused():
    with pytest.raises(ValueError, match="conversation") as excinfo:
        _cfg(mode="iframe")
    assert "tmux command line" in str(excinfo.value)


@pytest.mark.parametrize(
    ("kw", "named"),
    [
        ({"credentials_json": {"a": 1}}, "credentials_json"),
        ({"on_seed_saved": lambda s, i: None}, "on_seed_saved"),
        ({"seed_id": _provider}, "seed_id"),
        ({"env": {"ANTHROPIC_BASE_URL": "x"}}, "ANTHROPIC_BASE_URL"),
        ({"claude_config": {"env": {"ANTHROPIC_BASE_URL": "x"}}}, "ANTHROPIC_BASE_URL"),
    ],
    ids=["credentials_json", "on_seed_saved", "seed_provider", "anthropic_env",
         "anthropic_settings_env"],
)
def test_oauth_inputs_are_refused(kw, named):
    with pytest.raises(ValueError, match=named):
        _cfg(**kw)


def test_the_env_refusal_names_no_value():
    with pytest.raises(ValueError) as excinfo:
        _cfg(env={"ANTHROPIC_AUTH_TOKEN": "sk-not-for-errors"})
    assert "ANTHROPIC_AUTH_TOKEN" in str(excinfo.value)
    assert "sk-not-for-errors" not in str(excinfo.value)


# The Claude Code settings a gateway session sets itself, and the OAuth token
# it must never get (ruling A).
_GATEWAY_CLAUDE_CODE_KEYS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
    "CLAUDE_CODE_AUTO_MODE_SERVER",
    "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT",
    "CLAUDE_CODE_EFFORT_LEVEL",
)


@pytest.mark.parametrize("key", _GATEWAY_CLAUDE_CODE_KEYS)
def test_env_cannot_undo_the_gateway_environment(key):
    with pytest.raises(ValueError) as excinfo:
        _cfg(env={key: "sk-not-for-errors"})
    assert key in str(excinfo.value)
    assert "sk-not-for-errors" not in str(excinfo.value)


@pytest.mark.parametrize("key", ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", *_GATEWAY_CLAUDE_CODE_KEYS])
def test_claude_config_env_cannot_undo_the_gateway_environment(key):
    # Claude Code applies settings.json `env` itself: ANTHROPIC_BASE_URL there
    # would send the token to another host.
    with pytest.raises(ValueError) as excinfo:
        _cfg(claude_config={"env": {"FOO": "bar", key: "https://sk-not-for-errors"}})
    assert "claude_config" in str(excinfo.value) and key in str(excinfo.value)
    assert "sk-not-for-errors" not in str(excinfo.value)
    assert "FOO" not in str(excinfo.value)


def test_a_seed_id_string_and_other_env_are_accepted():
    cfg = _cfg(seed_id="64b000000000000000000001", env={"FOO": "bar", "CLAUDE_CODE_NO_FLICKER": "1"},
               claude_config={"env": {"FOO": "bar"}, "model": "x"})
    assert cfg.seed_id == "64b000000000000000000001"
    assert _cfg(claude_config={"theme": "dark"}).claude_config == {"theme": "dark"}


def test_the_factory():
    task = create_claudecode_requesty_task("p", "n", _cfg(conversation_ui=True))
    assert task.ui_widget == "conversation"
    with pytest.raises(TypeError):
        create_claudecode_requesty_task(
            "p", "n", ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False),
        )
