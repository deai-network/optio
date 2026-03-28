"""Task definitions for the optio demo application."""

from optio_core.models import TaskInstance

from optio_demo.tasks.terraforming import get_tasks as terraforming_tasks
from optio_demo.tasks.home import get_tasks as home_tasks
from optio_demo.tasks.heist import get_tasks as heist_tasks
from optio_demo.tasks.festival import get_tasks as festival_tasks
from optio_demo.tasks.wakeup import get_tasks as wakeup_tasks


async def get_task_definitions(services: dict) -> list[TaskInstance]:
    return [
        *terraforming_tasks(),
        *home_tasks(),
        *heist_tasks(),
        *festival_tasks(),
        *wakeup_tasks(),
    ]
