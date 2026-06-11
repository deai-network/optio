"""plant_seed: host-free (db-first) variant of merge_seed."""

import asyncio
import os

import pytest
from bson import ObjectId
from optio_host.host import LocalHost

from optio_agents import seeds


async def _ctx(mongo_db):
    from optio_core.context import ProcessContext

    oid = ObjectId()
    await mongo_db["test_processes"].insert_one({"_id": oid, "processId": "p"})
    return ProcessContext(
        process_oid=oid, process_id="p", root_oid=oid, depth=0, params={},
        services={}, db=mongo_db, prefix="test",
        cancellation_flag=asyncio.Event(), child_counter={"next": 0},
    )


_MANIFEST = seeds.SeedManifest(home_subdir="home", include=[".cfg/file.txt"])


async def test_plant_seed_extracts_into_host(mongo_db, tmp_path):
    src = LocalHost(taskdir=str(tmp_path / "src"))
    await src.setup_workdir()
    target = os.path.join(src.workdir, "home", ".cfg")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "file.txt"), "w") as fh:
        fh.write("CONTENT-1")

    ctx = await _ctx(mongo_db)
    seed_id = await seeds.capture_seed(
        ctx, src, manifest=_MANIFEST, suffix="_t_seeds", encrypt=None,
    )

    dst = LocalHost(taskdir=str(tmp_path / "dst"))
    await dst.setup_workdir()
    # db-first: no ProcessContext
    await seeds.plant_seed(
        mongo_db, dst, prefix="test", seed_id=seed_id,
        manifest=_MANIFEST, suffix="_t_seeds", decrypt=None,
    )
    with open(os.path.join(dst.workdir, "home", ".cfg", "file.txt")) as fh:
        assert fh.read() == "CONTENT-1"


async def test_plant_seed_unknown_id_raises(mongo_db, tmp_path):
    dst = LocalHost(taskdir=str(tmp_path / "d2"))
    await dst.setup_workdir()
    with pytest.raises(KeyError):
        await seeds.plant_seed(
            mongo_db, dst, prefix="test", seed_id=str(ObjectId()),
            manifest=_MANIFEST, suffix="_t_seeds", decrypt=None,
        )
