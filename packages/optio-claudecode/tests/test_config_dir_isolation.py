"""Claude must read config only from the planted per-task dir, never the host
user's global ~/.claude (the leak this closes)."""
import os
import shutil
import subprocess

import pytest

from optio_claudecode import host_actions


def test_launch_env_sets_claude_config_dir_to_planted_dir():
    env, _shell = host_actions._build_claude_shell_command(
        claude_path="/x/home/.local/bin/claude",
        workdir="/wd",
        extra_env=None,
        claude_flags=[],
        local_mode=True,
    )
    assert "CLAUDE_CONFIG_DIR=/wd/home/.claude" in env


@pytest.mark.skipif(shutil.which("claude") is None, reason="no real claude binary on PATH")
def test_real_claude_resolves_config_under_isolated_dir_not_host_home(tmp_path):
    # Run the real claude under the isolation env. Config-path resolution is
    # written to the debug file at startup, BEFORE any API call, so this needs
    # no auth (the process exits non-zero on "Not logged in" — fine).
    claude = shutil.which("claude")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    dbg = tmp_path / "dbg.txt"
    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }
    subprocess.run(
        [claude, "--debug-file", str(dbg), "--print", "x"],
        env=env, capture_output=True, timeout=60,
    )
    log = dbg.read_text(errors="replace") if dbg.exists() else ""
    isolated = str(home / ".claude")
    host_global = os.path.join(os.path.expanduser("~"), ".claude")

    # Claude resolved its config dir under the isolated dir...
    assert isolated in log, f"isolated config dir not referenced in debug log:\n{log[:2000]}"
    # ...and NEVER touched the host user's real global config dir.
    assert f"{host_global}/" not in log, (
        f"host global config dir {host_global} leaked into resolution:\n{log[:2000]}"
    )
