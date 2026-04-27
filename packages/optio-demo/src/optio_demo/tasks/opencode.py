"""Reference demo task for optio-opencode.

Defaults to local mode; set the ``OPTIO_OPENCODE_DEMO_SSH_HOST``
environment variable to run the same task via SSH on a remote host.
Relevant env vars (all optional except ``_HOST``):

- ``OPTIO_OPENCODE_DEMO_SSH_HOST`` — enables remote mode.
- ``OPTIO_OPENCODE_DEMO_SSH_USER`` — default: ``$USER`` on the worker.
- ``OPTIO_OPENCODE_DEMO_SSH_KEY_PATH`` — default: ``~/.ssh/id_ed25519``.
- ``OPTIO_OPENCODE_DEMO_SSH_PORT`` — default: ``22``.

Hook walkthrough:

- ``before_execute`` runs ``whoami`` on the host (proves the hook fires
  inside the session pipeline) and ships ``context.txt`` into the
  workdir via ``copy_file`` (bytes source). The LLM is instructed to
  read that file, so its presence is observable end-to-end.
- ``on_deliverable`` prints the deliverable body to the worker's
  terminal — additive to the framework's auto-emitted
  ``"Deliverable: <path>"`` progress message.
- ``after_execute`` reads ``./optio.log`` back via
  ``read_text_from_host`` and reports a one-line summary, proving the
  hook fires before snapshot capture (the file is still alive).
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


CONTEXT_TXT = b"""\
Mission code-name: Project Petunia
Authorized color: turquoise
"""


CONSUMER_PROMPT = (
    "First, read the file `./context.txt` in your working directory. It "
    "contains a mission code-name and an authorized color. Then ask the "
    "human about their favorite color. Ship a deliverable file containing "
    "the mission code-name, the authorized color, the human's favorite "
    "color, and the number 42. Then signal completion by appending a "
    "`DONE` line to the `./optio.log` file (writing `DONE` in the chat "
    "has no effect — it must go into that file)."
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
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


async def _on_deliverable(
    hook_ctx: HookContext, path: str, text: str,
) -> None:
    print(f"[opencode-demo] deliverable {path}:\n{text}")


async def _after_execute(hook_ctx: HookContext) -> None:
    try:
        log = await hook_ctx.read_text_from_host("optio.log")
    except FileNotFoundError:
        hook_ctx.report_progress(None, "session log: not present")
        return
    lines = log.splitlines()
    counts = {"STATUS": 0, "DELIVERABLE": 0, "DONE": 0, "ERROR": 0}
    for line in lines:
        for keyword in counts:
            if line.startswith(keyword):
                counts[keyword] += 1
                break
    summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
    hook_ctx.report_progress(
        None,
        f"session log: {len(lines)} lines ({summary or 'no keywords'})",
    )


def get_tasks() -> list[TaskInstance]:
    return [
        create_opencode_task(
            process_id="opencode-demo",
            name="Opencode demo",
            description=(
                "Opencode session that reads a context file shipped by "
                "before_execute, asks for a favorite color, ships a "
                "deliverable combining both colors and a code-name, then "
                "after_execute reports a session-log summary. Set "
                "OPTIO_OPENCODE_DEMO_SSH_HOST to run remotely; otherwise "
                "runs locally."
            ),
            config=OpencodeTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                ssh=_resolve_ssh_config(),
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=_on_deliverable,
            ),
        )
    ]
