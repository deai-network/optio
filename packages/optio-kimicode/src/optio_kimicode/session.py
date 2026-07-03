"""State machine for one optio-kimicode session (iframe/kimi web, local + SSH).

Orchestrates a Host (local or remote) through resolve kimi → plant AGENTS.md →
launch ``kimi server run --foreground`` (the ``kimi web`` surface) → establish a
tunnel → inject the bearer token via the ``#token=`` URL fragment → pre-create a
kimi session and point the iframe at it → optio.log protocol session → teardown.

The iframe surface is driven the **opencode** way (not grok's): kimi web is a
pure web server with no ``--continue`` / no positional prompt, so the wrapper
pre-creates a session over REST (``POST /api/v1/sessions``), points the iframe at
``/sessions/<id>``, and injects agent input (the auto-start kickoff, the resume
notice, and ``_agent_sender`` feedback) via ``POST /api/v1/sessions/<id>/prompts``.

Resume is the two halves of resume-awareness: the PULL half restores the kimi
session store from a snapshot, appends ``resume.log``, and rotates a stale
``optio.log`` out of the way; the PUSH half POSTs ``System: you have been
resumed`` to the recovered session so the agent notices promptly. Seed,
credential-planting, conversation mode, and fs-isolation arrive in later stages.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import shlex

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance

from optio_agents import HookContext, get_protocol
from optio_agents import RESUME_NOTICE, SYSTEM_MESSAGE_PREFIX
from optio_agents import seeds as _seeds
from optio_agents.protocol.session import _SessionFailed, run_log_protocol_session
from optio_host.host import Host, LocalHost, ProcessHandle, proc_wait
from optio_host.paths import task_dir

from optio_kimicode import cred_watcher, host_actions
from optio_kimicode.prompt import compose_agents_md
from optio_kimicode.seed_manifest import KIMI_SEED_MANIFEST, KIMI_SEED_SUFFIX
from optio_kimicode.snapshots import (
    capture_snapshot,
    load_latest_snapshot,
    restore_snapshot,
)
from optio_kimicode.types import KimiCodeTaskConfig


_LOG = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0

# Fresh-launch kickoff prompt POSTed to the pre-created kimi session so the agent
# starts the task unattended. Suppressed on resume (opencode parity).
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"


async def _call_maybe_async(fn, *args) -> None:
    """Invoke a callback that may be sync or async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        await result


def _teardown_aggressive(*, cancelled: bool, seeded: bool) -> bool:
    """Whether to SIGKILL kimi immediately on teardown vs SIGTERM-and-wait.

    A **seeded** session is torn down GRACEFULLY even on cancel: kimi's
    single-use refresh token may have rotated this session, and kimi's
    credential write is best-effort — an aggressive SIGKILL can beat the flush,
    stranding the rotation so the credential save-back persists the now-spent
    token and the next launch demands re-auth. SIGTERM-and-wait lets kimi flush
    ``kimi-code.json`` first. A non-seeded session keeps the fast aggressive kill
    on cancel.
    """
    return cancelled and not seeded


def _build_host(config: KimiCodeTaskConfig, process_id: str) -> Host:
    """Construct the appropriate Host for the given config.

    Extracted so tests can monkeypatch ``session._build_host`` to inject a
    fake host (mirrors the grok/claudecode pattern). Delegates to
    ``host_actions.build_host`` (shared with verify)."""
    taskdir = task_dir(
        ssh=config.ssh, process_id=process_id, consumer_name="optio-kimicode",
    )
    return host_actions.build_host(config.ssh, taskdir)


