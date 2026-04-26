"""Integration tests for RemoteHost new primitives (Docker-gated)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio

from optio_opencode.host import RemoteHost
from optio_opencode.types import SSHConfig


HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(not _have_docker(), reason="Docker not available")


@pytest_asyncio.fixture(scope="module")
async def sshd():
    """Start the SSH container, generate a key pair, wait for port 22222."""
    keys_dir = HERE / "ssh-keys"
    keys_dir.mkdir(exist_ok=True)
    priv = keys_dir / "id_ed25519"
    if not priv.exists():
        subprocess.check_call([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv)
        ])
    # Make shim executable.
    (HERE / "opencode-shim.sh").chmod(0o755)

    subprocess.check_call(["docker", "compose", "-f", str(COMPOSE), "up", "-d"])

    # Wait for port.
    deadline = time.time() + 30
    import socket as _s
    while time.time() < deadline:
        try:
            c = _s.create_connection(("127.0.0.1", 22222), timeout=1)
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
        "port": 22222,
        "user": "optiotest",
        "key_path": str(priv),
    }

    subprocess.call(["docker", "compose", "-f", str(COMPOSE), "down"])


@pytest_asyncio.fixture
async def remote_host(sshd):
    """A connected RemoteHost with a stable, fresh taskdir."""
    h = RemoteHost(
        ssh_config=SSHConfig(
            host=sshd["host"],
            user=sshd["user"],
            key_path=sshd["key_path"],
            port=sshd["port"],
        ),
    )
    await h.connect()
    await h.setup_workdir()
    try:
        yield h
    finally:
        try:
            await h.cleanup_taskdir(aggressive=True)
        except Exception:
            pass
        await h.disconnect()


async def test_remote_run_command_captures_stdout(remote_host):
    result = await remote_host.run_command("echo hello")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""


async def test_remote_run_command_captures_stderr_and_exit(remote_host):
    result = await remote_host.run_command("echo oops 1>&2; exit 9")
    assert result.exit_code == 9
    assert "oops" in result.stderr


async def test_remote_run_command_default_cwd_is_workdir(remote_host):
    result = await remote_host.run_command("pwd")
    assert result.stdout.strip() == remote_host.workdir


async def test_remote_run_command_cwd_override(remote_host):
    result = await remote_host.run_command("pwd", cwd="/tmp")
    assert result.stdout.strip() == "/tmp"


async def test_remote_run_command_env(remote_host):
    result = await remote_host.run_command(
        'echo "$X"', env={"X": "yes"},
    )
    assert result.stdout.strip() == "yes"
