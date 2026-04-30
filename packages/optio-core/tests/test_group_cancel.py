"""Tests for group_cancel / group_cancel_and_wait.

Spec: docs/2026-04-30-group-cancel-design.md
"""
import asyncio
import pytest

from optio_core.lifecycle import Optio
from optio_core.models import TaskInstance, LaunchBlocked

pytestmark = pytest.mark.asyncio


# ---------- Filter validation (no Mongo needed) ----------

@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel(bad_filter, block_new_launches=block_new_launches)


@pytest.mark.parametrize("bad_filter", [None, {}])
@pytest.mark.parametrize("block_new_launches", [False, True])
async def test_group_cancel_and_wait_rejects_empty_filter(bad_filter, block_new_launches):
    """group_cancel_and_wait raises ValueError on None / empty filter."""
    optio = Optio()
    with pytest.raises(ValueError, match="non-empty metadata_filter"):
        await optio.group_cancel_and_wait(bad_filter, block_new_launches=block_new_launches)
