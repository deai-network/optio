import pytest

from optio_cursor.types import CursorTaskConfig


def test_defaults_and_validation():
    c = CursorTaskConfig(consumer_instructions="do it")
    assert c.mode == "iframe" and c.host_protocol is True and c.force is False
    # auto_start defaults False (parity) — a task must opt in to the kickoff,
    # else an interactive/conversation task auto-fires and blocks the first chat.
    assert c.auto_start is False
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", sandbox="nope")


def test_install_dirs_must_be_absolute():
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", cursor_install_dir="rel/path")
    with pytest.raises(ValueError):
        CursorTaskConfig(consumer_instructions="x", ttyd_install_dir="rel/path")
    CursorTaskConfig(
        consumer_instructions="x",
        cursor_install_dir="/opt/cursor",
        ttyd_install_dir="~/bin",
    )
