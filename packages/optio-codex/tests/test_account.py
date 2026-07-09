"""analyze_account unit tests over the committed live-seed fixtures (offline
id_token decode + monkeypatched wham/usage fetch), a fail-soft test, and a
verify test asserting the alive path carries an AccountInfo.

No network: the wham/usage HTTP fetch is monkeypatched to return the fixture
JSON; identity is decoded offline from a JWT built out of the claims fixture.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
from datetime import datetime, timezone

from bson import ObjectId
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_agents.account import EMPTY, AccountInfo, UsageWindow
from optio_codex import account, verify
from optio_codex.account import analyze_account
from optio_codex.seed_manifest import CODEX_SEED_MANIFEST, CODEX_SEED_SUFFIX
from optio_codex.verify import verify_and_refresh_seed

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _make_id_token(claims: dict) -> str:
    """A JWT whose middle segment b64url-decodes to ``claims`` (header/sig are
    inert — analyze_account only decodes the payload, no signature check)."""
    def _seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{_seg({'alg': 'RS256'})}.{_seg(claims)}.sig"


async def test_analyze_account_maps_fixture(monkeypatch):
    claims = _fixture("codex_id_token_claims_free.json")
    usage = _fixture("codex_wham_usage_free.json")
    account_uuid = claims["https://api.openai.com/auth"]["chatgpt_account_id"]

    seen = {}

    async def fake_fetch(access_token, acct_id):
        seen["args"] = (access_token, acct_id)
        return usage

    monkeypatch.setattr(account, "_fetch_usage", fake_fetch)

    creds = {
        "id_token": _make_id_token(claims),
        "access_token": "ACCESS-TOKEN",
        "account_id": account_uuid,
    }
    info = await analyze_account(creds)

    # identity (name/email offline from id_token; plan from live usage)
    assert info.name == "Ada Lovelace"
    assert info.email == "user@example.com"
    assert info.plan == "Free"
    # the account uuid comes from creds.account_id, NOT usage.account_id
    assert info.account_id == account_uuid
    assert info.account_id != usage["account_id"]  # usage.account_id is the user id
    # the fetch received the bearer + the account-id header value
    assert seen["args"] == ("ACCESS-TOKEN", account_uuid)

    # windows: only the primary window is populated on the free seed
    assert len(info.windows) == 1
    w = info.windows[0]
    assert w.label == "primary"
    assert w.pct == 68.0
    assert w.model is None
    assert w.resets_at == datetime.fromtimestamp(1784650946, tz=timezone.utc)

    # human-readable summary threads through
    assert info.summary == "Plan: Free for Ada Lovelace <user@example.com>"


async def test_analyze_account_falls_back_to_id_token_when_usage_none(monkeypatch):
    """A soft usage failure (fetch returns None) still yields offline identity
    from the id_token, with no windows — graceful degradation (not EMPTY)."""
    claims = _fixture("codex_id_token_claims_free.json")

    async def none_fetch(access_token, acct_id):
        return None

    monkeypatch.setattr(account, "_fetch_usage", none_fetch)
    creds = {
        "id_token": _make_id_token(claims),
        "access_token": "A",
        "account_id": "00000000-0000-4000-8000-000000000000",
    }
    info = await analyze_account(creds)
    assert info.name == "Ada Lovelace"
    assert info.email == "user@example.com"
    assert info.plan == "Free"  # from id_token chatgpt_plan_type
    assert info.windows == ()


async def test_analyze_account_fail_soft_on_fetch_raise(monkeypatch):
    """A raising fetch must NOT propagate — analyze_account returns EMPTY."""
    claims = _fixture("codex_id_token_claims_free.json")

    async def boom(access_token, acct_id):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(account, "_fetch_usage", boom)
    creds = {
        "id_token": _make_id_token(claims),
        "access_token": "A",
        "account_id": "00000000-0000-4000-8000-000000000000",
    }
    info = await analyze_account(creds)
    assert info is EMPTY


async def test_analyze_account_non_dict_creds_is_empty():
    assert (await analyze_account(None)) is EMPTY
    assert (await analyze_account("not-a-dict")) is EMPTY


# --- verify integration: the alive path carries the AccountInfo --------------

_DISCO = {
    "issuer": "https://auth.openai.com",
    "token_endpoint": "https://auth.openai.com/api/accounts/oauth/token",
}


async def _make_fresh_seed(mongo_db, tmp_path) -> str:
    """A fresh ChatGPT-mode seed (recent last_refresh → alive without a rotate)."""
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{oid}"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".codex")
    os.makedirs(d, exist_ok=True)
    auth = {
        "OPENAI_API_KEY": None,
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tokens": {
            "id_token": "OLD_ID", "access_token": "OLD_ACCESS",
            "refresh_token": "ORIGINAL", "account_id": "acct-uuid-1",
        },
    }
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps(auth))
    with open(os.path.join(d, "config.toml"), "w") as fh:
        fh.write('model = "gpt-5.5"\n')
    return await seeds.capture_seed(
        ctx, src, manifest=CODEX_SEED_MANIFEST, suffix=CODEX_SEED_SUFFIX, encrypt=None,
    )


async def test_verify_alive_stamps_and_returns_account_info(mongo_db, tmp_path, monkeypatch):
    seed_id = await _make_fresh_seed(mongo_db, tmp_path)
    monkeypatch.setattr(verify, "_discover_sync", lambda issuer: _DISCO)

    known = AccountInfo(
        name="Ada Lovelace", email="user@example.com", plan="Free",
        account_id="acct-uuid-1",
        windows=(UsageWindow(label="primary", pct=68.0, resets_at=None),),
        raw={"stub": True},
    )

    captured = {}

    async def fake_analyze(creds):
        captured["creds"] = creds
        return known

    monkeypatch.setattr(verify, "analyze_account", fake_analyze)

    res = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert res["alive"] is True
    # the alive return carries the AccountInfo
    assert res["account"] == known
    # analyze_account was handed the seed's tokens dict (not a bare token)
    assert captured["creds"]["account_id"] == "acct-uuid-1"

    # ...and it was stamped as metadata.account on the seed doc
    doc = await seeds.load_seed(mongo_db, prefix="test", suffix=CODEX_SEED_SUFFIX, seed_id=seed_id)
    assert doc["metadata"]["account"]["email"] == "user@example.com"
    assert doc["metadata"]["account"]["plan"] == "Free"
    assert doc["metadata"]["account"]["summary"] == "Plan: Free for Ada Lovelace <user@example.com>"
