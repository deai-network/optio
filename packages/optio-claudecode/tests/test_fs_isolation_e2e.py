"""End-to-end: the allowlist optio computes, fed to a real claustrum binary,
actually confines the command to the workdir.

This ties together the security-critical path: ``fs_allowlist.build_grant_flags``
(what optio emits per task) -> the ``claustrum --best-effort --abi-min 1 <grants>
-- CMD`` wrapper (what the launch builds) -> real Landlock enforcement. It asserts
a read INSIDE the workdir succeeds and a read OUTSIDE it is denied (EACCES).

The full session launch wiring (that this wrapper is what gets run) is covered by
the wrap-threading unit tests in test_claustrum_wrap.py; here we verify the
wrapper genuinely confines on a Landlock-capable kernel.

Skips unless a claustrum binary can be obtained (built from the local source repo
with a Go toolchain) and Landlock is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import pathlib

import pytest

from optio_claudecode import fs_allowlist


def _claustrum_binary(tmp: pathlib.Path) -> str | None:
    """Return a path to a claustrum binary, building it from the local source
    repo if a Go toolchain is present. None -> skip."""
    # 1) An already-built engine cache (what ensure_claustrum_installed writes).
    cached = os.path.expanduser(
        "~/.cache/optio-claudecode/claustrum/v0.1.1/amd64/claustrum"
    )
    if os.path.exists(cached) and os.access(cached, os.X_OK):
        return cached
    # 2) Build from the source checkout next to optio, if Go is available.
    src = os.path.expanduser("~/deai/claustrum")
    if shutil.which("go") and os.path.isdir(src):
        out = str(tmp / "claustrum")
        r = subprocess.run(
            ["go", "build", "-trimpath", "-o", out, "."],
            cwd=src,
            env={**os.environ, "CGO_ENABLED": "0"},
            capture_output=True,
        )
        if r.returncode == 0 and os.access(out, os.X_OK):
            return out
    return None


def _landlock_ok(claustrum: str) -> bool:
    """True iff claustrum can apply a Landlock ruleset on this kernel."""
    r = subprocess.run(
        [claustrum, "--abi-min", "1", "--rox", "/usr", "--rox", "/bin",
         "--rox", "/lib", "--rox", "/lib64", "--", "/bin/true"],
        capture_output=True,
    )
    return r.returncode == 0


def test_optio_allowlist_confines_to_workdir(tmp_path):
    claustrum = _claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary (need Go + ~/deai/claustrum or engine cache)")
    if not _landlock_ok(claustrum):
        pytest.skip("Landlock unavailable on this kernel")

    workdir = tmp_path / "wd"
    (workdir / "home").mkdir(parents=True)
    inside = workdir / "inside.txt"
    inside.write_text("allowed")

    # A secret OUTSIDE the workdir (the kind of host file we must never leak).
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    secret = secret_dir / "secret.txt"
    secret.write_text("nope")

    # The exact grant flags optio computes for a task, then the exact wrapper the
    # launch builds. claude_cache_dir points at a real dir so --rox has a target.
    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir),
        claude_cache_dir="/usr",
        extra_allowed_dirs=None,
    )
    wrap = [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]

    # Inside the workdir -> allowed.
    r_in = subprocess.run([*wrap, "/bin/cat", str(inside)], capture_output=True)
    assert r_in.returncode == 0, (
        f"reading inside the workdir should succeed: {r_in.stderr.decode()}"
    )
    assert r_in.stdout == b"allowed"

    # Outside the workdir -> denied (Landlock EACCES; cat exits non-zero).
    r_out = subprocess.run([*wrap, "/bin/cat", str(secret)], capture_output=True)
    assert r_out.returncode != 0, "reading a secret outside the workdir must be denied"


def test_extra_allowed_dirs_widen_the_jail(tmp_path):
    claustrum = _claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary")
    if not _landlock_ok(claustrum):
        pytest.skip("Landlock unavailable on this kernel")

    from optio_claudecode.types import AllowedDir

    workdir = tmp_path / "wd"
    (workdir / "home").mkdir(parents=True)
    extra = tmp_path / "extra"
    extra.mkdir()
    f = extra / "data.txt"
    f.write_text("shared")

    # Without the extra grant: denied.
    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir), claude_cache_dir="/usr", extra_allowed_dirs=None)
    wrap = [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]
    assert subprocess.run([*wrap, "/bin/cat", str(f)], capture_output=True).returncode != 0

    # With the caller-supplied extra (ro): allowed.
    grants2 = fs_allowlist.build_grant_flags(
        workdir=str(workdir), claude_cache_dir="/usr",
        extra_allowed_dirs=[AllowedDir(path=str(extra), mode="ro")])
    wrap2 = [claustrum, "--best-effort", "--abi-min", "1", *grants2, "--"]
    r = subprocess.run([*wrap2, "/bin/cat", str(f)], capture_output=True)
    assert r.returncode == 0, f"extra_allowed_dirs should permit the read: {r.stderr.decode()}"
    assert r.stdout == b"shared"
