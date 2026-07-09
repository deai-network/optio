"""xai provider handler — the ``api`` (raw api-key) branch.

An opencode ``xai`` entry can be ``{"type": "api", "key": "xai-..."}`` instead
of oauth. The only identity surface reachable with a raw api-key is
``GET https://api.x.ai/v1/api-key`` (Bearer <key>), which returns the key's
metadata (owning ``user_id``/``team_id``, the key's label, acls, block flags) —
no name, no email, no plan, no usage. The handler maps ``user_id`` (fallback
``team_id``) into ``account_id`` and stashes the whole body under ``raw``.

The network fetch is mocked at the ``_fetch_api_key`` seam — never the real
x.ai API — so every case is hermetic. Fixture: a real PII-scrubbed capture.
"""

import json
import pathlib

import pytest

from optio_agents.account import EMPTY, AccountInfo
from optio_opencode.providers import xai

FIX = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def api_key_body():
    return json.loads((FIX / "account_xai_apikey.json").read_text())


async def test_api_branch_maps_user_id_and_stashes_body(monkeypatch, api_key_body):
    async def _fetch(key):
        assert key == "xai-secret-key"
        return api_key_body

    monkeypatch.setattr(xai, "_fetch_api_key", _fetch)

    info = await xai.handle({"type": "api", "key": "xai-secret-key"})

    assert isinstance(info, AccountInfo)
    # account_id = user_id; the key's ``name`` label is NOT used as identity.
    assert info.account_id == api_key_body["user_id"]
    assert info.name is None
    assert info.email is None
    assert info.plan is None
    assert info.windows == ()
    # No plan → no summary; the meta-analyzer keeps it (account_id shows).
    assert info.summary is None
    assert info != EMPTY
    assert info.raw == {"api_key": api_key_body}


async def test_api_branch_falls_back_to_team_id(monkeypatch):
    body = {"team_id": "team-xyz", "name": "Default", "acls": []}

    async def _fetch(key):
        return body

    monkeypatch.setattr(xai, "_fetch_api_key", _fetch)

    info = await xai.handle({"type": "api", "key": "k"})
    assert info is not None
    assert info.account_id == "team-xyz"


async def test_api_branch_missing_key_declines_without_network(monkeypatch):
    async def _boom(key):
        raise AssertionError("must not fetch when the key is absent")

    monkeypatch.setattr(xai, "_fetch_api_key", _boom)
    assert await xai.handle({"type": "api"}) is None


async def test_api_branch_fetch_none_declines(monkeypatch):
    async def _fetch(key):
        return None

    monkeypatch.setattr(xai, "_fetch_api_key", _fetch)
    assert await xai.handle({"type": "api", "key": "k"}) is None


async def test_api_branch_fetch_raising_is_fail_soft(monkeypatch):
    async def _fetch(key):
        raise RuntimeError("x.ai exploded")

    monkeypatch.setattr(xai, "_fetch_api_key", _fetch)
    assert await xai.handle({"type": "api", "key": "k"}) is None


async def test_api_branch_no_identity_declines(monkeypatch):
    # A body with neither user_id nor team_id has no identity → placeholder.
    async def _fetch(key):
        return {"name": "Default", "acls": []}

    monkeypatch.setattr(xai, "_fetch_api_key", _fetch)
    assert await xai.handle({"type": "api", "key": "k"}) is None


async def test_oauth_branch_unchanged(monkeypatch):
    # Regression guard: the oauth path still delegates to account_from_xai and
    # never touches the api-key fetch seam.
    async def _from_xai(access_token):
        assert access_token == "xai-access-tok"
        return AccountInfo(plan="xAI Team", account_id="xai-1",
                           raw={"provider": "xai"})

    async def _boom(key):
        raise AssertionError("oauth path must not hit the api-key fetch")

    monkeypatch.setattr(xai, "account_from_xai", _from_xai)
    monkeypatch.setattr(xai, "_fetch_api_key", _boom)

    info = await xai.handle({"type": "oauth", "access": "xai-access-tok"})
    assert info is not None
    assert info.plan == "xAI Team"
    assert info.account_id == "xai-1"
