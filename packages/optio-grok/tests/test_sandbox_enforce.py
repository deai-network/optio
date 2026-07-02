"""Real-grok Landlock enforcement test for Stage 8 (skip-if-no-landlock).

Unlike the rest of the suite (which uses the fake grok), this exercises the
REAL grok binary under the fail-closed custom ``optio`` sandbox profile and
verifies that a write OUTSIDE the sandbox (into the operator's real home) is
denied by the kernel — proving isolation is genuinely enforced, not merely
requested.

Skips cleanly unless ALL prerequisites are present: Linux, a kernel Landlock
LSM, a real ``grok`` binary, and an authenticated ``~/.grok/auth.json`` (a real
one-shot ``grok -p`` needs a live login). grok's Landlock applier opens
``/dev/tty``, so the child is run under a pty; without a controlling terminal
(the usual pytest case) the profile can't apply and the test skips with the
kernel's reason rather than failing.
"""

from __future__ import annotations

import json
import os
import platform
import pty
import select
import shutil
import time
from pathlib import Path

import pytest

from optio_grok.fs_allowlist import build_sandbox_toml
from optio_grok.host_actions import SANDBOX_PROFILE_NAME


_REAL_GROK_BIN = Path.home() / ".grok" / "bin" / "grok"


def _resolve_real_grok() -> "str | None":
    found = shutil.which("grok")
    if found:
        return found
    return str(_REAL_GROK_BIN) if _REAL_GROK_BIN.exists() else None


def _landlock_available() -> bool:
    try:
        return "landlock" in Path("/sys/kernel/security/lsm").read_text()
    except OSError:
        return False


def _skip_reason() -> "str | None":
    if platform.system() != "Linux":
        return "sandbox enforcement test requires Linux/Landlock"
    if not _landlock_available():
        return "kernel Landlock LSM not available"
    if _resolve_real_grok() is None:
        return "real grok binary not found"
    if not (Path.home() / ".grok" / "auth.json").exists():
        return "no authenticated grok (~/.grok/auth.json absent)"
    return None


pytestmark = pytest.mark.skipif(
    _skip_reason() is not None, reason=_skip_reason() or "",
)


def _run_grok_under_pty(argv: list[str], env: dict, *, timeout_s: float) -> str:
    """Run ``argv`` under a pty (grok's Landlock applier needs ``/dev/tty``).

    Returns the captured combined output. Reaps the child within ``timeout_s``.
    """
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.execve(argv[0], argv, env)
    out: list[bytes] = []
    start = time.time()
    while time.time() - start < timeout_s:
        r, _, _ = select.select([fd], [], [], 1.0)
        if r:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            out.append(data)
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    return b"".join(out).decode("utf-8", errors="replace")


def _profile_applied_event(grok_home: Path) -> "dict | None":
    """Return the last ProfileApplied event for the optio profile, or None."""
    log = grok_home / "sandbox-events.jsonl"
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    applied = None
    apply_failed = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if evt.get("profile") != SANDBOX_PROFILE_NAME:
            continue
        if evt.get("event_type") == "ProfileApplied":
            applied = evt
        elif evt.get("event_type") == "ApplyFailed":
            apply_failed = evt
    if applied is not None:
        return applied
    if apply_failed is not None:
        pytest.skip(
            "Landlock could not be applied in this environment: "
            f"{apply_failed.get('error')!r}"
        )
    return None


def test_real_grok_sandbox_blocks_out_of_tree_write(tmp_path: Path):
    grok_bin = _resolve_real_grok()
    workdir = tmp_path / "work"
    grok_home = workdir / "home" / ".grok"
    grok_home.mkdir(parents=True)

    # Seed the isolated home with the operator's real grok login so the
    # one-shot prompt can actually run (the sandbox is applied at process
    # startup regardless, but a live login lets grok attempt the tool call).
    real_home = Path.home()
    for name in ("auth.json", "config.toml"):
        src = real_home / ".grok" / name
        if src.exists():
            shutil.copy(src, grok_home / name)

    # Plant the fail-closed custom profile exactly as session._prepare does.
    (grok_home / "sandbox.toml").write_text(
        build_sandbox_toml(
            workdir=str(workdir),
            extra_allowed_dirs=None,
            host_home=str(real_home),
        ),
        encoding="utf-8",
    )

    # Probe target: OUTSIDE the sandbox (the operator's real home is not in
    # read_write). Normally writable by this user; must be denied under optio.
    probe = real_home / f".optio-sandbox-probe-{os.getpid()}"
    if probe.exists():
        probe.unlink()

    env = {
        **os.environ,
        "HOME": str(workdir / "home"),
        "GROK_HOME": str(grok_home),
        "XDG_CONFIG_HOME": str(workdir / "home" / ".config"),
        "XDG_DATA_HOME": str(workdir / "home" / ".local" / "share"),
        "XDG_CACHE_HOME": str(workdir / "home" / ".cache"),
    }
    argv = [
        grok_bin, "--sandbox", SANDBOX_PROFILE_NAME, "--no-leader",
        "--always-approve", "-p",
        f"Use your shell tool to run exactly this command: "
        f"echo HELLO > {probe}\nThen report whether it succeeded.",
    ]

    try:
        _run_grok_under_pty(argv, env, timeout_s=180.0)

        applied = _profile_applied_event(grok_home)
        # No event at all → grok never got far enough to apply the profile
        # (e.g. auth expired before startup logging). Not a sandbox result.
        if applied is None:
            pytest.skip(
                "grok logged no ProfileApplied event (could not verify "
                "enforcement; likely an auth/startup failure)"
            )
        # The kernel actually enforced the profile...
        assert applied.get("enforced") is True
        # ...and the operator's real home is NOT among the writable roots.
        assert str(real_home) not in (applied.get("read_write_paths") or [])
        # The forbidden out-of-sandbox write did not escape the sandbox.
        assert not probe.exists(), (
            "sandbox FAILED: grok wrote outside the allowlisted tree"
        )
    finally:
        if probe.exists():
            probe.unlink()
