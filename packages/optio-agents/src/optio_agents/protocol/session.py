"""Generic log/deliverables session driver.

`run_log_protocol_session` runs a caller-supplied ``body`` callable
against a ``Host`` while two cooperating tasks consume the
``<workdir>/optio.log`` channel:

  - ``_tail_and_dispatch`` parses each log line into a typed event
    (STATUS / DELIVERABLE / DONE / ERROR) and dispatches accordingly.
  - ``_deliverable_fetch_loop`` drains the deliverable queue, fetches
    each file from the host, decodes UTF-8, and invokes the
    consumer's ``on_deliverable`` callback.

The driver knows nothing about specific consumers (opencode,
recipe-execution, ...). Each consumer's body is responsible for its
own subprocess management and arranging for the agent on the host to
write events to ``<workdir>/optio.log``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Awaitable, Callable

from optio_agents.context import HookContext
from optio_host.host import Host
from optio_agents.protocol.parser import (
    AttentionEvent,
    BrowserEvent,
    DeliverableEvent,
    DomainMessageEvent,
    DoneEvent,
    ErrorEvent,
    LogEvent,
    StatusEvent,
    UnknownLine,
    parse_log_line,
    relativize_deliverable_path,
    validate_deliverable_path,
)
from optio_agents.protocol.protocol import Protocol, get_protocol

if TYPE_CHECKING:
    from optio_core.context import ProcessContext


_LOG = logging.getLogger(__name__)


DELIVERABLE_QUEUE_BOUND = 64


# Public type aliases. ``HookContext`` is forward-quoted in these aliases
# so consumers don't need to import HookContext to type-check.
DeliverableCallback = Callable[["HookContext", str, str], Awaitable["str | None"]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: ``(hook_ctx, deliverable_path, decoded_text)``.

May return a non-empty string to send back to the running agent (the
deliverable loop routes it through ``hook_ctx.send_to_agent``); return
``None`` or ``""`` to send nothing. A hook may also call
``hook_ctx.send_to_agent(...)`` directly instead of / in addition to
returning.

``deliverable_path`` is the path of the deliverable file relative to
``<workdir>/deliverables/`` (e.g. ``"summary.md"`` or
``"sub/summary.md"``). It matches the value emitted in the
auto-generated ``"Deliverable: <path>"`` progress message.
"""


HookCallback = Callable[["HookContext"], Awaitable[None]]
"""Hook callback receiving a HookContext. Used by before_execute and
after_execute."""


AgentSender = Callable[[str], Awaitable[None]]
"""Backend transport that pushes one message into the live agent session.

Raises on transport failure (worker down / tmux session gone / non-zero
exit); ``HookContext.send_to_agent`` catches that and returns False."""


class _SessionFailed(Exception):
    """Internal signal: drive the surrounding session to ``failed``.

    Re-raised by ``run_log_protocol_session`` when:
      * the agent emits ERROR
      * the body returns without DONE having fired

    Consumers catch this and translate to their own failure semantics.
    """


async def fetch_deliverable_text(host: Host, absolute_path: str) -> str:
    """Read the host file at ``absolute_path`` and decode it as UTF-8.

    Thin wrapper around ``host.fetch_bytes_from_host`` for the common
    text-deliverable case used by the protocol session driver.
    """
    data = await host.fetch_bytes_from_host(absolute_path)
    return data.decode("utf-8")