async def run_kimicode_session(ctx: ProcessContext, config: KimiCodeTaskConfig) -> None:
    """Execute function body for one optio-kimicode task instance."""
    host: Host = _build_host(config, ctx.process_id)
    # ``redirect``: kimi's first-login device-code URL is surfaced to the
    # operator (no browser interception needed — kimi prints the URL). Parity
    # with grok's protocol wiring; Stage 0 has no auth yet, so this is inert.
    protocol = get_protocol(browser="redirect")
    launched_handle: ProcessHandle | None = None
    cancelled = False
    kimi_path: str | None = None

    # Set by _prepare (after the workdir wipe, before the optio.log tail); read
    # by the body, _agent_sender, and the teardown finally.
    resuming = False
    preserved_session_id: str | None = None

    # Stage-4 seed lease + credential save-back. ``resolved_seed_id`` is the
    # seed planted for a fresh, seeded launch (str seed_id → itself; a
    # SeedProvider callable → awaited); None on resume / when unseeded.
    # ``lease_holder`` is the task's process_id when the seed came from a
    # lease-holding SeedProvider (renewed by the watcher, released at teardown).
    # ``cred_baseline`` is the post-merge kimi-code.json fingerprint the watcher
    # + backstop diff against; ``cred_watch_task`` is the in-session watcher.
    resolved_seed_id: str | None = None
    lease_holder: str | None = None
    cred_baseline: str | None = None
    cred_watch_task: "asyncio.Task | None" = None

    # Set by the body at launch; read by _agent_sender.
    worker_port: int | None = None
    token: str | None = None
    session_id: str | None = None

    await host.connect()

    async def _prepare(host: Host, hook_ctx: HookContext) -> None:
        """Resolve kimi, restore a resume snapshot, plant AGENTS.md.

        Handed to run_log_protocol_session, which runs it AFTER
        host.setup_workdir() wiped the workdir and BEFORE it subscribes the
        optio.log tail — so the resume restore + optio.log rotation land before
        the tail can re-emit a stale DONE.
        """
        nonlocal kimi_path, resuming, preserved_session_id
        nonlocal resolved_seed_id, lease_holder, cred_baseline
        # Two-tier provision: reuse a worker kimi on the login-shell PATH (fast
        # copy), else vendor-install, into an evictable cache OUTSIDE the workdir;
        # returns the per-task launch symlink ``<workdir>/home/.local/bin/kimi``.
        kimi_path = await host_actions.ensure_kimicode_installed(
            host,
            install_dir=config.kimi_install_dir,
            install_if_missing=config.install_if_missing,
        )

        snapshot = None
        if getattr(ctx, "resume", False) and config.supports_resume:
            snapshot = await load_latest_snapshot(ctx._db, ctx._prefix, ctx.process_id)
        resuming = snapshot is not None

        if resuming:
            # PULL half: restore the kimi session store (home/sessions) under
            # the identical workdir path (workDirKey pins on the abs path), then
            # rotate the restored optio.log so its stale DONE is not replayed.
            await restore_snapshot(
                ctx, host, snapshot,
                session_blob_decrypt=config.session_blob_decrypt,
            )
            # The launch symlink (home/.local/bin/kimi) lives INSIDE the workdir
            # and was wiped + re-materialized by the restore; re-establish it
            # against the cache (which lives OUTSIDE the workdir and survives).
            # Idempotent: cache hit → just relinks, no reinstall/redownload.
            kimi_path = await host_actions.ensure_kimicode_installed(
                host,
                install_dir=config.kimi_install_dir,
                install_if_missing=config.install_if_missing,
            )
            await host_actions.rotate_optio_log(host)
            preserved_session_id = await _recover_session_id(host)
        else:
            # Seeded FRESH start: resolve the seed id (str → itself; a
            # SeedProvider callable → awaited, may raise SeedUnavailableError)
            # and overlay the stored kimi identity (credentials/kimi-code.json)
            # into the fresh workdir BEFORE AGENTS.md, so kimi launches
            # already-authed. No resume/preserved session: this begins anew.
            # kimi credentials are cwd-independent, so no rekey is needed.
            if config.seed_id is not None:
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
                    manifest=KIMI_SEED_MANIFEST,
                    suffix=KIMI_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
                # Baseline the merged kimi-code.json so the in-session watcher
                # and the teardown backstop only save back a genuinely rotated
                # token.
                cred_baseline = await cred_watcher.cred_fingerprint(host)

            # Fresh: plant the AGENTS.md the agent consumes. (On resume the
            # snapshot-restored AGENTS.md is kept.)
            await host.write_text(
                "AGENTS.md",
                compose_agents_md(
                    config.consumer_instructions,
                    host_protocol=config.host_protocol,
                    workdir_exclude=config.workdir_exclude,
                    supports_resume=config.supports_resume,
                    file_download=config.file_download,
                ),
            )

        if config.supports_resume:
            await host_actions.append_resume_log_entry(host)

        if config.before_execute is not None:
            await config.before_execute(hook_ctx)

    async def _iframe_body(host: Host, hook_ctx: HookContext) -> None:
        nonlocal launched_handle, worker_port, token, session_id
        nonlocal cred_watch_task

        # Network binding (same env handling as grok/claudecode for
        # multi-container deploys).
        bind_addr = os.environ.get("OPTIO_WIDGET_TUNNEL_BIND", "127.0.0.1")
        upstream_host = os.environ.get("OPTIO_WIDGET_TUNNEL_HOST", "127.0.0.1")
        server_iface = bind_addr if isinstance(host, LocalHost) else "127.0.0.1"

        launch_env = {
            **(config.env or {}),
            **(hook_ctx.browser_launch_env or {}),
        }
        ctx.report_progress(None, "Launching Kimi Code…")
        handle, server_port, token = await host_actions.launch_kimi_web(
            host,
            kimi_path=kimi_path,
            bind_iface=server_iface,
            extra_env=launch_env,
            env_remove=config.scrub_env,
            ready_timeout_s=READY_TIMEOUT_S,
        )
        launched_handle = handle

        worker_port = await host.establish_tunnel(server_port, bind_addr=bind_addr)
        await ctx.set_widget_upstream(f"http://{upstream_host}:{worker_port}")

        # Pre-create (or, on resume, reuse) a single kimi session for this task.
        # All dashboards embedding the widget navigate to the same session id via
        # the iframe URL, so concurrent viewers share live state rather than each
        # opening a fresh session (one background process, N observers).
        if preserved_session_id is not None:
            session_id = preserved_session_id
        else:
            session_id = await _create_kimi_session(worker_port, token, host.workdir)

        # Point the iframe directly at the session. kimi-web recognises exactly
        # one deep-link path shape — ``/sessions/<id>`` (apps/kimi-web/src/lib/
        # sessionRoute.ts) — and reads the bearer from the ``#token=`` fragment
        # (apps/kimi-web/src/api/daemon/serverAuth.ts), a client-side fragment
        # the SPA scrubs from the URL and never sends to the server.
        fragment = f"#token={token}" if token else ""
        await ctx.set_widget_data({
            "iframeSrc": f"{{widgetProxyUrl}}/sessions/{session_id}{fragment}",
        })
        ctx.report_progress(None, "Kimi Code is live")

        # Start the in-session credential watcher for a seeded session: it saves
        # back the rotated kimi-code.json, and (when the seed is leased) renews
        # the lease and aborts the session on lease loss.
        if resolved_seed_id is not None:
            cred_watch_task = asyncio.create_task(
                cred_watcher.run_credential_watcher(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                    lease_holder=lease_holder,
                )
            )

        # auto_start: on a fresh launch, POST the kickoff prompt so kimi starts
        # the task unattended. On resume, PUSH the resume notice instead so the
        # rehydrated agent notices promptly (resume.log stays the pull-based
        # source of truth). Mirrors opencode session.py lines ~418-432.
        if config.auto_start and not resuming:
            await _post_kimi_prompt(worker_port, token, session_id, AUTO_START_PROMPT)
        elif resuming and config.supports_resume:
            await _post_kimi_prompt(
                worker_port, token, session_id,
                f"{SYSTEM_MESSAGE_PREFIX}{RESUME_NOTICE}",
            )

        # Await the kimi server. The protocol driver cancels this body when it
        # sees DONE/ERROR in optio.log; if the server exits on its own,
        # proc_wait returns and the body returns → driver treats it as a
        # premature exit. (kimi's server is long-lived — it does not exit on
        # task completion, unlike grok's TUI.)
        wait_task = asyncio.create_task(proc_wait(handle))
        try:
            while ctx.should_continue():
                if wait_task.done():
                    break
                await asyncio.sleep(1.0)
        finally:
            if not wait_task.done():
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

    async def _agent_sender(message: str) -> None:
        # worker_port / token / session_id are set by _iframe_body at launch.
        # _post_kimi_prompt raises on a non-2xx / unreachable worker, which
        # send_to_agent converts to False.
        await _post_kimi_prompt(worker_port, token, session_id, message)

    try:
        await run_log_protocol_session(
            host, ctx,
            body=_iframe_body,
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
        # kimi authenticates with a SINGLE-USE rotating refresh token. If kimi
        # rotated it this session, the new kimi-code.json must reach the seed via
        # the backstop below — but an aggressive SIGKILL can beat kimi's flush,
        # stranding the rotation (the seed keeps the now-spent token → the next
        # launch demands re-auth). So when a SEED is in use, tear kimi down
        # GRACEFULLY (SIGTERM + wait, ≤5s) even on cancel, giving it time to
        # persist kimi-code.json before the final save-back reads it. Only a
        # non-seeded session keeps the fast aggressive kill on cancel.
        kimi_aggressive = _teardown_aggressive(
            cancelled=cancelled, seeded=resolved_seed_id is not None,
        )
        # kimi serves its own SPA — there is no tmux/ttyd tree. Terminate the
        # server subprocess directly. A cancelled non-seeded session is torn
        # down aggressively; a clean completion or any seeded session uses
        # SIGTERM so kimi shuts its socket down (and flushes creds) gracefully.
        if launched_handle is not None:
            try:
                await host.terminate_subprocess(
                    launched_handle, aggressive=kimi_aggressive,
                )
            except Exception:
                _LOG.exception("terminate kimi server subprocess failed")

        # Stop the credential watcher before the final save-back so the two
        # never race on the same seed blob.
        if cred_watch_task is not None:
            cred_watch_task.cancel()
            try:
                await cred_watch_task
            except asyncio.CancelledError:
                pass

        # Final backstop save-back — LOAD-BEARING, not defensive: kimi's own
        # credential write is best-effort and the kimi provider has already
        # consumed the old refresh token; a rotation in the last poll window is
        # persisted ONLY here. Runs after kimi terminated so kimi-code.json is
        # final (the graceful teardown above ensured the flush completed).
        if resolved_seed_id is not None:
            try:
                cred_baseline = await cred_watcher.save_back_if_changed(
                    ctx, host,
                    seed_id=resolved_seed_id,
                    baseline=cred_baseline,
                    encrypt=config.session_blob_encrypt,
                    decrypt=config.session_blob_decrypt,
                )
            except Exception:
                _LOG.exception("final credential save-back failed")

        # Release the lease AFTER the final save-back (opencode's deliberate
        # ordering): a new acquirer must never merge the pre-save-back blob.
        if lease_holder is not None and resolved_seed_id is not None:
            try:
                await _seeds.release(
                    ctx._db, prefix=ctx._prefix, suffix=KIMI_SEED_SUFFIX,
                    seed_id=resolved_seed_id, holder=lease_holder,
                )
            except Exception:
                _LOG.exception("lease release failed (TTL will reclaim)")

        # Seed capture (fresh only): store this session's kimi identity as a
        # reusable seed so a later fresh task can start already-authed. Same
        # reached-live gate as snapshots (launched_handle assigned strictly
        # after a successful launch). Guarded on kimi-code.json present — never
        # seed a login-less identity. Ignored on resume.
        if (
            not resuming
            and config.on_seed_saved is not None
            and launched_handle is not None
        ):
            try:
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: home/credentials/kimi-code.json "
                        "absent or invalid (login-less session)",
                    )
                else:
                    seed_id = await _seeds.capture_seed(
                        ctx, host,
                        manifest=KIMI_SEED_MANIFEST,
                        suffix=KIMI_SEED_SUFFIX,
                        encrypt=config.session_blob_encrypt,
                    )
                    await _call_maybe_async(config.on_seed_saved, seed_id, None)
            except Exception:
                _LOG.exception(
                    "seed capture failed; callback not fired, teardown continues",
                )

        # Capture a resume snapshot of the now-static workdir + session store.
        # Gated on supports_resume + a launched handle (a session actually ran).
        if config.supports_resume and launched_handle is not None:
            try:
                await capture_snapshot(
                    ctx, host,
                    end_state="cancelled" if cancelled else "done",
                    session_blob_encrypt=config.session_blob_encrypt,
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


# --- kimi REST helpers (session pre-create / prompt push) ------------------
#
# All under kimi's real ``/api/v1`` prefix (apps/kimi-web/src/api/config.ts
# buildRestUrl). Auth is ``Authorization: Bearer <token>`` (the banner token;
# packages/server/src/middleware/auth.ts). The blocking urllib calls run in an
# executor and retry transient connect/read errors because the first request
# over a freshly-opened SSH local forward occasionally drops while asyncssh
# wires up the channel (opencode's rationale).


def _bearer_headers(token: str | None) -> dict:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers


def _create_kimi_session_sync(port: int, token: str | None, directory: str) -> str:
    """POST kimi's ``/api/v1/sessions`` and return the new session id.

    Body mirrors ``createSessionRequestSchema`` (``sessionCreateSchema``):
    ``{metadata:{cwd}}`` (either ``workspace_id`` or ``metadata.cwd`` is
    required; the server registers the cwd). The reply is the standard envelope
    ``{code,msg,data:Session,request_id}`` — the session id is ``data.id``."""
    import time
    import urllib.request
    from urllib.error import URLError

    url = f"http://127.0.0.1:{port}/api/v1/sessions"
    payload = json.dumps({"metadata": {"cwd": directory}}).encode("utf-8")
    headers = _bearer_headers(token)

    last_exc: Exception | None = None
    body = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.15 * attempt)
        req = urllib.request.Request(url, method="POST", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
            break
        except (URLError, ConnectionError, OSError) as exc:
            last_exc = exc
            continue
    else:
        raise RuntimeError(f"kimi POST /sessions failed after retries: {last_exc!r}")

    envelope = json.loads(body)
    data = envelope.get("data") if isinstance(envelope, dict) else None
    session_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError(
            f"kimi POST /sessions envelope has no string data.id: {body!r}"
        )
    return session_id


async def _create_kimi_session(port: int, token: str | None, directory: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _create_kimi_session_sync, port, token, directory,
    )


def _post_kimi_prompt_sync(
    port: int, token: str | None, session_id: str, message: str,
) -> None:
    """POST a text prompt to kimi's ``/api/v1/sessions/<id>/prompts``.

    Body mirrors ``promptSubmissionSchema``: a non-empty ``content`` array of
    message-content parts — here a single ``{type:'text',text}`` part."""
    import time
    import urllib.request
    from urllib.error import URLError

    url = f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}/prompts"
    payload = json.dumps(
        {"content": [{"type": "text", "text": message}]}
    ).encode("utf-8")
    headers = _bearer_headers(token)

    last_exc: Exception | None = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.15 * attempt)
        req = urllib.request.Request(url, method="POST", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            return
        except (URLError, ConnectionError, OSError) as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"kimi session prompt failed after retries: {last_exc!r}")


