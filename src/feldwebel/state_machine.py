"""Process state machine — valid transitions and validation."""

VALID_TRANSITIONS: dict[str, set[str]] = {
    "idle": {"scheduled"},
    "scheduled": {"running", "cancel_requested"},
    "running": {"done", "failed", "cancel_requested"},
    "done": {"scheduled", "idle"},
    "failed": {"scheduled", "idle"},
    "cancel_requested": {"cancelling"},
    "cancelling": {"cancelled"},
    "cancelled": {"scheduled", "idle"},
}

ACTIVE_STATES = {"scheduled", "running", "cancel_requested", "cancelling"}
END_STATES = {"done", "failed", "cancelled"}
LAUNCHABLE_STATES = {"idle", "done", "failed", "cancelled"}
CANCELLABLE_STATES = {"scheduled", "running"}
DISMISSABLE_STATES = {"done", "failed", "cancelled"}


def can_transition(from_state: str, to_state: str) -> bool:
    """Check if a state transition is valid."""
    return to_state in VALID_TRANSITIONS.get(from_state, set())


def validate_transition(from_state: str, to_state: str) -> None:
    """Validate a state transition, raising ValueError if invalid."""
    if not can_transition(from_state, to_state):
        raise ValueError(f"Invalid state transition: {from_state} → {to_state}")
