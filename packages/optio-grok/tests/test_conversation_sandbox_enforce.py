"""Real-grok regression test for the conversation-mode sandbox + controlling-tty
fix (skip-if-no-landlock, opt-in).

Grok's custom fail-closed Landlock profile opens ``/dev/tty`` and refuses to start
without a controlling terminal. Conversation mode launches ``grok agent stdio``
over PIPES with ``start_new_session=True`` (no tty) so the ACP JSON-RPC stream
stays byte-clean — which used to make the sandbox fail closed and grok exit
immediately ("could not apply the 'optio' sandbox profile"). ``build_conversation_argv``
now wraps the command in a controlling-tty helper; this test proves that a REAL
``grok agent stdio`` starts under ``--sandbox optio`` via that wrapper, applies the
profile, and answers ACP over the pipe.

NON-billable: it only performs the ACP ``initialize`` handshake (which needs no
auth and hits no model) and kills grok before any prompt. Gated behind the same
opt-in env as the other real-grok test to keep the real binary out of the default
suite; also skips cleanly without Linux/Landlock/a real grok binary.
"""

from __future__ import annotations

import json
import os
import platform
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from optio_grok.fs_allowlist import build_sandbox_toml
from optio_grok.host_actions import SANDBOX_PROFILE_NAME, build_conversation_argv

_REAL_GROK_BIN = Path.home() / ".grok" / "bin" / "grok"


def _resolve_real_grok() -> "str | None":
    return shutil.which("grok") or (str(_REAL_GROK_BIN) if _REAL_GROK_BIN.exists() else None)


def _landlock_available() -> bool:
    try:
        return "landlock" in Path("/sys/kernel/security/lsm").read_text()
    except OSError:
        return False


def _skip_reason() -> "str | None":
    if os.environ.get("OPTIO_GROK_SANDBOX_ENFORCE_TEST") != "1":
        return "set OPTIO_GROK_SANDBOX_ENFORCE_TEST=1 to run the real-grok tests"
    if platform.system() != "Linux":
        return "sandbox enforcement test requires Linux/Landlock"
    if not _landlock_available():
        return "kernel Landlock LSM not available"
    if _resolve_real_grok() is None:
        return "real grok binary not found"
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")


def test_real_grok_conversation_starts_under_sandbox_via_ctty_wrap(tmp_path: Path):
    grok_bin = _resolve_real_grok()
    workdir = tmp_path / "work"
    grok_home = workdir / "home" / ".grok"
    grok_home.mkdir(parents=True)
    real_home = Path.home()

    # Plant the fail-closed custom profile exactly as session._prepare does.
    (grok_home / "sandbox.toml").write_text(
        build_sandbox_toml(
            workdir=str(workdir), extra_allowed_dirs=None, host_home=str(real_home),
        ),
        encoding="utf-8",
    )

    # The real conversation argv (fs_isolation=True): grok --sandbox optio agent
    # ... stdio, wrapped in the controlling-tty helper. build_conversation_argv is
    # the exact code path the session uses.
    argv = build_conversation_argv(
        grok_bin, no_leader=True, always_approve=True, fs_isolation=True,
    )
    assert argv[0] == "python3"  # controlling-tty wrapper is present

    env = {
        **os.environ,
        "HOME": str(workdir / "home"),
        "GROK_HOME": str(grok_home),
        "XDG_CONFIG_HOME": str(workdir / "home" / ".config"),
        "XDG_DATA_HOME": str(workdir / "home" / ".local" / "share"),
        "XDG_CACHE_HOME": str(workdir / "home" / ".cache"),
    }

    # Launch EXACTLY like optio_host.launch_subprocess: pipes + start_new_session
    # (no controlling tty from the parent). If the wrapper works, grok acquires
    # one itself; if it didn't, the sandbox would fail closed and grok would exit.
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, start_new_session=True,
    )
    try:
        proc.stdin.write(
            (json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            }) + "\n").encode()
        )
        proc.stdin.flush()

        buf = b""
        deadline = time.time() + 30
        while time.time() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], 1.0)
            if r:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            if proc.poll() is not None:
                break

        # ACP answered over the pipe → grok started (did not fail-closed).
        assert b'"result"' in buf, (
            f"no ACP initialize result; grok exited={proc.poll()!r} out={buf[:200]!r}"
        )

        # And the kernel actually applied the fail-closed custom profile.
        events = grok_home / "sandbox-events.jsonl"
        applied = [
            json.loads(ln) for ln in events.read_text().splitlines() if ln.strip()
            and json.loads(ln).get("event_type") == "ProfileApplied"
            and json.loads(ln).get("profile") == SANDBOX_PROFILE_NAME
        ]
        assert applied and applied[-1].get("enforced") is True
    finally:
        proc.kill()
