"""Tests for optio_host.download — the URL → file task factory and HookContext.download_file."""


def test_download_failed_fields_and_str():
    from optio_host.download import DownloadFailed
    err = DownloadFailed(
        url="https://example/foo.bin",
        target="/tmp/foo.bin",
        exit_code=22,
        stderr_tail="curl: (22) The requested URL returned error: 404\n",
    )
    assert err.url == "https://example/foo.bin"
    assert err.target == "/tmp/foo.bin"
    assert err.exit_code == 22
    assert "curl" in err.stderr_tail
    s = str(err)
    assert "https://example/foo.bin" in s
    assert "22" in s
    assert "404" in s


def test_create_download_task_returns_taskinstance_with_fields():
    from optio_core.models import TaskInstance
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
        host=None,
        description="grab the binary",
    )

    assert isinstance(t, TaskInstance)
    assert t.process_id == "p.download-0"
    assert t.name == "download foo.bin"
    assert t.description == "grab the binary"
    assert t.cancellable is True
    assert t.supports_resume is False
    assert t.auto_cancel_children is True
    assert t.ui_widget is None
    assert callable(t.execute)


def test_create_download_task_defaults_description():
    from optio_host.download import create_download_task

    t = create_download_task(
        process_id="p.download-0",
        name="download foo.bin",
        url="https://example.com/foo.bin",
        target="/tmp/foo.bin",
    )
    assert t.description is None


import pytest


class _RoutingFakeCtx:
    """Fake ProcessContext for testing HookContext.download_file routing only."""

    def __init__(self, *, process_id="p"):
        self.process_id = process_id
        self._child_counter = {"next": 0}
        self.run_child_calls = []

    async def run_child_task(self, task, **kw):
        self.run_child_calls.append(
            (task.execute, task.process_id, task.name, task.description)
        )
        self._child_counter["next"] += 1
        return "done"


class _RoutingFakeHost:
    def __init__(self, *, workdir="/wd", host_home="/home/u"):
        self.workdir = workdir
        self._host_home = host_home

    async def resolve_host_home(self):
        return self._host_home


async def test_download_file_routes_through_run_child_with_generated_id_and_name():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin",
        "downloads/foo.bin",
    )

    assert len(ctx.run_child_calls) == 1
    execute, pid, name, description = ctx.run_child_calls[0]
    assert pid == "root.parent.download-0"
    assert name == "download foo.bin"
    assert description is None


async def test_download_file_second_call_increments_counter():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx(process_id="root.parent")
    host = _RoutingFakeHost(workdir="/wd")
    h = HookContext(ctx, host)

    await h.download_file("https://example/a.bin", "a.bin")
    await h.download_file("https://example/b.bin", "b.bin")

    assert ctx.run_child_calls[0][1] == "root.parent.download-0"
    assert ctx.run_child_calls[1][1] == "root.parent.download-1"


async def test_download_file_passes_description_through():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    await h.download_file(
        "https://example/foo.bin", "foo.bin",
        description="grab it",
    )
    assert ctx.run_child_calls[0][3] == "grab it"


async def test_download_file_resolves_workdir_relative_target_to_absolute(monkeypatch):
    """The factory should receive an already-resolved absolute target path."""
    from optio_host import context as ctx_mod

    captured: dict = {}
    original = ctx_mod.create_download_task

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx_mod, "create_download_task", spy)

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost(workdir="/wd")
    h = ctx_mod.HookContext(ctx, host)
    await h.download_file("https://example/foo.bin", "sub/foo.bin")

    assert captured["target"] == "/wd/sub/foo.bin"
    assert captured["url"] == "https://example/foo.bin"
    assert captured["host"] is host


async def test_download_file_rejects_workdir_escape_without_spawning():
    from optio_host.context import HookContext

    ctx = _RoutingFakeCtx()
    host = _RoutingFakeHost()
    h = HookContext(ctx, host)

    with pytest.raises(ValueError):
        await h.download_file("https://example/foo", "../escape")
    assert ctx.run_child_calls == []


def test_download_file_appears_on_hook_context_protocol():
    from optio_host.context import HookContextProtocol
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    assert "download_file" in methods


def test_create_download_task_and_downloadfailed_exported_from_optio_host():
    import optio_host
    assert hasattr(optio_host, "create_download_task")
    assert hasattr(optio_host, "DownloadFailed")


def test_parse_trace_line_content_length():
    from optio_host.download import _parse_trace_line
    out = _parse_trace_line(b"0000: content-length: 1048576\r\n")
    assert out == ("length", 1048576)


def test_parse_trace_line_recv_data():
    from optio_host.download import _parse_trace_line
    out = _parse_trace_line(b"<= Recv data, 16384 bytes (0x4000)\n")
    assert out == ("recv", 16384)


