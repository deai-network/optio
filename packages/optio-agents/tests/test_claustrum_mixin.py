import dataclasses

import pytest

from optio_agents.config_types import ClaustrumConfigMixin


@dataclasses.dataclass(frozen=True)
class _Cfg(ClaustrumConfigMixin):
    name: str = "x"

    def __post_init__(self):
        self._validate_claustrum()


def test_triad_fields_present_and_defaulted():
    # fs_isolation=False so the validator does not require delivery_type.
    c = _Cfg(fs_isolation=False)
    assert c.fs_isolation is False
    assert c.extra_allowed_dirs is None
    assert c.delivery_type is None


def test_fs_isolation_on_requires_delivery_type():
    with pytest.raises(ValueError, match="delivery_type"):
        _Cfg(fs_isolation=True, delivery_type=None)


def test_fs_isolation_off_allows_missing_delivery_type():
    c = _Cfg(fs_isolation=False)
    assert c.delivery_type is None


def test_delivery_type_satisfies_the_rule():
    c = _Cfg(fs_isolation=True, delivery_type="audit")
    assert c.delivery_type == "audit"
