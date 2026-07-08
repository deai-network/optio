"""Real-cursor claustrum enforcement test for Stage 8 (skip-if-no-landlock).

Unlike the rest of the suite (which uses the fake cursor + a claustrum shim),
this exercises the REAL ``cursor-agent`` binary wrapped in the REAL claustrum
Landlock CLI and verifies that a write OUTSIDE the allowlisted tree (into the
operator's real home) is denied by the kernel — proving the Stage-8 isolation
is genuinely enforced, not merely requested.

Cursor's mechanism is claustrum (whole-process Landlock wrap), NOT cursor's own
native sandbox: cursor's native sandbox only confines the per-command shell
helper, leaving the agent's in-process Write/Edit tools unconfined (see the
mechanism decision block in ``fs_allowlist.py``). So the enforcement asserted
here is claustrum's, applied to the entire cursor-agent process tree.

Skips cleanly unless ALL prerequisites are present: an opt-in env var, Linux, a
kernel Landlock LSM, a real ``cursor-agent`` binary, an authenticated
``~/.config/cursor/auth.json`` (a real one-shot ``cursor-agent -p`` needs a
live login), and a provisioned claustrum binary. The host in the reference
environment is NOT logged into cursor, so this skips there.

Positive control: the test also asks cursor to write a file INSIDE the workdir.
If that in-tree write does not land, cursor never actually performed any write
(model/auth/startup issue) and the out-of-tree absence proves nothing — so the
test skips rather than falsely passing. Only when the in-tree write succeeded do
we assert the out-of-tree write was denied.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
from pathlib import Path

import pytest

from optio_agents.claustrum import CLAUSTRUM_PINNED_TAG
from optio_agents.fs_grants import build_grant_flags
from optio_cursor import host_actions
from optio_cursor.host_actions import run_cursor_probe


_CURSOR_AUTH = Path.home() / ".config" / "cursor" / "auth.json"


def _resolve_real_cursor_agent() -> "str | None":
    """Absolute path to the real cursor-agent binary (symlink resolved), or None.

    ``~/.local/bin/cursor-agent`` is a symlink into the versioned install tree;
    resolving it yields the launcher that lives beside its node bundle, so the
    containing dir is the one claustrum must grant read+exec.
    """
    found = shutil.which("cursor-agent")
    return os.path.realpath(found) if found else None


def _landlock_available() -> bool:
    try:
        return "landlock" in Path("/sys/kernel/security/lsm").read_text()
    except OSError:
        return False


def _resolve_claustrum() -> "str | None":
    """Path to a provisioned claustrum binary (pinned tag), or None.

    ``OPTIO_CURSOR_CLAUSTRUM`` overrides; otherwise the optio-owned cursor
    cache built by ``ensure_claustrum_installed`` at
    ``~/.cache/optio-cursor/claustrum/<tag>/<arch>/claustrum``.
    """
    override = os.environ.get("OPTIO_CURSOR_CLAUSTRUM")
    if override and os.path.exists(override):
        return override
    pattern = os.path.expanduser(
        f"~/.cache/optio-cursor/claustrum/{CLAUSTRUM_PINNED_TAG}/*/claustrum"
    )
    hits = [p for p in glob.glob(pattern) if os.path.exists(p)]
    return hits[0] if hits else None


def _skip_reason() -> "str | None":
    # Opt-in only: this exercises the REAL cursor-agent binary with a live,
    # billable API call. Never runs in the default suite (slow + costs tokens
    # even on a fully-provisioned dev host). Set
    # OPTIO_CURSOR_SANDBOX_ENFORCE_TEST=1 to run it.
    if os.environ.get("OPTIO_CURSOR_SANDBOX_ENFORCE_TEST") != "1":
        return (
            "set OPTIO_CURSOR_SANDBOX_ENFORCE_TEST=1 to run the real-cursor "
            "enforcement test"
        )
    if platform.system() != "Linux":
        return "sandbox enforcement test requires Linux/Landlock"
    if not _landlock_available():
        return "kernel Landlock LSM not available"
    if _resolve_real_cursor_agent() is None:
        return "real cursor-agent binary not found"
    if not _CURSOR_AUTH.exists():
        return f"no authenticated cursor ({_CURSOR_AUTH} absent)"
    if _resolve_claustrum() is None:
        return "no provisioned claustrum binary (run a task once, or set OPTIO_CURSOR_CLAUSTRUM)"
    return None


pytestmark = pytest.mark.skipif(
    _skip_reason() is not None, reason=_skip_reason() or "",
)


async def test_real_cursor_claustrum_blocks_out_of_tree_write(tmp_path: Path):
    cursor_agent = _resolve_real_cursor_agent()
    claustrum = _resolve_claustrum()

    # A LocalHost whose workdir is the ONLY writable root (plus baseline system
    # dirs + the cursor install tree). run_cursor_probe runs cursor-agent under
    # the per-task HOME/XDG isolation env rooted at <workdir>/home.
    taskdir = str(tmp_path / "task")
    host = host_actions.build_host(None, taskdir)
    await host.connect()
    await host.setup_workdir()
    workdir = host.workdir.rstrip("/")

    # Seed the isolated home with the operator's real cursor login so the
    # one-shot prompt can actually run (claustrum is applied at exec regardless,
    # but a live login lets cursor attempt the tool call).
    auth_dst = Path(workdir) / "home" / ".config" / "cursor" / "auth.json"
    auth_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_CURSOR_AUTH, auth_dst)

    # The cursor-agent install tree (outside the workdir) must be read+exec so
    # the wrapped binary can start; the workdir is granted rwx by the builder.
    cursor_cache_dir = os.path.dirname(cursor_agent)
    grants = build_grant_flags(
        workdir=workdir,
        engine_cache_dir=cursor_cache_dir,
        extra_allowed_dirs=None,
    )
    claustrum_wrap = [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]

    # Probe targets. The out-of-tree write aims at the operator's real home
    # (normally writable by this user, NOT in the allowlist → must be denied).
    # The in-tree write is the positive control (proves cursor actually acted).
    out_probe = Path.home() / f".optio-cursor-sandbox-probe-{os.getpid()}"
    in_probe = Path(workdir) / "sandbox-ok.txt"
    if out_probe.exists():
        out_probe.unlink()

    prompt = (
        "Use your shell tool to run exactly these two commands, in order:\n"
        f"echo IN > {in_probe}\n"
        f"echo OUT > {out_probe}\n"
        "Then report, for each command, whether it succeeded or was denied."
    )

    try:
        await run_cursor_probe(
            host,
            cursor_executable=cursor_agent,
            prompt=prompt,
            wrap=claustrum_wrap,
            timeout_s=180.0,
        )

        # Positive control: if the in-tree write never landed, cursor did not
        # perform the write at all (auth/model/startup issue) — the out-of-tree
        # absence would then prove nothing, so skip rather than falsely pass.
        if not in_probe.exists():
            pytest.skip(
                "cursor did not perform the in-workdir write; cannot verify "
                "enforcement (likely an auth/model/startup issue, not a "
                "sandbox result)"
            )

        # The forbidden out-of-sandbox write did not escape the claustrum tree.
        assert not out_probe.exists(), (
            "sandbox FAILED: cursor wrote outside the allowlisted tree "
            f"({out_probe})"
        )
    finally:
        if out_probe.exists():
            out_probe.unlink()
