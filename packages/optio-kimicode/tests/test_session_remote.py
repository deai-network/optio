"""Remote-mode integration test (Stage 1) — spins up an SSH container.

Proves optio-kimicode runs identically over SSH: the generic ``RemoteHost``
path (selected automatically when ``config.ssh`` is set — ``host_actions.
build_host`` branches on ``ssh is None``, not on a host type) drives the same
resolve-kimi → plant AGENTS.md → launch ``kimi server run --foreground`` →
tunnel → optio.log flow as the local iframe test, and a deliverable emitted by
the fake-kimi inside the container round-trips back through the optio.log tail.

The ONLY host-type branch in the session path is the local-vs-remote server
bind interface (``session._iframe_body``: bind to the operator's chosen
interface locally, always loopback on the remote). Everything else is generic
Host primitives, so this test exercises the same code as the local test with
``config.ssh`` set.

Adapted from optio-grok's ``test_session_remote.py``. Deltas: kimi serves its
own web SPA (no ttyd/tmux shim — the sshd image needs only bash + python3), a
single ``kimi-shim.sh`` is mounted, and the launch env carries
``FAKE_KIMI_SCENARIO`` (the remote kimi cannot inherit the test process env).
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

from optio_kimicode import KimiCodeTaskConfig, create_kimicode_task
from optio_kimicode.types import SSHConfig


HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _have_docker(), reason="Docker not available (tracked gap: remote SSH parity)"
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
    # The shim must be executable inside the (read-only) bind mount.
    (HERE / "kimi-shim.sh").chmod(0o755)

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

    The fake-kimi ``deliverable`` scenario writes a file, emits a DELIVERABLE
    line to optio.log, then DONE. Selecting the scenario via ``config.env``
    (not the process env) is what makes it reach the remote kimi process.
    """
    ctx, *_ = ctx_and_captures

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    config = KimiCodeTaskConfig(
        consumer_instructions="hand back a file",
        ssh=SSHConfig(
            host=sshd["host"], user=sshd["user"],
            key_path=sshd["key_path"], port=sshd["port"],
        ),
        kimi_install_dir="/usr/local/bin",
        install_if_missing=False,
        on_deliverable=on_deliverable,
        # Stage-0 surfaces only: resume (Stage 2) and fs-isolation (Stage 8)
        # are not wired yet.
        supports_resume=False,
        fs_isolation=False,
        # Remote kimi can't inherit the test process env — the scenario must
        # travel in the launch env.
        env={"FAKE_KIMI_SCENARIO": "deliverable"},
    )

    task = create_kimicode_task(
        process_id="kimi-remote-deliverable", name="r", config=config,
    )
    await task.execute(ctx)

    assert len(captured) == 1
    assert "hello from fake kimi" in captured[0][1]
