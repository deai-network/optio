"""Standalone seed verify/refresh for grok seeds.

Engine-free: db-first, no ProcessContext/HookContext. Plants a seed into a
throwaway workdir + GROK_HOME, runs the grok binary once headless
(``grok -p "<probe>"``) against a challenge-answer prompt, takes the verdict
from stdout only, and writes the refreshed (rotated) auth.json back into the
seed.

Direct adaptation of ``optio_opencode.verify`` (opencode → grok renames):
grok's own token refresh rotates the single-use xAI refresh_token on the probe,
exactly like opencode's loader(). Grok has no separate model gate (its model in
config.toml is optional), so — unlike opencode — the probe always runs.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from optio_host.paths import task_dir

from optio_agents import seeds
from optio_grok import host_actions
from optio_grok.seed_manifest import GROK_SEED_MANIFEST, GROK_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

# Challenge-answer probe: the answer token ("paris") must NOT appear in the
# prompt (an error path that echoes the prompt can then never false-positive)
# and must be improbable in error noise (a word, not a digit).
PROBE_PROMPT = "What is the capital of France? Answer with the city name."
PROBE_ANSWER_RE = re.compile(r"paris", re.IGNORECASE)

_AUTH_RELPATH = "home/.grok/auth.json"
_AUTH_MEMBER = ".grok/auth.json"


async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = GROK_SEED_SUFFIX,
    seed_id: str,
    ssh=None,
    install_dir: str | None = None,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> bool:
    """Verify a seed by probing grok with its credentials; refresh + save back.

    Returns True iff grok answered the challenge (the seed is alive). Never
    raises for a dead seed. Stamps the verdict as seed metadata and marks the
    seed's pool status (dead seeds are never handed out by seeds.acquire).

    Call only on a FREE seed, or one whose lease the caller holds: the probe
    rotates the single-use xAI refresh token, so verifying a seed in use by a
    live session leaves that session's next refresh stranded (and its save-back
    would clobber this one). The caller owns the lease discipline; this
    function does not acquire or check leases.

    Run on a host whose environment carries no provider API keys — inherited
    env vars could mask a dead seed.
    """
    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return False

    taskdir = task_dir(
        ssh=ssh, process_id=f"seed-verify-{uuid.uuid4().hex[:12]}",
        consumer_name="optio-grok",
    )
    host = host_actions.build_host(ssh, taskdir)
    await host.connect()
    alive = False
    try:
        await host.setup_workdir()
        grok_exec = await host_actions.resolve_grok(host, install_dir=install_dir)
        await seeds.plant_seed(
            db, host, prefix=prefix, seed_id=seed_id,
            manifest=GROK_SEED_MANIFEST, suffix=suffix, decrypt=decrypt,
        )

        stdout, exit_code = await host_actions.run_grok_probe(
            host, grok_executable=grok_exec, prompt=PROBE_PROMPT,
        )
        # Verdict: stdout-only. The exit code carries zero verdict bits (answer
        # present proves the full chain regardless; requiring exit 0 would only
        # add a false-dead path) — diagnostics only.
        alive = PROBE_ANSWER_RE.search(stdout) is not None
        if not alive:
            _LOG.info(
                "seed %s: probe dead (exit=%s, stdout[:200]=%r)",
                seed_id, exit_code, stdout[:200],
            )

        # Write back the (possibly rotated) auth.json — valid files only (same
        # validity bar as the watcher's save-back gate).
        workdir = host.workdir.rstrip("/")
        try:
            auth_raw = await host.fetch_bytes_from_host(f"{workdir}/{_AUTH_RELPATH}")
            auth = json.loads(auth_raw.decode("utf-8"))
            if isinstance(auth, dict) and auth:
                await seeds.overwrite_seed_member(
                    db, prefix=prefix, suffix=suffix, seed_id=seed_id,
                    member_path=_AUTH_MEMBER, content=auth_raw,
                    encrypt=encrypt, decrypt=decrypt,
                )
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            _LOG.warning(
                "seed %s: no valid auth.json after probe; skipping write-back",
                seed_id,
            )

        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            metadata={"verify": {
                "alive": alive,
                "checkedAt": datetime.now(timezone.utc),
            }},
        )
        await seeds.mark_seed_status(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            status="alive" if alive else "dead",
        )
        return alive
    finally:
        try:
            await host.cleanup_taskdir(aggressive=True)
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: host.disconnect failed")
