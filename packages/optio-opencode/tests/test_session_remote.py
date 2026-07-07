"""Remote-mode integration test — spins up an SSH container."""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from bson import ObjectId

from optio_core.context import ProcessContext
from optio_opencode.session import run_opencode_session
from optio_opencode.types import OpencodeTaskConfig, SSHConfig


from optio_host.testing import have_docker

HERE = Path(__file__).parent

# The isolation-safe ``sshd`` fixture lives in conftest.py. `--dist loadscope`
# keeps this whole module on one worker, so its per-test rewrites of the shared
# ``opencode-shim.sh`` never run concurrently.
pytestmark = pytest.mark.skipif(
    not have_docker(), reason="Docker not available"
)


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
    async def on_d(hook_ctx, path, text):
        received.append((path, text))

    # Short-circuit the HTTP-based session pre-creation for this test.  The
    # fake_opencode test double's HTTP server is reliable over loopback (see
    # test_session_local) but has occasional RemoteDisconnected flakes when
    # called through asyncssh's local port forward, which is unrelated to
    # the SSH code path we're actually exercising here.  The local
    # integration suite covers the real HTTP-session-creation path.
    import optio_opencode.session as _session_mod
    from optio_opencode import host_actions as _host_actions
    async def _stub_create_session(port, password, directory):
        return "fake-session-id"
    monkeypatch.setattr(_session_mod, "_create_opencode_session", _stub_create_session)

    # Short-circuit ensure_opencode_installed: the shim at /usr/local/bin/opencode
    # is a stand-in for the real binary and would not satisfy smart-install.sh's
    # version check (which queries GitHub for the latest released tag). The test
    # is exercising the SSH/protocol wiring, not the install code path. Mirrors
    # the equivalent stub in test_session_local.
    async def _ensure(host, **kwargs):
        return "/usr/local/bin/opencode"
    monkeypatch.setattr(_host_actions, "ensure_opencode_installed", _ensure)

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
