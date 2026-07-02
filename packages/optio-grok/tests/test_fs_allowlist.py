"""Unit tests for the native-grok sandbox profile builder (Stage 8).

``build_sandbox_toml`` emits a custom ``[profiles.optio]`` sandbox profile
(``extends="strict"``) that grok launches under via ``--sandbox optio``. The
custom profile is fail-CLOSED: if grok can't apply it, it refuses to start
(built-in profiles fail-open, which is why optio uses a custom one).
"""

from __future__ import annotations

import tomllib

import pytest

from optio_grok.fs_allowlist import build_sandbox_toml
from optio_grok.types import AllowedDir


def _parse(toml_str: str) -> dict:
    return tomllib.loads(toml_str)["profiles"]["optio"]


def test_build_sandbox_toml_shape():
    toml_str = build_sandbox_toml(
        workdir="/w/task",
        extra_allowed_dirs=[
            AllowedDir("~/data", "ro"),
            AllowedDir("/scratch", "rw"),
        ],
        host_home="/home/u",
    )
    # A named custom profile so isolation is fail-closed.
    assert "[profiles.optio]" in toml_str
    prof = _parse(toml_str)
    assert prof["extends"] == "strict"
    # Baseline writable roots + rw extras.
    assert "/w/task" in prof["read_write"]
    assert "/tmp" in prof["read_write"]
    assert "/var/tmp" in prof["read_write"]
    assert "/scratch" in prof["read_write"]
    # ``~/`` in an extra grant expands against the REAL host home.
    assert "/home/u/data" in prof["read_only"]
    # No deny list → Landlock-only (no bubblewrap dependency).
    assert "deny" not in prof


def test_build_sandbox_toml_no_extras():
    toml_str = build_sandbox_toml(
        workdir="/w/task/", extra_allowed_dirs=None, host_home="/home/u",
    )
    prof = _parse(toml_str)
    assert prof["extends"] == "strict"
    # Trailing slash on the workdir is normalized.
    assert "/w/task" in prof["read_write"]
    assert prof.get("read_only", []) == []
    assert "deny" not in prof


def test_build_sandbox_toml_rw_extra_home_expansion():
    toml_str = build_sandbox_toml(
        workdir="/w/task",
        extra_allowed_dirs=[AllowedDir("~/cache", "rw")],
        host_home="/home/alice",
    )
    prof = _parse(toml_str)
    assert "/home/alice/cache" in prof["read_write"]


def test_allowed_dir_rejects_bad_mode():
    with pytest.raises(ValueError):
        AllowedDir("/x", "wx")  # type: ignore[arg-type]
