"""Caller-message channel wiring (harmonization P3).

``use_client_messages`` / ``on_caller_message`` must flow into the protocol
build (``get_protocol(client_messages=..., caller_messages=...)``) and the
``on_caller_message`` callback must reach ``run_log_protocol_session``. Pure
wiring test: ``_build_host``, ``get_protocol`` and ``run_log_protocol_session``
are stubbed so nothing is spawned and no MongoDB is needed.
"""

from __future__ import annotations

from unittest.mock import patch

from optio_cursor import CursorTaskConfig
from optio_cursor import session as cursor_session


class _FakeHost:
    workdir = "/tmp/cursor-wiring-test"

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def run_command(self, cmd, **kw):
        return None

    async def cleanup_taskdir(self, aggressive=False):
        pass


class _FakeCtx:
    process_id = "p"
    resume = False

    def should_continue(self):
        return True

    def report_progress(self, *a, **k):
        pass


async def _capture_wiring(config):
    """Run run_cursor_session with stubs; return (protocol_kwargs, run_kwargs)."""
    captured: dict = {}

    def _fake_get_protocol(**kwargs):
        captured["protocol"] = kwargs
        return object()  # opaque; run_log is stubbed so it is never inspected

    async def _fake_run_log(host, ctx, **kwargs):
        captured["run"] = kwargs

    with patch.object(cursor_session, "_build_host", lambda c, pid: _FakeHost()), \
         patch.object(cursor_session, "get_protocol", _fake_get_protocol), \
         patch.object(cursor_session, "run_log_protocol_session", _fake_run_log):
        await cursor_session.run_cursor_session(_FakeCtx(), config)
    return captured["protocol"], captured["run"]


async def test_caller_message_flags_flow_through():
    async def _cb(hook_ctx, text, meta):
        return None

    proto, run = await _capture_wiring(
        CursorTaskConfig(
            consumer_instructions="x",
            use_client_messages=True,
            on_caller_message=_cb,
            delivery_type="audit",
        )
    )
    assert proto["client_messages"] is True
    assert proto["caller_messages"] is True
    assert run["on_caller_message"] is _cb


async def test_caller_message_defaults_off():
    proto, run = await _capture_wiring(
        CursorTaskConfig(consumer_instructions="x", delivery_type="audit")
    )
    assert proto["client_messages"] is False
    assert proto["caller_messages"] is False
    assert run["on_caller_message"] is None
