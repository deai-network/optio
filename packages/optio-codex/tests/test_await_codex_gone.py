import pytest

import optio_codex.host_actions as H


class _Result:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = 0


class _Host:
    """Returns successive pgrep outputs from ``seq`` (last value repeats)."""

    def __init__(self, seq):
        self.seq = seq
        self.commands = []

    async def run_command(self, cmd, **kwargs):
        i = min(len(self.commands), len(self.seq) - 1)
        self.commands.append(cmd)
        return _Result(self.seq[i])


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    sleeps = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(H.asyncio, "sleep", _fake_sleep)
    return sleeps


@pytest.mark.asyncio
async def test_waits_until_gone_then_returns_true(_no_real_sleep):
    host = _Host(["12345\n", "12345\n", ""])
    ok = await H.await_codex_gone(
        host, "/w/home/.local/bin/codex", poll_s=1.0, timeout_s=10.0,
    )
    assert ok is True
    assert len(_no_real_sleep) == 2
    assert all("/w/home/.local/bin/[c]odex" in c for c in host.commands)


@pytest.mark.asyncio
async def test_returns_false_on_timeout(_no_real_sleep):
    host = _Host(["999\n"])
    ok = await H.await_codex_gone(
        host, "/w/home/.local/bin/codex", poll_s=1.0, timeout_s=3.0,
    )
    assert ok is False
