"""Stable, per-task filesystem layout helpers.

The resume feature needs a `task_dir` per process_id that survives across
runs (on the same host). Previously the executor created a fresh tmpdir
every launch; now we root everything at an env-overridable per-process
location.
"""

import os
import re


_SAFE_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate(process_id: str) -> None:
    if not _SAFE_RE.match(process_id):
        raise ValueError(
            f"process_id {process_id!r} contains characters that are unsafe "
            "as a filesystem path segment; expected [A-Za-z0-9._-] only"
        )


def local_task_dir(process_id: str) -> str:
    """Return the absolute local task directory for a process_id.

    Resolution order:
      1. `$OPTIO_OPENCODE_TASK_ROOT/<process_id>`
      2. `$XDG_DATA_HOME/optio-opencode/<process_id>`
      3. `$HOME/.local/share/optio-opencode/<process_id>`
    """
    _validate(process_id)
    root = os.environ.get("OPTIO_OPENCODE_TASK_ROOT")
    if root:
        return os.path.join(root, process_id)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "optio-opencode", process_id)
    home = os.environ.get("HOME", os.path.expanduser("~"))
    return os.path.join(home, ".local", "share", "optio-opencode", process_id)


def remote_task_dir(process_id: str) -> str:
    """Return the per-task directory on a remote host.

    Resolution order:
      1. `$OPTIO_OPENCODE_REMOTE_TASK_ROOT/<process_id>`
      2. `/tmp/optio-opencode/<process_id>`
    """
    _validate(process_id)
    root = os.environ.get("OPTIO_OPENCODE_REMOTE_TASK_ROOT", "/tmp/optio-opencode")
    return f"{root}/{process_id}"
