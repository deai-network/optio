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
