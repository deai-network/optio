"""Reference demo tasks for optio-claudecode — the seed lifecycle.

Exposes a static **"Setup Claude Code seed"** task plus one dynamic
**"Claude Code demo — {name}"** task per captured seed. The operator
launches setup, logs into Claude Code interactively (``/login``) in the
ttyd TUI, configures plugins, then stops the task; on teardown the
environment is captured as a seed and a seed-pinned demo task appears
(via in-process ``resync``). Authentication comes from the seed, not an
``ANTHROPIC_API_KEY`` — claude runs under HOME-isolation
(``HOME=<workdir>/home``), so the host user's ``~/.claude`` is not
inherited; the seed supplies credentials/settings/plugins instead.

Defaults to local mode; set the ``OPTIO_CLAUDECODE_DEMO_SSH_HOST``
environment variable to run via SSH on a remote host. Relevant env vars
(all optional except ``_HOST``):

- ``OPTIO_CLAUDECODE_DEMO_SSH_HOST`` — enables remote mode.
- ``OPTIO_CLAUDECODE_DEMO_SSH_USER`` — default: ``$USER`` on the worker.
- ``OPTIO_CLAUDECODE_DEMO_SSH_KEY_PATH`` — default: ``~/.ssh/id_ed25519``.
- ``OPTIO_CLAUDECODE_DEMO_SSH_PORT`` — default: ``22``.

Hook walkthrough (mirrors the opencode demo), wired on each seed task:

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

from optio_demo.tasks._feedback import make_feedback_on_deliverable


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
    "This is a one-time setup session for a human operator. You do not "
    "need to do anything. The operator will log into Claude Code (run "
    "`/login`) and install any plugins/MCP servers directly in this "
    "terminal, then stop this task — their credentials/settings/plugins "
    "are then captured as a reusable seed and a seed-pinned demo task "
    "appears automatically. Do not run setup commands or narrate; if the "
    "operator asks you something, answer briefly, otherwise stay idle."
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


async def _before_execute(hook_ctx: HookContext) -> None:
    out = await hook_ctx.run_on_host("whoami")
    hook_ctx.report_progress(None, f"Claude Code will run as {out.strip()}")
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


# Exercises the agent feedback channel (tmux fake-typing transport): rejects
# a first delivery missing "over and out", nudges, accepts the re-delivery.
_on_deliverable = make_feedback_on_deliverable("claudecode-demo")


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

    async def _on_seed_saved(seed_id: str, info: str | None = None) -> None:
        # info: account summary from the seeded OAuth token, e.g.
        # "Plan: Claude Max 20x for Jane Doe <jane@x.com>" (or None).
        print(f"[claudecode-demo] seed saved {seed_id}: {info}")
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
                delivery_type="system-notices",
                ssh=ssh,
                # Run setup in bypassPermissions so the operator accepts the
                # bypass-mode warning once here; the acknowledgment lands in
                # ~/.claude.json and is captured into the seed, so every
                # seed-launched demo task (also bypassPermissions) starts
                # pre-acked instead of warning on each launch.
                permission_mode="bypassPermissions",
                # Interactive login; no resume for a one-time setup session.
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
                    delivery_type="system-notices",
                    permission_mode="bypassPermissions",
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    supports_resume=True,
                    # Kick the agent off unattended (reads CLAUDE.md + executes).
                    auto_start=True,
                    # Quiet TUI: one-line tool-call summaries, no bash spam.
                    focus_mode=True,
                ),
            )
        )
        tasks.append(
            create_claudecode_task(
                process_id=f"claudecode-conversation-seed-{seed_id}",
                name=f"Claude Code conversation — {name}",
                description=(
                    "Conversation-mode Claude Code session from a captured "
                    f"seed ({name}): chat with the agent in the dashboard, "
                    "approve tool permissions interactively."
                ),
                config=ClaudeCodeTaskConfig(
                    consumer_instructions="",   # defaulted conversation prompt
                    mode="conversation",
                    conversation_ui=True,
                    permission_gate=True,       # exercises the approve/deny UI
                    host_protocol=False,        # pure conversation gate
                    ssh=ssh,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
            )
        )
        tasks.append(
            create_claudecode_task(
                process_id=f"claudecode-conversation-task-seed-{seed_id}",
                name=f"Claude Code conversation+task — {name}",
                description=(
                    "Same favourite-colour task as the iframe demo (reads "
                    "context.txt, asks for a colour, ships a deliverable) but "
                    "surfaced through the new conversation UI instead of the "
                    "ttyd iframe. host_protocol stays on, so DONE/DELIVERABLE "
                    "and the deliverable-ack channel run alongside the chat."
                ),
                config=ClaudeCodeTaskConfig(
                    consumer_instructions=CONSUMER_PROMPT,
                    mode="conversation",
                    conversation_ui=True,
                    # host_protocol left at its True default — keyword channel on.
                    permission_mode="bypassPermissions",
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    supports_resume=True,
                    # Kick the agent off unattended (reads CLAUDE.md + executes).
                    auto_start=True,
                ),
            )
        )

    return tasks
