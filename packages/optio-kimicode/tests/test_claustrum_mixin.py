"""kimicode adopts the shared claustrum config mixin (Universal Claustrum Task 6).

KimiCodeTaskConfig inherits ``optio_agents.config_types.ClaustrumConfigMixin``,
so the fs-isolation triad (``fs_isolation`` / ``extra_allowed_dirs`` /
``delivery_type``) and its shared validation come from one source. The key new
contract: ``delivery_type`` is MANDATORY when ``fs_isolation`` is on (default),
because a newer claustrum release may patch a vulnerability the operator must be
told about immediately (routed via ``on_deliverable``).
"""

import pytest

from optio_agents.config_types import ClaustrumConfigMixin
from optio_kimicode.types import KimiCodeTaskConfig


def test_config_inherits_shared_mixin():
    assert issubclass(KimiCodeTaskConfig, ClaustrumConfigMixin)


def test_default_fs_isolation_on_requires_delivery_type():
    # fs_isolation defaults ON, so a config that omits delivery_type is invalid.
    with pytest.raises(ValueError, match="delivery_type"):
        KimiCodeTaskConfig(consumer_instructions="x")


def test_fs_isolation_on_requires_delivery_type():
    with pytest.raises(ValueError, match="delivery_type"):
        KimiCodeTaskConfig(consumer_instructions="x", fs_isolation=True)


def test_delivery_type_satisfies_the_rule():
    cfg = KimiCodeTaskConfig(consumer_instructions="x", delivery_type="audit")
    assert cfg.delivery_type == "audit"
    assert cfg.fs_isolation is True


def test_fs_isolation_off_allows_missing_delivery_type():
    cfg = KimiCodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert cfg.delivery_type is None
