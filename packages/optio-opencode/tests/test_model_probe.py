"""opencode's model-availability probe: the error-signal usable-check, the
/config/providers id enumeration, and the widgetData disabled-map."""
from datetime import datetime, timedelta, timezone

from optio_opencode import model_probe


class FakeConv:
    """opencode conversation stand-in. A model in ``errored`` ends its turn with
    an error event (no answer); ``blank`` answers with empty text; others answer
    normally."""

    def __init__(self, *, errored=(), blank=(), how="session"):
        self.current_model_id = None
        self._model = None
        self._errored = set(errored)
        self._blank = set(blank)
        self._how = how  # "session" | "message" — which error event shape
        self._msg = []
        self._evt = []

    async def set_active_model(self, m):
        self._model = m
        self.current_model_id = m

    def on_message(self, h):
        self._msg.append(h)
        return lambda: self._msg.remove(h)

    def on_event(self, h):
        self._evt.append(h)
        return lambda: self._evt.remove(h)

    async def send(self, text):
        if self._model in self._errored:
            ev = (
                {"type": "session.error",
                 "properties": {"error": {"message": "not supported"}}}
                if self._how == "session"
                else {"type": "message.updated",
                      "properties": {"info": {"id": "m1", "role": "assistant",
                                              "error": {"message": "not supported"},
                                              "time": {"completed": 2}}}}
            )
            for h in list(self._evt):
                h(ev)
            return
        reply = "" if self._model in self._blank else "Budapest is the capital."
        for h in list(self._msg):
            h(reply)


async def test_error_event_marks_model_unusable_session_error():
    conv = FakeConv(errored={"prov/bad"}, how="session")
    usable = await model_probe.probe_models(
        conv, ["prov/good", "prov/bad"], per_model_timeout=2.0,
    )
    assert usable == {"prov/good": True, "prov/bad": False}


async def test_error_event_marks_model_unusable_message_updated_error():
    conv = FakeConv(errored={"prov/bad"}, how="message")
    usable = await model_probe.probe_models(
        conv, ["prov/good", "prov/bad"], per_model_timeout=2.0,
    )
    assert usable == {"prov/good": True, "prov/bad": False}


async def test_blank_answer_is_unusable():
    # A completed turn with no answer text is not proof the model works.
    conv = FakeConv(blank={"prov/mute"})
    usable = await model_probe.probe_models(
        conv, ["prov/ok", "prov/mute"], per_model_timeout=2.0,
    )
    assert usable == {"prov/ok": True, "prov/mute": False}


def test_error_from_event_shapes():
    f = model_probe._error_from_event
    assert f({"type": "session.error",
              "properties": {"error": {"message": "x"}}}) == {"message": "x"}
    assert f({"type": "message.updated",
              "properties": {"info": {"role": "assistant",
                                      "error": {"message": "y"}}}}) == {"message": "y"}
    # a normal completed assistant message carries no error
    assert f({"type": "message.updated",
              "properties": {"info": {"role": "assistant",
                                      "time": {"completed": 2}}}}) is None
    # unrelated events
    assert f({"type": "message.part.updated", "properties": {}}) is None


def test_parse_model_ids_from_config_providers():
    providers = {
        "providers": [
            {"id": "opencode", "name": "OpenCode Zen",
             "models": {
                 "deepseek-v4-flash": {"id": "deepseek-v4-flash", "providerID": "opencode"},
                 "big-pickle": {"id": "big-pickle", "providerID": "opencode"},
             }},
            {"id": "xai", "name": "xAI",
             "models": {"grok-5": {"id": "grok-5", "providerID": "xai"}}},
        ],
        "default": {"opencode": "big-pickle", "xai": "grok-5"},
    }
    ids = model_probe.parse_model_ids(providers)
    assert set(ids) == {"opencode/deepseek-v4-flash", "opencode/big-pickle", "xai/grok-5"}


def test_parse_model_ids_malformed():
    assert model_probe.parse_model_ids({}) == []
    assert model_probe.parse_model_ids(None) == []


def test_parse_model_variants_reads_variant_keys():
    providers = {
        "providers": [
            {"id": "opencode", "name": "OpenCode Zen",
             "models": {
                 # graded: variant keys become the effort levels (order kept)
                 "big-pickle": {
                     "id": "big-pickle", "providerID": "opencode",
                     "variants": {"low": {}, "medium": {}, "high": {}},
                 },
                 # no variants → omitted (no effort control for this model)
                 "deepseek-v4-flash": {
                     "id": "deepseek-v4-flash", "providerID": "opencode",
                 },
                 # empty variants map → omitted
                 "grok-5": {
                     "id": "grok-5", "providerID": "opencode", "variants": {},
                 },
             }},
        ],
    }
    v = model_probe.parse_model_variants(providers)
    assert v == {"opencode/big-pickle": ["low", "medium", "high"]}


def test_parse_model_variants_malformed():
    assert model_probe.parse_model_variants({}) == {}
    assert model_probe.parse_model_variants(None) == {}
    assert model_probe.parse_model_variants({"providers": "nope"}) == {}


def test_disabled_map_uses_opencode_reason():
    m = model_probe.disabled_map({"a": True, "b": False})
    assert m == {"b": model_probe.DISABLED_REASON}


async def test_cache_roundtrip_uses_opencode_suffix(mongo_db):
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    usable = {"opencode/big-pickle": True, "xai/grok-5": False}
    await model_probe.save_probe_cache(mongo_db, "t", "seed1", usable, now=now)
    # stored under the opencode-specific collection name
    doc = await mongo_db[f"t{model_probe.PROBE_CACHE_SUFFIX}"].find_one({"_id": "seed1"})
    assert doc is not None and doc["usable"] == usable
    got = await model_probe.load_probe_cache(mongo_db, "t", "seed1", now=now)
    assert got == usable
    got = await model_probe.load_probe_cache(
        mongo_db, "t", "seed1", now=now + timedelta(hours=25),
    )
    assert got is None
