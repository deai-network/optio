import pytest

from optio_opencode.types import DeliverableCallback, OpencodeTaskConfig, SSHConfig


def test_ssh_config_required_fields_only():
    cfg = SSHConfig(host="h", user="u", key_path="/tmp/k")
    assert cfg.host == "h"
    assert cfg.user == "u"
    assert cfg.key_path == "/tmp/k"
    assert cfg.port == 22


def test_ssh_config_custom_port():
    cfg = SSHConfig(host="h", user="u", key_path="/tmp/k", port=2222)
    assert cfg.port == 2222


def test_opencode_task_config_minimal():
    cfg = OpencodeTaskConfig(consumer_instructions="do X")
    assert cfg.consumer_instructions == "do X"
    assert cfg.opencode_config == {}
    assert cfg.ssh is None
    assert cfg.on_deliverable is None
    assert cfg.install_if_missing is True


def test_opencode_task_config_independent_default_dicts():
    a = OpencodeTaskConfig(consumer_instructions="")
    b = OpencodeTaskConfig(consumer_instructions="")
    a.opencode_config["k"] = 1
    assert "k" not in b.opencode_config


def test_deliverable_callback_is_callable_alias():
    # Type alias: the callback takes (str, str) and returns an awaitable.
    # Existence check only — no runtime behavior to assert.
    assert DeliverableCallback is not None
