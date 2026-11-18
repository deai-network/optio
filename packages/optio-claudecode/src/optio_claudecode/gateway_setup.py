"""Gateway seed setup: validation, one short real conversation per model.

`validate_gateway_models` checks each model with a fresh gateway-mode Claude
Code conversation on the session host, by default three at once: the workdir
holds `CHECK_FILE` with a random word, and the prompt asks claude to read it
with the Read tool and reply with only that word. A model passes when the turn
did not end in an error and the answer contains the word (case-insensitive).
The answer is every assistant text block of the turn, joined: a model may
split its reply around a thinking block, and the turn's result event holds
only the last block.

A check runs its model alone, as the seed's only usable model and its default:
the seed being set up has no usable model yet, and Claude Code's aliases (its
own background calls) then point at the model under test, so each result
depends on that model only. The effort flag is the whole seed's, though
(`_check_env`): a session sets it for the process when any model of its seed
has levels, and Claude Code then sends an effort to a model without levels
too, so that model is checked under it. No reason ever contains the token.

`create_gateway_setup_task` is the optio task around it. It installs or
updates claude once, in a child process of its own (`_install_claude`), and
the checks then link that cached binary; its log names every step
(`_validate`). A setup tests a pending seed: the seed becomes alive with only
the models that passed when its default model passed, and is removed
otherwise. Test again (`retest=True`) tests an alive or dead seed again: a
failing model stays in it as failed, and the seed is dead when every model
fails.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import inspect
import logging
import posixpath
import secrets
import shlex
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import TypeVar

from bson import ObjectId
from gridfs.errors import NoFile
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from optio_agents.context import HookContext
from optio_core.context import ProcessContext
from optio_core.exceptions import ChildProcessFailed
from optio_core.models import TaskInstance
from optio_host import SSHConfig, make_host
from optio_host.host import proc_wait
from optio_host.paths import task_dir

from optio_claudecode import host_actions
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.gateway import (
    GATEWAY_ENV_REMOVE,
    GATEWAY_SECRET_ENV,
    GATEWAY_TRANSPORT_ENV,
    GatewaySeed,
    SeedStatus,
    gateway_launch_env,
    gateway_seed_status,
    gateway_settings_json,
    gateway_transport_pins,
    launch_effort,
    read_gateway_seed,
    seed_summary,
    update_gateway_seed,
)
from optio_claudecode.info import AGENT_INFO, REQUESTY_AGENT_INFO

_LOG = logging.getLogger(__name__)
_T = TypeVar("_T")

CHECK_FILE = "optio-check.txt"
CHECK_PROMPT = (
    f"Use the Read tool to read the file {CHECK_FILE} in the current directory, "
    "then reply with only the word it contains."
)

# The check word is one of these plus four digits, so no model can guess it.
_WORDS = (
    "amber", "basalt", "cedar", "cobalt", "delta", "ember", "fjord", "garnet",
    "harbor", "indigo", "juniper", "lagoon", "marble", "nimbus", "orchid",
    "quartz", "saffron", "tundra", "violet", "willow",
)

# Checks at once, by default and in the setup task.
_CONCURRENCY = 3

# The steps of a check that can take time, in order, as `on_step` names them.
_CONNECTING = "connecting to the session host"
_STARTING = f"starting {AGENT_INFO.name}"
_ANSWERING = "waiting for the test answer"


class _Quiet:
    """The progress sink of a check's claude link: the setup's install child
    reports the install (`_install_claude`), and a check reports its own
    steps (`on_step`)."""

    def report_progress(self, percent: float | None, message: str | None = None) -> None:
        pass


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _check_seed(seed: GatewaySeed, model_id: str) -> GatewaySeed:
    """The seed a check of ``model_id`` launches with: that model alone,
    usable and the default. ValueError when the seed does not have it."""
    model = next((m for m in seed.models if m.id == model_id), None)
    if model is None:
        raise ValueError(f"model {model_id!r} is not one of the seed's models")
    return dataclasses.replace(
        seed, models=(dataclasses.replace(model, status="ok", reason=None),), default_model=model_id,
    )


def _check_env(seed: GatewaySeed, check_seed: GatewaySeed) -> dict[str, str]:
    """The non-secret gateway environment of a check: ``check_seed``'s (the
    aliases on the model under test), with `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT`
    as a session on the whole ``seed`` sets it: when any of its models has
    levels. The flag holds for the whole process (`gateway_launch_env`), and
    with it Claude Code sends its default effort to a model without levels as
    well (AGENTS.md, Testing), so a model that rejects an effort fails here,
    not in every session of a seed that also holds a model with levels."""
    env = gateway_launch_env(check_seed)
    if any(m.efforts for m in seed.models):
        env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] = "1"
    return env


def _result_error(event: dict) -> str | None:
    """A result event's error text; None for any other event, and for a
    result that is not an error."""
    if event.get("type") != "result" or not event.get("is_error"):
        return None
    text = event.get("result")
    if isinstance(text, str) and text:
        return text
    return f"the turn ended in an error ({event.get('subtype')})"


def _text_blocks(event: dict) -> list[str]:
    """An assistant event's text blocks, in order; [] for any other event."""
    if event.get("type") != "assistant":
        return []
    content = (event.get("message") or {}).get("content")
    if isinstance(content, str):
        return [content]
    return [
        block["text"] for block in content or ()
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]


