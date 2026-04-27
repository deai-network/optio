"""APScheduler integration for cron-based process triggering."""

import logging
from typing import Callable, Awaitable

from optio_core.models import TaskInstance, ProcessMetadataFilter, matches_filter

logger = logging.getLogger("optio_core_core.scheduler")


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
        """Start the scheduler."""
        try:
            from apscheduler import AsyncScheduler
            self._scheduler = AsyncScheduler()
            await self._scheduler.__aenter__()
            logger.info("Scheduler started")
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
            logger.info("Scheduler stopped")

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
                    await self._scheduler.remove_job(job_id)
                except Exception as e:
                    logger.warning(f"Failed to remove scheduled job {job_id}: {e}")
                del self._jobs[job_id]

        for task in tasks:
            if not task.schedule:
                continue
            job_id = f"sched_{task.process_id}"
            if job_id in self._jobs:
                try:
                    await self._scheduler.remove_job(job_id)
                except Exception as e:
                    logger.warning(f"Failed to remove scheduled job {job_id} prior to replace: {e}")
            try:
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger.from_crontab(task.schedule)
                await self._scheduler.add_job(
                    self._launch_fn,
                    trigger=trigger,
                    id=job_id,
                    args=[task.process_id],
                )
                self._jobs[job_id] = task
                logger.info(f"Scheduled {task.process_id}: {task.schedule}")
            except Exception as e:
                logger.error(f"Failed to schedule {task.process_id}: {e}")
