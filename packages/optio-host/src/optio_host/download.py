"""Download a URL to a file on a host, as an optio child task.

Public surface:
  - DownloadFailed: exception raised by the child's execute on curl failure.
  - create_download_task: factory returning a TaskInstance.

See docs/2026-05-12-optio-host-download-design.md for the full design.
"""

from __future__ import annotations


class DownloadFailed(Exception):
    """Raised when a download child task's curl invocation exits non-zero.

    Carries enough structured detail to diagnose:
      - url: the URL that was being downloaded
      - target: the absolute path curl was writing to (on the host)
      - exit_code: curl's exit code (see curl(1) EXIT CODES)
      - stderr_tail: up to ~1 KB of curl's stderr (the most recent bytes)

    Note: when this exception propagates out of a child task body, the
    optio-core executor converts it to ``str(self)`` in ``status.error``
    and the parent's ``run_child`` re-raises as a plain ``RuntimeError``.
    See /tmp/optio-child-failure-problem.md for the cross-cutting fix shape.
    """

    def __init__(self, *, url: str, target: str, exit_code: int, stderr_tail: str) -> None:
        self.url = url
        self.target = target
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail
        super().__init__(
            f"download failed (exit {exit_code}): {url} -> {target}\n"
            f"stderr: {stderr_tail[:200]}"
        )
