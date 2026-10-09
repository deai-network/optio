"""Claude Code's model list, as the CLI itself reports it.

The stream-json control request ``initialize`` answers with the list Claude
Code's own /model picker shows: aliases (``default``, ``opus[1m]``, ``sonnet``,
``haiku``) with a display name, a description, the full model id each one
resolves to today, and per-model effort levels. The model control is built
from that list; the value shown is the alias the task or the operator picked.
"""

import asyncio

import pytest

from optio_claudecode import models as cc_models
from optio_claudecode.controls import build_controls
from optio_claudecode.conversation import ClaudeCodeConversation, ControlRejected

from .fake_claude import CLI_MODELS, cli_models_for
from .test_conversation_driver import _FakeHandle

ALL = ["low", "medium", "high", "xhigh", "max"]


def _catalog():
    return cc_models.parse_cli_models(CLI_MODELS)


def _controls(catalog=None, *, model=None, runtime_model=None, effort=None):
    return {c["id"]: c for c in build_controls(
        catalog=_catalog() if catalog is None else catalog, model=model, runtime_model=runtime_model, effort=effort,
        permission_mode=None, permission_options=[],
    )}


# --- the CLI's list --------------------------------------------------------------

def test_the_cli_list_maps_to_the_catalog_in_its_own_order():
    assert _catalog() == [
        {"id": "default", "label": "Default (recommended)",
         "description": "Use the default model (currently Opus 5 (1M context)) · $5/$25 per Mtok",
         "resolved": "claude-opus-5[1m]", "effort": ALL},
        {"id": "opus[1m]", "label": "Opus (1M context)",
         "description": "Opus 5 with 1M context · Best for everyday, complex tasks · $5/$25 per Mtok",
         "resolved": "claude-opus-5[1m]", "effort": ALL},
        {"id": "sonnet", "label": "Sonnet",
         "description": "Sonnet 5 · Efficient for routine tasks · $2/$10 per Mtok",
         "resolved": "claude-sonnet-5", "effort": ALL},
        {"id": "haiku", "label": "Haiku",
         "description": "Haiku 4.5 · Fastest for quick answers · $1/$5 per Mtok",
         "resolved": "claude-haiku-4-5-20251001"},
    ]


def test_a_model_without_effort_support_gets_no_levels():
    listed = cc_models.parse_cli_models([
        {"value": "a", "displayName": "A", "supportsEffort": False,
         "supportedEffortLevels": ["low", "high"]},
        {"value": "b", "displayName": "B", "supportedEffortLevels": ["low", "high"]},
        {"value": "c", "displayName": "C", "supportsEffort": True, "supportedEffortLevels": []},
    ])
    assert [m.get("effort") for m in listed] == [None, None, None]


def test_entries_without_a_value_are_skipped_and_a_missing_name_shows_the_value():
    listed = cc_models.parse_cli_models([
        {"displayName": "nameless"}, "junk", {"value": ""}, {"value": "x"},
    ])
    assert listed == [{"id": "x", "label": "x"}]


def test_no_usable_list_means_none():
    for bad in (None, [], "models", [{"displayName": "no value"}]):
        assert cc_models.parse_cli_models(bad) == []


def test_the_fallback_list_is_the_four_aliases_without_descriptions():
    assert cc_models.fallback_models() == [
        {"id": "default", "label": "Default"},
        {"id": "opus", "label": "Opus"},
        {"id": "sonnet", "label": "Sonnet"},
        {"id": "haiku", "label": "Haiku"},
    ]
    # a fresh copy each time: a caller may extend it
    assert cc_models.fallback_models() is not cc_models.fallback_models()


# --- the model control ------------------------------------------------------------

