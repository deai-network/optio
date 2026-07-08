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
    cfg = OpencodeTaskConfig(consumer_instructions="do X", fs_isolation=False)
    assert cfg.consumer_instructions == "do X"
    assert cfg.opencode_config == {}
    assert cfg.ssh is None
    assert cfg.on_deliverable is None
    assert cfg.install_if_missing is True


def test_opencode_task_config_independent_default_dicts():
    a = OpencodeTaskConfig(consumer_instructions="", fs_isolation=False)
    b = OpencodeTaskConfig(consumer_instructions="", fs_isolation=False)
    a.opencode_config["k"] = 1
    assert "k" not in b.opencode_config


def test_deliverable_callback_is_callable_alias():
    # Type alias: the callback takes (str, str) and returns an awaitable.
    # Existence check only — no runtime behavior to assert.
    assert DeliverableCallback is not None


def test_opencode_task_config_workdir_exclude_default_none():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi", fs_isolation=False)
    assert c.workdir_exclude is None


def test_opencode_task_config_workdir_exclude_empty_list():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi", workdir_exclude=[], fs_isolation=False)
    assert c.workdir_exclude == []


def test_opencode_task_config_workdir_exclude_custom_list():
    from optio_opencode.types import OpencodeTaskConfig
    c = OpencodeTaskConfig(consumer_instructions="hi", workdir_exclude=["*.log"], fs_isolation=False)
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
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.before_execute is None
    assert cfg.after_execute is None


def test_deliverable_callback_now_takes_three_args():
    # The Callable type alias is structural; we can't introspect deeply,
    # but we can ensure HookCallback exists and is callable type.
    assert HookCallback is not None
    assert DeliverableCallback is not None


def test_opencode_task_config_supports_resume_default_true():
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.supports_resume is True


def test_opencode_task_config_supports_resume_can_be_disabled():
    cfg = OpencodeTaskConfig(consumer_instructions="x", supports_resume=False, fs_isolation=False)
    assert cfg.supports_resume is False


def test_opencode_task_config_on_resume_refresh_default_identity():
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    # Default is identity-refresh (recompose AGENTS.md from the same config),
    # not None — a resumed session no longer freezes its instructions.
    assert cfg.on_resume_refresh is not None
    assert cfg.on_resume_refresh(cfg) is cfg


def test_opencode_task_config_on_resume_refresh_accepts_callable():
    def _refresh(c):
        return c

    cfg = OpencodeTaskConfig(consumer_instructions="x", on_resume_refresh=_refresh, fs_isolation=False)
    assert cfg.on_resume_refresh is _refresh


async def test_seed_id_accepts_callable_provider():
    from optio_opencode.types import OpencodeTaskConfig, SeedProvider  # noqa: F401

    async def provider(process_id: str) -> str:
        return "abc123"

    cfg = OpencodeTaskConfig(consumer_instructions="x", seed_id=provider, fs_isolation=False)
    assert callable(cfg.seed_id)


# --- harmonization: shared aliases, install_dir rename, model, inert fs -------


def test_shared_aliases_reexported_from_types():
    # C1: the config vocabulary now lives in optio_agents; types.py re-exports
    # it (and AllowedDir/SeedUnavailableError) so .types imports keep working.
    from optio_agents import AllowedDir as _SharedAllowedDir
    from optio_opencode.types import (  # noqa: F401
        AllowedDir,
        ConversationMode,
        SeedProvider,
        SeedUnavailableError,
        ThinkingVerbosity,
        ToolVerbosity,
    )

    assert AllowedDir is _SharedAllowedDir
    assert issubclass(SeedUnavailableError, Exception)


def test_install_dir_field_present_and_default_none():
    # C2: opencode_install_dir → install_dir.
    fields = {f for f in OpencodeTaskConfig.__dataclass_fields__}
    assert "install_dir" in fields
    assert "opencode_install_dir" not in fields
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.install_dir is None


def test_no_default_model_field_only_model():
    # C3: default_model → model (single field).
    fields = {f for f in OpencodeTaskConfig.__dataclass_fields__}
    assert "model" in fields
    assert "default_model" not in fields
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.model is None


def test_fs_isolation_defaults_true_and_extra_allowed_dirs_none():
    # delivery_type is mandatory when fs_isolation is on (inherited from
    # ClaustrumConfigMixin); pass it so construction succeeds while still
    # asserting the fs_isolation default.
    cfg = OpencodeTaskConfig(consumer_instructions="x", delivery_type="audit")
    assert cfg.fs_isolation is True
    assert cfg.extra_allowed_dirs is None


def test_extra_allowed_dirs_accepts_shared_alloweddir():
    from optio_opencode.types import AllowedDir

    cfg = OpencodeTaskConfig(
        consumer_instructions="x",
        delivery_type="audit",
        extra_allowed_dirs=[AllowedDir("/data", "ro"), AllowedDir("/work", "rwx")],
    )
    assert [d.mode for d in cfg.extra_allowed_dirs] == ["ro", "rwx"]


def test_allowed_disallowed_tools_default_none():
    cfg = OpencodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.allowed_tools is None
    assert cfg.disallowed_tools is None
