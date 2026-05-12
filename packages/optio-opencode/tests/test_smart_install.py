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


# ---------------------------------------------------------------------------
# _install_opencode_from_zip integration tests (real curl + fake zip server).
# ---------------------------------------------------------------------------

import asyncio
import hashlib
import io
import os
import threading
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


@pytest.fixture
def zip_server(tmp_path):
    """Serve a directory over HTTP. Tests write zips into it."""
    served = tmp_path / "zip_served"
    served.mkdir()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(served), **kwargs)

        def log_message(self, format, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", served
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


def _make_fake_opencode_zip(target_path, binary_content: bytes):
    """Build a zip with bin/opencode at target_path (matching real layout)."""
    with zipfile.ZipFile(target_path, "w") as zf:
        zf.writestr("bin/opencode", binary_content)
        zf.writestr("package.json", '{"name":"opencode","version":"0.0.0-test"}')


class _ExecutingFakeCtx:
    """Fake ProcessContext whose run_child actually awaits the execute fn.

    download_file routes through here in our test: parent ctx receives
    run_child, which we implement by allocating a child sub-ctx and
    awaiting ``execute(sub_ctx)`` directly. Sufficient for an integration
    test of _install_opencode_from_zip that doesn't need real Mongo/Optio.
    """

    def __init__(self, *, process_id="root"):
        self.process_id = process_id
        self._child_counter = {"next": 0}
        self.progress = []
        self.cancellation_flag = asyncio.Event()

    def report_progress(self, percent, message=None):
        self.progress.append((percent, message))

    def should_continue(self) -> bool:
        return not self.cancellation_flag.is_set()

    async def run_child(self, execute, process_id, name, *, description=None, **kw):
        sub = _ExecutingFakeCtx(process_id=process_id)
        # share the cancellation flag so parent-set cancel propagates manually
        sub.cancellation_flag = self.cancellation_flag
        self._child_counter["next"] += 1
        try:
            await execute(sub)
        except Exception as e:
            # Mirror executor's wrapping: structured exceptions are lost.
            raise RuntimeError(
                f"Child process '{name}' failed: {e!r}"
            ) from e
        return "done"


async def test_install_opencode_from_zip_happy_path(zip_server, tmp_path):
    from optio_host.context import HookContext
    from optio_host.host import LocalHost
    from optio_opencode.host_actions import _install_opencode_from_zip

    base_url, served = zip_server
    # Pretend "binary" is a tiny shell script so we can exercise chmod +x +
    # actually invoke it after install.
    binary = b"#!/bin/sh\necho fake-opencode\n"
    _make_fake_opencode_zip(served / "opencode-linux-x64.zip", binary)
    url = f"{base_url}/opencode-linux-x64.zip"

    # Use a per-test HOME so we don't clobber the real ~/.local/bin/opencode.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        taskdir = tmp_path / "taskdir"
        taskdir.mkdir()
        host = LocalHost(taskdir=str(taskdir))
        await host.setup_workdir()

        parent = _ExecutingFakeCtx(process_id="opencode.task")
        hook_ctx = HookContext(parent, host)

        path = await _install_opencode_from_zip(hook_ctx, url)

        assert path == str(fake_home / ".local" / "bin" / "opencode")
        # Installed and executable.
        st = os.stat(path)
        assert st.st_mode & 0o111, "install path is not executable"
        with open(path, "rb") as fh:
            assert fh.read() == binary
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home


async def test_install_opencode_from_zip_cleans_up_tempdir(zip_server, tmp_path):
    """The temp dir created on the host should be removed after install."""
    from optio_host.context import HookContext
    from optio_host.host import LocalHost
    from optio_opencode.host_actions import _install_opencode_from_zip

    base_url, served = zip_server
    _make_fake_opencode_zip(
        served / "opencode-linux-x64.zip", b"#!/bin/sh\nexit 0\n",
    )
    url = f"{base_url}/opencode-linux-x64.zip"

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        taskdir = tmp_path / "taskdir"
        taskdir.mkdir()
        host = LocalHost(taskdir=str(taskdir))
        await host.setup_workdir()
        parent = _ExecutingFakeCtx()
        hook_ctx = HookContext(parent, host)
        # Capture every mktemp -d call so we can verify cleanup of its result.
        tmpdirs: list[str] = []
        orig_run = host.run_command

        async def spy(command, **kwargs):
            r = await orig_run(command, **kwargs)
            if command.startswith("mktemp -d"):
                tmpdirs.append(r.stdout.strip())
            return r

        host.run_command = spy  # type: ignore[method-assign]

        await _install_opencode_from_zip(hook_ctx, url)

        assert tmpdirs, "no mktemp -d call observed"
        for td in tmpdirs:
            assert not os.path.exists(td), f"tempdir {td!r} not cleaned up"
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home


# ---------------------------------------------------------------------------
# ensure_opencode_installed (top-level) tests.
# ---------------------------------------------------------------------------


async def test_ensure_opencode_installed_returns_existing_path_when_ok(monkeypatch):
    """When smart-install says 'ok', resolve the on-PATH path and return it."""
    from optio_host.context import HookContext
    from optio_opencode import host_actions

    async def stub_check(host):
        return ("ok", None)

    async def fake_run(command, *, cwd=None, env=None):
        # Capture both calls — the smart-install stub bypasses run_command for
        # the check itself, so the only call we expect is 'command -v opencode'.
        if "command -v opencode" in command:
            return RunResult(stdout="/usr/local/bin/opencode\n", stderr="", exit_code=0)
        raise AssertionError(f"unexpected run_command: {command!r}")

    monkeypatch.setattr(host_actions, "_smart_install_check", stub_check)

    class StubHost:
        workdir = "/wd"
        async def run_command(self, command, *, cwd=None, env=None):
            return await fake_run(command, cwd=cwd, env=env)

    host = StubHost()
    parent = _ExecutingFakeCtx()
    hook_ctx = HookContext(parent, host)

    path = await host_actions.ensure_opencode_installed(hook_ctx)
    assert path == "/usr/local/bin/opencode"


async def test_ensure_opencode_installed_raises_when_install_disabled(monkeypatch):
    from optio_host.context import HookContext
    from optio_opencode import host_actions

    async def stub_check(host):
        return ("download", "https://example/opencode.zip")

    monkeypatch.setattr(host_actions, "_smart_install_check", stub_check)

    class StubHost:
        workdir = "/wd"
        async def run_command(self, command, *, cwd=None, env=None):
            raise AssertionError(f"unexpected run_command: {command!r}")

    host = StubHost()
    parent = _ExecutingFakeCtx()
    hook_ctx = HookContext(parent, host)

    with pytest.raises(RuntimeError, match="install_if_missing"):
        await host_actions.ensure_opencode_installed(
            hook_ctx, install_if_missing=False,
        )


async def test_ensure_opencode_installed_installs_when_download_required(
    monkeypatch, zip_server, tmp_path,
):
    """End-to-end with a real LocalHost + fake zip server: install path."""
    from optio_host.context import HookContext
    from optio_host.host import LocalHost
    from optio_opencode import host_actions

    base_url, served = zip_server
    binary = b"#!/bin/sh\necho fake\n"
    _make_fake_opencode_zip(served / "opencode-linux-x64.zip", binary)
    url = f"{base_url}/opencode-linux-x64.zip"

    async def stub_check(host):
        return ("download", url)

    monkeypatch.setattr(host_actions, "_smart_install_check", stub_check)

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        taskdir = tmp_path / "taskdir"
        taskdir.mkdir()
        host = LocalHost(taskdir=str(taskdir))
        await host.setup_workdir()
        parent = _ExecutingFakeCtx()
        hook_ctx = HookContext(parent, host)

        path = await host_actions.ensure_opencode_installed(hook_ctx)
        assert path == str(fake_home / ".local" / "bin" / "opencode")
        st = os.stat(path)
        assert st.st_mode & 0o111
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home
