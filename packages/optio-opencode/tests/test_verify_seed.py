"""verify_and_refresh_seed unit tests (fake opencode binary, real Mongo)."""

import asyncio
import json
import os
import shlex
import stat

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from optio_host.host import LocalHost

from optio_agents import seeds
from optio_agents.account import AccountInfo
from optio_opencode import host_actions, verify as verify_module
from optio_opencode.seed_manifest import OPENCODE_SEED_MANIFEST, OPENCODE_SEED_SUFFIX
from optio_opencode.verify import verify_and_refresh_seed


@pytest.fixture(autouse=True)
def _stub_accounts(monkeypatch):
    """Keep verify hermetic: stub the meta-analyzer to [] by default so the
    liveness tests never reach a vendor network. The account-stamping test
    overrides this."""

    async def _none(auth):
        return []

    monkeypatch.setattr(verify_module, "analyze_accounts", _none)


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_oc_verify_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def _make_seed(mongo_db, tmp_path, *, model="prov/model-1"):
    """Capture a seed with auth.json (+ optional model config) via a scratch host."""
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    ctx = ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )
    src = LocalHost(taskdir=str(tmp_path / "seedsrc"))
    await src.setup_workdir()
    d = os.path.join(src.workdir, "home", ".local", "share", "opencode")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "auth.json"), "w") as fh:
        fh.write(json.dumps({"xai": {"type": "oauth", "refresh": "ORIGINAL"}}))
    c = os.path.join(src.workdir, "home", ".config", "opencode")
    os.makedirs(c, exist_ok=True)
    with open(os.path.join(c, "opencode.json"), "w") as fh:
        fh.write(json.dumps({"model": model} if model else {}))
    return await seeds.capture_seed(
        ctx, src, manifest=OPENCODE_SEED_MANIFEST,
        suffix=OPENCODE_SEED_SUFFIX, encrypt=None,
    )


def _fake_opencode(tmp_path, script_body: str) -> str:
    """A fake opencode binary; `script_body` handles the `run` subcommand."""
    p = tmp_path / "opencode"
    p.write_text(f"#!/usr/bin/env bash\nif [ \"$1\" = run ]; then\n{script_body}\nfi\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture
def _patch_install(monkeypatch):
    """Route verify's install step to a fake binary set per-test."""
    def patch(path):
        async def _ensure(host, **kwargs):
            return path
        monkeypatch.setattr(host_actions, "ensure_opencode_installed", _ensure)
    return patch


@pytest.fixture
def task_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIO_OPENCODE_TASK_ROOT", str(tmp_path / "tasks"))


async def _seed_auth(mongo_db, seed_id) -> dict:
    """Extract auth.json from the seed blob for assertions."""
    import io
    import tarfile
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    buf = io.BytesIO()
    await AsyncIOMotorGridFSBucket(mongo_db).download_to_stream(doc["blobId"], buf)
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        f = tar.extractfile(".local/share/opencode/auth.json")
        return json.loads(f.read().decode("utf-8"))


async def test_alive_and_writes_back_rotated_auth(mongo_db, tmp_path, _patch_install, task_root):
    seed_id = await _make_seed(mongo_db, tmp_path)
    # fake: rotates auth.json (as opencode's loader would), answers the probe
    _patch_install(_fake_opencode(tmp_path, (
        '  mkdir -p "$XDG_DATA_HOME/opencode"\n'
        '  printf %s \'{"xai": {"type": "oauth", "refresh": "ROTATED"}}\' '
        '> "$XDG_DATA_HOME/opencode/auth.json"\n'
        "  printf 'The capital of France is Paris.\\n'\n"
        "  exit 0"
    )))
    result = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=seed_id,
    )
    assert result["alive"] is True
    assert result["accounts"] == []
    assert result["model"] == "prov/model-1"
    auth = await _seed_auth(mongo_db, seed_id)
    assert auth["xai"]["refresh"] == "ROTATED"
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["metadata"]["verify"]["alive"] is True
    assert doc["status"] == "alive"


