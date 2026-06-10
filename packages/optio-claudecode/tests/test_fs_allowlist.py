from optio_claudecode.types import AllowedDir
from optio_claudecode import fs_allowlist


def test_grant_flags_orders_modes_and_maps_caller():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        claude_cache_dir="/cache/versions",
        extra_allowed_dirs=[AllowedDir(path="/data", mode="ro"),
                            AllowedDir(path="/scratch", mode="rw")],
    )
    # workdir is read-write-execute
    assert "--rwx" in flags
    i = flags.index("--rwx")
    assert flags[i + 1] == "/wd"
    # claude cache is read+exec
    assert "--rox" in flags
    assert "/cache/versions" in flags
    # caller extras mapped
    assert "--ro" in flags and "/data" in flags
    assert "--rw" in flags and "/scratch" in flags
    # baseline system dir present
    assert "/usr" in flags


def test_no_extra_dirs_ok():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd", claude_cache_dir="/cache", extra_allowed_dirs=None)
    assert "/wd" in flags and "/cache" in flags


def test_exec_modes_map_to_claustrum_flags():
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        claude_cache_dir="/cache",
        extra_allowed_dirs=[AllowedDir(path="/venv", mode="rox"),
                            AllowedDir(path="/build", mode="rwx")],
    )
    i = flags.index("/venv")
    assert flags[i - 1] == "--rox"
    i = flags.index("/build")
    assert flags[i - 1] == "--rwx"


def test_tilde_extras_expand_against_host_home():
    # Grants go to claustrum verbatim (no shell between), and the claude
    # process runs with an ISOLATED $HOME — so a caller's `~/...` extra must
    # be expanded against the real host home at flag-build time.
    flags = fs_allowlist.build_grant_flags(
        workdir="/wd",
        claude_cache_dir="/cache",
        extra_allowed_dirs=[AllowedDir(path="~/analysis-venv", mode="rox"),
                            AllowedDir(path="/abs/stays", mode="ro")],
        host_home="/real/home",
    )
    i = flags.index("/real/home/analysis-venv")
    assert flags[i - 1] == "--rox"
    assert "~/analysis-venv" not in flags
    assert "/abs/stays" in flags