def test_parse_trace_line_recv_data_lowercased():
    from optio_host.download import _parse_trace_line
    out = _parse_trace_line(b"<= recv data, 4096 bytes (0x1000)\n")
    assert out == ("recv", 4096)


def test_parse_trace_line_unrelated_returns_none():
    from optio_host.download import _parse_trace_line
    assert _parse_trace_line(b"== Info: Trying 1.2.3.4...\n") is None
    assert _parse_trace_line(b"=> Send header, 123 bytes (0x7b)\n") is None
    assert _parse_trace_line(b"") is None
    assert _parse_trace_line(b"0000: GET /foo HTTP/1.1\n") is None


def test_build_curl_cmd_includes_required_flags():
    from optio_host.download import _build_curl_cmd
    cmd = _build_curl_cmd(url="https://example/foo bin", target="/tmp/out file")
    assert "--trace-ascii -" in cmd
    assert " -s " in cmd
    assert " -f " in cmd
    assert " -L " in cmd
    assert "'/tmp/out file'" in cmd
    assert "'https://example/foo bin'" in cmd


def test_build_curl_cmd_omits_stdbuf_when_unavailable(monkeypatch):
    import shutil
    from optio_host import download as dl_mod
    monkeypatch.setattr(
        shutil, "which",
        lambda name: None if name == "stdbuf" else f"/usr/bin/{name}",
    )
    cmd = dl_mod._build_curl_cmd(url="https://example/foo", target="/tmp/out")
    assert cmd.startswith("exec curl ")
    assert "stdbuf" not in cmd


def test_build_curl_cmd_includes_stdbuf_when_available(monkeypatch):
    import shutil
    from optio_host import download as dl_mod
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/usr/bin/" + name,
    )
    cmd = dl_mod._build_curl_cmd(url="https://example/foo", target="/tmp/out")
    assert cmd.startswith("exec stdbuf -oL curl ")


# ---------------------------------------------------------------------------
# Real-curl integration tests for _execute (no-host and host branches).
# ---------------------------------------------------------------------------

import asyncio
import hashlib
import os
import threading
import time as _time
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


@pytest.fixture
def http_server(tmp_path):
    """Serve ``tmp_path/served/`` over a thread-backed HTTP server.

    Yields (base_url, served_dir).
    """
    served = tmp_path / "served"
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


@pytest.fixture
def slow_http_server(tmp_path):
    """Serve a 50MB blob throttled per 64KB chunk with 50ms sleeps."""
    served = tmp_path / "slow_served"
    served.mkdir()
    blob = os.urandom(50 * 1024 * 1024)
    (served / "big.bin").write_bytes(blob)

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.lstrip("/")
            file_path = served / path
            if not file_path.is_file():
                self.send_error(404)
                return
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            chunk = 64 * 1024
            for i in range(0, len(data), chunk):
                try:
                    self.wfile.write(data[i:i + chunk])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                _time.sleep(0.05)

        def log_message(self, format, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


class _RecordingCtx:
    """Fake ProcessContext for execute-level tests. Records report_progress."""

    def __init__(self):
        self.process_id = "p"
        self.cancellation_flag = asyncio.Event()
        self.progress = []

    def report_progress(self, percent, message=None):
        self.progress.append((percent, message))

    def should_continue(self) -> bool:
        return not self.cancellation_flag.is_set()


async def test_download_execute_no_host_happy_path(http_server, tmp_path):
    base_url, served = http_server
    blob = os.urandom(4 * 1024 * 1024)
    (served / "blob.bin").write_bytes(blob)
    expected_sha = hashlib.sha256(blob).hexdigest()

    from optio_host.download import create_download_task

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download blob.bin",
        url=f"{base_url}/blob.bin",
        target=str(target),
        host=None,
    )
    ctx = _RecordingCtx()
    await task.execute(ctx)

    # Basename in the message is derived from the target path per design.
    assert ctx.progress[0] == (None, "Downloading out.bin")
    numeric = [p for p, m in ctx.progress[1:] if p is not None]
    assert numeric, "expected at least one numeric progress report"
    for a, b in zip(numeric, numeric[1:]):
        assert a <= b, f"percent went backwards: {a} -> {b}"
    assert numeric[-1] >= 99.0
    assert target.exists()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha


async def test_download_execute_no_host_404_raises_and_cleans_up(http_server, tmp_path):
    base_url, _ = http_server  # served dir empty; any URL is 404

    from optio_host.download import DownloadFailed, create_download_task

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download nope.bin",
        url=f"{base_url}/nope.bin",
        target=str(target),
        host=None,
    )
    ctx = _RecordingCtx()
    with pytest.raises(DownloadFailed) as ei:
        await task.execute(ctx)
    assert ei.value.exit_code == 22
    assert ei.value.url == f"{base_url}/nope.bin"
    assert ei.value.target == str(target)
    assert ei.value.stderr_tail
    assert not target.exists()


