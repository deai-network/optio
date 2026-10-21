"""Tests for TaskFactory: the checks of its parameter definitions and of a
launch's values, its process record, and launches with values.

Spec: docs/2026-10-20-task-factory-design.md §1, §4.
"""
import asyncio
import dataclasses
import time

import pytest

import optio_core
from optio_core import TaskFactory, TaskInstance, LaunchOutcome, LaunchError
from optio_core.lifecycle import Optio
from optio_core.models import LaunchBlocked
from optio_core.store import get_process_by_process_id, upsert_process
from optio_core.task_factory import check_parameters, check_values


PARAMS = [
    {"kind": "boolean", "id": "dry", "label": "Dry run", "required": False},
    {"kind": "oneOf", "id": "mode", "label": {"en": "Mode", "hu": "Mód"},
     "choices": [{"value": "fast", "label": "Fast"}, {"value": "slow"}]},
    {"kind": "manyOf", "id": "sources", "label": "Sources",
     "choices": [{"value": "a"}, {"value": "b"}, {"value": "c", "label": {"en": "C"}}]},
    {"kind": "numeric", "id": "limit", "label": "Limit", "min": 1, "max": 100, "step": 1},
    {"kind": "range", "id": "units", "label": "Units", "min": 1, "max": 12},
    {"kind": "text", "id": "note", "label": "Note", "maxLength": 5, "required": False},
]
GOOD = {
    "dry": True, "mode": "fast", "sources": ["a", "c"], "limit": 10,
    "units": [2, 5], "note": "hi",
}


