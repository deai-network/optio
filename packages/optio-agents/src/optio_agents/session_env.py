"""What an agent session takes from the engine's environment, and what it never does.

The engine may run from a virtualenv (``<venv>/bin/python -m engine.main``);
its PATH then starts with ``<venv>/bin``. An agent session must not inherit
that venv: its ``python3`` would be the engine's venv Python, whose
``pyvenv.cfg`` claustrum cannot (and should not) grant to the session, so
Python dies at startup. Every launch path builds the session PATH from
:func:`session_base_path`, and every agent launch removes
:data:`ENGINE_PYTHON_ENV` through the hosts' ``env_remove``
(:func:`session_env_remove`).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

# The PATH a session gets when nothing else is left.
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"

# The engine's Python setup, removed from every agent session's environment.
ENGINE_PYTHON_ENV: tuple[str, ...] = ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")


def engine_venv_bins() -> list[str]:
    """The engine's own virtualenv bin directories: ``<sys.prefix>/bin`` when
    the engine runs in a venv, and ``$VIRTUAL_ENV/bin`` when that is set."""
    bins = []
    if sys.prefix != sys.base_prefix:
        bins.append(os.path.join(sys.prefix, "bin"))
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        bins.append(os.path.join(venv, "bin"))
    return bins


def _forms(entry: str) -> set[str]:
    return {os.path.normpath(entry), os.path.realpath(entry)}


def session_base_path(path: str | None = None) -> str:
    """``path`` (empty or None: the engine's PATH, else ``DEFAULT_PATH``) with
    every engine virtualenv bin directory removed, the rest in order. An entry
    matches after normalising both sides (``normpath`` and ``realpath``), so a
    trailing slash or a symlinked venv still matches. Never empty: when nothing
    is left the result is ``DEFAULT_PATH``."""
    if not path:
        path = os.environ.get("PATH", DEFAULT_PATH)
    drop: set[str] = set()
    for bin_dir in engine_venv_bins():
        drop |= _forms(bin_dir)
    kept = [e for e in path.split(os.pathsep) if not (e and _forms(e) & drop)]
    return os.pathsep.join(kept) or DEFAULT_PATH


def session_env_remove(patterns: Sequence[str] | None) -> list[str]:
    """The ``env_remove`` of an agent session launch: ``patterns`` (the task's
    ``scrub_env``) followed by :data:`ENGINE_PYTHON_ENV`."""
    return [*(patterns or ()), *ENGINE_PYTHON_ENV]
