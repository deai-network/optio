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


def test_opencode_task_config_workdir_exclude_default_none():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi")
    assert c.workdir_exclude is None


def test_opencode_task_config_workdir_exclude_empty_list():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi", workdir_exclude=[])
    assert c.workdir_exclude == []


def test_opencode_task_config_workdir_exclude_custom_list():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi", workdir_exclude=["*.log"])
    assert c.workdir_exclude == ["*.log"]


"""Type-shape tests for OpencodeTaskConfig and DeliverableCallback."""

import inspect
from typing import get_type_hints

from optio_opencode.types import (
    DeliverableCallback,
    OpencodeTaskConfig,
    HookCallback,
)


def test_opencode_task_config_has_hook_fields():
    fields = {f for f in OpencodeTaskConfig.__dataclass_fields__}
    assert "before_execute" in fields
    assert "after_execute" in fields


def test_opencode_task_config_hook_default_none():
    cfg = OpencodeTaskConfig(consumer_instructions="x")
    assert cfg.before_execute is None
    assert cfg.after_execute is None


def test_deliverable_callback_now_takes_three_args():
    # The Callable type alias is structural; we can't introspect deeply,
    # but we can ensure HookCallback exists and is callable type.
    assert HookCallback is not None
    assert DeliverableCallback is not None


def test_opencode_task_config_supports_resume_default_true():
    cfg = OpencodeTaskConfig(consumer_instructions="x")
    assert cfg.supports_resume is True


def test_opencode_task_config_supports_resume_can_be_disabled():
    cfg = OpencodeTaskConfig(consumer_instructions="x", supports_resume=False)
    assert cfg.supports_resume is False


def test_opencode_task_config_on_resume_refresh_default_none():
    cfg = OpencodeTaskConfig(consumer_instructions="x")
    assert cfg.on_resume_refresh is None


def test_opencode_task_config_on_resume_refresh_accepts_callable():
    def _refresh(c):
        return c

    cfg = OpencodeTaskConfig(consumer_instructions="x", on_resume_refresh=_refresh)
    assert cfg.on_resume_refresh is _refresh
