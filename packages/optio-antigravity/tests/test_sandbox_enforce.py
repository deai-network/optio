"""Real-agy iframe surface under Landlock (Stage 10, checklist items 1 + 3).

Runs the REAL ``agy`` binary wrapped by a REAL claustrum Landlock jail built from
the exact grant flags optio computes per task (``fs_allowlist.build_grant_flags``
→ ``claustrum --best-effort --abi-min 1 <grants> --``, i.e. what
``host_actions._build_claustrum_wrap`` prepends and ``_build_agy_shell_command``
places ahead of the agy invocation in the tmux pane). It proves the real binary
LAUNCHES and its stdout SURVIVES the jail — the sandbox does not break agy's own
execution nor swallow its output when confined.

NON-billable / no-auth: it drives ``agy --help`` (prints a banner, no PTY, no
network, no model, no Google login) through the confining wrap and asserts the
banner appears. The genuine kernel-level DENY of an out-of-tree write is
claustrum's own contract (covered in optio_agents/tests); here the claim is that
real agy runs confined under optio's computed grants. The full interactive
TUI-drive to ``DONE`` (checklist item 1, in full) is the Stage-10 real-binary
acceptance run — it needs a Google login and a PTY-attached ttyd session.
Mirrors optio-kimicode's ``test_sandbox_enforce.py`` (kimi ``server run`` banner
→ agy ``--help`` banner).

Skips cleanly unless ALL prerequisites are present: opt-in flag, Linux, a kernel
Landlock LSM, a real ``agy`` binary, and a runnable claustrum.
"""

from __future__ import annotations

import os
import select
import subprocess
import time
from pathlib import Path

import pytest

from optio_antigravity import fs_allowlist, host_actions

from realbin import claustrum_binary, resolve_real_agy, sandbox_enforce_skip_reason

_FLAG = "OPTIO_ANTIGRAVITY_SANDBOX_ENFORCE_TEST"

pytestmark = pytest.mark.skipif(
    sandbox_enforce_skip_reason(_FLAG, need_agy=True) is not None,
    reason=sandbox_enforce_skip_reason(_FLAG, need_agy=True) or "",
)


def test_real_agy_launches_under_landlock(tmp_path: Path):
    agy = resolve_real_agy()
    assert agy is not None  # guaranteed by the gate; re-probe for clarity
    claustrum = claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary (need Go + ~/deai/claustrum or engine cache)")

    workdir = tmp_path / "work"
    home = workdir / "home"
    (home / ".local" / "bin").mkdir(parents=True)

    # agy's binary lives outside the workdir; grant its dir read+exec so
    # claustrum can execve it under the isolated $HOME — exactly what
    # _build_claustrum_wrap does with the resolved cache dir.
    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir),
        agy_cache_dir=str(Path(agy).resolve().parent),
        extra_allowed_dirs=None,
    )
    wrap = [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]
    # Non-billable, no-auth agy invocation exercising the confined launch path.
    argv = [agy, "--help"]
    env = host_actions.build_launch_env(str(workdir))

    proc = subprocess.Popen(
        [*wrap, *argv],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, **env},
        cwd=str(workdir),
        start_new_session=True,
    )
    try:
        buf = b""
        deadline = time.time() + 30
        while time.time() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], 1.0)
            if r:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    break
                buf += chunk
            if proc.poll() is not None and not r:
                break

        blob = buf.decode("utf-8", errors="replace").lower()
        # Banner printed → real agy launched AND its stdout survived the Landlock
        # jail (did not fail to start inside the confinement).
        assert "antigravity" in blob or "agy" in blob or "usage" in blob, (
            f"agy printed no banner under the sandbox; "
            f"exited={proc.poll()!r} out={buf[:400]!r}"
        )
    finally:
        proc.kill()
