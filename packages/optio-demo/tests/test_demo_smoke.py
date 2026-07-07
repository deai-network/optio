"""Smoke test: optio-demo's opencode task imports and is well-formed."""

import inspect

from optio_demo.tasks.opencode import get_tasks


def test_get_tasks_is_async_services_factory():
    """The opencode demo is a seed-lifecycle factory: an async
    ``get_tasks(services)`` returning the static demo + seed-setup tasks plus
    one seed-pinned task per captured seed (the latter discovered from Mongo
    via ``services``). Structural check only — calling it needs a db, which
    the seed integration tests already cover."""
    assert inspect.iscoroutinefunction(get_tasks)
    assert list(inspect.signature(get_tasks).parameters) == ["services"]

    src = inspect.getsource(inspect.getmodule(get_tasks))
    # the static tasks the factory always emits
    assert 'process_id="opencode-demo"' in src
    assert 'process_id="opencode-seed-setup"' in src
    assert "create_task" in src


def test_demo_does_not_use_wrapper_execute_pattern():
    """Confirm no _make_on_deliverable factory or inner.execute(ctx) wrapping."""
    import optio_demo.tasks.opencode as mod
    src = inspect.getsource(mod)
    assert "_make_on_deliverable" not in src
    # No inner-task execute wrapping.
    assert "inner.execute(ctx)" not in src


def test_demo_module_defines_all_three_hook_kinds():
    """Confirm before_execute, after_execute, and on_deliverable are all wired."""
    import optio_demo.tasks.opencode as mod
    assert hasattr(mod, "_before_execute")
    assert hasattr(mod, "_after_execute")
    assert hasattr(mod, "_on_deliverable")
    src = inspect.getsource(mod)
    # All three are referenced in the OpencodeTaskConfig.
    assert "before_execute=_before_execute" in src
    assert "after_execute=_after_execute" in src
    assert "on_deliverable=_on_deliverable" in src
