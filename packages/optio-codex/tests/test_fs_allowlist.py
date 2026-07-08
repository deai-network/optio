"""Unit tests for the codex native-sandbox settings SSOT (Stage 8).

codex divergence from grok: no planted profile file — settings render to
``--sandbox <mode>`` + ``-c sandbox_workspace_write.*`` CLI overrides
(iframe/exec surfaces) and, for the ``codex app-server`` launch (which has NO
``--sandbox`` flag — the mode travels via ``thread/start``'s ``sandbox``
kebab-enum field), just the ``-c`` overrides via
:func:`build_sandbox_config_overrides`. ``ro`` grants are a documented no-op
(codex workspace-write leaves reads open).
"""

from __future__ import annotations

from optio_codex.fs_allowlist import (
    SandboxSettings,
    build_sandbox_cli_args,
    build_sandbox_config_overrides,
    resolve_sandbox_settings,
)
from optio_codex.types import AllowedDir, CodexTaskConfig


def _cfg(**kw) -> CodexTaskConfig:
    return CodexTaskConfig(consumer_instructions="x", delivery_type="audit", **kw)


def test_resolve_default_danger_full_access_no_extras():
    # Default posture: fs_isolation=True → claustrum owns fs, native mode
    # resolves to danger-full-access with no writable_roots / network.
    s = resolve_sandbox_settings(_cfg(), host_home="/home/u")
    assert s == SandboxSettings(
        mode="danger-full-access", writable_roots=(), network_access=False,
    )


def test_resolve_rw_extras_expand_against_real_host_home():
    # rw extras / host-home expansion live on codex's NATIVE workspace-write
    # path, reachable only standalone (fs_isolation=False + explicit sandbox).
    s = resolve_sandbox_settings(
        _cfg(fs_isolation=False, sandbox="workspace-write", extra_allowed_dirs=[
            AllowedDir("~/cache", "rw"),
            AllowedDir("/scratch/", "rw"),
            AllowedDir("~/data", "ro"),   # no-op: codex reads are open
        ]),
        host_home="/home/alice",
    )
    assert s.writable_roots == ("/home/alice/cache", "/scratch")


def test_resolve_native_mode_decoupled_from_fs_isolation():
    # Native mode no longer follows fs_isolation (claustrum owns fs now):
    # fs_isolation=False does NOT auto-pick workspace-write — with sandbox unset
    # the default resolves to danger-full-access.
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


# --- app-server config overrides (Task 4) ----------------------------------
# The `codex app-server` launch has no `--sandbox` flag: the mode rides
# thread/start's `sandbox` field, and writable_roots/network_access ride the
# `-c sandbox_workspace_write.*` overrides — the SAME strings the iframe uses.


def test_config_overrides_workspace_write():
    ov = build_sandbox_config_overrides(SandboxSettings(
        mode="workspace-write",
        writable_roots=("/home/u/cache", "/scratch"),
        network_access=True,
    ))
    assert (
        'sandbox_workspace_write.writable_roots=["/home/u/cache", "/scratch"]'
        in ov
    )
    assert "sandbox_workspace_write.network_access=true" in ov
    assert ov.count("-c") == 2
    # NO --sandbox flag: app-server takes the mode via thread/start instead.
    assert "--sandbox" not in ov


def test_config_overrides_other_modes_empty():
    assert build_sandbox_config_overrides(SandboxSettings(mode="read-only")) == []
    assert build_sandbox_config_overrides(
        SandboxSettings(mode="danger-full-access")
    ) == []
    # workspace-write with no extras yields no overrides either.
    assert build_sandbox_config_overrides(
        SandboxSettings(mode="workspace-write")
    ) == []


def test_cli_args_reuse_config_overrides():
    # build_sandbox_cli_args == mode flag + the shared config overrides
    # (one SSOT, two launch surfaces).
    s = SandboxSettings(
        mode="workspace-write", writable_roots=("/s",), network_access=True,
    )
    assert build_sandbox_cli_args(s) == [
        "--sandbox", "workspace-write", *build_sandbox_config_overrides(s),
    ]
