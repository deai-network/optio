"""Unit + round-trip tests for the kimi seed manifest (Stage 3, Task 1).

Ported from optio-grok's seed-manifest tests. kimi, like grok/opencode, needs
no cwd-rekey: its credential file is path-independent JSON, so
``consume_transform`` is None.

KIMI_CODE_HOME is ``<workdir>/home``; the engine roots capture/extract at
``host.workdir + "/" + home_subdir``, so the manifest uses ``home_subdir="home"``
with the ``credentials/`` creds dir as its include member (kimi's rotating token
lives at ``<workdir>/home/credentials/kimi-code.json``).

Delta from grok: grok's full seed = ``auth.json`` + ``config.toml``; kimi has no
config member in the identity seed, so KIMI_SEED_MANIFEST == KIMI_CRED_MANIFEST
(both carry only the creds dir).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from bson import ObjectId
from optio_host.host import LocalHost

from optio_agents import seeds

from optio_kimicode.seed_manifest import (
    KIMI_CRED_MANIFEST,
    KIMI_SEED_MANIFEST,
    KIMI_SEED_SUFFIX,
)


def test_seed_manifest_home_and_contents():
    assert isinstance(KIMI_SEED_MANIFEST, seeds.SeedManifest)
    # home_subdir is the isolated HOME (engine docstring: "HOME relative to the
    # workdir, e.g. 'home'"); KIMI_CODE_HOME = <workdir>/home lives beneath it,
    # with the creds dir at <workdir>/home/credentials.
    assert KIMI_SEED_MANIFEST.home_subdir == "home"
    assert "credentials" in KIMI_SEED_MANIFEST.include


def test_no_consume_transform():
    # kimi creds are path-independent JSON → no rekey (like grok/opencode).
    assert KIMI_SEED_MANIFEST.consume_transform is None
    assert KIMI_CRED_MANIFEST.consume_transform is None


def test_cred_manifest_is_creds_only():
    # kimi has no config member → the save-back CRED manifest and the full SEED
    # manifest coincide (both carry only the creds dir).
    assert KIMI_CRED_MANIFEST.home_subdir == "home"
    assert KIMI_CRED_MANIFEST.include == ["credentials"]
    assert KIMI_SEED_MANIFEST.include == KIMI_CRED_MANIFEST.include


def test_seed_suffix():
    assert KIMI_SEED_SUFFIX == "_kimicode_seeds"


async def _ctx(mongo_db) -> "object":
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "seedsrc"})
    return ProcessContext(
        process_oid=oid, process_id="seedsrc", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


@pytest.mark.asyncio
async def test_capture_then_plant_lands_kimi_creds(mongo_db, tmp_path):
    """Capture a seed from a populated credentials dir, then plant it into a
    fresh KIMI_CODE_HOME and assert kimi-code.json lands with its content."""
    # --- populate a source KIMI_CODE_HOME (<workdir>/home/credentials) ---
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    creds_dir = os.path.join(src.workdir, "home", "credentials")
    os.makedirs(creds_dir, exist_ok=True)
    token = {
        "access_token": "ACCESS-XYZ",
        "refresh_token": "REFRESH-ORIGINAL",
        "expires_at": 9999999999,
        "scope": "openid",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    with open(os.path.join(creds_dir, "kimi-code.json"), "w") as fh:
        fh.write(json.dumps(token))

    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX,
        encrypt=None,
    )

    # --- plant into a FRESH, empty KIMI_CODE_HOME (db-first, no ctx) ---
    dst = LocalHost(taskdir=str(tmp_path / "dst"))
    await dst.setup_workdir()
    await seeds.plant_seed(
        mongo_db, dst, prefix="test", seed_id=seed_id,
        manifest=KIMI_SEED_MANIFEST, suffix=KIMI_SEED_SUFFIX, decrypt=None,
    )

    landed = os.path.join(dst.workdir, "home", "credentials", "kimi-code.json")
    assert os.path.exists(landed)
    with open(landed) as fh:
        assert json.load(fh) == token
