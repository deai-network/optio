"""TaskFactory: the checks of its parameters, and the TaskInstance of a launch.

A factory's `parameters` are vultus-core field definitions as plain JSON, the
subset a definition can carry as data (spec 1.3): `check_parameters` checks
them at task sync. `check_values` checks a launch's values before its child
exists (spec 1.4). `child_task` is the TaskInstance of one launch (spec 1.2),
and `is_factory_launch` tells its record. Nothing is coerced: a value of the
wrong JSON type is refused.

Throughout, a value that is no value (`_is_no_value`: None; for a text the
empty string, for a manyOf the empty list) counts as absent: a required
parameter needs another, and a launch's params leave it out.
Spec: docs/2026-10-20-task-factory-design.md §1.
"""

import dataclasses
import math
import re
import secrets
from typing import Any

from optio_core.models import TaskFactory, TaskInstance

# The kinds a definition may have, each with its own static options (spec 1.3,
# plus vultus-core's `integer` for the numeric kinds).
_KIND_OPTIONS: dict[str, frozenset[str]] = {
    "boolean": frozenset(),
    "oneOf": frozenset({"choices"}),
    "manyOf": frozenset({"choices"}),
    "numeric": frozenset({"min", "max", "step", "integer"}),
    "range": frozenset({"min", "max", "step", "integer"}),
    "text": frozenset({"maxLength"}),
}
# A parameter's id: an identifier, so that a refusal's "<id>: " prefix is
# unambiguous and the id is a plain Mongo key.
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COMMON_KEYS = frozenset({"kind", "id", "label", "initialValue", "required"})
_CHOICE_KEYS = frozenset({"value", "label"})
_LABEL_PROBLEM = "label must be a string or a map of language code to string"


def check_parameters(parameters: list[dict]) -> None:
    """Check a factory's parameter definitions (spec 1.3). Raises
    ValueError("<id>: <what>") at the first problem; a definition without a
    usable id is named by its position ("parameters[2]: ...")."""
    if not isinstance(parameters, list):
        raise ValueError("parameters: must be a list of field definitions")
    seen: set[str] = set()
    for i, definition in enumerate(parameters):
        if not isinstance(definition, dict):
            raise ValueError(f"parameters[{i}]: must be an object")
        pid = definition.get("id")
        if not isinstance(pid, str) or not _ID.fullmatch(pid):
            raise ValueError(
                f"parameters[{i}]: id must be an identifier "
                f"(letters, digits and _, not starting with a digit)"
            )
        if pid == "ephemeral":
            raise ValueError(
                f"parameters[{i}]: id 'ephemeral' is reserved for the launch's own flag"
            )
        if pid in seen:
            raise ValueError(f"{pid}: id is not unique")
        seen.add(pid)
        problem = _definition_problem(definition)
        if problem is not None:
            raise ValueError(f"{pid}: {problem}")


def check_values(parameters: list[dict], values: dict[str, Any]) -> str | None:
    """Check a launch's values against the factory's checked parameter
    definitions (spec 1.4): every key is a parameter's id, every required
    parameter (`required`, default true) has a value, and each value has its
    kind's type and options. Returns the refusal, "<id>: <what>", or None.

    A null value is no value; for a text or manyOf parameter, neither is the
    empty string or list. A parameter without a value is left out of the
    checks of its kind."""
    if not isinstance(values, dict):
        return "params: must be an object of parameter values"
    by_id = {p["id"]: p for p in parameters}
    for key in values:
        if key not in by_id:
            return f"{key}: not a parameter"
    for p in parameters:
        value = values.get(p["id"])
        if _is_no_value(p["kind"], value):
            if p.get("required") is not False:
                return f"{p['id']}: required"
            continue
        problem = _value_problem(p, value)
        if problem is not None:
            return f"{p['id']}: {problem}"
    return None


