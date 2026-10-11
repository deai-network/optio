"""Custom session controls end to end (real engine, fake claude).

A task's custom controls (``ClaudeCodeTaskConfig.custom_controls``): a fresh
launch runs each control's default over the config's own model / effort /
permission mode; widgetData and every control update carry
``customControls`` with the selection derived from the running values;
``POST /control {id: "custom:<id>", value}`` switches claude live, refused
while a turn runs (Review Focus 1); a built-in change clears the selection
and a custom pick moves the built-ins (Review Focus 2); a resume keeps the
pick (Review Focus 3).
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import aiohttp
import pytest

from optio_agents import CustomControl
from optio_claudecode import create_claudecode_task

from .test_conversation_ui_session import (
    _basic,
    _controls,
    _has_control,
    _log_lines,
    _make_optio,
    _plant_transcript_on,
    _post_control,
    _read_until,
    _remember_workdir,
    _replies,
    _ui_config,
    _wait_terminal,
    _wait_widget_data,
    _wait_widget_upstream,
)
from .test_custom_controls import ACCESS, SPEED

pytestmark = pytest.mark.serial

SPEED_LABEL = {"en": "Model", "hu": "Modell", "de": "Modell"}
# The data chat's control, defaulting to Fast instead.
SPEED_FAST_FIRST = CustomControl(id="speed", label=SPEED_LABEL, default="fast", options=SPEED.options)


def _custom(event: dict, control_id: str = "speed"):
    """The custom control's selection in a control update (KeyError: none)."""
    return next(c for c in event["customControls"] if c["id"] == control_id)["value"]


def _custom_update(control_id: str, value):
    def match(event: dict) -> bool:
        return (event.get("type") == "x-optio-control-update"
                and any(c["id"] == control_id and c["value"] == value
                        for c in event.get("customControls") or []))
    return match


def _spawned(argv_log: pathlib.Path) -> list[list[str]]:
    """The conversation launches (not the model probes), in order."""
    return [argv for argv in _log_lines(argv_log) if "--replay-user-messages" in argv]


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


MARK = "x-test-mark"


def _next_init(conv) -> asyncio.Event:
    """Set when the conversation next dispatches a system/init (claude starts
    every turn with one)."""
    seen = asyncio.Event()

    def watch(event: dict) -> None:
        if event.get("type") == "system" and event.get("subtype") == "init":
            seen.set()
    conv.on_event(watch)
    return seen


async def _settled(conv) -> None:
    """Every system/init names the permission mode, and the session body
    re-emits the controls for it (an x-optio-control-update). Wait until it
    has handled the last one, then put a marker through the event stream:
    whatever the body emitted is ahead of it, so the next control update
    after the marker comes from something else."""
    while conv.permission_mode_observed.is_set():
        await asyncio.sleep(0.01)
    conv.emit_event({"type": MARK})


async def _pick(session, url: str, headers: dict, control_id: str, value) -> tuple[int, dict]:
    async with session.post(f"{url}/control", headers=headers,
                            json={"id": f"custom:{control_id}", "value": value},
                            timeout=aiohttp.ClientTimeout(total=30)) as r:
        return r.status, await r.json()


