"""Real-kimi conversation surface under Landlock (row-30, checklist items 2 + 3).

Launches the REAL ``kimi acp`` (the headless ACP/JSON-RPC conversation transport
``session._conversation_body`` spawns) wrapped by a REAL claustrum Landlock jail
built from optio's per-task grant flags, exactly as ``_build_claustrum_wrap``
prepends them. It proves the real ACP transport HANDSHAKES over pipes while
confined — kimi answers ``initialize`` over stdout, i.e. the sandbox did not
break the byte-clean JSON-RPC stream and kimi started inside the jail.

NON-billable: it performs only the ACP ``initialize`` handshake (needs no auth,
hits no model) and kills kimi before ``session/new`` or any prompt — so it never
spends tokens. The full stream / tool-call / turn-end drive (checklist item 2,
in full) is the billable ``test_real_conversation_stream_tool_turn`` in
``test_real_session_e2e.py``. Mirrors optio-grok's
``test_conversation_sandbox_enforce.py`` (grok's native ``--sandbox`` +
controlling-tty wrap → kimi's claustrum wrap; kimi's claustrum has no
``/dev/tty`` requirement, so no ctty helper is needed and the launch is plain
pipes + ``start_new_session``).

Skips cleanly unless: opt-in flag, Linux, kernel Landlock, a real ``kimi``, and a
runnable claustrum.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import time
from pathlib import Path

import pytest

from optio_kimicode import fs_allowlist

from realbin import claustrum_binary, resolve_real_kimi, sandbox_enforce_skip_reason

_FLAG = "OPTIO_KIMICODE_SANDBOX_ENFORCE_TEST"

pytestmark = pytest.mark.skipif(
    sandbox_enforce_skip_reason(_FLAG, need_kimi=True) is not None,
    reason=sandbox_enforce_skip_reason(_FLAG, need_kimi=True) or "",
)


def test_real_kimi_acp_handshakes_under_landlock(tmp_path: Path):
    kimi = resolve_real_kimi()
    assert kimi is not None  # guaranteed by the gate
    claustrum = claustrum_binary(tmp_path)
    if claustrum is None:
        pytest.skip("no claustrum binary (need Go + ~/deai/claustrum or engine cache)")

    workdir = tmp_path / "work"
    home = workdir / "home"
    home.mkdir(parents=True)

    grants = fs_allowlist.build_grant_flags(
        workdir=str(workdir),
        kimi_cache_dir=str(Path(kimi).resolve().parent),
        extra_allowed_dirs=None,
    )
    wrap = [claustrum, "--best-effort", "--abi-min", "1", *grants, "--"]
    # The exact conversation argv session._conversation_body launches: ``kimi
    # acp`` (headless ACP over stdio). Confined by the claustrum wrap.
    argv = [kimi, "acp"]
    env = {
        **os.environ,
        "HOME": str(home),
        "KIMI_CODE_HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }

    proc = subprocess.Popen(
        [*wrap, *argv],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=str(workdir),
        start_new_session=True,
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

        # ACP answered over the pipe → kimi acp started confined and its
        # JSON-RPC stdout survived the Landlock jail.
        assert b'"result"' in buf, (
            f"no ACP initialize result under the sandbox; "
            f"kimi exited={proc.poll()!r} out={buf[:400]!r}"
        )
    finally:
        proc.kill()
