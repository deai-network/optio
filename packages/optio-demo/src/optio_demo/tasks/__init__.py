"""Task definitions for the optio demo application."""

from optio_core.models import TaskInstance, ProcessMetadataFilter

from optio_demo.tasks.terraforming import get_tasks as terraforming_tasks
from optio_demo.tasks.home import get_tasks as home_tasks
from optio_demo.tasks.heist import get_tasks as heist_tasks
from optio_demo.tasks.festival import get_tasks as festival_tasks
from optio_demo.tasks.wakeup import get_tasks as wakeup_tasks
from optio_demo.tasks.marimo import get_tasks as marimo_tasks
from optio_demo.tasks.opencode import get_tasks as opencode_tasks
from optio_demo.tasks.client_directed import get_tasks as client_directed_tasks
from optio_demo.tasks.claudecode import get_tasks as claudecode_tasks
from optio_demo.tasks.grok import get_tasks as grok_tasks


async def get_task_definitions(
    services: dict,
    metadata_filter: ProcessMetadataFilter | None = None,
) -> list[TaskInstance]:
    return [
        *terraforming_tasks(),
        *home_tasks(),
        *heist_tasks(),
        *festival_tasks(),
        *wakeup_tasks(),
        *marimo_tasks(),
        *await opencode_tasks(services),
        *client_directed_tasks(),
        *await claudecode_tasks(services),
        *await grok_tasks(services),
    ]
