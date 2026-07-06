"""Remote-mode integration test (Stage 1) — spins up an SSH container.

Proves optio-antigravity runs identically over SSH: the generic ``RemoteHost``
path (selected automatically when ``config.ssh`` is set) drives the same
tmux+ttyd+agy flow as the local test, and a deliverable emitted by the
fake-agy inside the container round-trips back through the optio.log tail.

Adapted from optio-grok's ``test_session_remote.py`` (grok ← agy renames; the
fake scenario env var is ``FAKE_AGY_SCENARIO``). The sshd image gains tmux +
bash so agy's detached-tmux launch works on the remote.
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

from optio_antigravity import AntigravityTaskConfig
from optio_antigravity.session import run_antigravity_session
from optio_antigravity.types import SSHConfig


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
    (HERE / "agy-shim.sh").chmod(0o755)
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

    The fake-agy ``deliverable`` scenario writes a file, emits a DELIVERABLE
    line to optio.log, then DONE. Selecting the scenario via ``config.env``
    (not process env) is what makes it reach the remote agy process.
    """
    ctx, *_ = ctx_and_captures

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    config = AntigravityTaskConfig(
        consumer_instructions="hand back a file",
        ssh=SSHConfig(
            host=sshd["host"], user=sshd["user"],
            key_path=sshd["key_path"], port=sshd["port"],
        ),
        agy_install_dir="/usr/local/bin",
        ttyd_install_dir="/usr/local/bin",
        install_if_missing=False,
        install_ttyd_if_missing=False,
        # Remote launch mechanics only — fs-isolation has its own suite; the
        # sshd container has no claustrum toolchain.
        fs_isolation=False,
        on_deliverable=on_deliverable,
        # Remote agy can't inherit the test process env — the scenario must
        # travel in the launch env.
        env={"FAKE_AGY_SCENARIO": "deliverable"},
    )

    await run_antigravity_session(ctx, config)

    assert len(captured) == 1
