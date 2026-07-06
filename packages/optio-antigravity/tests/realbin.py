"""Shared capability probes + skip-gates for the opt-in real-binary suite.

Backs the row-30 real-binary checklist (plan Stage 10): every test that needs the
REAL ``agy`` binary (or a real Landlock kernel) imports its gate from here. Two
invariants, enforced uniformly so the guard is reproducible rather than a scatter
of one-off skips:

* **Opt-in.** A real-binary test never runs in the default suite. It requires an
  explicit env flag (slow / network / auth), AND every capability it needs to be
  probeably-present. Absent either, it SKIPS.
* **Precise skips, never a fake pass.** The skip reason names exactly which
  prerequisite was missing (flag / Linux / Landlock / binary / claustrum) so a
  maintainer on a provisioned host knows what to supply. A test that cannot
  exercise the real binary must skip — never assert a hollow success.

No real authed ``agy`` is present in CI or the dev worktree (it needs a Google
login), so all of these skip cleanly here; that is the point — the remaining
real-binary work is tracked (Stage 10), not silently green. Mirrors
optio-kimicode's ``realbin.py`` (kimi→agy).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

# --- where a real agy lives -------------------------------------------------

# The vendor installer drops ``agy`` on the login-shell PATH; optio's own cache
# keeps one at ``<cache>/agy`` (default ``~/.cache/optio-antigravity/bin/agy``).
# Prefer an agy already on PATH, then the documented cache locations.
_REAL_AGY_CANDIDATES = (
    Path.home() / ".cache" / "optio-antigravity" / "bin" / "agy",  # optio cache
    Path.home() / ".local" / "bin" / "agy",                        # vendor default
)


def _is_real_agy(path: str) -> bool:
    """True iff ``path`` is functionally the Antigravity ``agy`` — its ``--help``
    exits cleanly and names the tool. Mirrors ``host_actions._is_agy``: a
    real-binary probe MUST reject a name-colliding stranger, else the opt-in
    suite silently exercises the wrong binary.

    TODO(S1): tighten once the real ``agy --help`` banner is captured (the
    credential/login spike has not run); today we accept any agy whose help text
    mentions the tool."""
    try:
        r = subprocess.run(
            [path, "--help"], capture_output=True, timeout=20, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    blob = ((r.stdout or "") + (r.stderr or "")).lower()
    return r.returncode == 0 and ("antigravity" in blob or "agy" in blob)


def resolve_real_agy() -> "str | None":
    """Absolute path to a real ``agy`` binary, or None. PATH first, then the
    documented cache/install locations — every candidate is identity-checked so a
    name-colliding stranger is never returned."""
    found = shutil.which("agy")
    if found and _is_real_agy(found):
        return found
    for cand in _REAL_AGY_CANDIDATES:
        if cand.exists() and os.access(cand, os.X_OK) and _is_real_agy(str(cand)):
            return str(cand)
    return None


# --- Landlock / claustrum ----------------------------------------------------


def landlock_available() -> bool:
    """True iff the kernel exposes the Landlock LSM (claustrum's enforcement
    backend). Mirrors the grok/claudecode/kimicode probe."""
    try:
        return "landlock" in Path("/sys/kernel/security/lsm").read_text()
    except OSError:
        return False


def _detect_goarch() -> "str | None":
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())


def claustrum_binary(tmp: Path) -> "str | None":
    """A runnable claustrum binary, or None → skip. Resolution mirrors
    optio-kimicode's ``realbin``: (1) the engine build cache
    ``ensure_claustrum_installed`` writes under the per-engine cache dir, then
    (2) a source build from ``~/deai/claustrum`` if a Go toolchain is present."""
    goarch = _detect_goarch()
    if goarch is not None:
        # host_actions pins claustrum's tag under the optio-antigravity cache.
        from optio_agents.claustrum import CLAUSTRUM_PINNED_TAG

        cached = (
            Path.home() / ".cache" / "optio-antigravity" / "claustrum"
            / CLAUSTRUM_PINNED_TAG / goarch / "claustrum"
        )
        if cached.exists() and os.access(cached, os.X_OK):
            return str(cached)
    src = Path.home() / "deai" / "claustrum"
    if shutil.which("go") and src.is_dir():
        out = str(tmp / "claustrum")
        r = subprocess.run(
            ["go", "build", "-trimpath", "-o", out, "."],
            cwd=str(src),
            env={**os.environ, "CGO_ENABLED": "0"},
            capture_output=True,
        )
        if r.returncode == 0 and os.access(out, os.X_OK):
            return out
    return None


def landlock_enforces(claustrum: str) -> bool:
    """True iff claustrum can actually apply a Landlock ruleset on this kernel
    (some kernels expose the LSM but deny ruleset creation under seccomp/CI)."""
    r = subprocess.run(
        [claustrum, "--abi-min", "1", "--rox", "/usr", "--rox", "/bin",
         "--rox", "/lib", "--rox", "/lib64", "--", "/bin/true"],
        capture_output=True,
    )
    return r.returncode == 0


# --- composed skip gates -----------------------------------------------------


def sandbox_enforce_skip_reason(env_flag: str, *, need_agy: bool) -> "str | None":
    """Skip reason for a Landlock-enforcement test, or None to run. ``need_agy``
    gates whether a real agy binary is also required.

    Order matters: report the CHEAPEST-to-fix missing prerequisite first (the
    opt-in flag), so a maintainer sees "set X=1" before "install agy"."""
    if os.environ.get(env_flag) != "1":
        return f"opt-in: set {env_flag}=1 to run this Landlock-enforcement test"
    if platform.system() != "Linux":
        return "Landlock enforcement requires Linux"
    if not landlock_available():
        return "kernel Landlock LSM not available"
    if need_agy and resolve_real_agy() is None:
        return "real agy binary not found (PATH / ~/.local/bin/agy / optio cache)"
    return None
