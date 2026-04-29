"""Tests for deadline-driven cooperative cancel.

Spec: docs/2026-04-29-deadline-driven-cancel-design.md
"""
import asyncio

import pytest

from optio_core.lifecycle import Optio


pytestmark = pytest.mark.asyncio


async def test_init_accepts_cancel_grace_seconds(mongo_db):
    """Optio.init forwards cancel_grace_seconds onto OptioConfig."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsinit", cancel_grace_seconds=2.5)
    assert optio._config.cancel_grace_seconds == 2.5


async def test_init_default_cancel_grace_seconds(mongo_db):
    """Default cancel_grace_seconds is 5.0."""
    optio = Optio()
    await optio.init(mongo_db=mongo_db, prefix="cgsdefault")
    assert optio._config.cancel_grace_seconds == 5.0
