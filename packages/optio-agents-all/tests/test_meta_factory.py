from typing import get_args

import pytest

import optio_agents_all as aa
from optio_agents_all.factory import _REGISTRY
from optio_agents_all.types import AgentType


def test_every_slug_registered_and_in_union():
    slugs = set(get_args(AgentType))
    assert set(_REGISTRY) == slugs
    # union covers exactly the 7 configs
    union_types = {t.__name__ for t in get_args(aa.AgentTaskConfig)}
    assert len(union_types) == 7


def test_create_task_dispatches_by_agent_type(monkeypatch):
    called = {}
    for slug in _REGISTRY:
        monkeypatch.setitem(
            _REGISTRY,
            slug,
            lambda p, n, c, description=None, metadata=None, _s=slug: called.setdefault(
                "hit", _s
            ),
        )
    cfg = aa.KimiCodeTaskConfig(consumer_instructions="x")
    aa.create_task("pid", "nm", cfg)
    assert called["hit"] == "kimicode"


def test_unknown_agent_type_raises():
    cfg = aa.GrokTaskConfig(consumer_instructions="x")
    object.__setattr__(cfg, "agent_type", "bogus")
    with pytest.raises(ValueError):
        aa.create_task("pid", "nm", cfg)


def test_import_surface():
    for name in (
        "create_task",
        "AgentTaskConfig",
        "AgentType",
        "KimiCodeTaskConfig",
        "GrokTaskConfig",
        "CursorTaskConfig",
        "ClaudeCodeTaskConfig",
        "CodexTaskConfig",
        "OpencodeTaskConfig",
        "AntigravityTaskConfig",
        "create_kimicode_task",
        "create_grok_task",
        "create_cursor_task",
        "create_claudecode_task",
        "create_codex_task",
        "create_opencode_task",
        "create_antigravity_task",
    ):
        assert hasattr(aa, name), name


def test_agent_type_defaults_per_engine():
    assert aa.KimiCodeTaskConfig(consumer_instructions="x").agent_type == "kimicode"
    assert aa.GrokTaskConfig(consumer_instructions="x").agent_type == "grok"
    assert (
        aa.AntigravityTaskConfig(consumer_instructions="x").agent_type == "antigravity"
    )
