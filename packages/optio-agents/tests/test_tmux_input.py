"""Shared tmux TUI-input helpers: command shape, quoting, and key validation."""
import pytest

from optio_agents.tmux_input import (
    NAV_KEYS,
    send_key_to_tmux,
    send_text_to_tmux,
)


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


class _FailHost:
    async def run_command(self, cmd, **kwargs):
        r = _Result()
        r.exit_code = 1
        r.stderr = "no server"
        return r


@pytest.mark.asyncio
async def test_send_text_sequence_quoting_and_buffer():
    host = _Host()
    await send_text_to_tmux(
        host, "/usr/bin/tmux", "/tmp/sock", "optio", 'hi "there"', buffer="optio-feedback",
    )
    cmd = host.commands[0]
    assert "/usr/bin/tmux -S /tmp/sock set-buffer -b optio-feedback -- " in cmd
    assert "paste-buffer -d -b optio-feedback -t optio" in cmd
    assert cmd.rstrip().endswith("send-keys -t optio Enter")
    # Ordered: paste-buffer ... sleep ... send-keys, with the default 1.0 settle.
    assert "sleep 1.0" in cmd
    assert cmd.index("paste-buffer") < cmd.index("sleep 1.0") < cmd.rindex("send-keys")
    # The message is shell-quoted (embedded double-quote intact).
    assert '\'hi "there"\'' in cmd


@pytest.mark.asyncio
async def test_send_text_default_buffer_and_settle_override():
    host = _Host()
    await send_text_to_tmux(host, "tmux", "/s", "optio", "x", submit_settle="0.2")
    cmd = host.commands[0]
    assert "-b optio-input -- " in cmd
    assert "sleep 0.2" in cmd


@pytest.mark.asyncio
async def test_send_text_raises_on_nonzero_exit():
    with pytest.raises(RuntimeError):
        await send_text_to_tmux(_FailHost(), "tmux", "/tmp/s", "optio", "x")


@pytest.mark.asyncio
async def test_send_key_allowlisted_and_command_shape():
    host = _Host()
    await send_key_to_tmux(host, "/usr/bin/tmux", "/tmp/sock", "optio", "Up")
    assert host.commands[0] == "/usr/bin/tmux -S /tmp/sock send-keys -t optio Up"


@pytest.mark.asyncio
async def test_send_key_rejects_disallowed():
    host = _Host()
    with pytest.raises(ValueError):
        await send_key_to_tmux(host, "tmux", "/s", "optio", "rm -rf")
    assert host.commands == []  # never reached send-keys
    assert "Up" in NAV_KEYS and "rm -rf" not in NAV_KEYS


@pytest.mark.asyncio
async def test_send_key_raises_on_nonzero_exit():
    with pytest.raises(RuntimeError):
        await send_key_to_tmux(_FailHost(), "tmux", "/s", "optio", "Enter")
