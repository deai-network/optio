"""Tests for RemoteHost resume-related methods.

Skipped automatically when the docker-compose.sshd.yml SSH harness is unavailable
(i.e. when Docker is not on PATH).

The SSH container runs opencode-shim.sh, which is:

    #!/bin/sh
    exec python3 /usr/local/bin/fake_opencode.py "$@" --scenario happy

fake_opencode.py handles `import` and `export` subcommands before it reaches
any scenario logic, so those round-trips work over SSH without any shim
modifications.  The `--env-dump` hook used in the LocalHost env-propagation
test cannot be wired through the SSH launch path (the shim hardcodes
``--scenario happy`` and the env_prefix is in the outer shell, not passed as
an explicit arg to the shim), so the remote env test is a smoke test: it
verifies launch_opencode(..., env={...}) accepts the kwarg and produces a
valid LaunchedProcess.
"""

from __future__ import annotations

import asyncio
import json
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


pytestmark = pytest.mark.skipif(
    not _have_docker(), reason="Docker not available"
)


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
    taskdir = f"/tmp/optio-test-resume-{id(object())}"
    host = RemoteHost(
        ssh_config=SSHConfig(
            host=sshd["host"],
            user=sshd["user"],
            key_path=sshd["key_path"],
            port=sshd["port"],
        ),
        taskdir=taskdir,
    )
    await host.connect()
    await host.setup_workdir()
    yield host
    await host.cleanup_taskdir(aggressive=False)
    await host.disconnect()


async def test_remote_launch_env_is_propagated(remote_host: RemoteHost):
    """Smoke test: launch_opencode accepts env kwarg and returns a LaunchedProcess.

    Full env inspection (--env-dump) cannot be wired through the SSH shim
    without modifying it, so we verify that passing env does not break the
    launch and that the returned object has a non-zero port.
    """
    from optio_opencode.host import LaunchedProcess

    proc = await remote_host.launch_opencode(
        password="test-pw",
        ready_timeout_s=15.0,
        env={"OPENCODE_DB": "/tmp/fake.db", "X": "1"},
    )
    assert isinstance(proc, LaunchedProcess)
    assert proc.opencode_port > 0
    await remote_host.terminate_opencode(proc, aggressive=True)


async def test_remote_opencode_export_then_import_roundtrip(remote_host: RemoteHost):
    """export then import via the SSH fake roundtrips correctly.

    The opencode-shim.sh passes all args to fake_opencode.py, which handles
    `import` and `export` subcommands synchronously before any scenario logic.
    """
    db_path = f"{remote_host.workdir}/opencode.db"
    seed = json.dumps({"id": "sess-remote-42", "messages": []}).encode("utf-8")

    await remote_host.opencode_import(db_path, seed)

    out = await remote_host.opencode_export(db_path, "sess-remote-42")
    decoded = json.loads(out.decode("utf-8"))
    assert decoded["id"] == "sess-remote-42"


async def test_remote_archive_workdir_yields_chunks(remote_host: RemoteHost):
    """archive_workdir streams at least one non-empty chunk of tar data."""
    await remote_host.write_text("AGENTS.md", "# instructions\n")
    await remote_host.write_text("data.txt", "payload\n")

    chunks = []
    async for chunk in remote_host.archive_workdir(exclude=None):
        chunks.append(chunk)

    assert len(b"".join(chunks)) > 0, "expected at least some tar bytes"


async def test_remote_restore_workdir_empties_then_extracts(sshd):
    """archive from one workdir, restore into a second, verify content and staleness."""
    ssh = SSHConfig(
        host=sshd["host"],
        user=sshd["user"],
        key_path=sshd["key_path"],
        port=sshd["port"],
    )

    src_taskdir = f"/tmp/optio-test-resume-src-{id(object())}"
    dst_taskdir = f"/tmp/optio-test-resume-dst-{id(object())}"

    src = RemoteHost(ssh_config=ssh, taskdir=src_taskdir)
    dst = RemoteHost(ssh_config=ssh, taskdir=dst_taskdir)

    await src.connect()
    await dst.connect()

    try:
        await src.setup_workdir()
        await src.write_text("AGENTS.md", "# instructions\n")
        await src.write_text("data.txt", "payload\n")

        # Setup dst with a stale file that restore should remove.
        await dst.setup_workdir()
        await dst.write_text("stale.txt", "zzz\n")

        # Archive src → chunks.
        chunks = []
        async for chunk in src.archive_workdir(exclude=None):
            chunks.append(chunk)

        async def replay():
            for c in chunks:
                yield c

        await dst.restore_workdir(replay())

        # Verify stale file is gone and src content is present.
        r_stale = await dst._conn.run(
            f"test -f {dst.workdir}/stale.txt && echo yes || echo no",
            check=False,
        )
        assert (r_stale.stdout or "").strip() == "no", "stale.txt should have been removed"

        r_agents = await dst._conn.run(
            f"cat {dst.workdir}/AGENTS.md",
            check=False,
        )
        assert (r_agents.stdout or "").strip() == "# instructions"

    finally:
        await src.cleanup_taskdir(aggressive=False)
        await dst.cleanup_taskdir(aggressive=False)
        await src.disconnect()
        await dst.disconnect()


async def test_remote_remove_file_is_idempotent(remote_host: RemoteHost):
    """remove_file does not raise when the file is absent (rm -f semantics)."""
    absent = f"{remote_host.workdir}/no-such-file.txt"
    # Should not raise.
    await remote_host.remove_file(absent)

    # Also verify it removes an existing file.
    await remote_host.write_text("to-remove.txt", "bye\n")
    await remote_host.remove_file(f"{remote_host.workdir}/to-remove.txt")
    r = await remote_host._conn.run(
        f"test -f {remote_host.workdir}/to-remove.txt && echo yes || echo no",
        check=False,
    )
    assert (r.stdout or "").strip() == "no"
