"""Tar+gzip helpers for persisting and restoring a workdir.

`yield_workdir_archive` is an async generator that yields gzipped tar
chunks of a directory's contents. `consume_workdir_archive` consumes such
a stream, wiping the destination first and extracting in. Both run their
synchronous tarfile work in a thread executor so the event loop stays
responsive.

These helpers back `LocalHost.archive_workdir` and `LocalHost.restore_workdir`.
`RemoteHost` does not use them — it shells out to `tar` over SSH instead.
"""

from __future__ import annotations

import asyncio
import fnmatch
import io
import os
import shutil
import tarfile
from typing import AsyncIterator, Iterable


DEFAULT_WORKDIR_EXCLUDES: list[str] = [
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "*.pyc",
    ".DS_Store",
]

_CHUNK_SIZE = 1 << 20  # 1 MiB


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


def _build_archive_bytes(root: str, patterns: list[str]) -> bytes:
    """Build the entire tar.gz in memory and return it as bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [
                d for d in dirnames
                if not _excluded(os.path.join(rel_dir, d) if rel_dir != "." else d, patterns)
            ]
            for name in filenames:
                rel = os.path.join(rel_dir, name) if rel_dir != "." else name
                if _excluded(rel, patterns):
                    continue
                tar.add(os.path.join(dirpath, name), arcname=rel, recursive=False)
    return buf.getvalue()


async def yield_workdir_archive(
    root: str,
    exclude: Iterable[str] | None = None,
) -> AsyncIterator[bytes]:
    """Yield 1 MiB chunks of the gzipped tar of `root`."""
    patterns = list(DEFAULT_WORKDIR_EXCLUDES) if exclude is None else list(exclude)
    loop = asyncio.get_event_loop()
    blob = await loop.run_in_executor(None, _build_archive_bytes, root, patterns)
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
