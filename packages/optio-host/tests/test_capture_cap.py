"""Tests for the workdir capture cap setting and the regenerable list parser."""

import pytest

from optio_host.capture_cap import (
    DEFAULT_MAX_MB,
    ENV_VAR,
    REGENERABLE_LIST,
    parse_regenerable,
    workdir_capture_max_bytes,
)


def test_module_constants():
    assert ENV_VAR == "OPTIO_WORKDIR_CAPTURE_MAX_MB"
    assert DEFAULT_MAX_MB == 100
    assert REGENERABLE_LIST == "regenerable.txt"


def test_unset_gives_default(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert workdir_capture_max_bytes() == 104857600


def test_empty_gives_default(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "")
    assert workdir_capture_max_bytes() == 104857600


def test_zero_turns_it_off(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0")
    assert workdir_capture_max_bytes() == 0


def test_valid_value_is_used(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "250")
    assert workdir_capture_max_bytes() == 250 * 1048576


@pytest.mark.parametrize("bad", ["-1", "1.5", "lots"])
def test_invalid_value_raises(monkeypatch, bad):
    monkeypatch.setenv(ENV_VAR, bad)
    with pytest.raises(ValueError, match="OPTIO_WORKDIR_CAPTURE_MAX_MB"):
        workdir_capture_max_bytes()


def test_parse_regenerable_sanitises():
    text = (
        "# deps\n"
        "node_modules  # npm ci\n"
        "./frontend/node_modules/\n"
        "\n"
        "/abs\n"
        "../out\n"
        "a/../b\n"
        ".\n"
        "build[1]\n"
        "node_modules\n"
    )
    assert parse_regenerable(text) == [
        "node_modules",
        "frontend/node_modules",
        "build[1]",
    ]


def test_parse_regenerable_empty_text():
    assert parse_regenerable("") == []
