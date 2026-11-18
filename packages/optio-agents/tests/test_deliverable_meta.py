"""optio_agents.deliverable_meta: the entry type, the key constants, meta_get, clamp, builders,
and invoke_on_deliverable."""

import copy
import functools
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from optio_agents.agent_info import AgentInfo
from optio_agents.deliverable_meta import (
    KEY_AGENT_NAME,
    KEY_AGENT_SESSION,
    KEY_AGENT_TYPE,
    KEY_AGENT_VERSION,
    KEY_DELIVERED_AT,
    KEY_EFFORT,
    KEY_GATEWAY_REGION,
    KEY_MODEL,
    KEY_MODEL_LABEL,
    KEY_ORIGIN,
    KEY_PROVIDER,
    KEY_RESUMED,
    ORIGIN_AGENT,
    ORIGIN_HARNESS,
    SOURCE_AGENT,
    SOURCE_CONFIG,
    SOURCE_HARNESS,
    SOURCE_OPTIO,
    SOURCE_SEED,
    agent_entries,
    clamp,
    delivery_entries,
    entry,
    invoke_on_deliverable,
    meta_get,
)

MD = [entry("model", "claude-opus-5", "optio"), entry("model", "lyceum/kimi-k3.1-pro", "agent"),
      entry("model.label", "Kimi K3.1 Pro", "seed")]


def test_the_key_and_source_names():
    assert (KEY_ORIGIN, KEY_AGENT_TYPE, KEY_AGENT_NAME, KEY_AGENT_VERSION, KEY_AGENT_SESSION, KEY_PROVIDER,
            KEY_GATEWAY_REGION, KEY_MODEL, KEY_MODEL_LABEL, KEY_EFFORT, KEY_DELIVERED_AT, KEY_RESUMED) == (
        "origin", "agent.type", "agent.name", "agent.version", "agent.session", "provider",
        "gateway.region", "model", "model.label", "effort", "delivered_at", "resumed")
    assert (SOURCE_CONFIG, SOURCE_OPTIO, SOURCE_AGENT, SOURCE_SEED, SOURCE_HARNESS) == (
        "config", "optio", "agent", "seed", "harness")


def test_meta_get_picks_by_source_preference():
    assert meta_get(MD, KEY_MODEL) == "lyceum/kimi-k3.1-pro"
    assert meta_get(MD, KEY_MODEL, prefer=(SOURCE_OPTIO, SOURCE_AGENT)) == "claude-opus-5"
    assert meta_get(MD, KEY_MODEL, prefer=(SOURCE_SEED,)) is None
    assert meta_get(MD, KEY_MODEL_LABEL) == "Kimi K3.1 Pro"
    assert meta_get(MD, KEY_AGENT_VERSION) is None
    assert meta_get(None, KEY_MODEL) is None and meta_get([], KEY_MODEL) is None
    assert meta_get([entry("model", "a", "agent"), entry("model", "b", "agent")], KEY_MODEL) == "a"


def test_clamp_keeps_scalars_only_and_only_the_three_fields():        # RF5
    md = [entry("a", "x", "optio"), {"key": "b", "value": {"x": 1}, "source": "agent"},
          {"key": "c", "value": [1], "source": "agent"}, entry("d", 3, "agent"), entry("e", 1.5, "agent"),
          entry("f", True, "agent"), entry("g", None, "agent"), "junk", {"key": 1, "value": "x", "source": "agent"},
          {"key": "h", "value": "y", "source": "agent", "token": "rq-sentinel"}]
    assert clamp(md) == [entry("a", "x", "optio"), entry("d", 3, "agent"), entry("e", 1.5, "agent"),
                         entry("f", True, "agent"), entry("g", None, "agent"), entry("h", "y", "agent")]


def test_clamp_drops_a_non_str_source_and_a_missing_value():
    md = [{"key": "a", "value": "x", "source": 7}, {"key": "b", "value": "x", "source": None},
          {"key": "c", "source": "agent"}, entry("d", "y", "agent")]
    assert clamp(md) == [entry("d", "y", "agent")]


def test_clamp_drops_what_bson_cannot_store():
    md = [entry("a", 2**63, "agent"), entry("b", -(2**63) - 1, "agent"), entry("c", 2**63 - 1, "agent"),
          entry("d", -(2**63), "agent"), entry("e", float("nan"), "agent"), entry("f", float("inf"), "agent"),
          entry("g", float("-inf"), "agent"), entry("h", True, "agent"), entry("i", 1.5, "agent")]
    assert clamp(md) == [entry("c", 2**63 - 1, "agent"), entry("d", -(2**63), "agent"),
                         entry("h", True, "agent"), entry("i", 1.5, "agent")]


def test_clamp_cuts_values_and_the_count():
    assert clamp([entry("a", "x" * 300, "agent")])[0]["value"] == "x" * 256
    md = [entry(f"k{i}", "v", "agent") for i in range(40)]
    before = copy.deepcopy(md)
    out = clamp(md)
    assert [e["key"] for e in out] == [f"k{i}" for i in range(32)]
    assert md == before
    long_md = [{"key": "a", "value": "x" * 300, "source": "agent", "extra": 1}]
    long_before = copy.deepcopy(long_md)
    clamp(long_md)
    assert long_md == long_before
    assert all(a is not b for a, b in zip(out, md))


