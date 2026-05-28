"""Reference demo task for optio-claudecode.

Defaults to local mode; set the ``OPTIO_CLAUDECODE_DEMO_SSH_HOST``
environment variable to run the same task via SSH on a remote host.
Relevant env vars (all optional except ``_HOST``):

- ``OPTIO_CLAUDECODE_DEMO_SSH_HOST`` — enables remote mode.
- ``OPTIO_CLAUDECODE_DEMO_SSH_USER`` — default: ``$USER`` on the worker.
- ``OPTIO_CLAUDECODE_DEMO_SSH_KEY_PATH`` — default: ``~/.ssh/id_ed25519``.
- ``OPTIO_CLAUDECODE_DEMO_SSH_PORT`` — default: ``22``.
- ``ANTHROPIC_API_KEY`` — when set, passed through to the claude process
  via the task's ``env`` config field. Required for any real run because
  optio-claudecode runs claude under HOME-isolation
  (``HOME=<workdir>/home``) — the host user's pre-existing
  ``~/.claude/.credentials.json`` is NOT visible to the agent. Without
  either ``ANTHROPIC_API_KEY`` or ``credentials_json``, claude will
  display "Not logged in" inside the TUI and the demo cannot complete.

Hook walkthrough (mirrors the opencode demo):

- ``before_execute`` runs ``whoami`` on the host (proves the hook fires
  inside the session pipeline) and ships ``context.txt`` into the
  workdir via ``copy_file`` (bytes source). The LLM is instructed to
  read that file, so its presence is observable end-to-end.
- ``on_deliverable`` prints the deliverable body to the worker's
  terminal — additive to the framework's auto-emitted
  ``"Deliverable: <path>"`` progress message.
- ``after_execute`` reads ``./optio.log`` back via
  ``read_text_from_host`` and reports a one-line summary, proving the
  hook fires before workdir teardown.
"""

from __future__ import annotations

import os

from optio_claudecode import (
    ClaudeCodeTaskConfig,
    HookContext,
    SSHConfig,
    create_claudecode_task,
)
from optio_core.models import TaskInstance


CONTEXT_TXT = b"""\
Mission code-name: Project Petunia
Authorized color: turquoise
"""


CONSUMER_PROMPT = (
    "First, read the file `./context.txt` in your working directory. It "
    "contains a mission code-name and an authorized color. Then ask the "
    "human about their favorite color. Ship a deliverable file at "
    "`./deliverables/mission-report.txt` containing the mission "
    "code-name, the authorized color, the human's favorite color, and "
    "the number 42. Then signal completion by appending a `DONE` line "
    "to the `./optio.log` file (writing `DONE` in the chat has no "
    "effect — it must go into that file)."
)


def _resolve_ssh_config() -> SSHConfig | None:
    host = os.environ.get("OPTIO_CLAUDECODE_DEMO_SSH_HOST")
    if not host:
        return None
    user = (
        os.environ.get("OPTIO_CLAUDECODE_DEMO_SSH_USER")
        or os.environ.get("USER")
        or "root"
    )
    key_path = os.environ.get(
        "OPTIO_CLAUDECODE_DEMO_SSH_KEY_PATH",
        os.path.expanduser("~/.ssh/id_ed25519"),
    )
    port_raw = os.environ.get("OPTIO_CLAUDECODE_DEMO_SSH_PORT", "22")
    try:
        port = int(port_raw)
    except ValueError:
        raise RuntimeError(
            f"OPTIO_CLAUDECODE_DEMO_SSH_PORT must be an integer, got {port_raw!r}"
        )
    return SSHConfig(host=host, user=user, key_path=key_path, port=port)


def _resolve_env() -> dict[str, str] | None:
    """Pass-through env to the claude process.

    Forwards ``ANTHROPIC_API_KEY`` when set on the worker so the demo
    can complete without the user also planting a credentials.json. If
    unset, returns None and the demo will surface a "Not logged in"
    state inside the TUI — acceptable for a smoke check of the
    iframe + tunnel plumbing.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return {"ANTHROPIC_API_KEY": api_key}


async def _before_execute(hook_ctx: HookContext) -> None:
    out = await hook_ctx.run_on_host("whoami")
    hook_ctx.report_progress(None, f"claude will run as {out.strip()}")
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


async def _on_deliverable(
    hook_ctx: HookContext, path: str, text: str,
) -> None:
    print(f"[claudecode-demo] deliverable {path}:\n{text}")


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
        create_claudecode_task(
            process_id="claudecode-demo",
            name="Claude Code demo",
            description=(
                "Claude Code session that reads a context file shipped "
                "by before_execute, asks for a favorite color, ships a "
                "deliverable combining both colors and a code-name, "
                "then after_execute reports a session-log summary. Set "
                "OPTIO_CLAUDECODE_DEMO_SSH_HOST to run remotely; "
                "otherwise runs locally. Set ANTHROPIC_API_KEY for the "
                "agent to authenticate (HOME is isolated per-task, so "
                "the host user's ~/.claude/.credentials.json is NOT "
                "inherited)."
            ),
            config=ClaudeCodeTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                env=_resolve_env(),
                # Demo runs autonomously; skip per-tool prompts so the
                # agent isn't blocked waiting for user clicks inside
                # the iframe.
                permission_mode="bypassPermissions",
                ssh=_resolve_ssh_config(),
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=_on_deliverable,
                # Resume support. Crypto hooks left None → plaintext
                # session blob (same shape as the opencode demo). Operators
                # forking the demo for a real deployment supply both hooks
                # pointing at actual crypto. No on_resume_refresh: AGENTS.md
                # is reused verbatim on resume. workdir_exclude left default.
                supports_resume=True,
            ),
        )
    ]
