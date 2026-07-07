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

from pathlib import Path

import pytest
import pytest_asyncio

from optio_antigravity import AntigravityTaskConfig
from optio_antigravity.session import run_antigravity_session
from optio_antigravity.types import SSHConfig


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
    (HERE / "agy-shim.sh").chmod(0o755)
    (HERE / "ttyd-shim.sh").chmod(0o755)
    async with sshd_container(COMPOSE, "optio-antigravity", ready_timeout=60.0) as info:
        yield info


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
        install_dir="/usr/local/bin",
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
