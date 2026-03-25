"""Shared test fixtures."""

import asyncio
import os
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def mongo_db():
    """Provide a test MongoDB database, cleaned up after each test."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"feldwebel_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def redis_url():
    """Provide Redis URL for tests."""
    return os.environ.get("REDIS_URL", "redis://localhost:6379")
