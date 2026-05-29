"""Browser-open shims for the agent launch environment, by mode.

There is no real browser on the worker. Each agent decides what should
happen when its child process tries to open one — encoded as a
``BrowserMode``:

- ``ignore``    — install nothing; the real opener (if any) runs.
- ``suppress``  — shadow the openers with silent no-op stubs.
- ``redirect``  — shadow the openers with capture stubs that append a
  ``BROWSER: "<url>"`` marker to ``optio.log`` (surfaced to the operator
  via ``ctx.request_browser_open``).

``prepare_browser_shims`` writes the stubs (if any) under
``<workdir>/bin`` and returns the env additions to merge into the agent
launch env (``BROWSER`` + a ``<workdir>/bin`` PATH prepend), or ``None``
for ``ignore``. Both returned values are absolute, so the stub wins
regardless of HOME isolation or local-vs-SSH.
"""

from __future__ import annotations

import os
from typing import Literal

from optio_host.host import Host


BrowserMode = Literal["ignore", "suppress", "redirect"]

_SHIM_NAMES = ("xdg-open", "gio", "open", "sensible-browser", "www-browser")

_SUPPRESS_BODY = "#!/bin/sh\nexit 0\n"


def _redirect_body(host: Host) -> str:
    # $1 is the URL the opener was invoked with. Quote it so the captured
    # marker is unambiguous even if the URL contains spaces.
    return (
        "#!/bin/sh\n"
        f'printf \'BROWSER: "%s"\\n\' "$1" >> {host.workdir}/optio.log\n'
        "exit 0\n"
    )


async def _write_shims(host: Host, body: str) -> dict[str, str]:
    for name in _SHIM_NAMES:
        await host.write_text(f"bin/{name}", body)
    await host.run_command(f"chmod +x {host.workdir}/bin/*")
    workdir_bin = f"{host.workdir}/bin"
    extra_path = workdir_bin + ":" + os.environ.get(
        "PATH", "/usr/local/bin:/usr/bin:/bin",
    )
    return {"BROWSER": f"{workdir_bin}/xdg-open", "PATH": extra_path}


async def prepare_browser_shims(
    host: Host, browser: BrowserMode,
) -> dict[str, str] | None:
    """Install the browser shims for ``browser`` and return env additions.

    ``ignore`` → no shims, returns ``None``. ``suppress`` → silent no-op
    stubs. ``redirect`` → capture stubs emitting the ``BROWSER:`` marker.
    """
    if browser == "ignore":
        return None
    if browser == "suppress":
        return await _write_shims(host, _SUPPRESS_BODY)
    if browser == "redirect":
        return await _write_shims(host, _redirect_body(host))
    raise ValueError(f"unknown browser mode: {browser!r}")
