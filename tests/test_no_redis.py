"""Tests for Redis-free mode."""

import pytest
from feldwebel.lifecycle import Feldwebel


@pytest.mark.asyncio
async def test_init_without_redis(mongo_db):
    """Init succeeds without redis_url."""
    fw = Feldwebel()
    await fw.init(mongo_db=mongo_db, prefix="test_no_redis")

    assert fw._config is not None
    assert fw._config.redis_url is None
    assert fw._redis is None
    assert fw._consumer is None
    assert fw._executor is not None
    assert fw._scheduler is not None
