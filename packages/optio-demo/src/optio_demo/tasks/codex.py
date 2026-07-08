"""Demo tasks for optio-codex — the seed lifecycle.

Exposes a static **"Setup Codex seed"** task plus a seed-pinned iframe run
task per captured seed. The operator launches setup, logs into codex
interactively in the ttyd TUI, then stops the task; on teardown the
environment is captured as a seed and the seed-pinned demo tasks appear
(via in-process ``resync``). Authentication comes from the seed, not an
inherited host identity — codex runs under HOME-isolation
(``HOME=<workdir>/home``), so the host user's ``~/.codex`` is not
inherited; the seed supplies ``auth.json`` / ``config.toml`` instead.
(The seed-pinned CONVERSATION demo completes the trio at Stage 6 / Plan D.)

Login options inside the setup terminal:

- ``codex login --device-auth`` — fully headless (URL + one-time code, done
  in any browser; loopback OAuth is NOT relied on).
- API key: export ``OPTIO_CODEX_DEMO_OPENAI_API_KEY`` before starting the
  demo (surfaced to the session env as ``OPENAI_API_KEY``), then run
  ``printenv OPENAI_API_KEY | codex login --with-api-key`` (codex does not
  honor the env var at runtime; login writes auth.json).

Gating mirrors the wrapper's own seed store: the pinned tasks are driven by
``optio_codex.list_seeds`` over the real ``{prefix}_codex_seeds``
collection. The demo keeps a small ``{prefix}_demo_codex_seeds`` sidecar
(written by ``on_seed_saved``) purely to attach a friendly display name to
each seed; seeds without a name fall back to their seed id.

Defaults to local mode; set ``OPTIO_CODEX_DEMO_SSH_HOST`` to run via SSH on
a remote host. Relevant env vars (all optional except ``_HOST``):
``OPTIO_CODEX_DEMO_SSH_{HOST,USER,KEY_PATH,PORT}``.

Hook walkthrough (mirrors the grok/claudecode/opencode demos), wired on
each seed-pinned task: ``before_execute`` runs ``whoami`` + ships
``context.txt``; ``on_deliverable`` exercises the agent feedback channel
(reject → nudge → accept); ``after_execute`` reads ``./optio.log`` back and
reports a one-line keyword summary.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from optio_agents_all import CodexTaskConfig, create_task, get_agent_info
from optio_codex import (
    HookContext,
    SSHConfig,
    list_seeds,
)
from optio_core.models import TaskInstance

from optio_demo.tasks._feedback import make_feedback_on_deliverable


_NAME = get_agent_info("codex").name  # "Codex"


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


DEMO_SEED_COLLECTION_SUFFIX = "_demo_codex_seeds"

SEED_SETUP_PROMPT = (
    "This is a one-time setup session for a human operator. You do not "
    "need to do anything. The operator will log into Codex directly in "
    "this terminal, then stop this task — their credentials/settings are "
    "then captured as a reusable seed and seed-pinned demo tasks appear "
    "automatically. Do not run setup commands or narrate; if the operator "
    "asks you something, answer briefly, otherwise stay idle."
)


def _resolve_ssh_config() -> SSHConfig | None:
    host = os.environ.get("OPTIO_CODEX_DEMO_SSH_HOST")
    if not host:
        return None
    user = (
        os.environ.get("OPTIO_CODEX_DEMO_SSH_USER")
        or os.environ.get("USER")
        or "root"
    )
    key_path = os.environ.get(
        "OPTIO_CODEX_DEMO_SSH_KEY_PATH",
        os.path.expanduser("~/.ssh/id_ed25519"),
    )
    port_raw = os.environ.get("OPTIO_CODEX_DEMO_SSH_PORT", "22")
    try:
        port = int(port_raw)
    except ValueError:
        raise RuntimeError(
            f"OPTIO_CODEX_DEMO_SSH_PORT must be an integer, got {port_raw!r}"
        )
    return SSHConfig(host=host, user=user, key_path=key_path, port=port)


def _setup_env() -> dict[str, str] | None:
    """Optional API key for `codex login --with-api-key` in the setup TUI."""
    api_key = os.environ.get("OPTIO_CODEX_DEMO_OPENAI_API_KEY")
    return {"OPENAI_API_KEY": api_key} if api_key else None


async def _before_execute(hook_ctx: HookContext) -> None:
    out = await hook_ctx.run_on_host("whoami")
    hook_ctx.report_progress(None, f"codex will run as {out.strip()}")
    await hook_ctx.copy_file(CONTEXT_TXT, "context.txt")


# Exercises the agent feedback channel: rejects a first delivery that doesn't
# meet the bar, nudges the agent, accepts the corrected re-delivery.
_on_deliverable = make_feedback_on_deliverable("codex")


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
        print(f"[codex-demo] seed saved {seed_id}: {info}")
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
        create_task(
            process_id="codex-seed-setup",
            name=f"Setup {_NAME} seed",
            description=(
                "One-time: log into Codex interactively (`codex login "
                "--device-auth`, or `codex login --with-api-key` with "
                "OPTIO_CODEX_DEMO_OPENAI_API_KEY exported), then stop the "
                "task to capture a reusable seed. New seed-pinned demo "
                "tasks appear afterward."
            ),
            config=CodexTaskConfig(
                consumer_instructions=SEED_SETUP_PROMPT,
                # claustrum fs-isolation on (default); delivery_type is mandatory
                # then — it routes the "newer claustrum available" security
                # notice through on_deliverable.
                delivery_type="system-notices",
                ssh=ssh,
                env=_setup_env(),
                # One-time interactive setup: no resume. Codex writes its
                # login state under the per-task CODEX_HOME
                # (<workdir>/home/.codex), captured as the seed on teardown.
                supports_resume=False,
                on_seed_saved=_make_on_seed_saved(db, prefix, fw),
            ),
        ),
    ]

    # One seed-pinned demo task per recorded seed, gated on the real codex
    # seed store (mirrors the wrapper). Friendly names come from the
    # sidecar. (The seed-pinned CONVERSATION demo joins at Stage 6/Plan D.)
    names = await _seed_name_map(db, prefix)
    for rec in await list_seeds(fw.mongo_store):
        seed_id = rec["seedId"]
        name = names.get(seed_id, seed_id)
        tasks.append(
            create_task(
                process_id=f"codex-demo-seed-{seed_id}",
                name=f"{_NAME} demo — {name}",
                description=(
                    "Fresh Codex session started from a captured seed "
                    f"({name}): logged-in and configured, new "
                    "conversation. Reads context.txt, asks for a color, "
                    "ships a deliverable, exercises the feedback channel."
                ),
                config=CodexTaskConfig(
                    consumer_instructions=CONSUMER_PROMPT,
                    delivery_type="system-notices",
                    ssh=ssh,
                    before_execute=_before_execute,
                    after_execute=_after_execute,
                    on_deliverable=_on_deliverable,
                    seed_id=seed_id,
                    supports_resume=True,
                    # Kick the agent off unattended (reads AGENTS.md +
                    # executes). auto_start now defaults to False (Gap 2) — a
                    # task-execution surface must opt in explicitly; the
                    # seed-pinned CONVERSATION demo below correctly omits it.
                    auto_start=True,
                ),
            )
        )
        tasks.append(
            create_task(
                process_id=f"codex-conversation-seed-{seed_id}",
                name=f"{_NAME} conversation — {name}",
                description=(
                    "Conversation-mode Codex session from a captured "
                    f"seed ({name}): chat with the agent in the dashboard, "
                    "approve tool permissions interactively."
                ),
                config=CodexTaskConfig(
                    consumer_instructions="",   # defaulted conversation prompt
                    delivery_type="system-notices",
                    mode="conversation",
                    conversation_ui=True,
                    tool_verbosity="description-only",
                    show_session_controls=True,
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
