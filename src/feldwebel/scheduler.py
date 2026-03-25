"""APScheduler integration for cron-based process triggering."""

import logging
from typing import Callable, Awaitable

logger = logging.getLogger("feldwebel.scheduler")


class ProcessScheduler:
    """Manages cron schedules for process execution.

    Uses APScheduler 4.x AsyncScheduler for async cron triggers.
    Falls back to a no-op if APScheduler is not available or fails.
    """

    def __init__(self, launch_fn: Callable[[str], Awaitable]):
        self._launch_fn = launch_fn
        self._scheduler = None
        self._job_ids: set[str] = set()

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

    async def sync_schedules(self, tasks: list) -> None:
        """Clear all jobs and re-register from task list."""
        if not self._scheduler:
            return

        # Remove existing jobs
        for job_id in list(self._job_ids):
            try:
                await self._scheduler.remove_job(job_id)
            except Exception:
                pass
        self._job_ids.clear()

        # Register new jobs
        for task in tasks:
            if task.schedule:
                job_id = f"sched_{task.process_id}"
                try:
                    from apscheduler.triggers.cron import CronTrigger
                    trigger = CronTrigger.from_crontab(task.schedule)
                    await self._scheduler.add_job(
                        self._launch_fn,
                        trigger=trigger,
                        id=job_id,
                        args=[task.process_id],
                    )
                    self._job_ids.add(job_id)
                    logger.info(f"Scheduled {task.process_id}: {task.schedule}")
                except Exception as e:
                    logger.error(f"Failed to schedule {task.process_id}: {e}")
