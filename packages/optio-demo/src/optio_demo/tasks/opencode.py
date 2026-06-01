"""Reference demo tasks for optio-opencode — the seed lifecycle.

Exposes a static **"Opencode demo"** task (the original additive demo),
a static **"Setup opencode seed"** task, plus one dynamic **"opencode
demo — {name}"** task per captured seed. The operator launches setup,
connects a provider in the web TUI, configures plugins, then stops the
task; on teardown the environment is captured as a seed and a
seed-pinned demo task appears (via in-process ``resync``).
Authentication comes from the seed — opencode runs under HOME/XDG
isolation (``HOME=<workdir>/home``, ``XDG_*`` underneath), so the host
user's ``~/.local/share/opencode`` is not inherited; the seed supplies
``auth.json`` / config / plugins instead.

Defaults to local mode; set the ``OPTIO_OPENCODE_DEMO_SSH_HOST``
environment variable to run the same tasks via SSH on a remote host.
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
from datetime import datetime, timezone

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


DEMO_SEED_COLLECTION_SUFFIX = "_demo_opencode_seeds"

SEED_SETUP_PROMPT = (
    "This is a one-time setup session for a human operator. The operator "
    "will connect a provider, pick the model they want to use, and send at "
    "least one message with it (so that model choice is recorded and "
    "captured into the seed as the default for unattended runs), then stop "
    "this task — their credentials/settings and chosen model are then "
    "captured as a reusable seed and a seed-pinned demo task appears "
    "automatically. If the operator sends you a message, answer briefly so "
    "they get a reply; otherwise stay idle and do not run setup commands or "
    "narrate."
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


def _make_on_seed_saved(db, prefix: str, fw):
    coll = db[f"{prefix}{DEMO_SEED_COLLECTION_SUFFIX}"]

    async def _on_seed_saved(seed_id: str, info: str | None = None) -> None:
        # info: the resolved "providerID/modelID" for this seed (or None).
        print(f"[opencode-demo] seed saved {seed_id}: {info}")
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
        # The original, additive demo task (no seed; vanilla session).
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
                ssh=ssh,
                before_execute=_before_execute,
                after_execute=_after_execute,
                on_deliverable=_on_deliverable,
            ),
        ),
        # The seed setup task: vanilla (no seed_id), on_seed_saved wired.
        # The login browser stays suppressed (the opencode launch default);
        # no redirect/OAuth handling is built here.
        create_opencode_task(
            process_id="opencode-seed-setup",
            name="Setup opencode seed",
            description=(
                "One-time: connect a provider in opencode, pick your model "
                "and send one message with it (this records the model as the "
                "seed's default), then stop the task to capture a reusable "
                "seed. A new seed-pinned demo task appears afterward."
            ),
            config=OpencodeTaskConfig(
                consumer_instructions=SEED_SETUP_PROMPT,
                ssh=ssh,
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
            create_opencode_task(
                process_id=f"opencode-demo-seed-{seed_id}",
                name=f"opencode demo — {name}",
                description=(
                    "Fresh opencode session started from a captured "
                    f"seed ({name}): authenticated and configured, new "
                    "conversation. Reads context.txt, asks for a color, "
                    "ships a deliverable."
                ),
                config=OpencodeTaskConfig(
                    consumer_instructions=CONSUMER_PROMPT,
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    supports_resume=True,
                    # Kick the agent off unattended (reads AGENTS.md + executes).
                    auto_start=True,
                ),
            )
        )

    return tasks
