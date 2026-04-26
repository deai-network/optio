"""Reference demo task for optio-opencode.

Defaults to local mode; set the ``OPTIO_OPENCODE_DEMO_SSH_HOST``
environment variable to run the same task via SSH on a remote host.
Relevant env vars (all optional except ``_HOST``):

- ``OPTIO_OPENCODE_DEMO_SSH_HOST`` — enables remote mode.
- ``OPTIO_OPENCODE_DEMO_SSH_USER`` — default: ``$USER`` on the worker.
- ``OPTIO_OPENCODE_DEMO_SSH_KEY_PATH`` — default: ``~/.ssh/id_ed25519``.
- ``OPTIO_OPENCODE_DEMO_SSH_PORT`` — default: ``22``.

The before_execute hook runs ``whoami`` on the host before opencode
launches and reports the result via ``report_progress``. The
on_deliverable callback prints the deliverable body to the worker's
terminal — additive to the framework's auto-emitted
``"Deliverable: <path>"`` progress message.
"""

from __future__ import annotations

import os

from optio_core.models import TaskInstance
from optio_opencode import (
    HookContext,
    OpencodeTaskConfig,
    SSHConfig,
    create_opencode_task,
)


CONSUMER_PROMPT = (
    "Tell me the hostname of the system you are running on. "
    "Then ask the human about their favorite color, then ship a "
    "deliverable containing the number 42 and the designated color. "
    "Then signal completion by appending a `DONE` line to the "
    "`./optio.log` file (writing `DONE` in the chat has no effect — "
    "it must go into that file)."
)


def _resolve_ssh_config() -> SSHConfig | None:
    host = os.environ.get("OPTIO_OPENCODE_DEMO_SSH_HOST")
    if not host:
        return None
    user = (
        os.environ.get("OPTIO_OPENCODE_DEMO_SSH_USER")
        or os.environ.get("USER")
        or "root"
    )
    key_path = os.environ.get(
        "OPTIO_OPENCODE_DEMO_SSH_KEY_PATH",
        os.path.expanduser("~/.ssh/id_ed25519"),
    )
    port_raw = os.environ.get("OPTIO_OPENCODE_DEMO_SSH_PORT", "22")
    try:
        port = int(port_raw)
    except ValueError:
        raise RuntimeError(
            f"OPTIO_OPENCODE_DEMO_SSH_PORT must be an integer, got {port_raw!r}"
        )
    return SSHConfig(host=host, user=user, key_path=key_path, port=port)


async def _before_execute(hook_ctx: HookContext) -> None:
    out = await hook_ctx.run_on_host("whoami")
    hook_ctx.report_progress(None, f"opencode will run as {out.strip()}")


async def _on_deliverable(
    hook_ctx: HookContext, path: str, text: str,
) -> None:
    print(f"[opencode-demo] deliverable {path}:\n{text}")


def get_tasks() -> list[TaskInstance]:
    return [
        create_opencode_task(
            process_id="opencode-demo",
            name="Opencode demo",
            description=(
                "Opencode session asking for a color and shipping a "
                "deliverable. Runs `whoami` on the host before launching "
                "opencode, and prints any deliverable to the worker terminal. "
                "Set OPTIO_OPENCODE_DEMO_SSH_HOST to run remotely; "
                "otherwise runs locally."
            ),
            config=OpencodeTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                ssh=_resolve_ssh_config(),
                before_execute=_before_execute,
                on_deliverable=_on_deliverable,
            ),
        )
    ]
