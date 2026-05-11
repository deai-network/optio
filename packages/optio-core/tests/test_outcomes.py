"""Outcome dataclass smoke tests; real coverage added in later tasks."""

from optio_core.models import LaunchOutcome, CancelOutcome, DismissOutcome


def test_launch_outcome_ok():
    out = LaunchOutcome(ok=True)
    assert out.ok is True
    assert out.reason is None


def test_launch_outcome_failure_reason():
    out = LaunchOutcome(ok=False, reason="not-found")
    assert out.ok is False
    assert out.reason == "not-found"


def test_cancel_outcome_failure_reason():
    out = CancelOutcome(ok=False, reason="not-cancellable")
    assert out.ok is False
    assert out.reason == "not-cancellable"


def test_dismiss_outcome_failure_reason():
    out = DismissOutcome(ok=False, reason="not-dismissable")
    assert out.ok is False
    assert out.reason == "not-dismissable"


def test_outcomes_top_level_reexport():
    import optio_core
    assert optio_core.LaunchOutcome is LaunchOutcome
    assert optio_core.CancelOutcome is CancelOutcome
    assert optio_core.DismissOutcome is DismissOutcome
