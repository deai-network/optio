"""Tests for state machine."""

import pytest
from feldwebel.state_machine import can_transition, validate_transition, LAUNCHABLE_STATES


def test_valid_transitions():
    assert can_transition("idle", "scheduled")
    assert can_transition("scheduled", "running")
    assert can_transition("running", "done")
    assert can_transition("running", "failed")
    assert can_transition("running", "cancel_requested")
    assert can_transition("cancel_requested", "cancelling")
    assert can_transition("cancelling", "cancelled")
    assert can_transition("done", "scheduled")
    assert can_transition("failed", "scheduled")
    assert can_transition("cancelled", "scheduled")
    assert can_transition("done", "idle")
    assert can_transition("failed", "idle")
    assert can_transition("cancelled", "idle")


def test_invalid_transitions():
    assert not can_transition("idle", "running")
    assert not can_transition("idle", "done")
    assert not can_transition("running", "idle")
    assert not can_transition("done", "running")
    assert not can_transition("scheduled", "done")


def test_validate_raises():
    with pytest.raises(ValueError, match="Invalid state transition"):
        validate_transition("idle", "running")


def test_launchable_states():
    assert LAUNCHABLE_STATES == {"idle", "done", "failed", "cancelled"}
