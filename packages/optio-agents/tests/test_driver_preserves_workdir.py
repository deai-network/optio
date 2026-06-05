"""Workdir lifecycle is centralized in the protocol driver.

The driver owns the destructive clean-start (so no caller can forget it and no
double-wipe is possible) and invokes a caller-supplied ``prepare`` hook in the
one correct window: AFTER the wipe (so stale prior-run state is gone) and
BEFORE it subscribes the optio.log tail (so a resume-restore done in prepare
precedes the tail and is not replayed). ``prepare`` carries the per-agent
runtime install + resume-restore that used to run, fragile and uncentralized,
in each session module before calling the driver.
"""

import os

import pytest

from optio_host.host import LocalHost
from optio_agents.protocol import session as S
from optio_agents import get_protocol


class _Ctx:
    def report_progress(self, percent, message=None):
        pass

    def should_continue(self) -> bool:
        return True


async def _done_body(host, hook_ctx) -> None:
    await host.run_command(f"echo DONE >> {host.workdir}/optio.log")


@pytest.mark.asyncio
async def test_driver_wipes_stale_state_before_body(tmp_workdir):
    """Centralized clean-start: state left in the workdir before the driver
    runs (e.g. a prior force-cancel's leftovers) is gone by the time body runs."""
    host = LocalHost(taskdir=tmp_workdir)
    await host.connect()
    await host.setup_workdir()
    await host.write_text("stale.txt", "leftover from a previous run")

    seen: dict[str, bool] = {}

    async def body(host, hook_ctx) -> None:
        seen["stale"] = os.path.exists(os.path.join(host.workdir, "stale.txt"))
        await host.run_command(f"echo DONE >> {host.workdir}/optio.log")

    await S.run_log_protocol_session(
        host, _Ctx(), body=body, protocol=get_protocol(),
    )

    assert seen["stale"] is False


@pytest.mark.asyncio
async def test_prepare_runs_after_wipe_and_survives_to_body(tmp_workdir):
    """prepare runs AFTER the wipe, so the runtime it installs reaches body
    intact (this is exactly the claude symlink that the old double-wipe nuked)."""
    host = LocalHost(taskdir=tmp_workdir)
    await host.connect()
    await host.setup_workdir()

    async def prepare(host, hook_ctx) -> None:
        await host.write_text("home/.local/bin/claude", "#!/bin/sh\n")

    seen: dict[str, bool] = {}

    async def body(host, hook_ctx) -> None:
        seen["runtime"] = os.path.exists(
            os.path.join(host.workdir, "home/.local/bin/claude")
        )
        await host.run_command(f"echo DONE >> {host.workdir}/optio.log")

    await S.run_log_protocol_session(
        host, _Ctx(), body=body, prepare=prepare, protocol=get_protocol(),
    )

    assert seen["runtime"] is True


@pytest.mark.asyncio
async def test_prepare_runs_before_log_tail(tmp_workdir):
    """prepare runs BEFORE the optio.log reset + tail subscription. A stale
    ERROR a resume-restore drops into optio.log during prepare must therefore
    be cleared by the driver's reset and never reach the tail (which would fail
    the session). Reaching the end without _SessionFailed proves the ordering."""
    host = LocalHost(taskdir=tmp_workdir)
    await host.connect()
    await host.setup_workdir()

    async def prepare(host, hook_ctx) -> None:
        await host.write_text("optio.log", "ERROR: stale from a previous run\n")

    await S.run_log_protocol_session(
        host, _Ctx(), body=_done_body, prepare=prepare, protocol=get_protocol(),
    )
