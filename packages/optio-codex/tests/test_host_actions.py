import asyncio
import re
import shutil
import subprocess

import pytest

from optio_codex.host_actions import (
    _build_codex_shell_command,
    _codex_pgrep_pattern,
    _isolation_env,
)


def test_isolation_env_all_keys():
    env = _isolation_env("/w/task")
    assert env == {
        "HOME": "/w/task/home",
        "CODEX_HOME": "/w/task/home/.codex",
        "XDG_CONFIG_HOME": "/w/task/home/.config",
        "XDG_DATA_HOME": "/w/task/home/.local/share",
        "XDG_CACHE_HOME": "/w/task/home/.cache",
    }


def test_build_shell_command_composes_path_on_host():
    env, cmd = _build_codex_shell_command(
        codex_path="/x/codex", workdir="/w/task", extra_env=None,
        codex_flags=[],
    )
    for k, v in _isolation_env("/w/task").items():
        assert f"{k}={v}" in env
    # PATH must NOT be baked in from the engine environment…
    assert not any(a.startswith("PATH=") for a in env)
    # …it is composed on the HOST inside the bash payload instead.
    assert 'export PATH=/w/task/home/.local/bin:"$PATH"' in cmd


def test_build_shell_command_honors_extra_env_path_override():
    env, cmd = _build_codex_shell_command(
        codex_path="/x/codex", workdir="/w/task",
        extra_env={"PATH": "/custom/bin"}, codex_flags=[],
    )
    assert "export PATH=/w/task/home/.local/bin:/custom/bin" in cmd
    assert not any(a.startswith("PATH=") for a in env)


def test_env_isolation_and_done_error():
    from optio_codex.host_actions import build_codex_flags

    flags = build_codex_flags(model="gpt-test")
    env, cmd = _build_codex_shell_command(
        codex_path="/x/codex", workdir="/w/task", extra_env=None,
        codex_flags=flags,
    )
    assert "HOME=/w/task/home" in env
    assert "CODEX_HOME=/w/task/home/.codex" in env
    assert "echo DONE" in cmd and "ERROR: codex exited" in cmd
    assert "--ask-for-approval" in cmd and "never" in cmd
    assert "--sandbox" in cmd and "workspace-write" in cmd
    assert "--model" in cmd and "gpt-test" in cmd


class _RecordingHost:
    """Fake Host: records run_command calls, returns success."""

    def __init__(self, workdir="/w/task/workdir", stdout=""):
        self.workdir = workdir
        self.commands: list[str] = []
        self._stdout = stdout

    async def run_command(self, cmd, **kwargs):
        self.commands.append(cmd)

        class _R:
            stdout = self._stdout
            stderr = ""
            exit_code = 0

        return _R()


@pytest.mark.asyncio
async def test_provision_task_home_creates_tree_and_symlink():
    from optio_codex.host_actions import _provision_task_home

    host = _RecordingHost(workdir="/w/task/workdir")
    per_task = await _provision_task_home(host, shared_codex_path="/usr/local/bin/codex")
    assert per_task == "/w/task/workdir/home/.local/bin/codex"
    joined = " && ".join(host.commands)
    # Home tree: HOME itself, CODEX_HOME, bin dir, and the XDG dirs.
    for d in (
        "/w/task/workdir/home/.codex",
        "/w/task/workdir/home/.local/bin",
        "/w/task/workdir/home/.config",
        "/w/task/workdir/home/.local/share",
        "/w/task/workdir/home/.cache",
    ):
        assert d in joined
    assert "mkdir -p" in joined
    # Per-task launch path is a symlink to the shared binary (C2 precondition).
    assert "ln -sfn /usr/local/bin/codex /w/task/workdir/home/.local/bin/codex" in joined


def test_pgrep_pattern_scoped_to_per_task_path_only():
    """C2: the anchored pattern from THIS task's per-task path must not match
    a codex launched from the shared path or from ANOTHER task's path."""
    pattern = _codex_pgrep_pattern("/w/taskA/workdir/home/.local/bin/codex")
    # pkill/pgrep -f applies the pattern as a regex over the full cmdline.
    assert re.search(pattern, "/w/taskA/workdir/home/.local/bin/codex --sandbox workspace-write")
    assert not re.search(pattern, "/usr/local/bin/codex --sandbox workspace-write")
    assert not re.search(pattern, "/w/taskB/workdir/home/.local/bin/codex --sandbox workspace-write")
    # Self-match guard intact ([c]odex): the pattern string itself must not
    # contain the literal token 'codex' at the anchored tail.
    assert "[c]odex" in pattern


class _HomeHost(_RecordingHost):
    async def resolve_host_home(self):
        return "/home/worker"


@pytest.mark.asyncio
async def test_expand_user_path_tilde_forms():
    from optio_codex.host_actions import _expand_user_path

    host = _HomeHost()
    assert await _expand_user_path(host, "~/bin") == "/home/worker/bin"
    assert await _expand_user_path(host, "~") == "/home/worker"
    assert await _expand_user_path(host, "/abs/path") == "/abs/path"
    with pytest.raises(ValueError):
        await _expand_user_path(host, "~otheruser/bin")


@pytest.mark.asyncio
async def test_resolve_codex_expands_tilde_install_dir():
    from optio_codex.host_actions import resolve_codex

    host = _HomeHost(stdout="OK")
    path = await resolve_codex(host, install_dir="~/tools")
    assert path == "/home/worker/tools/codex"
    # The probe command must carry the EXPANDED path, not a quoted literal '~'.
    assert any("/home/worker/tools/codex" in c for c in host.commands)
    assert not any("'~" in c for c in host.commands)


@pytest.mark.asyncio
async def test_kill_codex_processes_spares_other_tasks(tmp_path):
    """Launch two real processes from two per-task paths; killing task A's
    must leave task B's alive. Uses real pkill against unique tmp paths."""
    from optio_codex.host_actions import kill_codex_processes
    from optio_host.host import LocalHost

    sleep_bin = shutil.which("sleep")
    procs = []
    paths = []
    for task in ("a", "b"):
        bin_dir = tmp_path / task / "workdir" / "home" / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        codex = bin_dir / "codex"
        shutil.copy(sleep_bin, codex)
        paths.append(str(codex))
        procs.append(subprocess.Popen([str(codex), "30"]))
    try:
        taskdir_a = str(tmp_path / "a")
        host = LocalHost(taskdir=taskdir_a)
        await kill_codex_processes(host, paths[0])
        await asyncio.sleep(0.5)
        assert procs[0].poll() is not None, "task A's codex should be dead"
        assert procs[1].poll() is None, "task B's codex must survive"
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()