def test_agent_entries():
    config = SimpleNamespace(agent_type="claudecode_requesty")
    info = AgentInfo("claudecode_requesty", "Claude Code + Requesty", "https://requesty.ai")
    assert agent_entries(config, info) == [entry("agent.type", "claudecode_requesty", "config"),
                                           entry("agent.name", "Claude Code + Requesty", "config")]


def test_delivery_entries():
    now = datetime(2026, 11, 18, 9, 30, 5, 123000, tzinfo=timezone.utc)
    assert delivery_entries(origin=ORIGIN_AGENT, source=SOURCE_OPTIO, resumed=True, now=now) == [
        entry("origin", "agent", "optio"),
        entry("delivered_at", "2026-11-18T09:30:05.123+00:00", "optio"),
        entry("resumed", True, "optio"),
    ]
    harness = delivery_entries(origin=ORIGIN_HARNESS, source=SOURCE_HARNESS, resumed=None, now=now)
    assert [e["key"] for e in harness] == ["origin", "delivered_at"]
    assert all(e["source"] == "harness" for e in harness)
    plus_two = datetime(2026, 11, 18, 11, 30, 5, 123000, tzinfo=timezone(timedelta(hours=2)))
    shifted = delivery_entries(origin=ORIGIN_AGENT, source=SOURCE_OPTIO, resumed=False, now=plus_two)
    assert shifted[1] == entry("delivered_at", "2026-11-18T09:30:05.123+00:00", "optio")
    assert shifted[2] == entry("resumed", False, "optio")


CALLS = []


async def _three(h, p, t):
    CALLS.append((h, p, t))
    return "r3"


async def _four(h, p, t, md): CALLS.append((h, p, t, md))
async def _excavator_form(h, p, t, metadata=None): CALLS.append((h, p, t, metadata))
async def _kw_only(h, p, t, *, metadata): CALLS.append((h, p, t, {"metadata": metadata}))
async def _var_args(*args): CALLS.append(args)
async def _var_kwargs(h, p, t, **kw): CALLS.append((h, p, t, kw))


class _Bound:
    async def cb(self, h, p, t): CALLS.append((h, p, t))


async def _extra_first(extra, h, p, t, md): CALLS.append((h, p, t, md))


class _NoWeakref:
    __slots__ = ()

    async def __call__(self, h, p, t, md): CALLS.append((h, p, t, md))


MDX = [entry("model", "m", "agent")]


@pytest.mark.parametrize("cb, expected", [
    (_three, ("H", "p", "t")),                                          # RF3
    (_Bound().cb, ("H", "p", "t")),                                     # RF3
    (_four, ("H", "p", "t", MDX)), (_excavator_form, ("H", "p", "t", MDX)),
    (_kw_only, ("H", "p", "t", {"metadata": MDX})), (_var_args, ("H", "p", "t", MDX)),
    (_var_kwargs, ("H", "p", "t", {"metadata": MDX})), (functools.partial(_extra_first, "x"), ("H", "p", "t", MDX)),
])
async def test_the_metadata_goes_only_to_a_callback_that_takes_it(cb, expected):
    CALLS.clear()
    await invoke_on_deliverable(cb, "H", "p", "t", MDX)
    assert CALLS == [expected]


async def test_the_callbacks_answer_is_returned():
    assert await invoke_on_deliverable(_three, "H", "p", "t", MDX) == "r3"
    assert await invoke_on_deliverable(_four, "H", "p", "t", MDX) is None


def _count_signature_reads(monkeypatch, cb):
    """Wrap inspect.signature; the returned dict counts the reads of ``cb``."""
    real = inspect.signature
    reads = {"n": 0}

    def counting(fn, *args, **kwargs):
        if fn is cb:
            reads["n"] += 1
        return real(fn, *args, **kwargs)

    monkeypatch.setattr(inspect, "signature", counting)
    return reads


async def test_the_signature_is_read_once_per_callback(monkeypatch):
    async def four(h, p, t, md): CALLS.append((h, p, t, md))   # fresh: not yet read by another test
    reads = _count_signature_reads(monkeypatch, four)
    CALLS.clear()
    await invoke_on_deliverable(four, "H", "p", "t", MDX)
    await invoke_on_deliverable(four, "H", "p", "t", MDX)
    assert CALLS == [("H", "p", "t", MDX)] * 2
    assert reads["n"] == 1


async def test_a_callback_without_weak_references_is_read_each_time(monkeypatch):
    cb = _NoWeakref()
    reads = _count_signature_reads(monkeypatch, cb)
    CALLS.clear()
    await invoke_on_deliverable(cb, "H", "p", "t", MDX)
    await invoke_on_deliverable(cb, "H", "p", "t", MDX)
    assert CALLS == [("H", "p", "t", MDX)] * 2
    assert reads["n"] == 2


@pytest.mark.parametrize("error", [ValueError, TypeError])
async def test_an_unreadable_signature_gets_three_arguments(monkeypatch, error):
    async def fresh(*args): CALLS.append(args)
    real = inspect.signature

    def unreadable(fn, *args, **kwargs):
        if fn is fresh:
            raise error("no signature")
        return real(fn, *args, **kwargs)

    monkeypatch.setattr(inspect, "signature", unreadable)
    CALLS.clear()
    await invoke_on_deliverable(fresh, "H", "p", "t", MDX)
    assert CALLS == [("H", "p", "t")]
