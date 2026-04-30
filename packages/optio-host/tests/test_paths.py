"""Tests for per-task directory helpers."""

import os

import pytest

from optio_host.paths import task_dir
from optio_host.types import SSHConfig


CONSUMER = "optio-opencode"
SSH_DUMMY = SSHConfig(host="h", user="u", key_path="/tmp/k", port=22)


def test_local_task_dir_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    got = task_dir(ssh=None, process_id="my_task_1", consumer_name=CONSUMER)
    assert got == os.path.join(str(tmp_path), "my_task_1")


def test_local_task_dir_defaults_to_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_TASK_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    got = task_dir(ssh=None, process_id="alpha", consumer_name=CONSUMER)
    assert got == os.path.join(str(tmp_path), "optio-opencode", "alpha")


def test_local_task_dir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_TASK_ROOT", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    got = task_dir(ssh=None, process_id="beta", consumer_name=CONSUMER)
    assert got == os.path.join(str(tmp_path), ".local", "share", "optio-opencode", "beta")


def test_remote_task_dir_uses_env_override(monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_REMOTE_TASK_ROOT", "/var/optio-oc")
    got = task_dir(ssh=SSH_DUMMY, process_id="gamma", consumer_name=CONSUMER)
    assert got == "/var/optio-oc/gamma"


def test_remote_task_dir_default(monkeypatch):
    monkeypatch.delenv("OPTIO_OPENCODE_REMOTE_TASK_ROOT", raising=False)
    got = task_dir(ssh=SSH_DUMMY, process_id="delta", consumer_name=CONSUMER)
    assert got == "/tmp/optio-opencode/delta"


def test_process_id_safe_chars_only(tmp_path, monkeypatch):
    """Reject path-traversing or slash-containing process_ids."""
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path))
    with pytest.raises(ValueError):
        task_dir(ssh=None, process_id="../evil", consumer_name=CONSUMER)
    with pytest.raises(ValueError):
        task_dir(ssh=None, process_id="a/b", consumer_name=CONSUMER)


def test_consumer_name_drives_env_var_derivation(tmp_path, monkeypatch):
    """Different consumer_name → different env var read."""
    monkeypatch.setenv("OPTIO_RECIPE_EXECUTION_TASK_ROOT", str(tmp_path / "recipe"))
    monkeypatch.delenv("OPTIO_OPENCODE_TASK_ROOT", raising=False)
    got = task_dir(
        ssh=None, process_id="run-1", consumer_name="optio-recipe-execution",
    )
    assert got == os.path.join(str(tmp_path / "recipe"), "run-1")


def test_consumer_name_drives_dir_segment(tmp_path, monkeypatch):
    """consumer_name appears verbatim as the dir segment under XDG/HOME."""
    monkeypatch.delenv("OPTIO_RECIPE_EXECUTION_TASK_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    got = task_dir(
        ssh=None, process_id="run-2", consumer_name="optio-recipe-execution",
    )
    assert got == os.path.join(str(tmp_path), "optio-recipe-execution", "run-2")
