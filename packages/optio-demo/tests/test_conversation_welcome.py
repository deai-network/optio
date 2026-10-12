"""Every conversation-UI demo task greets the operator with a welcome card.

Pure: each engine module's ``get_tasks`` runs against a stub db holding one
seed (and a stub ``list_seeds``), with ``create_task`` replaced by a recorder,
so the configs the demo builds can be inspected without Mongo."""
from __future__ import annotations

import asyncio
import importlib
import types

import pytest

from optio_agents import WelcomeCard

ENGINES = ["claudecode", "codex", "cursor", "grok", "kimicode", "opencode", "antigravity"]
ICON = "/welcome/optio.png"  # served from optio-dashboard's public/


class _OneSeed:
    """A demo seed sidecar collection holding one named seed."""

    def find(self, *args, **kwargs):
        async def _records():
            yield {"seedId": "s1", "name": "Config #1"}
        return _records()


class _Db:
    def __getitem__(self, name):
        return _OneSeed()


def _configs(engine: str, monkeypatch) -> list[tuple[str, object]]:
    """(process_id, config) of every task the engine's demo module builds."""
    mod = importlib.import_module(f"optio_demo.tasks.{engine}")
    built: list[tuple[str, object]] = []
    monkeypatch.setattr(
        mod, "create_task",
        lambda *, process_id, config, **kw: built.append((process_id, config)),
    )
    if hasattr(mod, "list_seeds"):
        async def _list_seeds(store):
            return [{"seedId": "s1"}]
        monkeypatch.setattr(mod, "list_seeds", _list_seeds)
    services = {"db": _Db(), "prefix": "t",
                "optio": types.SimpleNamespace(mongo_store=None)}
    asyncio.run(mod.get_tasks(services))
    return built


@pytest.mark.parametrize("engine", ENGINES)
def test_every_conversation_ui_task_has_a_welcome_card(engine, monkeypatch):
    built = _configs(engine, monkeypatch)
    conversation = [(pid, c) for pid, c in built if c.conversation_ui]
    assert conversation, f"{engine}: no conversation_ui demo task"
    for pid, config in conversation:
        assert isinstance(config.welcome, WelcomeCard), pid
        assert config.welcome.description, pid
        assert config.welcome.icon == ICON, pid
    # The card is a conversation-widget feature: the other tasks have none.
    for pid, config in built:
        if not config.conversation_ui:
            assert config.welcome is None, pid


def test_the_demo_shows_a_language_map_title_and_a_tall_card(monkeypatch):
    cards = [config.welcome for engine in ENGINES
             for _, config in _configs(engine, monkeypatch) if config.welcome]
    assert any(isinstance(card.title, dict) and {"en", "hu", "de"} <= set(card.title)
               for card in cards)
    assert any(card.min_height == 200 for card in cards)
