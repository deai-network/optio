"""Smoke test: optio-demo's opencode task imports and is well-formed."""

import inspect

from optio_demo.tasks.opencode import get_tasks


def test_get_tasks_returns_one_task_instance():
    tasks = get_tasks()
    assert len(tasks) == 1
    t = tasks[0]
    assert t.process_id == "opencode-demo"
    assert t.name == "Opencode demo"
    assert t.ui_widget == "iframe"


def test_demo_does_not_use_wrapper_execute_pattern():
    """Confirm no _make_on_deliverable factory or inner.execute(ctx) wrapping."""
    import optio_demo.tasks.opencode as mod
    src = inspect.getsource(mod)
    assert "_make_on_deliverable" not in src
    # No inner-task execute wrapping.
    assert "inner.execute(ctx)" not in src
