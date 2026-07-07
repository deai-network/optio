"""Unit tests for the three harmonization port features (P1/P2/P3).

All xdist-safe and spawn-free — they exercise the wrapper's own wiring with
in-memory fakes for the Host / ProcessContext / HookContext, not a live agy:

  * P1 session-blob encryption — ``_capture_snapshot`` encrypts the workdir tar
    when a transform is set; ``_stream_restore_blob`` decrypts on read (and
    streams unchanged when no transform is configured).
  * P2 resume-refresh — ``_maybe_refresh_on_resume`` recomposes AGENTS.md and
    rewrites it only when the content changed.
  * P3 caller-message channel — ``run_antigravity_session`` threads the
    client/caller-message flags into ``get_protocol`` and passes
    ``on_caller_message`` to ``run_log_protocol_session``.

The full-cycle spawn tests (test_session_*.py) cover the integration path.
"""

from __future__ import annotations

import contextlib

import pytest

from optio_antigravity import session as agy_session
from optio_antigravity.prompt import compose_agents_md
from optio_antigravity.types import AntigravityTaskConfig


# --- fakes ------------------------------------------------------------------


class _FakeReader:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, n: int) -> bytes:
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeWriter:
    def __init__(self):
        self.buf = bytearray()
        self.file_id = "blob-1"

    async def write(self, data: bytes) -> None:
        self.buf.extend(data)


class _FakeCtx:
    """Minimal ProcessContext stand-in for the blob helpers."""

    def __init__(self, *, load_data: bytes = b"", process_id: str = "p"):
        self._load_data = load_data
        self.process_id = process_id
        self._db = None
        self._prefix = "test"
        self.writer = _FakeWriter()
        self.marked = False

    @contextlib.asynccontextmanager
    async def store_blob(self, kind: str):
        yield self.writer

    @contextlib.asynccontextmanager
    async def load_blob(self, blob_id):
        yield _FakeReader(self._load_data)

    async def mark_has_saved_state(self) -> None:
        self.marked = True


class _FakeHost:
    def __init__(self, *, archive: bytes = b"", existing_text: dict | None = None):
        self._archive = archive
        self.written: dict[str, str] = {}
        self._existing = existing_text or {}

    async def archive_workdir(self, exclude):
        # Emit in two chunks so the buffer-join path is exercised.
        mid = len(self._archive) // 2
        yield self._archive[:mid]
        yield self._archive[mid:]

    async def write_text(self, name: str, content: str) -> None:
        self.written[name] = content


class _FakeHookCtx:
    def __init__(self, host: _FakeHost, *, missing: bool = False, boom: bool = False):
        self._host = host
        self._missing = missing
        self._boom = boom

    async def read_text_from_host(self, path: str, *, silent: bool = False) -> str:
        if self._boom:
            raise RuntimeError("read failed")
        if self._missing or path not in self._host.written:
            raise FileNotFoundError(path)
        return self._host.written[path]


# --- P1: session-blob encryption -------------------------------------------


async def test_stream_restore_blob_plaintext_passthrough():
    ctx = _FakeCtx(load_data=b"tar-bytes")
    out = b"".join(
        [c async for c in agy_session._stream_restore_blob(ctx, "b", decrypt=None)]
    )
    assert out == b"tar-bytes"


async def test_stream_restore_blob_decrypts_whole_blob():
    xor = lambda b: bytes(x ^ 0x5A for x in b)  # noqa: E731
    ciphertext = xor(b"plain-tar")
    ctx = _FakeCtx(load_data=ciphertext)
    out = b"".join(
        [c async for c in agy_session._stream_restore_blob(ctx, "b", decrypt=xor)]
    )
    assert out == b"plain-tar"


async def test_capture_snapshot_encrypts_when_transform_set(monkeypatch):
    monkeypatch.setattr(agy_session, "insert_snapshot", _noop_async)
    monkeypatch.setattr(agy_session, "prune_snapshots", _return_empty)
    xor = lambda b: bytes(x ^ 0x5A for x in b)  # noqa: E731
    ctx = _FakeCtx()
    host = _FakeHost(archive=b"the-workdir-tar")
    await agy_session._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None,
        session_blob_encrypt=xor,
    )
    # Stored bytes are the ciphertext; decrypting recovers the archive.
    assert bytes(ctx.writer.buf) == xor(b"the-workdir-tar")
    assert xor(bytes(ctx.writer.buf)) == b"the-workdir-tar"
    assert ctx.marked is True