def without_no_values(parameters: list[dict], values: dict[str, Any]) -> dict[str, Any]:
    """`values` without the ones that are no value of their parameter (as
    the required check counts it: None; '' for a text, [] for a manyOf): a
    launch's params as its task (and the factory's `validate` and
    `name_for`) see them, so that a value left out, a null one and an empty
    one look the same, whether a form or a program launched it. False and 0
    are values."""
    kinds = {p["id"]: p["kind"] for p in parameters}
    return {k: v for k, v in values.items() if not _is_no_value(kinds.get(k, ""), v)}


def child_task(
    factory: TaskFactory, values: dict[str, Any], process_id: str | None = None,
) -> TaskInstance:
    """The TaskInstance of one launch of `factory` with `values` (already
    checked), the one place a launch's task is built (a launch, and the
    rebuild of a kept one from its stored values and id).

    `params` = the values without the ones that are no value
    (without_no_values); the process id is `process_id` or the factory's plus
    "_" and 8 random hex digits (a path segment when the factory's id is one,
    as optio-host wants an agent task's: [A-Za-z0-9._-]); the name is
    `name_for(values)` or the factory's (the instance's, with `task_for`);
    the metadata is the factory's plus `taskFactory: <the factory's process
    id>` (with `task_for`, over the instance's). With `execute`, the other
    fields are the factory's (spec 1.2). With `task_for`, they are those of
    the instance `task_for(values, process_id)` returns, whose own
    process_id, params, metadata, name and ttl_seconds optio overrides, as
    the factory's `ttl_seconds` is the launch's (launch options spec 1.4).
    An exception from `task_for` propagates."""
    values = without_no_values(factory.parameters, values)
    if process_id is None:
        process_id = f"{factory.process_id}_{secrets.token_hex(4)}"
    if factory.task_for is not None:
        instance = factory.task_for(values, process_id)
        name = instance.name
        metadata = {**instance.metadata, **factory.metadata}
    else:
        instance = TaskInstance(
            execute=factory.execute,
            process_id=process_id,
            name=factory.name,
            description=factory.description,
            special=factory.special,
            warning=factory.warning,
            cancellable=factory.cancellable,
            ui_widget=factory.ui_widget,
            auto_cancel_children=factory.auto_cancel_children,
        )
        name = factory.name
        metadata = factory.metadata
    return dataclasses.replace(
        instance,
        process_id=process_id,
        name=factory.name_for(values) if factory.name_for is not None else name,
        params=values,
        metadata={**metadata, "taskFactory": factory.process_id},
        ttl_seconds=factory.ttl_seconds,
    )


def is_factory_launch(proc: dict) -> bool:
    """Whether the process record `proc` is a factory's launch (or a record
    under one, which inherits its metadata): its metadata names the factory,
    `taskFactory: <factory process id>` (child_task). Such a record ran once,
    with its values, and is never launched again (spec 1.2)."""
    factory = (proc.get("metadata") or {}).get("taskFactory")
    return isinstance(factory, str) and factory != ""


def _definition_problem(p: dict) -> str | None:
    """What is wrong with one definition (its id already checked), or None."""
    kind = p.get("kind")
    if not isinstance(kind, str) or kind not in _KIND_OPTIONS:
        return f"kind must be one of {', '.join(_KIND_OPTIONS)}"
    unknown = set(p) - _COMMON_KEYS - _KIND_OPTIONS[kind]
    if unknown:
        return f"unknown key(s) {', '.join(sorted(map(str, unknown)))} for kind {kind}"
    if p.get("label") is not None and not _is_label(p["label"]):
        return _LABEL_PROBLEM
    if p.get("required") is not None and not isinstance(p["required"], bool):
        return "required must be true or false"
    if kind in ("oneOf", "manyOf"):
        problem = _choices_problem(p.get("choices"))
        if problem is not None:
            return problem
    if kind in ("numeric", "range"):
        for key in ("min", "max", "step"):
            if p.get(key) is not None and not _is_number(p[key]):
                return f"{key} must be a number"
        lo, hi, step = p.get("min"), p.get("max"), p.get("step")
        if kind == "range" and (lo is None or hi is None):
            return "a range needs min and max"
        if lo is not None and hi is not None and lo > hi:
            return "min must not be greater than max"
        if step is not None and step <= 0:
            return "step must be positive"
        if p.get("integer") is not None and not isinstance(p["integer"], bool):
            return "integer must be true or false"
    if kind == "text" and p.get("maxLength") is not None:
        m = p["maxLength"]
        if isinstance(m, bool) or not isinstance(m, int) or m < 1:
            return "maxLength must be a positive integer"
    # The form's default is checked like a value, but may be empty.
    if p.get("initialValue") is not None:
        problem = _value_problem(p, p["initialValue"])
        if problem is not None:
            return f"initialValue: {problem}"
    return None


