from datetime import datetime, timedelta, timezone

from optio_cursor import model_probe


class FakeConv:
    """Minimal ACP-conversation stand-in: gated models answer "Upgrade your
    plan…", others answer with Budapest."""

    def __init__(self, gated):
        self.current_model_id = "m-default"
        self._model = "m-default"
        self._gated = set(gated)
        self._handlers = []
        self.set_calls = []

    async def set_active_model(self, m):
        self._model = m
        self.set_calls.append(m)

    def on_message(self, h):
        self._handlers.append(h)
        return lambda: self._handlers.remove(h)

    async def send(self, text):
        reply = (
            "Upgrade your plan to continue"
            if self._model in self._gated
            else "The capital city of Hungary is Budapest."
        )
        for h in list(self._handlers):
            h(reply)


async def test_probe_marks_gated_models_unusable():
    conv = FakeConv(gated={"m-gated"})
    usable = await model_probe.probe_models(
        conv, ["m-default", "m-gated"], per_model_timeout=2.0,
    )
    assert usable == {"m-default": True, "m-gated": False}
    # original model restored after probing
    assert conv._model == "m-default"
    assert conv.set_calls[-1] == "m-default"


def test_probe_cache_key_survives_resume():
    """A fresh merge sets resolved_seed_id; a RESUMED session skips the merge so
    resolved_seed_id is None — fall back to the config's string seed_id so the
    per-seed cache still hits/saves. Pooled (callable) seeds have no stable key
    on resume → None."""
    # fresh: resolved id wins
    assert model_probe.probe_cache_key("resolved", "cfg") == "resolved"
    # resumed (resolved None): string config seed_id is the stable key
    assert model_probe.probe_cache_key(None, "cfg-seed") == "cfg-seed"
    # resumed pooled seed (callable) → no stable key
    assert model_probe.probe_cache_key(None, lambda pid: "x") is None
    # no seed at all
    assert model_probe.probe_cache_key(None, None) is None


def test_apply_probe_disables_only_unusable_with_reason():
    models = [
        {"id": "a", "label": "A", "disabled": False},
        {"id": "b", "label": "B", "disabled": False},
        {"id": "c", "label": "C", "disabled": False},  # not probed
    ]
    out = model_probe.apply_probe(models, {"a": True, "b": False})
    assert out[0]["disabled"] is False
    assert "disabledReason" not in out[0]
    # unusable → disabled + a reason the picker can surface (excavator
    # decision/reason pattern)
    assert out[1]["disabled"] is True
    assert out[1]["disabledReason"] == model_probe.DISABLED_REASON
    assert out[2]["disabled"] is False  # untouched (not probed)
    assert "disabledReason" not in out[2]


async def test_probe_cache_roundtrip_and_ttl(mongo_db):
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    usable = {"m-default": True, "m-gated": False}
    await model_probe.save_probe_cache(mongo_db, "t", "seed1", usable, now=now)
    # fresh -> returns the map
    got = await model_probe.load_probe_cache(mongo_db, "t", "seed1", now=now)
    assert got == usable
    # just within TTL
    got = await model_probe.load_probe_cache(
        mongo_db, "t", "seed1", now=now + timedelta(hours=23),
    )
    assert got == usable
    # past TTL -> None
    got = await model_probe.load_probe_cache(
        mongo_db, "t", "seed1", now=now + timedelta(hours=25),
    )
    assert got is None
    # unknown seed -> None
    assert await model_probe.load_probe_cache(mongo_db, "t", "nope", now=now) is None
