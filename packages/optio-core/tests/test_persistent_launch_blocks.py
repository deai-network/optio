"""Tests for persistent launch blocks ("perma-ban").

Spec: docs/2026-04-30-persistent-launch-blocks-design.md
"""
import asyncio
import pytest

from optio_core import _launch_block_store as store
from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


# ---------- Store-level tests ----------

def _coll(mongo_db, prefix="optio_test"):
    return store.collection(mongo_db, prefix)


async def test_load_all_empty(mongo_db):
    """load_all returns [] for a fresh / missing collection."""
    rows = await store.load_all(_coll(mongo_db))
    assert rows == []


async def test_upsert_inserts_record(mongo_db):
    """First upsert with a new filter inserts a record."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason="bad behavior")

    rows = await store.load_all(coll)
    assert len(rows) == 1
    row = rows[0]
    assert row.filter == {"tenant": "acme"}
    assert row.reason == "bad behavior"
    # createdAt is set
    assert row.created_at is not None


async def test_upsert_dedupes_by_exact_filter(mongo_db):
    """Second upsert with the same filter does NOT create a new record."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason="x")
    await store.upsert_block(coll, {"tenant": "acme"}, reason="y")

    rows = await store.load_all(coll)
    assert len(rows) == 1
    # Reason concatenated when both sides non-null.
    assert rows[0].reason == "x AND y"


async def test_upsert_reason_concat_null_handling(mongo_db):
    """Reason concat skips when either side is null."""
    coll = _coll(mongo_db)

    # Existing reason None, new reason set -> existing kept (None).
    await store.upsert_block(coll, {"a": 1}, reason=None)
    await store.upsert_block(coll, {"a": 1}, reason="later")
    rows = await store.load_all(coll)
    assert [r.reason for r in rows if r.filter == {"a": 1}] == [None]

    # Existing reason set, new reason None -> existing kept.
    await store.upsert_block(coll, {"b": 2}, reason="initial")
    await store.upsert_block(coll, {"b": 2}, reason=None)
    rows = await store.load_all(coll)
    assert [r.reason for r in rows if r.filter == {"b": 2}] == ["initial"]


async def test_upsert_different_filter_inserts_separate_record(mongo_db):
    """Different filters yield separate records."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)
    await store.upsert_block(coll, {"tenant": "globex"}, reason=None)

    rows = await store.load_all(coll)
    filters = sorted([tuple(sorted(r.filter.items())) for r in rows])
    assert filters == [(("tenant", "acme"),), (("tenant", "globex"),)]


async def test_delete_by_filter_removes_matching(mongo_db):
    """delete_by_filter deletes all matching rows; returns count."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)
    await store.upsert_block(coll, {"tenant": "globex"}, reason=None)

    deleted = await store.delete_by_filter(coll, {"tenant": "acme"})
    assert deleted == 1

    rows = await store.load_all(coll)
    assert len(rows) == 1
    assert rows[0].filter == {"tenant": "globex"}


async def test_delete_by_filter_no_match_is_noop(mongo_db):
    """delete_by_filter returns 0 when no record matches."""
    coll = _coll(mongo_db)
    await store.upsert_block(coll, {"tenant": "acme"}, reason=None)

    deleted = await store.delete_by_filter(coll, {"tenant": "nonexistent"})
    assert deleted == 0
    rows = await store.load_all(coll)
    assert len(rows) == 1
