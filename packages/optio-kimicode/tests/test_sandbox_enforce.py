"""Real-kimi iframe surface under Landlock (row-30, checklist items 1 + 3).

Launches the REAL ``kimi server run --foreground`` (the ``kimi web`` iframe
surface) wrapped by a REAL claustrum Landlock jail built from the exact grant
flags optio computes per task (``fs_allowlist.build_grant_flags`` →
``claustrum --best-effort --abi-min 1 <grants> --``, i.e. what
``host_actions._build_claustrum_wrap`` prepends). It proves the real binary
LAUNCHES and RENDERS (prints its ready banner, the readiness signal
``launch_kimi_web`` keys off) while confined — i.e. the sandbox does not break
kimi's own serving, and kimi's stdout survives the jail.

Non-billable: it starts the server and reads the ready banner, then kills kimi
before any model turn. The genuine kernel-level DENY of an out-of-tree write is
asserted directly against claustrum in ``test_fs_isolation_e2e.py`` (the
enforcement is claustrum's, not kimi's); here the assertion is that real kimi
runs confined. Mirrors optio-grok's ``test_sandbox_enforce.py`` (grok's native
``--sandbox`` → kimi's claustrum wrap).

Skips cleanly unless ALL prerequisites are present: opt-in flag, Linux, a kernel
Landlock LSM, a real ``kimi`` binary, and a runnable claustrum.
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from optio_kimicode import fs_allowlist, host_actions

from realbin import claustrum_binary, sandbox_enforce_skip_reason

_FLAG = "OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST"

pytestmark = pytest.mark.skipif(
    sandbox_enforce_skip_reason(_FLAG, need_kimi=True) is not None,
    reason=sandbox_enforce_skip_reason(_FLAG, need_kimi=True) or "",
)


def test_real_kimi_web_launches_under_landlock(tmp_path: Path):
    from realbin import kimi_creds_path, resolve_real_kimi

    kimi = resolve_real_kimi()
    assert kimi is not None  # guaranteed by the gate; re-probe for mypy/clarity
    claustrum = claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary (need Go + ~/deai/claustrum or engine cache)")

    workdir = tmp_path / "work"
    home = workdir / "home"
    (home / ".local" / "bin").mkdir(parents=True)

    # Seed the isolated home with the operator's real creds if present, so the
    # server can fully initialise (the sandbox is applied at process startup
    # regardless — the test asserts kimi launches confined, not that it auths).
    creds_src = kimi_creds_path()
    if creds_src.exists():
        dst = home / "credentials" / "kimi-code.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(creds_src, dst)

    # kimi's binary lives outside the workdir; grant its dir read+exec so
    # claustrum can execve it under the isolated $HOME (exactly what
    # _build_claustrum_wrap does with the resolved cache dir).
    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir),
        kimi_cache_dir=str(Path(kimi).resolve().parent),
        extra_allowed_dirs=None,
    )
    wrap = [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]
    argv = host_actions.build_kimi_server_argv(kimi, bind_iface="127.0.0.1", port=0)
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
        matched = None
        deadline = time.time() + 30
        while time.time() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], 1.0)
            if r:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    break
                buf += chunk
                m = host_actions._KIMI_READY_RE.search(
                    buf.decode("utf-8", errors="replace")
                )
                if m:
                    matched = m
                    break
            if proc.poll() is not None:
                break

        # Ready banner printed → real kimi launched AND rendered under Landlock
        # (did not fail to start inside the jail, stdout survived confinement).
        assert matched is not None, (
            f"kimi web printed no ready banner under the sandbox; "
            f"exited={proc.poll()!r} out={buf[:400]!r}"
        )
        assert int(matched.group(1)) > 0  # a real listening port
    finally:
        proc.kill()
