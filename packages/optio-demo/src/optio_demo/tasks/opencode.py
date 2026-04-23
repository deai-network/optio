"""Reference demo task for optio-opencode.

Defaults to local mode (per the design spec, Section 10); set the
``OPTIO_OPENCODE_DEMO_SSH_HOST`` environment variable to run the same
task via SSH on a remote host.  Relevant env vars (all optional except
``_HOST``):

- ``OPTIO_OPENCODE_DEMO_SSH_HOST`` — enables remote mode.
- ``OPTIO_OPENCODE_DEMO_SSH_USER`` — default: ``$USER`` on the worker.
- ``OPTIO_OPENCODE_DEMO_SSH_KEY_PATH`` — default: ``~/.ssh/id_ed25519``.
- ``OPTIO_OPENCODE_DEMO_SSH_PORT`` — default: ``22``.

The consumer prompt is the hostname-and-color-and-42 prompt described
in the spec.  The deliverable callback surfaces the file contents back
into the optio log channel so the human can visually confirm
round-trip success.
"""

import os

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_opencode import OpencodeTaskConfig, SSHConfig, create_opencode_task


CONSUMER_PROMPT = (
    "Tell me the hostname of the system you are running on. "
    "Then ask the human about their favorite color, then ship a "
    "deliverable containing the number 42 and the designated color. "
    "Then signal completion by appending a `DONE` line to the "
    "`./optio.log` file (writing `DONE` in the chat has no effect — "
    "it must go into that file)."
)


def _resolve_ssh_config() -> SSHConfig | None:
    """Build an SSHConfig from env vars, or None for local mode."""
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


def _make_on_deliverable(ctx: ProcessContext):
    async def _cb(path: str, text: str) -> None:
        ctx.report_progress(
            None,
            f"deliverable {os.path.basename(path)}: {text[:200]}",
        )
    return _cb


def get_tasks() -> list[TaskInstance]:
    async def _execute(ctx: ProcessContext) -> None:
        config = OpencodeTaskConfig(
            consumer_instructions=CONSUMER_PROMPT,
            opencode_config={},
            # Resolved at execution time so env-var changes between
            # worker restarts are picked up without reinstalling the task.
            ssh=_resolve_ssh_config(),
            on_deliverable=_make_on_deliverable(ctx),
        )
        inner = create_opencode_task(
            process_id="opencode-demo-inner",
            name="opencode demo inner",
            config=config,
        )
        await inner.execute(ctx)

    return [
        TaskInstance(
            execute=_execute,
            process_id="opencode-demo",
            name="Opencode demo",
            description=(
                "Opencode session asking for a color and shipping a "
                "deliverable. Set OPTIO_OPENCODE_DEMO_SSH_HOST to run "
                "remotely; otherwise runs locally."
            ),
            ui_widget="iframe",
        )
    ]
