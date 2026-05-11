"""Tests for the smart-install-driven ensure_opencode_installed pipeline."""

import pytest

from optio_host.context import RunResult


class _FakeHost:
    """Minimal Host stub for run_command-only interactions."""

    def __init__(self, results):
        self.workdir = "/wd"
        self._results = list(results)
        self.calls = []

    async def run_command(self, command, *, cwd=None, env=None):
        self.calls.append(command)
        return self._results.pop(0)


async def test_smart_install_check_returns_ok_when_up_to_date():
    from optio_opencode.host_actions import _smart_install_check

    host = _FakeHost([RunResult(stdout="opencode ok\n", stderr="", exit_code=0)])
    kind, url = await _smart_install_check(host)
    assert kind == "ok"
    assert url is None


async def test_smart_install_check_returns_download_url():
    from optio_opencode.host_actions import _smart_install_check

    line = (
        "download "
        "https://github.com/csillag/opencode/releases/latest/download/"
        "opencode-linux-x64.zip\n"
    )
    host = _FakeHost([RunResult(stdout=line, stderr="", exit_code=0)])
    kind, url = await _smart_install_check(host)
    assert kind == "download"
    assert url == (
        "https://github.com/csillag/opencode/releases/latest/download/"
        "opencode-linux-x64.zip"
    )


async def test_smart_install_check_raises_on_nonzero_exit():
    from optio_opencode.host_actions import _smart_install_check

    host = _FakeHost([RunResult(stdout="", stderr="boom", exit_code=1)])
    with pytest.raises(RuntimeError, match="smart-install"):
        await _smart_install_check(host)


async def test_smart_install_check_raises_on_unparseable_output():
    from optio_opencode.host_actions import _smart_install_check

    host = _FakeHost(
        [RunResult(stdout="unexpected nonsense\n", stderr="", exit_code=0)]
    )
    with pytest.raises(RuntimeError, match="unexpected"):
        await _smart_install_check(host)


@pytest.mark.network
async def test_smart_install_check_against_real_localhost(tmp_workdir):
    """Smoke test: hit the live smart-install.sh URL via LocalHost.

    Marked ``network`` — requires outbound https to raw.githubusercontent.com
    and the upstream csillag/opencode repo. Skipped in offline runs.
    """
    from optio_host.host import LocalHost
    from optio_opencode.host_actions import _smart_install_check

    host = LocalHost(taskdir=tmp_workdir)
    await host.setup_workdir()
    kind, url = await _smart_install_check(host)
    assert kind in ("ok", "download")
    if kind == "download":
        assert url and url.startswith("https://")
