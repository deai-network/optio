"""Gateway seed setup: validation, one short real conversation per model.

`validate_gateway_models` checks each model with a fresh gateway-mode Claude
Code conversation on the session host, by default three at once: the workdir
holds `CHECK_FILE` with a random word, and the prompt asks claude to read it
with the Read tool and reply with only that word. A model passes when the turn
did not end in an error and the answer contains the word (case-insensitive).

A check runs its model alone, as the seed's only usable model and its default:
the seed being set up has no usable model yet, and Claude Code's aliases (its
own background calls) then point at the model under test, so each result
depends on that model only. The effort flag is the whole seed's, though
(`_check_env`): a session sets it for the process when any model of its seed
has levels, and Claude Code then sends an effort to a model without levels
too, so that model is checked under it. No reason ever contains the token.

`create_gateway_setup_task` is the optio task around it. A setup tests a
pending seed: the seed becomes alive with only the models that passed when its
default model passed, and is removed otherwise. Test again (`retest=True`)
tests an alive or dead seed again: a failing model stays in it as failed, and
the seed is dead when every model fails.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import inspect
import logging
import secrets
import shlex
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone

from optio_agents import seeds
from optio_agents.context import HookContext
from optio_agents.model_probe import probe_turn
from optio_core.context import ProcessContext
from optio_core.models import TaskInstance
from optio_host import SSHConfig, make_host
from optio_host.host import proc_wait
from optio_host.paths import task_dir

from optio_claudecode import host_actions
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.gateway import (
    GATEWAY_ENV_REMOVE,
    GATEWAY_SECRET_ENV,
    GatewaySeed,
    SeedStatus,
    gateway_launch_env,
    gateway_seed_status,
    gateway_settings_json,
    launch_effort,
    read_gateway_seed,
    seed_summary,
    update_gateway_seed,
)

_LOG = logging.getLogger(__name__)

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

class _Quiet:
    """The progress sink of a check's claude install: validation reports per
    model (`report`), not per install step."""

    def report_progress(self, percent: float | None, message: str | None = None) -> None:
        pass


class _InstallLock:
    """One claude install at a time within a batch: the first one also asks
    upstream for a newer claude, the others link the cached one."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.update_checked = False


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


async def _cleanup(steps: Sequence[tuple[str, Callable[[], Awaitable[object]]]]) -> None:
    """A check's cleanup steps, each run in order whatever the others do. A
    failed step is logged, not raised: the model's outcome stands. A
    cancellation stops only the step it lands in; the remaining steps still
    run, and it is raised after the last one."""
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
    install_lock: _InstallLock,
) -> tuple[bool, str | None]:
    """One model's check: ``(True, None)`` or ``(False, reason)``. Raises for
    a host, install or launch failure, which the batch makes the model's
    reason. Whatever happens, claude is killed and the task directory
    removed. The batch removes the token from every reason; an answer is
    cleaned here as well, before it is cut short."""
    check_seed = _check_seed(seed, model_id)
    host = make_host(ssh=ssh, taskdir=task_dir(
        ssh=ssh, process_id=f"gateway-check-{secrets.token_hex(6)}", consumer_name="optio-claudecode",
    ))
    handle = None
    reader = None
    try:
        await host.connect()
        await host.setup_workdir()
        async with install_lock.lock:
            claude_path = await host_actions.ensure_claude_installed(
                HookContext(_Quiet(), host), install_dir=install_dir,
                check_update=not install_lock.update_checked,
            )
            install_lock.update_checked = True
        word = secrets.choice(_WORDS) + str(1000 + secrets.randbelow(9000))
        await host.write_text(CHECK_FILE, f"{word}\n")
        gateway_env = _check_env(seed, check_seed)
        flags = host_actions.build_claude_flags(
            permission_mode="dontAsk", allowed_tools=["Read"], disallowed_tools=None, tools=["Read"],
            model=model_id, effort=launch_effort(check_seed, model_id, None),
            # Pinned as in a session (gateway_settings_json).
            settings=gateway_settings_json(gateway_env),
        )
        argv = host_actions.build_conversation_argv(claude_path, claude_flags=flags, permission_gate=False)
        # The token reaches claude's environment over its first stdin line,
        # never a command line (RemoteHost writes `env=` into the one sshd runs).
        handle = await host.launch_subprocess(
            host_actions.with_stdin_secrets(shlex.join(argv), GATEWAY_SECRET_ENV),
            env=host_actions.conversation_launch_env(host.workdir, gateway_env),
            env_remove=list(GATEWAY_ENV_REMOVE), cwd=host.workdir, stdin=True,
        )
        await host_actions.write_stdin_secrets(handle, [seed.token], names=GATEWAY_SECRET_ENV)
        conv = ClaudeCodeConversation()
        # An is_error result reaches on_message too (probe_turn then returns
        # its text as the answer), so the result event decides. It is recorded
        # by the time probe_turn returns: _route queues the event as it
        # schedules on_message, so the dispatcher runs before probe_turn resumes.
        results: list[dict] = []
        conv.on_event(lambda event: results.append(event) if event.get("type") == "result" else None)
        conv.attach(handle)
        reader = asyncio.create_task(conv.run_reader())
        turn = asyncio.create_task(
            probe_turn(conv, CHECK_PROMPT, per_model_timeout, error_from_event=_result_error),
        )
        exited = asyncio.create_task(proc_wait(handle))
        try:
            done, _ = await asyncio.wait({turn, exited}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (turn, exited):
                if not task.done():
                    task.cancel()
        if turn not in done:
            return False, f"claude exited (exit {exited.result()}) before answering"
        answer, error = turn.result()
        if results:
            error = _result_error(results[0])
            text = results[0].get("result")
            answer = text if isinstance(text, str) else answer
        elif not answer and error is None:
            return False, f"no answer within {per_model_timeout:g} s"
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
    concurrency: int = 3,
    per_model_timeout: float = 90.0,
    report: Callable[[int, int, str, bool, str | None], None] | None = None,
    install_dir: str | None = None,
) -> dict[str, tuple[bool, str | None]]:
    """Check each of ``model_ids`` (models of ``seed``) on the session host
    (``ssh``, None: this machine), at most ``concurrency`` at once, each turn
    within ``per_model_timeout`` seconds. Returns ``{model_id: (ok, reason)}``
    in the order given. ``report(done, total, model_id, ok, reason)`` is
    called after each model; an error it raises is logged, and the outcomes
    stand. A check that raises fails its model only, with ``"check failed:
    <type>: <message>"``. ``install_dir`` is the claude cache override (as
    ``ClaudeCodeTaskConfig.install_dir``). ValueError for a ``concurrency``
    below 1."""
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, not {concurrency!r}")
    gate = asyncio.Semaphore(concurrency)
    install_lock = _InstallLock()
    results: dict[str, tuple[bool, str | None]] = {}
    done = 0

    async def _one(model_id: str) -> None:
        nonlocal done
        async with gate:
            try:
                ok, reason = await _check_model(
                    seed, model_id, ssh=ssh, install_dir=install_dir,
                    per_model_timeout=per_model_timeout, install_lock=install_lock,
                )
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


