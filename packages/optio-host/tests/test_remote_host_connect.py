"""RemoteHost.connect turns on SSH-level keepalive.

asyncssh leaves SSH keepalive off by default (keepalive_interval=0), so an
idle connection can be dropped by a NAT or firewall idle timeout, and a dead
peer goes unnoticed until the next write. RemoteHost connections can live for
a whole sync or agent session, so connect() must ask asyncssh for keepalives.
"""

from __future__ import annotations

import optio_host.host as host_mod
from optio_host.host import RemoteHost
from optio_host.types import SSHConfig


class _FakeConnection:
    async def start_sftp_client(self):
        return object()


async def test_connect_enables_ssh_keepalive(monkeypatch):
    seen: dict = {}

    async def fake_connect(**kwargs):
        seen.update(kwargs)
        return _FakeConnection()

    monkeypatch.setattr(host_mod.asyncssh, "connect", fake_connect)
    host = RemoteHost(
        ssh_config=SSHConfig(host="h", user="u", key_path="/k", port=22),
        taskdir="/tmp/optio-host-keepalive-test",
    )

    await host.connect()

    assert seen["keepalive_interval"] == host_mod.SSH_KEEPALIVE_INTERVAL == 30
    assert seen["keepalive_count_max"] == host_mod.SSH_KEEPALIVE_COUNT_MAX == 3
