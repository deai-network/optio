"""browser_capture.enable writes capturing shims; a subprocess invoking
the shim is captured end-to-end into optio.log."""

import os
import subprocess

import pytest

from optio_host.host import LocalHost
from optio_agents import browser_capture
from optio_agents.protocol.parser import parse_log_line, BrowserEvent


@pytest.mark.asyncio
async def test_enable_returns_env_and_writes_shims(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    env_add = await browser_capture.enable(host)

    assert env_add["BROWSER"].endswith("/bin/xdg-open")
    assert env_add["PATH"].startswith(f"{host.workdir}/bin:")
    for name in ("xdg-open", "gio", "open", "sensible-browser", "www-browser"):
        shim = os.path.join(host.workdir, "bin", name)
        assert os.path.isfile(shim)
        assert os.access(shim, os.X_OK)


@pytest.mark.asyncio
async def test_shim_captures_browser_marker_end_to_end(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    # optio.log must exist for the append to land somewhere observable.
    await host.write_text("optio.log", "")
    await browser_capture.enable(host)

    # Invoke the shim exactly as a real opener would be invoked.
    shim = os.path.join(host.workdir, "bin", "xdg-open")
    subprocess.run([shim, "https://example.com/login"], check=True)

    log = open(os.path.join(host.workdir, "optio.log")).read()
    lines = [ln for ln in log.splitlines() if ln.startswith("BROWSER:")]
    assert len(lines) == 1
    ev = parse_log_line(lines[0])
    assert isinstance(ev, BrowserEvent)
    assert ev.url == '"https://example.com/login"'
