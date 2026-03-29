"""The Great Museum Heist — parallel failure, cascading errors, warning.

Exercises: parallel group failure (default, not survived), cascading failure
through nested children (3 levels), warning field, indeterminate progress,
cancel propagation. ~8 minutes.
"""

import asyncio
from optio_core.models import TaskInstance
from optio_core.progress_helpers import average_progress, sequential_progress


async def _disable_cameras(ctx):
    """Sequential: hack -> loop -> erase."""

    async def _hack_mainframe(h_ctx):
        steps = ["Connecting to museum WiFi", "Bypassing firewall", "Accessing camera server",
                 "Extracting admin credentials", "Logging in..."]
        for i, step in enumerate(steps):
            if not h_ctx.should_continue():
                return
            h_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    async def _loop_cameras(l_ctx):
        cams = ["Lobby cam", "Hall A cam", "Hall B cam", "Vault corridor cam",
                "Loading dock cam", "Roof cam"]
        for i, cam in enumerate(cams):
            if not l_ctx.should_continue():
                return
            l_ctx.report_progress((i + 1) / len(cams) * 100, f"Looping {cam} — replaying empty footage")
            await asyncio.sleep(4)

    async def _erase_logs(e_ctx):
        steps = ["Identifying log files", "Wiping access logs", "Clearing audit trail",
                 "Planting false timestamps"]
        for i, step in enumerate(steps):
            if not e_ctx.should_continue():
                return
            e_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    cb = sequential_progress(ctx, 3)
    await ctx.run_child(_hack_mainframe, "heist-cam-hack", "Hacking Museum Mainframe",
                        description="WiFi infiltration and credential theft.", on_child_progress=cb)
    await ctx.run_child(_loop_cameras, "heist-cam-loop", "Looping Security Cameras",
                        description="Replacing live feeds with pre-recorded empty footage.", on_child_progress=cb)
    await ctx.run_child(_erase_logs, "heist-cam-erase", "Erasing Security Logs",
                        description="Removing all evidence of system access.", on_child_progress=cb)


async def _crack_vault(ctx):
    """Deep nesting: outer lock -> inner lock -> laser grid (fails!).
    Cascading failure — no survive_failure."""

    async def _pick_outer_lock(o_ctx):
        steps = ["Examining lock mechanism", "Inserting tension wrench",
                 "Raking pins... click", "Outer lock open!"]
        for i, step in enumerate(steps):
            if not o_ctx.should_continue():
                return
            o_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    async def _pick_inner_lock(i_ctx):
        steps = ["This one's electronic", "Attaching bypass device",
                 "Brute-forcing 6-digit code...", "Code cracked: 847291", "Inner lock open!"]
        for i, step in enumerate(steps):
            if not i_ctx.should_continue():
                return
            i_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(5)

    async def _bypass_laser_grid(l_ctx):
        """Always fails — triggers cascading failure up the chain."""
        steps = ["Mapping laser pattern", "Calculating safe path",
                 "Deploying mirror array", "Redirecting beam 1...", "Redirecting beam 2..."]
        for i, step in enumerate(steps):
            if not l_ctx.should_continue():
                return
            l_ctx.report_progress((i + 1) / len(steps) * 100, step)
            await asyncio.sleep(4)
        raise Exception("Triggered silent alarm! Mirror alignment off by 0.3 degrees! (Intentional — tests cascading failure)")

    await ctx.run_child(_pick_outer_lock, "heist-vault-outer", "Picking Outer Lock",
                        description="Mechanical tumbler lock. Old school.")
    await ctx.run_child(_pick_inner_lock, "heist-vault-inner", "Picking Inner Lock",
                        description="Electronic lock with 6-digit code.")
    await ctx.run_child(_bypass_laser_grid, "heist-vault-laser", "Bypassing Laser Grid",
                        description="This always fails! Cascading failure: laser grid -> vault -> entire heist. No survive_failure.")