def test_the_model_control_offers_the_cli_list():
    model = _controls()["model"]
    assert model["options"] == [
        {"value": "default", "label": "Default (recommended)", "disabled": False,
         "description": "Use the default model (currently Opus 5 (1M context)) · $5/$25 per Mtok"},
        {"value": "opus[1m]", "label": "Opus (1M context)", "disabled": False,
         "description": "Opus 5 with 1M context · Best for everyday, complex tasks · $5/$25 per Mtok"},
        {"value": "sonnet", "label": "Sonnet", "disabled": False,
         "description": "Sonnet 5 · Efficient for routine tasks · $2/$10 per Mtok"},
        {"value": "haiku", "label": "Haiku", "disabled": False,
         "description": "Haiku 4.5 · Fastest for quick answers · $1/$5 per Mtok"},
    ]


def test_nothing_picked_shows_default_and_its_effort_levels():
    ctrls = _controls()
    assert ctrls["model"]["value"] == "default"
    assert ctrls["reasoning_effort"]["levels"] == ALL
    assert ctrls["reasoning_effort"]["value"] == cc_models.DEFAULT_EFFORT


def test_the_effort_slider_follows_the_picked_alias():
    full = _catalog()
    catalog = [*full[:2], {**full[2], "effort": ["low", "medium", "high"]}, full[3]]
    assert _controls(catalog, model="sonnet")["reasoning_effort"]["levels"] == ["low", "medium", "high"]
    assert "reasoning_effort" not in _controls(catalog, model="haiku")
    assert _controls(catalog, model="opus[1m]", effort="max")["reasoning_effort"]["value"] == "max"


def test_the_runtime_model_does_not_replace_the_alias_that_resolves_to_it():
    # system/init reports the full id the alias stands for today
    assert _controls(runtime_model="claude-opus-5[1m]")["model"]["value"] == "default"
    assert _controls(model="opus[1m]", runtime_model="claude-opus-5[1m]")["model"]["value"] == "opus[1m]"
    assert _controls(model="haiku", runtime_model="claude-haiku-4-5-20251001")["model"]["value"] == "haiku"


def test_a_runtime_model_the_pick_does_not_resolve_to_shows_its_own_alias():
    # e.g. the task's settings.json names another model: system/init tells
    ctrls = _controls(runtime_model="claude-sonnet-5")
    assert ctrls["model"]["value"] == "sonnet"
    # a model no alias resolves to shows as an option of its own
    model = _controls(runtime_model="claude-opus-4-6")["model"]
    assert model["value"] == "claude-opus-4-6"
    assert model["options"][-1] == {"value": "claude-opus-4-6", "label": "claude-opus-4-6", "disabled": False}


def test_a_pinned_full_id_an_alias_resolves_to_shows_that_alias():
    ctrls = _controls(model="claude-sonnet-5", runtime_model="claude-sonnet-5")
    assert ctrls["model"]["value"] == "sonnet"
    assert [o["value"] for o in ctrls["model"]["options"]] == ["default", "opus[1m]", "sonnet", "haiku"]
    assert _controls(model="claude-sonnet-5")["model"]["value"] == "sonnet"


def test_a_full_id_shows_as_its_own_alias_not_as_default():
    # default resolves to claude-opus-5[1m] today too, but only names today's default
    assert _controls(model="claude-opus-5[1m]")["model"]["value"] == "opus[1m]"
    assert _controls(model="sonnet", runtime_model="claude-opus-5[1m]")["model"]["value"] == "opus[1m]"


def test_a_pinned_full_id_the_cli_lists_on_its_own_is_selected():
    # launched with --model claude-opus-4-8 the CLI appends an entry for it
    listed, running = cli_models_for("claude-opus-4-8")
    catalog = cc_models.parse_cli_models(listed)
    ctrls = _controls(catalog, model="claude-opus-4-8", runtime_model=running)
    assert ctrls["model"]["value"] == "claude-opus-4-8"
    assert [o["value"] for o in ctrls["model"]["options"]].count("claude-opus-4-8") == 1


def test_a_pinned_full_id_missing_from_the_list_is_added_as_an_option():
    ctrls = _controls(cc_models.fallback_models(), model="claude-opus-4-8")
    model = ctrls["model"]
    assert model["value"] == "claude-opus-4-8"
    assert model["options"][-1] == {"value": "claude-opus-4-8", "label": "claude-opus-4-8", "disabled": False}
    assert "reasoning_effort" not in ctrls


