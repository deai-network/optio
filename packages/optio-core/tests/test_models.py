"""Tests for data models."""

from optio_core.models import (
    TaskInstance, TaskInstanceCore, ChildResult, ProcessStatus, Progress,
    matches_filter,
)


async def dummy_execute(ctx):
    pass


def test_task_instance_defaults():
    task = TaskInstance(execute=dummy_execute, process_id="test", name="Test Task")
    assert task.process_id == "test"
    assert task.name == "Test Task"
    assert task.params == {}
    assert task.schedule is None
    assert task.special is False
    assert task.warning is None
    assert task.cancellable is True
    assert task.description is None


def test_task_instance_with_description():
    task = TaskInstance(
        execute=dummy_execute,
        process_id="test",
        name="Test Task",
        description="This task does something useful.",
    )
    assert task.description == "This task does something useful."


def test_process_status_to_dict():
    status = ProcessStatus(state="running")
    d = status.to_dict()
    assert d["state"] == "running"
    assert d["error"] is None


def test_progress_to_dict():
    progress = Progress(percent=42.5, message="Working...")
    d = progress.to_dict()
    assert d["percent"] == 42.5
    assert d["message"] == "Working..."


def test_child_result():
    result = ChildResult(process_id="child_1", state="done")
    assert result.state == "done"
    assert result.error is None


def test_child_result_failed():
    result = ChildResult(process_id="child_1", state="failed", error="boom")
    assert result.error == "boom"


def test_task_instance_supports_resume_default_false():
    task = TaskInstance(execute=dummy_execute, process_id="t", name="T")
    assert task.supports_resume is False


def test_task_instance_supports_resume_can_be_set():
    task = TaskInstance(execute=dummy_execute, process_id="t", name="T", supports_resume=True)
    assert task.supports_resume is True


def test_task_instance_auto_cancel_children_default_true():
    """TaskInstance.auto_cancel_children defaults to True."""
    task = TaskInstance(execute=dummy_execute, process_id="p1", name="P1")
    assert task.auto_cancel_children is True


def test_task_instance_auto_cancel_children_can_be_false():
    """TaskInstance.auto_cancel_children can be explicitly set to False."""
    task = TaskInstance(
        execute=dummy_execute, process_id="p1", name="P1", auto_cancel_children=False,
    )
    assert task.auto_cancel_children is False


def test_matches_filter_none_filter_matches_anything():
    assert matches_filter({}, None) is True
    assert matches_filter({"group": "ingest"}, None) is True


def test_matches_filter_empty_filter_matches_anything():
    assert matches_filter({}, {}) is True
    assert matches_filter({"group": "ingest"}, {}) is True


def test_matches_filter_equality_match():
    assert matches_filter({"group": "ingest"}, {"group": "ingest"}) is True


def test_matches_filter_equality_mismatch():
    assert matches_filter({"group": "ingest"}, {"group": "etl"}) is False


def test_matches_filter_missing_key_is_mismatch():
    assert matches_filter({}, {"group": "ingest"}) is False


def test_matches_filter_and_semantics():
    metadata = {"group": "ingest", "tier": "fast"}
    assert matches_filter(metadata, {"group": "ingest", "tier": "fast"}) is True
    assert matches_filter(metadata, {"group": "ingest", "tier": "slow"}) is False


def test_task_instance_core_minimal():
    """TaskInstanceCore carries only child-applicable fields."""
    task = TaskInstanceCore(execute=dummy_execute, process_id="t", name="T")
    assert task.process_id == "t"
    assert task.name == "T"
    assert task.description is None
    assert task.params == {}


def test_task_instance_is_subclass_of_core():
    """TaskInstance extends TaskInstanceCore — full task is acceptable
    anywhere a Core is expected (e.g. ProcessContext.run_child_task)."""
    task = TaskInstance(execute=dummy_execute, process_id="t", name="T")
    assert isinstance(task, TaskInstanceCore)


def test_task_instance_field_order_unchanged():
    """Existing positional construction order must be preserved across
    the Core/Instance split: execute, process_id, name, description, params,
    metadata, schedule, special, warning, cancellable, ui_widget,
    supports_resume, ttl_seconds, auto_cancel_children."""
    task = TaskInstance(
        dummy_execute, "p", "P", "desc", {"k": "v"},
        {"m": "v"}, "0 * * * *", True, "warn",
        False, "widget", True, 60, False,
    )
    assert task.execute is dummy_execute
    assert task.process_id == "p"
    assert task.name == "P"
    assert task.description == "desc"
    assert task.params == {"k": "v"}
    assert task.metadata == {"m": "v"}
    assert task.schedule == "0 * * * *"
    assert task.special is True
    assert task.warning == "warn"
    assert task.cancellable is False
    assert task.ui_widget == "widget"
    assert task.supports_resume is True
    assert task.ttl_seconds == 60
    assert task.auto_cancel_children is False
