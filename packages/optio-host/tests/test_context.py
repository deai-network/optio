"""Tests for HookContext foundations: dataclasses + path resolver."""

import pytest

from optio_host.context import (
    HostCommandError,
    RunResult,
    _resolve_target_path,
)


def test_run_result_fields():
    r = RunResult(stdout="hi\n", stderr="", exit_code=0)
    assert r.stdout == "hi\n"
    assert r.stderr == ""
    assert r.exit_code == 0


def test_host_command_error_str_includes_exit_code_and_stderr():
    err = HostCommandError(
        command="false", exit_code=1, stdout="", stderr="boom\n",
    )
    s = str(err)
    assert "exit 1" in s
    assert "boom" in s
    assert "false" in s


def test_resolve_target_path_workdir_relative():
    out = _resolve_target_path("data/foo.yaml", "/wd", "/home/u")
    assert out == "/wd/data/foo.yaml"


def test_resolve_target_path_absolute_passthrough():
    assert _resolve_target_path("/usr/local/bin/tool", "/wd", "/home/u") == "/usr/local/bin/tool"


def test_resolve_target_path_home_relative_expanded():
    assert _resolve_target_path("~/.local/bin/tool", "/wd", "/home/u") == "/home/u/.local/bin/tool"


def test_resolve_target_path_bare_tilde_expanded():
    assert _resolve_target_path("~", "/wd", "/home/u") == "/home/u"


def test_resolve_target_path_rejects_empty():
    with pytest.raises(ValueError):
        _resolve_target_path("", "/wd", "/home/u")


def test_resolve_target_path_rejects_dotdot_in_workdir_relative():
    with pytest.raises(ValueError):
        _resolve_target_path("data/../../etc/passwd", "/wd", "/home/u")


def test_resolve_target_path_rejects_workdir_relative_escape():
    # Even after normalization, must stay inside workdir.
    with pytest.raises(ValueError):
        _resolve_target_path("../outside", "/wd", "/home/u")


def test_resolve_target_path_dotdot_allowed_in_absolute():
    # Absolute paths are consumer-trusted; .. is fine there.
    assert _resolve_target_path("/usr/../tmp/x", "/wd", "/home/u") == "/usr/../tmp/x"


def test_resolve_target_path_dotdot_allowed_in_home_relative():
    # Home-relative is also consumer-trusted.
    assert _resolve_target_path("~/foo/../bar", "/wd", "/home/u") == "/home/u/foo/../bar"


import asyncio

from optio_host.context import HookContext, HookContextProtocol


class _FakeCtx:
    def __init__(self):
        self.process_id = "p"
        self.params = {"k": "v"}
        self.calls = []

    def report_progress(self, percent, message=None):
        self.calls.append(("rp", percent, message))


class _FakeHost:
    def __init__(self):
        self.workdir = "/wd"


def test_hook_context_delegates_attributes():
    ctx = _FakeCtx()
    host = _FakeHost()
    h = HookContext(ctx, host)
    # Process-context attributes flow through __getattr__.
    assert h.process_id == "p"
    assert h.params == {"k": "v"}
    h.report_progress(50, "halfway")
    assert ctx.calls == [("rp", 50, "halfway")]


def test_hook_context_protocol_is_protocol():
    # Smoke test: the Protocol exists and has the expected method names.
    methods = {m for m in dir(HookContextProtocol) if not m.startswith("_")}
    expected = {
        "copy_file", "run_on_host", "read_from_host", "read_text_from_host",
        "report_progress", "should_continue", "params", "metadata",
    }
    assert expected <= methods


class _FakeRunHost:
    def __init__(self, results):
        self.workdir = "/wd"
        self._results = list(results)
        self.calls = []

    async def run_command(self, command, *, cwd=None, env=None):
        self.calls.append((command, cwd, env))
        return self._results.pop(0)


async def test_run_on_host_check_true_returns_stdout_on_success():
    from optio_host.context import RunResult
    host = _FakeRunHost([RunResult(stdout="hi\n", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("echo hi")
    assert out == "hi\n"


async def test_run_on_host_check_true_raises_on_nonzero():
    from optio_host.context import HostCommandError, RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="boom", exit_code=2)])
    h = HookContext(_FakeCtx(), host)
    with pytest.raises(HostCommandError) as ei:
        await h.run_on_host("false")
    assert ei.value.exit_code == 2
    assert ei.value.stderr == "boom"


async def test_run_on_host_check_false_returns_result_object():
    from optio_host.context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="oops", exit_code=3)])
    h = HookContext(_FakeCtx(), host)
    res = await h.run_on_host("false", check=False)
    assert res.exit_code == 3
    assert res.stderr == "oops"


