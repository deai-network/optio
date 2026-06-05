import pytest

from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_claudecode.host_actions import (
    build_resume_notice_args,
    send_text_to_claude,
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
async def test_send_text_sequence_and_quoting():
    host = _Host()
    await send_text_to_claude(host, "/usr/bin/tmux", "/tmp/sock", "optio", 'hi "there"')
    cmd = host.commands[0]
    assert "/usr/bin/tmux -S /tmp/sock set-buffer -b optio-feedback -- " in cmd
    assert "paste-buffer -d -b optio-feedback -t optio" in cmd
    assert cmd.rstrip().endswith("send-keys -t optio Enter")
    # settle between paste and Enter, ordered: paste-buffer ... sleep ... send-keys
    assert "sleep 1.0" in cmd
    assert cmd.index("paste-buffer") < cmd.index("sleep 1.0") < cmd.rindex("send-keys")
    # the message is shell-quoted (contains the embedded double-quote intact)
    assert '\'hi "there"\'' in cmd


@pytest.mark.asyncio
async def test_send_text_raises_on_nonzero_exit():
    with pytest.raises(RuntimeError):
        await send_text_to_claude(_FailHost(), "tmux", "/tmp/s", "optio", "x")


def test_resume_notice_args_gating():
    expect = [f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}"]
    assert build_resume_notice_args(resuming=True, pass_continue=True) == expect
    assert build_resume_notice_args(resuming=True, pass_continue=False) == []
    assert build_resume_notice_args(resuming=False, pass_continue=False) == []
    assert build_resume_notice_args(resuming=False, pass_continue=True) == []
