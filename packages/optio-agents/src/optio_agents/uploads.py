"""Agent-agnostic file-upload materialization, shared by every engine.

A conversation task registers an upload writer (via ctx.register_upload_writer);
the central clamator ``materialize_upload`` handler resolves it by process id and
calls ``materialize`` below with the GridFS blob bytes. Extracted from the original
claudecode ``_handle_upload``/``_write_upload`` so all engines share one copy.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from optio_agents.context import HookContext

UploadCallback = Callable[["HookContext", str], Awaitable[None]]

_UPLOADS_DIR = "uploads"


def safe_upload_relpath(filename: str) -> str:
    """`uploads/<basename>` preserving the human name but confined to uploads/.

    Strips any directory components (client-supplied paths are untrusted) and
    rejects names that resolve to nothing / escape the dir. The human-readable
    characters (spaces, unicode, dots) are kept — only path structure is removed.
    """
    base = os.path.basename(filename.strip().replace("\\", "/").rstrip("/"))
    if not base or base in (".", ".."):
        raise ValueError(f"unsafe upload filename: {filename!r}")
    rel = f"{_UPLOADS_DIR}/{base}"
    # Defense in depth: the normalized path must stay directly under uploads/.
    if os.path.normpath(rel) != rel or "/" in base:
        raise ValueError(f"unsafe upload filename: {filename!r}")
    return rel