async def run_log_protocol_session(
    host: Host,
    ctx: "ProcessContext",
    *,
    body: Callable[[Host, HookContext], Awaitable[None]],
    on_deliverable: DeliverableCallback | None = None,
    before_execute: HookCallback | None = None,
    after_execute: HookCallback | None = None,
    protocol: "Protocol | None" = None,
    browser_url_rewrite: "Callable[[str], str] | None" = None,
    # Mandatory for agent backends that emit deliverables: the deliverable
    # acknowledgment in _deliverable_fetch_loop calls send_to_agent for EVERY
    # deliverable, and the agent is instructed to wait for that ack before
    # DONE — so without a sender a deliverable-emitting agent would hang.
    # opencode + claudecode both wire one. The default stays None only to
    # serve non-agent driver tasks that emit no deliverables (e.g. the
    # browser-bridge demo), where no agent is waiting.
    agent_sender: "AgentSender | None" = None,
    # Per-agent runtime install + resume-restore. The driver runs it in the one
    # correct window — after the workdir wipe, before the optio.log tail — so
    # callers never hand-sequence the workdir lifecycle (which is how the
    # double-wipe regression slipped in). None for driver tasks that install
    # nothing (e.g. the browser-bridge demo).
    prepare: "Callable[[Host, HookContext], Awaitable[None]] | None" = None,
    # Keyword-channel opt-out. True (default) keeps today's behavior for every
    # existing caller. False runs scaffolding only — see the docstring.
    keywords: bool = True,
) -> None:
    """Run ``body`` against ``host`` while the log/deliverables protocol
    cooperates with it.

    Lifecycle:
      1. ``host.setup_workdir()`` — destructive clean-start of the workdir.
      2. ``prepare(host, hook_ctx)`` if set — per-agent runtime install +
         resume-restore, after the wipe and before the optio.log tail.
      3. Create ``<workdir>/deliverables/`` and an empty
         ``<workdir>/optio.log``.
      3. ``before_execute(hook_ctx)`` if set.
      4. Spawn three concurrent tasks:
         - ``_tail_and_dispatch``: parse lines from ``optio.log``,
           emit progress / queue deliverables / set done/error flags.
         - ``_deliverable_fetch_loop``: drain queue, fetch + decode,
           invoke ``on_deliverable``.
         - ``body(host, hook_ctx)``: caller's work.
      5. Await ``{tail, body, cancel}`` with ``FIRST_COMPLETED``.
      6. Drain queue, cancel the still-running watchers.
      7. ``after_execute(hook_ctx)`` if set, with the same failure
         semantics: re-raises if the session was healthy, logged
         otherwise.

    Outcomes:
      * Agent emits ``DONE`` → returns clean.
      * Agent emits ``ERROR`` → raises ``_SessionFailed``.
      * Body returns without ``DONE`` having fired → raises
        ``_SessionFailed`` (the body finished prematurely; no
        successful completion signal observed).
      * Process cancellation → returns clean (caller decides what to
        do next).

    Scaffolding-only mode (``keywords=False``): the driver runs the
    generic session scaffolding only — workdir lifecycle + ``prepare``,
    deliverables dir, optio.log creation, browser shims, hooks, cancel
    watcher — but no optio.log tail, no deliverable fetch loop, no
    DONE/ERROR semantics, and no premature-exit-without-DONE rule (the
    body's own return governs: normal return = clean completion).

    What this driver does NOT do:
      * Workdir teardown / ``host.cleanup_taskdir`` — caller's
        responsibility (caller may want to capture a snapshot first).
      * Subprocess termination — body owns its handles.
      * Snapshot / resume — caller brackets around this call.
    """
    if protocol is None:
        protocol = get_protocol()
    hook_ctx = HookContext(ctx, host)
    hook_ctx._agent_sender = agent_sender

    # Workdir lifecycle, centralized here (exactly once, no caller can forget
    # it, no double-wipe possible):
    #   1. setup_workdir() — destructive clean-start that kills stale state
    #      from a prior run (e.g. a force-cancel that skipped teardown).
    #   2. prepare() — the caller's per-agent runtime install + resume-restore,
    #      run AFTER the wipe but BEFORE the optio.log reset + tail subscription
    #      below, so a restored optio.log is cleared and never replayed as a
    #      stale DONE/ERROR.
    # Teardown (snapshot capture + cleanup_taskdir) stays the caller's bracket
    # around this call — it may want the workdir intact to snapshot first.
    await host.setup_workdir()
    if prepare is not None:
        await prepare(host, hook_ctx)

    # Protocol artifacts over the now-prepared workdir: the deliverables/ dir
    # and the empty optio.log channel the tail subscribes to.
    deliverables_dir = f"{host.workdir}/deliverables"
    await host.run_command(f"mkdir -p {deliverables_dir}")
    await host.write_text("optio.log", "")

    # Install the per-agent browser-open shims (if any) and expose the
    # resulting launch-env additions on the HookContext. The agent body
    # merges hook_ctx.browser_launch_env into the env it launches with.
    hook_ctx.browser_launch_env = await protocol.prepare_browser_shims(host)

    session_error: BaseException | None = None
    cancelled = False
    fetch_task: asyncio.Task | None = None
    tail_task: asyncio.Task | None = None
    body_task: asyncio.Task | None = None
    cancel_task: asyncio.Task | None = None

    try:
        # before_execute runs inside the try so a failure here still
        # triggers the after_execute cleanup in the outer finally.
        if before_execute is not None:
            await before_execute(hook_ctx)

        deliverable_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
            maxsize=DELIVERABLE_QUEUE_BOUND,
        )
        done_flag = asyncio.Event()
        error_flag: list[str | None] = []  # [message] or [] if not fired

        if keywords:
            fetch_task = asyncio.create_task(
                _deliverable_fetch_loop(host, on_deliverable, deliverable_queue, ctx, hook_ctx),
            )
            tail_task = asyncio.create_task(
                _tail_and_dispatch(
                    host, ctx, deliverable_queue, done_flag, error_flag,
                    protocol.parse_log_line, browser_url_rewrite,
                ),
            )
        body_task = asyncio.create_task(body(host, hook_ctx))
        cancel_task = asyncio.create_task(_watch_cancellation(ctx))

        wait_set = {body_task, cancel_task}
        if tail_task is not None:
            wait_set.add(tail_task)
        done, _pending = await asyncio.wait(
            wait_set, return_when=asyncio.FIRST_COMPLETED,
        )

        cancelled = (
            cancel_task in done
            and not cancel_task.cancelled()
            and cancel_task.exception() is None
            and cancel_task.result() is True
        )

        if error_flag:
            raise _SessionFailed(error_flag[0] or "agent reported ERROR")

        if body_task in done and not cancelled:
            exc = body_task.exception()
            if exc is not None:
                raise exc
            if keywords and not done_flag.is_set():
                # Body completed without DONE — premature exit.
                raise _SessionFailed("body returned before DONE was observed")

        # Drain remaining deliverables before returning.
        if keywords:
            await deliverable_queue.join()

    except BaseException as exc:
        session_error = exc
        raise

    finally:
        active_tasks = [
            t for t in (tail_task, body_task, cancel_task, fetch_task)
            if t is not None
        ]
        for t in active_tasks:
            if not t.done():
                t.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        if after_execute is not None:
            try:
                await after_execute(hook_ctx)
            except BaseException as after_exc:
                if session_error is None:
                    raise
                ctx.report_progress(
                    None,
                    f"after_execute callback raised: {after_exc!r}",
                )


