"""Streaming removal of one member from a gzip tar. Pure: no Mongo, no host.

`strip_member` reads a gzip tar stream and writes a gzip tar of the same
members, in the same order and with the same headers (mode, mtime, uid/gid,
names, link targets, type), except the top-level member named ``name``, with or
without a leading ``./``. It returns that member's bytes. The deferred
migration in `optio_claudecode.migrations` uses it to take the conversation
buffer file out of workdir snapshot blobs written before the fix
(docs/2026-10-11-conversation-buffer-encryption-design.md, section 6).

Memory stays bounded for any archive size: `tarfile` runs in stream mode
(``r|`` in, ``w|`` out) on a worker thread, behind our own gunzip and level-1
gzip. Bounded queues feed it and drain it, so only a few MiB are in flight.
Only the stripped member is held whole.

The caller deletes the original once the copy is stored, so the input is
checked in full and anything short of an intact archive raises ArchiveError:
- the gzip stream must end properly in every member, with matching CRC and
  length (tarfile's own ``r|gz`` checks neither);
- the only end of the archive accepted is an all-zero header block, followed
  by nothing but zero bytes (tarfile alone takes a bad or truncated header
  after the first member for the end);
- every member must be one that copies exactly: no unknown or sparse types,
  and no data on a non-regular member.
"""

from __future__ import annotations

import asyncio
import tarfile
import threading
import zlib
from typing import AsyncIterator, Awaitable, Callable


class ArchiveError(Exception):
    """The input is not an intact gzip tar (see the module docstring), or holds
    a member that cannot be copied exactly."""


# Queue depth on each side. Input items are the caller's chunks (GridFS reads
# are 1 MiB); output items are _OUT_BATCH-sized.
_QUEUE_DEPTH = 4
_OUT_BATCH = 1 << 20
# How often a worker blocked on a queue checks for an abort.
_ABORT_POLL_S = 0.25
# The capture compresses with `pigz -1` / `gzip -1`. tarfile's `w|gz` only
# takes a compresslevel from Python 3.12, and its default is 9.
_GZIP_LEVEL = 1

# Read size when checking the bytes after the end-of-archive block.
_DRAIN_BLOCK = 64 * 1024
# Most compressed input handed to the decompressor per call.
_FEED = 64 * 1024

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


class _GunzipQueueReader:
    """Worker side: a read-only file object of the gunzipped input queue.

    Handles multi-member gzip. zlib checks each member's CRC and length;
    input that ends inside a member raises ArchiveError. The decompressor is
    fed at most _FEED bytes at a time: what it leaves unconsumed is copied on
    every call, so feeding a whole 1 MiB chunk would be quadratic on very
    compressible data."""

    def __init__(self, bridge: _Bridge) -> None:
        self._bridge = bridge
        self._gunzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._chunk = memoryview(b"")  # input not fed to the decompressor yet
        self._tail = b""  # fed but left unconsumed (at most _FEED bytes)
        self._src_eof = False

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = _OUT_BATCH
        while True:
            if not self._tail and self._chunk:
                self._tail = bytes(self._chunk[:_FEED])
                self._chunk = self._chunk[_FEED:]
            if self._tail:
                if self._gunzip.eof:  # bytes after a complete member: the next one
                    self._gunzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
                out = self._gunzip.decompress(self._tail, n)
                self._tail = (
                    self._gunzip.unused_data if self._gunzip.eof
                    else self._gunzip.unconsumed_tail
                )
                if out:
                    return out
                continue
            if self._src_eof:
                if not self._gunzip.eof:
                    raise ArchiveError("gzip stream ends inside a member")
                return b""
            item = self._bridge.call(self._bridge.inq.get())
            if item is _EOF:
                self._src_eof = True
            else:
                self._chunk = memoryview(item)


class _StrictTarInfo(tarfile.TarInfo):
    """A header that does not parse ends the archive only when it is the
    all-zero end-of-archive block; any other (bad checksum, truncated, missing)
    raises, where `TarFile.next` would silently stop."""

    @classmethod
    def fromtarfile(cls, tarfile_obj):
        try:
            return super().fromtarfile(tarfile_obj)
        except tarfile.EOFHeaderError:
            raise
        except tarfile.HeaderError as exc:
            raise ArchiveError(f"bad tar header: {exc}") from exc


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
    reader = _GunzipQueueReader(bridge)
    writer = _QueueGzipWriter(bridge)
    stripped = None
    try:
        with tarfile.open(fileobj=reader, mode="r|", tarinfo=_StrictTarInfo) as tin, \
                tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tout:
            while (member := tin.next()) is not None:
                # TarFile keeps a TarInfo per member (`addfile` always; `next`
                # in stream mode before Python 3.13). Drop them so memory does
                # not grow with the member count.
                tin.members.clear()
                _check_copyable(member)
                if _bare(member.name) == name:
                    if member.isreg():
                        stripped = tin.extractfile(member).read()
                    continue
                tout.addfile(member, tin.extractfile(member) if member.isreg() else None)
                tout.members.clear()
            # After the end-of-archive block: only zero padding, then the end
            # of the gzip stream (the reader raises if it ends early).
            while block := tin.fileobj.read(_DRAIN_BLOCK):
                if block.strip(b"\0"):
                    raise ArchiveError("data after the end-of-archive marker")
    except (_Aborted, ArchiveError):
        raise
    except Exception as exc:
        # The worker does no I/O except through the bridge (which raises only
        # _Aborted), so anything else comes from the archive content: TarError,
        # zlib.error, EOFError, OSError (tarfile reports a truncated member as
        # OSError("unexpected end of data")), and ValueError (e.g. a header
        # field the PAX copy cannot hold: "overflow in number field").
        raise ArchiveError(f"unreadable gzip tar: {type(exc).__name__}: {exc}") from exc
    writer.finish()
    bridge.call(bridge.outq.put(_END))
    return stripped


def _check_copyable(member: tarfile.TarInfo) -> None:
    """`addfile` copies data only for regular members, and writes a sparse
    member's expanded data under its sparse header; refuse what it would
    corrupt."""
    if member.type not in tarfile.SUPPORTED_TYPES or member.issparse():
        raise ArchiveError(f"member {member.name!r}: type {member.type!r} is not copied")
    if not member.isreg() and member.size:
        raise ArchiveError(
            f"member {member.name!r}: type {member.type!r} with {member.size} data bytes",
        )


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

    ``src`` is read to its end and closed (``aclose``) when it is an async
    generator. Raises ArchiveError unless the input is an intact gzip tar of
    copyable members (module docstring); an error from ``src`` or ``sink``
    propagates as is. On any error the output is incomplete and should be
    discarded.
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
        # Normal end: everything is done (the feeder too, as the worker read
        # the input to its end). Error or cancellation: stop everything; the
        # worker notices the abort within _ABORT_POLL_S.
        bridge.abort.set()
        feeder.cancel()
        drainer.cancel()
        await asyncio.gather(worker, feeder, drainer, return_exceptions=True)
