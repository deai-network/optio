"""Tests for per-task directory helpers."""

import os

import pytest

from optio_opencode.paths import local_taskdir, remote_taskdir


def test_local_taskdir_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    got = local_taskdir("my_task_1")
    assert got == os.path.join(str(tmp_path), "my_task_1")


def test_local_taskdir_defaults_to_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_TASK_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    got = local_taskdir("alpha")
    assert got == os.path.join(str(tmp_path), "optio-opencode", "alpha")


def test_local_taskdir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_TASK_ROOT", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    got = local_taskdir("beta")
    assert got == os.path.join(str(tmp_path), ".local", "share", "optio-opencode", "beta")


def test_remote_taskdir_uses_env_override(monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_REMOTE_TASK_ROOT", "/var/optio-oc")
    got = remote_taskdir("gamma")
    assert got == "/var/optio-oc/gamma"


def test_remote_taskdir_default(monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_REMOTE_TASK_ROOT", raising=False)
    got = remote_taskdir("delta")
    assert got == "/tmp/optio-opencode/delta"


def test_process_id_safe_chars_only(tmp_path, monkeypatch):
    """Reject path-traversing or slash-containing process_ids."""
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    with pytest.raises(ValueError):
        local_taskdir("../evil")
    with pytest.raises(ValueError):
        local_taskdir("a/b")
