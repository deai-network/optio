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
from datetime import datetime, timezone

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


DEMO_SEED_COLLECTION_SUFFIX = "_demo_claude_seeds"

SEED_SETUP_PROMPT = (
    "This is a one-time setup session. Use the terminal to log into "
    "Claude Code (run `/login` and follow the prompts) and install any "
    "plugins or MCP servers you want available to demo tasks. When you "
    "are done, STOP this task from the dashboard — your configuration "
    "(credentials, settings, plugins) will be captured as a reusable "
    "seed, and a new 'Claude Code demo' task pinned to that seed will "
    "appear automatically."
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


def _make_on_seed_saved(db, prefix: str, fw):
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]

    async def _on_seed_saved(seed_id: str) -> None:
        # Cosmetic numbering; a concurrent-save race may reuse a number —
        # acceptable, the seedId is the real key.
        count = await coll.count_documents({})
        name = f"Config #{count + 1}"
        await coll.insert_one({
            "seedId": seed_id,
            "name": name,
            "createdAt": datetime.now(timezone.utc),
        })
        # Regenerate the task list so a seed-pinned demo task appears.
        await fw.resync()

    return _on_seed_saved


async def get_tasks(services: dict) -> list[TaskInstance]:
    db = services["db"]
    prefix = services["prefix"]
    fw = services["optio"]
    ssh = _resolve_ssh_config()

    tasks: list[TaskInstance] = [
        # The existing API-key / credentials demo task (unchanged behavior).
        create_claudecode_task(
            process_id="claudecode-demo",
            name="Claude Code demo",
            description=(
                "Claude Code session that reads a context file shipped "
                "by before_execute, asks for a favorite color, ships a "
                "deliverable combining both colors and a code-name, then "
                "after_execute reports a session-log summary. Set "
                "ANTHROPIC_API_KEY for the agent to authenticate."
            ),
            config=ClaudeCodeTaskConfig(
                consumer_instructions=CONSUMER_PROMPT,
                env=_resolve_env(),
                permission_mode="bypassPermissions",
                ssh=ssh,
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=_on_deliverable,
                supports_resume=True,
            ),
        ),
        # The seed setup task: vanilla (no seed_id), on_seed_saved wired.
        create_claudecode_task(
            process_id="claudecode-seed-setup",
            name="Setup Claude Code seed",
            description=(
                "One-time: log into Claude Code and configure plugins, "
                "then stop the task to capture a reusable seed. A new "
                "seed-pinned demo task appears afterward."
            ),
            config=ClaudeCodeTaskConfig(
                consumer_instructions=SEED_SETUP_PROMPT,
                ssh=ssh,
                # Interactive login — no autonomous bypass, no resume.
                supports_resume=False,
                on_seed_saved=_make_on_seed_saved(db, prefix, fw),
            ),
        ),
    ]

    # One seed-pinned demo task per recorded seed.
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]
    async for rec in coll.find({}, projection={"seedId": 1, "name": 1}):
        seed_id = rec["seedId"]
        name = rec.get("name", seed_id)
        tasks.append(
            create_claudecode_task(
                process_id=f"claudecode-demo-seed-{seed_id}",
                name=f"Claude Code demo — {name}",
                description=(
                    "Fresh Claude Code session started from a captured "
                    f"seed ({name}): logged-in and configured, new "
                    "conversation. Reads context.txt, asks for a color, "
                    "ships a deliverable."
                ),
                config=ClaudeCodeTaskConfig(
                    consumer_instructions=CONSUMER_PROMPT,
                    permission_mode="bypassPermissions",
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
            )
        )

    return tasks
