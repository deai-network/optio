"""State machine for one optio-grok session (Stage 0: iframe/ttyd, local).

Orchestrates a Host (local or remote) through resolve grok → install ttyd →
plant AGENTS.md → launch ttyd(grok) inside tmux → optio.log protocol session →
teardown.

Adapted from optio-claudecode's iframe path. Stage 0 drops the
resume/snapshot, seed, conversation, credential-planting, fs-isolation, and
in-session input-listener branches; those arrive in later stages.
"""

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
from optio_host.host import Host, LocalHost, ProcessHandle, RemoteHost
from optio_host.paths import task_dir

from optio_grok import cred_watcher, host_actions
from optio_grok.prompt import compose_agents_md
from optio_grok.seed_manifest import GROK_SEED_MANIFEST, GROK_SEED_SUFFIX
from optio_grok.snapshots import (
    insert_snapshot,
    load_latest_snapshot,
    prune_snapshots,
)
from optio_grok.types import GrokTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


def _build_host(config: GrokTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the claudecode/opencode pattern).
    """
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-grok",
    )
    if config.ssh is None:
        os.makedirs(taskdir, exist_ok=True)
        host: Host = LocalHost(taskdir=taskdir)
        os.makedirs(host.workdir, exist_ok=True)
        return host
    return RemoteHost(ssh_config=config.ssh, taskdir=taskdir)


async def run_grok_session(ctx: ProcessContext, config: GrokTaskConfig) -> None:
    """Execute function body for one optio-grok task instance."""
    host: Host = _build_host(config, ctx.process_id)
    protocol = get_protocol(browser="suppress")
    launched_handle: ProcessHandle | None = None
    tmux_path: str | None = None
    tmux_socket: str | None = None
    tmux_session: str | None = None
    grok_path: str | None = None
    ttyd_path: str | None = None
    cancelled = False
    # Whether a snapshot was restored this run (drives --continue + the
    # auto-start suppression). Set by _prepare, read by the body + teardown.
    resuming = False
    pass_continue = False
    # Resolved seed id for a fresh, seeded launch (Stage 3). Set by _prepare
    # (str seed_id → itself; SeedProvider callable → awaited). Stays None on
    # resume and when no seed_id is configured.
    resolved_seed_id: str | None = None
    # Stage 4 lease + credential save-back. ``lease_holder`` is the task's
    # process_id when the seed came from a lease-holding SeedProvider (renewed
    # by the watcher, released at teardown). ``cred_baseline`` is the
    # post-merge auth.json fingerprint the watcher/backstop diff against.
    lease_holder: str | None = None
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve grok + ttyd, restore a resume snapshot, and plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() has wiped the workdir and BEFORE it subscribes
        the optio.log tail. That ordering is why restore belongs here: the
        restored optio.log is rotated away below before the tail can replay
        its stale DONE/ERROR, and AGENTS.md is (re)planted AFTER the restore
        so it is not wiped by it.
        """
        nonlocal grok_path, ttyd_path, resuming, pass_continue, resolved_seed_id
        nonlocal lease_holder, cred_baseline
        grok_path = await host_actions.ensure_grok_installed(
            hook_ctx,
            install_if_missing=config.install_if_missing,
            install_dir=config.grok_install_dir,
        )
        ttyd_path = await host_actions.ensure_ttyd_installed(
            hook_ctx,
            install_if_missing=config.install_ttyd_if_missing,
            install_dir=config.ttyd_install_dir,
        )

        resume_requested = bool(getattr(ctx, "resume", False))
        snapshot = None
        if resume_requested:
            snapshot = await load_latest_snapshot(
                ctx._db, ctx._prefix, ctx.process_id,
            )
        resuming = snapshot is not None
        if resuming:
            # Restore the workdir tar (carries home/.grok, i.e. grok's session
            # store). A present snapshot that fails to restore is fatal — the
            # restore call is intentionally outside any except so it surfaces
            # to the caller (no silent fresh-start). grok + ttyd survive the
            # restore: grok is resolved from PATH and ttyd lives in the real
            # host home, both OUTSIDE the workdir, so no re-install is needed.
            await host.restore_workdir(_stream_blob(ctx, snapshot["workdirBlobId"]))
            await host_actions._rotate_optio_log(host)
            # A restored snapshot means grok persisted a session for this cwd;
            # --continue resumes the most recent one. GROK_HOME lives inside the
            # restored workdir at the same absolute path (deterministic taskdir),
            # so the cwd-keyed session lookup matches.
            pass_continue = True

        if not resuming and config.seed_id is not None:
            # Seeded FRESH start: resolve the seed id (str → itself; a
            # SeedProvider callable → awaited, may raise SeedUnavailableError)
            # and overlay the stored grok identity (auth.json + config.toml)
            # into the fresh workdir BEFORE AGENTS.md, so grok launches
            # already-authed. No --continue: this begins a NEW session. Grok
            # auth/config are cwd-independent, so no rekey is needed.
            if callable(config.seed_id):
                # A SeedProvider leases a seed from the pool (holder =
                # process_id); the watcher renews the lease, teardown releases
                # it. A plain string carries no lease.
                resolved_seed_id = await config.seed_id(ctx.process_id)
                lease_holder = ctx.process_id
            else:
                resolved_seed_id = config.seed_id
            await _seeds.merge_seed(
                ctx, host,
                seed_id=resolved_seed_id,
                manifest=GROK_SEED_MANIFEST,
                suffix=GROK_SEED_SUFFIX,
                decrypt=None,
            )
            # Baseline the merged auth.json so the in-session watcher and the
            # teardown backstop only save back a genuinely rotated token.
            cred_baseline = await cred_watcher.cred_fingerprint(host)

        await host.write_text(
            "AGENTS.md",
            compose_agents_md(
                config.consumer_instructions,
                host_protocol=config.host_protocol,
                workdir_exclude=config.workdir_exclude,
                supports_resume=config.supports_resume,
            ),
        )
        if config.supports_resume:
            await host_actions._append_resume_log_entry(host)
        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

    async def _grok_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, tmux_path, tmux_socket, tmux_session
        nonlocal cred_watch_task

        # Network binding (same env handling as claudecode/opencode for
        # multi-container deploys).
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        ttyd_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        grok_flags = host_actions.build_grok_flags(
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            disallowed_tools=config.disallowed_tools,
            model=config.model,
            effort=config.effort,
            reasoning_effort=config.reasoning_effort,
            no_leader=config.no_leader,
            resuming=pass_continue,
        )
        grok_flags = [
            *grok_flags,
            *host_actions.build_auto_start_args(
                auto_start=config.auto_start, resuming=resuming,
            ),
        ]
        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Grok Build…")
        handle, ttyd_port, tmux_socket, tmux_session = await host_actions.launch_ttyd_with_grok(
            host,
            ttyd_path=ttyd_path,
            grok_path=grok_path,
            bind_iface=ttyd_iface,
            extra_env=launch_env,
            grok_flags=grok_flags,
            ready_timeout_s=READY_TIMEOUT_S,
            env_remove=config.scrub_env,
        )
        launched_handle = handle
        tmux_path = await host_actions._require_tmux(host)

        worker_port = await host.establish_tunnel(ttyd_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")
        await ctx.set_widget_data({
            "iframeSrc": "{widgetProxyUrl}/",
        })
        ctx.report_progress(None, "Grok Build is live")

        # Start the in-session credential watcher for a seeded session: it
        # saves back the rotated auth.json, and (when the seed is leased)
        # renews the lease and aborts the session on lease loss.
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(
                cred_watcher.run_credential_watcher(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=None,
                    decrypt=None,
                    lease_holder=lease_holder,
                )
            )

        # Await the grok process inside tmux (NOT the ttyd connection). ttyd
        # stays up serving viewers; the task is alive while the tmux session
        # is. The protocol driver cancels this body when it sees DONE/ERROR in
        # optio.log; if grok exits some other way, has-session goes false and
        # the body returns -> driver treats it as premature exit.
        while ctx.should_continue() and await host_actions.tmux_session_alive(
            host, tmux_path, tmux_socket, tmux_session,
        ):
            await asyncio.sleep(1.0)

    async def _agent_sender(message: str) -> None:
        await host_actions.send_text_to_grok(
            host, tmux_path, tmux_socket, tmux_session, message,
        )

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_grok_body,
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
            launched_handle is not None
            and tmux_path is not None
            and tmux_socket is not None
            and tmux_session is not None
            and grok_path
        ):
            try:
                await host_actions.teardown_session_tree(
                    host,
                    tmux_path=tmux_path,
                    tmux_socket=tmux_socket,
                    tmux_session=tmux_session,
                    grok_path=grok_path,
                    ttyd_handle=launched_handle,
                    aggressive=cancelled,
                )
            except Exception:
                _LOG.exception("teardown_session_tree failed")

        # Stop the credential watcher before the final save-back so the two
        # never race on the same seed blob.
        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        # Final backstop save-back — LOAD-BEARING, not defensive: grok's own
        # auth write-back is best-effort and the xAI provider has already
        # consumed the old refresh token; a rotation in the last poll window is
        # persisted ONLY here. Runs after grok terminated so auth.json is final.
        if resolved_seed_id is not None:
            try:
                cred_baseline = await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=None,
                    decrypt=None,
                )
            except Exception:
                _LOG.exception("final credential save-back failed")

        # Release the lease AFTER the final save-back (opencode's deliberate
        # ordering): a new acquirer must never merge the pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=GROK_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")

        # Seed capture (fresh only): store this session's grok identity as a
        # reusable seed so a later fresh task can start already-authed. Same
        # reached-live gate as snapshots (launched_handle assigned strictly
        # after a successful launch). Guarded on auth.json present — never
        # seed a login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: home/.grok/auth.json absent or "
                        "invalid (login-less session)",
                    )
                else:
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=GROK_SEED_MANIFEST,
                        suffix=GROK_SEED_SUFFIX,
                        encrypt=None,
                    )
                    # 2nd arg (account summary) is resolved in a later stage;
                    # None in Stage 3.
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
            except Exception:
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )

        # Reached-live gate: only capture if grok actually came up
        # (launched_handle is assigned strictly after a successful ttyd/grok
        # launch). An interrupt before launch leaves it None — skip capture so
        # any prior good snapshot survives and hasSavedState is untouched.
        if config.supports_resume and launched_handle is not None:
            try:
                await _capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    workdir_exclude=config.workdir_exclude,
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
) -> None:
    """Capture a single-blob resume snapshot of the (now static) workdir.

    Grok's session store lives under ``home/.grok`` INSIDE the workdir, so one
    plaintext workdir tar carries everything ``--continue`` needs — no separate
    session blob (unlike optio-claudecode) and no defensive home wipe. Streams
    the tar into GridFS, records the snapshot row, prunes to the retention
    limit (deleting stale blobs), and surfaces the Resume affordance.
    """
    async with ctx.store_blob("workdir") as wwriter:
        async for chunk in host.archive_workdir(workdir_exclude):
            await wwriter.write(chunk)
        workdir_blob_id = wwriter.file_id

    await insert_snapshot(
        ctx._db, ctx._prefix,
        process_id=ctx.process_id,
        end_state=end_state,
        workdir_blob_id=workdir_blob_id,
    )

    stale = await prune_snapshots(ctx._db, ctx._prefix, ctx.process_id)
    for blob_id in stale:
        try:
            await ctx.delete_blob(blob_id)
        except Exception:
            _LOG.exception("delete_blob(workdir) failed")

    await ctx.mark_has_saved_state()


def create_grok_task(
    process_id: str,
    name: str,
    config: GrokTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-grok session.

    ``metadata`` is the caller app's task-tagging payload; it is stamped onto
    the TaskInstance verbatim and never read by the task itself.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_grok_session(ctx, config)

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget="iframe",
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