async def _ask(conv: ClaudeCodeConversation, prompt: str, timeout: float) -> tuple[str, dict | None]:
    """Send ``prompt`` and wait up to ``timeout`` seconds for the turn's
    result event, which ends the turn. Returns the answer, every assistant
    text block of the turn joined with no separator (a model may split its
    reply around a thinking block, and the result event holds only the last
    block), and the result event, None when none came in time. The events
    reach ``on_event`` in order, so every block before the result is in."""
    ended: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
    blocks: list[str] = []

    def collect(event: dict) -> None:
        if ended.done():
            return
        if event.get("type") == "result":
            ended.set_result(event)
        else:
            blocks.extend(_text_blocks(event))

    unsubscribe = conv.on_event(collect)
    try:
        await conv.send(prompt)
        try:
            result = await asyncio.wait_for(ended, timeout)
        except TimeoutError:
            result = None
    finally:
        unsubscribe()
    return "".join(blocks), result


async def _cleanup(steps: Sequence[tuple[str, Callable[[], Awaitable[object]]]]) -> None:
    """Cleanup steps (a check's, the install child's), each run in order
    whatever the others do. A failed step is logged, not raised: the
    outcome stands. A cancellation stops only the step it lands in; the
    remaining steps still run, and it is raised after the last one."""
    cancelled: asyncio.CancelledError | None = None
    for step, run in steps:
        try:
            await run()
        except asyncio.CancelledError as e:
            cancelled = e
        except Exception:  # noqa: BLE001
            _LOG.warning("gateway check: %s failed", step, exc_info=True)
    if cancelled is not None:
        raise cancelled


# The claude install (the setup's install child; a check's link, under the
# batch's install lock) has its own bound above the per-model one: a first
# install downloads the binary.
_INSTALL_TIMEOUT_S = 600.0


class _TooSlow(Exception):
    """A host or install step ran past its bound. The message is the
    model's reason (in the setup's install child, the install's)."""


async def _bounded(
    seconds: float, steps: Sequence[tuple[str, Callable[[], Awaitable[object]]]],
) -> list[object]:
    """Run ``steps`` in order, within ``seconds`` in all; their results.
    `_TooSlow` names the step that was running when the time ran out (raised
    outside the except block, so nothing travels with it). Any other error,
    a TimeoutError a step raised itself included, passes through."""
    results: list[object] = []
    running = steps[0][0]
    timeout = asyncio.timeout(seconds)
    try:
        async with timeout:
            for running, step in steps:
                results.append(await step())
        return results
    except TimeoutError:
        if not timeout.expired():
            raise
    raise _TooSlow(f"{running} did not finish within {seconds:g} s")


