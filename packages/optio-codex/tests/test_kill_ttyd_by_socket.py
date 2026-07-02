import pytest

import optio_codex.host_actions as H


class _Result:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0


class _Host:
    def __init__(self):
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)
        return _Result()


@pytest.mark.asyncio
async def test_kill_ttyd_by_socket_anchored_pkill():
    host = _Host()
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cx-deadbeef0badcafe.sock")
    assert len(host.commands) == 1
    cmd = host.commands[0]
    assert "pkill" in cmd
    assert "/tmp/optio-cx-deadbeef0badcafe.sock" in cmd
    assert "|| true" in cmd


@pytest.mark.asyncio
async def test_kill_ttyd_by_socket_does_not_self_match():
    host = _Host()
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cx-abc123.sock")
    cmd = host.commands[0]
    assert "[t]tyd" in cmd