def _choices_problem(choices: Any) -> str | None:
    if not isinstance(choices, list) or not choices:
        return "choices must be a non-empty list"
    seen: set[str] = set()
    for c in choices:
        if not isinstance(c, dict) or not isinstance(c.get("value"), str):
            return "each choice must be an object with a string value"
        value = c["value"]
        unknown = set(c) - _CHOICE_KEYS
        if unknown:
            return f"choice {value!r}: unknown key(s) {', '.join(sorted(map(str, unknown)))}"
        if c.get("label") is not None and not _is_label(c["label"]):
            return f"choice {value!r}: {_LABEL_PROBLEM}"
        if value in seen:
            return f"choice {value!r} is listed twice"
        seen.add(value)
    return None


def _value_problem(p: dict, v: Any) -> str | None:
    """What is wrong with `v` as a value of definition `p` (its kind's type
    and options; spec 1.4), or None."""
    kind = p["kind"]
    if kind == "boolean":
        return None if isinstance(v, bool) else "must be true or false"
    if kind == "oneOf":
        if isinstance(v, str) and v in _choice_values(p):
            return None
        return "must be one of its choices"
    if kind == "manyOf":
        if not isinstance(v, list):
            return "must be a list of its choices"
        choices = _choice_values(p)
        for item in v:
            if not isinstance(item, str) or item not in choices:
                return f"{item!r} is not one of its choices"
        if len(set(v)) != len(v):
            return "lists a choice twice"
        return None
    if kind == "numeric":
        if not _is_number(v):
            return "must be a number"
        if p.get("integer") and not _is_whole(v):
            return "must be a whole number"
        return _bounds_problem(p, v)
    if kind == "range":
        if not (isinstance(v, list) and len(v) == 2 and all(_is_number(x) for x in v)):
            return "must be a [from, to] pair of numbers"
        if p.get("integer") and not all(_is_whole(x) for x in v):
            return "must be a pair of whole numbers"
        if v[0] > v[1]:
            return "from must not be greater than to"
        return _bounds_problem(p, v[0]) or _bounds_problem(p, v[1])
    # text
    if not isinstance(v, str):
        return "must be a string"
    m = p.get("maxLength")
    if m is not None and len(v) > m:
        return f"must be at most {m} characters"
    return None


def _bounds_problem(p: dict, n: int | float) -> str | None:
    lo, hi = p.get("min"), p.get("max")
    if lo is not None and n < lo:
        return f"must be at least {lo}"
    if hi is not None and n > hi:
        return f"must be at most {hi}"
    return None


def _choice_values(p: dict) -> set[str]:
    return {c["value"] for c in p["choices"]}


def _is_no_value(kind: str, v: Any) -> bool:
    if v is None:
        return True
    if kind == "text":
        return v == ""
    if kind == "manyOf":
        return v == []
    return False


def _is_number(v: Any) -> bool:
    """A JSON number: an int or a finite float, never a bool."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v)


def _is_whole(n: int | float) -> bool:
    """A number (already checked) with no fractional part; 2.0 counts, as a
    JSON number need not be written as an integer."""
    return isinstance(n, int) or n.is_integer()


def _is_label(label: Any) -> bool:
    """A string, or a non-empty map of language code to string (the UI picks
    its language's, else `en`'s, else the first)."""
    if isinstance(label, str):
        return True
    return (
        isinstance(label, dict) and bool(label)
        and all(isinstance(k, str) and isinstance(t, str) for k, t in label.items())
    )
