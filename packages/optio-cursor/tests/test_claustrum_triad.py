"""Task 5 (universal claustrum) — cursor adopts the shared claustrum triad.

The ``fs_isolation`` / ``extra_allowed_dirs`` / ``delivery_type`` triad now comes
from ``optio_agents.config_types.ClaustrumConfigMixin`` (inherited, top-level,
zero caller churn). Its shared validation makes ``delivery_type`` MANDATORY
whenever ``fs_isolation`` is on — a newer claustrum release may patch a
vulnerability the operator must hear about via ``on_deliverable``.
"""

import pytest

from optio_agents.config_types import ClaustrumConfigMixin
from optio_cursor.types import CursorTaskConfig


def test_config_inherits_shared_mixin():
    assert issubclass(CursorTaskConfig, ClaustrumConfigMixin)


def test_fs_isolation_on_requires_delivery_type():
    # Default config: fs_isolation defaults True, delivery_type unset -> raises.
    with pytest.raises(ValueError, match="delivery_type"):
        CursorTaskConfig(consumer_instructions="x")
    with pytest.raises(ValueError, match="delivery_type"):
        CursorTaskConfig(
            consumer_instructions="x", fs_isolation=True, delivery_type=None,
        )


def test_fs_isolation_off_allows_missing_delivery_type():
    c = CursorTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert c.fs_isolation is False
    assert c.delivery_type is None


def test_delivery_type_satisfies_the_rule():
    c = CursorTaskConfig(consumer_instructions="x", delivery_type="audit")
    assert c.fs_isolation is True
    assert c.delivery_type == "audit"
