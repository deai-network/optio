"""Standalone seed verify/refresh for opencode seeds.

Engine-free: db-first, no ProcessContext/HookContext. Runs the opencode
binary once against a throwaway workdir (option B in the design spec —
zero per-provider auth code; opencode's own loader() refreshes/rotates
tokens) and writes the refreshed auth.json back into the seed blob.

See docs/2026-06-11-opencode-seed-save-back-design.md.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from optio_agents import seeds
from optio_agents.account import accounts_to_metadata
from optio_host.paths import task_dir

from optio_opencode import host_actions
from optio_opencode.account import analyze_accounts
from optio_opencode.seed_manifest import OPENCODE_SEED_MANIFEST, OPENCODE_SEED_SUFFIX

_LOG = logging.getLogger(__name__)

# Challenge-answer probe: the answer token must NOT appear in the prompt
# (an error path that echoes/quotes the prompt can then never false-
# positive) and must be improbable in error noise (a word, not a digit).
PROBE_PROMPT = "What is the capital of France? Answer with the city name."
PROBE_ANSWER_RE = re.compile(r"paris", re.IGNORECASE)

_AUTH_RELPATH = "home/.local/share/opencode/auth.json"
_AUTH_MEMBER = ".local/share/opencode/auth.json"
_CONFIG_RELPATH = "home/.config/opencode/opencode.json"


async def verify_and_refresh_seed(
    db,
    *,
    prefix: str,
    suffix: str = OPENCODE_SEED_SUFFIX,
    seed_id: str,
    ssh=None,
    install_dir: str | None = None,
    encrypt: "Callable[[bytes], bytes] | None" = None,
    decrypt: "Callable[[bytes], bytes] | None" = None,
) -> dict:
    """Verify a seed by probing its default provider; refresh + save back.

    Returns {"alive": bool, "accounts": list[AccountInfo], "model": str | None}.
    ``accounts`` holds one entry per configured provider on the alive path (via
    the meta-analyzer over the refreshed auth.json), else ``[]``. Never raises
    for a dead seed. Stamps the verdict + ``metadata.accounts`` as seed metadata
    and marks the seed's pool status (dead seeds are never handed out by
    seeds.acquire).

    Call only on a FREE seed, or one whose lease the caller holds: the
    probe rotates single-use refresh tokens, so verifying a seed in use by
    a live session leaves that session's next refresh stranded (and its
    save-back would clobber this one). The caller owns the lease
    discipline; this function does not acquire or check leases.

    Run on a host whose environment carries no provider API keys —
    inherited env vars could mask a dead seed.
    """
    doc = await seeds.load_seed(db, prefix=prefix, suffix=suffix, seed_id=seed_id)
    if doc is None:
        return {"alive": False, "accounts": [], "model": None}

    taskdir = task_dir(
        ssh=ssh, process_id=f"seed-verify-{uuid.uuid4().hex[:12]}",
        consumer_name="optio-opencode",
    )
    host = host_actions.build_host(ssh, taskdir)
    await host.connect()
    alive = False
    model: str | None = None
    accounts: list = []
    try:
        await host.setup_workdir()
        opencode_exec = await host_actions.ensure_opencode_installed(
            host,
            download=host_actions.curl_downloader(host),
            report_progress=None,
            install_dir=install_dir,
        )
        await seeds.plant_seed(
            db, host, prefix=prefix, seed_id=seed_id,
            manifest=OPENCODE_SEED_MANIFEST, suffix=suffix, decrypt=decrypt,
        )

        # Default-provider model: small_model if set, else model. Must stay
        # on the DEFAULT provider — that is whose token a seed-pinned task
        # will drive (and whose liveness we are certifying).
        workdir = host.workdir.rstrip("/")
        try:
            raw = await host.fetch_bytes_from_host(f"{workdir}/{_CONFIG_RELPATH}")
            cfg = json.loads(raw.decode("utf-8"))
            if isinstance(cfg, dict):
                model = cfg.get("small_model") or cfg.get("model") or None
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            model = None
        if not model:
            _LOG.warning("seed %s: no model in opencode.json; unusable -> dead", seed_id)
        else:
            stdout, exit_code = await host_actions.run_opencode_probe(
                host, opencode_executable=opencode_exec,
                model=model, prompt=PROBE_PROMPT,
            )
            # Verdict: stdout-only. The exit code carries zero verdict bits
            # (answer present proves the full chain regardless; requiring
            # exit 0 would only add a false-dead path) — diagnostics only.
            alive = PROBE_ANSWER_RE.search(stdout) is not None
            if not alive:
                _LOG.info(
                    "seed %s: probe dead (exit=%s, stdout[:200]=%r)",
                    seed_id, exit_code, stdout[:200],
                )

            # Write back the (possibly refreshed/rotated) auth.json — valid
            # files only (same validity bar as the watcher's save-back gate).
            try:
                auth_raw = await host.fetch_bytes_from_host(f"{workdir}/{_AUTH_RELPATH}")
                auth = json.loads(auth_raw.decode("utf-8"))
                if isinstance(auth, dict) and auth:
                    await seeds.overwrite_seed_member(
                        db, prefix=prefix, suffix=suffix, seed_id=seed_id,
                        member_path=_AUTH_MEMBER, content=auth_raw,
                        encrypt=encrypt, decrypt=decrypt,
                    )
                    # Meta-analyze the (refreshed) auth.json — one account per
                    # configured provider. Only on the alive path: a dead seed's
                    # tokens are unusable, so its account list is meaningless.
                    if alive:
                        accounts = await analyze_accounts(auth)
            except (FileNotFoundError, ValueError, UnicodeDecodeError):
                _LOG.warning("seed %s: no valid auth.json after probe; skipping write-back", seed_id)

        await seeds.declare_metadata(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            metadata={
                "verify": {
                    "alive": alive,
                    "checkedAt": datetime.now(timezone.utc),
                    "probedModel": model,
                },
                "accounts": accounts_to_metadata(accounts),
            },
        )
        await seeds.mark_seed_status(
            db, prefix=prefix, suffix=suffix, seed_id=seed_id,
            status="alive" if alive else "dead",
        )
        return {"alive": alive, "accounts": accounts, "model": model}
    finally:
        try:
            await host.cleanup_taskdir(aggressive=True)
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: cleanup_taskdir failed")
        try:
            await host.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("verify: host.disconnect failed")