async def test_run_on_host_capture_stderr_merges_into_returned_stdout():
    from optio_host.context import RunResult
    host = _FakeRunHost([RunResult(stdout="o", stderr="e", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    out = await h.run_on_host("cmd", capture_stderr=True)
    assert out == "oe"


async def test_run_on_host_cwd_is_forwarded():
    from optio_host.context import RunResult
    host = _FakeRunHost([RunResult(stdout="", stderr="", exit_code=0)])
    h = HookContext(_FakeCtx(), host)
    await h.run_on_host("pwd", cwd="/elsewhere")
    assert host.calls[0][1] == "/elsewhere"


class _FakeCopyHost:
    def __init__(self, *, host_home="/home/u"):
        self.workdir = "/wd"
        self._host_home = host_home
        self.put_calls = []
        self.fetch_calls = []
        self.fetch_returns = b""

    async def resolve_host_home(self):
        return self._host_home

    async def put_file_to_host(
        self, source, absolute_target, *,
        expected_sha256=None, skip_if_unchanged=False, progress_cb=None,
    ):
        # Snapshot the call.
        self.put_calls.append({
            "source": source,
            "absolute_target": absolute_target,
            "expected_sha256": expected_sha256,
            "skip_if_unchanged": skip_if_unchanged,
        })
        if progress_cb is not None:
            progress_cb(None, None)
            progress_cb(50.0, None)
            progress_cb(100.0, None)

    async def fetch_bytes_from_host(self, absolute_path, *, progress_cb=None):
        self.fetch_calls.append(absolute_path)
        return self.fetch_returns


async def test_copy_file_workdir_relative_resolves_to_workdir():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "out/foo.bin")
    assert host.put_calls[0]["absolute_target"] == "/wd/out/foo.bin"


async def test_copy_file_absolute_passthrough():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "/usr/local/bin/tool")
    assert host.put_calls[0]["absolute_target"] == "/usr/local/bin/tool"


async def test_copy_file_home_relative_expanded():
    host = _FakeCopyHost(host_home="/home/u")
    h = HookContext(_FakeCtx(), host)
    await h.copy_file(b"data", "~/.local/bin/tool")
    assert host.put_calls[0]["absolute_target"] == "/home/u/.local/bin/tool"


async def test_copy_file_path_source_forwarded():
    host = _FakeCopyHost()
    h = HookContext(_FakeCtx(), host)
    await h.copy_file("/worker/path/file", "out.bin")
    assert host.put_calls[0]["source"] == "/worker/path/file"


async def test_copy_file_emits_progress_messages():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(b"data", "out.bin")
    # First call: start message; subsequent: percent.
    msgs = [c[2] for c in ctx.calls]
    assert any("Copying out.bin" in (m or "") for m in msgs)


async def test_copy_file_skip_if_unchanged_emits_verifying_then_done():
    """When the host reports skipped (progress_cb called once with 'already up to date'),
    HookContext should emit Verifying + Already up to date messages."""
    ctx = _FakeCtx()

    class _SkippingHost(_FakeCopyHost):
        async def put_file_to_host(self, source, absolute_target, *,
                                   expected_sha256=None,
                                   skip_if_unchanged=False,
                                   progress_cb=None):
            self.put_calls.append({"source": source, "absolute_target": absolute_target})
            if progress_cb is not None:
                progress_cb(None, "already up to date")

    host = _SkippingHost()
    h = HookContext(ctx, host)
    await h.copy_file(b"data", "out.bin", skip_if_unchanged=True)
    msgs = [c[2] for c in ctx.calls]
    assert any("Verifying out.bin" in (m or "") for m in msgs)
    assert any("Already up to date: out.bin" in (m or "") for m in msgs)


async def test_read_from_host_workdir_relative_resolves():
    host = _FakeCopyHost()
    host.fetch_returns = b"contents"
    h = HookContext(_FakeCtx(), host)
    out = await h.read_from_host("data/x")
    assert out == b"contents"
    assert host.fetch_calls[0] == "/wd/data/x"


async def test_read_text_from_host_decodes_utf8():
    host = _FakeCopyHost()
    host.fetch_returns = "héllo".encode("utf-8")
    h = HookContext(_FakeCtx(), host)
    out = await h.read_text_from_host("data/x")
    assert out == "héllo"


async def test_read_from_host_emits_reading_message():
    ctx = _FakeCtx()
    host = _FakeCopyHost()
    host.fetch_returns = b"abc"
    h = HookContext(ctx, host)
    await h.read_from_host("data/x")
    msgs = [c[2] for c in ctx.calls]
    assert any("Reading x" in (m or "") for m in msgs)


from bson import ObjectId


class _FakeBlobReader:
    def __init__(self, data: bytes):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self, n=None):
        if n is None:
            data, self._data = self._data, b""
            return data
        out, self._data = self._data[:n], self._data[n:]
        return out


class _BlobCtx(_FakeCtx):
    def __init__(self, blobs: dict):
        super().__init__()
        self._blobs = blobs

    def load_blob(self, file_id):
        # Return an async-context-manager wrapping a reader.
        return _FakeBlobReader(self._blobs[file_id])


async def test_copy_file_objectid_source_streams_blob():
    blob_id = ObjectId()
    payload = b"blob-payload"
    ctx = _BlobCtx({blob_id: payload})
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(blob_id, "out.bin")
    # The host should have received an async iterator that yields the blob bytes.
    src = host.put_calls[0]["source"]
    assert hasattr(src, "__aiter__")
    collected = b"".join([chunk async for chunk in src])
    assert collected == payload


async def test_copy_file_objectid_skip_if_unchanged_supplies_expected_sha():
    import hashlib
    blob_id = ObjectId()
    payload = b"blob-payload"
    expected_sha = hashlib.sha256(payload).hexdigest()
    ctx = _BlobCtx({blob_id: payload})
    host = _FakeCopyHost()
    h = HookContext(ctx, host)
    await h.copy_file(blob_id, "out.bin", skip_if_unchanged=True)
    assert host.put_calls[0]["expected_sha256"] == expected_sha
    assert host.put_calls[0]["skip_if_unchanged"] is True
