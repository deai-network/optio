"""Download a URL to a file on a host, as an optio child task.

Public surface:
  - DownloadFailed: exception raised by the child's execute on curl failure.
  - create_download_task: factory returning a TaskInstance.

See docs/2026-05-12-optio-host-download-design.md for the full design.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from collections import deque
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
        basename = os.path.basename(target) or target
        ctx.report_progress(None, f"Downloading {basename}")

        cmd = _build_curl_cmd(url=url, target=target)
        total = {"value": 0}
        received = {"value": 0}
        stderr_tail: deque = deque()
        cancelled = False

        def _on_length(n: int) -> None:
            total["value"] = n

        def _on_recv(n: int) -> None:
            received["value"] += n
            if total["value"] > 0:
                pct = min(100.0, received["value"] * 100.0 / total["value"])
                ctx.report_progress(pct, None)

        if host is None:
            # "exec" so the shell replaces itself with stdbuf/curl — that
            # way SIGTERM from proc.terminate() reaches the curl process
            # directly rather than being absorbed by an intermediate /bin/sh.
            proc = await asyncio.create_subprocess_exec(
                "sh", "-c", "exec " + cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert proc.stdout is not None and proc.stderr is not None

            async def _stdout_task() -> None:
                await _drain_stdout_trace(
                    proc.stdout,
                    on_length=_on_length,
                    on_recv=_on_recv,
                    should_continue=ctx.should_continue,
                )

            async def _stderr_task() -> None:
                await _drain_stderr_tail(proc.stderr, stderr_tail)

            async def _cancel_watcher() -> None:
                nonlocal cancelled
                while True:
                    if proc.returncode is not None:
                        return
                    if not ctx.should_continue():
                        cancelled = True
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                        return
                    await asyncio.sleep(0.05)

            stdout_t = asyncio.create_task(_stdout_task())
            stderr_t = asyncio.create_task(_stderr_task())
            watcher_t = asyncio.create_task(_cancel_watcher())
            exit_code = await proc.wait()
            await asyncio.gather(stdout_t, stderr_t, watcher_t, return_exceptions=True)
        else:
            raise NotImplementedError("host branch not yet implemented")

        if cancelled:
            if cleanup_on_fail:
                await _maybe_remove(host, target)
            return
        if exit_code != 0:
            if cleanup_on_fail:
                await _maybe_remove(host, target)
            stderr_text = b"".join(stderr_tail).decode("utf-8", errors="replace")
            raise DownloadFailed(
                url=url, target=target,
                exit_code=exit_code, stderr_tail=stderr_text,
            )

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        cancellable=True,
        supports_resume=False,
    )


def _build_curl_cmd(*, url: str, target: str) -> str:
    """Build the shell command string that runs curl with progress trace.

    Output stream layout when the command runs:
      - stdout: ``--trace-ascii -`` protocol trace lines.
      - stderr: curl's own error messages.
      - the response body is written to ``target`` directly (``-o``).

    ``stdbuf -oL`` is prefixed when available so trace lines flush
    promptly. Absent stdbuf, the parser still works (just chunkier).
    """
    parts = [
        "curl",
        "--trace-ascii", "-",
        "-s",
        "-S",
        "-f",
        "-L",
        "-o", shlex.quote(target),
        shlex.quote(url),
    ]
    cmd = " ".join(parts)
    if shutil.which("stdbuf"):
        cmd = "stdbuf -oL " + cmd
    return cmd


def _parse_trace_line(raw: bytes) -> tuple[str, int] | None:
    """Parse a single curl ``--trace-ascii -`` output line.

    Returns:
      ('length', N) on a content-length header line.
      ('recv', N) on a "<= Recv data, N bytes" line.
      None for any other line.

    The matcher is lowercase + prefix-based to tolerate header-name case
    variation and curl version drift on incidental fields.
    """
    if not raw:
        return None
    line = raw.decode("utf-8", errors="replace").strip().lower()
    cl_prefix = "0000: content-length:"
    if line.startswith(cl_prefix):
        value = line[len(cl_prefix):].strip()
        try:
            return ("length", int(value))
        except ValueError:
            return None
    if line.startswith("<= recv data,"):
        parts = line.split()
        if len(parts) >= 4:
            try:
                return ("recv", int(parts[3]))
            except ValueError:
                return None
    return None


_STDERR_TAIL_CAP = 1024


async def _readline(stream) -> bytes:
    """Read one newline-terminated chunk.

    Works with both ``asyncio.StreamReader`` (has ``.readline``) and a
    bare async-iterator of bytes (e.g. ``ProcessHandle.stdout``). For the
    iterator case, accumulate bytes until a newline appears or EOF.
    """
    if hasattr(stream, "readline"):
        return await stream.readline()
    buf = bytearray()
    try:
        async for chunk in stream:
            buf.extend(chunk)
            if b"\n" in chunk:
                break
    except StopAsyncIteration:
        pass
    return bytes(buf)


async def _drain_stdout_trace(
    stream,
    *,
    on_length,
    on_recv,
    should_continue,
) -> None:
    """Read ``stream`` line by line; dispatch parsed trace events."""
    while True:
        if not should_continue():
            return
        line = await _readline(stream)
        if not line:
            return
        parsed = _parse_trace_line(line)
        if parsed is None:
            continue
        kind, value = parsed
        if kind == "length":
            on_length(value)
        elif kind == "recv":
            on_recv(value)


async def _drain_stderr_tail(stream, tail: deque) -> None:
    """Read ``stream`` into ``tail`` (a deque of bytes), bounded by cap."""
    while True:
        if hasattr(stream, "read"):
            chunk = await stream.read(4096)
            if not chunk:
                return
        else:
            try:
                chunk = await stream.__anext__()
            except StopAsyncIteration:
                return
        tail.append(chunk)
        while sum(len(c) for c in tail) > _STDERR_TAIL_CAP and len(tail) > 1:
            tail.popleft()


async def _maybe_remove(host, target: str) -> None:
    """Best-effort cleanup of the target file; errors swallowed."""
    try:
        if host is None:
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
        else:
            await host.remove_file(target)
    except Exception:
        pass