async def _validate(
    ctx: ProcessContext,
    seed: GatewaySeed,
    *,
    ssh: SSHConfig | None,
    install_dir: str | None,
    per_model_timeout: float,
) -> _Outcomes | None:
    """`validate_gateway_models` over every model of ``seed``, with one
    progress line per model: ``"<id>: passed"`` or ``"<id>: failed
    (<reason>)"``. None when the run is cancelled meanwhile: the checks are
    then stopped, and their cleanup has ended by the time this returns."""

    def report(done: int, total: int, model_id: str, ok: bool, reason: str | None) -> None:
        line = f"{model_id}: passed" if ok else f"{model_id}: failed ({reason})"
        ctx.report_progress(100 * done / total, line)

    batch = asyncio.ensure_future(validate_gateway_models(
        seed, [m.id for m in seed.models], ssh=ssh, per_model_timeout=per_model_timeout,
        report=report, install_dir=install_dir,
    ))
    stop = asyncio.ensure_future(ctx.cancellation_flag.wait())
    try:
        await asyncio.wait({batch, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.cancel()
        batch.cancel()  # nothing once it has ended
        await asyncio.wait({batch})
    if not ctx.should_continue():
        return None
    return batch.result()


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


async def _purge(db, *, prefix: str, suffix: str, seed_id: str) -> None:
    """Remove a pending seed (document and blob); one already gone is fine."""
    try:
        await seeds.purge_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    except KeyError:
        pass


async def _first_setup(
    ctx: ProcessContext, where: dict, *, encrypt: _Cipher, decrypt: _Cipher, validate: _Validate,
) -> tuple[GatewaySeed, SeedStatus] | None:
    """Test a pending seed. Its default model passed: write the seed alive
    with only the models that passed, and return the tested seed (every
    model, with its outcome). It failed: remove the seed and raise
    RuntimeError. None when the run was cancelled. A cancellation, or an
    exception before the seed is written, removes the seed."""
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
            await _purge(db, **where)
            purged = True
            raise RuntimeError(f"default model {seed.default_model}: {reason}")
        await update_gateway_seed(db, **where, seed=_passed_only(tested), encrypt=encrypt, status="alive")
        written = True
        return tested, "alive"
    finally:
        if not written and not purged:
            try:
                await _purge(db, **where)
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
    read with ``decrypt`` and written with ``encrypt``. Every model of the
    seed is checked with `validate_gateway_models` on the session host
    (``ssh``, None: this machine; ``install_dir`` and ``per_model_timeout`` as
    there), one progress line per model.

    - A setup (``retest=False``) needs a pending seed. Its default model
      passed: the seed becomes alive, holding only the models that passed (a
      preset naming a dropped model is cleared). It failed: the seed is
      removed and the run fails with ``"default model <id>: <reason>"``,
      publishing nothing. A cancellation, or an exception before the seed is
      written, removes the seed too.
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
