"""Claudecode-specific actions over a generic Host.

Free functions; each takes a Host or HookContext and uses only generic
primitives (run_command, resolve_host_home, etc.). No isinstance branches.
"""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from optio_host import HookContextProtocol, Host
    from optio_host.host import ProcessHandle


_DEFAULT_INSTALL_SUBDIR = ".local/bin"

_CLAUDE_INSTALL_URL = "https://claude.ai/install.sh"

# Pinned ttyd version. Update with care; the URL pattern below is
# tsl0922/ttyd's release-asset convention as of 1.7.x.
_TTYD_VERSION = "1.7.7"
_TTYD_RELEASE_BASE = (
    f"https://github.com/tsl0922/ttyd/releases/download/{_TTYD_VERSION}"
)


async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Return ``install_dir`` if given, else ``<host_home>/<DEFAULT_INSTALL_SUBDIR>``."""
    if install_dir is not None:
        return install_dir
    host_home = await host.resolve_host_home()
    return f"{host_home}/{_DEFAULT_INSTALL_SUBDIR}"


async def _claude_present(host: "Host", claude_path: str) -> bool:
    """Return True iff ``claude_path`` is an executable file on the host
    that produces version output when invoked with --version."""
    cmd = f"[ -x {shlex.quote(claude_path)} ] && {shlex.quote(claude_path)} --version"
    result = await host.run_command(cmd)
    return result.exit_code == 0 and "Claude Code" in result.stdout


async def ensure_claude_installed(
    hook_ctx: "HookContextProtocol",
    *,
    install_if_missing: bool = True,
    install_dir: str | None = None,
) -> str:
    """Ensure the ``claude`` binary is present on the host behind ``hook_ctx``.

    The framework looks for a symlink at ``<install_dir>/claude``. When
    missing and ``install_if_missing=True``, it runs the vendor install
    script (``curl -fsSL https://claude.ai/install.sh | bash``) on the
    host. The script downloads + checksum-verifies + places the native
    binary under ``~/.local/share/claude/versions/<v>/`` and creates a
    symlink at ``~/.local/bin/claude``. The framework re-checks for the
    symlink after the install runs.

    Returns the absolute path of the ``claude`` symlink on the host.

    Raises RuntimeError when the binary is absent and either
    ``install_if_missing=False`` or the install fails.
    """
    host = hook_ctx._host
    resolved_install_dir = await _resolve_install_dir(host, install_dir)
    claude_path = f"{resolved_install_dir}/claude"

    hook_ctx.report_progress(None, "Checking claude installation…")
    if await _claude_present(host, claude_path):
        return claude_path

    if not install_if_missing:
        raise RuntimeError(
            f"claude not present at {claude_path!r} on host and "
            f"install_if_missing=False; nothing to do."
        )

    hook_ctx.report_progress(None, "Installing claude (vendor install.sh)…")
    install_cmd = f"curl -fsSL {shlex.quote(_CLAUDE_INSTALL_URL)} | bash"
    result = await host.run_command(install_cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"claude install failed on host (exit {result.exit_code}): "
            f"{result.stderr.strip()[:300]}"
        )

    if not await _claude_present(host, claude_path):
        raise RuntimeError(
            f"claude install reported success but {claude_path!r} is still "
            f"not executable on the host. Inspect the host's "
            f"~/.local/bin and ~/.local/share/claude/versions for diagnostics."
        )
    return claude_path
