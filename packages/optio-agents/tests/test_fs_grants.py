from optio_agents.config_types import AllowedDir
from optio_agents import fs_grants


def test_baseline_then_workdir_then_cache_then_extras():
    flags = fs_grants.build_grant_flags(
        workdir="/wd/", engine_cache_dir="/cache/",
        extra_allowed_dirs=[AllowedDir("/data", "ro")],
    )
    # system baseline present
    assert "--rox" in flags and "/usr" in flags
    # workdir rwx, cache rox, extra ro — in order, trailing
    assert flags[-6:] == ["--rwx", "/wd", "--rox", "/cache", "--ro", "/data"]


def test_home_tilde_expands_against_host_home():
    flags = fs_grants.build_grant_flags(
        workdir="/wd", engine_cache_dir="/cache",
        extra_allowed_dirs=[AllowedDir("~/x", "rw")], host_home="/home/u",
    )
    assert flags[-2:] == ["--rw", "/home/u/x"]


def test_extra_baseline_appended_to_system_baseline():
    flags = fs_grants.build_grant_flags(
        workdir="/wd", engine_cache_dir="/cache",
        extra_baseline=[("--ro", "/opt/opencode")],
    )
    assert "/opt/opencode" in flags
