"""Remote-mode integration test (Stage 1) — spins up an SSH container.

Proves optio-cursor runs identically over SSH: the generic ``RemoteHost``
path (selected automatically when ``config.ssh`` is set) drives the same
tmux+ttyd+cursor-agent flow as the local test, and a deliverable emitted by
the fake-cursor inside the container round-trips back through the optio.log
tail.

Adapted from optio-grok's ``test_session_remote.py`` (its docker-sshd
fixture / compose) plus the local cursor deliverable test. The sshd image
gains tmux + bash so cursor's detached-tmux launch works on the remote.
"""

from __future__ import annotations

import asyncio
import shutil
import socket as _socket
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio

from optio_cursor import CursorTaskConfig
from optio_cursor.session import run_cursor_session
from optio_cursor.types import SSHConfig


HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _have_docker(), reason="Docker not available"
)


@pytest_asyncio.fixture(scope="module")
async def sshd():
    """Start the SSH container, generate a key pair, wait for port 22224."""
    keys_dir = HERE / "ssh-keys"
    keys_dir.mkdir(exist_ok=True)
    priv = keys_dir / "id_ed25519"
    if not priv.exists():
        subprocess.check_call([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv)
        ])
    # Shims must be executable inside the (read-only) bind mount.
    (HERE / "cursor-shim.sh").chmod(0o755)
    (HERE / "ttyd-shim.sh").chmod(0o755)

    try:
        subprocess.check_call(
            ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--build"]
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - env dependent
        pytest.skip(f"docker compose up failed: {exc}")

    # Wait for the SSH port.
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            c = _socket.create_connection(("127.0.0.1", 22224), timeout=1)
            c.close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        subprocess.call(["docker", "compose", "-f", str(COMPOSE), "down"])
        pytest.skip("sshd container did not come up")

    # Extra settle time for sshd to accept auth.
    await asyncio.sleep(2)

    yield {
        "host": "127.0.0.1",
        "port": 22224,
        "user": "optiotest",
        "key_path": str(priv),
    }

    subprocess.call(["docker", "compose", "-f", str(COMPOSE), "down"])


@pytest.mark.asyncio
async def test_remote_deliverable_callback_fired(sshd, ctx_and_captures):
    """Same as the local deliverable test, but over SSH against the container.

    The fake-cursor ``deliverable`` scenario writes a file, emits a
    DELIVERABLE line to optio.log, then DONE. Selecting the scenario via
    ``config.env`` (not process env) is what makes it reach the remote
    cursor-agent process.
    """
    ctx, *_ = ctx_and_captures

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    config = CursorTaskConfig(
        consumer_instructions="hand back a file",
        ssh=SSHConfig(
            host=sshd["host"], user=sshd["user"],
            key_path=sshd["key_path"], port=sshd["port"],
        ),
        cursor_install_dir="/usr/local/bin",
        ttyd_install_dir="/usr/local/bin",
        install_if_missing=False,
        install_ttyd_if_missing=False,
        on_deliverable=on_deliverable,
        # This test proves the SSH transport, not fs isolation: the fast suite
        # stubs claustrum with an ENGINE-local shim, which a remote host cannot
        # exec, so opt out here. Real remote Landlock enforcement is the
        # env-gated test_sandbox_enforce.py (Task 3).
        fs_isolation=False,
        # Remote cursor-agent can't inherit the test process env — the
        # scenario must travel in the launch env.
        env={"FAKE_CURSOR_SCENARIO": "deliverable"},
    )

    await run_cursor_session(ctx, config)

    assert len(captured) == 1
