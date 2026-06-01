"""purge_seed fully expunges a claudecode seed (doc + GridFS blob)."""

import os

import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from optio_agents import seeds
from optio_agents.seeds import insert_seed

from optio_core import MongoStore
from optio_claudecode import purge_seed
from optio_claudecode.seed_manifest import CLAUDE_SEED_SUFFIX


@pytest_asyncio.fixture
async def mongo_db():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_cc_purge_seed_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


async def test_purge_removes_doc_and_blob(mongo_db):
    bucket = AsyncIOMotorGridFSBucket(mongo_db)
    blob_id = await bucket.upload_from_stream("seed", b"seed-payload")
    seed_id = await insert_seed(
        mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX,
        blob_id=blob_id, manifest_version=1,
    )

    await purge_seed(MongoStore(db=mongo_db, prefix="test"), seed_id)

    # the seed doc is gone
    assert await seeds.load_seed(
        mongo_db, prefix="test", suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id,
    ) is None

    # the GridFS blob is gone
    import gridfs

    with pytest.raises(gridfs.errors.NoFile):
        await bucket.open_download_stream(blob_id)


async def test_purge_unknown_id_raises(mongo_db):
    with pytest.raises(KeyError):
        await purge_seed(MongoStore(db=mongo_db, prefix="test"), str(ObjectId()))