async def _distract_guards(ctx):
    """Parallel distractions."""
    cb = average_progress(ctx)
    async with ctx.parallel_group(on_child_progress=cb) as group:
        async def _pizza(p_ctx):
            steps = ["Ordering pizza to museum entrance", "Pizza arriving...",
                     "Guard: 'I didn't order this'", "Delivery person: 'Someone did, it's paid for'",
                     "Guard distracted for 8 minutes arguing about anchovies"]
            for i, step in enumerate(steps):
                if not p_ctx.should_continue():
                    return
                p_ctx.report_progress((i + 1) / len(steps) * 100, step)
                await asyncio.sleep(5)

        async def _car_alarm(c_ctx):
            steps = ["Locating target vehicle across the street", "Triggering alarm remotely",
                     "BEEP BEEP BEEP BEEP", "Guard looking out window",
                     "Guard going outside to investigate"]
            for i, step in enumerate(steps):
                if not c_ctx.should_continue():
                    return
                c_ctx.report_progress((i + 1) / len(steps) * 100, step)
                await asyncio.sleep(4)

        async def _pigeons(pg_ctx):
            steps = ["Releasing trained pigeons at loading dock",
                     "Pigeons entering through ventilation",
                     "Pigeons causing chaos in gift shop",
                     "Guard running to gift shop: 'NOT THE POSTCARDS!'",
                     "Gift shop fully occupied — path clear"]
            for i, step in enumerate(steps):
                if not pg_ctx.should_continue():
                    return
                pg_ctx.report_progress((i + 1) / len(steps) * 100, step)
                await asyncio.sleep(5)

        await group.spawn(_pizza, "heist-distract-pizza", "Fake Pizza Delivery",
                          description="Anchovy-based social engineering.")
        await group.spawn(_car_alarm, "heist-distract-alarm", "Setting Off Car Alarm",
                          description="Remote-triggered distraction across the street.")
        await group.spawn(_pigeons, "heist-distract-pigeons", "Releasing Trained Pigeons",
                          description="Avian chaos agents deployed to the gift shop.")


async def _getaway_driver(ctx):
    """Indeterminate progress — just waiting."""
    messages = [
        "Engine running...", "Checking mirrors...", "Adjusting seat (nervous habit)...",
        "Listening to police scanner... all clear", "Drumming on steering wheel...",
        "Getting nervous...", "Checking watch...", "This is taking too long...",
        "Considering a career change...", "Still waiting...",
    ]
    for msg in messages:
        if not ctx.should_continue():
            return
        ctx.report_progress(None, msg)
        await asyncio.sleep(5)


async def _museum_heist(ctx):
    """All phases run in parallel. The vault cracking fails, taking down the whole heist."""
    cb = average_progress(ctx)
    ctx.report_progress(0, "The heist begins at midnight...")

    async with ctx.parallel_group(on_child_progress=cb) as group:
        await group.spawn(_disable_cameras, "heist-cameras", "Disabling Security Cameras",
                          description="Sequential: hack mainframe -> loop cameras -> erase logs.")
        await group.spawn(_crack_vault, "heist-vault", "Cracking the Vault",
                          description="Deep nesting with cascading failure. Laser grid fails, taking down the whole vault operation.")
        await group.spawn(_distract_guards, "heist-guards", "Distracting the Guards",
                          description="Three parallel distractions: pizza, car alarm, pigeons.")
        await group.spawn(_getaway_driver, "heist-getaway", "Getaway Driver Waiting",
                          description="Indeterminate progress — just anxiously waiting outside.")

    # We'll never reach here — the parallel group raises because vault fails
    ctx.report_progress(100, "Heist complete!")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_museum_heist,
            process_id="museum-heist",
            name="The Great Museum Heist",
            description=(
                "A daring parallel operation that always fails. Exercises parallel group "
                "failure (default, not survived), cascading failure through 3 levels of "
                "nested children, the warning field, and indeterminate progress. ~8 minutes."
            ),
            warning="This is a highly illegal operation",
            metadata={"target": "louvre", "crew_size": "4"},
        ),
    ]