async def test_a_fresh_launch_runs_the_defaults_over_the_configs_own_values(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    tmp_path: pathlib.Path,
):
    """The config says haiku / low / bypassPermissions; the controls' defaults
    (Extensive, Edits) win at launch, widgetData shows them selected (labels
    unresolved) and the built-ins show the same values. A built-in pick of
    the config's own mode is kept across a resume (it is not the launch mode)."""
    argv_log = tmp_path / "argv.jsonl"
    seen: dict = {}
    optio = await _make_optio(mongo_db, "cccc-defaults")
    try:
        task = create_claudecode_task(
            process_id="cc-cc-defaults", name="Custom defaults",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, show_session_controls=True,
                model="haiku", reasoning_effort="low",
                custom_controls=[SPEED, ACCESS],
                supports_resume=True, credentials_json={"token": "test"},
                before_execute=_plant_and_remember(seen),
                env={"FAKE_CLAUDE_ARGV_LOG": str(argv_log),
                     "FAKE_CLAUDE_REPLY": "{model} {effort}"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-cc-defaults", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-cc-defaults"))["widgetUpstream"]
        proc = await _wait_widget_data(optio, "cc-cc-defaults")
        assert proc["widgetData"]["customControls"] == [
            {"id": "speed", "label": SPEED_LABEL, "description": None,
             "options": [{"value": "fast", "label": "Fast", "description": None},
                         {"value": "extensive", "label": "Extensive", "description": None}],
             "value": "extensive"},
            {"id": "access", "label": "Access", "description": {"en": "What may Claude do?"},
             "options": [{"value": "edits", "label": "Edits",
                          "description": "Edits files without asking"},
                         {"value": "auto", "label": {"en": "Auto", "hu": "Automatikus"},
                          "description": None}],
             "value": "edits"},
        ]
        controls = _controls(proc)
        assert controls["model"]["value"] == "opus"
        assert controls["permission_mode"]["value"] == "acceptEdits"
        texts, replied = _replies(conv)
        await conv.send("ping")
        await asyncio.wait_for(replied.wait(), 60)
        assert texts == ["opus xhigh"]

        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                await _post_control(session, url, headers, "permission_mode", "bypassPermissions")
                upd = await _read_until(
                    events, lambda e: _has_control(e, "permission_mode", "bypassPermissions"))
                assert (_custom(upd, "speed"), _custom(upd, "access")) == ("extensive", None)
        picks = json.loads((pathlib.Path(seen["workdir"]) / "home/.claude/optio-picks.json").read_text())
        assert picks == {"permission_mode": "bypassPermissions"}
        await conv.close()
        await _wait_terminal(optio, "cc-cc-defaults")

        conv2 = await optio.launch_and_await_result(
            "cc-cc-defaults", resume=True, session_id=None, timeout=60,
        )
        proc = await _wait_widget_data(optio, "cc-cc-defaults")
        assert [c["value"] for c in proc["widgetData"]["customControls"]] == ["extensive", None]
        assert _controls(proc)["permission_mode"]["value"] == "bypassPermissions"
        await conv2.close()
        await _wait_terminal(optio, "cc-cc-defaults")
    finally:
        await optio.shutdown(grace_seconds=1.0)

    first, resumed = _spawned(argv_log)
    assert (_flag(first, "--model"), _flag(first, "--effort"), _flag(first, "--permission-mode")) == (
        "opus", "xhigh", "acceptEdits")
    assert "--allow-dangerously-skip-permissions" in first      # the config allows bypass
    assert "--continue" in resumed
    assert (_flag(resumed, "--model"), _flag(resumed, "--effort"),
            _flag(resumed, "--permission-mode")) == ("opus", "xhigh", "bypassPermissions")


def _plant_and_remember(into: dict):
    plant, remember = _plant_transcript_on("claude-opus-5[1m]"), _remember_workdir(into)

    async def before(hook_ctx):
        await plant(hook_ctx)
        await remember(hook_ctx)
    return before


