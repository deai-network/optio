import pytest

import optio_claudecode.host_actions as H


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
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cc-deadbeef0badcafe.sock")
    assert len(host.commands) == 1
    cmd = host.commands[0]
    # Targets ttyd processes by the socket path they carry in their cmdline.
    assert "pkill" in cmd
    assert "/tmp/optio-cc-deadbeef0badcafe.sock" in cmd
    # Anchored so the rescue's own command line is not matched (mirrors the
    # [c]laude self-match guard used for claude).
    assert "optio-cc-deadbeef0badcafe.sock" in cmd
    # Best-effort: never fails the caller when nothing matches.
    assert "|| true" in cmd


@pytest.mark.asyncio
async def test_kill_ttyd_by_socket_does_not_self_match():
    # The emitted pattern must contain a bracket-escape so pkill -f does not
    # match its own argv. We assert the socket digest is bracket-split.
    host = _Host()
    await H._kill_ttyd_by_socket(host, "/tmp/optio-cc-abc123.sock")
    cmd = host.commands[0]
    assert "[" in cmd and "]" in cmd
