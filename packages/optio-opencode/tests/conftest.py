"""Shared test fixtures for optio-opencode."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from optio_host.testing import sshd_container

_COMPOSE = Path(__file__).parent / "docker-compose.sshd.yml"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def sshd():
    """One isolation-safe sshd container shared by every remote module.

    Session-scoped so the three ``*_remote`` modules reuse a single container
    per xdist worker; :func:`sshd_container` keys the compose project on the
    worker id and uses an ephemeral host port, so concurrent workers/packages
    never collide.
    """
    # The shim is bind-mounted into the container as the `opencode` binary;
    # ensure it is executable before the container starts.
    (_COMPOSE.parent / "opencode-shim.sh").chmod(0o755)
    async with sshd_container(_COMPOSE, "optio-opencode") as info:
        yield info


@pytest.fixture
def tmp_workdir():
    """A temporary directory that is removed after the test."""
    path = tempfile.mkdtemp(prefix="optio-opencode-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def mongo_db():
    """Test MongoDB database, dropped after each test. Matches optio-core's fixture."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_opencode_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()
