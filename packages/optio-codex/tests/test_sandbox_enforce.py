"""Real-codex sandbox enforcement test for Stage 8 (opt-in, env-gated).

Unlike the rest of the suite (fake codex), this exercises the REAL codex
binary's sandbox via ``codex sandbox -- <cmd>`` and verifies that a write
OUTSIDE the workspace (the operator's real home — /tmp and the cwd are
writable in workspace-write, so neither can serve as a deny target) is
denied by the kernel, a write INSIDE the cwd is allowed, and a
``writable_roots`` grant is honored — proving isolation is enforced, not
merely requested.

Divergence from grok's analogue: ``codex sandbox`` runs a raw command with
NO model call, so no auth is needed and the test costs nothing — it stays
opt-in (OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1) purely because it requires a
real binary and a sandbox-capable kernel (bubblewrap or Landlock). It NEVER
runs in the default suite.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest


# The pinned `codex sandbox` workspace-write invocation form (Plan E Task 0,
# codex-cli 0.142.5). The `codex sandbox` SUBCOMMAND has no -s/--sandbox flag;
# mode is selected only via `-c sandbox_mode=…` (the launch surfaces
# codex/codex exec take -s/--sandbox, the subcommand does not). See the design
# doc's Stage-8 "Pinned codex sandbox invocation" line. Update here if the pin
# differs on a version bump.
_WS_WRITE_ARGS: list[str] = ["-c", "sandbox_mode=workspace-write"]


def _cache_dir() -> Path:
    root = os.environ.get("OPTIO_CODEX_CACHE_DIR")
    if root:
        return Path(root)
    xdg = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(xdg) / "optio-codex" / "bin"


def _resolve_real_codex() -> "str | None":
    found = shutil.which("codex")
    if found:
        return found
    cached = _cache_dir() / "codex"
    return str(cached) if cached.exists() else None


def _skip_reason() -> "str | None":
    # Opt-in only: requires the REAL codex binary and a sandbox-capable
    # kernel. Never runs in the default suite.
    if os.environ.get("OPTIO_CODEX_SANDBOX_ENFORCE_TEST") != "1":
        return (
            "set OPTIO_CODEX_SANDBOX_ENFORCE_TEST=1 to run the real-codex "
            "enforcement test"
        )
    if platform.system() != "Linux":
        return "sandbox enforcement test requires Linux"
    if _resolve_real_codex() is None:
        return "real codex binary not found (PATH or optio cache)"
    return None


pytestmark = pytest.mark.skipif(
    _skip_reason() is not None, reason=_skip_reason() or "",
)


def _run_sandboxed(
    argv_tail: list[str], *, cwd: Path, codex_home: Path,
    extra_args: "list[str] | None" = None,
) -> subprocess.CompletedProcess:
    codex = _resolve_real_codex()
    env = {
        **os.environ,
        "HOME": str(cwd / "home"),
        "CODEX_HOME": str(codex_home),
    }
    return subprocess.run(
        [codex, "sandbox", *_WS_WRITE_ARGS, *(extra_args or []), "--",
         *argv_tail],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
    )


@pytest.fixture()
def sandbox_env(tmp_path: Path):
    """Workspace + throwaway CODEX_HOME; skip (not fail) when the kernel has
    no usable sandbox mechanism (that's an environment verdict, not a bug —
    the launch-time posture for such hosts is covered by Task 5)."""
    workdir = tmp_path / "work"
    codex_home = workdir / "home" / ".codex"
    codex_home.mkdir(parents=True)   # helper bins go to $CODEX_HOME/tmp/arg0/
    probe = _run_sandboxed(["true"], cwd=workdir, codex_home=codex_home)
    if probe.returncode != 0:
        pytest.skip(
            "codex sandbox cannot run here: "
            f"rc={probe.returncode} stderr={probe.stderr.strip()[:300]!r}"
        )
    return workdir, codex_home


def test_outside_write_denied(sandbox_env):
    workdir, codex_home = sandbox_env
    real_home = Path.home()
    canary = real_home / f".optio-codex-enforce-probe-{os.getpid()}"
    if canary.exists():
        canary.unlink()
    try:
        r = _run_sandboxed(
            ["touch", str(canary)], cwd=workdir, codex_home=codex_home,
        )
        assert not canary.exists(), (
            "sandbox FAILED: codex wrote outside the workspace "
            f"(rc={r.returncode}, stderr={r.stderr[:300]!r})"
        )
        assert r.returncode != 0
    finally:
        if canary.exists():
            canary.unlink()


def test_inside_write_allowed(sandbox_env):
    workdir, codex_home = sandbox_env
    target = workdir / "inside.txt"
    r = _run_sandboxed(
        ["touch", str(target)], cwd=workdir, codex_home=codex_home,
    )
    assert r.returncode == 0, r.stderr[:300]
    assert target.exists()


def test_writable_roots_grant_honored(sandbox_env, tmp_path: Path):
    """A -c sandbox_workspace_write.writable_roots grant makes an
    otherwise-denied dir writable — the exact plumbing optio's
    extra_allowed_dirs(rw) rides on. Uses a real-home subdir because /tmp is
    already writable in workspace-write."""
    workdir, codex_home = sandbox_env
    grant_dir = Path.home() / f".optio-codex-grant-{os.getpid()}"
    grant_dir.mkdir(exist_ok=True)
    target = grant_dir / "granted.txt"
    try:
        r = _run_sandboxed(
            ["touch", str(target)], cwd=workdir, codex_home=codex_home,
            extra_args=[
                "-c",
                f'sandbox_workspace_write.writable_roots=["{grant_dir}"]',
            ],
        )
        assert r.returncode == 0, r.stderr[:300]
        assert target.exists()
    finally:
        shutil.rmtree(grant_dir, ignore_errors=True)
