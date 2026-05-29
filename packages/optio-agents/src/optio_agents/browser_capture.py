"""Opt-in browser-open capture shims for the agent launch environment.

`enable(host)` writes capture-only shims for the common browser-opener
commands (xdg-open, gio, open, sensible-browser, www-browser) under
``<workdir>/bin``. Each shim appends a ``BROWSER: "<url>"`` line to
``<workdir>/optio.log`` and exits 0 — it never launches a real browser
(there is none on the worker). It returns env additions to merge into
the agent launch env: ``BROWSER`` pointing at the shim and a
``<workdir>/bin`` PATH prepend.

Opt-in (default off) so it never collides with opencode's own browser
*suppression* shims (the two shim sets are never enabled together).

Mirrors the opencode suppression-shim pattern in
``optio_opencode.host_actions`` but captures instead of suppressing.
"""

from __future__ import annotations

import os

from optio_host.host import Host


_SHIM_NAMES = ("xdg-open", "gio", "open", "sensible-browser", "www-browser")


async def enable(host: Host) -> dict[str, str]:
    """Write the capture shims under ``<workdir>/bin`` and return env additions.

    Returns a dict with ``BROWSER`` and ``PATH`` keys to merge into the
    agent launch env (``PATH`` prepends ``<workdir>/bin``).
    """
    # The shim appends `BROWSER: "<first-arg>"` to optio.log and exits 0.
    # $1 is the URL the opener was invoked with. Quote it so the captured
    # marker is unambiguous even if the URL contains spaces.
    shim_body = (
        "#!/bin/sh\n"
        f'printf \'BROWSER: "%s"\\n\' "$1" >> {host.workdir}/optio.log\n'
        "exit 0\n"
    )
    for name in _SHIM_NAMES:
        await host.write_text(f"bin/{name}", shim_body)
    await host.run_command(f"chmod +x {host.workdir}/bin/*")

    workdir_bin = f"{host.workdir}/bin"
    extra_path = workdir_bin + ":" + os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {
        "BROWSER": f"{workdir_bin}/xdg-open",
        "PATH": extra_path,
    }
