"""Shared capability probes + skip-gates for the opt-in real-binary E2E suite.

Backs the Appendix A row-30 checklist (plan Task 6.3): every test that needs the
REAL ``kimi`` binary (or real creds, or a real Landlock kernel) lives in a
``test_*`` file that imports its gate from here. Two invariants, enforced
uniformly so the guard is reproducible rather than a scatter of one-off skips:

* **Opt-in.** A real-binary test never runs in the default suite. It requires an
  explicit env flag (billable / slow / network / creds), AND every capability it
  needs to be probeable-present. Absent either, it SKIPS.
* **Precise skips, never a fake pass.** The skip reason names exactly which
  prerequisite was missing (flag / Linux / Landlock / binary / creds / claustrum)
  so a maintainer on a provisioned host knows what to supply to make it run. A
  test that cannot exercise the real binary must skip — it must never assert a
  hollow success.

No real authed kimi is present in CI or the dev worktree, so all of these skip
cleanly here; that is the point — the remaining real-binary work is tracked
(docs/2026-07-03-optio-kimicode-parity.md), not silently green.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

# --- where a real kimi / its creds live -------------------------------------

# The vendor installer drops the single binary at ``$KIMI_INSTALL_DIR/bin/kimi``
# (default ``~/.kimi-code/bin/kimi``); optio's own cache keeps one at
# ``<cache>/kimi``. Prefer a kimi already on PATH, then the documented locations.
_REAL_KIMI_CANDIDATES = (
    Path.home() / ".kimi-code" / "bin" / "kimi",             # vendor installer default
    Path.home() / ".cache" / "optio-kimicode" / "bin" / "kimi",  # optio cache
    Path.home() / ".local" / "bin" / "kimi",                 # legacy documented path
)


def _is_real_kimicode(path: str) -> bool:
    """True iff ``path`` is **kimi-code**, not the name-colliding Python
    ``kimi-cli`` (same ``kimi`` command name). kimi-code ships ``server run``;
    kimi-cli answers "No such command 'server'". Mirrors
    ``host_actions._is_kimicode`` — a real-binary probe MUST reject the wrong
    product, else the whole opt-in suite silently exercises kimi-cli (or skips
    when a real kimi-code IS installed but under a name it doesn't recognise)."""
    try:
        r = subprocess.run(
            [path, "server", "run", "--help"],
            capture_output=True, timeout=20,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_real_kimi() -> "str | None":
    """Absolute path to a real **kimi-code** binary, or None. PATH first, then the
    documented install/cache locations — every candidate is identity-checked so a
    leftover kimi-cli is never returned."""
    found = shutil.which("kimi")
    if found and _is_real_kimicode(found):
        return found
    for cand in _REAL_KIMI_CANDIDATES:
        if cand.exists() and os.access(cand, os.X_OK) and _is_real_kimicode(str(cand)):
            return str(cand)
    return None


def kimi_creds_path() -> Path:
    """The operator's kimi credential file: ``$KIMI_CODE_HOME/credentials/
    kimi-code.json`` (default home ``~/.kimi-code``). Mirrors verify.py's
    ``_CRED_MEMBER`` and the design's auth-storage note."""
    home = os.environ.get("KIMI_CODE_HOME") or str(Path.home() / ".kimi-code")
    return Path(home) / "credentials" / "kimi-code.json"


def has_kimi_creds() -> bool:
    return kimi_creds_path().exists()


# --- Landlock / claustrum ----------------------------------------------------


def landlock_available() -> bool:
    """True iff the kernel exposes the Landlock LSM (claustrum's enforcement
    backend). Mirrors the grok/claudecode probe."""
    try:
        return "landlock" in Path("/sys/kernel/security/lsm").read_text()
    except OSError:
        return False


def _detect_goarch() -> "str | None":
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())


def claustrum_binary(tmp: Path) -> "str | None":
    """A runnable claustrum binary, or None → skip. Resolution mirrors
    optio-claudecode's ``test_fs_isolation_e2e``: (1) the engine build cache
    ``ensure_claustrum_installed`` writes, then (2) a source build from
    ``~/deai/claustrum`` if a Go toolchain is present."""
    goarch = _detect_goarch()
    if goarch is not None:
        # host_actions pins v0.1.1 under the optio-kimicode cache.
        cached = (
            Path.home() / ".cache" / "optio-kimicode" / "claustrum"
            / "v0.1.1" / goarch / "claustrum"
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


def real_kimi_skip_reason(env_flag: str, *, need_creds: bool) -> "str | None":
    """Skip reason for a test that drives the REAL kimi binary, or None to run.

    Order matters: report the CHEAPEST-to-fix missing prerequisite first (the
    opt-in flag), so a maintainer sees "set X=1" before "install kimi".
    """
    if os.environ.get(env_flag) != "1":
        return f"opt-in: set {env_flag}=1 to run this real-kimi test"
    if resolve_real_kimi() is None:
        return "real kimi binary not found (PATH / ~/.local/bin/kimi / optio cache)"
    if need_creds and not has_kimi_creds():
        return f"no authenticated kimi ({kimi_creds_path()} absent)"
    return None


def sandbox_enforce_skip_reason(env_flag: str, *, need_kimi: bool) -> "str | None":
    """Skip reason for a Landlock-enforcement test. ``need_kimi`` gates whether a
    real kimi binary is also required (the claustrum-only allowlist test does
    not)."""
    if os.environ.get(env_flag) != "1":
        return f"opt-in: set {env_flag}=1 to run this Landlock-enforcement test"
    if platform.system() != "Linux":
        return "Landlock enforcement requires Linux"
    if not landlock_available():
        return "kernel Landlock LSM not available"
    if need_kimi and resolve_real_kimi() is None:
        return "real kimi binary not found (PATH / ~/.local/bin/kimi / optio cache)"
    return None
