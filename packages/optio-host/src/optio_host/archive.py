"""Tar+gzip helpers for persisting and restoring a workdir.

`yield_workdir_archive` is an async generator that yields gzipped tar
chunks of a directory's contents. `consume_workdir_archive` consumes such
a stream, wiping the destination first and extracting in. Both run their
synchronous tarfile work in a thread executor so the event loop stays
responsive.

`consume_workdir_archive` backs `LocalHost.restore_workdir`.
`yield_workdir_archive` is only `LocalHost.archive_workdir`'s fallback for a
host without `tar` or `bash`; normally both hosts stream `tar | pigz -1` (or
`gzip -1`), RemoteHost over SSH and LocalHost as a local subprocess. That
fallback makes its capture-cap decision with `fallback_regenerable_excludes`,
which measures with a Python walk (`measure_workdir`) instead of `du`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import io
import os
import shutil
import stat
import tarfile
import time
from typing import AsyncIterator, Iterable

from optio_host.capture_cap import LIST_MAX_BYTES, REGENERABLE_LIST, over_cap_excludes


DEFAULT_WORKDIR_EXCLUDES: list[str] = [
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "*.pyc",
    ".DS_Store",
]

_CHUNK_SIZE = 1 << 20  # 1 MiB

# How long `measure_workdir` may walk before giving up; the fallback's
# counterpart of `du`'s `timeout`. Read at call time, so tests can raise it.
WALK_BUDGET_S = 1.0


def _excluded(relpath: str, patterns: list[str]) -> bool:
    """Return True if `relpath` matches any pattern in `patterns`.

    Matching is applied at each path segment (so `.git` in `patterns`
    excludes `./a/b/.git/…`) as well as against the full relative path
    (so `*.pyc` matches `mod.pyc` but also `a/b/mod.pyc`).
    """
    parts = relpath.split(os.sep)
    for pat in patterns:
        if fnmatch.fnmatch(relpath, pat):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _anchored_excluded(relpath: str, anchored: list[str]) -> bool:
    """Return True if `relpath` is one of `anchored`'s entries, or lies under
    one, taken as a literal path from the root (no globbing, no per-segment
    matching): `node_modules` excludes `./node_modules` and everything under
    it, but not `./pkg/node_modules`.
    """
    for entry in anchored:
        if relpath == entry or relpath.startswith(entry + os.sep):
            return True
    return False


def _build_archive_bytes(
    root: str, patterns: list[str], anchored: list[str] = (),
) -> bytes:
    """Build the entire tar.gz in memory and return it as bytes.

    Level 1, not tarfile's default 9: level 9 is ~7x slower for ~10% smaller
    output, and a snapshot capture runs against the cancel grace.

    `patterns` keep the existing match-anywhere glob semantics. `anchored`
    entries are literal paths from `root`: an entry excludes itself and,
    when it is a directory, everything under it (see `_anchored_excluded`).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tar:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [
                d for d in dirnames
                if not _excluded(os.path.join(rel_dir, d) if rel_dir != "." else d, patterns)
                and not _anchored_excluded(
                    os.path.join(rel_dir, d) if rel_dir != "." else d, anchored,
                )
            ]
            for name in filenames:
                rel = os.path.join(rel_dir, name) if rel_dir != "." else name
                if _excluded(rel, patterns) or _anchored_excluded(rel, anchored):
                    continue
                tar.add(os.path.join(dirpath, name), arcname=rel, recursive=False)
    return buf.getvalue()


def measure_workdir(
    root: str, patterns: list[str], budget_s: float | None = None,
) -> int | None:
    """Size of `root` in bytes as a capture sees it, like `du -sb`.

    Sums the `lstat` sizes of every entry under `root` that `patterns`
    (match-anywhere, as in `_excluded`) do not exclude, without following
    symlinks. Returns `None` once the walk has run for `budget_s` seconds of
    `time.monotonic()` (`None`: `WALK_BUDGET_S`).
    """
    if budget_s is None:
        budget_s = WALK_BUDGET_S
    deadline = time.monotonic() + budget_s
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)

        def rel(name: str) -> str:
            return os.path.join(rel_dir, name) if rel_dir != "." else name

        dirnames[:] = [d for d in dirnames if not _excluded(rel(d), patterns)]
        kept = dirnames + [f for f in filenames if not _excluded(rel(f), patterns)]
        for name in kept:
            if time.monotonic() >= deadline:
                return None
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _read_list(root: str) -> str:
    """At most `LIST_MAX_BYTES` of `root`'s `regenerable.txt`, decoded with
    `errors="replace"`; `""` unless it is a regular file (or a symlink to
    one) that can be read. Opened non-blocking, so a FIFO cannot block it."""
    try:
        fd = os.open(os.path.join(root, REGENERABLE_LIST), os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(LIST_MAX_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        os.close(fd)


def fallback_regenerable_excludes(
    root: str, patterns: list[str], cap_bytes: int,
) -> tuple[list[str], str | None]:
    """`capture_cap.regenerable_excludes` for a host without `tar` or `bash`.

    The same decision and result, measured with `measure_workdir` (running
    out of its budget counts as over the cap) and the list read with Python
    (`_read_list`: bounded, regular files only; anything else counts as an
    empty list). Blocking: run it in an executor.
    """
    if cap_bytes == 0:
        return [], None
    size = measure_workdir(root, patterns)
    if size is not None and size <= cap_bytes:
        return [], None
    return over_cap_excludes(
        size, f"walk timed out after {WALK_BUDGET_S:g} s", cap_bytes, _read_list(root),
    )


async def yield_workdir_archive(
    root: str,
    exclude: Iterable[str] | None = None,
    anchored: Iterable[str] | None = None,
) -> AsyncIterator[bytes]:
    """Yield 1 MiB chunks of the gzipped tar of `root`.

    `exclude` keeps its existing match-anywhere glob semantics (`None` means
    the defaults). `anchored` entries are literal, root-relative paths with
    no globbing; `None` or empty excludes none beyond `exclude`.
    """
    patterns = list(DEFAULT_WORKDIR_EXCLUDES) if exclude is None else list(exclude)
    anchored_list = [] if anchored is None else list(anchored)
    loop = asyncio.get_event_loop()
    blob = await loop.run_in_executor(
        None, _build_archive_bytes, root, patterns, anchored_list,
    )
    for offset in range(0, len(blob), _CHUNK_SIZE):
        yield blob[offset : offset + _CHUNK_SIZE]


def _empty_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        return
    for entry in os.listdir(path):
        full = os.path.join(path, entry)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.unlink(full)
            except OSError:
                pass


def _extract_sync(blob: bytes, dest: str) -> None:
    _empty_dir(dest)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        tar.extractall(dest)


async def consume_workdir_archive(
    stream: AsyncIterator[bytes],
    dest: str,
) -> None:
    """Empty `dest`, then untar the chunked gzipped stream into it."""
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
    blob = b"".join(chunks)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _extract_sync, blob, dest)
