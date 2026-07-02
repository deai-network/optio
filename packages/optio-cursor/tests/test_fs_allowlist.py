"""Task 1 (Stage 8) — claustrum filesystem-allowlist grant builder.

Mechanism per the Task-0 probe DECISION: CLAUSTRUM, not cursor's native
sandbox (which is a per-shell-command wrapper only — the agent's own
in-process file writes escape it). We therefore port claudecode's
``fs_allowlist.build_grant_flags``: a baseline system allowlist + workdir rwx
+ cursor cache rox + caller extras, emitted as ordered claustrum grant flags.
"""

from optio_cursor.types import AllowedDir
from optio_cursor import fs_allowlist


def test_grant_flags_orders_modes_and_maps_caller():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        cursor_cache_dir="/cache/versions",
        extra_allowed_dirs=[AllowedDir(path="/data", mode="ro"),
                            AllowedDir(path="/scratch", mode="rw")],
    )
    # workdir is read-write-execute
    assert "--rwx" in flags
    i = flags.index("--rwx")
    assert flags[i + 1] == "/wd"
    # cursor cache is read+exec
    assert "--rox" in flags
    assert "/cache/versions" in flags
    # caller extras mapped
    assert "--ro" in flags and "/data" in flags
    assert "--rw" in flags and "/scratch" in flags
    # baseline system dir present
    assert "/usr" in flags


def test_no_extra_dirs_ok():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd", cursor_cache_dir="/cache", extra_allowed_dirs=None)
    assert "/wd" in flags and "/cache" in flags


def test_exec_modes_map_to_claustrum_flags():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        cursor_cache_dir="/cache",
        extra_allowed_dirs=[AllowedDir(path="/venv", mode="rox"),
                            AllowedDir(path="/build", mode="rwx")],
    )
    i = flags.index("/venv")
    assert flags[i - 1] == "--rox"
    i = flags.index("/build")
    assert flags[i - 1] == "--rwx"


def test_tilde_extras_expand_against_host_home():
    # Grants go to claustrum verbatim (no shell between), and the cursor-agent
    # process runs with an ISOLATED $HOME — so a caller's `~/...` extra must
    # be expanded against the real host home at flag-build time.
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        cursor_cache_dir="/cache",
        extra_allowed_dirs=[AllowedDir(path="~/analysis-venv", mode="rox"),
                            AllowedDir(path="/abs/stays", mode="ro")],
        host_home="/real/home",
    )
    i = flags.index("/real/home/analysis-venv")
    assert flags[i - 1] == "--rox"
    assert "~/analysis-venv" not in flags
    assert "/abs/stays" in flags


def test_trailing_slashes_stripped_on_workdir_and_cache():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd/", cursor_cache_dir="/cache/versions/",
        extra_allowed_dirs=None)
    assert "/wd" in flags and "/wd/" not in flags
    assert "/cache/versions" in flags and "/cache/versions/" not in flags


def test_config_carries_fs_isolation_and_extra_dirs():
    from optio_cursor.types import CursorTaskConfig
    cfg = CursorTaskConfig(consumer_instructions="x")
    # fs_isolation defaults on
    assert cfg.fs_isolation is True
    assert cfg.extra_allowed_dirs is None
    cfg2 = CursorTaskConfig(
        consumer_instructions="x",
        fs_isolation=False,
        extra_allowed_dirs=[AllowedDir(path="/data", mode="ro")],
    )
    assert cfg2.fs_isolation is False
    assert cfg2.extra_allowed_dirs[0].path == "/data"
