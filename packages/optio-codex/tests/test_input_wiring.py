"""iframe-input wiring: the task advertises the iframe-input widget, and a POST to
the input listener reaches codex's tmux TUI via the shared inject helpers."""
import asyncio

import aiohttp
import pytest

from optio_agents.input_listener import serialized, start_input_listener
from optio_codex import host_actions
from optio_codex.session import create_codex_task
from optio_codex.types import CodexTaskConfig


class _Result:
    exit_code = 0
    stdout = ""
    stderr = ""


class _Host:
    def __init__(self):
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        return _Result()


async def _post(port, payload):
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{port}/input", json=payload) as r:
            return r.status, await r.json()


def test_iframe_task_advertises_iframe_input_widget():
    t = create_codex_task(
        process_id="p", name="n",
        config=CodexTaskConfig(consumer_instructions="x", mode="iframe"),
    )
    assert t.ui_widget == "iframe-input"


def test_conversation_ui_task_still_uses_conversation_widget():
    t = create_codex_task(
        process_id="p", name="n",
        config=CodexTaskConfig(
            consumer_instructions="x", mode="conversation", conversation_ui=True,
        ),
    )
    assert t.ui_widget == "conversation"


@pytest.mark.asyncio
async def test_posted_text_reaches_codex_tmux_inject():
    host = _Host()
    lock = asyncio.Lock()

    async def _human_input(text):
        await host_actions.send_text_to_codex(host, "tmux", "/sock", "optio", text)

    runner, port = await start_input_listener(
        bind_iface="127.0.0.1", on_input=serialized(lock, _human_input),
    )
    try:
        status, body = await _post(port, {"text": "paste-the-code"})
        assert status == 200 and body == {"ok": True}
        cmd = host.commands[0]
        assert "set-buffer -b optio-feedback -- paste-the-code" in cmd
        assert cmd.rstrip().endswith("send-keys -t optio Enter")
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_posted_nav_key_reaches_codex_send_keys():
    host = _Host()
    lock = asyncio.Lock()

    async def _human_key(key):
        await host_actions.send_key_to_codex(host, "tmux", "/sock", "optio", key)

    async def _human_input(text):  # unused for this test but required by the API
        pass

    runner, port = await start_input_listener(
        bind_iface="127.0.0.1",
        on_input=serialized(lock, _human_input),
        on_key=serialized(lock, _human_key),
    )
    try:
        status, body = await _post(port, {"key": "Down"})
        assert status == 200 and body["ok"] is True
        assert host.commands == ["tmux -S /sock send-keys -t optio Down"]
        # A disallowed key never reaches send-keys (400 at the listener).
        status2, body2 = await _post(port, {"key": "rm -rf"})
        assert status2 == 400 and body2["reason"] == "bad-key"
        assert len(host.commands) == 1
    finally:
        await runner.cleanup()
