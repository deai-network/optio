"""APScheduler integration for cron-based process triggering."""

import logging
from typing import Callable, Awaitable

from optio_core.models import TaskInstance, ProcessMetadataFilter, matches_filter

logger = logging.getLogger("optio_core_core.scheduler")

# APScheduler logs routine housekeeping ("Cleaned up expired job results and
# finished schedules", etc.) at INFO, which floods consumer logs. Quiet it to
# WARNING; mirrors how optio-claudecode quiets asyncssh.
logging.getLogger("apscheduler").setLevel(logging.WARNING)


def _cron_trigger(task: TaskInstance):
    """The CronTrigger for `task.schedule`, firing `task.schedule_offset_seconds`
    into each matching minute (second 0 when unset)."""
    from apscheduler.triggers.cron import CronTrigger

    offset = task.schedule_offset_seconds
    if offset is None:
        return CronTrigger.from_crontab(task.schedule)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 59:
        raise ValueError(f"schedule_offset_seconds must be an int 0-59, got {offset!r}")
    values = task.schedule.split()
    if len(values) != 5:
        raise ValueError(f"Wrong number of fields; got {len(values)}, expected 5")
    # The fields CronTrigger.from_crontab sets, plus the second.
    return CronTrigger(
        second=offset, minute=values[0], hour=values[1], day=values[2],
        month=values[3], day_of_week=values[4], timezone="local",
    )


class ProcessScheduler:
    """Manages cron schedules for process execution.

    Uses APScheduler 4.x AsyncScheduler for async cron triggers.
    Falls back to a no-op if APScheduler is not available or fails.
    """

    def __init__(self, launch_fn: Callable[[str], Awaitable]):
        self._launch_fn = launch_fn
        self._scheduler = None
        self._jobs: dict[str, TaskInstance] = {}

    async def start(self) -> None:
        """Start the scheduler.

        apscheduler 4.x requires BOTH __aenter__() and start_in_background()
        — the former wires up internal services, the latter runs the
        trigger evaluation loop. Without start_in_background(), schedules
        register cleanly but no job ever fires.

        Idempotent — subsequent calls are no-ops. Optio.init() starts the
        scheduler so _sync_definitions can register schedules into a live
        runloop; Optio.run() also calls start() defensively, and that
        second call must not re-create the apscheduler instance."""
        if self._scheduler is not None:
            return
        try:
            from apscheduler import AsyncScheduler
            self._scheduler = AsyncScheduler()
            await self._scheduler.__aenter__()
            await self._scheduler.start_in_background()
            logger.debug("Scheduler started")
        except Exception as e:
            logger.warning(f"Could not start scheduler: {e}")
            self._scheduler = None

    async def stop(self) -> None:
        """Stop the scheduler."""
        if self._scheduler:
            try:
                await self._scheduler.__aexit__(None, None, None)
            except Exception:
                pass
            logger.debug("Scheduler stopped")

    async def sync_schedules(
        self,
        tasks: list,
        metadata_filter: ProcessMetadataFilter | None = None,
    ) -> None:
        """Sync APScheduler jobs against `tasks`.

        With no `metadata_filter`, every existing job is removed and the
        full task list is re-registered (current behaviour). With a filter,
        only jobs whose stored `TaskInstance.metadata` matches the filter
        are eligible for removal; out-of-scope jobs are preserved.
        """
        if not self._scheduler:
            return

        new_ids = {f"sched_{t.process_id}" for t in tasks if t.schedule}

        for job_id in list(self._jobs):
            existing = self._jobs[job_id]
            if metadata_filter is None:
                should_remove = True
            else:
                should_remove = (
                    matches_filter(existing.metadata, metadata_filter)
                    and job_id not in new_ids
                )
            if should_remove:
                try:
                    await self._scheduler.remove_schedule(job_id)
                except Exception as e:
                    logger.warning(f"Failed to remove scheduled job {job_id}: {e}")
                del self._jobs[job_id]

        for task in tasks:
            if not task.schedule:
                continue
            job_id = f"sched_{task.process_id}"
            if job_id in self._jobs:
                try:
                    await self._scheduler.remove_schedule(job_id)
                except Exception as e:
                    logger.warning(f"Failed to remove scheduled job {job_id} prior to replace: {e}")
            try:
                trigger = _cron_trigger(task)
                await self._scheduler.add_schedule(
                    self._launch_fn,
                    trigger,
                    id=job_id,
                    args=[task.process_id],
                )
                self._jobs[job_id] = task
                logger.debug(
                    f"Scheduled {task.process_id}: {task.schedule}"
                    f" (+{task.schedule_offset_seconds or 0}s)"
                )
            except Exception as e:
                logger.error(f"Failed to schedule {task.process_id}: {e}")
