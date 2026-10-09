"""Session-level + validation tests for the opt-in conversation UI.

Three layers (Phase II spec, docs/2026-06-10-claudecode-conversation-ui-design.md):

  1. Validation matrix (pure unit): ``conversation_ui=True`` requires
     ``mode="conversation"``; default is False.
  2. argv (pure unit): ``build_conversation_argv`` emits
     ``--include-partial-messages`` / ``--replay-user-messages`` only when
     asked for them.
  3. Session integration (same bootstrap as ``test_conversation_session.py``
     — real engine, claude shim): a ``conversation_ui=True`` task registers
     a reachable ConversationListener as widgetUpstream (per-task basic-auth
     inner credential, ``widgetData`` carrying ``protocol``/``toolVerbosity``
     plus the session-control keys (``showSessionControls``/``controls``),
     ``uiWidget == "conversation"``), the SSE replay carries the
     fake's ``system/init`` event, and the listener port is closed once the
     task reaches its terminal state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import time as _time

import aiohttp
import pytest

from optio_core.lifecycle import Optio

from optio_claudecode import ClaudeCodeTaskConfig, create_claudecode_task
from optio_claudecode import host_actions
from optio_claudecode.transcript import slugify_workdir

from .fake_claude import CLI_MODELS


_TERMINAL = {"done", "failed", "cancelled"}
BYPASS_CONFIRM = "Switch to Bypass? Claude will run everything without asking."
NEEDS_GATE = ("Needs the permission gate: this task has no one to answer Claude's "
              "questions (permission_gate is off).")


# --- 1. validation matrix (pure unit) ------------------------------------


# Some tests here drive a live conversation session and share fixed home/cache
# paths under ~/.local/share/optio-claudecode; run this module in the final
# non-parallel phase.
pytestmark = pytest.mark.serial


def test_conversation_ui_requires_conversation_mode():
    with pytest.raises(ValueError, match="conversation_ui"):
        ClaudeCodeTaskConfig(
            consumer_instructions="x",
            mode="iframe",
            conversation_ui=True,
            fs_isolation=False,
        )


def test_conversation_ui_ok_in_conversation_mode():
    config = ClaudeCodeTaskConfig(
        consumer_instructions="x",
        mode="conversation",
        permission_mode="bypassPermissions",
        conversation_ui=True,
        fs_isolation=False,
    )
    assert config.conversation_ui is True


def test_conversation_ui_defaults_false():
    config = ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False)
    assert config.conversation_ui is False


# --- 2. argv flags (pure unit) --------------------------------------------


def test_argv_includes_ui_flags_when_requested():
    argv = host_actions.build_conversation_argv(
        "/opt/claude", claude_flags=["--model", "m"], permission_gate=False,
        include_partial_messages=True,
        replay_user_messages=True,
    )
    assert "--include-partial-messages" in argv
    assert "--replay-user-messages" in argv


def test_argv_defaults_omit_ui_flags():
    argv = host_actions.build_conversation_argv(
        "/opt/claude", claude_flags=[], permission_gate=True,
    )
    assert "--include-partial-messages" not in argv
    assert "--replay-user-messages" not in argv


def test_include_partial_messages_standalone_knob():
    from optio_claudecode import session as session_mod

    on = ClaudeCodeTaskConfig(
        consumer_instructions="x", mode="conversation",
        permission_mode="bypassPermissions",
        include_partial_messages=True, fs_isolation=False,
    )
    off = ClaudeCodeTaskConfig(
        consumer_instructions="x", mode="conversation",
        permission_mode="bypassPermissions", fs_isolation=False,
    )
    ui = ClaudeCodeTaskConfig(
        consumer_instructions="x", mode="conversation",
        permission_mode="bypassPermissions", conversation_ui=True,
        fs_isolation=False,
    )
    assert off.include_partial_messages is False  # default stays off
    assert session_mod._partials_enabled(on) is True
    assert session_mod._partials_enabled(off) is False
    # conversation_ui still implies partials (behavior unchanged).
    assert session_mod._partials_enabled(ui) is True


def test_partials_knob_flag_reaches_argv():
    argv = host_actions.build_conversation_argv(
        "/opt/claude", claude_flags=[], permission_gate=False,
        include_partial_messages=True,
    )
    assert "--include-partial-messages" in argv
    # The knob does not drag user-message replay along.
    assert "--replay-user-messages" not in argv


# --- 3. session integration ------------------------------------------------


async def _make_optio(mongo_db, prefix: str) -> Optio:
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix=prefix)
    return optio


async def _wait_terminal(optio: Optio, process_id: str, timeout: float = 60.0) -> dict:
    """Poll until process_id reaches a terminal state or timeout."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc["status"]["state"] in _TERMINAL:
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} did not reach terminal state in {timeout}s")


