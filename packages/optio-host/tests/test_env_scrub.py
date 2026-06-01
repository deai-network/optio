from optio_host.host import _scrub_env


def test_scrub_env_removes_matching_names():
    env = {"FOO_API_KEY": "x", "BAR_TOKEN": "y", "KEEP": "z", "PATH": "/bin"}
    _scrub_env(env, ["*_API_KEY", "*_TOKEN"])
    assert env == {"KEEP": "z", "PATH": "/bin"}


def test_scrub_env_noop_when_no_patterns():
    env = {"FOO_API_KEY": "x"}
    _scrub_env(env, None)
    assert env == {"FOO_API_KEY": "x"}


def test_scrub_env_case_sensitive():
    env = {"foo_api_key": "x"}  # lower-case name should NOT match upper-case glob
    _scrub_env(env, ["*_API_KEY"])
    assert env == {"foo_api_key": "x"}
