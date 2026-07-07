import pytest

from optio_codex import CodexTaskConfig
from optio_codex.types import AllowedDir


def test_defaults_and_validation():
    c = CodexTaskConfig(consumer_instructions="do it")
    assert c.mode == "iframe" and c.host_protocol is True and c.auto_start is False
    assert c.ask_for_approval == "never" and c.effective_sandbox_mode == "workspace-write"
    assert c.supports_resume is True
    assert c.workdir_exclude is None
    with pytest.raises(ValueError):
        CodexTaskConfig(consumer_instructions="x", install_dir="relative/path")
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


def test_supports_resume_flows_to_task_instance():
    from optio_codex import create_codex_task

    on = create_codex_task(
        process_id="p-resume-on", name="n",
        config=CodexTaskConfig(consumer_instructions="x"),
    )
    off = create_codex_task(
        process_id="p-resume-off", name="n",
        config=CodexTaskConfig(consumer_instructions="x", supports_resume=False),
    )
    assert on.supports_resume is True
    assert off.supports_resume is False


def _cfg(**kw):
    base = dict(consumer_instructions="do things")
    base.update(kw)
    return CodexTaskConfig(**base)


def test_conversation_mode_accepted():
    cfg = _cfg(mode="conversation")
    assert cfg.mode == "conversation"


def test_host_protocol_false_now_legal_in_conversation_mode():
    cfg = _cfg(mode="conversation", host_protocol=False)
    assert cfg.host_protocol is False


def test_host_protocol_false_still_rejected_in_iframe_mode():
    with pytest.raises(ValueError, match="host_protocol"):
        _cfg(mode="iframe", host_protocol=False)


def test_permission_gate_requires_conversation_mode():
    with pytest.raises(ValueError, match="permission_gate"):
        _cfg(permission_gate=True)
    assert _cfg(mode="conversation", permission_gate=True).permission_gate


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        _cfg(conversation_ui=True)
    assert _cfg(mode="conversation", conversation_ui=True).conversation_ui


def test_tool_verbosity_validated():
    with pytest.raises(ValueError, match="tool_verbosity"):
        _cfg(mode="conversation", tool_verbosity="loud")
    assert _cfg(mode="conversation", tool_verbosity="verbose").tool_verbosity == "verbose"


@pytest.mark.parametrize("field,value", [
    ("show_session_controls", True),
    ("native_spinner", True),
    ("show_file_upload", True),
    ("file_download", True),
])
def test_frontend_flags_require_conversation_ui(field, value):
    # Rejected without conversation_ui …
    with pytest.raises(ValueError, match=field):
        _cfg(mode="conversation", **{field: value})
    # … and accepted with it.
    cfg = _cfg(mode="conversation", conversation_ui=True, **{field: value})
    assert getattr(cfg, field) == value


def test_model_is_ungated_single_field():
    # C3: default_model dropped; the single `model` field carries the launch
    # model AND (in conversation_ui) the picker's initial value. It is valid
    # in every mode — no conversation_ui gate.
    assert _cfg(model="gpt-5.5").model == "gpt-5.5"
    assert _cfg(mode="conversation", model="gpt-5.5").model == "gpt-5.5"
    assert not hasattr(_cfg(), "default_model")


def test_upload_download_byte_limits_default():
    cfg = _cfg(mode="conversation", conversation_ui=True,
               show_file_upload=True, file_download=True)
    assert cfg.max_upload_bytes == 10_000_000
    assert cfg.max_download_bytes == 10_000_000


# --- Stage 8: filesystem-isolation config reconciliation --------------------


def test_allowed_dir_rejects_bad_mode():
    with pytest.raises(ValueError):
        AllowedDir("/x", "wx")  # type: ignore[arg-type]


def test_sandbox_defaults_derive_from_fs_isolation():
    on = CodexTaskConfig(consumer_instructions="x")
    assert on.fs_isolation is True
    assert on.sandbox is None
    assert on.effective_sandbox_mode == "workspace-write"
    off = CodexTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert off.effective_sandbox_mode == "danger-full-access"


def test_fs_isolation_forbids_danger_full_access():
    with pytest.raises(ValueError, match="danger-full-access"):
        CodexTaskConfig(
            consumer_instructions="x", sandbox="danger-full-access",
        )


def test_fs_isolation_off_forbids_restrictive_sandbox():
    with pytest.raises(ValueError, match="fs_isolation=False"):
        CodexTaskConfig(
            consumer_instructions="x",
            fs_isolation=False,
            sandbox="workspace-write",
        )


def test_explicit_read_only_with_fs_isolation_is_valid():
    c = CodexTaskConfig(consumer_instructions="x", sandbox="read-only")
    assert c.effective_sandbox_mode == "read-only"


def test_rw_grant_under_read_only_rejected():
    with pytest.raises(ValueError, match="read-only"):
        CodexTaskConfig(
            consumer_instructions="x",
            sandbox="read-only",
            extra_allowed_dirs=[AllowedDir("/scratch", "rw")],
        )


def test_ro_grant_always_accepted_and_noop():
    # codex workspace-write leaves the READ side open, so "ro" grants are
    # trivially satisfied (documented no-op) — accepted in every mode.
    c = CodexTaskConfig(
        consumer_instructions="x",
        sandbox="read-only",
        extra_allowed_dirs=[AllowedDir("~/data", "ro")],
    )
    assert c.extra_allowed_dirs[0].mode == "ro"


def test_network_access_requires_workspace_write():
    with pytest.raises(ValueError, match="network_access"):
        CodexTaskConfig(
            consumer_instructions="x", sandbox="read-only", network_access=True,
        )
    ok = CodexTaskConfig(consumer_instructions="x", network_access=True)
    assert ok.network_access is True


def test_allowed_dir_exported():
    import optio_codex

    assert optio_codex.AllowedDir is AllowedDir