def test_with_the_fallback_list_the_runtime_model_cannot_move_the_value():
    ctrls = _controls(cc_models.fallback_models(), runtime_model="claude-opus-5[1m]")
    assert ctrls["model"]["value"] == "default"
    assert [o["value"] for o in ctrls["model"]["options"]] == ["default", "opus", "sonnet", "haiku"]


# --- what --model a launch passes --------------------------------------------------

def test_a_fresh_launch_passes_only_a_configured_model():
    assert cc_models.launch_model(None, continuing=False) is None
    assert cc_models.launch_model("sonnet", continuing=False) == "sonnet"
    assert cc_models.launch_model("claude-opus-4-8", continuing=False) == "claude-opus-4-8"


def test_a_continued_session_passes_an_alias_so_it_runs_the_newest_model():
    # --continue alone would keep the full id the transcript ended on
    assert cc_models.launch_model(None, continuing=True) == "default"
    assert cc_models.launch_model("opus[1m]", continuing=True) == "opus[1m]"
    assert cc_models.launch_model("claude-opus-4-8", continuing=True) == "claude-opus-4-8"


# --- asking the running CLI --------------------------------------------------------

async def _answer_next_request(handle, response: dict) -> dict:
    req = await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.feed({"type": "control_response", "response": {
        "request_id": req["request_id"], **response}})
    return req


@pytest.mark.asyncio
async def test_initialize_asks_the_cli_and_keeps_the_answer_out_of_the_event_stream():
    handle = _FakeHandle()
    conv = ClaudeCodeConversation()
    conv.attach(handle)
    events: list = []
    conv.on_event(events.append)
    reader = asyncio.create_task(conv.run_reader())
    ask = asyncio.create_task(conv.initialize())
    req = await _answer_next_request(handle, {
        "subtype": "success", "response": {"models": CLI_MODELS, "commands": []}})
    assert req["type"] == "control_request"
    assert req["request"] == {"subtype": "initialize"}
    assert (await asyncio.wait_for(ask, 60))["models"] == CLI_MODELS
    handle.stdout.feed({"type": "system", "subtype": "status", "status": None})
    handle.stdout.eof()
    await reader
    # the (large) initialize answer is not fanned out, so it is not buffered
    # for replay; the events after it still are
    assert [e["type"] for e in events] == ["system", "x-optio-closed"]


@pytest.mark.asyncio
async def test_initialize_raises_when_the_cli_answers_with_an_error():
    handle = _FakeHandle()
    conv = ClaudeCodeConversation()
    conv.attach(handle)
    reader = asyncio.create_task(conv.run_reader())
    ask = asyncio.create_task(conv.initialize())
    await _answer_next_request(handle, {"subtype": "error", "error": "Already initialized"})
    with pytest.raises(ControlRejected, match="Already initialized"):
        await asyncio.wait_for(ask, 60)
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_fetch_cli_models_reads_the_list():
    handle = _FakeHandle()
    conv = ClaudeCodeConversation()
    conv.attach(handle)
    reader = asyncio.create_task(conv.run_reader())
    fetch = asyncio.create_task(cc_models.fetch_cli_models(conv))
    await _answer_next_request(handle, {"subtype": "success", "response": {"models": CLI_MODELS}})
    assert await asyncio.wait_for(fetch, 60) == _catalog()
    handle.stdout.eof()
    await reader