async def test_a_custom_pick_moves_the_built_ins_and_a_built_in_change_clears_it(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    tmp_path: pathlib.Path,
):
    """Review Focus 2: picking Fast switches claude live with ONE
    apply_flag_settings (sonnet / medium), the built-in select and slider
    follow; a manual model change leaves no option matching (null); coming
    back to Fast's values selects it again. The picks are kept and the next
    turn runs on the picked model."""
    control_log = tmp_path / "control.jsonl"
    seen: dict = {}
    optio = await _make_optio(mongo_db, "cccc-builtins")
    try:
        task = create_claudecode_task(
            process_id="cc-cc-builtins", name="Custom and built-in",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, show_session_controls=True,
                custom_controls=[SPEED], before_execute=_remember_workdir(seen),
                env={"FAKE_CLAUDE_CONTROL_LOG": str(control_log),
                     "FAKE_CLAUDE_REPLY": "{model} {effort}"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-cc-builtins", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-cc-builtins"))["widgetUpstream"]
        proc = await _wait_widget_data(optio, "cc-cc-builtins")
        assert proc["widgetData"]["customControls"][0]["value"] == "extensive"
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                assert await _pick(session, url, headers, "speed", "fast") == (200, {"ok": True})
                upd = await _read_until(events, _custom_update("speed", "fast"))
                values = {c["id"]: c["value"] for c in upd["controls"]}
                assert (values["model"], values["reasoning_effort"]) == ("sonnet", "medium")

                await _post_control(session, url, headers, "model", "haiku")
                upd = await _read_until(events, lambda e: _has_control(e, "model", "haiku"))
                assert _custom(upd) is None

                await _post_control(session, url, headers, "model", "sonnet")
                upd = await _read_until(events, lambda e: _has_control(e, "model", "sonnet"))
                assert _custom(upd) == "fast"
        picks = json.loads((pathlib.Path(seen["workdir"]) / "home/.claude/optio-picks.json").read_text())
        assert picks == {"model": "sonnet", "model_resolved": "claude-sonnet-5", "effort": "medium"}
        texts, replied = _replies(conv)
        await conv.send("ping")
        await asyncio.wait_for(replied.wait(), 60)
        assert texts == ["claude-sonnet-5 medium"]
        await conv.close()
        await _wait_terminal(optio, "cc-cc-builtins")
    finally:
        await optio.shutdown(grace_seconds=1.0)

    requests = _log_lines(control_log)
    assert [r["settings"] for r in requests if r["subtype"] == "apply_flag_settings"] == [
        {"model": "sonnet", "effortLevel": "medium"},          # Fast: one request
        {"model": "haiku", "effortLevel": "medium"},
        {"model": "sonnet", "effortLevel": "medium"},
    ]
    assert not any(r["subtype"] == "set_permission_mode" for r in requests)


async def test_a_custom_pick_while_claude_answers_is_refused_and_changes_nothing(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    tmp_path: pathlib.Path,
):
    """Review Focus 1, on a data-chat-like task (no controls bar): while a
    turn runs a pick is 409 busy, nothing reaches claude, and the widget gets
    the unchanged selection back; once the turn is over the pick goes
    through. The built-in ids stay refused (the bar is off)."""
    control_log = tmp_path / "control.jsonl"
    optio = await _make_optio(mongo_db, "cccc-busy")
    try:
        task = create_claudecode_task(
            process_id="cc-cc-busy", name="Pick while busy",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, custom_controls=[SPEED],
                env={"FAKE_CLAUDE_HOLD_TURN": "1",
                     "FAKE_CLAUDE_CONTROL_LOG": str(control_log)},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-cc-busy", session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, "cc-cc-busy"))["widgetUpstream"]
        proc = await _wait_widget_data(optio, "cc-cc-busy")
        assert proc["widgetData"]["showSessionControls"] is False
        assert proc["widgetData"]["customControls"][0]["value"] == "extensive"
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{url}/control", headers=headers,
                                    json={"id": "model", "value": "sonnet"}) as r:
                assert r.status == 403
            async with session.get(f"{url}/events", headers=headers) as events:
                turn_started = _next_init(conv)
                await conv.send("a question claude takes its time over")
                await asyncio.wait_for(turn_started.wait(), 60)
                await _settled(conv)
                await _read_until(events, lambda e: e.get("type") == MARK)
                assert await _pick(session, url, headers, "speed", "fast") == (
                    409, {"ok": False, "reason": "busy"})
                # The widget patched its selection optimistically: the
                # unchanged one comes back.
                upd = await _read_until(events, lambda e: e.get("type") == "x-optio-control-update")
                assert _custom(upd) == "extensive"
                assert (conv.model, conv.effort) == ("opus", "xhigh")

                await conv.interrupt()
                await _read_until(events, lambda e: e.get("type") == "result")
                assert await _pick(session, url, headers, "speed", "fast") == (200, {"ok": True})
                await _read_until(events, _custom_update("speed", "fast"))
        await conv.close()
        await _wait_terminal(optio, "cc-cc-busy")
    finally:
        await optio.shutdown(grace_seconds=1.0)

    applied = [r for r in _log_lines(control_log) if r["subtype"] == "apply_flag_settings"]
    assert applied == [{"subtype": "apply_flag_settings",
                        "settings": {"model": "sonnet", "effortLevel": "medium"}}]


