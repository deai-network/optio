"""Remote-mode integration test (Stage 1) — spins up an SSH container.

Proves optio-grok runs identically over SSH: the generic ``RemoteHost`` path
(selected automatically when ``config.ssh`` is set) drives the same
tmux+ttyd+grok flow as the local test, and a deliverable emitted by the
fake-grok inside the container round-trips back through the optio.log tail.

Adapted from optio-opencode's ``test_session_remote.py`` (its docker-sshd
fixture / compose) plus the local grok deliverable test. The sshd image gains
tmux + bash so grok's detached-tmux launch works on the remote.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from optio_grok import GrokTaskConfig
from optio_grok.session import run_grok_session
from optio_grok.types import SSHConfig


from optio_host.testing import have_docker, sshd_container

HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.sshd.yml"

pytestmark = pytest.mark.skipif(
    not have_docker(), reason="Docker not available"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sshd():
    """Isolation-safe sshd container (per-worker project + ephemeral port)."""
    # Shims must be executable inside the (read-only) bind mount.
    (HERE / "grok-shim.sh").chmod(0o755)
    (HERE / "ttyd-shim.sh").chmod(0o755)
    async with sshd_container(COMPOSE, "optio-grok", ready_timeout=60.0) as info:
        yield info


@pytest.mark.asyncio
async def test_remote_deliverable_callback_fired(sshd, ctx_and_captures):
    """Same as the local deliverable test, but over SSH against the container.

    The fake-grok ``deliverable`` scenario writes a file, emits a DELIVERABLE
    line to optio.log, then DONE. Selecting the scenario via ``config.env``
    (not process env) is what makes it reach the remote grok process.
    """
    ctx, *_ = ctx_and_captures

    captured: list[tuple[str, str]] = []

    async def on_deliverable(hook_ctx, path, text):
        captured.append((path, text))

    config = GrokTaskConfig(
        consumer_instructions="hand back a file",
        ssh=SSHConfig(
            host=sshd["host"], user=sshd["user"],
            key_path=sshd["key_path"], port=sshd["port"],
        ),
        grok_install_dir="/usr/local/bin",
        ttyd_install_dir="/usr/local/bin",
        install_if_missing=False,
        install_ttyd_if_missing=False,
        on_deliverable=on_deliverable,
        # Remote grok can't inherit the test process env — the scenario must
        # travel in the launch env.
        env={"FAKE_GROK_SCENARIO": "deliverable"},
    )

    await run_grok_session(ctx, config)

    assert len(captured) == 1
