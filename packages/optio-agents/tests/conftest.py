"""Shared test fixtures for optio-agents."""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.fixture
def tmp_workdir():
    """A temporary directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-agents-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def mongo_db():
    """Per-test MongoDB database, dropped after each test."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_agents_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()
