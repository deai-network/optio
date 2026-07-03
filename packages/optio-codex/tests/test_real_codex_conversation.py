"""Opt-in real-codex conversation E2E + Layer-3 wire capture (never default).

Guide Testing Layer 2 (conversation surface end-to-end against the REAL
binary) AND Layer 3 (capture the real wire → the reducer test replays it). One
real (billable) model turn, so it runs only when explicitly opted in:

    OPTIO_CODEX_CONVERSATION_TEST=1 .venv/bin/python -m pytest \
        packages/optio-codex/tests/test_real_codex_conversation.py -q

Skip-chain (grok convention): env flag set, real ``codex`` on PATH, an authed
``~/.codex/auth.json``. Writes the captured event stream to the
conversation-ui Layer-3 fixture so reduceCodexEvent is exercised on the real
wire (interleaved reasoning + answer deltas), not just fakes.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from optio_core.lifecycle import Optio

from optio_codex import CodexTaskConfig, create_codex_task

REAL_HOME_CODEX = Path.home() / ".codex"
_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "optio-conversation-ui" / "src" / "__tests__" / "fixtures" / "codex-events.json"
)


def _authed() -> bool:
    try:
        data = json.loads((REAL_HOME_CODEX / "auth.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(data.get("tokens") or data.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    os.environ.get("OPTIO_CODEX_CONVERSATION_TEST") != "1"
    or shutil.which("codex") is None
    or not _authed(),
    reason="opt-in real-codex conversation test (OPTIO_CODEX_CONVERSATION_TEST=1, "
    "codex on PATH, authed ~/.codex/auth.json)",
)


async def _plant_identity(hook_ctx):
    """Merge the operator identity into the task's isolated CODEX_HOME before
    launch — the same shape Stage 3 seeds automate (mirrors
    test_real_codex_session.py). Pre-trusts the workdir so the app-server never
    parks on the trust prompt."""
    host = hook_ctx._host
    await host.write_text(
        "home/.codex/auth.json", (REAL_HOME_CODEX / "auth.json").read_text())
    await host.write_text(
        "home/.codex/config.toml",
        f'[projects."{host.workdir}"]\ntrust_level = "trusted"\n',
    )


@pytest.mark.asyncio
async def test_real_conversation_turn_and_capture(mongo_db, task_root):
    """Drive one real ``codex app-server`` conversation turn through the shipped
    CodexConversation, record every raw backend event via ``on_event``, assert a
    coherent turn, and materialize the recorded stream as the Layer-3 fixture so
    reduceCodexEvent is exercised on the real interleaved-reasoning wire."""
    events: list = []
    task = create_codex_task(
        process_id="codex-real-conv",
        name="real conversation proof",
        config=CodexTaskConfig(
            consumer_instructions="",
            mode="conversation",
            host_protocol=False,
            before_execute=_plant_identity,
        ),
    )

    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cxrealconv")
    reply: str | None = None
    try:
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "codex-real-conv", session_id=None, timeout=120,
        )
        # Register the raw-event capture BEFORE the first send so the whole
        # turn is recorded. on_event fans out the transparent backend dicts
        # (the app-server JSON-RPC notifications reduceCodexEvent consumes).
        conv.on_event(events.append)
        done: asyncio.Queue[str] = asyncio.Queue()
        conv.on_message(done.put_nowait)
        # A prompt that provokes reasoning + a short deterministic answer.
        await conv.send("Think step by step, then answer with just the word PONG.")
        reply = await asyncio.wait_for(done.get(), 90)
        await conv.close()
    finally:
        await optio.shutdown(grace_seconds=1.0)

    assert reply and "PONG" in reply.upper()
    assert events, "no raw events captured from the real app-server"
    # Materialize the Layer-3 fixture (only the raw JSON-RPC dicts the reducer
    # consumes — no synthetic x-optio-* wrappers; those are added by the view).
    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(json.dumps(events, indent=2))
