import os

from optio_kimicode.host_actions import (
    _isolation_env,
    build_host,
    build_launch_env,
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


def test_build_launch_env_disables_auto_update():
    """Every kimi launch pins KIMI_CODE_NO_AUTO_UPDATE=1 — the wrapper controls
    the binary version, so kimi must not self-update (fork preflight gate)."""
    env = build_launch_env("/w/task")
    assert env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    # carries the isolation identity + a layered PATH
    assert env["KIMI_CODE_HOME"] == "/w/task/home"
    assert env["PATH"].startswith("/w/task/home/.local/bin:")


def test_build_launch_env_extra_can_override():
    """A caller extra_env wins over the base defaults (merged last)."""
    env = build_launch_env("/w/task", {"KIMI_CODE_NO_AUTO_UPDATE": "0"})
    assert env["KIMI_CODE_NO_AUTO_UPDATE"] == "0"


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


async def test_write_kimi_config_creates_permission_mode(tmp_path):
    """write_kimi_config sets default_permission_mode in <workdir>/home/config.toml
    (the daemon applies it to every session — blanket 'yolo' auto-approves)."""
    from optio_kimicode.host_actions import build_host, write_kimi_config

    host = build_host(None, str(tmp_path / "t"))
    await write_kimi_config(host, host.workdir, permission_mode="yolo")
    cfg = os.path.join(host.workdir, "home", "config.toml")
    assert os.path.exists(cfg)
    assert 'default_permission_mode = "yolo"' in open(cfg).read()


async def test_write_kimi_config_none_is_noop(tmp_path):
    """permission_mode=None writes nothing (no config.toml)."""
    from optio_kimicode.host_actions import build_host, write_kimi_config

    host = build_host(None, str(tmp_path / "t"))
    await write_kimi_config(host, host.workdir, permission_mode=None)
    assert not os.path.exists(os.path.join(host.workdir, "home", "config.toml"))


async def test_write_kimi_config_replaces_not_duplicates(tmp_path):
    """A pre-existing default_permission_mode is REPLACED (no duplicate key that
    would make the TOML invalid), other config lines are preserved."""
    from optio_kimicode.host_actions import build_host, write_kimi_config

    host = build_host(None, str(tmp_path / "t"))
    home = os.path.join(host.workdir, "home")
    os.makedirs(home, exist_ok=True)
    cfg = os.path.join(home, "config.toml")
    with open(cfg, "w") as f:
        f.write('default_model = "kimi-code/kimi-for-coding"\ndefault_permission_mode = "manual"\n')
    await write_kimi_config(host, host.workdir, permission_mode="yolo")
    body = open(cfg).read()
    assert body.count("default_permission_mode") == 1
    assert 'default_permission_mode = "yolo"' in body
    assert 'default_model = "kimi-code/kimi-for-coding"' in body


async def test_write_kimi_config_root_scoped_even_with_tables(tmp_path):
    """Regression (the config #4 'manual' bug): a config.toml with [table] sections
    (as a seed-captured one has) must still get default_permission_mode as a ROOT
    key. Appending it after a table scopes it INTO that table, so kimi ignores it
    and permission stays 'manual'. Verified via a real TOML parse."""
    import tomllib
    from optio_kimicode.host_actions import build_host, write_kimi_config

    host = build_host(None, str(tmp_path / "t"))
    home = os.path.join(host.workdir, "home")
    os.makedirs(home, exist_ok=True)
    cfg = os.path.join(home, "config.toml")
    with open(cfg, "w") as f:
        f.write(
            'default_model = "kimi-code/kimi-for-coding"\n\n'
            '[providers."managed:kimi-code"]\ntype = "kimi"\n\n'
            '[services.moonshot_fetch.oauth]\nstorage = "file"\n'
        )
    await write_kimi_config(host, host.workdir, permission_mode="yolo")
    data = tomllib.loads(open(cfg).read())
    assert data["default_permission_mode"] == "yolo"          # ROOT key
    assert data["default_model"] == "kimi-code/kimi-for-coding"
    assert data["providers"]["managed:kimi-code"]["type"] == "kimi"
    assert "default_permission_mode" not in data["services"]["moonshot_fetch"]["oauth"]
