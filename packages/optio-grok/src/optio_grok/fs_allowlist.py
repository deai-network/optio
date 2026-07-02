"""Builder for grok's native sandbox profile (Stage 8 filesystem isolation).

optio-grok confines the agent using grok's OWN kernel-level sandbox
(``--sandbox``, Landlock on Linux) rather than porting optio-claudecode's
claustrum. The sandbox is applied to the entire grok process at startup, so
every tool and spawned subprocess (``bash``, ``grep``, subagents) inherits the
restriction automatically.

We plant a CUSTOM ``[profiles.optio]`` profile (``extends = "strict"``) under
the per-task ``GROK_HOME`` and launch with ``--sandbox optio``. This is
deliberate: grok's BUILT-IN profiles fail-OPEN (they log a warning and run
unconfined if the kernel can't apply them), whereas an explicitly-requested
CUSTOM profile fails-CLOSED — grok refuses to start rather than run exposed.
So a custom profile is the only fail-closed option.

No ``deny`` list is emitted: a non-empty ``deny`` is the sole feature that
requires ``bubblewrap`` on Linux. Omitting it keeps the profile Landlock-only,
matching the deployment optio-claudecode already targets (no bwrap install).
"""

from __future__ import annotations

from optio_grok.types import AllowedDir


# Baseline writable roots granted to every task on top of ``strict`` (which
# already grants CWD + ~/.grok/ + temp dirs). Listing the workdir + temp dirs
# explicitly keeps the profile self-describing and independent of grok's CWD
# resolution.
_BASELINE_READ_WRITE = ("/tmp", "/var/tmp")


def _expand_home(path: str, host_home: str) -> str:
    """Expand a leading ``~/`` against the REAL host home.

    The grok process runs under an isolated ``$HOME`` (``<workdir>/home``), so
    a ``~/`` grant cannot rely on grok's own shell expansion — it must be
    resolved against the operator's real home here, at profile-build time.
    """
    home = host_home.rstrip("/")
    if path == "~":
        return home
    if path.startswith("~/"):
        return f"{home}/{path[2:]}"
    return path


def _toml_str_array(paths: list[str]) -> str:
    """Render a list of paths as a TOML string array (basic strings)."""
    inner = ", ".join(f'"{p}"' for p in paths)
    return f"[{inner}]"


def build_sandbox_toml(
    *,
    workdir: str,
    extra_allowed_dirs: "list[AllowedDir] | None",
    host_home: str,
) -> str:
    """Return the ``[profiles.optio]`` sandbox TOML for one task.

    The profile ``extends`` the built-in ``strict`` base, grants read-write to
    the task ``workdir`` + temp dirs (plus any ``rw`` extras), and read-only to
    the ``ro`` extras. ``~/`` in an extra grant expands against ``host_home``.
    No ``deny`` list is emitted (Landlock-only, no bubblewrap).
    """
    workdir_clean = workdir.rstrip("/")

    read_write: list[str] = [workdir_clean, *_BASELINE_READ_WRITE]
    read_only: list[str] = []
    for ad in extra_allowed_dirs or []:
        target = _expand_home(ad.path, host_home)
        if ad.mode == "rw":
            read_write.append(target)
        else:
            read_only.append(target)

    lines = [
        "[profiles.optio]",
        '# Fail-closed custom profile planted by optio-grok (Stage 8).',
        '# extends the built-in "strict" base; Landlock-only (no deny list).',
        'extends = "strict"',
        f"read_write = {_toml_str_array(read_write)}",
        f"read_only = {_toml_str_array(read_only)}",
    ]
    return "\n".join(lines) + "\n"