async def test_capture_snapshot_plaintext_default(monkeypatch):
    monkeypatch.setattr(agy_session, "insert_snapshot", _noop_async)
    monkeypatch.setattr(agy_session, "prune_snapshots", _return_empty)
    ctx = _FakeCtx()
    host = _FakeHost(archive=b"the-workdir-tar")
    await agy_session._capture_snapshot(
        ctx, host, end_state="done", workdir_exclude=None,
    )
    assert bytes(ctx.writer.buf) == b"the-workdir-tar"


async def _noop_async(*a, **k):
    return None


async def _return_empty(*a, **k):
    return []


# --- P2: resume-refresh -----------------------------------------------------


def _cfg(**kw) -> AntigravityTaskConfig:
    base = dict(consumer_instructions="do the thing", fs_isolation=False)
    base.update(kw)
    return AntigravityTaskConfig(**base)


def _rendered(config: AntigravityTaskConfig) -> str:
    return compose_agents_md(
        config.consumer_instructions,
        host_protocol=config.host_protocol,
        workdir_exclude=config.workdir_exclude,
        supports_resume=config.supports_resume,
        file_download=config.file_download,
    )


async def test_resume_refresh_no_rewrite_when_unchanged():
    config = _cfg()
    host = _FakeHost()
    host.written["AGENTS.md"] = _rendered(config)  # restored == recomposed
    hook_ctx = _FakeHookCtx(host)
    refreshed = await agy_session._maybe_refresh_on_resume(host, hook_ctx, config)
    assert refreshed == []


async def test_resume_refresh_rewrites_when_changed():
    config = _cfg()
    host = _FakeHost()
    host.written["AGENTS.md"] = "STALE CONTENT"
    hook_ctx = _FakeHookCtx(host)
    refreshed = await agy_session._maybe_refresh_on_resume(host, hook_ctx, config)
    assert refreshed == ["AGENTS.md"]
    assert host.written["AGENTS.md"] == _rendered(config)


async def test_resume_refresh_disabled_returns_empty():
    config = _cfg(on_resume_refresh=None)
    host = _FakeHost()
    hook_ctx = _FakeHookCtx(host)
    refreshed = await agy_session._maybe_refresh_on_resume(host, hook_ctx, config)
    assert refreshed == []
    assert "AGENTS.md" not in host.written


async def test_resume_refresh_swallows_hook_error():
    def _boom(_c):
        raise RuntimeError("hook exploded")

    config = _cfg(on_resume_refresh=_boom)
    host = _FakeHost()
    hook_ctx = _FakeHookCtx(host)
    refreshed = await agy_session._maybe_refresh_on_resume(host, hook_ctx, config)
    assert refreshed == []
    assert "AGENTS.md" not in host.written


# --- P3: caller-message channel --------------------------------------------


class _ProtoCtx:
    """Just enough ProcessContext for run_antigravity_session's outer shell."""

    def __init__(self):
        self.process_id = "p"

    def should_continue(self) -> bool:
        return True


class _ProtoHost:
    async def connect(self) -> None:
        pass

    async def cleanup_taskdir(self, aggressive: bool) -> None:
        pass

    async def disconnect(self) -> None:
        pass


@pytest.mark.parametrize(
    "use_client,has_cb", [(False, False), (True, True)],
)
async def test_caller_message_flags_flow_to_protocol(monkeypatch, use_client, has_cb):
    captured: dict = {}

    def fake_get_protocol(**kw):
        captured["protocol"] = kw
        return object()

    async def fake_run(host, ctx, **kw):
        captured["run"] = kw

    monkeypatch.setattr(agy_session, "get_protocol", fake_get_protocol)
    monkeypatch.setattr(agy_session, "run_log_protocol_session", fake_run)
    monkeypatch.setattr(agy_session, "_build_host", lambda config, pid: _ProtoHost())

    async def _cb(*a, **k):
        return None

    cb = _cb if has_cb else None
    config = _cfg(use_client_messages=use_client, on_caller_message=cb)
    await agy_session.run_antigravity_session(_ProtoCtx(), config)

    assert captured["protocol"]["browser"] == "redirect"
    assert captured["protocol"]["client_messages"] is use_client
    assert captured["protocol"]["caller_messages"] is has_cb
    assert captured["run"]["on_caller_message"] is cb
