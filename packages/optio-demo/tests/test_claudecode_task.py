"""The claudecode demo task opts into resume explicitly."""

from optio_demo.tasks.claudecode import get_tasks


def test_demo_task_supports_resume():
    tasks = get_tasks()
    assert len(tasks) == 1
    assert tasks[0].supports_resume is True
