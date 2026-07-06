"""Unit tests for the antigravity seed manifest (Stage 3, Task 3.1).

Adapted from optio-grok / optio-kimicode's seed-manifest shape. Antigravity's
state tree lives under ``~/.gemini`` (shared with Gemini CLI); the isolated HOME
is ``<workdir>/home`` (see host_actions._isolation_env), so — like grok/kimi —
the engine roots capture/extract at ``host.workdir + "/home"`` and the manifest
uses ``home_subdir="home"`` with ``.gemini/`` prefixes on the include paths.

NOTE (S1 pending): the real credential-location spike (plan Task S1) has NOT run.
These tests pin the *shape* of the seed (a full provisioned set, not just the
rotating token; a token-only cred manifest; a validity gate) which is invariant
across the S1 outcomes. The exact token-store path/format is a placeholder to be
reconciled with S1 and is deliberately NOT asserted by literal value here.
"""

from __future__ import annotations

from optio_agents import seeds

from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_CRED_MANIFEST,
    ANTIGRAVITY_SEED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
    token_capture_is_valid,
)


def test_seed_manifest_home_and_contents():
    assert isinstance(ANTIGRAVITY_SEED_MANIFEST, seeds.SeedManifest)
    # home_subdir is the isolated HOME (engine docstring: "HOME relative to the
    # workdir, e.g. 'home'"); agy's ~/.gemini tree lives beneath it.
    assert ANTIGRAVITY_SEED_MANIFEST.home_subdir == "home"
    # settings.json (login-provisioned, non-secret) is part of the full seed.
    assert ".gemini/antigravity-cli/settings.json" in ANTIGRAVITY_SEED_MANIFEST.include


def test_cred_manifest_is_token_store_only():
    # The save-back manifest carries ONLY the rotating token store — the single
    # file agy mutates on OAuth-token refresh (mirrors grok's auth.json-only
    # GROK_CRED_MANIFEST / kimi's credentials-only KIMI_CRED_MANIFEST).
    assert ANTIGRAVITY_CRED_MANIFEST.home_subdir == "home"
    assert len(ANTIGRAVITY_CRED_MANIFEST.include) == 1


def test_seed_captures_provisioned_set_not_just_token():
    # The full seed must carry EVERYTHING login provisions, not just the token:
    # a token-only replant leaves a fresh task without the provider/account
    # registration + settings and drops back to the login screen (the kimi
    # "token but no provider" failure mode). So the full manifest is a strict
    # superset of the cred (token-only) manifest.
    cred = set(ANTIGRAVITY_CRED_MANIFEST.include)
    full = set(ANTIGRAVITY_SEED_MANIFEST.include)
    assert cred.issubset(full)
    assert len(full) > len(cred)


def test_no_consume_transform():
    # Likely-outcome (S1 pending): Google OAuth tokens + settings are
    # cwd-independent, so — like grok/kimi — no consume-time rekey.
    # TODO(S1): reconcile if settings/account registration pin a project path.
    assert ANTIGRAVITY_SEED_MANIFEST.consume_transform is None
    assert ANTIGRAVITY_CRED_MANIFEST.consume_transform is None


def test_seed_suffix():
    assert ANTIGRAVITY_SEED_SUFFIX == "_antigravity_seeds"


def test_creds_only_capture_rejected_without_valid_token():
    # A creds-only capture (the save-back path) must be REJECTED unless the token
    # store actually holds a usable credential — otherwise a logged-out / empty /
    # half-written store would poison the seed with a dead identity.
    assert token_capture_is_valid(None) is False
    assert token_capture_is_valid(b"") is False
    assert token_capture_is_valid(b"not json") is False
    assert token_capture_is_valid(b"{}") is False
    assert token_capture_is_valid(b'{"refresh_token": ""}') is False
    # A store with a non-empty refresh token is a valid capture.
    assert token_capture_is_valid(b'{"refresh_token": "1//0g-validlookingtoken"}') is True
