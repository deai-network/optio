"""Download a URL to a file on a host, as an optio child task.

Public surface:
  - DownloadFailed: exception raised by the child's execute on curl failure.
  - create_download_task: factory returning a TaskInstance.

See docs/2026-05-12-optio-host-download-design.md for the full design.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from optio_core.models import TaskInstance

if TYPE_CHECKING:
    from optio_host.host import Host


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


def create_download_task(
    process_id: str,
    name: str,
    *,
    url: str,
    target: str,
    host: "Host | None" = None,
    description: str | None = None,
    cleanup_on_fail: bool = True,
) -> TaskInstance:
    """Build a TaskInstance that downloads ``url`` to ``target`` on ``host``.

    See module docstring and the design document for the full contract.

    Args:
      process_id: child process_id for the resulting TaskInstance.
      name: child process name (typically ``"download <basename>"``).
      url: http(s) URL to download.
      target: absolute path the response body is written to. When ``host``
        is given, an absolute path on that host. When ``host`` is None, an
        absolute path on the local filesystem.
      host: when provided, curl runs via ``host.launch_subprocess``. When
        None, curl runs locally via ``asyncio.create_subprocess_exec``.
      description: optional description shown in the UI for the child.
      cleanup_on_fail: when True (default), best-effort delete the target
        file if curl exits non-zero or the child is cancelled. Errors
        during cleanup are swallowed.
    """

    async def _execute(ctx: Any) -> None:
        raise NotImplementedError("download _execute body not implemented yet")

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        cancellable=True,
        supports_resume=False,
    )
