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
    ],
    ids=["credentials_json", "on_seed_saved", "seed_provider", "anthropic_env"],
)
def test_oauth_inputs_are_refused(kw, named):
    with pytest.raises(ValueError, match=named):
        _cfg(**kw)


def test_the_env_refusal_names_no_value():
    with pytest.raises(ValueError) as excinfo:
        _cfg(env={"ANTHROPIC_AUTH_TOKEN": "sk-not-for-errors"})
    assert "ANTHROPIC_AUTH_TOKEN" in str(excinfo.value)
    assert "sk-not-for-errors" not in str(excinfo.value)


def test_a_seed_id_string_and_other_env_are_accepted():
    cfg = _cfg(seed_id="64b000000000000000000001", env={"FOO": "bar"})
    assert cfg.seed_id == "64b000000000000000000001"


def test_the_factory():
    task = create_claudecode_requesty_task("p", "n", _cfg(conversation_ui=True))
    assert task.ui_widget == "conversation"
    with pytest.raises(TypeError):
        create_claudecode_requesty_task(
            "p", "n", ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False),
        )
