"""Streaming removal of one member from a gzip tar. Pure: no Mongo, no host.

`strip_member` reads a gzip tar stream and writes a gzip tar of the same
members, in the same order and with the same headers (mode, mtime, uid/gid,
names, link targets, type), except the top-level member named ``name``, with or
without a leading ``./``. It returns that member's bytes. The deferred
migration in `optio_claudecode.migrations` uses it to take the conversation
buffer file out of workdir snapshot blobs written before the fix
(docs/2026-10-11-conversation-buffer-encryption-design.md, section 6).

Memory stays bounded for any archive size: `tarfile` runs in stream mode
(``r|gz`` in, ``w|`` out through a level-1 gzip writer) on a worker thread.
Bounded queues feed it and drain it, so only a few MiB are in flight. Only
the stripped member is held whole.
"""

from __future__ import annotations

import asyncio
import tarfile
import threading
import zlib
from typing import AsyncIterator, Awaitable, Callable


class ArchiveError(Exception):
    """The input is not a readable gzip tar (bad gzip, bad header, truncated)."""


# Queue depth on each side. Input items are the caller's chunks (GridFS reads
# are 1 MiB); output items are _OUT_BATCH-sized.
_QUEUE_DEPTH = 4
_OUT_BATCH = 1 << 20
# How often a worker blocked on a queue checks for an abort.
_ABORT_POLL_S = 0.25
# The capture compresses with `pigz -1` / `gzip -1`. tarfile's `w|gz` only
# takes a compresslevel from Python 3.12, and its default is 9.
_GZIP_LEVEL = 1

_EOF = None
_END = object()


class _Aborted(Exception):
    """The async side gave up (a failure or a cancellation); the worker unwinds."""


class _Bridge:
    """Two bounded asyncio queues between the event loop and the worker thread,
    plus an abort flag that a worker blocked on either queue notices."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.inq: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_DEPTH)
        self.outq: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_DEPTH)
        self.abort = threading.Event()

    def call(self, coro):
        """Worker thread: run a queue operation on the loop and wait for it."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        while True:
            if self.abort.is_set():
                fut.cancel()
                raise _Aborted()
            try:
                return fut.result(timeout=_ABORT_POLL_S)
            except TimeoutError:
                continue


class _QueueReader:
    """Worker side: a read-only file object over the input queue."""

    def __init__(self, bridge: _Bridge) -> None:
        self._bridge = bridge
        self._chunk = memoryview(b"")
        self._eof = False

    def read(self, n: int = -1) -> bytes:
        while not self._chunk:
            if self._eof:
                return b""
            item = self._bridge.call(self._bridge.inq.get())
            if item is _EOF:
                self._eof = True
                return b""
            self._chunk = memoryview(item)
        if n is None or n < 0 or n >= len(self._chunk):
            out, self._chunk = self._chunk, memoryview(b"")
        else:
            out, self._chunk = self._chunk[:n], self._chunk[n:]
        return bytes(out)


class _QueueGzipWriter:
    """Worker side: a write-only file object that gzips into the output queue
    in _OUT_BATCH pieces."""

    def __init__(self, bridge: _Bridge) -> None:
        self._bridge = bridge
        self._gzip = zlib.compressobj(_GZIP_LEVEL, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        self._buf = bytearray()

    def write(self, data) -> int:
        self._buf += self._gzip.compress(data)
        if len(self._buf) >= _OUT_BATCH:
            self._push()
        return len(data)

    def finish(self) -> None:
        self._buf += self._gzip.flush()
        self._push()

    def _push(self) -> None:
        if self._buf:
            self._bridge.call(self._bridge.outq.put(bytes(self._buf)))
            self._buf.clear()


def _bare(member_name: str) -> str:
    return member_name[2:] if member_name.startswith("./") else member_name


def _strip_sync(bridge: _Bridge, name: str) -> bytes | None:
    """Worker thread body. Returns the stripped member's bytes (the last
    regular-file match), or None."""
    reader = _QueueReader(bridge)
    writer = _QueueGzipWriter(bridge)
    stripped = None
    try:
        with tarfile.open(fileobj=reader, mode="r|gz") as tin, \
                tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tout:
            while (member := tin.next()) is not None:
                # TarFile keeps a TarInfo per member (`addfile` always; `next`
                # in stream mode before Python 3.13). Drop them so memory does
                # not grow with the member count.
                tin.members.clear()
                if _bare(member.name) == name:
                    if member.isreg():
                        stripped = tin.extractfile(member).read()
                    continue
                tout.addfile(member, tin.extractfile(member) if member.isreg() else None)
                tout.members.clear()
    except _Aborted:
        raise
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        # OSError: tarfile reports a truncated member as OSError("unexpected
        # end of data"). The file objects here are in memory, so nothing else
        # raises it.
        raise ArchiveError(f"unreadable gzip tar: {exc}") from exc
    writer.finish()
    bridge.call(bridge.outq.put(_END))
    return stripped


async def strip_member(
    src: AsyncIterator[bytes],
    sink: Callable[[bytes], Awaitable[None]],
    *,
    name: str,
) -> bytes | None:
    """Copy the gzip tar read from ``src`` into ``sink`` (gzip tar chunks)
    without its top-level member ``name`` (matched with any one leading ``./``
    removed). Returns that member's bytes, or None when the archive has no such
    regular file (then the output holds every member).

    ``src`` is consumed and closed (``aclose``) when it is an async generator;
    the archive's tail past the end-of-archive marker may be left unread.
    Raises ArchiveError for input that is not a readable gzip tar; an error
    from ``src`` or ``sink`` propagates as is. On any error the output is
    incomplete and should be discarded.
    """
    bridge = _Bridge(asyncio.get_running_loop())

    async def feed() -> None:
        try:
            async for chunk in src:
                if chunk:
                    await bridge.inq.put(chunk)
            await bridge.inq.put(_EOF)
        finally:
            aclose = getattr(src, "aclose", None)
            if aclose is not None:
                await aclose()

    async def drain() -> None:
        while (item := await bridge.outq.get()) is not _END:
            await sink(item)

    worker = asyncio.ensure_future(asyncio.to_thread(_strip_sync, bridge, name))
    feeder = asyncio.ensure_future(feed())
    drainer = asyncio.ensure_future(drain())
    try:
        pending = {worker, feeder, drainer}
        while not (worker.done() and drainer.done()):
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception() is not None:
                    raise task.exception()
        return worker.result()
    finally:
        # Normal end: the feeder may still be waiting to hand over the
        # archive's unread tail. Error or cancellation: stop everything; the
        # worker notices the abort within _ABORT_POLL_S.
        bridge.abort.set()
        feeder.cancel()
        drainer.cancel()
        await asyncio.gather(worker, feeder, drainer, return_exceptions=True)
