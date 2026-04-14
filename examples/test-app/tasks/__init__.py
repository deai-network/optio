"""Task definitions for the optio test application."""

from optio_core.models import TaskInstance

from tasks.basic import get_tasks as basic_tasks
from tasks.progress import get_tasks as progress_tasks
from tasks.children import get_tasks as children_tasks
from tasks.cancellation import get_tasks as cancellation_tasks
from tasks.errors import get_tasks as errors_tasks
from tasks.adhoc_ephemeral import get_tasks as adhoc_ephemeral_tasks
from tasks.scheduled import get_tasks as scheduled_tasks


async def get_task_definitions(services: dict) -> list[TaskInstance]:
    return [
        *basic_tasks(),
        *progress_tasks(),
        *children_tasks(),
        *cancellation_tasks(),
        *errors_tasks(),
        *adhoc_ephemeral_tasks(services),
        *scheduled_tasks(),
    ]
