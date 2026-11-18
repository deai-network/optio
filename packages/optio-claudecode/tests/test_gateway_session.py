"""Gateway mode in the conversation session (Claude Code + Requesty).

Real engine and claude shim, as in test_conversation_ui_session.py: the
session reads the gateway seed, the child gets the seed's environment (the
token over stdin, never argv or a file), the switcher offers the seed's usable
models with their effort levels, and no OAuth path runs. A resume runs the
saved pick's exact id while it is usable, and the snapshot guard of later runs
(rescue, Resurrect) takes the gateway marker.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import pathlib
from dataclasses import replace

import aiohttp
import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_core.exceptions import ResultNotPublished
from optio_host import LocalHost

from optio_claudecode import (
    REQUESTY_SEED_SUFFIX,
    ClaudeCodeRequestyTaskConfig,
    create_claudecode_requesty_task,
)
from optio_claudecode import host_actions
from optio_claudecode import picks as cc_picks
from optio_claudecode import resurrect
from optio_claudecode import session as session_mod
from optio_claudecode import snapshots
from optio_claudecode.gateway import (
    GatewayModel,
    gateway_launch_env,
    update_gateway_seed,
    write_gateway_seed,
)
from optio_claudecode.gateways import requesty

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
    _wait_terminal,
    _wait_widget_data,
    _wait_widget_upstream,
)


# Live sessions sharing the claude cache paths: the non-parallel phase.
pytestmark = pytest.mark.serial

TOKEN = "rq-live-token-7f3a9c2e5b1d"
KIMI = GatewayModel("prov-a/kimi-k3.1-pro", "prov-a/kimi-k3.1-pro", "pinned", ("low", "high"), status="ok")
GLM = GatewayModel("glm-flash", "glm-flash", "managed", None, status="ok")
BROKEN = GatewayModel("prov-b/broken", "prov-b/broken", "pinned", ("high",), status="failed",
                      reason="no answer")
MODELS = (KIMI, GLM, BROKEN)
ALL_FAILED = tuple(replace(m, status="failed", reason="no answer") for m in MODELS)


def _cipher(data: bytes) -> bytes:
    """The seed cipher of these tests (its own inverse): the session must read
    the seed through config.seed_decrypt."""
    return data[::-1]


def _seed(token: str = TOKEN, models=MODELS):
    return requesty.new_seed(token=token, region="eu", models=models, default_model=KIMI.id)


async def _gw_config(
    mongo_db, prefix: str, shim, cache, *, status: str = "alive", models=MODELS,
    token: str = TOKEN, **cfg,
) -> ClaudeCodeRequestyTaskConfig:
    """Write a gateway seed under ``prefix``, and the task's config naming it
    (``cfg`` overrides, ``seed_id`` included)."""
    sid = await write_gateway_seed(
        mongo_db, prefix=prefix, suffix=REQUESTY_SEED_SUFFIX,
        seed=_seed(token, models), encrypt=_cipher, status=status,
    )
    return ClaudeCodeRequestyTaskConfig(**{
        "consumer_instructions": "Converse with the test.",
        "conversation_ui": True,
        "show_session_controls": True,
        "permission_mode": "bypassPermissions",
        "fs_isolation": False,
        "install_dir": str(cache),
        "ttyd_install_dir": str(shim),
        "seed_id": sid,
        "seed_blob_encrypt": _cipher,
        "seed_blob_decrypt": _cipher,
        **cfg,
    })


async def _gw_task(mongo_db, prefix: str, pid: str, shim, cache, **kw):
    config = await _gw_config(mongo_db, prefix, shim, cache, **kw)
    return create_claudecode_requesty_task(process_id=pid, name="Gateway session", config=config)


def _files_containing(root: pathlib.Path, needle: str) -> list[str]:
    """The regular files under ``root`` whose bytes hold ``needle``, relative
    to it. Symlinks are skipped (the claude cache is linked in)."""
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = pathlib.Path(dirpath, name)
            if path.is_symlink() or not path.is_file():
                continue
            if needle.encode() in path.read_bytes():
                hits.append(str(path.relative_to(root)))
    return hits


async def _snapshot_blobs(mongo_db, prefix: str, pid: str) -> list[bytes]:
    """Every blob of every snapshot of ``pid``: the session and workdir tars
    gunzipped, the conversation record as stored."""
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    out = []
    async for snap in snapshots._collection(mongo_db, prefix).find({"processId": pid}):
        for key, gzipped in (("sessionBlobId", True), ("workdirBlobId", True),
                             ("conversationBlobId", False)):
            if snap.get(key) is None:
                continue
            data = await (await bucket.open_download_stream(snap[key])).read()
            out.append(gzip.decompress(data) if gzipped else data)
    return out


@pytest.mark.asyncio
async def test_a_gateway_session_gets_the_seed_in_its_environment_only(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, monkeypatch, tmp_path, caplog,
):
    """RF1: the child's environment holds the seed's values and the token,
    the inherited Anthropic key and OAuth token are gone; argv, the workdir,
    the snapshot, the process record and the log hold no token; the snapshot
    guard takes the gateway marker."""
    caplog.set_level(logging.DEBUG)
    # The driver's own command log (binary payloads base64'd) is not ours.
    caplog.set_level(logging.WARNING, logger="pymongo")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "inherited-oauth")
    env_log, argv_log = tmp_path / "env.jsonl", tmp_path / "argv.jsonl"
    seen: dict = {}
    pid = "gw-env"
    optio = await _make_optio(mongo_db, "gwenv")
    try:
        task = await _gw_task(
            mongo_db, "gwenv", pid, shim_install_dir, claude_cache_dir,
            supports_resume=True, before_execute=_remember_workdir(seen),
            env={"FAKE_CLAUDE_ENV_LOG": str(env_log), "FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)

        env, argv = _log_lines(env_log)[0], _log_lines(argv_log)[0]
        assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
        assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert env["ANTHROPIC_BASE_URL"] == "https://router.eu.requesty.ai"
        assert {env[f"ANTHROPIC_DEFAULT_{f}_MODEL"]
                for f in ("OPUS", "SONNET", "HAIKU", "FABLE")} == {KIMI.id}
        assert (env["CLAUDE_CODE_AUTO_MODE_SERVER"], env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"],
                env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"]) == ("0", "1", "1")
        assert "CLAUDE_CODE_EFFORT_LEVEL" not in env
        assert argv[argv.index("--model") + 1] == KIMI.id
        assert argv[argv.index("--effort") + 1] == "high"
        # The non-secret gateway values, pinned above every settings file.
        assert json.loads(argv[argv.index("--settings") + 1]) == {"env": gateway_launch_env(_seed())}
        assert TOKEN not in json.dumps(argv)

        workdir = pathlib.Path(seen["workdir"])
        assert _files_containing(workdir, TOKEN) == []
        assert not (workdir / "home/.claude/.credentials.json").exists()
        assert (workdir / "home/.claude/.optio-gateway").read_text() == "gateway\n"

        await conv.close()
        proc = await _wait_terminal(optio, pid)
        assert proc["status"]["state"] == "done"
        assert proc["hasSavedState"] is True            # the marker passed the guard
        blobs = await _snapshot_blobs(mongo_db, "gwenv", pid)
        assert len(blobs) == 3                          # session, workdir, conversation
        for blob in blobs:
            assert TOKEN.encode() not in blob
        assert TOKEN not in json.dumps(proc, default=str)
    finally:
        await optio.shutdown(grace_seconds=1.0)
    assert TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_the_switcher_offers_the_seeds_usable_models(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """RF3: the model select offers the usable models, the effort slider the
    selected model's levels; a model without levels is switched to without an
    effort, one outside the seed's usable models is refused, and switching
    back to a model with levels sends its preselected level."""
    control_log = tmp_path / "control.jsonl"
    pid = "gw-switch"
    optio = await _make_optio(mongo_db, "gwswitch")
    try:
        task = await _gw_task(
            mongo_db, "gwswitch", pid, shim_install_dir, claude_cache_dir,
            env={"FAKE_CLAUDE_CONTROL_LOG": str(control_log)},
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        upstream = (await _wait_widget_upstream(optio, pid))["widgetUpstream"]
        controls = _controls(await _wait_widget_data(optio, pid))
        assert [o["value"] for o in controls["model"]["options"]] == [KIMI.id, GLM.id]
        assert controls["model"]["value"] == KIMI.id
        assert controls["reasoning_effort"]["levels"] == ["low", "high"]
        assert controls["reasoning_effort"]["value"] == "high"

        url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/events", headers=headers) as events:
                await _post_control(session, url, headers, "model", GLM.id)
                upd = await _read_until(events, lambda e: _has_control(e, "model", GLM.id))
                assert "reasoning_effort" not in {c["id"] for c in upd["controls"]}
                for refused in (BROKEN.id, "claude-opus-5"):
                    async with session.post(f"{url}/control", headers=headers,
                                            json={"id": "model", "value": refused}) as r:
                        assert r.status == 400
                await _post_control(session, url, headers, "model", KIMI.id)
                upd = await _read_until(events, lambda e: _has_control(e, "model", KIMI.id))
                assert {c["id"]: c["value"] for c in upd["controls"]}["reasoning_effort"] == "high"
        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    assert [r for r in _log_lines(control_log) if r["subtype"] == "apply_flag_settings"] == [
        {"subtype": "apply_flag_settings", "settings": {"model": GLM.id}},
        {"subtype": "apply_flag_settings", "settings": {"model": KIMI.id, "effortLevel": "high"}},
    ]


@pytest.mark.asyncio
async def test_no_oauth_path_runs(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, monkeypatch, tmp_path,
):
    """No credentials watcher, no save-back, no seed merge, and no
    initialize: the catalog comes from the seed."""
    calls: dict[str, list] = {"watcher": [], "save_back": [], "merge_seed": []}

    def _recorder(name: str):
        async def record(*args, **kwargs):
            calls[name].append(kwargs)
        return record

    monkeypatch.setattr(session_mod.cred_watcher, "run_credential_watcher", _recorder("watcher"))
    monkeypatch.setattr(session_mod.cred_watcher, "save_back_if_changed", _recorder("save_back"))
    monkeypatch.setattr(session_mod._seeds, "merge_seed", _recorder("merge_seed"))
    control_log = tmp_path / "control.jsonl"
    pid = "gw-no-oauth"
    optio = await _make_optio(mongo_db, "gwnooauth")
    try:
        task = await _gw_task(
            mongo_db, "gwnooauth", pid, shim_install_dir, claude_cache_dir,
            supports_resume=True, env={"FAKE_CLAUDE_CONTROL_LOG": str(control_log)},
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)
        await conv.close()
        proc = await _wait_terminal(optio, pid)
        assert proc["status"]["state"] == "done"
    finally:
        await optio.shutdown(grace_seconds=1.0)

    assert calls == {"watcher": [], "save_back": [], "merge_seed": []}
    subtypes = [r["subtype"] for r in _log_lines(control_log)]
    assert "get_settings" in subtypes            # claude was asked, just not initialize
    assert "initialize" not in subtypes


_NAME = "Claude Code + Requesty"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("pending", _NAME + ": gateway seed {sid} is still being tested"),
        ("no_seed", _NAME + ": no gateway seed configured"),
        ("unknown", _NAME + ": unknown gateway seed {sid}"),
        ("all_failed", _NAME + ": gateway seed {sid} has no usable model (run Test again)"),
        ("dead", _NAME + ": gateway seed {sid} has no usable model (run Test again)"),
    ],
)
@pytest.mark.asyncio
async def test_a_launch_refuses_a_pending_missing_or_dead_seed(
    case, expected, shim_install_dir, claude_cache_dir, task_root, mongo_db,
):
    """RF2: a seed still being tested, none, an unknown id, or one without a
    usable model never reaches a launch; the run fails saying which."""
    kw: dict = {
        "pending": {"status": "pending"},
        "no_seed": {"seed_id": None},
        "unknown": {"seed_id": str(ObjectId())},
        "all_failed": {"models": ALL_FAILED},
        "dead": {"status": "dead", "models": ALL_FAILED},
    }[case]
    pid = f"gw-refuse-{case}"
    optio = await _make_optio(mongo_db, "gwrefuse")
    try:
        config = await _gw_config(mongo_db, "gwrefuse", shim_install_dir, claude_cache_dir, **kw)
        await optio.adhoc_define(
            create_claudecode_requesty_task(process_id=pid, name="Refused", config=config),
        )
        with pytest.raises(ResultNotPublished):
            await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        proc = await _wait_terminal(optio, pid)
        assert proc["status"]["state"] == "failed"
        assert expected.format(sid=config.seed_id) in (proc["status"]["error"] or "")
        assert TOKEN not in json.dumps(proc, default=str)
    finally:
        await optio.shutdown(grace_seconds=1.0)


@pytest.mark.asyncio
async def test_scrub_env_never_removes_a_gateway_variable(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """Ruling A(c): the gateway environment is applied after the scrub, so
    scrub_env patterns that match every gateway variable remove none."""
    env_log = tmp_path / "env.jsonl"
    pid = "gw-scrub"
    optio = await _make_optio(mongo_db, "gwscrub")
    try:
        task = await _gw_task(
            mongo_db, "gwscrub", pid, shim_install_dir, claude_cache_dir,
            scrub_env=["ANTHROPIC_*", "CLAUDE_CODE_*"],
            env={"FAKE_CLAUDE_ENV_LOG": str(env_log)},
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)
        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    env = _log_lines(env_log)[0]
    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
    expected = gateway_launch_env(_seed())
    assert {k: env.get(k) for k in expected} == expected


@pytest.mark.asyncio
async def test_the_gateway_environment_is_merged_last(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """Ruling A(d): were a gateway key in config.env (the config refuses it;
    set here past the refusal), the gateway's value still reaches the child."""
    env_log = tmp_path / "env.jsonl"
    pid = "gw-last"
    optio = await _make_optio(mongo_db, "gwlast")
    try:
        config = await _gw_config(mongo_db, "gwlast", shim_install_dir, claude_cache_dir)
        object.__setattr__(config, "env", {
            "FAKE_CLAUDE_ENV_LOG": str(env_log),
            "ANTHROPIC_BASE_URL": "https://elsewhere.invalid",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "0",
            "CLAUDE_CODE_AUTO_MODE_SERVER": "1",
        })
        await optio.adhoc_define(
            create_claudecode_requesty_task(process_id=pid, name="Merged last", config=config),
        )
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)
        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    env = _log_lines(env_log)[0]
    expected = gateway_launch_env(_seed())
    assert {k: env.get(k) for k in expected} == expected
    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN


