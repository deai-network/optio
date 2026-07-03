"""Meta-test: the row-30 real-binary gates skip PRECISELY here (and are wired).

Unlike the opt-in real-binary tests (which skip and therefore assert nothing in a
worktree with no real kimi), THIS file always runs. It proves the guard itself is
sound: in an environment without the opt-in flags / a real kimi, every gate
returns a NON-EMPTY, specific skip reason that names the missing prerequisite —
so the row-30 suite fails safe (skips), never fakes a pass, and a maintainer can
see exactly what to provision.

If someone later deletes an opt-in check (making a billable/destructive test run
by default), or a probe starts silently returning a truthy "reason" of empty
string, this test catches it.
"""

from __future__ import annotations

import os

import realbin


def test_no_opt_in_flags_are_set_by_default():
    """None of the row-30 opt-in flags should be set in the default suite — if
    one is, the gated tests would run unexpectedly."""
    for flag in (
        "OPTIO_KIMICODE_REAL_E2E",
        "OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST",
        "OPTIO_KIMICODE_FS_ENFORCE_TEST",
        "OPTIO_KIMICODE_REAL_INSTALL",
        "OPTIO_KIMICODE_REAL_SEED_REFRESH",
        "OPTIO_KIMICODE_REAL_DEVICE_LOGIN",
    ):
        assert os.environ.get(flag) != "1", (
            f"{flag} is set — the opt-in real-binary suite would run by default"
        )


def test_real_kimi_gate_skips_with_precise_reason():
    reason = realbin.real_kimi_skip_reason("OPTIO_KIMICODE_REAL_E2E", need_creds=True)
    assert reason, "real-kimi gate returned no skip reason without the opt-in flag"
    # The cheapest-to-fix prerequisite (the flag) is named first.
    assert "OPTIO_KIMICODE_REAL_E2E" in reason


def test_sandbox_enforce_gate_skips_with_precise_reason():
    reason = realbin.sandbox_enforce_skip_reason(
        "OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST", need_kimi=True,
    )
    assert reason, "sandbox-enforce gate returned no skip reason without the flag"
    assert "OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST" in reason


def test_probes_do_not_crash():
    """The capability probes must degrade to a boolean/None, never raise, so a
    gate evaluation on any host is safe."""
    assert isinstance(realbin.landlock_available(), bool)
    assert isinstance(realbin.has_kimi_creds(), bool)
    assert realbin.resolve_real_kimi() is None or isinstance(
        realbin.resolve_real_kimi(), str
    )


def test_gate_opens_only_when_flag_and_capability_present(monkeypatch, tmp_path):
    """With the flag set but no real kimi, the gate still skips (naming the
    binary as missing) — proving the gate requires BOTH the opt-in AND the
    capability, not the flag alone."""
    monkeypatch.setenv("OPTIO_KIMICODE_REAL_E2E", "1")
    monkeypatch.setattr(realbin, "resolve_real_kimi", lambda: None)
    reason = realbin.real_kimi_skip_reason("OPTIO_KIMICODE_REAL_E2E", need_creds=True)
    assert reason and "binary not found" in reason