async def test_download_execute_no_host_404_no_cleanup_does_not_call_remove(
    http_server, tmp_path, monkeypatch,
):
    base_url, _ = http_server
    from optio_host import download as dl_mod
    from optio_host.download import DownloadFailed, create_download_task

    called = {"n": 0}

    async def spy_remove(host, target):
        called["n"] += 1

    monkeypatch.setattr(dl_mod, "_maybe_remove", spy_remove)

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download nope.bin",
        url=f"{base_url}/nope.bin",
        target=str(target),
        host=None,
        cleanup_on_fail=False,
    )
    ctx = _RecordingCtx()
    with pytest.raises(DownloadFailed):
        await task.execute(ctx)
    assert called["n"] == 0


async def test_download_execute_no_host_cancel_mid_stream(slow_http_server, tmp_path):
    from optio_host.download import create_download_task

    target = tmp_path / "out.bin"
    task = create_download_task(
        process_id="p.download-0",
        name="download big.bin",
        url=f"{slow_http_server}/big.bin",
        target=str(target),
        host=None,
    )
    ctx = _RecordingCtx()

    async def _run():
        await task.execute(ctx)

    run_t = asyncio.create_task(_run())
    for _ in range(200):
        if any(p is not None and p > 0 for p, _m in ctx.progress):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("did not see any numeric progress before cancelling")

    start = _time.monotonic()
    ctx.cancellation_flag.set()
    await asyncio.wait_for(run_t, timeout=10.0)
    elapsed = _time.monotonic() - start

    assert elapsed < 8.0, f"cancel took too long: {elapsed:.1f}s"
    assert not target.exists()


async def test_download_execute_host_happy_path_via_localhost(http_server, tmp_path):
    base_url, served = http_server
    blob = os.urandom(2 * 1024 * 1024)
    (served / "blob.bin").write_bytes(blob)
    expected_sha = hashlib.sha256(blob).hexdigest()

    from optio_host.host import LocalHost
    from optio_host.download import create_download_task

    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    host = LocalHost(taskdir=str(taskdir))
    await host.setup_workdir()
    target_abs = os.path.join(host.workdir, "out.bin")

    task = create_download_task(
        process_id="p.download-0",
        name="download blob.bin",
        url=f"{base_url}/blob.bin",
        target=target_abs,
        host=host,
    )
    ctx = _RecordingCtx()
    await task.execute(ctx)

    assert ctx.progress[0] == (None, "Downloading out.bin")
    numeric = [p for p, m in ctx.progress[1:] if p is not None]
    assert numeric
    for a, b in zip(numeric, numeric[1:]):
        assert a <= b
    assert numeric[-1] >= 99.0
    assert os.path.exists(target_abs)
    assert hashlib.sha256(Path(target_abs).read_bytes()).hexdigest() == expected_sha


async def test_download_execute_host_404_raises_and_cleans_up(http_server, tmp_path):
    base_url, _ = http_server
    from optio_host.host import LocalHost
    from optio_host.download import DownloadFailed, create_download_task

    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    host = LocalHost(taskdir=str(taskdir))
    await host.setup_workdir()
    target_abs = os.path.join(host.workdir, "nope.bin")

    task = create_download_task(
        process_id="p.download-0",
        name="download nope.bin",
        url=f"{base_url}/nope.bin",
        target=target_abs,
        host=host,
    )
    ctx = _RecordingCtx()
    with pytest.raises(DownloadFailed) as ei:
        await task.execute(ctx)
    assert ei.value.exit_code == 22
    assert not os.path.exists(target_abs)


async def test_download_execute_host_cancel_mid_stream(slow_http_server, tmp_path):
    from optio_host.host import LocalHost
    from optio_host.download import create_download_task

    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    host = LocalHost(taskdir=str(taskdir))
    await host.setup_workdir()
    target_abs = os.path.join(host.workdir, "out.bin")

    task = create_download_task(
        process_id="p.download-0",
        name="download big.bin",
        url=f"{slow_http_server}/big.bin",
        target=target_abs,
        host=host,
    )
    ctx = _RecordingCtx()

    run_t = asyncio.create_task(task.execute(ctx))
    for _ in range(200):
        if any(p is not None and p > 0 for p, _m in ctx.progress):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("did not see any numeric progress before cancelling")

    start = _time.monotonic()
    ctx.cancellation_flag.set()
    await asyncio.wait_for(run_t, timeout=10.0)
    elapsed = _time.monotonic() - start

    assert elapsed < 8.0, f"cancel took too long: {elapsed:.1f}s"
    assert not os.path.exists(target_abs)
