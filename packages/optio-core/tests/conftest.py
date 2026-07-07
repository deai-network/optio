"""Shared test fixtures."""

import asyncio
import os
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.fixture(autouse=True)
def _fast_lifecycle_timeouts(monkeypatch):
    """Shrink the cancel/deadline/shutdown timing knobs for the whole suite.

    The lifecycle timeouts (internal cancel-wait ceiling, shutdown-drain margin,
    terminal poll interval, force-cancel shield) default to production-sized
    real-time waits. Tests exercise the *logic*, not the durations, so injecting
    tiny values keeps the timing-sensitive suites off real wall-clock seconds —
    which both cuts run time dramatically and makes them safe under pytest-xdist.

    Applied via ``Optio.init`` with ``setdefault`` so any test that deliberately
    pins a specific value still wins.
    """
    import optio_core.lifecycle as _lc

    _orig_init = _lc.Optio.init

    async def _fast_init(self, *args, **kwargs):
        # Small but not tiny: the internal ceiling must still exceed a real
        # force-cancel (grace + shield + overhead) so cooperative/stubborn
        # unwinds finish before the backstop fires. These cut the ~25s/5s/2s
        # production waits to a couple of seconds without racing the logic.
        kwargs.setdefault("cancel_wait_ceiling_margin_seconds", 2.0)
        kwargs.setdefault("shutdown_drain_margin_seconds", 1.0)
        kwargs.setdefault("terminal_poll_interval_seconds", 0.05)
        kwargs.setdefault("force_cancel_shield_seconds", 0.5)
        return await _orig_init(self, *args, **kwargs)

    monkeypatch.setattr(_lc.Optio, "init", _fast_init)


@pytest_asyncio.fixture
async def mongo_db():
    """Provide a test MongoDB database, cleaned up after each test."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db_name = f"optio_test_{os.getpid()}"
    db = client[db_name]
    yield db
    await client.drop_database(db_name)
    client.close()


@pytest.fixture
def redis_url():
    """Provide Redis URL for tests."""
    return os.environ.get("REDIS_URL", "redis://localhost:6379")
