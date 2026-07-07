"""Agent-agnostic file-upload materialization, shared by every engine.

A conversation task registers an upload writer (via ctx.register_upload_writer);
the central clamator ``materialize_upload`` handler resolves it by process id and
calls ``materialize`` below with the GridFS blob bytes. Extracted from the original
claudecode ``_handle_upload``/``_write_upload`` so all engines share one copy.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from optio_agents.context import HookContext

UploadCallback = Callable[["HookContext", str], Awaitable[None]]

_UPLOADS_DIR = "uploads"

_LOG = logging.getLogger("optio_agents.uploads")


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


def upload_url_token(database: str, prefix: str, process_id: str) -> str:
    """The relative widgetData.uploadUrl token every engine advertises.

    The client POSTs uploads to the generic optio-api /api/widget-upload route,
    resolved relative to ``{widgetProxyUrl}`` (=<base>/api/widget/<db>/<prefix>/
    <pid>/): climb to <base>/api/, then descend into the sibling widget-upload
    route with the SAME db/prefix/pid. Relative so a base path prefix or
    non-origin API host is preserved (see resolveUploadUrl). Engines assert this
    exact string, so the format must stay byte-identical.
    """
    return (
        "{widgetProxyUrl}../../../../widget-upload/"
        f"{database}/{prefix}/{process_id}"
    )


async def materialize(host, workdir, filename, data, hook_ctx=None, on_upload=None):
    """Write an uploaded blob into <workdir>/uploads/<name> and fire on_upload.

    Runs in the task's own process (only it holds the live Host, which may be a
    remote SFTP connection). Returns the workdir-relative path. on_upload is
    additive to the System: LLM announce the caller emits separately.
    """
    rel = safe_upload_relpath(filename)
    abs_path = f"{workdir.rstrip('/')}/{rel}"
    await host.put_file_to_host(data, abs_path)
    if on_upload is not None:
        try:
            await on_upload(hook_ctx, rel)
        except Exception:
            _LOG.exception("on_upload callback raised for %s", rel)
    return rel
