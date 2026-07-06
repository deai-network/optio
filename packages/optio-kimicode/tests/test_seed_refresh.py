"""Unit tests for the pre-merge seed refresh (session._merge_seed_with_refresh).

A seeded kimi launch overlays the seed's ``credentials/kimi-code.json`` into the
workdir. kimi's access token is short-lived (~15 min), so a stored seed is
almost always EXPIRED at launch — session/new then ships an empty model picker
(no model → silent turn failures) or is rejected with "Authentication required".
The fix: refresh the seed's rotating token host-free (using the still-valid
refresh token) BEFORE merging, so kimi launches with a live token.
"""

from __future__ import annotations

import pytest

from optio_kimicode import session as kc


class _Ctx:
    _db = object()
    _prefix = "pfx"


class _Cfg:
    session_blob_encrypt = None
    session_blob_decrypt = None


@pytest.mark.asyncio
async def test_refreshes_seed_before_merging(monkeypatch):
    calls: list = []

    async def _verify(db, *, prefix, seed_id, encrypt=None, decrypt=None):
        calls.append(("verify", seed_id, prefix))
        return True

    async def _merge(ctx, host, *, seed_id, manifest, suffix, decrypt=None):
        calls.append(("merge", seed_id))

    monkeypatch.setattr(kc.verify, "verify_and_refresh_seed", _verify)
    monkeypatch.setattr(kc._seeds, "merge_seed", _merge)

    await kc._merge_seed_with_refresh(_Ctx(), object(), _Cfg(), seed_id="s9")

    # Refresh happens FIRST, with the resolved seed id + prefix, then the merge.
    assert calls == [("verify", "s9", "pfx"), ("merge", "s9")]


@pytest.mark.asyncio
async def test_still_merges_when_refresh_reports_not_alive(monkeypatch):
    """A dead/inconclusive refresh must NOT block the launch — merge the existing
    credential anyway and let session/new surface the auth error."""
    calls: list = []

    async def _verify(db, *, prefix, seed_id, encrypt=None, decrypt=None):
        calls.append("verify")
        return False

    async def _merge(ctx, host, *, seed_id, manifest, suffix, decrypt=None):
        calls.append("merge")

    monkeypatch.setattr(kc.verify, "verify_and_refresh_seed", _verify)
    monkeypatch.setattr(kc._seeds, "merge_seed", _merge)

    await kc._merge_seed_with_refresh(_Ctx(), object(), _Cfg(), seed_id="s9")

    assert calls == ["verify", "merge"]


@pytest.mark.asyncio
async def test_merge_proceeds_when_refresh_raises(monkeypatch):
    """A raised refresh (transport bug, etc.) must never abort the launch."""
    calls: list = []

    async def _verify(db, *, prefix, seed_id, encrypt=None, decrypt=None):
        raise RuntimeError("boom")

    async def _merge(ctx, host, *, seed_id, manifest, suffix, decrypt=None):
        calls.append("merge")

    monkeypatch.setattr(kc.verify, "verify_and_refresh_seed", _verify)
    monkeypatch.setattr(kc._seeds, "merge_seed", _merge)

    await kc._merge_seed_with_refresh(_Ctx(), object(), _Cfg(), seed_id="s9")

    assert calls == ["merge"]
