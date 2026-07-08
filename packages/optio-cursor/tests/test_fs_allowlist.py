"""Cursor's claustrum fs-isolation config surface.

The grant-flag builder itself now lives in the shared
``optio_agents.fs_grants.build_grant_flags`` (unit-tested in optio-agents);
cursor consumes it via session.py's ``_build_claustrum_wrap`` and exercises the
full wiring end-to-end in ``test_session_fs_isolation.py``. This module only
guards that ``CursorTaskConfig`` carries the inherited claustrum triad.
"""

import pytest

from optio_cursor.types import AllowedDir, CursorTaskConfig


def test_config_carries_fs_isolation_and_extra_dirs():
    # fs_isolation defaults on -> delivery_type is mandatory.
    cfg = CursorTaskConfig(consumer_instructions="x", delivery_type="audit")
    assert cfg.fs_isolation is True
    assert cfg.extra_allowed_dirs is None
    assert cfg.delivery_type == "audit"
    cfg2 = CursorTaskConfig(
        consumer_instructions="x",
        fs_isolation=False,
        extra_allowed_dirs=[AllowedDir(path="/data", mode="ro")],
    )
    assert cfg2.fs_isolation is False
    assert cfg2.extra_allowed_dirs[0].path == "/data"


def test_fs_isolation_on_without_delivery_type_raises():
    with pytest.raises(ValueError, match="delivery_type"):
        CursorTaskConfig(consumer_instructions="x")