@pytest.mark.asyncio
async def test_fetch_cli_models_is_none_when_the_cli_cannot_tell():
    handle = _FakeHandle()
    conv = ClaudeCodeConversation()
    conv.attach(handle)
    reader = asyncio.create_task(conv.run_reader())
    # an error answer (e.g. a CLI without initialize)
    fetch = asyncio.create_task(cc_models.fetch_cli_models(conv))
    await _answer_next_request(handle, {"subtype": "error", "error": "unknown subtype"})
    assert await asyncio.wait_for(fetch, 60) is None
    # an answer without models
    fetch = asyncio.create_task(cc_models.fetch_cli_models(conv))
    await _answer_next_request(handle, {"subtype": "success", "response": {"commands": []}})
    assert await asyncio.wait_for(fetch, 60) is None
    # the process ends before answering
    fetch = asyncio.create_task(cc_models.fetch_cli_models(conv))
    await asyncio.wait_for(handle.stdin.lines.get(), 60)
    handle.stdout.eof()
    await reader
    assert await asyncio.wait_for(fetch, 60) is None
    # and once it is closed
    assert await cc_models.fetch_cli_models(conv) is None


@pytest.mark.asyncio
async def test_a_new_process_has_not_reported_its_model_yet():
    conv = ClaudeCodeConversation()
    conv._route({"type": "system", "subtype": "init", "model": "claude-opus-5[1m]"})
    assert conv.runtime_model == "claude-opus-5[1m]"
    conv.begin_restart()
    conv.attach(_FakeHandle())
    # the relaunched process names its model in its own system/init
    assert conv.runtime_model is None
    assert not conv.runtime_model_observed.is_set()


# --- current models first, older versions after them -------------------------

# What a logged-in CLI lists (2.1.29x, as the demo showed): the aliases, then
# older versions as entries of their own, in no useful order.
LOGGED_IN = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)"},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5"},
    {"value": "fable", "resolvedModel": "claude-fable-5-1", "displayName": "Fable 5.1"},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet 5.5"},
    {"value": "haiku", "resolvedModel": "claude-haiku-5-5", "displayName": "Haiku 5.5"},
    {"value": "claude-haiku-4-5-20251001", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku 4.5"},
    {"value": "claude-sonnet-5", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet 5"},
    {"value": "claude-opus-5", "resolvedModel": "claude-opus-5", "displayName": "Opus 5"},
    {"value": "claude-fable-5", "resolvedModel": "claude-fable-5", "displayName": "Fable 5"},
    {"value": "claude-opus-4-8", "resolvedModel": "claude-opus-4-8", "displayName": "Opus 4.8"},
    {"value": "claude-opus-4-7", "resolvedModel": "claude-opus-4-7", "displayName": "Opus 4.7"},
    {"value": "claude-opus-4-6", "resolvedModel": "claude-opus-4-6", "displayName": "Opus 4.6"},
    {"value": "claude-sonnet-4-6", "resolvedModel": "claude-sonnet-4-6", "displayName": "Sonnet 4.6"},
]


def _listed(model):
    return [(o["label"], o.get("group")) for o in model["options"]]


def test_current_models_first_in_the_clis_order_then_older_versions_by_family_newest_first():
    model = _controls(cc_models.parse_cli_models(LOGGED_IN))["model"]
    older = cc_models.OLDER_VERSIONS
    assert _listed(model) == [
        ("Default (recommended)", None), ("Opus 5.5", None), ("Fable 5.1", None),
        ("Sonnet 5.5", None), ("Haiku 5.5", None),
        ("Opus 5", older), ("Opus 4.8", older), ("Opus 4.7", older), ("Opus 4.6", older),
        ("Fable 5", older), ("Sonnet 5", older), ("Sonnet 4.6", older), ("Haiku 4.5", older),
    ]


def test_a_list_without_older_versions_has_no_group():
    assert all(o.get("group") is None for o in _controls()["model"]["options"])


def test_a_selected_older_version_stays_selected_in_its_group():
    model = _controls(cc_models.parse_cli_models(LOGGED_IN), model="claude-opus-4-8")["model"]
    assert model["value"] == "claude-opus-4-8"
    assert ("Opus 4.8", cc_models.OLDER_VERSIONS) in _listed(model)


def test_a_saved_older_version_the_cli_still_lists_is_kept_on_resume():
    catalog = cc_models.parse_cli_models(LOGGED_IN)
    assert cc_models.restore_model(catalog, saved="claude-opus-4-8", resolved="claude-opus-4-8") == "claude-opus-4-8"
