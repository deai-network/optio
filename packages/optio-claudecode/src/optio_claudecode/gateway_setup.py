"""Gateway seed setup: validation, one short real conversation per model.

`validate_gateway_models` checks each model with a fresh gateway-mode Claude
Code conversation on the session host, by default three at once: the workdir
holds `CHECK_FILE` with a random word, and the prompt asks claude to read it
with the Read tool and reply with only that word. A model passes when the turn
did not end in an error and the answer contains the word (case-insensitive).

A check runs its model alone, as the seed's only usable model and its default:
the seed being set up has no usable model yet, and Claude Code's aliases (its
own background calls) then point at the model under test, so each result
depends on that model only. No reason ever contains the token.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import secrets
import shlex
from collections.abc import Awaitable, Callable, Sequence

from optio_agents.context import HookContext
from optio_agents.model_probe import probe_turn
from optio_host import SSHConfig, make_host
from optio_host.host import proc_wait
from optio_host.paths import task_dir

from optio_claudecode import host_actions
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.gateway import (
    GATEWAY_ENV_REMOVE,
    GATEWAY_SECRET_ENV,
    GatewaySeed,
    gateway_launch_env,
    launch_effort,
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


def _result_error(event: dict) -> str | None:
    """A result event's error text; None for any other event, and for a
    result that is not an error."""
    if event.get("type") != "result" or not event.get("is_error"):
        return None
    text = event.get("result")
    if isinstance(text, str) and text:
        return text
    return f"the turn ended in an error ({event.get('subtype')})"


async def _quietly(step: str, cleanup: Awaitable[object]) -> None:
    """One cleanup step of a check. A failure is logged, not raised: the
    model's outcome stands."""
    try:
        await cleanup
    except Exception:  # noqa: BLE001
        _LOG.warning("gateway check: %s failed", step, exc_info=True)


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
        flags = host_actions.build_claude_flags(
            permission_mode="dontAsk", allowed_tools=["Read"], disallowed_tools=None, tools=["Read"],
            model=model_id, effort=launch_effort(check_seed, model_id, None),
        )
        argv = host_actions.build_conversation_argv(claude_path, claude_flags=flags, permission_gate=False)
        # The token reaches claude's environment over its first stdin line,
        # never a command line (RemoteHost writes `env=` into the one sshd runs).
        handle = await host.launch_subprocess(
            host_actions.with_stdin_secrets(shlex.join(argv), GATEWAY_SECRET_ENV),
            env=host_actions.conversation_launch_env(host.workdir, gateway_launch_env(check_seed)),
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
        if handle is not None:
            await _quietly("stopping claude", host.terminate_subprocess(handle, aggressive=True))
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
        await _quietly("removing the task directory", host.cleanup_taskdir(aggressive=True))
        await _quietly("disconnecting", host.disconnect())


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
    called after each model. A check that raises fails its model only, with
    ``"check failed: <type>: <message>"``. ``install_dir`` is the claude
    cache override (as ``ClaudeCodeTaskConfig.install_dir``)."""
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
            report(done, len(model_ids), model_id, ok, reason)

    await asyncio.gather(*(_one(model_id) for model_id in model_ids))
    return {model_id: results[model_id] for model_id in model_ids}
