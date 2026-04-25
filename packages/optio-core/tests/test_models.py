"""Tests for data models."""

from optio_core.models import (
    TaskInstance, ChildResult, ProcessStatus, Progress,
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


async def _dummy(ctx):
    pass


def test_task_instance_supports_resume_default_false():
    task = TaskInstance(execute=_dummy, process_id="t", name="T")
    assert task.supports_resume is False


def test_task_instance_supports_resume_can_be_set():
    task = TaskInstance(execute=_dummy, process_id="t", name="T", supports_resume=True)
    assert task.supports_resume is True
