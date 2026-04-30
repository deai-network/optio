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


from typing import Any, AsyncIterator, Awaitable, Callable, Protocol


class HookContext:
    """ProcessContext + host primitives, passed to before/after_execute hooks
    and to on_deliverable callbacks.

    Attributes not defined on this class fall through to the wrapped
    ProcessContext via __getattr__, so consumers can call e.g.
    ``hook_ctx.report_progress(...)`` directly.
    """

    def __init__(self, ctx, host) -> None:
        # Use object.__setattr__ to avoid __getattr__ recursion in __init__.
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_host", host)

    def __getattr__(self, name: str) -> Any:
        # Only called if the attribute isn't found on the instance / class.
        return getattr(self._ctx, name)

    async def run_on_host(
        self,
        command: str,
        *,
        check: bool = True,
        capture_stderr: bool = False,
        cwd: str | None = None,
    ):
        result = await self._host.run_command(command, cwd=cwd)
        if check:
            if result.exit_code != 0:
                raise HostCommandError(
                    command=command,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            return result.stdout + (result.stderr if capture_stderr else "")
        return result

    async def copy_file(
        self,
        source,
        target: str,
        *,
        skip_if_unchanged: bool = False,
    ) -> None:
        from bson import ObjectId  # local import to avoid a hard top-level dep

        host_home = await self._host.resolve_host_home()
        abs_target = _resolve_target_path(target, self._host.workdir, host_home)
        basename = os.path.basename(abs_target) or abs_target
        ctx = self._ctx
        skipped = False

        def _progress_cb(percent, message):
            nonlocal skipped
            if message == "already up to date":
                skipped = True
                ctx.report_progress(None, f"Already up to date: {basename}")
            elif percent is not None:
                ctx.report_progress(percent, None)

        # Resolve blob sources to (iterator, expected_sha) pair.
        expected_sha: str | None = None
        if isinstance(source, ObjectId):
            payload = await self._read_blob_bytes(source)
            if skip_if_unchanged:
                import hashlib
                expected_sha = hashlib.sha256(payload).hexdigest()

            async def _gen():
                # Yield the payload as one (or a few) chunks. For very large
                # blobs we could stream via load_blob, but reading-then-iterating
                # is simpler and correct.
                yield payload

            source = _gen()

        if skip_if_unchanged:
            ctx.report_progress(None, f"Verifying {basename}...")
        else:
            ctx.report_progress(None, f"Copying {basename}...")

        await self._host.put_file_to_host(
            source,
            abs_target,
            expected_sha256=expected_sha,
            skip_if_unchanged=skip_if_unchanged,
            progress_cb=_progress_cb,
        )

        if skip_if_unchanged and not skipped:
            ctx.report_progress(None, f"Copying {basename}...")

    async def _read_blob_bytes(self, file_id) -> bytes:
        async with self._ctx.load_blob(file_id) as reader:
            return await reader.read()

    async def read_from_host(self, path: str) -> bytes:
        host_home = await self._host.resolve_host_home()
        abs_path = _resolve_target_path(path, self._host.workdir, host_home)
        basename = os.path.basename(abs_path) or abs_path
        self._ctx.report_progress(None, f"Reading {basename}...")

        def _progress_cb(percent, message):
            if percent is not None:
                self._ctx.report_progress(percent, None)

        return await self._host.fetch_bytes_from_host(
            abs_path, progress_cb=_progress_cb,
        )

    async def read_text_from_host(self, path: str) -> str:
        data = await self.read_from_host(path)
        return data.decode("utf-8")


class HookContextProtocol(Protocol):
    """Type-hint surface for hook authors who want IDE discoverability.

    Subset of ProcessContext + the four new methods.
    """

    process_id: str

    @property
    def params(self) -> dict: ...

    @property
    def metadata(self) -> dict: ...

    def report_progress(self, percent: float | None, message: str | None = None) -> None: ...
    def should_continue(self) -> bool: ...
    async def copy_file(
        self,
        source,
        target: str,
        *,
        skip_if_unchanged: bool = False,
    ) -> None: ...
    async def run_on_host(
        self,
        command: str,
        *,
        check: bool = True,
        capture_stderr: bool = False,
        cwd: str | None = None,
    ) -> "str | RunResult": ...
    async def read_from_host(self, path: str) -> bytes: ...
    async def read_text_from_host(self, path: str) -> str: ...


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
