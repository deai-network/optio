"""Unit tests for the codex native-sandbox settings SSOT (Stage 8).

codex divergence from grok: no planted profile file — settings render to
``--sandbox <mode>`` + ``-c sandbox_workspace_write.*`` CLI overrides (and,
in Task 4, an app-server ``sandboxPolicy``). ``ro`` grants are a documented
no-op (codex workspace-write leaves reads open).
"""

from __future__ import annotations

from optio_codex.fs_allowlist import (
    SandboxSettings,
    build_sandbox_cli_args,
    resolve_sandbox_settings,
)
from optio_codex.types import AllowedDir, CodexTaskConfig


def _cfg(**kw) -> CodexTaskConfig:
    return CodexTaskConfig(consumer_instructions="x", **kw)


def test_resolve_default_workspace_write_no_extras():
    s = resolve_sandbox_settings(_cfg(), host_home="/home/u")
    assert s == SandboxSettings(
        mode="workspace-write", writable_roots=(), network_access=False,
    )


def test_resolve_rw_extras_expand_against_real_host_home():
    s = resolve_sandbox_settings(
        _cfg(extra_allowed_dirs=[
            AllowedDir("~/cache", "rw"),
            AllowedDir("/scratch/", "rw"),
            AllowedDir("~/data", "ro"),   # no-op: codex reads are open
        ]),
        host_home="/home/alice",
    )
    assert s.writable_roots == ("/home/alice/cache", "/scratch")


def test_resolve_fs_isolation_off_is_danger_full_access():
    s = resolve_sandbox_settings(_cfg(fs_isolation=False), host_home="/home/u")
    assert s.mode == "danger-full-access"
    assert s.writable_roots == ()
    assert s.network_access is False


def test_cli_args_minimal_default():
    args = build_sandbox_cli_args(SandboxSettings(mode="workspace-write"))
    assert args == ["--sandbox", "workspace-write"]


def test_cli_args_with_roots_and_network():
    args = build_sandbox_cli_args(SandboxSettings(
        mode="workspace-write",
        writable_roots=("/home/u/cache", "/scratch"),
        network_access=True,
    ))
    assert args[:2] == ["--sandbox", "workspace-write"]
    assert (
        'sandbox_workspace_write.writable_roots=["/home/u/cache", "/scratch"]'
        in args
    )
    assert "sandbox_workspace_write.network_access=true" in args
    # every override rides its own -c
    assert args.count("-c") == 2


def test_cli_args_read_only_and_danger_have_no_overrides():
    assert build_sandbox_cli_args(SandboxSettings(mode="read-only")) == [
        "--sandbox", "read-only",
    ]
    assert build_sandbox_cli_args(
        SandboxSettings(mode="danger-full-access")
    ) == ["--sandbox", "danger-full-access"]
