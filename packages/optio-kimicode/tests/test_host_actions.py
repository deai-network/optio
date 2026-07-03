import os

import pytest

from optio_kimicode.host_actions import (
    _isolation_env,
    build_host,
    ensure_kimicode_installed,
)


def test_isolation_env_all_keys():
    """_isolation_env is the single source of truth for a task's HOME/XDG/kimi
    identity — every key rooted at ``<workdir>/home``. KIMI_CODE_HOME relocates
    kimi's whole data root (creds, sessions, AGENTS.md) into the per-task home."""
    env = _isolation_env("/w/task")
    assert env == {
        "HOME": "/w/task/home",
        "KIMI_CODE_HOME": "/w/task/home",
        "XDG_CONFIG_HOME": "/w/task/home/.config",
        "XDG_DATA_HOME": "/w/task/home/.local/share",
        "XDG_CACHE_HOME": "/w/task/home/.cache",
    }


def test_isolation_env_strips_trailing_slash():
    """A trailing slash on the workdir must not double the separator."""
    env = _isolation_env("/w/task/")
    assert env["HOME"] == "/w/task/home"
    assert env["KIMI_CODE_HOME"] == "/w/task/home"


def test_isolation_env_no_path_key():
    """PATH is layered by the caller (launch prepends <home>/.local/bin), never
    baked into the isolation identity."""
    assert "PATH" not in _isolation_env("/w/task")


def test_build_host_local_when_no_ssh(tmp_path):
    """ssh=None → LocalHost; the taskdir and its workdir are created on disk."""
    from optio_host.host import LocalHost

    taskdir = str(tmp_path / "t1")
    host = build_host(None, taskdir)
    assert isinstance(host, LocalHost)
    assert os.path.isdir(taskdir)
    assert os.path.isdir(host.workdir)


def test_build_host_remote_when_ssh(tmp_path):
    """A non-None ssh_config → RemoteHost; no local filesystem is touched."""
    from optio_host.host import RemoteHost

    taskdir = str(tmp_path / "remote-task")
    host = build_host(object(), taskdir)
    assert isinstance(host, RemoteHost)
    # Remote hosts must not materialize the taskdir locally.
    assert not os.path.exists(taskdir)


async def test_ensure_kimicode_installed_is_stub():
    """Two-tier install is deferred to plan group 4; the entrypoint exists but
    fails loudly rather than silently pretending kimi is provisioned."""
    with pytest.raises(NotImplementedError):
        await ensure_kimicode_installed(object())
