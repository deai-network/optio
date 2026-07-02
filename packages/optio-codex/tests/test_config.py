import pytest

from optio_codex import CodexTaskConfig


def test_defaults_and_validation():
    c = CodexTaskConfig(consumer_instructions="do it")
    assert c.mode == "iframe" and c.host_protocol is True and c.auto_start is True
    assert c.ask_for_approval == "never" and c.sandbox == "workspace-write"
    with pytest.raises(ValueError):
        CodexTaskConfig(consumer_instructions="x", codex_install_dir="relative/path")
    with pytest.raises(ValueError):
        CodexTaskConfig(consumer_instructions="x", ask_for_approval="bogus")


def test_ssh_config_routes_to_remote_host():
    """Stage 1: ssh config selects the RemoteHost path (the Stage-0
    NotImplementedError guard is gone). Construction only — no connection
    is attempted; the end-to-end proof is test_session_remote.py."""
    from optio_host.host import RemoteHost

    from optio_codex import SSHConfig
    from optio_codex.session import _build_host

    config = CodexTaskConfig(
        consumer_instructions="x",
        ssh=SSHConfig(host="worker.example", user="u", key_path="/k", port=2222),
    )
    host = _build_host(config, "codex-remote-route")
    assert isinstance(host, RemoteHost)
    # Remote taskdir layout: /tmp/<consumer>/<process_id>/workdir (no
    # OPTIO_CODEX_REMOTE_TASK_ROOT override in the test env).
    assert host.workdir == "/tmp/optio-codex/codex-remote-route/workdir"