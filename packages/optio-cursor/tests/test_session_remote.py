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

from pathlib import Path

import pytest
import pytest_asyncio

from optio_cursor import CursorTaskConfig
from optio_cursor.session import run_cursor_session
from optio_cursor.types import SSHConfig


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
    (HERE / "cursor-shim.sh").chmod(0o755)
    (HERE / "ttyd-shim.sh").chmod(0o755)
    async with sshd_container(COMPOSE, "optio-cursor", ready_timeout=60.0) as info:
        yield info


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
        install_dir="/usr/local/bin",
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