async def _post_kimi_prompt(
    port: int, token: str | None, session_id: str, message: str,
) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _post_kimi_prompt_sync, port, token, session_id, message,
    )


async def _recover_session_id(host: Host) -> str | None:
    """Recover the restored kimi session id from the on-disk session store.

    After a snapshot restore, kimi's session store lives at
    ``<workdir>/home/sessions/<workDirKey>/<sessionId>/state.json`` (design §1).
    The session id is that dir's name — recovered so the resumed iframe targets
    (and the resume notice POSTs to) the SAME session rather than a fresh one.
    Returns None when no restored session is found (body then pre-creates one).
    """
    workdir = host.workdir.rstrip("/")
    sessions_root = f"{workdir}/home/sessions"
    result = await host.run_command(
        f"find {shlex.quote(sessions_root)} -name state.json -type f 2>/dev/null "
        f"| head -n1 || true"
    )
    path = (result.stdout or "").strip()
    if not path:
        return None
    # .../sessions/<workDirKey>/<sessionId>/state.json → <sessionId>.
    return os.path.basename(os.path.dirname(path))


def create_kimicode_task(
    process_id: str,
    name: str,
    config: KimiCodeTaskConfig,
    description: str | None = None,
    metadata: dict | None = None,
) -> TaskInstance:
    """Return a TaskInstance that runs one optio-kimicode session.

    ``metadata`` is the caller app's task-tagging payload; it is stamped onto
    the TaskInstance verbatim and never read by the task itself.
    """

    async def _execute(ctx: ProcessContext) -> None:
        await run_kimicode_session(ctx, config)

    # iframe → the kimi web SPA widget. Conversation mode (Stage 6) carries the
    # live chat widget only when conversation_ui is on; otherwise no widget.
    if config.conversation_ui:
        ui_widget: str | None = "conversation"
    elif config.mode == "conversation":
        ui_widget = None
    else:
        ui_widget = "iframe"

    return TaskInstance(
        execute=_execute,
        process_id=process_id,
        name=name,
        description=description,
        ui_widget=ui_widget,
        supports_resume=config.supports_resume,
        metadata=metadata or {},
    )
