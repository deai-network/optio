"""Deliverable metadata from a live Claude Code session, native and through a
gateway (Claude Code + Requesty).

Real engine and claude shim, as in test_gateway_session.py. After one turn
(so the stream's system/init has been routed) the test writes a deliverable
and announces it in optio.log; the on_deliverable callback's metadata names
the agent, the model the CLI runs and its label and, under a gateway, the
seed's provider and region, never a secret (RF1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from dataclasses import replace

import pytest

from optio_agents import (
    KEY_AGENT_NAME,
    KEY_AGENT_SESSION,
    KEY_AGENT_TYPE,
    KEY_AGENT_VERSION,
    KEY_GATEWAY_REGION,
    KEY_MODEL,
    KEY_MODEL_LABEL,
    KEY_ORIGIN,
    KEY_PROVIDER,
    KEY_RESUMED,
    SOURCE_AGENT,
    SOURCE_OPTIO,
    meta_get,
)
from optio_claudecode import create_claudecode_task

from .test_conversation_ui_session import (
    _make_optio,
    _remember_workdir,
    _replies,
    _ui_config,
    _wait_terminal,
    _wait_widget_data,
)
from .test_gateway_session import GLM, KIMI, TOKEN, _gw_task

# Live sessions sharing the claude cache paths: the non-parallel phase.
pytestmark = pytest.mark.serial


def _deliver(workdir: str, rel: str) -> None:
    """Write ``<workdir>/deliverables/<rel>`` and announce it in optio.log."""
    path = pathlib.Path(workdir, "deliverables", rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# a recipe\n", encoding="utf-8")
    with open(pathlib.Path(workdir, "optio.log"), "a", encoding="utf-8") as fh:
        fh.write(f"DELIVERABLE: ./deliverables/{rel}\n")


def _collect(into: asyncio.Queue):
    """on_deliverable taking the metadata: queues ``(path, metadata)``."""
    async def on_deliverable(hook_ctx, path, text, metadata=None):
        await into.put((path, metadata))
    return on_deliverable


async def _deliver_after_a_turn(optio, pid: str, conv, seen: dict, delivered: asyncio.Queue):
    """One turn, then one deliverable: its ``(path, metadata)``."""
    # widgetData is written once the model list is in (natively asked of
    # claude after publish_result): the label needs it.
    await _wait_widget_data(optio, pid)
    _texts, replied = _replies(conv)
    await conv.send("ping")
    await asyncio.wait_for(replied.wait(), 60)
    _deliver(seen["workdir"], "recipes/r.md")
    return await asyncio.wait_for(delivered.get(), 60)


@pytest.mark.asyncio
async def test_a_gateway_deliverable_carries_the_seeds_label_and_no_secret(       # RF1
        shim_install_dir, claude_cache_dir, task_root, mongo_db, caplog):
    caplog.set_level(logging.DEBUG)
    # The driver's own command log (binary payloads base64'd) is not ours.
    caplog.set_level(logging.WARNING, logger="pymongo")
    seen: dict = {}
    delivered: asyncio.Queue = asyncio.Queue()
    pid = "gw-meta"
    optio = await _make_optio(mongo_db, "gwmeta")
    try:
        task = await _gw_task(
            mongo_db, "gwmeta", pid, shim_install_dir, claude_cache_dir,
            models=(replace(KIMI, label="Kimi K3.1 Pro"), GLM),
            on_deliverable=_collect(delivered), before_execute=_remember_workdir(seen),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        path, md = await _deliver_after_a_turn(optio, pid, conv, seen, delivered)

        assert path == "recipes/r.md"
        assert meta_get(md, KEY_MODEL) == KIMI.id == meta_get(md, KEY_MODEL, prefer=(SOURCE_OPTIO,))
        assert meta_get(md, KEY_MODEL_LABEL) == "Kimi K3.1 Pro"
        assert (meta_get(md, KEY_PROVIDER), meta_get(md, KEY_GATEWAY_REGION)) == ("requesty", "eu")
        assert (meta_get(md, KEY_AGENT_VERSION), meta_get(md, KEY_AGENT_SESSION)) == ("2.1.326", "fake-session-0000")
        assert (meta_get(md, KEY_ORIGIN), meta_get(md, KEY_RESUMED)) == ("agent", False)
        dumped = json.dumps(md)
        for secret in (TOKEN, TOKEN[-4:], "https://router.eu.requesty.ai", "ANTHROPIC_"):
            assert secret not in dumped

        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)
    assert TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_a_native_deliverable_names_the_model_the_cli_runs(
        shim_install_dir, claude_cache_dir, task_root, mongo_db):
    seen: dict = {}
    delivered: asyncio.Queue = asyncio.Queue()
    pid = "cc-meta"
    optio = await _make_optio(mongo_db, "ccmeta")
    try:
        task = create_claudecode_task(
            process_id=pid, name="Deliverable metadata",
            config=_ui_config(shim_install_dir, claude_cache_dir,
                              on_deliverable=_collect(delivered), before_execute=_remember_workdir(seen)),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        path, md = await _deliver_after_a_turn(optio, pid, conv, seen, delivered)

        assert path == "recipes/r.md"
        assert (meta_get(md, KEY_AGENT_TYPE), meta_get(md, KEY_AGENT_NAME)) == ("claudecode", "Claude Code")
        assert meta_get(md, KEY_MODEL, prefer=(SOURCE_AGENT,)) == "claude-opus-5[1m]"   # the fake's default
        assert meta_get(md, KEY_MODEL_LABEL) == "Opus (1M context)"
        assert meta_get(md, KEY_AGENT_VERSION) == "2.1.326"
        assert not {KEY_PROVIDER, KEY_GATEWAY_REGION} & {e["key"] for e in md}

        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)
