import pytest

import optio_claudecode.host_actions as H


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
    host = _Host(["12345\n", "12345\n", ""])  # found, found, gone
    ok = await H.await_claude_gone(
        host, "/w/home/.local/bin/claude", poll_s=1.0, timeout_s=10.0,
    )
    assert ok is True
    assert len(_no_real_sleep) == 2  # polled twice before "gone"
    # scoped to the per-task path, and uses the [c]laude self-match guard
    assert all("/w/home/.local/bin/[c]laude" in c for c in host.commands)


@pytest.mark.asyncio
async def test_returns_false_on_timeout(_no_real_sleep):
    host = _Host(["999\n"])  # never gone
    ok = await H.await_claude_gone(
        host, "/w/home/.local/bin/claude", poll_s=1.0, timeout_s=3.0,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_returns_true_immediately_when_already_gone(_no_real_sleep):
    host = _Host([""])
    ok = await H.await_claude_gone(host, "/w/home/.local/bin/claude")
    assert ok is True
    assert len(_no_real_sleep) == 0
    assert len(host.commands) == 1
