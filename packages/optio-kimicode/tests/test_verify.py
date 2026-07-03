"""verify_and_refresh_seed unit tests — host-free direct-refresh path (mocked HTTP).

The refresh talks straight to the kimi OAuth token endpoint
(``POST auth.kimi.com/api/oauth/token``, ``grant_type=refresh_token``) — no kimi
process, no model inference, non-billable. There is NO OIDC discovery: the
endpoint + public ``client_id`` are hardcoded (kimi ships no
``.well-known/openid-configuration``). These tests stub the sync HTTP helper
(``_refresh_sync``) so the whole verify/refresh/save-back/status logic runs
against a real Mongo seed with zero network, plus a handful of direct
``_refresh_sync`` tests that exercise its HTTP status classification.

Deltas from grok's test_verify:
* kimi's ``kimi-code.json`` is a FLAT snake_case token dict (no
  ``{issuer::client: {...}}`` nesting), field ``access_token`` (not ``key``).
* ``expires_at`` is unix SECONDS (int), not an RFC3339 string.
* Refresh is threshold-driven: remaining life < max(300s, 0.5*expires_in). No
  userinfo liveness endpoint exists, so a token outside the threshold is left
  un-rotated (confirmed alive) without any network call.
* Dead signal is a 401/403 or ``invalid_grant``; every other HTTP/transport
  failure is inconclusive and never retires a possibly-healthy seed.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import time
import urllib.error
import urllib.request

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from optio_core.context import ProcessContext
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_kimicode import verify
from optio_kimicode.seed_manifest import KIMI_SEED_MANIFEST, KIMI_SEED_SUFFIX
from optio_kimicode.verify import verify_and_refresh_seed

_CRED_MEMBER = "credentials/kimi-code.json"


# --- seed fixtures ----------------------------------------------------------


async def _make_seed(
    mongo_db, tmp_path, *, expires_at, expires_in=3600, refresh_token="ORIGINAL",
    access_token="OLD_ACCESS",
) -> str:
    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / f"seedsrc-{oid}"))
    await src.setup_workdir()
    creds_dir = os.path.join(src.workdir, "home", "credentials")
    os.makedirs(creds_dir, exist_ok=True)
    token = {
        "access_token": access_token,
        "expires_at": expires_at,
        "scope": "openid",
        "token_type": "Bearer",
        "expires_in": expires_in,
    }
    if refresh_token is not None:
        token["refresh_token"] = refresh_token
    with open(os.path.join(creds_dir, "kimi-code.json"), "w") as fh:
        fh.write(json.dumps(token))
    return await seeds.capture_seed(
        ctx, src, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX, encrypt=None,
    )


async def _seed_creds(mongo_db, seed_id: str) -> dict:
    import tarfile

    doc = await seeds.load_seed(mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX, seed_id=seed_id)
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        return json.loads(tar.extractfile(_CRED_MEMBER).read().decode("utf-8"))


async def _doc(mongo_db, seed_id: str) -> dict:
    return await seeds.load_seed(mongo_db, prefix="test", suffix=KIMI_SEED_SUFFIX, seed_id=seed_id)


# --- verify_and_refresh_seed branches (stub _refresh_sync) ------------------


async def test_stale_within_threshold_refreshes_and_writes_back(mongo_db, tmp_path, monkeypatch):
    # expires in 60s, expires_in=3600 → threshold max(300, 1800)=1800; 60 < 1800.
    soon = int(time.time()) + 60
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=soon)

    refreshed = {}

    def fake_refresh(token_endpoint, refresh_token, client_id):
        refreshed["called"] = (token_endpoint, refresh_token, client_id)
        return {
            "access_token": "NEW_ACCESS", "refresh_token": "ROTATED",
            "expires_in": 3600, "scope": "openid", "token_type": "Bearer",
        }

    monkeypatch.setattr(verify, "_refresh_sync", fake_refresh)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    assert refreshed["called"] == (verify._token_endpoint(), "ORIGINAL", verify._CLIENT_ID)

    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["access_token"] == "NEW_ACCESS"        # rotated access token
    assert creds["refresh_token"] == "ROTATED"          # rotated single-use RT saved back
    assert isinstance(creds["expires_at"], int)
    assert creds["expires_at"] > int(time.time())       # expiry pushed forward (unix s)
    assert creds["scope"] == "openid"                   # preserved / refreshed

    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "alive"
    assert doc["metadata"]["verify"]["alive"] is True


async def test_expired_refreshes(mongo_db, tmp_path, monkeypatch):
    past = int(time.time()) - 3600
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)
    monkeypatch.setattr(
        verify, "_refresh_sync",
        lambda *a: {"access_token": "NEW", "refresh_token": "R2", "expires_in": 3600},
    )
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    assert (await _seed_creds(mongo_db, seed_id))["access_token"] == "NEW"


async def test_fresh_outside_threshold_does_not_refresh(mongo_db, tmp_path, monkeypatch):
    # expires in 2h, expires_in=3600 → threshold 1800; 7200 remaining ≫ 1800.
    far = int(time.time()) + 7200
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=far)

    def boom(*a):
        raise AssertionError("must not refresh a token outside the refresh threshold")

    monkeypatch.setattr(verify, "_refresh_sync", boom)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is True
    creds = await _seed_creds(mongo_db, seed_id)
    assert creds["refresh_token"] == "ORIGINAL"         # untouched
    assert creds["access_token"] == "OLD_ACCESS"
    assert (await _doc(mongo_db, seed_id))["status"] == "alive"


async def test_refresh_invalid_grant_marks_dead(mongo_db, tmp_path, monkeypatch):
    past = int(time.time()) - 3600
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: verify._DEAD)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    doc = await _doc(mongo_db, seed_id)
    assert doc["status"] == "dead"
    assert doc["metadata"]["verify"]["alive"] is False


async def test_transport_failure_is_inconclusive_not_dead(mongo_db, tmp_path, monkeypatch):
    past = int(time.time()) - 3600
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past)
    monkeypatch.setattr(verify, "_refresh_sync", lambda *a: None)  # network/5xx

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    # A transient failure must NOT retire a possibly-healthy seed.
    assert (await _doc(mongo_db, seed_id)).get("status") != "dead"


async def test_no_refresh_token_is_dead(mongo_db, tmp_path):
    past = int(time.time()) - 3600
    seed_id = await _make_seed(mongo_db, tmp_path, expires_at=past, refresh_token=None)

    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert alive is False
    assert (await _doc(mongo_db, seed_id))["status"] == "dead"


async def test_unknown_seed(mongo_db):
    alive = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=str(ObjectId()))
    assert alive is False


# --- _refresh_sync HTTP status classification (mock urlopen) ----------------


def _http_error(code: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://auth.kimi.com/api/oauth/token", code=code, msg="err",
        hdrs=None, fp=io.BytesIO(body),
    )


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def test_refresh_sync_success_returns_dict(monkeypatch):
    payload = {"access_token": "A", "refresh_token": "R", "expires_in": 3600}

    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = verify._refresh_sync("https://auth.kimi.com/api/oauth/token", "RT", "CID")
    assert out == payload


def test_refresh_sync_401_is_dead(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(401)),
    )
    assert verify._refresh_sync("https://x/token", "RT", "CID") is verify._DEAD


def test_refresh_sync_400_invalid_grant_is_dead(monkeypatch):
    err = _http_error(400, b'{"error":"invalid_grant"}')
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(err),
    )
    assert verify._refresh_sync("https://x/token", "RT", "CID") is verify._DEAD


def test_refresh_sync_500_is_inconclusive(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(500)),
    )
    assert verify._refresh_sync("https://x/token", "RT", "CID") is None


def test_refresh_sync_transport_error_is_inconclusive(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("no route")),
    )
    assert verify._refresh_sync("https://x/token", "RT", "CID") is None


# --- endpoint resolution ----------------------------------------------------


def test_token_endpoint_default():
    assert verify._token_endpoint() == "https://auth.kimi.com/api/oauth/token"


def test_token_endpoint_env_override(monkeypatch):
    monkeypatch.setenv("KIMI_CODE_OAUTH_HOST", "https://auth.example.test/")
    assert verify._token_endpoint() == "https://auth.example.test/api/oauth/token"