async def _stop_reader(reader: asyncio.Task) -> None:
    """Cancel the conversation's reader and wait until it has ended. An error
    it ended with (asyncssh's when the connection is lost) is raised, for the
    cleanup to log; its own cancellation is not."""
    reader.cancel()
    await asyncio.wait({reader})
    if not reader.cancelled() and reader.exception() is not None:
        raise reader.exception()


async def _check_model(
    seed: GatewaySeed,
    model_id: str,
    *,
    ssh: SSHConfig | None,
    install_dir: str | None,
    per_model_timeout: float,
    install_lock: asyncio.Lock,
    on_step: Callable[[str], None],
) -> tuple[bool, str | None]:
    """One model's check: ``(True, None)`` or ``(False, reason)``. Raises for
    a host, install or launch failure, which the batch makes the model's
    reason; `_TooSlow` when the host steps (connect, task directory, check
    file, launch environment) take longer than ``per_model_timeout``
    together, or the install longer than `_INSTALL_TIMEOUT_S`. Claude is
    linked from the cache without asking upstream for a newer one, under
    ``install_lock``. ``on_step`` is called as each step that can take time
    begins: `_CONNECTING`, `_STARTING`, `_ANSWERING`. Whatever happens,
    claude is killed and the task directory removed. The batch removes the
    token from every reason; an answer is cleaned here as well, before it is
    cut short."""
    check_seed = _check_seed(seed, model_id)
    host = make_host(ssh=ssh, taskdir=task_dir(
        ssh=ssh, process_id=f"gateway-check-{secrets.token_hex(6)}", consumer_name="optio-claudecode",
    ))
    word = secrets.choice(_WORDS) + str(1000 + secrets.randbelow(9000))
    gateway_env = _check_env(seed, check_seed)
    launch_env = host_actions.conversation_launch_env(host.workdir, gateway_env)
    env_remove = list(GATEWAY_ENV_REMOVE)
    handle = None
    reader = None
    try:
        on_step(_CONNECTING)
        # Bounded like the turn, so an unreachable host fails its model
        # rather than hanging the setup.
        *_, transport = await _bounded(per_model_timeout, (
            (_CONNECTING, lambda: host.connect()),
            ("setting up the task directory", lambda: host.setup_workdir()),
            ("writing the check file", lambda: host.write_text(CHECK_FILE, f"{word}\n")),
            ("reading the launch environment", lambda: host_actions.child_env_values(
                host, GATEWAY_TRANSPORT_ENV, env=launch_env, env_remove=env_remove)),
        ))
        async with install_lock:
            [claude_path] = await _bounded(_INSTALL_TIMEOUT_S, ((
                "installing claude", lambda: host_actions.ensure_claude_installed(
                    HookContext(_Quiet(), host), install_dir=install_dir, check_update=False,
                ),
            ),))
        flags = host_actions.build_claude_flags(
            permission_mode="dontAsk", allowed_tools=["Read"], disallowed_tools=None, tools=["Read"],
            model=model_id, effort=launch_effort(check_seed, model_id, None),
            # Pinned as in a session: the gateway's environment and the
            # transport variables at this launch's own values.
            settings=gateway_settings_json({**gateway_env, **gateway_transport_pins(transport)}),
        )
        argv = host_actions.build_conversation_argv(claude_path, claude_flags=flags, permission_gate=False)
        on_step(_STARTING)
        # The token reaches claude's environment over its first stdin line,
        # never a command line (RemoteHost writes `env=` into the one sshd runs).
        handle = await host.launch_subprocess(
            host_actions.with_stdin_secrets(shlex.join(argv), GATEWAY_SECRET_ENV),
            env=launch_env, env_remove=env_remove, cwd=host.workdir, stdin=True,
        )
        await host_actions.write_stdin_secrets(handle, [seed.token], names=GATEWAY_SECRET_ENV)
        conv = ClaudeCodeConversation()
        conv.attach(handle)
        reader = asyncio.create_task(conv.run_reader())
        on_step(_ANSWERING)
        turn = asyncio.create_task(_ask(conv, CHECK_PROMPT, per_model_timeout))
        exited = asyncio.create_task(proc_wait(handle))
        try:
            done, _ = await asyncio.wait({turn, exited}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (turn, exited):
                if not task.done():
                    task.cancel()
        if turn not in done:
            return False, f"claude exited (exit {exited.result()}) before answering"
        answer, result = turn.result()
        if result is None:
            return False, f"no answer within {per_model_timeout:g} s"
        # The result event decides whether the turn ended in an error.
        error = _result_error(result)
        if error is not None:
            return False, error
        if word.lower() in answer.lower():
            return True, None
        return False, f"did not answer with the word (answered {_redact(answer, seed.token)[:120]!r})"
    finally:
        steps: list[tuple[str, Callable[[], Awaitable[object]]]] = []
        if handle is not None:
            steps.append(("stopping claude", lambda: host.terminate_subprocess(handle, aggressive=True)))
        if reader is not None:
            steps.append(("stopping the reader", lambda: _stop_reader(reader)))
        steps.append(("removing the task directory", lambda: host.cleanup_taskdir(aggressive=True)))
        steps.append(("disconnecting", host.disconnect))
        await _cleanup(steps)


async def validate_gateway_models(
    seed: GatewaySeed,
    model_ids: Sequence[str],
    *,
    ssh: SSHConfig | None = None,
    concurrency: int = _CONCURRENCY,
    per_model_timeout: float = 90.0,
    report: Callable[[int, int, str, bool, str | None], None] | None = None,
    install_dir: str | None = None,
    on_step: Callable[[str, str], None] | None = None,
) -> dict[str, tuple[bool, str | None]]:
    """Check each of ``model_ids`` (models of ``seed``) on the session host
    (``ssh``, None: this machine), at most ``concurrency`` at once, each turn
    within ``per_model_timeout`` seconds. Returns ``{model_id: (ok, reason)}``
    in the order given. ``report(done, total, model_id, ok, reason)`` is
    called after each model, and ``on_step(model_id, step)`` as each step of
    a check that can take time begins (`_CONNECTING`, `_STARTING`,
    `_ANSWERING`). An error either raises is logged, and the outcomes
    stand. A check that raises fails its model only, with ``"check failed:
    <type>: <message>"``. The host steps before the turn (connect, task
    directory, check file, launch environment) share ``per_model_timeout``
    too, and the claude install has its own `_INSTALL_TIMEOUT_S`: past it
    the model fails with ``"<step> did not finish within <t> s"``. The
    checks link claude from the cache without asking upstream for a newer
    one (the setup task installs or updates it first, `_install_claude`);
    on a cold cache the first check installs it, one install at a time.
    ``install_dir`` is the claude cache override (as
    ``ClaudeCodeTaskConfig.install_dir``). ValueError for a ``concurrency``
    below 1."""
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, not {concurrency!r}")
    gate = asyncio.Semaphore(concurrency)
    install_lock = asyncio.Lock()
    results: dict[str, tuple[bool, str | None]] = {}
    done = 0

    async def _one(model_id: str) -> None:
        nonlocal done

        def step(name: str) -> None:
            if on_step is None:
                return
            try:
                on_step(model_id, name)
            except Exception:  # noqa: BLE001 — the check goes on
                _LOG.warning("gateway check: step report for %s failed", model_id, exc_info=True)

        async with gate:
            try:
                ok, reason = await _check_model(
                    seed, model_id, ssh=ssh, install_dir=install_dir,
                    per_model_timeout=per_model_timeout, install_lock=install_lock, on_step=step,
                )
            except _TooSlow as e:
                ok, reason = False, str(e)
            except Exception as e:  # noqa: BLE001 — one failing check fails its model only
                ok, reason = False, f"check failed: {type(e).__name__}: {_redact(str(e), seed.token)[:200]}"
        if reason is not None:
            reason = _redact(reason, seed.token)
            _LOG.info("gateway check: %s failed: %s", model_id, reason)
        results[model_id] = (ok, reason)
        done += 1
        if report is not None:
            try:
                report(done, len(model_ids), model_id, ok, reason)
            except Exception:  # noqa: BLE001 — the checks go on; the outcome stands
                _LOG.warning("gateway check: report for %s failed", model_id, exc_info=True)

    await asyncio.gather(*(_one(model_id) for model_id in model_ids))
    return {model_id: results[model_id] for model_id in model_ids}


# --- the setup task ---------------------------------------------------------

_Outcomes = dict[str, tuple[bool, str | None]]
_Cipher = Callable[[bytes], bytes] | None
_Validate = Callable[[GatewaySeed], Awaitable[_Outcomes | None]]


def _check_flow(seed_id: str, status: str | None, retest: bool) -> None:
    """ValueError when the flow does not fit the seed: a setup tests a
    pending seed, Test again an alive or dead one."""
    if status is None:
        raise ValueError(f"unknown gateway seed {seed_id}")
    if retest and status == "pending":
        raise ValueError(f"gateway seed {seed_id} is pending: Test again needs an alive or dead seed")
    if not retest and status != "pending":
        raise ValueError(
            f"gateway seed {seed_id} is {status}: the setup needs a pending seed (Test again is retest=True)"
        )


async def _unless_cancelled(ctx: ProcessContext, work: Awaitable[_T]) -> _T | None:
    """``work``'s result; None when ``ctx`` is cancelled meanwhile: ``work``
    is then stopped, and has ended by the time this returns."""
    job = asyncio.ensure_future(work)
    stop = asyncio.ensure_future(ctx.cancellation_flag.wait())
    try:
        await asyncio.wait({job, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.cancel()
        job.cancel()  # nothing once it has ended
        await asyncio.wait({job})
    if not ctx.should_continue():
        return None
    return job.result()


async def _linked_version(host, claude_path: str) -> str | None:
    """The claude version ``claude_path`` links to (a cached binary is named
    by its version); None when the link names none or cannot be read. The
    version is only shown, so nothing fails over it."""
    try:
        r = await host.run_command(f"readlink {shlex.quote(claude_path)}")
    except Exception:  # noqa: BLE001
        _LOG.warning("gateway setup: reading the claude version failed", exc_info=True)
        return None
    name = posixpath.basename(r.stdout.strip())
    return name if r.exit_code == 0 and host_actions._version_key(name) is not None else None


async def _install_claude(
    ctx: ProcessContext,
    *,
    ssh: SSHConfig | None,
    install_dir: str | None,
    per_model_timeout: float,
    token: str,
) -> str | None:
    """Install claude on the session host, or update it, in a child process
    of the run, with its own log and status (as `HookContext.download_file`
    runs a download): ensure_claude_installed with the update check, in a
    task directory of its own, removed afterwards. The host steps are
    bounded by ``per_model_timeout``, the install by `_INSTALL_TIMEOUT_S`.
    Returns the version installed, None when it is not known. RuntimeError
    ``"Claude Code install failed: <reason>"`` when the child fails. When
    the run is cancelled meanwhile the child stops; the caller then finds
    ``ctx`` cancelled."""
    n = ctx._child_counter.get("next", 0)
    installed: dict[str, str | None] = {"version": None}

    async def _execute(child_ctx: ProcessContext) -> None:
        host = make_host(ssh=ssh, taskdir=task_dir(
            ssh=ssh, process_id=f"gateway-install-{secrets.token_hex(6)}", consumer_name="optio-claudecode",
        ))

        async def install() -> str | None:
            await _bounded(per_model_timeout, (
                (_CONNECTING, lambda: host.connect()),
                ("setting up the task directory", lambda: host.setup_workdir()),
            ))
            [claude_path] = await _bounded(_INSTALL_TIMEOUT_S, ((
                "installing claude", lambda: host_actions.ensure_claude_installed(
                    HookContext(child_ctx, host), install_dir=install_dir, check_update=True,
                ),
            ),))
            return await _linked_version(host, claude_path)

        try:
            installed["version"] = await _unless_cancelled(child_ctx, install())
        finally:
            await _cleanup((
                ("removing the task directory", lambda: host.cleanup_taskdir(aggressive=True)),
                ("disconnecting", host.disconnect),
            ))

    task = TaskInstance(
        execute=_execute, process_id=f"{ctx.process_id}.install-claude-{n}", name=f"Install {AGENT_INFO.name}",
    )
    failure = None
    try:
        await ctx.run_child_task(task)
    except ChildProcessFailed as e:
        reason = str(e.original) or type(e.original).__name__
        failure = f"{AGENT_INFO.name} install failed: {_redact(reason, token)[:200]}"
    # Raised outside the except block, so nothing travels with it.
    if failure is not None:
        raise RuntimeError(failure)
    return installed["version"]


def _testing_line(seed: GatewaySeed) -> str:
    """The setup's first log line: what it tests, where, how many at once."""
    n = len(seed.models)
    region = seed.region.upper() if len(seed.region) <= 2 else seed.region.capitalize()
    at_once = f", up to {_CONCURRENCY} at a time" if n > _CONCURRENCY else ""
    return f"Testing {n} model{'' if n == 1 else 's'} with {REQUESTY_AGENT_INFO.name} ({region}){at_once}."


def _outcome(ok: bool, reason: str | None, seconds: float | None) -> str:
    """A model's result: ``"passed (<t> s)"`` or ``"failed after <t> s
    (<reason>)"``; without the time when it is not known."""
    took = None if seconds is None else f"{seconds:.0f} s"
    if ok:
        return "passed" if took is None else f"passed ({took})"
    return f"failed ({reason})" if took is None else f"failed after {took} ({reason})"


def _saving_line(outcomes: _Outcomes) -> str:
    passed = sum(1 for ok, _ in outcomes.values() if ok)
    total = len(outcomes)
    return f"Saving: {passed} of {total} model{'' if total == 1 else 's'} passed."


async def _validate(
    ctx: ProcessContext,
    seed: GatewaySeed,
    *,
    ssh: SSHConfig | None,
    install_dir: str | None,
    per_model_timeout: float,
) -> _Outcomes | None:
    """Install claude (`_install_claude`), then `validate_gateway_models`
    over every model of ``seed``, logging each step on ``ctx``: what is
    tested (`_testing_line`), ``"Claude Code <version> ready."``, then for
    each model ``"<label> (<i> of <n>): <step>"`` as each of its slow steps
    begins and ``"<label> (<i> of <n>): <outcome>"`` (`_outcome`, timed
    from its first step) when it ends. The bar is indeterminate until the
    install has ended, the first of n + 1 shares, and then advances a share
    per model. Every line passes `_redact`. None when the run is cancelled
    meanwhile: the checks are then stopped, and their cleanup has ended by
    the time this returns. A failed install raises (`_install_claude`)."""
    total = len(seed.models)
    names = {m.id: f"{m.label} ({i} of {total})" for i, m in enumerate(seed.models, 1)}
    loop = asyncio.get_running_loop()
    started: dict[str, float] = {}
    shares = 0
    percent: float | None = None

    def log(line: str) -> None:
        ctx.report_progress(percent, _redact(line, seed.token))

    def advance() -> None:
        nonlocal shares, percent
        shares += 1
        percent = 100 * shares / (total + 1)

    log(_testing_line(seed))
    version = await _install_claude(
        ctx, ssh=ssh, install_dir=install_dir, per_model_timeout=per_model_timeout, token=seed.token,
    )
    if not ctx.should_continue():
        return None
    advance()
    log(f"{AGENT_INFO.name} {version} ready." if version else f"{AGENT_INFO.name} ready.")

    def on_step(model_id: str, step: str) -> None:
        started.setdefault(model_id, loop.time())
        log(f"{names[model_id]}: {step}")

    def report(_done: int, _of: int, model_id: str, ok: bool, reason: str | None) -> None:
        began = started.get(model_id)
        advance()
        log(f"{names[model_id]}: {_outcome(ok, reason, None if began is None else loop.time() - began)}")

    return await _unless_cancelled(ctx, validate_gateway_models(
        seed, [m.id for m in seed.models], ssh=ssh, concurrency=_CONCURRENCY,
        per_model_timeout=per_model_timeout, report=report, install_dir=install_dir, on_step=on_step,
    ))


def _with_outcomes(seed: GatewaySeed, outcomes: _Outcomes) -> GatewaySeed:
    """``seed`` with each model's outcome (status "ok" or "failed", and the
    reason) and the test time."""
    return dataclasses.replace(
        seed,
        models=tuple(
            dataclasses.replace(m, status="ok" if outcomes[m.id][0] else "failed", reason=outcomes[m.id][1])
            for m in seed.models
        ),
        tested_at=datetime.now(timezone.utc),
    )


def _passed_only(seed: GatewaySeed) -> GatewaySeed:
    """A first setup's alive seed: only the models that passed. A preset
    naming a dropped model is cleared, so consumers fall back to
    `effective_default_model()`; with no preset left, presets is None."""
    kept = seed.usable_models()
    ids = {m.id for m in kept}
    presets = {key: p for key, p in (seed.presets or {}).items() if p.model in ids}
    return dataclasses.replace(seed, models=kept, presets=presets or None)


async def _purge_pending(db, *, prefix: str, suffix: str, seed_id: str) -> None:
    """Remove the seed (document and blob) only while it is still pending.
    Another setup on the same seed may have written it alive (or a Test
    again dead) meanwhile: that seed, and one already gone, are left alone.
    One find_one_and_delete matches the status with the id, so no write can
    land between the check and the removal."""
    doc = await db[f"{prefix}{suffix}"].find_one_and_delete(
        {"_id": ObjectId(seed_id), "status": "pending"},
    )
    if doc is None:
        return
    try:
        await AsyncIOMotorGridFSBucket(db).delete(doc["blobId"])
    except NoFile:
        pass


async def _first_setup(
    ctx: ProcessContext, where: dict, *, encrypt: _Cipher, decrypt: _Cipher, validate: _Validate,
) -> tuple[GatewaySeed, SeedStatus] | None:
    """Test a pending seed. Its default model passed: write the seed alive
    with only the models that passed, and return the tested seed (every
    model, with its outcome). It failed: remove the seed and raise
    RuntimeError. None when the run was cancelled. A cancellation, or an
    exception before the seed is written, removes the seed. Each removal
    takes the seed only while it is still pending (`_purge_pending`)."""
    db = ctx._db
    written = purged = False
    try:
        seed = await read_gateway_seed(db, **where, decrypt=decrypt)
        outcomes = await validate(seed)
        if outcomes is None:
            return None
        tested = _with_outcomes(seed, outcomes)
        ok, reason = outcomes[seed.default_model]
        if not ok:
            await _purge_pending(db, **where)
            purged = True
            raise RuntimeError(f"default model {seed.default_model}: {reason}")
        ctx.report_progress(100, _saving_line(outcomes))
        await update_gateway_seed(db, **where, seed=_passed_only(tested), encrypt=encrypt, status="alive")
        written = True
        return tested, "alive"
    finally:
        if not written and not purged:
            try:
                await _purge_pending(db, **where)
            except Exception:  # noqa: BLE001 — the run's own outcome stands
                _LOG.warning("gateway setup: removing pending seed %s failed", where["seed_id"], exc_info=True)


async def _test_again(
    ctx: ProcessContext, where: dict, *, encrypt: _Cipher, decrypt: _Cipher, validate: _Validate,
) -> tuple[GatewaySeed, SeedStatus] | None:
    """Test an alive or dead seed again and write every model's outcome: the
    seed is alive when some model passed, dead when none did. The presets
    stay as they are. Returns the tested seed and its status; None when the
    run was cancelled (the seed is left as it was)."""
    seed = await read_gateway_seed(ctx._db, **where, decrypt=decrypt)
    outcomes = await validate(seed)
    if outcomes is None:
        return None
    tested = _with_outcomes(seed, outcomes)
    status: SeedStatus = "alive" if tested.usable_models() else "dead"
    ctx.report_progress(100, _saving_line(outcomes))
    await update_gateway_seed(ctx._db, **where, seed=tested, encrypt=encrypt, status=status)
    return tested, status


async def _run_setup(
    ctx: ProcessContext,
    *,
    seed_id: str,
    prefix: str,
    suffix: str,
    encrypt: _Cipher,
    decrypt: _Cipher,
    ssh: SSHConfig | None,
    on_seed_saved: Callable[[str, str], object] | None,
    retest: bool,
    install_dir: str | None,
    per_model_timeout: float,
) -> None:
    where = {"prefix": prefix, "suffix": suffix, "seed_id": seed_id}
    _check_flow(seed_id, await gateway_seed_status(ctx._db, **where), retest)
    validate = functools.partial(
        _validate, ctx, ssh=ssh, install_dir=install_dir, per_model_timeout=per_model_timeout,
    )
    flow = _test_again if retest else _first_setup
    outcome = await flow(ctx, where, encrypt=encrypt, decrypt=decrypt, validate=validate)
    if outcome is None:  # cancelled: the executor records it
        return
    tested, status = outcome
    if on_seed_saved is not None:
        saved = on_seed_saved(seed_id, seed_summary(tested))
        if inspect.isawaitable(saved):
            await saved
    ctx.publish_result({
        "seedId": seed_id,
        "status": status,
        "models": [{"id": m.id, "status": m.status, "reason": m.reason} for m in tested.models],
    })


def create_gateway_setup_task(
    process_id: str,
    name: str,
    *,
    seed_id: str,
    prefix: str,
    suffix: str,
    encrypt: Callable[[bytes], bytes] | None,
    decrypt: Callable[[bytes], bytes] | None,
    ssh: SSHConfig | None = None,
    on_seed_saved: Callable[[str, str], object] | None = None,
    retest: bool = False,
    description: str | None = None,
    metadata: dict | None = None,
    install_dir: str | None = None,
    per_model_timeout: float = 90.0,
) -> TaskInstance:
    """The task that tests the gateway seed ``seed_id`` in
    ``{prefix}{suffix}`` (the consumer's optio prefix, `REQUESTY_SEED_SUFFIX`),
    read with ``decrypt`` and written with ``encrypt``. Claude is installed
    or updated first, by a child process of the run ("Install Claude
    Code"); then every model of the seed is checked with
    `validate_gateway_models` on the session host (``ssh``, None: this
    machine; ``install_dir`` and ``per_model_timeout`` as there). The log
    names every step (`_validate`), and ``"Saving: <n> of <m> models
    passed."`` before the seed is written. A failed install fails the run
    with ``"Claude Code install failed: <reason>"``.

    - A setup (``retest=False``) needs a pending seed. Its default model
      passed: the seed becomes alive, holding only the models that passed (a
      preset naming a dropped model is cleared). It failed: the seed is
      removed and the run fails with ``"default model <id>: <reason>"``,
      publishing nothing. A cancellation, or an exception before the seed is
      written (a failed install too), removes the seed too.
    - Test again (``retest=True``) needs an alive or dead seed: a model that
      fails stays in the seed as failed, the presets stay, and the seed is
      dead when every model fails.

    After each write ``on_seed_saved(seed_id, seed_summary(seed))`` runs
    (sync or async; an exception it raises fails the run, and the seed stays
    as written), then the result ``{"seedId", "status": "alive" | "dead",
    "models": [{"id", "status", "reason"}]}`` (every model tested) is
    published. A flow that does not fit the seed's status, or an unknown id,
    fails the run with ValueError and leaves the seed as it is."""

    async def _execute(ctx: ProcessContext) -> None:
        await _run_setup(
            ctx, seed_id=seed_id, prefix=prefix, suffix=suffix, encrypt=encrypt, decrypt=decrypt,
            ssh=ssh, on_seed_saved=on_seed_saved, retest=retest, install_dir=install_dir,
            per_model_timeout=per_model_timeout,
        )

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        metadata=metadata or {},
        cancellable=True,
        supports_resume=False,
    )
