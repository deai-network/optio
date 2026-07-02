"""State machine for one optio-codex session (Stage 0: iframe/ttyd, local)."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import AsyncIterator

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_agents import HookContext, get_protocol
from optio_agents import seeds as _seeds
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle
from optio_host.paths import task_dir

from optio_codex import cred_watcher, host_actions
from optio_codex.prompt import compose_agents_md
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
from optio_codex.snapshots import (
    effective_workdir_exclude,
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_codex.types import CodexTaskConfig


_LOG = logging.getLogger(__name__)
READY_TIMEOUT_S = 30.0


def _build_host(config: CodexTaskConfig, process_id: str) -> Host:
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-codex",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


async def run_codex_session(ctx: ProcessContext, config: CodexTaskConfig) -> None:
    """Execute function body for one optio-codex task instance."""
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(browser="suppress")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    codex_path: str | None = None
    ttyd_path: str | None = None
    cancelled = False
    # Whether a snapshot was restored this run (suppresses the auto-start
    # positional). Set by _prepare, read by the body.
    resuming = False
    # The session/rollout id recorded in the restored snapshot; drives the
    # `codex resume <id>` relaunch. None ⇒ fresh codex session even when the
    # workdir was restored (the snapshot predates any rollout).
    resume_session_id: str | None = None
    # Resolved seed id for a fresh, seeded launch (Stage 3). Set by _prepare
    # (str seed_id → itself; SeedProvider callable → awaited). Stays None on
    # resume and when no seed_id is configured.
    resolved_seed_id: str | None = None
    # Stage 4 lease + credential save-back. ``lease_holder`` is the task's
    # process_id when the seed came from a lease-holding SeedProvider
    # (renewed by the watcher, released at teardown). ``cred_baseline`` is
    # the post-merge auth.json fingerprint the watcher/backstop diff against.
    lease_holder: str | None = None
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Restore a resume snapshot, provision codex + ttyd, plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail. That ordering is why the restore belongs here:
        the restored optio.log is rotated away below before the tail can
        replay its stale DONE/ERROR, and AGENTS.md is planted AFTER the
        restore so the restore cannot wipe it.

        Restore runs BEFORE ensure_codex_installed — a deliberate divergence
        from the grok template (which ensures first): codex's launch path is
        the per-task symlink INSIDE the workdir
        (<workdir>/home/.local/bin/codex), and restore_workdir empties the
        workdir before extracting. Provisioning after the restore re-creates
        the home tree and re-points the symlink (mkdir -p / ln -sfn are
        idempotent), so the launch path can never dangle.
        """
        nonlocal codex_path, ttyd_path, resuming, resume_session_id
        nonlocal resolved_seed_id, lease_holder, cred_baseline

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, ctx._prefix, ctx.process_id,
            )
        resuming = snapshot is not None
        if resuming:
            # Restore the workdir tar (carries home/.codex — sessions/,
            # auth, config). A present snapshot that fails to restore is
            # fatal — the call is intentionally outside any except so it
            # surfaces to the caller (no silent fresh-start).
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            await host_actions._rotate_optio_log(host)
            resume_session_id = snapshot.get("sessionId")
            if resume_session_id is None:
                _LOG.warning(
                    "resume: snapshot for %s carries no sessionId (codex never "
                    "persisted a rollout in that run); the workdir is restored "
                    "but codex starts a FRESH session — explicit-id resume "
                    "only, never `resume --last` (it silently mints a new "
                    "session on a miss).",
                    ctx.process_id,
                )

        codex_path = await host_actions.ensure_codex_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.codex_install_dir,
        )
        ttyd_path = await host_actions.ensure_ttyd_installed(
            hook_ctx,
            install_if_missing=config.install_ttyd_if_missing,
            install_dir=config.ttyd_install_dir,
        )
        if not resuming and config.seed_id is not None:
            # Seeded FRESH start: resolve the seed id (str → itself; a
            # SeedProvider callable → awaited, may raise
            # SeedUnavailableError) and overlay the stored codex identity
            # (auth.json + config.toml) into the fresh workdir BEFORE
            # AGENTS.md, so codex launches already-authed. Codex auth/config
            # are cwd-independent, so no rekey is needed — but the new
            # workdir must be pre-trusted (cwd-dependent, hence a post-merge
            # edit here rather than a manifest transform).
            if callable(config.seed_id):
                # A SeedProvider leases a seed from the pool (holder =
                # process_id); the watcher renews the lease, teardown
                # releases it. A plain string carries no lease.
                resolved_seed_id = await config.seed_id(ctx.process_id)
                lease_holder = ctx.process_id
            else:
                resolved_seed_id = config.seed_id
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=CODEX_SEED_MANIFEST,
                suffix=CODEX_SEED_SUFFIX,
                decrypt=None,
            )
            await host_actions.ensure_workdir_trusted(host)
            # Baseline the merged auth.json so the in-session watcher and
            # the teardown backstop only save back a genuinely rotated token.
            cred_baseline = await cred_watcher.cred_fingerprint(host)
        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                documentation=protocol.documentation if config.host_protocol else None,
                host_protocol=config.host_protocol,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
            ),
        )
        if config.supports_resume:
            await host_actions._append_resume_log_entry(host)
        if config.before_execute is not None:
            # End-of-prepare placement matches claudecode (its
            # _plant_session_content ends with before_execute, inside its
            # _prepare); opencode fires it inside the body instead.
            await config.before_execute(hook_ctx)

    async def _codex_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session

        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        codex_flags = [
            # `codex resume <id>` is a SUBCOMMAND — it must precede the flags.
            *host_actions.build_resume_args(resume_session_id),
            *host_actions.build_codex_flags(
                model=config.model,
                ask_for_approval=config.ask_for_approval,
                sandbox=config.sandbox,
            ),
            # Positional kickoff prompt: fresh launches only (suppressed when
            # a snapshot was restored — re-kicking would duplicate the task).
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Codex…")
        handle, tmux_path_local, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_codex(
            host,
            ttyd_path=ttyd_path,
            codex_path=codex_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            codex_flags=codex_flags,
            ready_timeout_s=READY_TIMEOUT_S,
            env_remove=config.scrub_env,
        )
        launched_handle = handle
        tmux_path = tmux_path_local

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "Codex is live")

        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    async def _agent_sender(message: str) -> None:
        await host_actions.send_text_to_codex(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_codex_body,
            prepare=_prepare,
            on_deliverable=config.on_deliverable,
            after_execute=config.after_execute,
            protocol=protocol,
            agent_sender=_agent_sender,
            keywords=config.host_protocol,
        )
    except _SessionFailed as fail:
        raise RuntimeError(str(fail)) from None
    finally:
        if not ctx.should_continue():
            cancelled = True
        if (
            tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and codex_path
        ):
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    codex_path=codex_path,
                    ttyd_handle=launched_handle,
                    aggressive=cancelled,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")

        # Seed capture (fresh only): store this session's codex identity as
        # a reusable seed so a later fresh task can start already-authed.
        # Reached-live gate: launched_handle is assigned strictly after a
        # successful launch — an interrupt before launch leaves it None.
        # Guarded on a VALID auth.json (capture_gate_ok) — never seed a
        # login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: home/.codex/auth.json absent "
                        "or invalid (login-less session)",
                    )
                else:
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=CODEX_SEED_MANIFEST,
                        suffix=CODEX_SEED_SUFFIX,
                        encrypt=None,
                    )
                    # 2nd arg (account summary) is resolved in a later
                    # stage; None for now.
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
            except Exception:
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )

        # Reached-live gate: only capture if codex actually came up
        # (launched_handle is assigned strictly after a successful ttyd/codex
        # launch). An interrupt before launch leaves it None — skip capture
        # so any prior good snapshot survives and hasSavedState is untouched.
        if config.supports_resume and launched_handle is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
                    # Iframe mode: scan the newest rollout filename. Plan D's
                    # conversation body passes its thread/started id through
                    # this same parameter instead.
                    session_id=await host_actions.read_latest_session_id(host),
                )
            except Exception:
                _LOG.exception(
                    "snapshot capture failed; proceeding with workdir wipe",
                )

        try:
            await host.cleanup_taskdir(aggressive=cancelled)
        except Exception:
            _LOG.exception("cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:
            _LOG.exception("host.disconnect failed")


async def _stream_blob(ctx: ProcessContext, blob_id) -> "AsyncIterator[bytes]":
    async with ctx.load_blob(blob_id) as reader:
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                break
            yield chunk


async def _capture_snapshot(
    ctx: ProcessContext,
    host: Host,
    *,
    end_state: str,
    workdir_exclude: list[str] | None,
    session_id: str | None,
) -> None:
    """Capture a single-blob resume snapshot of the (now static) workdir.

    Codex's rollout store lives under ``home/.codex/sessions`` INSIDE the
    workdir, so one workdir tar carries everything ``codex resume <id>``
    needs; ``session_id`` records WHICH session to resume. Streams the tar
    into GridFS honoring the effective exclude list, records the snapshot
    row, prunes to the retention limit (deleting stale blobs), and surfaces
    the Resume affordance.
    """
    exclude = effective_workdir_exclude(workdir_exclude)
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    await insert_snapshot(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        workdir_blob_id=workdir_blob_id,
        session_id=session_id,
    )

    stale = await prune_snapshots(ctx._db, ctx._prefix, ctx.process_id)
    for blob_id in stale:
        try:
            await ctx.delete_blob(blob_id)
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")

    await ctx.mark_has_saved_state()


def create_codex_task(
    process_id: str,
    name: str,
    config: CodexTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-codex session."""

    async def _execute(ctx: ProcessContext) -> None:
        await run_codex_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )