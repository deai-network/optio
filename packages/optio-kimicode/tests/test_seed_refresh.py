"""Unit tests for the pre-merge seed refresh (session._merge_seed_with_refresh).

A seeded kimi launch overlays the seed's ``credentials/kimi-code.json`` into the
workdir. kimi's access token is short-lived (~15 min), so a stored seed is
almost always EXPIRED at launch — session/new then ships an empty model picker
(no model → silent turn failures) or is rejected with "Authentication required".
The fix: refresh the seed's rotating token host-free (using the still-valid
refresh token) BEFORE merging, so kimi launches with a live token.

A SPOILED seed (refresh token spent/revoked → verify marks status "dead") can
never launch, so the refresh fails the launch early with an actionable message
instead of merging a dead credential.
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


def _patch(monkeypatch, *, verify, merge, load_seed=None):
    monkeypatch.setattr(kc.verify, "verify_and_refresh_seed", verify)
    monkeypatch.setattr(kc._seeds, "merge_seed", merge)
    if load_seed is not None:
        monkeypatch.setattr(kc._seeds, "load_seed", load_seed)


@pytest.mark.asyncio
async def test_refreshes_seed_before_merging(monkeypatch):
    calls: list = []

    async def _verify(db, *, prefix, seed_id, encrypt=None, decrypt=None):
        calls.append(("verify", seed_id, prefix))
        return {"alive": True, "accounts": []}

    async def _merge(ctx, host, *, seed_id, manifest, suffix, decrypt=None):
        calls.append(("merge", seed_id))

    _patch(monkeypatch, verify=_verify, merge=_merge)
    await kc._merge_seed_with_refresh(_Ctx(), object(), _Cfg(), seed_id="s9")

    # Refresh happens FIRST, with the resolved seed id + prefix, then the merge.
    assert calls == [("verify", "s9", "pfx"), ("merge", "s9")]


@pytest.mark.asyncio
async def test_spoiled_seed_fails_launch_before_merging(monkeypatch):
    """A refresh that leaves the seed status 'dead' (spent/revoked refresh token)
    aborts the launch with an actionable message — no merge, no kimi launch."""
    calls: list = []

    async def _verify(db, *, prefix, seed_id, encrypt=None, decrypt=None):
        calls.append("verify")
        return {"alive": False, "accounts": []}

    async def _load_seed(db, *, prefix, suffix, seed_id):
        return {"_id": seed_id, "status": "dead"}

    async def _merge(ctx, host, *, seed_id, manifest, suffix, decrypt=None):
        calls.append("merge")

    _patch(monkeypatch, verify=_verify, merge=_merge, load_seed=_load_seed)

    with pytest.raises(RuntimeError) as exc:
        await kc._merge_seed_with_refresh(_Ctx(), object(), _Cfg(), seed_id="s9")
    assert "spoiled" in str(exc.value).lower()
    assert "s9" in str(exc.value)
    assert "merge" not in calls  # never merged a dead credential


@pytest.mark.asyncio
async def test_transient_refresh_failure_still_merges(monkeypatch):
    """A NON-dead refresh failure (transient network, status not 'dead') must NOT
    abort — merge the existing credential and let session/new surface any error."""
    calls: list = []

    async def _verify(db, *, prefix, seed_id, encrypt=None, decrypt=None):
        calls.append("verify")
        return {"alive": False, "accounts": []}

    async def _load_seed(db, *, prefix, suffix, seed_id):
        return {"_id": seed_id, "status": "alive"}  # not dead → transient

    async def _merge(ctx, host, *, seed_id, manifest, suffix, decrypt=None):
        calls.append("merge")

    _patch(monkeypatch, verify=_verify, merge=_merge, load_seed=_load_seed)
    await kc._merge_seed_with_refresh(_Ctx(), object(), _Cfg(), seed_id="s9")

    assert calls == ["verify", "merge"]


@pytest.mark.asyncio
async def test_merge_proceeds_when_refresh_raises(monkeypatch):
    """A raised refresh (transport bug, etc.) is inconclusive, never a spoiled
    verdict — merge the existing credential and never abort the launch."""
    calls: list = []

    async def _verify(db, *, prefix, seed_id, encrypt=None, decrypt=None):
        raise RuntimeError("boom")

    async def _merge(ctx, host, *, seed_id, manifest, suffix, decrypt=None):
        calls.append("merge")

    _patch(monkeypatch, verify=_verify, merge=_merge)
    await kc._merge_seed_with_refresh(_Ctx(), object(), _Cfg(), seed_id="s9")

    assert calls == ["merge"]
