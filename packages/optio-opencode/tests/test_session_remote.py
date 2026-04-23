"""Remote-mode integration test — spins up an SSH container."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig, SSHConfig


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
async def ctx(mongo_db):
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({
        "_id": oid, "processId": "p", "name": "P", "params": {},
        "metadata": {}, "parentId": None, "rootId": None, "depth": 0,
        "order": 0, "adhoc": False, "ephemeral": False,
        "status": {"state": "running"},
        "progress": {"percent": None, "message": None},
        "log": [],
    })
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0,
        params={}, services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(),
        child_counter={"next": 0},
    )


async def test_remote_happy_path(sshd, ctx, monkeypatch):
    received: list = []
    async def on_d(path, text):
        received.append((path, text))

    # Short-circuit the HTTP-based session pre-creation for this test.  The
    # fake_opencode test double's HTTP server is reliable over loopback (see
    # test_session_local) but has occasional RemoteDisconnected flakes when
    # called through asyncssh's local port forward, which is unrelated to
    # the SSH code path we're actually exercising here.  The local
    # integration suite covers the real HTTP-session-creation path.
    import optio_opencode.session as _session_mod
    async def _stub_create_session(port, password, directory):
        return "fake-session-id"
    monkeypatch.setattr(_session_mod, "_create_opencode_session", _stub_create_session)

    config = OpencodeTaskConfig(
        consumer_instructions="remote test",
        ssh=SSHConfig(
            host=sshd["host"], user=sshd["user"],
            key_path=sshd["key_path"], port=sshd["port"],
        ),
        on_deliverable=on_d,
        install_if_missing=False,  # Shim is already present in the container.
    )

    # fake_opencode inside the container still needs a --scenario arg.
    # We pass one via opencode-shim.sh's arg passthrough + session.py's
    # launch_opencode.  The simplest hook: add a FAKE_SCENARIO env var the
    # shim reads.  For the happy-path test we set it in session via env
    # (production wiring passes it through OPENCODE_SERVER_PASSWORD only).
    #
    # Implementation detail: in the test, we install the scenario by editing
    # the shim once per session.  See conftest.
    scenario_file = HERE / "scenario.txt"
    scenario_file.write_text("happy\n")
    shim = HERE / "opencode-shim.sh"
    shim.write_text("#!/bin/sh\nexec python3 /usr/local/bin/fake_opencode.py \"$@\" --scenario happy\n")
    shim.chmod(0o755)

    await run_opencode_session(ctx, config)
    assert len(received) == 1
    path, text = received[0]
    assert text == "hello 42 blue"
