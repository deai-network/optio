import asyncio
import re
import shutil
import subprocess
import time as _time

import pytest

from optio_codex.host_actions import (
    _build_codex_shell_command,
    _codex_pgrep_pattern,
    _isolation_env,
    resolve_codex,
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


def test_build_shell_command_survives_shell_significant_paths(tmp_path):
    # A workdir (or PATH override) containing shell-significant characters
    # must not break the bash payload or open an injection seam. Proven
    # functionally: execute the built command against a real workdir with a
    # space in its path and observe the DONE marker.
    import subprocess

    workdir = tmp_path / "task with space"
    workdir.mkdir()
    probe = tmp_path / "probe.sh"
    probe.write_text('#!/bin/sh\necho "$PATH" > "$1"\n')
    probe.chmod(0o755)
    path_out = tmp_path / "path.txt"

    _, cmd = _build_codex_shell_command(
        codex_path=str(probe), workdir=str(workdir),
        extra_env={"PATH": "/custom/dir with space"},
        codex_flags=[str(path_out)],
    )
    subprocess.run(cmd, shell=True, check=True, timeout=10)
    assert (workdir / "optio.log").read_text().strip() == "DONE"
    assert (
        path_out.read_text().strip()
        == f"{workdir}/home/.local/bin:/custom/dir with space"
    )


def test_env_isolation_and_done_error():
    from optio_codex.host_actions import build_codex_flags

    flags = build_codex_flags(
        model="gpt-test", sandbox_args=["--sandbox", "workspace-write"],
    )
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
        # Wait for the OBSERVABLE event (task A's process actually exiting)
        # rather than assuming it happens within a fixed wall-clock window —
        # under CPU starvation the reap can lag arbitrarily. Generous ceiling
        # only bounds a true hang. task B is `sleep 30`, so it stays alive for
        # the whole window and the survivor check remains meaningful.
        _deadline = _time.monotonic() + 60.0
        while procs[0].poll() is None and _time.monotonic() < _deadline:
            await asyncio.sleep(0.02)
        assert procs[0].poll() is not None, "task A's codex should be dead"
        assert procs[1].poll() is None, "task B's codex must survive"
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


@pytest.mark.asyncio
async def test_resolve_codex_missing_names_the_stage_gap():
    class _NotFoundHost(_RecordingHost):
        async def run_command(self, cmd, **kwargs):
            self.commands.append(cmd)

            class _R:
                stdout = ""
                stderr = ""
                exit_code = 1

            return _R()

    host = _NotFoundHost()
    with pytest.raises(RuntimeError, match="auto-download"):
        await resolve_codex(host, install_if_missing=True)

def test_build_resume_args_subcommand_precedes_flags():
    """`codex resume <id>` is a SUBCOMMAND: it and the explicit session id
    lead the argv, before every flag. None ⇒ fresh launch, no prefix."""
    from optio_codex.host_actions import build_resume_args

    sid = "01234567-89ab-cdef-0123-456789abcdef"
    assert build_resume_args(sid) == ["resume", sid]
    assert build_resume_args(None) == []


def test_build_auto_start_args_suppressed_on_resume():
    from optio_codex.host_actions import AUTO_START_PROMPT, build_auto_start_args

    assert build_auto_start_args(auto_start=True) == [AUTO_START_PROMPT]
    assert build_auto_start_args(auto_start=True, resuming=True) == []
    assert build_auto_start_args(auto_start=False) == []
    assert build_auto_start_args(auto_start=False, resuming=True) == []


def test_build_resume_notice_args():
    from optio_codex.host_actions import build_resume_notice_args
    # Fresh launch → no notice.
    assert build_resume_notice_args(resuming=False) == []
    # Resume → a single System:-prefixed "you have been resumed" positional.
    notice = build_resume_notice_args(resuming=True)
    assert len(notice) == 1
    assert "you have been resumed" in notice[0]
    assert notice[0].startswith("System:")


@pytest.mark.asyncio
async def test_read_latest_session_id_scans_newest_rollout_by_name(tmp_path):
    """Newest by FILENAME, not mtime: rollout names embed an ISO-ordered
    timestamp, and mtimes do not survive a workdir tar restore. The test
    deliberately gives the OLDER rollout the NEWER mtime."""
    import pathlib as _p
    import time as _t

    from optio_codex.host_actions import build_host, read_latest_session_id

    host = build_host(None, str(tmp_path / "task"))
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    sessions = _p.Path(host.workdir) / "home" / ".codex" / "sessions"
    old_dir = sessions / "2026" / "07" / "01"
    new_dir = sessions / "2026" / "07" / "02"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (new_dir / f"rollout-2026-07-02T09-00-00-{new_id}.jsonl").write_text(
        "{}\n", encoding="utf-8",
    )
    _t.sleep(0.01)
    (old_dir / f"rollout-2026-07-01T09-00-00-{old_id}.jsonl").write_text(
        "{}\n", encoding="utf-8",
    )  # older name, newest mtime

    assert await read_latest_session_id(host) == new_id


@pytest.mark.asyncio
async def test_read_latest_session_id_none_when_no_rollouts(tmp_path):
    from optio_codex.host_actions import build_host, read_latest_session_id

    host = build_host(None, str(tmp_path / "task"))
    assert await read_latest_session_id(host) is None


@pytest.mark.asyncio
async def test_rotate_optio_log_moves_content_and_truncates(tmp_path):
    import pathlib as _p

    from optio_codex.host_actions import _rotate_optio_log, build_host

    host = build_host(None, str(tmp_path / "task"))
    wd = _p.Path(host.workdir)
    (wd / "optio.log").write_text("STATUS: old\nDONE\n", encoding="utf-8")

    await _rotate_optio_log(host)
    assert (wd / "optio.log").read_text(encoding="utf-8") == ""
    assert "DONE" in (wd / "optio.log.old").read_text(encoding="utf-8")

    # A second rotation APPENDS to optio.log.old (history preserved across
    # consecutive resumes).
    (wd / "optio.log").write_text("DONE again\n", encoding="utf-8")
    await _rotate_optio_log(host)
    old = (wd / "optio.log.old").read_text(encoding="utf-8")
    assert "DONE\n" in old and "DONE again\n" in old
    assert (wd / "optio.log").read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_rotate_optio_log_missing_log_writes_empty(tmp_path):
    import pathlib as _p

    from optio_codex.host_actions import _rotate_optio_log, build_host

    host = build_host(None, str(tmp_path / "task"))
    await _rotate_optio_log(host)
    assert (_p.Path(host.workdir) / "optio.log").read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_append_resume_log_entry_formats(tmp_path):
    import pathlib as _p

    from optio_codex.host_actions import _append_resume_log_entry, build_host

    host = build_host(None, str(tmp_path / "task"))
    await _append_resume_log_entry(host)
    await _append_resume_log_entry(host, refreshed=["AGENTS.md", "notes.md"])

    lines = (
        (_p.Path(host.workdir) / "resume.log")
        .read_text(encoding="utf-8").splitlines()
    )
    assert len(lines) == 2
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", lines[0])
    assert lines[1].endswith(" REFRESHED:AGENTS.md,notes.md")


def test_build_codex_flags_embeds_sandbox_args():
    from optio_codex.host_actions import build_codex_flags

    flags = build_codex_flags(
        model="gpt-5.5",
        ask_for_approval="never",
        sandbox_args=["--sandbox", "workspace-write",
                      "-c", 'sandbox_workspace_write.writable_roots=["/s"]'],
    )
    assert flags.index("--sandbox") < flags.index("--model")
    assert flags[flags.index("--sandbox") + 1] == "workspace-write"
    assert 'sandbox_workspace_write.writable_roots=["/s"]' in flags


def test_teardown_aggressive_grace_for_seeded_sessions():
    """A seeded session must tear codex down gracefully even on cancel so it
    can flush a rotated (single-use) auth.json before the backstop save-back
    reads it — an aggressive SIGKILL would strand the rotation and kill the
    seed. A non-seeded session keeps the fast aggressive kill on cancel."""
    from optio_codex.session import _teardown_aggressive
    assert _teardown_aggressive(cancelled=True, seeded=True) is False    # grace
    assert _teardown_aggressive(cancelled=True, seeded=False) is True    # fast kill
    assert _teardown_aggressive(cancelled=False, seeded=True) is False
    assert _teardown_aggressive(cancelled=False, seeded=False) is False