@pytest.mark.asyncio
async def test_a_failed_secret_write_stops_the_launched_shell(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, monkeypatch,
):
    """A token the stdin line cannot carry (a line break) is refused before
    anything is written, the launched shell (waiting on its first stdin line)
    is stopped, and the run fails without the token in its error."""
    terminated = []
    terminate = LocalHost.terminate_subprocess

    async def recording(self, handle, **kwargs):
        terminated.append(handle)
        await terminate(self, handle, **kwargs)

    monkeypatch.setattr(LocalHost, "terminate_subprocess", recording)
    pid = "gw-bad-token"
    optio = await _make_optio(mongo_db, "gwbadtoken")
    try:
        task = await _gw_task(
            mongo_db, "gwbadtoken", pid, shim_install_dir, claude_cache_dir,
            token="rq-first-line\nrq-second-line",
        )
        await optio.adhoc_define(task)
        with pytest.raises(ResultNotPublished):
            await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        proc = await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    error = proc["status"]["error"] or ""
    assert proc["status"]["state"] == "failed"
    assert "line break" in error
    assert "rq-first-line" not in error and "rq-second-line" not in error
    assert len(terminated) == 1
    assert terminated[0].pid_like.returncode is not None


# --- resume, rescue and Resurrect -------------------------------------------


