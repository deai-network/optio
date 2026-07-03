"""Demo tasks for optio-cursor — the seed lifecycle.

Exposes a static **"Setup Cursor seed"** task plus seed-pinned run tasks per
captured seed (one ttyd-iframe demo, one conversation demo). The operator
launches setup, runs ``cursor-agent login`` interactively in the ttyd TUI,
then stops the task; on teardown the environment is captured as a seed and
the seed-pinned demo tasks appear (via in-process ``resync``). Authentication
comes from the seed, not an inherited host identity — cursor runs under
HOME-isolation (``HOME=<workdir>/home``), so the host user's ``~/.cursor`` is
not inherited; the seed supplies ``auth.json`` / ``cli-config.json`` instead.

Gating mirrors the wrapper's own seed store: the pinned tasks are driven by
``optio_cursor.list_seeds`` over the real ``{prefix}_cursor_seeds`` collection.
The demo keeps a small ``{prefix}_demo_cursor_seeds`` sidecar (written by
``on_seed_saved``) purely to attach a friendly display name to each seed;
seeds without a name fall back to their seed id.

Defaults to local mode; set the ``OPTIO_CURSOR_DEMO_SSH_HOST`` environment
variable to run via SSH on a remote host. Relevant env vars (all optional
except ``_HOST``):

- ``OPTIO_CURSOR_DEMO_SSH_HOST`` — enables remote mode.
- ``OPTIO_CURSOR_DEMO_SSH_USER`` — default: ``$USER`` on the worker.
- ``OPTIO_CURSOR_DEMO_SSH_KEY_PATH`` — default: ``~/.ssh/id_ed25519``.
- ``OPTIO_CURSOR_DEMO_SSH_PORT`` — default: ``22``.

Hook walkthrough (mirrors the claudecode/opencode/grok demos), wired on each
seed-pinned iframe task:

- ``before_execute`` runs ``whoami`` on the host (proves the hook fires
  inside the session pipeline) and ships ``context.txt`` into the workdir
  via ``copy_file`` (bytes source). The LLM is instructed to read that file,
  so its presence is observable end-to-end.
- ``on_deliverable`` exercises the agent feedback channel (rejects a first
  delivery, nudges, accepts the re-delivery).
- ``after_execute`` reads ``./optio.log`` back via ``read_text_from_host``
  and reports a one-line summary, proving the hook fires before workdir
  teardown.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from optio_core.models import TaskInstance
from optio_cursor import (
    CursorTaskConfig,
    HookContext,
    SSHConfig,
    create_cursor_task,
    list_seeds,
)

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


DEMO_SEED_COLLECTION_SUFFIX = "_demo_cursor_seeds"

SEED_SETUP_PROMPT = (
    "This is a one-time setup session for a human operator. To capture a "
    "reusable seed, the operator (or you, if asked) runs `cursor-agent "
    "login` in this terminal. Because `NO_OPEN_BROWSER=1` is set, the "
    "login flow PRINTS an authentication URL instead of opening a browser "
    "— surface that URL to the operator on a `BROWSER:` line so they can "
    "complete the OAuth in their own browser. Once login succeeds, the "
    "operator stops this task: their credentials/settings are captured as "
    "a reusable seed and seed-pinned demo tasks appear automatically. "
    "(Alternative: instead of interactive login, paste a `CURSOR_API_KEY` "
    "into the task config.) Do not run other setup commands or narrate; if "
    "the operator asks you something, answer briefly, otherwise stay idle."
)


def _resolve_ssh_config() -> SSHConfig | None:
    host = os.environ.get("OPTIO_CURSOR_DEMO_SSH_HOST")
    if not host:
        return None
    user = (
        os.environ.get("OPTIO_CURSOR_DEMO_SSH_USER")
        or os.environ.get("USER")
        or "root"
    )
    key_path = os.environ.get(
        "OPTIO_CURSOR_DEMO_SSH_KEY_PATH",
        os.path.expanduser("~/.ssh/id_ed25519"),
    )
    port_raw = os.environ.get("OPTIO_CURSOR_DEMO_SSH_PORT", "22")
    try:
        port = int(port_raw)
    except ValueError:
        raise RuntimeError(
            f"OPTIO_CURSOR_DEMO_SSH_PORT must be an integer, got {port_raw!r}"
        )
    return SSHConfig(host=host, user=user, key_path=key_path, port=port)


async def _before_execute(hook_ctx: HookContext) -> None:
    out = await hook_ctx.run_on_host("whoami")
    hook_ctx.report_progress(None, f"cursor will run as {out.strip()}")
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


# Exercises the agent feedback channel: rejects a first delivery that doesn't
# end with "over and out", nudges the agent, accepts the corrected re-delivery.
_on_deliverable = make_feedback_on_deliverable("cursor-demo")


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
        # info: account summary from the seeded identity (or None).
        print(f"[cursor-demo] seed saved {seed_id}: {info}")
        # Cosmetic numbering; a concurrent-save race may reuse a number —
        # acceptable, the seedId is the real key.
        count = await coll.count_documents({})
        name = f"Config #{count + 1}"
        await coll.insert_one({
            "seedId": seed_id,
            "name": name,
            "createdAt": datetime.now(timezone.utc),
        })
        # Regenerate the task list so seed-pinned demo tasks appear.
        await fw.resync()

    return _on_seed_saved


async def _seed_name_map(db, prefix: str) -> dict[str, str]:
    """seedId -> friendly display name, from the demo sidecar collection."""
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]
    out: dict[str, str] = {}
    async for rec in coll.find({}, projection={"seedId": 1, "name": 1}):
        if rec.get("seedId") and rec.get("name"):
            out[rec["seedId"]] = rec["name"]
    return out


async def get_tasks(services: dict) -> list[TaskInstance]:
    db = services["db"]
    prefix = services["prefix"]
    fw = services["optio"]
    ssh = _resolve_ssh_config()

    tasks: list[TaskInstance] = [
        # The seed setup task: vanilla (no seed_id), on_seed_saved wired.
        create_cursor_task(
            process_id="cursor-seed-setup",
            name="Setup Cursor seed",
            description=(
                "One-time: run `cursor-agent login` interactively, then stop "
                "the task to capture a reusable seed. New seed-pinned demo "
                "tasks appear afterward."
            ),
            config=CursorTaskConfig(
                consumer_instructions=SEED_SETUP_PROMPT,
                ssh=ssh,
                # Interactive login; no resume for a one-time setup session.
                # fs_isolation left at its default (mirrors the claudecode/grok
                # seed-setup tasks): cursor writes its login state under the
                # per-task HOME (<workdir>/home/.cursor), which is inside the
                # sandbox, and device-auth happens in the host browser — so
                # isolation does not block login.
                supports_resume=False,
                on_seed_saved=_make_on_seed_saved(db, prefix, fw),
            ),
        ),
    ]

    # One seed-pinned demo task per recorded seed, gated on the real cursor
    # seed store (mirrors the wrapper). Friendly names come from the sidecar.
    names = await _seed_name_map(db, prefix)
    for rec in await list_seeds(fw.mongo_store):
        seed_id = rec["seedId"]
        name = names.get(seed_id, seed_id)
        tasks.append(
            create_cursor_task(
                process_id=f"cursor-demo-seed-{seed_id}",
                name=f"Cursor demo — {name}",
                description=(
                    "Fresh Cursor session started from a captured seed "
                    f"({name}): logged-in and configured, new conversation. "
                    "Reads context.txt, asks for a color, ships a deliverable."
                ),
                config=CursorTaskConfig(
                    consumer_instructions=CONSUMER_PROMPT,
                    # Cursor's auto-approve (no permission prompts); grok used
                    # --always-approve, claudecode bypassPermissions.
                    force=True,
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    supports_resume=True,
                    # Kick the agent off unattended (reads AGENTS.md + executes).
                    # auto_start now defaults to False — this iframe run task must
                    # opt in explicitly; the conversation/seed-setup tasks below
                    # correctly inherit False so they wait for the operator.
                    auto_start=True,
                ),
            )
        )
        tasks.append(
            create_cursor_task(
                process_id=f"cursor-conversation-seed-{seed_id}",
                name=f"Cursor conversation — {name}",
                description=(
                    "Conversation-mode Cursor session from a captured seed "
                    f"({name}): chat with the agent in the dashboard, approve "
                    "tool permissions interactively."
                ),
                config=CursorTaskConfig(
                    consumer_instructions="",   # defaulted conversation prompt
                    mode="conversation",
                    conversation_ui=True,
                    tool_verbosity="description-only",
                    show_model_selector=True,
                    show_file_upload=True,
                    file_download=True,
                    permission_gate=True,       # exercises the approve/deny UI
                    host_protocol=False,        # pure conversation gate
                    ssh=ssh,
                    seed_id=seed_id,
                    supports_resume=True,
                ),
            )
        )

    return tasks
