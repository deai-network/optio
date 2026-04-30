"""Stable, per-task filesystem layout helpers.

The resume feature needs a `task_dir` per process_id that survives across
runs (on the same host). The path layout is per-consumer (each task type
has its own root directory segment) so different consumers don't share or
collide on per-process state.

Local resolution order for one (consumer, process_id):

  1. ``$<CONSUMER>_TASK_ROOT/<process_id>``
     where ``<CONSUMER>`` is ``consumer_name.upper().replace("-", "_")``
  2. ``$XDG_DATA_HOME/<consumer_name>/<process_id>``
  3. ``$HOME/.local/share/<consumer_name>/<process_id>``

Remote resolution order:

  1. ``$<CONSUMER>_REMOTE_TASK_ROOT/<process_id>``
  2. ``/tmp/<consumer_name>/<process_id>``

The single ``task_dir(*, ssh, process_id, consumer_name)`` entry point
hides the local-vs-remote distinction from callers — pass the
``SSHConfig | None`` you'd hand to ``make_host(...)``.
"""

import os
import re

from optio_host.types import SSHConfig


_SAFE_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate(process_id: str) -> None:
    if not _SAFE_RE.match(process_id):
        raise ValueError(
            f"process_id {process_id!r} contains characters that are unsafe "
            "as a filesystem path segment; expected [A-Za-z0-9._-] only"
        )


def _env_prefix(consumer_name: str) -> str:
    """Convert ``optio-opencode`` → ``OPTIO_OPENCODE``.

    Used to derive env-var names for the ``_TASK_ROOT`` and
    ``_REMOTE_TASK_ROOT`` overrides.
    """
    return consumer_name.upper().replace("-", "_")


def task_dir(
    *,
    ssh: SSHConfig | None,
    process_id: str,
    consumer_name: str,
) -> str:
    """Return the absolute taskdir for one (consumer, process_id) pair.

    Resolves a local path when ``ssh is None`` and a remote path otherwise.
    """
    _validate(process_id)
    if ssh is None:
        return _local_task_dir(process_id, consumer_name)
    return _remote_task_dir(process_id, consumer_name)


def _local_task_dir(process_id: str, consumer_name: str) -> str:
    env = _env_prefix(consumer_name) + "_TASK_ROOT"
    if root := os.environ.get(env):
        return os.path.join(root, process_id)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, consumer_name, process_id)
    home = os.environ.get("HOME", os.path.expanduser("~"))
    return os.path.join(home, ".local", "share", consumer_name, process_id)


def _remote_task_dir(process_id: str, consumer_name: str) -> str:
    env = _env_prefix(consumer_name) + "_REMOTE_TASK_ROOT"
    root = os.environ.get(env, f"/tmp/{consumer_name}")
    return f"{root}/{process_id}"