async def test_a_resume_keeps_the_custom_pick_and_its_selection(
    shim_install_dir: pathlib.Path,
    claude_cache_dir: pathlib.Path,
    task_root,
    mongo_db,
    tmp_path: pathlib.Path,
):
    """Review Focus 3. Run 1 (fresh) starts on the default, Fast; run 2
    (resume, nothing picked) still does, and the operator picks Extensive:
    `opus`, which the CLI's list (launched on sonnet) does not name, but the
    task does. Run 3 (resume) comes back on opus / xhigh, Extensive selected,
    and its turn runs on them."""
    argv_log = tmp_path / "argv.jsonl"
    optio = await _make_optio(mongo_db, "cccc-resume")
    try:
        task = create_claudecode_task(
            process_id="cc-cc-resume", name="Custom pick across a resume",
            config=_ui_config(
                shim_install_dir, claude_cache_dir, custom_controls=[SPEED_FAST_FIRST],
                supports_resume=True, credentials_json={"token": "test"},
                before_execute=_plant_transcript_on("claude-opus-5[1m]"),
                env={"FAKE_CLAUDE_ARGV_LOG": str(argv_log),
                     "FAKE_CLAUDE_REPLY": "{model} {effort}"},
            ),
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result("cc-cc-resume", session_id=None, timeout=60)
        proc = await _wait_widget_data(optio, "cc-cc-resume")
        assert proc["widgetData"]["customControls"][0]["value"] == "fast"
        await conv.close()
        await _wait_terminal(optio, "cc-cc-resume")

        conv = await optio.launch_and_await_result(
            "cc-cc-resume", resume=True, session_id=None, timeout=60,
        )
        upstream = (await _wait_widget_upstream(optio, "cc-cc-resume"))["widgetUpstream"]
        proc = await _wait_widget_data(optio, "cc-cc-resume")
        assert proc["widgetData"]["customControls"][0]["value"] == "fast"
        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                # The resume notice's turn first.
                await _read_until(events, lambda e: e.get("type") == "result")
                assert await _pick(session, url, headers, "speed", "extensive") == (
                    200, {"ok": True})
                await _read_until(events, _custom_update("speed", "extensive"))
        await conv.close()
        await _wait_terminal(optio, "cc-cc-resume")

        conv = await optio.launch_and_await_result(
            "cc-cc-resume", resume=True, session_id=None, timeout=60,
        )
        proc = await _wait_widget_data(optio, "cc-cc-resume")
        assert proc["widgetData"]["customControls"][0]["value"] == "extensive"
        texts, replied = _replies(conv)
        await conv.send("ping")
        await asyncio.wait_for(replied.wait(), 60)
        assert texts[-1] == "opus xhigh"
        await conv.close()
        await _wait_terminal(optio, "cc-cc-resume")
    finally:
        await optio.shutdown(grace_seconds=1.0)

    fresh, resumed, resumed_again = _spawned(argv_log)
    assert (_flag(fresh, "--model"), _flag(fresh, "--effort")) == ("sonnet", "medium")
    assert (_flag(resumed, "--model"), _flag(resumed, "--effort")) == ("sonnet", "medium")
    assert (_flag(resumed_again, "--model"), _flag(resumed_again, "--effort")) == ("opus", "xhigh")
    assert "--continue" in resumed and "--continue" in resumed_again