async def _run_switched_to(optio, pid: str, model: str) -> None:
    """A first run that switches to ``model`` live, then closes: the picks
    file (saved before the switch's controls update goes out) keeps it for
    the resume."""
    conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
    upstream = (await _wait_widget_upstream(optio, pid))["widgetUpstream"]
    await _wait_widget_data(optio, pid)
    url, headers = upstream["url"], _basic(upstream["innerAuth"]["password"])
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{url}/events", headers=headers) as events:
            await _post_control(session, url, headers, "model", model)
            await _read_until(events, lambda e: _has_control(e, "model", model))
    await conv.close()
    proc = await _wait_terminal(optio, pid)
    assert proc["status"]["state"] == "done"


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


async def _log_message(optio, pid: str, needle: str, timeout: float = 60.0) -> str | None:
    """The first process log message holding ``needle``, polled for (the log
    is written behind report_progress); None after ``timeout``."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        proc = await optio.get_process(pid)
        found = [e["message"] for e in (proc or {}).get("log", [])
                 if needle in (e.get("message") or "")]
        if found or loop.time() > end:
            return found[0] if found else None
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_a_resumed_gateway_session_runs_the_model_it_was_switched_to(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """RF4: the model the operator switched to comes back on a resume as the
    exact id, with --continue and never the `default` alias; a model without
    levels is launched without an effort, and the switcher shows it with no
    effort control."""
    argv_log = tmp_path / "argv.jsonl"
    pid = "gw-resume"
    optio = await _make_optio(mongo_db, "gwresume")
    try:
        task = await _gw_task(
            mongo_db, "gwresume", pid, shim_install_dir, claude_cache_dir,
            supports_resume=True, before_execute=_plant_transcript_on(KIMI.id),
            env={"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        await optio.adhoc_define(task)
        await _run_switched_to(optio, pid, GLM.id)

        conv = await optio.launch_and_await_result(pid, resume=True, session_id=None, timeout=60)
        controls = _controls(await _wait_widget_data(optio, pid))
        assert controls["model"]["value"] == GLM.id
        assert "reasoning_effort" not in controls
        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    # One launch per run: gateway mode asks no model probe.
    fresh, resumed = _log_lines(argv_log)
    assert "--continue" not in fresh
    assert "--continue" in resumed
    assert _flag(resumed, "--model") == GLM.id
    assert "--effort" not in resumed
    assert "default" not in resumed


@pytest.mark.asyncio
async def test_a_resumed_gateway_session_whose_model_failed_runs_the_default(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """RF4: the saved pick failed its Test again since: the resume runs the
    effective default model with its preselected effort, and says why."""
    argv_log = tmp_path / "argv.jsonl"
    pid = "gw-resume-failed"
    optio = await _make_optio(mongo_db, "gwresumefailed")
    try:
        config = await _gw_config(
            mongo_db, "gwresumefailed", shim_install_dir, claude_cache_dir,
            supports_resume=True, before_execute=_plant_transcript_on(KIMI.id),
            env={"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        await optio.adhoc_define(
            create_claudecode_requesty_task(process_id=pid, name="Failed pick", config=config),
        )
        await _run_switched_to(optio, pid, GLM.id)
        failed = replace(GLM, status="failed", reason="no answer")
        await update_gateway_seed(
            mongo_db, prefix="gwresumefailed", suffix=REQUESTY_SEED_SUFFIX,
            seed_id=config.seed_id, seed=_seed(models=(KIMI, failed, BROKEN)), encrypt=_cipher,
        )

        conv = await optio.launch_and_await_result(pid, resume=True, session_id=None, timeout=60)
        controls = _controls(await _wait_widget_data(optio, pid))
        assert [o["value"] for o in controls["model"]["options"]] == [KIMI.id]
        assert controls["model"]["value"] == KIMI.id
        assert controls["reasoning_effort"]["value"] == "high"
        assert await _log_message(optio, pid, "is not usable") == (
            f"{_NAME} runs {KIMI.id}: {GLM.id} is not usable in this gateway configuration"
        )
        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    _fresh, resumed = _log_lines(argv_log)
    assert "--continue" in resumed
    assert (_flag(resumed, "--model"), _flag(resumed, "--effort")) == (KIMI.id, "high")
    assert "default" not in resumed


def _plant_and_remember(into: dict):
    """before_execute: the transcript a resume continues, and the workdir (to
    read the picks file)."""
    plant, remember = _plant_transcript_on(KIMI.id), _remember_workdir(into)

    async def before(hook_ctx):
        await plant(hook_ctx)
        await remember(hook_ctx)
    return before


@pytest.mark.asyncio
async def test_a_fallback_from_an_unusable_pick_becomes_the_saved_pick(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """RF4, as in non-gateway mode: the model a resume falls back to (with its
    effort) replaces the unusable saved pick, so what the switcher shows is
    what runs, and a later resume does not return to the old model even once
    a Test again has made it usable again."""
    argv_log = tmp_path / "argv.jsonl"
    seen: dict = {}
    pid = "gw-fallback-saved"
    optio = await _make_optio(mongo_db, "gwfallbacksaved")
    try:
        config = await _gw_config(
            mongo_db, "gwfallbacksaved", shim_install_dir, claude_cache_dir,
            supports_resume=True, before_execute=_plant_and_remember(seen),
            env={"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        await optio.adhoc_define(
            create_claudecode_requesty_task(process_id=pid, name="Fallback saved", config=config),
        )

        async def _test_again(glm: GatewayModel) -> None:
            await update_gateway_seed(
                mongo_db, prefix="gwfallbacksaved", suffix=REQUESTY_SEED_SUFFIX,
                seed_id=config.seed_id, seed=_seed(models=(KIMI, glm, BROKEN)), encrypt=_cipher,
            )

        await _run_switched_to(optio, pid, GLM.id)
        await _test_again(replace(GLM, status="failed", reason="no answer"))

        conv = await optio.launch_and_await_result(pid, resume=True, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)
        saved = cc_picks.loads(pathlib.Path(seen["workdir"], cc_picks.PICKS_RELPATH).read_text())
        assert (saved.model, saved.effort) == (KIMI.id, "high")
        await conv.close()
        await _wait_terminal(optio, pid)

        await _test_again(GLM)              # usable again
        conv = await optio.launch_and_await_result(pid, resume=True, session_id=None, timeout=60)
        controls = _controls(await _wait_widget_data(optio, pid))
        assert [o["value"] for o in controls["model"]["options"]] == [KIMI.id, GLM.id]
        assert controls["model"]["value"] == KIMI.id
        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    _fresh, fallback, later = _log_lines(argv_log)
    for argv in (fallback, later):
        assert "--continue" in argv
        assert (_flag(argv, "--model"), _flag(argv, "--effort")) == (KIMI.id, "high")


@pytest.mark.asyncio
async def test_a_resume_strips_gateway_keys_from_the_restored_settings_files(
    shim_install_dir, claude_cache_dir, task_root, mongo_db, tmp_path,
):
    """Important 2 (a): the agent can write the settings files Claude Code
    reads (HOME is <workdir>/home, the cwd is the workdir), and their env is
    applied over the process's. A block written during an earlier run comes
    back with the snapshot; the resume removes the gateway's keys from it
    after the restore and after claude_config, leaving every other key, and
    the launch pins the gateway's values with --settings."""
    argv_log = tmp_path / "argv.jsonl"
    seen: dict = {}
    pid = "gw-settings-strip"
    optio = await _make_optio(mongo_db, "gwstrip")
    planted = {"cleanupPeriodDays": 7, "env": {
        "FOO": "bar",
        "ANTHROPIC_BASE_URL": "https://elsewhere.invalid",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
        "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "0",
    }}
    try:
        task = await _gw_task(
            mongo_db, "gwstrip", pid, shim_install_dir, claude_cache_dir,
            supports_resume=True, before_execute=_plant_and_remember(seen),
            claude_config={"theme": "light"},
            env={"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        await optio.adhoc_define(task)
        conv = await optio.launch_and_await_result(pid, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)
        # What a prompt-injected agent could write during the session.
        workdir = pathlib.Path(seen["workdir"])
        for rel in host_actions.AGENT_SETTINGS_FILES:
            (workdir / rel).parent.mkdir(parents=True, exist_ok=True)
            (workdir / rel).write_text(json.dumps(planted))
        await conv.close()
        proc = await _wait_terminal(optio, pid)
        assert proc["status"]["state"] == "done" and proc["hasSavedState"] is True

        conv = await optio.launch_and_await_result(pid, resume=True, session_id=None, timeout=60)
        await _wait_widget_data(optio, pid)
        workdir = pathlib.Path(seen["workdir"])
        restored = {rel: json.loads((workdir / rel).read_text())
                    for rel in host_actions.AGENT_SETTINGS_FILES}
        await conv.close()
        await _wait_terminal(optio, pid)
    finally:
        await optio.shutdown(grace_seconds=1.0)

    kept = {"cleanupPeriodDays": 7, "env": {"FOO": "bar"}}
    assert restored == {
        "home/.claude/settings.json": {**kept, "theme": "light"},    # claude_config applied too
        ".claude/settings.json": kept,
        ".claude/settings.local.json": kept,
    }
    for argv in _log_lines(argv_log):
        assert json.loads(_flag(argv, "--settings")) == {"env": gateway_launch_env(_seed())}


@pytest.mark.asyncio
async def test_the_snapshot_guard_takes_the_gateway_marker(ctx_and_captures, tmp_path):
    """Crash-orphan rescue and Resurrect run in later runs, on the workdir
    alone: the marker a gateway launch wrote is what both the capture's guard
    and Resurrect's probe check, and nothing else stands in for it."""
    ctx, _cap, _cancel = ctx_and_captures

    def _workdir(name: str, *, marker: bool) -> LocalHost:
        host = LocalHost(taskdir=str(tmp_path / name))
        home = pathlib.Path(host.workdir, "home/.claude")
        home.mkdir(parents=True)
        if marker:
            (home / ".optio-gateway").write_text("gateway\n")
        return host

    with_marker = _workdir("with-marker", marker=True)
    assert await resurrect._session_state_present(with_marker, ".optio-gateway") is True
    assert await session_mod._capture_snapshot(
        ctx, with_marker, end_state="done", workdir_exclude=None, guard_file=".optio-gateway",
    ) is True
    assert await snapshots.load_latest_snapshot(
        ctx._db, prefix=ctx._prefix, process_id=ctx.process_id,
    ) is not None

    without = _workdir("without-marker", marker=False)
    assert await resurrect._session_state_present(without, ".optio-gateway") is False
    assert await session_mod._capture_snapshot(
        ctx, without, end_state="done", workdir_exclude=None, guard_file=".optio-gateway",
    ) is False
