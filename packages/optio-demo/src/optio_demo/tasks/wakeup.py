"""Your 15-min Wake-up Call — cron scheduled task.

Exercises: cron scheduling, automatic re-launch. Fires every 15 minutes.
"""

import asyncio
from datetime import datetime, timezone
from optio_core.models import TaskInstance


async def _wakeup(ctx):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    ctx.report_progress(0, f"ALARM TRIGGERED at {now}")

    sequence = [
        (10, "beep."),
        (20, "beep beep."),
        (30, "BEEP BEEP."),
        (40, "BEEP BEEP BEEP!"),
        (50, "WAKE UP!"),
        (60, "SERIOUSLY, WAKE UP!"),
        (70, "I'M NOT GOING AWAY!"),
        (80, "..."),
        (90, "Fine. Snoozing for 15 minutes."),
        (100, "See you again soon. *evil laugh*"),
    ]
    for pct, msg in sequence:
        if not ctx.should_continue():
            return
        ctx.report_progress(pct, msg)
        await asyncio.sleep(1.5)


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_wakeup,
            process_id="wakeup-call",
            name="Your 15-min Wake-up Call",
            description=(
                "Fires every 15 minutes via cron. Exercises cron scheduling and "
                "automatic re-launch. Will keep going until you stop the app."
            ),
            schedule="*/15 * * * *",
        ),
    ]
