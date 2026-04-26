"""HookContext: ProcessContext + host primitives for opencode-task hooks."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int


class HostCommandError(Exception):
    def __init__(
        self,
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"command failed (exit {exit_code}): {command!r}\n"
            f"stderr: {stderr[:200]}"
        )


def _resolve_target_path(path: str, workdir: str, host_home: str) -> str:
    """Resolve a user-supplied path to an absolute host path.

    Three forms:
      - starts with `/` → absolute, used as-is
      - `~` or starts with `~/` → home-relative, expand once
      - otherwise → workdir-relative; reject `..` and any escape

    workdir and host_home must both be absolute paths.
    """
    if not path:
        raise ValueError("path must not be empty")
    if path.startswith("/"):
        return path
    if path == "~":
        return host_home
    if path.startswith("~/"):
        return host_home + "/" + path[2:]
    # workdir-relative
    if ".." in path.split("/"):
        raise ValueError(f"workdir-relative path may not contain '..': {path!r}")
    resolved = os.path.normpath(os.path.join(workdir, path))
    workdir_norm = os.path.normpath(workdir).rstrip("/")
    if resolved != workdir_norm and not resolved.startswith(workdir_norm + "/"):
        raise ValueError(f"workdir-relative path escapes workdir: {path!r}")
    return resolved