async def test_dead_on_auth_error(mongo_db, tmp_path, _patch_install, task_root):
    seed_id = await _make_seed(mongo_db, tmp_path)
    _patch_install(_fake_opencode(tmp_path, "  echo 'Error: Unauthorized'\n  exit 1"))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is False
    assert result["accounts"] == []
    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    assert doc["status"] == "dead"


async def test_prompt_echo_does_not_false_positive(mongo_db, tmp_path, _patch_install, task_root):
    # An error path that echoes the full prompt must NOT count as alive —
    # the challenge-answer property: the answer token is absent from the prompt.
    seed_id = await _make_seed(mongo_db, tmp_path)
    _patch_install(_fake_opencode(
        tmp_path, '  echo "cannot process request: ${@: -1}"\n  exit 1',
    ))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is False
    assert result["accounts"] == []


async def test_exit_code_carries_no_verdict(mongo_db, tmp_path, _patch_install, task_root):
    # Answer present + nonzero exit -> still alive (stdout-only verdict).
    seed_id = await _make_seed(mongo_db, tmp_path)
    _patch_install(_fake_opencode(tmp_path, "  echo 'Paris'\n  exit 3"))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is True
    assert result["accounts"] == []


async def test_modelless_seed_is_dead_without_probe(mongo_db, tmp_path, _patch_install, task_root):
    seed_id = await _make_seed(mongo_db, tmp_path, model=None)
    marker = tmp_path / "ran"
    _patch_install(_fake_opencode(tmp_path, f"  touch {shlex.quote(str(marker))}\n  echo Paris"))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)
    assert result["alive"] is False
    assert result["accounts"] == []
    assert result["model"] is None
    assert not marker.exists()  # probe never ran


async def test_unknown_seed(mongo_db, task_root):
    result = await verify_and_refresh_seed(
        mongo_db, prefix="test", seed_id=str(ObjectId()),
    )
    assert result == {"alive": False, "accounts": [], "model": None}


async def test_alive_stamps_metadata_accounts(
    mongo_db, tmp_path, _patch_install, task_root, monkeypatch,
):
    # On the ALIVE path verify feeds the refreshed auth.json to the meta-analyzer
    # and stamps metadata.accounts; the accounts also ride the return dict.
    account = AccountInfo(
        plan="xAI Team", account_id="xai-1", email="pilot@x.com",
        raw={"provider": "xai"},
    )
    captured = {}

    async def _analyze(auth):
        captured["auth"] = auth
        return [account]

    monkeypatch.setattr(verify_module, "analyze_accounts", _analyze)

    seed_id = await _make_seed(mongo_db, tmp_path)
    _patch_install(_fake_opencode(tmp_path, (
        '  mkdir -p "$XDG_DATA_HOME/opencode"\n'
        '  printf %s \'{"xai": {"type": "oauth", "access": "A", "refresh": "R"}}\' '
        '> "$XDG_DATA_HOME/opencode/auth.json"\n'
        "  printf 'The capital of France is Paris.\\n'\n"
        "  exit 0"
    )))
    result = await verify_and_refresh_seed(mongo_db, prefix="test", seed_id=seed_id)

    assert result["alive"] is True
    assert result["accounts"] == [account]
    # the analyzer saw the refreshed auth.json (the rotated access token).
    assert captured["auth"]["xai"]["access"] == "A"

    doc = await seeds.load_seed(
        mongo_db, prefix="test", suffix=OPENCODE_SEED_SUFFIX, seed_id=seed_id,
    )
    stamped = doc["metadata"]["accounts"]
    assert len(stamped) == 1
    assert stamped[0]["account_id"] == "xai-1"
    assert stamped[0]["summary"] == "Plan: xAI Team for <pilot@x.com>"
