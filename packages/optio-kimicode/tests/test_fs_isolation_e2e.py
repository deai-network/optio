"""Real-Landlock enforcement of the optio-kimicode allowlist (row-30, checklist
item 3 — foundation).

Ties the security-critical path end to end: ``fs_allowlist.build_grant_flags``
(what optio emits per task) → the ``claustrum --best-effort --abi-min 1 <grants>
-- CMD`` wrapper (what ``_build_claustrum_wrap`` builds for both the kimi-web and
kimi-acp launches) → REAL Landlock enforcement by a real claustrum binary. It
asserts a read INSIDE the workdir succeeds and a read OUTSIDE it is denied
(EACCES), and that ``extra_allowed_dirs`` genuinely widens the jail.

Unlike the rest of the row-30 suite this needs NO real kimi and NO creds — the
enforcement under test is claustrum's, exercised with ``/bin/cat`` as a stand-in
tool subprocess. It is therefore the one real-binary check that can run wherever
Landlock + a claustrum binary are available. It still skips cleanly when neither
is: opt-in ``OPTIO_KIMICODE_FS_ENFORCE_TEST=1`` keeps real Landlock out of the
default suite. Ported from optio-claudecode's ``test_fs_isolation_e2e.py``
(claude→kimi; the grant builder is ``kimi_cache_dir`` here).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from optio_kimicode import fs_allowlist
from optio_kimicode.types import AllowedDir

from realbin import claustrum_binary, landlock_enforces

_FLAG = "OPTIO_KIMICODE_FS_ENFORCE_TEST"

pytestmark = pytest.mark.skipif(
    os.environ.get(_FLAG) != "1",
    reason=f"opt-in: set {_FLAG}=1 to run the real-Landlock allowlist test",
)


def _wrap(claustrum: str, grants: list[str]) -> list[str]:
    # Exactly what host_actions._build_claustrum_wrap prepends ahead of the
    # launch argv.
    return [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]


def test_optio_allowlist_confines_to_workdir(tmp_path: Path):
    claustrum = claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary (need Go + ~/deai/claustrum or engine cache)")
    if not landlock_enforces(claustrum):
        pytest.skip("Landlock unavailable / cannot apply a ruleset on this kernel")

    workdir = tmp_path / "wd"
    (workdir / "home").mkdir(parents=True)
    inside = workdir / "inside.txt"
    inside.write_text("allowed")

    # A secret OUTSIDE the workdir (the kind of host file we must never leak).
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    secret = secret_dir / "secret.txt"
    secret.write_text("nope")

    # The exact grant flags optio computes for a task. kimi_cache_dir points at a
    # real dir so the --rox grant has an existing target.
    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir),
        kimi_cache_dir="/usr",
        extra_allowed_dirs=None,
    )
    wrap = _wrap(claustrum, grants)

    r_in = subprocess.run([*wrap, "/bin/cat", str(inside)], capture_output=True)
    assert r_in.returncode == 0, (
        f"reading inside the workdir should succeed: {r_in.stderr.decode()}"
    )
    assert r_in.stdout == b"allowed"

    r_out = subprocess.run([*wrap, "/bin/cat", str(secret)], capture_output=True)
    assert r_out.returncode != 0, "reading a secret outside the workdir must be denied"


def test_extra_allowed_dirs_widen_the_jail(tmp_path: Path):
    claustrum = claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary")
    if not landlock_enforces(claustrum):
        pytest.skip("Landlock unavailable / cannot apply a ruleset on this kernel")

    workdir = tmp_path / "wd"
    (workdir / "home").mkdir(parents=True)
    extra = tmp_path / "extra"
    extra.mkdir()
    f = extra / "data.txt"
    f.write_text("shared")

    # Without the extra grant: denied.
    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir), kimi_cache_dir="/usr", extra_allowed_dirs=None,
    )
    wrap = _wrap(claustrum, grants)
    assert subprocess.run(
        [*wrap, "/bin/cat", str(f)], capture_output=True,
    ).returncode != 0

    # With the caller-supplied extra (ro): allowed.
    grants2 = fs_allowlist.build_grant_flags(
        workdir=str(workdir), kimi_cache_dir="/usr",
        extra_allowed_dirs=[AllowedDir(path=str(extra), mode="ro")],
    )
    wrap2 = _wrap(claustrum, grants2)
    r = subprocess.run([*wrap2, "/bin/cat", str(f)], capture_output=True)
    assert r.returncode == 0, (
        f"extra_allowed_dirs should permit the read: {r.stderr.decode()}"
    )
    assert r.stdout == b"shared"
