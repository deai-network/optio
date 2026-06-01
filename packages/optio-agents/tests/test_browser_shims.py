"""prepare_browser_shims: ignore=no shims, suppress=silent, redirect=capture."""

import os
import subprocess

import pytest

from optio_host.host import LocalHost
from optio_agents.browser_shims import prepare_browser_shims
from optio_agents.protocol.parser import BrowserEvent, parse_log_line


_SHIM_NAMES = ("xdg-open", "gio", "open", "sensible-browser", "www-browser")


@pytest.mark.asyncio
async def test_ignore_installs_nothing_and_returns_none(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    assert await prepare_browser_shims(host, "ignore") is None
    assert not os.path.isdir(os.path.join(host.workdir, "bin"))


@pytest.mark.asyncio
async def test_suppress_writes_silent_stubs_and_env(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    await host.write_text("optio.log", "")
    env_add = await prepare_browser_shims(host, "suppress")

    assert env_add["BROWSER"].endswith("/bin/xdg-open")
    assert env_add["PATH"].startswith(f"{host.workdir}/bin:")
    for name in _SHIM_NAMES:
        shim = os.path.join(host.workdir, "bin", name)
        assert os.path.isfile(shim)
        assert os.access(shim, os.X_OK)

    # The suppress stub exits 0 and writes nothing.
    subprocess.run([os.path.join(host.workdir, "bin", "xdg-open"),
                    "https://example.com"], check=True)
    log = open(os.path.join(host.workdir, "optio.log")).read()
    assert "BROWSER:" not in log


@pytest.mark.asyncio
async def test_redirect_captures_browser_marker_end_to_end(tmp_path):
    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    await host.write_text("optio.log", "")
    env_add = await prepare_browser_shims(host, "redirect")

    assert env_add["BROWSER"].endswith("/bin/xdg-open")
    assert env_add["PATH"].startswith(f"{host.workdir}/bin:")

    subprocess.run([os.path.join(host.workdir, "bin", "xdg-open"),
                    "https://example.com/login"], check=True)
    log = open(os.path.join(host.workdir, "optio.log")).read()
    lines = [ln for ln in log.splitlines() if ln.startswith("BROWSER:")]
    assert len(lines) == 1
    ev = parse_log_line(lines[0])
    assert isinstance(ev, BrowserEvent)
    # Shim quotes the URL for transport; the parser strips them to the bare URL.
    assert ev.url == 'https://example.com/login'
