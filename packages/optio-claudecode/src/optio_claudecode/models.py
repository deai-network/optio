"""Fetch the account-available Claude model list for the conversation widget.

Claude Code exposes no programmatic model list, so we call the Anthropic Models
API (GET /v1/models) using the OAuth access token in the seeded
home/.claude/.credentials.json. Best-effort: any failure returns FALLBACK_MODELS
so the picker still offers the common aliases.
"""
from __future__ import annotations

import json
import logging

_LOG = logging.getLogger(__name__)

# Shown when the live fetch fails (offline, no creds, API change). The picker
# stays useful; the engine still accepts any model string on relaunch.
FALLBACK_MODELS: dict = {
    "models": [
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    ],
    "default": None,
}


def _read_oauth_token(creds_json: str) -> str | None:
    """Extract the Claude Code OAuth access token from a .credentials.json blob."""
    try:
        data = json.loads(creds_json)
    except Exception:  # noqa: BLE001
        return None
    # Claude Code stores {"claudeAiOauth": {"accessToken": "..."}} (shape may
    # vary by version; probe the common locations).
    oauth = data.get("claudeAiOauth") or data.get("oauth") or {}
    return oauth.get("accessToken") or oauth.get("access_token") or data.get("accessToken")


def parse_models(api_json: dict) -> dict:
    """Map GET /v1/models response ({data:[{id, display_name}]}) to widget shape."""
    out = []
    for m in api_json.get("data", []):
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            out.append({"id": mid, "label": m.get("display_name") or mid})
    if not out:
        return FALLBACK_MODELS
    return {"models": out, "default": None}


async def fetch_available_models(host, *, home_dir: str) -> dict:
    """Best-effort GET /v1/models with the session's OAuth token. Never raises."""
    # Read the seeded credentials. The Host API exposes file reads via
    # fetch_bytes_from_host(absolute_path) -> bytes (there is no read_file);
    # decode to text the way the rest of session.py does.
    try:
        creds = (
            await host.fetch_bytes_from_host(f"{home_dir}/.claude/.credentials.json")
        ).decode("utf-8")
    except Exception:  # noqa: BLE001
        _LOG.info("model list: no credentials file; using fallback")
        return FALLBACK_MODELS
    token = _read_oauth_token(creds)
    if not token:
        return FALLBACK_MODELS
    # Run the HTTPS GET on the host so it shares the session's network context.
    cmd = (
        "curl -fsS https://api.anthropic.com/v1/models "
        f"-H 'authorization: Bearer {token}' "
        "-H 'anthropic-version: 2023-06-01' "
        "-H 'anthropic-beta: oauth-2025-04-20'"
    )
    try:
        # host.run_command(cmd) -> RunResult(stdout: str, stderr, exit_code).
        result = await host.run_command(cmd)
        if result.exit_code != 0:
            _LOG.info(
                "model list: live fetch failed (exit %s); using fallback",
                result.exit_code,
            )
            return FALLBACK_MODELS
        return parse_models(json.loads(result.stdout))
    except Exception:  # noqa: BLE001
        _LOG.info("model list: live fetch failed; using fallback", exc_info=True)
        return FALLBACK_MODELS