async def _wait_until(pred, timeout=60.0):
    """Poll `pred` until it returns something truthy; the timeout only bounds
    a hang."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = await pred()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached within the hang ceiling")


async def _init(mongo_db, prefix, defs, **init_kwargs):
    """An Optio whose task definitions are the current contents of `defs`."""
    async def get_defs(_services, metadata_filter=None):
        return list(defs)

    fw = Optio()
    await fw.init(
        mongo_db=mongo_db, prefix=prefix, get_task_definitions=get_defs,
        **init_kwargs,
    )
    return fw


def _recorder():
    """An execute function recording each run's params and session, by
    process id."""
    seen: dict[str, dict] = {}
    sessions: dict[str, str | None] = {}

    async def execute(ctx):
        seen[ctx.process_id] = ctx.params
        sessions[ctx.process_id] = ctx.session_id

    return execute, seen, sessions


def _factory(execute, **kw):
    kw.setdefault("metadata", {"module": "m"})
    return TaskFactory(
        execute=execute, process_id="fac", name="Drop proposals",
        parameters=PARAMS, **kw,
    )


async def _children(mongo_db, prefix, parent_oid):
    return await mongo_db[f"{prefix}_processes"].find(
        {"parentId": parent_oid},
    ).to_list(None)


async def _noop(ctx):  # noqa: ARG001
    pass


# ---------------------------------------------------------------- the declaration


def test_task_factory_fields_are_the_specs():
    names = [f.name for f in dataclasses.fields(TaskFactory)]
    assert names == [
        "execute", "process_id", "name", "parameters", "description",
        "metadata", "special", "warning", "cancellable", "ui_widget",
        "ttl_seconds", "auto_cancel_children", "validate", "name_for",
    ]
    f = TaskFactory(execute=_noop, process_id="f", name="F", parameters=[])
    assert f.metadata == {} and f.cancellable is True and f.ttl_seconds is None
    assert f.auto_cancel_children is True and f.validate is None and f.name_for is None
    assert optio_core.TaskFactory is TaskFactory
    assert "TaskFactory" in optio_core.__all__


# ---------------------------------------------------------------- the values (spec 1.4)


@pytest.mark.parametrize("pid,value", [
    ("dry", False),
    ("mode", "slow"),
    ("sources", ["b"]),
    ("sources", ["c", "a", "b"]),
    ("limit", 1),
    ("limit", 100),
    ("limit", 2.5),
    ("units", [1, 12]),
    ("units", [3, 3]),
    ("units", [1.5, 2]),
    ("note", "12345"),
    ("note", ""),
])
def test_check_values_accepts_good_values(pid, value):
    assert check_values(PARAMS, {**GOOD, pid: value}) is None


@pytest.mark.parametrize("pid,value,what", [
    ("dry", "true", "must be true or false"),
    ("dry", 1, "must be true or false"),
    ("dry", {"v": True}, "must be true or false"),
    ("mode", "medium", "must be one of its choices"),
    ("mode", True, "must be one of its choices"),
    ("mode", ["fast"], "must be one of its choices"),
    ("mode", {"value": "fast"}, "must be one of its choices"),
    ("sources", "a", "must be a list of its choices"),
    ("sources", {"a": True}, "must be a list of its choices"),
    ("sources", ["a", "x"], "'x' is not one of its choices"),
    ("sources", ["a", True], "True is not one of its choices"),
    ("sources", [["a"]], "['a'] is not one of its choices"),
    ("sources", ["a", "a"], "lists a choice twice"),
    ("limit", "10", "must be a number"),
    ("limit", True, "must be a number"),
    ("limit", float("nan"), "must be a number"),
    ("limit", float("inf"), "must be a number"),
    ("limit", {"n": 1}, "must be a number"),
    ("limit", [10], "must be a number"),
    ("limit", 0, "must be at least 1"),
    ("limit", 101, "must be at most 100"),
    ("units", [5, 2], "from must not be greater than to"),
    ("units", [0, 5], "must be at least 1"),
    ("units", [2, 13], "must be at most 12"),
    ("units", [2], "must be a [from, to] pair of numbers"),
    ("units", [1, 2, 3], "must be a [from, to] pair of numbers"),
    ("units", ["2", "5"], "must be a [from, to] pair of numbers"),
    ("units", [True, 5], "must be a [from, to] pair of numbers"),
    ("units", {"from": 2, "to": 5}, "must be a [from, to] pair of numbers"),
    ("units", 3, "must be a [from, to] pair of numbers"),
    ("note", "123456", "must be at most 5 characters"),
    ("note", 5, "must be a string"),
    ("note", {"text": "x"}, "must be a string"),
    ("note", ["x"], "must be a string"),
])
def test_check_values_refuses_bad_values_naming_the_parameter(pid, value, what):
    assert check_values(PARAMS, {**GOOD, pid: value}) == f"{pid}: {what}"


def test_check_values_refuses_an_unknown_key():
    assert check_values(PARAMS, {**GOOD, "extra": 1}) == "extra: not a parameter"


@pytest.mark.parametrize("values", [
    {k: v for k, v in GOOD.items() if k != "mode"},
    {**GOOD, "mode": None},
])
def test_check_values_requires_a_required_parameter(values):
    assert check_values(PARAMS, values) == "mode: required"


def test_check_values_an_empty_list_or_text_is_no_value():
    assert check_values(PARAMS, {**GOOD, "sources": []}) == "sources: required"
    params = [{"kind": "text", "id": "t"}]
    assert check_values(params, {"t": ""}) == "t: required"
    assert check_values(params, {"t": "x"}) is None


def test_check_values_optional_parameters_may_be_left_out():
    values = {k: v for k, v in GOOD.items() if k not in ("dry", "note")}
    assert check_values(PARAMS, values) is None
    assert check_values(PARAMS, {**values, "dry": None}) is None


def test_check_values_refuses_values_that_are_not_an_object():
    assert check_values(PARAMS, ["fast"]) is not None


# ---------------------------------------------------------------- the definitions (spec 1.3)


def test_check_parameters_accepts_good_definitions():
    check_parameters(PARAMS)
    check_parameters([])
    check_parameters([
        {"kind": "boolean", "id": "b", "initialValue": True},
        {"kind": "oneOf", "id": "o", "choices": [{"value": "x"}], "initialValue": "x"},
        {"kind": "manyOf", "id": "m", "choices": [{"value": "x"}], "initialValue": []},
        {"kind": "numeric", "id": "n"},
        {"kind": "numeric", "id": "n2", "min": 0, "initialValue": 0},
        {"kind": "range", "id": "r", "min": 0, "max": 5, "step": 0.5, "initialValue": [1, 2]},
        {"kind": "text", "id": "t", "initialValue": ""},
        {"kind": "text", "id": "t2", "label": None, "maxLength": None},
        {"kind": "numeric", "id": "i", "integer": True, "initialValue": 3},
        {"kind": "range", "id": "ir", "min": 1, "max": 9, "integer": True,
         "initialValue": [1, 2.0]},
        {"kind": "numeric", "id": "i2", "integer": False, "initialValue": 1.5},
        {"kind": "boolean", "id": "_private"},
        {"kind": "boolean", "id": "camelCase_2"},
        {"kind": "boolean", "id": "X"},
    ])


INTEGRAL = [
    {"kind": "numeric", "id": "count", "integer": True, "min": 0},
    {"kind": "range", "id": "units", "integer": True, "min": 1, "max": 12},
]


@pytest.mark.parametrize("values", [
    {"count": 3, "units": [1, 12]},
    {"count": 3.0, "units": [2.0, 5]},
    {"count": 0, "units": [4, 4]},
])
def test_check_values_integer_accepts_whole_numbers(values):
    assert check_values(INTEGRAL, values) is None


@pytest.mark.parametrize("values,refusal", [
    ({"count": 1.5, "units": [1, 2]}, "count: must be a whole number"),
    ({"count": 1, "units": [1.5, 3]}, "units: must be a pair of whole numbers"),
    ({"count": 1, "units": [1, 2.5]}, "units: must be a pair of whole numbers"),
    ({"count": True, "units": [1, 2]}, "count: must be a number"),
])
def test_check_values_integer_refuses_fractions(values, refusal):
    assert check_values(INTEGRAL, values) == refusal


@pytest.mark.parametrize("parameters,error", [
    ({"id": "x", "kind": "text"}, "parameters: must be a list of field definitions"),
    (["x"], "parameters[0]: must be an object"),
    ([{"kind": "text"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": ""}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": 3}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": "1x"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": "x-y"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": "x:y"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": "x.y"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": "a b"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": "$x"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "text", "id": "x\n"}], "parameters[0]: id must be an identifier"),
    ([{"kind": "boolean", "id": "ok"}, {"kind": "text", "id": "é"}],
     "parameters[1]: id must be an identifier"),
    ([{"kind": "text", "id": "x"}, {"kind": "boolean", "id": "x"}], "x: id is not unique"),
    ([{"kind": "mixed-boolean", "id": "x"}], "x: kind must be one of"),
    ([{"kind": ["text"], "id": "x"}], "x: kind must be one of"),
    ([{"id": "x"}], "x: kind must be one of"),
    ([{"kind": "text", "id": "x", "integer": True}], "x: unknown key(s) integer"),
    ([{"kind": "numeric", "id": "x", "integer": "yes"}], "x: integer must be true or false"),
    ([{"kind": "range", "id": "x", "min": 0, "max": 5, "integer": 1}],
     "x: integer must be true or false"),
    ([{"kind": "numeric", "id": "x", "integer": True, "initialValue": 1.5}],
     "x: initialValue: must be a whole number"),
    ([{"kind": "numeric", "id": "x", "unit": "kg"}], "x: unknown key(s) unit"),
    ([{"kind": "numeric", "id": "x", "choices": []}], "x: unknown key(s) choices"),
    ([{"kind": "text", "id": "x", "visible": False}], "x: unknown key(s) visible"),
    ([{"kind": "text", "id": "x", "label": 5}], "x: label must be a string or a map"),
    ([{"kind": "text", "id": "x", "label": {}}], "x: label must be a string or a map"),
    ([{"kind": "text", "id": "x", "label": {"en": 1}}], "x: label must be a string or a map"),
    ([{"kind": "text", "id": "x", "required": "yes"}], "x: required must be true or false"),
    ([{"kind": "oneOf", "id": "x"}], "x: choices must be a non-empty list"),
    ([{"kind": "manyOf", "id": "x", "choices": []}], "x: choices must be a non-empty list"),
    ([{"kind": "oneOf", "id": "x", "choices": [{"label": "A"}]}],
     "x: each choice must be an object with a string value"),
    ([{"kind": "oneOf", "id": "x", "choices": [{"value": 1}]}],
     "x: each choice must be an object with a string value"),
    ([{"kind": "oneOf", "id": "x", "choices": ["a"]}],
     "x: each choice must be an object with a string value"),
    ([{"kind": "oneOf", "id": "x", "choices": [{"value": "a"}, {"value": "a"}]}],
     "x: choice 'a' is listed twice"),
    ([{"kind": "oneOf", "id": "x", "choices": [{"value": "a", "icon": "i"}]}],
     "x: choice 'a': unknown key(s) icon"),
    ([{"kind": "oneOf", "id": "x", "choices": [{"value": "a", "label": 2}]}],
     "x: choice 'a': label must be a string or a map"),
    ([{"kind": "numeric", "id": "x", "min": "1"}], "x: min must be a number"),
    ([{"kind": "numeric", "id": "x", "max": True}], "x: max must be a number"),
    ([{"kind": "numeric", "id": "x", "min": 5, "max": 1}], "x: min must not be greater than max"),
    ([{"kind": "numeric", "id": "x", "step": 0}], "x: step must be positive"),
    ([{"kind": "range", "id": "x", "min": 1}], "x: a range needs min and max"),
    ([{"kind": "range", "id": "x", "max": 1}], "x: a range needs min and max"),
    ([{"kind": "text", "id": "x", "maxLength": 0}], "x: maxLength must be a positive integer"),
    ([{"kind": "text", "id": "x", "maxLength": "5"}], "x: maxLength must be a positive integer"),
    ([{"kind": "text", "id": "x", "maxLength": True}], "x: maxLength must be a positive integer"),
    ([{"kind": "numeric", "id": "x", "min": 1, "initialValue": 0}],
     "x: initialValue: must be at least 1"),
    ([{"kind": "oneOf", "id": "x", "choices": [{"value": "a"}], "initialValue": "b"}],
     "x: initialValue: must be one of its choices"),
    ([{"kind": "boolean", "id": "x", "initialValue": "true"}],
     "x: initialValue: must be true or false"),
    ([{"kind": "text", "id": "x", "maxLength": 2, "initialValue": "abc"}],
     "x: initialValue: must be at most 2 characters"),
])
def test_check_parameters_refuses_bad_definitions(parameters, error):
    with pytest.raises(ValueError) as exc:
        check_parameters(parameters)
    assert str(exc.value).startswith(error)


# ---------------------------------------------------------------- the record (spec 1.1)


async def test_sync_writes_the_factory_record(mongo_db):
    prefix = "tf_sync"
    defs = [
        TaskInstance(execute=_noop, process_id="plain", name="Plain"),
        _factory(
            _noop, description="Drops", special=True, warning="Careful",
            ui_widget="w", ttl_seconds=600,
        ),
    ]
    fw = await _init(mongo_db, prefix, defs)
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        assert fac["taskFactory"] is True
        assert fac["parameters"] == PARAMS
        assert fac["name"] == "Drop proposals"
        assert fac["description"] == "Drops"
        assert fac["special"] is True and fac["warning"] == "Careful"
        assert fac["metadata"] == {"module": "m"}
        assert fac["ttlSeconds"] == 600
        assert fac["params"] == {}
        # The widget is a launch's: the factory's record never runs.
        assert fac["uiWidget"] is None
        assert fac["parentId"] is None and fac["adhoc"] is False
        assert fac["status"]["state"] == "idle"

        plain = await get_process_by_process_id(mongo_db, prefix, "plain")
        assert "taskFactory" not in plain and "parameters" not in plain

        # Both fields are $set on every sync.
        changed = [{"kind": "boolean", "id": "only"}]
        defs[1] = TaskFactory(
            execute=_noop, process_id="fac", name="Drop", parameters=changed,
        )
        await fw.resync()
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        assert fac["parameters"] == changed and fac["taskFactory"] is True
        assert fac["name"] == "Drop"
    finally:
        await fw.shutdown()


async def test_sync_refuses_a_factory_with_bad_parameters(mongo_db):
    bad = TaskFactory(
        execute=_noop, process_id="fac", name="F",
        parameters=[{"kind": "oneOf", "id": "mode"}],
    )
    fw = Optio()

    async def get_defs(_services, metadata_filter=None):
        return [bad]

    with pytest.raises(ValueError, match="fac.*mode: choices must be a non-empty list"):
        await fw.init(mongo_db=mongo_db, prefix="tf_bad", get_task_definitions=get_defs)


async def test_a_process_id_that_turns_from_factory_to_task_loses_the_fields(mongo_db):
    prefix = "tf_switch"
    execute, seen, _ = _recorder()
    defs = [_factory(_noop)]
    fw = await _init(mongo_db, prefix, defs)
    try:
        defs[0] = TaskInstance(execute=execute, process_id="fac", name="Now a task")
        await fw.resync()
        rec = await get_process_by_process_id(mongo_db, prefix, "fac")
        assert "taskFactory" not in rec and "parameters" not in rec
        assert (await fw.launch("fac", session_id=None)).ok
        await _wait_until(lambda: _state_is(mongo_db, prefix, "fac", "done"))
        assert seen == {"fac": {}}
    finally:
        await fw.shutdown()


async def _state_is(mongo_db, prefix, process_id, state):
    proc = await get_process_by_process_id(mongo_db, prefix, process_id)
    return proc if proc is not None and proc["status"]["state"] == state else None


# ---------------------------------------------------------------- launch refusals (spec 1.2)


async def test_launch_refusals(mongo_db):
    prefix = "tf_refuse"
    execute, seen, _ = _recorder()

    def validate(values):
        if len(values["sources"]) > 2:
            return "sources: pick at most two"
        return None

    defs = [
        TaskInstance(execute=_noop, process_id="plain", name="Plain"),
        _factory(execute, validate=validate),
    ]
    fw = await _init(mongo_db, prefix, defs)
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")

        assert await fw.launch("fac", session_id=None) == LaunchOutcome(
            ok=False, reason="parameters-required",
        )
        assert await fw.launch("plain", session_id=None, params={}) == LaunchOutcome(
            ok=False, reason="not-a-factory",
        )
        assert await fw.launch("plain", session_id=None, params=GOOD) == LaunchOutcome(
            ok=False, reason="not-a-factory",
        )
        assert await fw.launch(
            "fac", resume=True, session_id=None, params=GOOD,
        ) == LaunchOutcome(ok=False, reason="no-resume-support")
        assert await fw.launch(
            "fac", session_id=None, params={**GOOD, "limit": "10"},
        ) == LaunchOutcome(
            ok=False, reason="invalid-parameters", message="limit: must be a number",
        )
        # The factory's own validate runs after the fields' checks.
        assert await fw.launch(
            "fac", session_id=None, params={**GOOD, "sources": ["a", "b", "c"]},
        ) == LaunchOutcome(
            ok=False, reason="invalid-parameters", message="sources: pick at most two",
        )
        assert await fw.launch("nope", session_id=None, params=GOOD) == LaunchOutcome(
            ok=False, reason="not-found",
        )

        # launch_and_wait and launch_and_await_result refuse the same way.
        with pytest.raises(LaunchError) as exc:
            await fw.launch_and_wait("fac", session_id=None)
        assert exc.value.reason == "parameters-required"
        with pytest.raises(LaunchError) as exc:
            await fw.launch_and_wait("plain", session_id=None, params=GOOD)
        assert exc.value.reason == "not-a-factory"
        with pytest.raises(LaunchError) as exc:
            await fw.launch_and_await_result(
                "fac", session_id=None, params={**GOOD, "mode": True}, timeout=60,
            )
        assert exc.value.reason == "invalid-parameters"
        assert exc.value.message == "mode: must be one of its choices"
        assert "mode: must be one of its choices" in str(exc.value)

        # A refused launch makes no child and runs nothing.
        assert await _children(mongo_db, prefix, fac["_id"]) == []
        assert seen == {}
        plain = await get_process_by_process_id(mongo_db, prefix, "plain")
        assert plain["status"]["state"] == "idle"
    finally:
        await fw.shutdown()


async def test_a_launch_block_on_the_launchs_metadata_refuses_it(mongo_db):
    prefix = "tf_block"
    execute, seen, _ = _recorder()
    fw = await _init(mongo_db, prefix, [_factory(execute)])
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        async with fw.block_launches({"taskFactory": "fac"}):
            assert await fw.launch("fac", session_id=None, params=GOOD) == LaunchOutcome(
                ok=False, reason="launch-blocked",
            )
            with pytest.raises(LaunchBlocked):
                await fw.launch_and_wait("fac", session_id=None, params=GOOD)
            with pytest.raises(LaunchError) as exc:
                await fw.launch_and_await_result(
                    "fac", session_id=None, params=GOOD, timeout=60,
                )
            assert exc.value.reason == "launch-blocked"
        assert await _children(mongo_db, prefix, fac["_id"]) == []
        assert seen == {}
    finally:
        await fw.shutdown()


# ---------------------------------------------------------------- launches (spec 1.2)


async def test_a_launch_runs_a_child_of_the_factory_with_the_values(mongo_db):
    prefix = "tf_launch"
    execute, seen, sessions = _recorder()
    defs = [_factory(
        execute, ttl_seconds=3600, ui_widget="w", cancellable=False,
        description="Drops", name_for=lambda v: f"Drop ({v['mode']})",
    )]
    fw = await _init(mongo_db, prefix, defs)
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        outcome = await fw.launch("fac", session_id="s1", params=GOOD)
        assert outcome.ok and outcome.reason is None and outcome.message is None
        child = outcome.proc
        pid = child["processId"]
        head, _, suffix = pid.partition(":")
        assert head == "fac" and len(suffix) == 8
        int(suffix, 16)
        assert child["parentId"] == fac["_id"]
        assert child["rootId"] == fac["_id"]
        assert child["depth"] == 1
        assert child["adhoc"] is True
        assert child["name"] == "Drop (fast)"
        assert child["metadata"] == {"module": "m", "taskFactory": "fac"}
        assert child["params"] == GOOD

        done = await _wait_until(lambda: _state_is(mongo_db, prefix, pid, "done"))
        # The values reach ctx.params as given: nothing coerced.
        assert seen == {pid: GOOD}
        assert type(seen[pid]["limit"]) is int
        assert sessions == {pid: "s1"}
        # The factory's fields are the launch's.
        assert done["cancellable"] is False
        assert done["uiWidget"] == "w"
        assert done["description"] == "Drops"
        # With ttl_seconds, a launch is kept until its TTL.
        assert done["ephemeral"] is False
        assert done["ttlSeconds"] == 3600
        assert done["expireAt"] is not None
        # The factory's record itself never ran.
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        assert fac["status"]["state"] == "idle"
    finally:
        await fw.shutdown()


async def test_a_launch_without_ttl_is_ephemeral(mongo_db):
    prefix = "tf_eph"
    during: list[dict] = []

    async def execute(ctx):
        during.append(await get_process_by_process_id(mongo_db, prefix, ctx.process_id))

    fw = await _init(mongo_db, prefix, [_factory(execute)])
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        await fw.launch_and_wait("fac", session_id=None, params=GOOD)
        assert len(during) == 1
        rec = during[0]
        assert rec["ephemeral"] is True
        assert rec["parentId"] == fac["_id"]
        assert rec["name"] == "Drop proposals"
        assert rec["params"] == GOOD
        # Gone once it ended, record and task.
        assert await get_process_by_process_id(mongo_db, prefix, rec["processId"]) is None
        assert rec["processId"] not in fw._executor._task_registry
        assert await _children(mongo_db, prefix, fac["_id"]) == []
    finally:
        await fw.shutdown()


async def test_an_ephemeral_launch_started_by_launch_goes_away_when_it_ends(mongo_db):
    prefix = "tf_eph2"
    execute, seen, _ = _recorder()
    fw = await _init(mongo_db, prefix, [_factory(execute)])
    try:
        outcome = await fw.launch("fac", session_id=None, params=GOOD)
        assert outcome.ok and outcome.proc["ephemeral"] is True
        pid = outcome.proc["processId"]

        async def gone():
            return pid in seen and await get_process_by_process_id(
                mongo_db, prefix, pid,
            ) is None

        await _wait_until(gone)
        assert seen[pid] == GOOD
    finally:
        await fw.shutdown()


async def test_two_launches_at_once_make_two_children(mongo_db):
    prefix = "tf_two"
    execute, seen, _ = _recorder()
    fw = await _init(mongo_db, prefix, [_factory(execute, ttl_seconds=3600)])
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        values_a = {**GOOD, "limit": 1}
        values_b = {**GOOD, "limit": 2, "mode": "slow"}
        out_a, out_b = await asyncio.gather(
            fw.launch("fac", session_id=None, params=values_a),
            fw.launch("fac", session_id=None, params=values_b),
        )
        assert out_a.ok and out_b.ok
        pid_a, pid_b = out_a.proc["processId"], out_b.proc["processId"]
        assert pid_a != pid_b
        await _wait_until(lambda: _state_is(mongo_db, prefix, pid_a, "done"))
        await _wait_until(lambda: _state_is(mongo_db, prefix, pid_b, "done"))
        assert seen == {pid_a: values_a, pid_b: values_b}
        children = await _children(mongo_db, prefix, fac["_id"])
        assert sorted(c["processId"] for c in children) == sorted([pid_a, pid_b])
    finally:
        await fw.shutdown()


async def test_launch_and_await_result_returns_the_launchs_result(mongo_db):
    prefix = "tf_result"

    async def execute(ctx):
        ctx.publish_result({"limit": ctx.params["limit"]})

    fw = await _init(mongo_db, prefix, [_factory(execute)])
    try:
        result = await fw.launch_and_await_result(
            "fac", session_id=None, params=GOOD, timeout=60,
        )
        assert result == {"limit": 10}
    finally:
        await fw.shutdown()


async def test_a_factory_record_without_a_synced_definition_is_not_launchable(mongo_db):
    prefix = "tf_nodef"
    # The record exists (as a sync left it), but this engine synced no
    # definition for it.
    await upsert_process(mongo_db, prefix, _factory(_noop))
    fw = Optio()
    await fw.init(mongo_db=mongo_db, prefix=prefix)
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        assert fac["taskFactory"] is True
        assert await fw.launch("fac", session_id=None, params=GOOD) == LaunchOutcome(
            ok=False, reason="not-launchable",
        )
        with pytest.raises(LaunchError) as exc:
            await fw.launch_and_wait("fac", session_id=None, params=GOOD)
        assert exc.value.reason == "not-launchable"
        assert await _children(mongo_db, prefix, fac["_id"]) == []
    finally:
        await fw.shutdown()


async def test_null_values_are_left_out_of_the_launchs_params(mongo_db):
    prefix = "tf_nulls"
    execute, seen, _ = _recorder()
    validated: list[dict] = []
    names: list[dict] = []

    def validate(values):
        validated.append(values)
        return None

    def name_for(values):
        names.append(values)
        return "Drop"

    fw = await _init(mongo_db, prefix, [_factory(
        execute, ttl_seconds=3600, validate=validate, name_for=name_for,
    )])
    try:
        values = {**GOOD, "dry": None, "note": None}
        outcome = await fw.launch("fac", session_id=None, params=values)
        assert outcome.ok
        pid = outcome.proc["processId"]
        expected = {k: v for k, v in GOOD.items() if k not in ("dry", "note")}
        assert outcome.proc["params"] == expected
        await _wait_until(lambda: _state_is(mongo_db, prefix, pid, "done"))
        # Left out and null are one shape, for the task and the hooks alike.
        assert seen == {pid: expected}
        assert validated == [expected] and names == [expected]
    finally:
        await fw.shutdown()


async def test_empty_values_are_left_out_of_the_launchs_params(mongo_db):
    # One convention for "no value" (the required check's): a launch from a
    # form, which sends '' for an empty text and [] for an empty list, looks
    # like one that leaves them out. False and 0 are values.
    prefix = "tf_empty"
    execute, seen, _ = _recorder()
    validated: list[dict] = []
    names: list[dict] = []

    def validate(values):
        validated.append(values)
        return None

    def name_for(values):
        names.append(values)
        return "Drop"

    parameters = [
        {"kind": "boolean", "id": "dry", "required": False},
        {"kind": "text", "id": "note", "required": False},
        {"kind": "manyOf", "id": "tags", "required": False, "choices": [{"value": "x"}]},
        {"kind": "numeric", "id": "limit", "required": False},
    ]
    fw = await _init(mongo_db, prefix, [TaskFactory(
        execute=execute, process_id="fac", name="Drop proposals",
        parameters=parameters, ttl_seconds=3600, validate=validate, name_for=name_for,
    )])
    try:
        values = {"dry": False, "note": "", "tags": [], "limit": 0}
        outcome = await fw.launch("fac", session_id=None, params=values)
        assert outcome.ok
        pid = outcome.proc["processId"]
        expected = {"dry": False, "limit": 0}
        assert outcome.proc["params"] == expected
        await _wait_until(lambda: _state_is(mongo_db, prefix, pid, "done"))
        assert seen == {pid: expected}
        assert validated == [expected] and names == [expected]
    finally:
        await fw.shutdown()


async def test_no_child_is_defined_while_shutting_down(mongo_db):
    prefix = "tf_stop"
    execute, seen, _ = _recorder()
    fw = await _init(mongo_db, prefix, [_factory(execute, ttl_seconds=3600)])
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        defined: list[str] = []
        original = fw.adhoc_define

        async def recording_define(task, *args, **kwargs):
            defined.append(task.process_id)
            return await original(task, *args, **kwargs)

        fw.adhoc_define = recording_define
        fw._shutting_down = True
        assert await fw.launch("fac", session_id=None, params=GOOD) == LaunchOutcome(
            ok=False, reason="shutting-down",
        )
        with pytest.raises(LaunchError) as exc:
            await fw.launch_and_wait("fac", session_id=None, params=GOOD)
        assert exc.value.reason == "shutting-down"
        with pytest.raises(LaunchError) as exc:
            await fw.launch_and_await_result(
                "fac", session_id=None, params=GOOD, timeout=60,
            )
        assert exc.value.reason == "shutting-down"
        assert defined == []
        assert await _children(mongo_db, prefix, fac["_id"]) == []
        assert seen == {}
    finally:
        await fw.shutdown()


@pytest.mark.parametrize("entry", ["launch", "launch_and_await_result"])
async def test_a_child_whose_launch_is_refused_after_its_definition_is_deleted(
    mongo_db, entry,
):
    prefix = f"tf_orphan_{entry}"
    execute, seen, _ = _recorder()
    fw = await _init(mongo_db, prefix, [_factory(execute, ttl_seconds=3600)])
    try:
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")
        original = fw.adhoc_define

        async def define_then_stop(task, *args, **kwargs):
            # A shutdown that comes between the child's definition and its
            # launch: the launch's stop sign refuses it.
            proc = await original(task, *args, **kwargs)
            fw._shutting_down = True
            return proc

        fw.adhoc_define = define_then_stop
        if entry == "launch":
            outcome = await fw.launch("fac", session_id=None, params=GOOD)
            assert outcome == LaunchOutcome(ok=False, reason="shutting-down")
        else:
            with pytest.raises(LaunchError) as exc:
                await fw.launch_and_await_result(
                    "fac", session_id=None, params=GOOD, timeout=60,
                )
            assert exc.value.reason == "shutting-down"
        assert await _children(mongo_db, prefix, fac["_id"]) == []
        assert not any(pid.startswith("fac:") for pid in fw._executor._task_registry)
        assert not any(pid.startswith("fac:") for pid in fw._executor._result_futures)
        assert seen == {}
    finally:
        await fw.shutdown()


async def test_a_resync_that_removes_a_factory_cancels_its_running_launches(mongo_db):
    prefix = "tf_gone"
    started = asyncio.Event()

    async def until_cancelled(ctx):
        started.set()
        while ctx.should_continue():
            await asyncio.sleep(0.02)

    defs = [
        TaskInstance(execute=_noop, process_id="plain", name="Plain"),
        _factory(until_cancelled, ttl_seconds=3600),
    ]
    # The grace bounds resync's wait for the cancelled launch; a cooperative
    # unwind needs far less.
    fw = await _init(mongo_db, prefix, defs, cancel_grace_seconds=60.0)
    try:
        outcome = await fw.launch("fac", session_id=None, params=GOOD)
        assert outcome.ok
        pid = outcome.proc["processId"]
        await asyncio.wait_for(started.wait(), timeout=60.0)

        del defs[1]
        await fw.resync()

        assert await get_process_by_process_id(mongo_db, prefix, "fac") is None
        # The launch was asked to stop: it ends cancelled, not by itself.
        await _wait_until(lambda: _state_is(mongo_db, prefix, pid, "cancelled"))
    finally:
        await fw.shutdown()


# ---------------------------------------------------------------- a launch gone before the re-read


def _gone_signal(fw):
    """An event set once the executor's cleanup has deleted an ephemeral
    record (the end of its run)."""
    gone = asyncio.Event()
    real = fw._executor._cleanup_ephemeral

    async def cleanup(process_id):
        await real(process_id)
        if await get_process_by_process_id(
            fw._config.mongo_db, fw._config.prefix, process_id,
        ) is None:
            gone.set()

    fw._executor._cleanup_ephemeral = cleanup
    return gone


def _cancel_rereads_after(fw, gone):
    """Make an ephemeral launch's cleanup win the race against cancel()'s
    re-read of the record: cancel() propagates to the children (and then
    re-reads) only once the record is gone."""
    real = fw._cancel_active_children

    async def after_cleanup(process_id, **kwargs):
        await asyncio.wait_for(gone.wait(), timeout=60.0)
        return await real(process_id, **kwargs)

    fw._cancel_active_children = after_cleanup


def _launch_rereads_after(fw, gone):
    """Make a launch's run (ended at once) and its cleanup win the race
    against launch()'s re-read of the record: once the run's task is made,
    a read of a record waits until the record is gone."""
    launched = asyncio.Event()
    real_launch_process = fw._executor.launch_process
    real_resolve = fw._resolve

    def launch_process(*args, **kwargs):
        launched.set()
        return real_launch_process(*args, **kwargs)

    async def resolve(id_str):
        if launched.is_set():
            await asyncio.wait_for(gone.wait(), timeout=60.0)
        return await real_resolve(id_str)

    fw._executor.launch_process = launch_process
    fw._resolve = resolve


async def _start_until_cancelled(mongo_db, prefix):
    """An Optio with an ephemeral factory whose launch waits for its
    cancellation flag, and that launch, running."""
    started = asyncio.Event()

    async def until_cancelled(ctx):
        started.set()
        await ctx.cancellation_flag.wait()

    fw = await _init(mongo_db, prefix, [_factory(until_cancelled)])
    outcome = await fw.launch("fac", session_id=None, params=GOOD)
    assert outcome.ok and outcome.proc["ephemeral"] is True
    await asyncio.wait_for(started.wait(), timeout=60.0)
    return fw, outcome.proc


async def test_a_cancel_whose_ephemeral_launch_is_gone_by_its_re_read_answers_the_record(
    mongo_db,
):
    # The launch ends as soon as its flag is set, and its record is deleted;
    # the cancel succeeded all the same, so its outcome carries the record
    # read before it.
    prefix = "tf_gone_cancel"
    fw, child = await _start_until_cancelled(mongo_db, prefix)
    try:
        _cancel_rereads_after(fw, _gone_signal(fw))
        outcome = await fw.cancel(str(child["_id"]))
        assert outcome.ok
        assert outcome.proc is not None
        assert outcome.proc["_id"] == child["_id"]
        assert await get_process_by_process_id(mongo_db, prefix, child["processId"]) is None
    finally:
        await fw.shutdown()


async def test_the_engine_rpc_answers_such_a_cancel_with_the_record(mongo_db):
    from optio_core._engine_service import OptioEngineService
    from optio_core._generated.optio_engine import CancelParams

    prefix = "tf_gone_cancel_rpc"
    fw, child = await _start_until_cancelled(mongo_db, prefix)
    try:
        _cancel_rereads_after(fw, _gone_signal(fw))
        result = await OptioEngineService(fw).cancel(
            CancelParams.model_validate({"processId": str(child["_id"])}),
        )
        assert result.root.ok is True
        assert result.root.process.process_id == child["processId"]
    finally:
        await fw.shutdown()


@pytest.mark.parametrize("kind", ["factory", "adhoc"])
async def test_a_launch_whose_ephemeral_record_is_gone_by_its_re_read_answers_the_record(
    mongo_db, kind,
):
    prefix = f"tf_gone_launch_{kind}"
    execute, seen, _ = _recorder()
    fw = await _init(mongo_db, prefix, [_factory(execute)])
    try:
        if kind == "factory":
            target, params = "fac", GOOD
        else:
            await fw.adhoc_define(
                TaskInstance(execute=execute, process_id="eph", name="Eph"),
                ephemeral=True,
            )
            target, params = "eph", None
        _launch_rereads_after(fw, _gone_signal(fw))
        outcome = await fw.launch(target, session_id=None, params=params)
        assert outcome.ok
        assert outcome.proc is not None
        pid = outcome.proc["processId"]
        assert pid in seen
        assert await get_process_by_process_id(mongo_db, prefix, pid) is None
    finally:
        await fw.shutdown()


async def test_the_engine_rpc_answers_such_a_launch_with_the_record(mongo_db):
    from optio_core._engine_service import OptioEngineService
    from optio_core._generated.optio_engine import LaunchParams

    prefix = "tf_gone_launch_rpc"
    execute, seen, _ = _recorder()
    fw = await _init(mongo_db, prefix, [_factory(execute)])
    try:
        _launch_rereads_after(fw, _gone_signal(fw))
        result = await OptioEngineService(fw).launch(LaunchParams.model_validate(
            {"processId": "fac", "sessionId": None, "params": GOOD},
        ))
        assert result.root.ok is True
        assert result.root.process.process_id in seen
    finally:
        await fw.shutdown()


# ---------------------------------------------------------------- the factory never runs


async def test_the_factory_record_never_runs_by_itself(mongo_db):
    prefix = "tf_never"
    runs: list[str] = []

    async def execute(ctx):
        runs.append(ctx.process_id)

    defs = [_factory(execute)]
    fw = await _init(mongo_db, prefix, defs)
    try:
        coll = mongo_db[f"{prefix}_processes"]
        fac = await get_process_by_process_id(mongo_db, prefix, "fac")

        # A direct launch, through every entry point.
        assert not (await fw.launch("fac", session_id=None)).ok
        assert not (await fw.launch(str(fac["_id"]), session_id=None)).ok
        with pytest.raises(LaunchError):
            await fw.launch_and_wait("fac", session_id=None)
        assert await fw._executor.launch_process("fac", session_id=None) is None
        await fw._scheduler_launch_adapter("fac")
        assert (await fw.resurrect("fac", session_id=None)).reason == "no-resurrect-support"

        # A resync, full and scoped.
        await fw.resync()
        await fw.resync(metadata_filter={"module": "m"})

        # The auto-resume sweep, on a record stamped as if it had been
        # interrupted with saved state.
        await coll.update_one({"_id": fac["_id"]}, {"$set": {
            "status.state": "cancelled", "hasSavedState": True,
            "autoResumeScheduled": True,
        }})
        await fw._auto_resume_scheduled_processes()
        swept = await coll.find_one({"_id": fac["_id"]})
        assert swept["status"]["state"] == "cancelled"
        assert swept["autoResumeScheduled"] is False

        assert runs == []
        assert await _children(mongo_db, prefix, fac["_id"]) == []
        assert swept["log"] == []
    finally:
        await fw.shutdown()
