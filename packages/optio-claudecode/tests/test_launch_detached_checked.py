"""A detached ``new-session`` whose exit code is non-zero must raise, surfacing
the tmux stderr (merged into stdout) — instead of being silently swallowed and
later misreported downstream as "body returned before DONE was observed".
"""
import pytest

from optio_claudecode import host_actions


class _Pid:
    def __init__(self, code: int) -> None:
        self._code = code

    async def wait(self) -> int:
        return self._code


async def _agen(lines):
    for ln in lines:
        yield ln.encode("utf-8")


class _Handle:
    def __init__(self, lines, code) -> None:
        self.stdout = _agen(lines)
        self.pid_like = _Pid(code)


class _FakeHost:
    def __init__(self, lines, code) -> None:
        self._lines = lines
        self._code = code
        self.calls: list[str] = []

    async def launch_subprocess(self, command, *, env_remove=None, **kw):
        self.calls.append(command)
        return _Handle(self._lines, self._code)


async def test_nonzero_exit_raises_with_stderr_surfaced():
    host = _FakeHost(["error connecting to .../tmux.sock (File name too long)\n"], 1)
    with pytest.raises(RuntimeError) as ei:
        await host_actions._launch_detached_checked(
            host, "tmux new-session ...", env_remove=None, what="tmux new-session",
        )
    msg = str(ei.value)
    assert "tmux new-session" in msg
    assert "File name too long" in msg


async def test_zero_exit_returns_output_lines():
    host = _FakeHost(["ok\n"], 0)
    out = await host_actions._launch_detached_checked(
        host, "tmux new-session ...", env_remove=None, what="tmux new-session",
    )
    assert "".join(out).strip() == "ok"
