"""session_base_path / session_env_remove: an agent session never inherits the
engine's virtualenv (sys.prefix / sys.base_prefix / VIRTUAL_ENV monkeypatched,
so nothing depends on the test runner's own layout)."""

from __future__ import annotations

import sys

import pytest

from optio_agents.session_env import (
    DEFAULT_PATH,
    ENGINE_PYTHON_ENV,
    session_base_path,
    session_env_remove,
)


def _in_venv(monkeypatch, venv: str, *, virtual_env: bool = False) -> str:
    monkeypatch.setattr(sys, "prefix", venv)
    monkeypatch.setattr(sys, "base_prefix", "/opt/base-python")
    if virtual_env:
        monkeypatch.setenv("VIRTUAL_ENV", venv)
    else:
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    return f"{venv}/bin"


def _not_in_venv(monkeypatch) -> None:
    monkeypatch.setattr(sys, "prefix", "/opt/base-python")
    monkeypatch.setattr(sys, "base_prefix", "/opt/base-python")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


def test_the_venv_first_is_removed(monkeypatch):
    venv_bin = _in_venv(monkeypatch, "/srv/engine/.venv")
    assert session_base_path(f"{venv_bin}:/usr/bin:/bin") == "/usr/bin:/bin"


def test_the_venv_in_the_middle_is_removed_and_the_order_kept(monkeypatch):
    venv_bin = _in_venv(monkeypatch, "/srv/engine/.venv")
    path = f"/home/u/.local/bin:{venv_bin}:/usr/local/bin:/usr/bin"
    assert session_base_path(path) == "/home/u/.local/bin:/usr/local/bin:/usr/bin"


def test_a_trailing_slash_still_matches(monkeypatch):
    venv_bin = _in_venv(monkeypatch, "/srv/engine/.venv")
    assert session_base_path(f"/a:{venv_bin}/:/b:{venv_bin}//") == "/a:/b"


def test_a_symlinked_venv_dir_still_matches(monkeypatch, tmp_path):
    real = tmp_path / "real-venv"
    (real / "bin").mkdir(parents=True)
    link = tmp_path / "linked-venv"
    link.symlink_to(real)
    # The engine runs through the link; PATH names the real directory.
    _in_venv(monkeypatch, str(link))
    assert session_base_path(f"{real}/bin:/usr/bin") == "/usr/bin"
    # And the other way round.
    _in_venv(monkeypatch, str(real))
    assert session_base_path(f"{link}/bin:/usr/bin") == "/usr/bin"


def test_virtual_env_alone_is_removed(monkeypatch):
    _not_in_venv(monkeypatch)
    monkeypatch.setenv("VIRTUAL_ENV", "/srv/other/.venv")
    assert session_base_path("/srv/other/.venv/bin:/usr/bin") == "/usr/bin"


def test_not_in_a_venv_the_path_is_unchanged(monkeypatch):
    _not_in_venv(monkeypatch)
    path = "/srv/engine/.venv/bin:/usr/bin::/bin:"
    assert session_base_path(path) == path


def test_nothing_left_falls_back(monkeypatch):
    venv_bin = _in_venv(monkeypatch, "/srv/engine/.venv", virtual_env=True)
    assert session_base_path(venv_bin) == DEFAULT_PATH
    assert session_base_path(f"{venv_bin}:{venv_bin}/") == DEFAULT_PATH


def test_the_default_is_the_engine_path(monkeypatch):
    venv_bin = _in_venv(monkeypatch, "/srv/engine/.venv")
    monkeypatch.setenv("PATH", f"{venv_bin}:/usr/bin:/bin")
    assert session_base_path() == "/usr/bin:/bin"
    assert session_base_path("") == "/usr/bin:/bin"
    monkeypatch.delenv("PATH")
    assert session_base_path() == DEFAULT_PATH


def test_a_non_venv_entry_of_the_same_prefix_is_kept(monkeypatch):
    _in_venv(monkeypatch, "/srv/engine/.venv")
    path = "/srv/engine/.venv/bin2:/srv/engine/bin:/usr/bin"
    assert session_base_path(path) == path


@pytest.mark.parametrize("patterns", [None, [], ["*_API_KEY", "HTTP_PROXY"]])
def test_session_env_remove_appends_the_engine_python_setup(patterns):
    got = session_env_remove(patterns)
    assert got == [*(patterns or []), *ENGINE_PYTHON_ENV]
    assert {"VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"} <= set(got)


def test_session_env_remove_does_not_change_its_argument():
    patterns = ["*_TOKEN"]
    session_env_remove(patterns)
    assert patterns == ["*_TOKEN"]
