"""Real-agy conversation surface under Landlock (Stage 10, checklist items 2 + 3).

Runs the REAL ``agy`` under a PTY wrapped by a REAL claustrum Landlock jail built
from optio's per-task grant flags, exactly as the conversation launch path
composes it: ``AntigravityConversation`` prepends ``_build_claustrum_wrap``'s
argv to each turn and wraps the whole thing in ``script -qec … /dev/null`` (the
mandatory PTY — under a non-TTY ``agy`` swallows stdout, design §1). It proves the
real binary LAUNCHES confined UNDER A PTY and its stdout survives BOTH the pty and
the Landlock jail.

NON-billable / no-auth: it drives ``agy --help`` (no model turn, no Google login)
through the PTY + confining wrap and asserts the banner appears. A real ``agy -p``
turn hits the model (unlike kimicode's ACP ``initialize`` handshake, agy has no
non-billable handshake), so the full transcript-tailed turn to a coalesced answer
(checklist item 2, in full) is the Stage-10 billable acceptance run. Here the
claim is narrower and honest: the conversation transport's exact confinement
(claustrum wrap + PTY + real agy) starts and its output survives. Mirrors
optio-kimicode's ``test_conversation_sandbox_enforce.py`` (kimi ``acp`` over
pipes → agy under a pty).

Skips cleanly unless: opt-in flag, Linux, kernel Landlock, a real ``agy``, and a
runnable claustrum.
"""

from __future__ import annotations

import os
import select
import shlex
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


def test_real_agy_runs_confined_under_pty(tmp_path: Path):
    agy = resolve_real_agy()
    assert agy is not None  # guaranteed by the gate
    claustrum = claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary (need Go + ~/deai/claustrum or engine cache)")

    workdir = tmp_path / "work"
    home = workdir / "home"
    home.mkdir(parents=True)

    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir),
        agy_cache_dir=str(Path(agy).resolve().parent),
        extra_allowed_dirs=None,
    )
    wrap = [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]
    # The confined conversation-turn argv shape: claustrum wrap ahead of agy
    # (here the non-billable ``--help`` stands in for ``-p <text>``), then the
    # whole thing under a pty — exactly AntigravityConversation._wrap_command's
    # ``script -qec <cmd> /dev/null``.
    inner = " ".join(shlex.quote(a) for a in [*wrap, agy, "--help"])
    argv = ["script", "-qec", inner, "/dev/null"]
    env = host_actions.build_launch_env(str(workdir))

    proc = subprocess.Popen(
        argv,
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
        # Banner over the pty → real agy started confined under the PTY and its
        # stdout survived both the pty and the Landlock jail.
        assert "antigravity" in blob or "agy" in blob or "usage" in blob, (
            f"agy printed no banner under the pty+sandbox; "
            f"exited={proc.poll()!r} out={buf[:400]!r}"
        )
    finally:
        proc.kill()
