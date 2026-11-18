"""Deliverable metadata wiring: the driver gets the base entries.

``run_codex_session`` must hand ``run_log_protocol_session`` a
``deliverable_metadata`` provider that returns the agent type and the agent's
name. Pure wiring test: ``_build_host``, ``get_protocol`` and
``run_log_protocol_session`` are stubbed so nothing is spawned and no MongoDB is
needed. With the driver stubbed, the outer shell awaits only ``host.connect``
and, in its ``finally``, ``host.cleanup_taskdir`` and ``host.disconnect``.
"""

from __future__ import annotations

from unittest.mock import patch

from optio_agents import KEY_AGENT_NAME, KEY_AGENT_TYPE, SOURCE_CONFIG
from optio_agents.deliverable_meta import entry
from optio_codex import CodexTaskConfig
from optio_codex import session as codex_session
from optio_codex.info import AGENT_INFO


class _FakeHost:
    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def cleanup_taskdir(self, aggressive=False):
        pass


class _FakeCtx:
    process_id = "p"

    def should_continue(self):
        return True


async def _capture_wiring(config):
    """Run run_codex_session with stubs; return (protocol_kwargs, run_kwargs)."""
    captured: dict = {}

    def _fake_get_protocol(**kwargs):
        captured["protocol"] = kwargs
        return object()  # opaque; run_log is stubbed so it is never inspected

    async def _fake_run_log(host, ctx, **kwargs):
        captured["run"] = kwargs

    with patch.object(codex_session, "_build_host", lambda c, pid: _FakeHost()), \
         patch.object(codex_session, "get_protocol", _fake_get_protocol), \
         patch.object(codex_session, "run_log_protocol_session", _fake_run_log):
        await codex_session.run_codex_session(_FakeCtx(), config)
    return captured["protocol"], captured["run"]


async def test_the_driver_gets_the_base_deliverable_entries():                   # RF5
    config = CodexTaskConfig(consumer_instructions="x", delivery_type="audit")
    _proto, run = await _capture_wiring(config)
    assert run["deliverable_metadata"]() == [
        entry(KEY_AGENT_TYPE, config.agent_type, SOURCE_CONFIG),
        entry(KEY_AGENT_NAME, AGENT_INFO.name, SOURCE_CONFIG),
    ]
    assert config.agent_type == AGENT_INFO.slug