async def _wait_widget_upstream(
    optio: Optio, process_id: str, timeout: float = 60.0,
) -> dict:
    """Poll the process doc until widgetUpstream is set; return the doc."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetUpstream"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetUpstream in {timeout}s")


async def _wait_widget_data(
    optio: Optio, process_id: str, timeout: float = 60.0,
) -> dict:
    """Poll the process doc until widgetData is set; return the doc.

    widgetData is written AFTER widgetUpstream (with the CLI's model list
    asked for in between), so a widgetUpstream-only wait can return before it
    lands.
    """
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        proc = await optio.get_process(process_id)
        if proc is not None and proc.get("widgetData"):
            return proc
        await asyncio.sleep(0.05)
    raise AssertionError(f"{process_id} never set widgetData in {timeout}s")


async def _read_until(resp, predicate, timeout: float = 60.0) -> dict:
    """Parse SSE data frames from an open aiohttp response until one
    satisfies ``predicate``; return it. Keep-alive comment frames carry no
    data line and are skipped."""
    buf = b""

    async def _go():
        nonlocal buf
        while True:
            chunk = await resp.content.read(1024)
            if not chunk:
                raise AssertionError("SSE stream ended before a match")
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data = [l[5:] for l in frame.split(b"\n") if l.startswith(b"data:")]
                if not data:
                    continue
                event = json.loads(b"".join(data).strip())
                if predicate(event):
                    return event

    return await asyncio.wait_for(_go(), timeout)


async def _wait_port_refused(port: int, timeout: float = 60.0) -> None:
    """Poll until connecting to 127.0.0.1:<port> is refused."""
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"port {port} still accepting connections after {timeout}s")


def _ui_config(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    **kw,
) -> ClaudeCodeTaskConfig:
    base = dict(
        consumer_instructions="Converse with the test.",
        mode="conversation",
        conversation_ui=True,
        permission_mode="bypassPermissions",
        fs_isolation=False,
        install_dir=str(claude_cache_dir),
        ttyd_install_dir=str(shim_install_dir),
    )
    base.update(kw)
    return ClaudeCodeTaskConfig(**base)


@pytest.mark.asyncio
async def test_conversation_ui_session_lifecycle(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """conversation_ui=True end to end: widgetUpstream + innerAuth registered,
    widgetData primed, uiWidget set, listener reachable (SSE replay carries
    the system/init event), and the listener stops with the task."""
    optio = await _make_optio(mongo_db, "ccui1")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-ui",
            name="Conversation UI",
            config=_ui_config(shim_install_dir, claude_cache_dir),
        )
        assert task.ui_widget == "conversation"
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(
            "cc-conv-ui", session_id=None, timeout=60,
        )

        # The listener registers itself right after publish_result.
        proc = await _wait_widget_upstream(optio, "cc-conv-ui")
        upstream = proc["widgetUpstream"]
        assert upstream["url"].startswith("http://")
        inner = upstream["innerAuth"]
        assert inner is not None
        assert inner["username"] == "optio"
        assert inner["password"]

        # widgetData lands after widgetUpstream (the CLI's model list is asked
        # for between the two writes); re-fetch until it is present so a
        # load-stalled write can't make the equality check read a missing field.
        proc = await _wait_widget_data(optio, "cc-conv-ui")
        assert proc["widgetData"] == {
            "protocol": "claudecode",
            "toolVerbosity": "description-only",
            "thinkingVerbosity": "hidden",
            "showSessionControls": False,
            "nativeSpinner": False,
            "controls": [
                {
                    "id": "model",
                    "kind": "select",
                    "label": "Model",
                    "category": "model",
                    "description": "What model is powering this conversation?",
                    # The CLI's own list (initialize), in its order; nothing
                    # picked: the default alias, before any turn has run.
                    "value": "default",
                    "disabled": False,
                    # Model descriptions read in the open list, not in tooltips.
                    "inlineDescriptions": True,
                    "options": [
                        {"value": m["value"], "label": m["displayName"],
                         "description": m["description"], "disabled": False}
                        for m in CLI_MODELS
                    ],
                },
                {
                    # The default model's effort levels, as the CLI lists them.
                    "id": "reasoning_effort",
                    "kind": "slider",
                    "label": "Effort",
                    "category": "thought_level",
                    "description": "How much should the model think before answering?",
                    "value": "high",
                    "levels": ["low", "medium", "high", "xhigh", "max"],
                    "disabled": False,
                },
                {
                    # Launched in bypassPermissions without the permission gate:
                    # all six modes; the two that ask are disabled (no gate).
                    "id": "permission_mode",
                    "kind": "select",
                    "label": "Permissions",
                    "category": "mode",
                    "description": "How should Claude ask before it acts?",
                    "value": "bypassPermissions",
                    "disabled": False,
                    "options": [
                        {"value": "default", "label": "Manual",
                         "description": "Asks before editing files or running commands",
                         "disabled": True, "whyDisabled": NEEDS_GATE},
                        {"value": "acceptEdits", "label": "Accept edits",
                         "description": "Edits files without asking; asks before other commands",
                         "disabled": False},
                        {"value": "plan", "label": "Plan",
                         "description": "Reads and plans without editing; asks you to approve the plan",
                         "disabled": True, "whyDisabled": NEEDS_GATE},
                        {"value": "auto", "label": "Auto",
                         "description": "A classifier reviews each action instead of you; risky actions are blocked",
                         "disabled": False},
                        {"value": "dontAsk", "label": "Don't ask",
                         "description": "Runs only pre-approved tools; refuses anything that would need approval",
                         "disabled": False},
                        {"value": "bypassPermissions", "label": "Bypass",
                         "description": "Runs everything without asking", "disabled": False,
                         "variant": "danger", "confirm": BYPASS_CONFIRM},
                    ],
                },
            ],
            "showFileUpload": False,
            "maxUploadBytes": 10_000_000,
            "fileDownload": False,
            "maxDownloadBytes": 10_000_000,
            "uploadUrl": (
                "{widgetProxyUrl}../../../../widget-upload/"
                f"{mongo_db.name}/ccui1/cc-conv-ui"
            ),
        }
        assert proc["uiWidget"] == "conversation"

        # Hit the listener directly, authenticating with the inner credential
        # the widget proxy would inject; the replay buffer must already carry
        # the fake claude's system/init event.
        token = base64.b64encode(
            f"optio:{inner['password']}".encode(),
        ).decode()
        headers = {"Authorization": f"Basic {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{upstream['url']}/events", headers=headers,
            ) as resp:
                assert resp.status == 200
                init = await _read_until(
                    resp, lambda e: e.get("type") == "system",
                )
                assert init["subtype"] == "init"

        await conv.close()
        proc = await _wait_terminal(optio, "cc-conv-ui")
        assert proc["status"]["state"] == "done"

        # Teardown stopped the listener: the port refuses connections.
        port = int(upstream["url"].rsplit(":", 1)[1])
        await _wait_port_refused(port)
    finally:
        await optio.shutdown(grace_seconds=1.0)


def _basic(password: str) -> dict:
    token = base64.b64encode(f"optio:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _has_control(event: dict, cid: str, value) -> bool:
    return event.get("type") == "x-optio-control-update" and any(
        c.get("id") == cid and c.get("value") == value for c in event.get("controls") or []
    )


@pytest.mark.asyncio
async def test_permission_mode_switches_live_and_the_allowlist_holds(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """session_controls=["permission_mode"]: only that control is offered, a
    hidden one cannot be set through /control, and a mode switch is applied
    live (control protocol) and re-emitted in the controls snapshot."""
    optio = await _make_optio(mongo_db, "ccui-perm")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-perm", name="Permission mode",
            config=_ui_config(
                shim_install_dir, claude_cache_dir,
                show_session_controls=True, session_controls=["permission_mode"],
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-perm", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-conv-perm"))["widgetUpstream"]
        proc = await _wait_widget_data(optio, "cc-conv-perm")
        assert [c["id"] for c in proc["widgetData"]["controls"]] == ["permission_mode"]

        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{url}/control", headers=headers,
                                    json={"id": "model", "value": "claude-haiku-4-5"}) as r:
                assert r.status == 403
            async with session.get(f"{url}/events", headers=headers) as events:
                async with session.post(f"{url}/control", headers=headers,
                                        json={"id": "permission_mode", "value": "acceptEdits"}) as r:
                    assert r.status == 200
                upd = await _read_until(events, lambda e: _has_control(e, "permission_mode", "acceptEdits"))
                assert [c["id"] for c in upd["controls"]] == ["permission_mode"]
            # Launched in bypassPermissions: switching back into it is allowed.
            async with session.post(f"{url}/control", headers=headers,
                                    json={"id": "permission_mode", "value": "bypassPermissions"}) as r:
                assert r.status == 200

        await conv.close()
        await _wait_terminal(optio, "cc-conv-perm")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_a_model_relaunch_keeps_the_picked_permission_mode(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """A model change relaunches claude: it comes back in the mode the operator
    picked, not the launch mode, and still able to return to bypass."""
    optio = await _make_optio(mongo_db, "ccui-perm2")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-perm2", name="Permission mode relaunch",
            config=_ui_config(shim_install_dir, claude_cache_dir, show_session_controls=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-perm2", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-conv-perm2"))["widgetUpstream"]
        await _wait_widget_data(optio, "cc-conv-perm2")
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                async with session.post(f"{url}/control", headers=headers,
                                        json={"id": "permission_mode", "value": "acceptEdits"}) as r:
                    assert r.status == 200
                await _read_until(events, lambda e: _has_control(e, "permission_mode", "acceptEdits"))
                async with session.post(f"{url}/control", headers=headers,
                                        json={"id": "model", "value": "haiku"}) as r:
                    assert r.status == 200
                # The relaunched process's own init reports the picked mode.
                await _read_until(events, lambda e: e.get("type") == "system"
                                  and e.get("subtype") == "init"
                                  and e.get("permissionMode") == "acceptEdits")
            async with session.post(f"{url}/control", headers=headers,
                                    json={"id": "permission_mode", "value": "bypassPermissions"}) as r:
                assert r.status == 200

        await conv.close()
        await _wait_terminal(optio, "cc-conv-perm2")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_a_picked_alias_stays_selected_when_claude_reports_its_full_id(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """Picking Haiku relaunches claude with --model haiku; its system/init
    names the full id (claude-haiku-4-5-20251001). The select keeps showing
    the alias, and the effort slider goes (Haiku has no effort levels)."""
    optio = await _make_optio(mongo_db, "ccui-alias")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-alias", name="Model alias",
            config=_ui_config(shim_install_dir, claude_cache_dir, show_session_controls=True),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-alias", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-conv-alias"))["widgetUpstream"]
        await _wait_widget_data(optio, "cc-conv-alias")
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                async with session.post(f"{url}/control", headers=headers,
                                        json={"id": "model", "value": "haiku"}) as r:
                    assert r.status == 200
                await _read_until(events, lambda e: e.get("type") == "system"
                                  and e.get("subtype") == "init"
                                  and e.get("model") == "claude-haiku-4-5-20251001")
                # A snapshot built after that init: the select still says haiku.
                async with session.post(f"{url}/control", headers=headers,
                                        json={"id": "permission_mode", "value": "acceptEdits"}) as r:
                    assert r.status == 200
                upd = await _read_until(events, lambda e: _has_control(e, "permission_mode", "acceptEdits"))
                by_id = {c["id"]: c for c in upd["controls"]}
                assert by_id["model"]["value"] == "haiku"
                assert "reasoning_effort" not in by_id

        await conv.close()
        await _wait_terminal(optio, "cc-conv-alias")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_a_pinned_full_model_id_launches_and_shows_selected(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """config.model names a full id no alias stands for: claude runs on it,
    and the CLI lists it as an entry of its own, selected; an older Opus than
    the listed one, so under Older versions."""
    optio = await _make_optio(mongo_db, "ccui-pin")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-pin", name="Pinned model",
            config=_ui_config(shim_install_dir, claude_cache_dir, model="claude-opus-4-8"),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-pin", session_id=None, timeout=60)
        proc = await _wait_widget_data(optio, "cc-conv-pin")
        model = next(c for c in proc["widgetData"]["controls"] if c["id"] == "model")
        assert model["value"] == "claude-opus-4-8"
        assert model["options"][-1] == {
            "value": "claude-opus-4-8", "label": "claude-opus-4-8",
            "description": "Custom model", "disabled": False, "group": "Older versions",
        }
        await conv.close()
        await _wait_terminal(optio, "cc-conv-pin")
    finally:
        await optio.shutdown(grace_seconds=1.0)


def _plant_transcript_on(model: str):
    """before_execute: a transcript whose last assistant turn ran on ``model``
    (the fake writes none), so a resume continues it."""
    async def before(hook_ctx):
        workdir = pathlib.Path(hook_ctx._host.workdir)
        pdir = workdir / "home/.claude/projects" / slugify_workdir(str(workdir))
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "t.jsonl").write_text("\n".join(json.dumps(line) for line in [
            {"uuid": "u1", "parentUuid": None, "sessionId": "s", "type": "user",
             "message": {"role": "user", "content": "hi"}},
            {"uuid": "u2", "parentUuid": "u1", "sessionId": "s", "type": "assistant",
             "message": {"role": "assistant", "model": model,
                         "content": [{"type": "text", "text": "hello"}]}},
        ]) + "\n")
    return before


@pytest.mark.asyncio
async def test_a_resumed_conversation_runs_the_default_alias_not_the_transcripts_model(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """--continue alone keeps the full id the transcript ended on (here an old
    claude-opus-4-6; the fake does what the CLI does). With no model
    configured the continued session passes --model default, so it runs
    today's default model."""
    optio = await _make_optio(mongo_db, "ccui-resume")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-resume", name="Resume model",
            config=_ui_config(
                shim_install_dir, claude_cache_dir,
                supports_resume=True, credentials_json={"token": "test"},
                before_execute=_plant_transcript_on("claude-opus-4-6"),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-resume", session_id=None, timeout=60)
        await _wait_widget_data(optio, "cc-conv-resume")
        await conv.close()
        await _wait_terminal(optio, "cc-conv-resume")

        conv2 = await optio.launch_and_await_result(
            "cc-conv-resume", resume=True, session_id=None, timeout=60,
        )
        replied = asyncio.Event()
        conv2.on_message(lambda _text: replied.set())
        await conv2.send("ping")
        await asyncio.wait_for(replied.wait(), 60)
        # the resumed claude named its model (system/init) before replying
        assert conv2.runtime_model == "claude-opus-5[1m]"
        await conv2.close()
        await _wait_terminal(optio, "cc-conv-resume")
    finally:
        await optio.shutdown(grace_seconds=1.0)


async def _post_control(session, url: str, headers: dict, cid: str, value) -> None:
    async with session.post(f"{url}/control", headers=headers, json={"id": cid, "value": value}) as r:
        assert r.status == 200


def _controls(proc: dict) -> dict:
    return {c["id"]: c for c in proc["widgetData"]["controls"]}


@pytest.mark.asyncio
async def test_a_resumed_conversation_keeps_the_operators_picks(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """The model, effort and permission mode the operator picked come back
    on an optio resume: the resumed claude runs them, the controls show them."""
    optio = await _make_optio(mongo_db, "ccui-picks")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-picks", name="Picks across a resume",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, show_session_controls=True,
                supports_resume=True, credentials_json={"token": "test"},
                before_execute=_plant_transcript_on("claude-opus-5[1m]"),
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-picks", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-conv-picks"))["widgetUpstream"]
        await _wait_widget_data(optio, "cc-conv-picks")
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                await _post_control(session, url, headers, "model", "sonnet")
                await _read_until(events, lambda e: _has_control(e, "model", "sonnet"))
                await _post_control(session, url, headers, "reasoning_effort", "low")
                await _read_until(events, lambda e: _has_control(e, "reasoning_effort", "low"))
                await _post_control(session, url, headers, "permission_mode", "acceptEdits")
                await _read_until(events, lambda e: _has_control(e, "permission_mode", "acceptEdits"))
        await conv.close()
        await _wait_terminal(optio, "cc-conv-picks")

        conv2 = await optio.launch_and_await_result(
            "cc-conv-picks", resume=True, session_id=None, timeout=60,
        )
        controls = _controls(await _wait_widget_data(optio, "cc-conv-picks"))
        assert controls["model"]["value"] == "sonnet"
        assert controls["reasoning_effort"]["value"] == "low"
        assert controls["permission_mode"]["value"] == "acceptEdits"
        replied = asyncio.Event()
        conv2.on_message(lambda _text: replied.set())
        await conv2.send("ping")
        await asyncio.wait_for(replied.wait(), 60)
        assert conv2.runtime_model == "claude-sonnet-5"
        assert conv2.permission_mode == "acceptEdits"
        await conv2.close()
        await _wait_terminal(optio, "cc-conv-picks")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_a_resumed_pick_the_cli_no_longer_lists_moves_to_its_family(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    tmp_path: pathlib.Path,
):
    """The operator picked Opus (1M context); by the resume the CLI offers
    only a plain Opus, on a newer model. The resumed session runs that one
    (same family), and the vanished alias is not offered."""
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(CLI_MODELS))
    optio = await _make_optio(mongo_db, "ccui-family")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-family", name="Pick moves to its family",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, show_session_controls=True,
                supports_resume=True, credentials_json={"token": "test"},
                before_execute=_plant_transcript_on("claude-opus-5[1m]"),
                env={"FAKE_CLAUDE_MODELS_FILE": str(models_file)},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-family", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-conv-family"))["widgetUpstream"]
        await _wait_widget_data(optio, "cc-conv-family")
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                await _post_control(session, url, headers, "model", "opus[1m]")
                await _read_until(events, lambda e: _has_control(e, "model", "opus[1m]"))
        await conv.close()
        await _wait_terminal(optio, "cc-conv-family")

        later = [m for m in CLI_MODELS if m["value"] not in ("default", "opus[1m]")]
        later[:0] = [
            {**CLI_MODELS[0], "resolvedModel": "claude-opus-6"},
            {"value": "opus", "resolvedModel": "claude-opus-6", "displayName": "Opus",
             "description": "Opus 6", "supportsEffort": True,
             "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
        ]
        models_file.write_text(json.dumps(later))

        conv2 = await optio.launch_and_await_result(
            "cc-conv-family", resume=True, session_id=None, timeout=60,
        )
        model = _controls(await _wait_widget_data(optio, "cc-conv-family"))["model"]
        assert model["value"] == "opus"
        assert "opus[1m]" not in [o["value"] for o in model["options"]]
        replied = asyncio.Event()
        conv2.on_message(lambda _text: replied.set())
        await conv2.send("ping")
        await asyncio.wait_for(replied.wait(), 60)
        assert conv2.runtime_model == "claude-opus-6"
        await conv2.close()
        await _wait_terminal(optio, "cc-conv-family")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_when_the_probe_cannot_tell_the_launched_list_corrects_the_pick(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    tmp_path: pathlib.Path,
):
    """A claude that answers initialize only when continuing (the fake's
    error-fresh): the probe before the resumed launch learns nothing, so the
    launched process's list shows the saved Opus (1M context) is gone, and
    the session relaunches on the family's Opus."""
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(CLI_MODELS))
    optio = await _make_optio(mongo_db, "ccui-fallback2")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-fallback2", name="Probe cannot tell",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, show_session_controls=True,
                supports_resume=True, credentials_json={"token": "test"},
                before_execute=_plant_transcript_on("claude-opus-5[1m]"),
                env={"FAKE_CLAUDE_MODELS_FILE": str(models_file),
                     "FAKE_CLAUDE_INITIALIZE": "error-fresh"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-fallback2", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-conv-fallback2"))["widgetUpstream"]
        await _wait_widget_data(optio, "cc-conv-fallback2")
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                await _post_control(session, url, headers, "model", "opus[1m]")
                await _read_until(events, lambda e: _has_control(e, "model", "opus[1m]"))
        await conv.close()
        await _wait_terminal(optio, "cc-conv-fallback2")

        later = [m for m in CLI_MODELS if m["value"] not in ("default", "opus[1m]")]
        later[:0] = [
            {**CLI_MODELS[0], "resolvedModel": "claude-opus-6"},
            {"value": "opus", "resolvedModel": "claude-opus-6", "displayName": "Opus",
             "description": "Opus 6", "supportsEffort": True,
             "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
        ]
        models_file.write_text(json.dumps(later))

        conv2 = await optio.launch_and_await_result(
            "cc-conv-fallback2", resume=True, session_id=None, timeout=60,
        )
        upstream = (await _wait_widget_upstream(optio, "cc-conv-fallback2"))["widgetUpstream"]
        model = _controls(await _wait_widget_data(optio, "cc-conv-fallback2"))["model"]
        assert model["value"] == "opus"
        assert "opus[1m]" not in [o["value"] for o in model["options"]]
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                await _read_until(events, lambda e: e.get("type") == "system"
                                  and e.get("subtype") == "init"
                                  and e.get("model") == "claude-opus-6")
        await conv2.close()
        await _wait_terminal(optio, "cc-conv-fallback2")
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_without_the_cli_list_the_picker_offers_the_aliases_and_a_relaunch_asks_again(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
):
    """The first claude refuses initialize: the model select still offers the
    bare aliases (never empty). A relaunch (here: picking Sonnet) asks the new
    claude, and the snapshot then carries the CLI's list."""
    optio = await _make_optio(mongo_db, "ccui-fallback")
    try:
        task = create_claudecode_task(
            process_id="cc-conv-fallback", name="Model list fallback",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, show_session_controls=True,
                env={"FAKE_CLAUDE_INITIALIZE": "error-fresh"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-conv-fallback", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-conv-fallback"))["widgetUpstream"]
        proc = await _wait_widget_data(optio, "cc-conv-fallback")
        model = next(c for c in proc["widgetData"]["controls"] if c["id"] == "model")
        assert model["value"] == "default"
        assert model["options"] == [
            {"value": "default", "label": "Default", "disabled": False},
            {"value": "opus", "label": "Opus", "disabled": False},
            {"value": "sonnet", "label": "Sonnet", "disabled": False},
            {"value": "haiku", "label": "Haiku", "disabled": False},
        ]
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                async with session.post(f"{url}/control", headers=headers,
                                        json={"id": "model", "value": "sonnet"}) as r:
                    assert r.status == 200
                upd = await _read_until(events, lambda e: _has_control(e, "model", "sonnet"))
                model = next(c for c in upd["controls"] if c["id"] == "model")
                assert [o["value"] for o in model["options"]] == [m["value"] for m in CLI_MODELS]
                assert model["options"][2]["description"] == CLI_MODELS[2]["description"]

        await conv.close()
        await _wait_terminal(optio, "cc-conv-fallback")
    finally:
        await optio.shutdown(grace_seconds=1.0)