# --- private helpers ---------------------------------------------------


async def _tail_and_dispatch(
    host: Host,
    ctx: "ProcessContext",
    deliverable_queue: asyncio.Queue[tuple[str, str]],
    done_flag: asyncio.Event,
    error_flag: list,
    parse_line: "Callable[[str], LogEvent]",
    browser_url_rewrite: "Callable[[str], str] | None" = None,
) -> None:
    """Consume tail_file(optio.log), parse each line, dispatch by keyword."""
    async for line in host.tail_file(f"{host.workdir}/optio.log"):
        ev: LogEvent = parse_line(line)
        if isinstance(ev, StatusEvent):
            ctx.report_progress(ev.percent, ev.message)
        elif isinstance(ev, DeliverableEvent):
            try:
                absolute = validate_deliverable_path(ev.path, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None, f"invalid deliverable path {ev.path!r}, skipping",
                )
                continue
            try:
                display = relativize_deliverable_path(absolute, host.workdir)
            except ValueError:
                ctx.report_progress(
                    None,
                    f"deliverable {ev.path!r}: not under deliverables/, "
                    "skipping (malfunction)",
                )
                continue
            ctx.report_progress(None, f"Deliverable: {display}")
            item = (absolute, display)
            try:
                deliverable_queue.put_nowait(item)
            except asyncio.QueueFull:
                await deliverable_queue.put(item)
        elif isinstance(ev, BrowserEvent):
            url = browser_url_rewrite(ev.url) if browser_url_rewrite else ev.url
            await ctx.request_browser_open(url)
        elif isinstance(ev, AttentionEvent):
            await ctx.need_attention(ev.reason)
        elif isinstance(ev, DomainMessageEvent):
            await ctx.domain_message(ev.keyword, ev.data)
        elif isinstance(ev, DoneEvent):
            if ev.summary:
                ctx.report_progress(None, ev.summary)
            done_flag.set()
            return
        elif isinstance(ev, ErrorEvent):
            error_flag.append(ev.message)
            return
        else:
            assert isinstance(ev, UnknownLine)
            if ev.text:
                ctx.report_progress(None, ev.text)


async def _deliverable_fetch_loop(
    host: Host,
    callback: DeliverableCallback | None,
    queue: asyncio.Queue[tuple[str, str]],
    ctx: "ProcessContext",
    hook_ctx: HookContext,
) -> None:
    """Drain the deliverable queue: fetch each file, decode UTF-8,
    invoke the consumer callback."""
    while True:
        absolute, display = await queue.get()
        try:
            try:
                text = await fetch_deliverable_text(host, absolute)
            except UnicodeDecodeError:
                ctx.report_progress(
                    None,
                    f"Deliverable {display}: not valid UTF-8, skipping callback",
                )
                continue
            except FileNotFoundError:
                ctx.report_progress(None, f"Deliverable {display}: not found")
                continue
            except Exception as exc:  # noqa: BLE001
                ctx.report_progress(
                    None,
                    f"Deliverable {display}: fetch failed: {exc!r}, skipping",
                )
                continue

            # Mandatory acknowledgment: every deliverable gets exactly one
            # `System: deliverable <name>: ...` reply so the agent (told to
            # wait after a DELIVERABLE line) never hangs. Three routes:
            #   - no callback / None / "" / "ok"  -> accepted
            #   - any other returned string       -> that string (revision msg)
            #   - callback raised                 -> harness-side trouble note
            name = os.path.basename(display) or display
            if callback is None:
                reply = "accepted. thanks for the good work."
            else:
                try:
                    feedback = await callback(hook_ctx, display, text)
                except Exception as exc:  # noqa: BLE001
                    ctx.report_progress(
                        None, f"on_deliverable callback raised: {exc!r}",
                    )
                    reply = (
                        "I have trouble with this one. Not your fault, but "
                        "mine. I will probably need human help. Please remember "
                        "to deliver this one again later, after you are resumed "
                        "next time."
                    )
                else:
                    if (
                        isinstance(feedback, str)
                        and feedback.strip()
                        and feedback.strip().lower() != "ok"
                    ):
                        reply = feedback.strip()
                    else:
                        reply = "accepted. thanks for the good work."
            await hook_ctx.send_to_agent(f"deliverable {name}: {reply}")
        finally:
            queue.task_done()


async def _watch_cancellation(ctx: "ProcessContext") -> bool:
    """Return True when the process is cancelled."""
    while ctx.should_continue():
        await asyncio.sleep(0.1)
    return True
