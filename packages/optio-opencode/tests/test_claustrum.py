"""Claustrum filesystem-isolation wiring for opencode (Task 10).

opencode is wrapped in claustrum for the first time: the ``opencode web`` server
tree is Landlock-confined. These tests cover the config triad rule, the grant-set
shape (including the taskdir grant that the live opencode.db needs), and the
argv splice inside ``launch_opencode``.
"""

from __future__ import annotations

import shlex

import pytest

from optio_host.host import ProcessHandle, RunResult

from optio_opencode import fs_allowlist, host_actions
from optio_opencode.types import OpencodeTaskConfig


# ---------------------------------------------------------------------------
# (a) fs_isolation on (default) requires delivery_type — inherited from the
#     shared ClaustrumConfigMixin.
# ---------------------------------------------------------------------------


def test_fs_isolation_on_requires_delivery_type():
    with pytest.raises(ValueError, match="delivery_type"):
        OpencodeTaskConfig(consumer_instructions="x", fs_isolation=True)


def test_delivery_type_satisfies_the_rule():
    cfg = OpencodeTaskConfig(
        consumer_instructions="x", fs_isolation=True, delivery_type="audit",
    )
    assert cfg.delivery_type == "audit"


def test_fs_isolation_off_allows_missing_delivery_type():
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.fs_isolation is False
    assert cfg.delivery_type is None


# ---------------------------------------------------------------------------
# (b) grant set: --rwx <taskdir> (the live opencode.db is a sibling of the
#     workdir) AND --rox <opencode cache> AND --rwx <workdir>.
# ---------------------------------------------------------------------------


def test_grant_flags_cover_taskdir_workdir_and_cache():
    flags = fs_allowlist.build_grant_flags(
        workdir="/td/workdir",
        taskdir="/td",
        opencode_cache_dir="/cache/optio-opencode/bin",
        extra_allowed_dirs=None,
    )
    # system baseline present
    assert "/usr" in flags
    # taskdir rwx (OPENCODE_DB = <taskdir>/opencode.db lives one level above workdir)
    assert ["--rwx", "/td"] == _pair_before(flags, "/td")
    # workdir rwx
    assert ["--rwx", "/td/workdir"] == _pair_before(flags, "/td/workdir")
    # opencode binary cache rox
    assert ["--rox", "/cache/optio-opencode/bin"] == _pair_before(
        flags, "/cache/optio-opencode/bin"
    )


def test_grant_flags_expand_extra_home_tilde():
    flags = fs_allowlist.build_grant_flags(
        workdir="/td/workdir",
        taskdir="/td",
        opencode_cache_dir="/cache",
        extra_allowed_dirs=[fs_allowlist.AllowedDir("~/data", "ro")],
        host_home="/home/u",
    )
    assert flags[-2:] == ["--ro", "/home/u/data"]


def _pair_before(flags: list[str], path: str) -> list[str]:
    i = flags.index(path)
    return [flags[i - 1], flags[i]]


# ---------------------------------------------------------------------------
# (c) launch_opencode splices the claustrum wrap immediately before the
#     opencode executable and AFTER the env/password assignment.
# ---------------------------------------------------------------------------


class _RecordingLaunchHost:
    def __init__(self, *, workdir: str = "/wd", taskdir: str = "/td") -> None:
        self.workdir = workdir
        self.taskdir = taskdir
        self.launch_cmd: str | None = None
        self.launch_env: dict[str, str] | None = None

    async def write_text(self, relpath: str, content: str) -> None:
        pass

    async def run_command(self, cmd: str, *, cwd=None, env=None) -> RunResult:
        return RunResult(stdout="", stderr="", exit_code=0)

    async def launch_subprocess(
        self, command, *, env=None, cwd=None, merge_stderr=True, stdin=False,
        env_remove=None,
    ) -> ProcessHandle:
        self.launch_cmd = command
        self.launch_env = env

        async def _stdout():
            yield b"server listening on http://127.0.0.1:54321\n"

        return ProcessHandle(pid_like=object(), stdout=_stdout())

    async def terminate_subprocess(self, handle, *, aggressive=False) -> None:
        pass


async def test_launch_opencode_splices_claustrum_wrap_before_executable():
    host = _RecordingLaunchHost(workdir="/wd", taskdir="/td")
    wrap = [
        "/c/claustrum", "--best-effort", "--abi-min", "1",
        "--rwx", "/td/workdir", "--",
    ]
    handle, port = await host_actions.launch_opencode(
        host, "pw", claustrum_wrap=wrap,
    )
    assert port == 54321
    cmd = host.launch_cmd
    assert cmd is not None
    joined = shlex.join(wrap)
    # The wrap appears immediately before the opencode executable.
    assert f"{joined} opencode web" in cmd
    # And AFTER the password assignment (localhost bind + URL scrape survive).
    assert "OPENCODE_SERVER_PASSWORD" in cmd
    assert cmd.index("OPENCODE_SERVER_PASSWORD") < cmd.index("/c/claustrum")


async def test_launch_opencode_without_wrap_is_unchanged():
    host = _RecordingLaunchHost(workdir="/wd", taskdir="/td")
    await host_actions.launch_opencode(host, "pw")
    cmd = host.launch_cmd
    assert cmd is not None
    assert "claustrum" not in cmd
    # opencode executable still launched right after the password assignment.
    assert "OPENCODE_SERVER_PASSWORD" in cmd
    assert "opencode web" in cmd
