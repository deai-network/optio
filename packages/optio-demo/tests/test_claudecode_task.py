"""The claudecode demo seed-launched tasks opt into resume explicitly."""

import inspect

from optio_demo.tasks.claudecode import get_tasks


def test_demo_is_async_services_factory_with_resume():
    """The claudecode demo is a seed-lifecycle factory: an async
    ``get_tasks(services)`` returning the seed-setup task plus one
    seed-pinned task per captured seed. Structural check only — calling it
    needs a db, which the seed integration tests already cover. The
    seed-launched demo tasks opt into resume (``supports_resume=True``)."""
    assert inspect.iscoroutinefunction(get_tasks)
    assert list(inspect.signature(get_tasks).parameters) == ["services"]

    src = inspect.getsource(inspect.getmodule(get_tasks))
    assert "create_claudecode_task" in src
    assert 'process_id="claudecode-seed-setup"' in src
    # seed-launched demo tasks opt into resume
    assert "supports_resume=True" in src
