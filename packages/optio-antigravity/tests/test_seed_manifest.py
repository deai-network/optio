"""Unit tests for the antigravity seed manifest (Stage 3, Task 3.1).

Adapted from optio-grok / optio-kimicode's seed-manifest shape. Antigravity's
state tree lives under ``~/.gemini`` (shared with Gemini CLI); the isolated HOME
is ``<workdir>/home`` (see host_actions._isolation_env), so — like grok/kimi —
the engine roots capture/extract at ``host.workdir + "/home"`` and the manifest
uses ``home_subdir="home"`` with ``.gemini/`` prefixes on the include paths.

Paths/format below are the S1 spike RESULTS (real interactive Google login,
2026-07-06): the token store is ``.gemini/antigravity-cli/antigravity-oauth-token``
(nested ``token.refresh_token``), settings carry a ``trustedWorkspaces`` list
rekeyed on consume.
"""

from __future__ import annotations

from optio_agents import seeds

from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_CRED_MANIFEST,
    ANTIGRAVITY_SEED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
    _TOKEN_STORE_RELPATH,
    token_capture_is_valid,
)


def test_seed_manifest_home_and_contents():
    assert isinstance(ANTIGRAVITY_SEED_MANIFEST, seeds.SeedManifest)
    assert ANTIGRAVITY_SEED_MANIFEST.home_subdir == "home"
    # settings.json (login-provisioned) + the token store are part of the seed.
    assert ".gemini/antigravity-cli/settings.json" in ANTIGRAVITY_SEED_MANIFEST.include
    assert _TOKEN_STORE_RELPATH in ANTIGRAVITY_SEED_MANIFEST.include
    # The real token path agy writes (S1), not the old oauth_creds.json guess.
    assert _TOKEN_STORE_RELPATH == ".gemini/antigravity-cli/antigravity-oauth-token"


def test_cred_manifest_is_token_store_only():
    # The save-back manifest carries ONLY the rotating token store.
    assert ANTIGRAVITY_CRED_MANIFEST.home_subdir == "home"
    assert ANTIGRAVITY_CRED_MANIFEST.include == [_TOKEN_STORE_RELPATH]


def test_seed_captures_provisioned_set_not_just_token():
    # The full seed must carry EVERYTHING login provisions, not just the token
    # (kimi's "token but no provider -> login screen" lesson) — a strict superset.
    cred = set(ANTIGRAVITY_CRED_MANIFEST.include)
    full = set(ANTIGRAVITY_SEED_MANIFEST.include)
    assert cred.issubset(full)
    assert len(full) > len(cred)
    # The onboarding flag + config tree are provisioned state a replant needs.
    assert ".gemini/antigravity-cli/cache/onboarding.json" in full
    assert ".gemini/config" in full


def test_consume_transform_rekeys_trusted_workspaces():
    # S1: settings.json trustedWorkspaces holds the CAPTURE workdir, so a replant
    # must rekey it to the new workdir → the full seed has a consume transform.
    # The token-only cred manifest (save-back) does not.
    assert ANTIGRAVITY_SEED_MANIFEST.consume_transform is not None
    assert ANTIGRAVITY_CRED_MANIFEST.consume_transform is None


def test_seed_suffix():
    assert ANTIGRAVITY_SEED_SUFFIX == "_antigravity_seeds"


def test_creds_only_capture_rejected_without_valid_token():
    # A capture must be REJECTED unless the token store holds a usable credential.
    assert token_capture_is_valid(None) is False
    assert token_capture_is_valid(b"") is False
    assert token_capture_is_valid(b"not json") is False
    assert token_capture_is_valid(b"{}") is False
    assert token_capture_is_valid(b'{"refresh_token": ""}') is False
    # agy's REAL nested shape: {"auth_method": ..., "token": {"refresh_token": ...}}.
    assert token_capture_is_valid(b'{"token": {"refresh_token": ""}}') is False
    assert token_capture_is_valid(
        b'{"auth_method": "consumer", "token": {"refresh_token": "1//0g-real", "access_token": "x"}}'
    ) is True
    # Defensive: a future flattened shape (top-level refresh_token) also valid.
    assert token_capture_is_valid(b'{"refresh_token": "1//0g-validlookingtoken"}') is True


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_rekey_trusted_workspaces_points_at_new_workdir(tmp_path):
    # A replant must rekey settings.json trustedWorkspaces from the capture
    # workdir to the new one, preserving other keys (parse-not-append).
    import json
    from optio_host.host import LocalHost
    from optio_antigravity.seed_manifest import _rekey_trusted_workspaces

    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    sp_rel = "home/.gemini/antigravity-cli/settings.json"
    await host.write_text(
        sp_rel,
        json.dumps({"AutoUpdate": False, "trustedWorkspaces": ["/old/capture/workdir"]}),
    )
    await _rekey_trusted_workspaces(host)
    after = json.loads(open(f"{host.workdir}/{sp_rel}").read())
    assert after["trustedWorkspaces"] == [host.workdir.rstrip("/")]
    assert after["AutoUpdate"] is False  # other keys survive


@pytest.mark.asyncio
async def test_rekey_tolerates_absent_settings(tmp_path):
    # A missing/corrupt settings.json must not raise (agy rewrites on start);
    # the transform creates a minimal one trusting the new workdir.
    from optio_host.host import LocalHost
    from optio_antigravity.seed_manifest import _rekey_trusted_workspaces
    import json

    host = LocalHost(taskdir=str(tmp_path))
    await host.setup_workdir()
    await _rekey_trusted_workspaces(host)  # no settings.json present
    sp = f"{host.workdir}/home/.gemini/antigravity-cli/settings.json"
    after = json.loads(open(sp).read())
    assert after["trustedWorkspaces"] == [host.workdir.rstrip("/")]
