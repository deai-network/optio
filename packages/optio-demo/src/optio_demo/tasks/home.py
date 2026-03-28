"""Organizing Your Home — mixed seq/parallel, error handling, non-cancellable.

Exercises: mixed sequential/parallel children, survive_failure in parallel group,
cancellable=False, indeterminate progress, cancellation ignored. ~10 minutes.
"""

import asyncio
from optio_core.models import TaskInstance
from optio_core.progress_helpers import sequential_progress, average_progress


# ---------------------------------------------------------------------------
# Phase 1: Cleaning Up Your Mess (~3 min)
# ---------------------------------------------------------------------------

async def _collect_socks(ctx):
    rooms = ["Living room", "Bedroom", "Bathroom", "Kitchen (why?!)",
             "Under the bed", "Behind the TV", "Inside the couch cushions"]
    for i, room in enumerate(rooms):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(rooms) * 100, f"{room}: found {2 + i} socks")
        await asyncio.sleep(5)


async def _wash_dishes(ctx):
    batches = ["Coffee mugs (7)", "Plates from last night (4)", "Mystery Tupperware (3)",
               "The pot you've been 'soaking' for 3 days", "Wine glasses (careful!)"]
    for i, batch in enumerate(batches):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(batches) * 100, f"Washing: {batch}")
        await asyncio.sleep(6)


async def _vacuum_couch(ctx):
    findings = [
        "Vacuuming cushion 1... found: 3 coins, 1 pen",
        "Vacuuming cushion 2... found: TV remote! (missing since Tuesday)",
        "Vacuuming cushion 3... found: ancient popcorn civilization",
        "Under the couch: dust bunnies the size of actual bunnies",
        "Behind the couch: the other sock! And a pizza box from... let's not discuss",
    ]
    for i, finding in enumerate(findings):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(findings) * 100, finding)
        await asyncio.sleep(7)


async def _cleaning_phase(ctx):
    cb = sequential_progress(ctx, 3)
    await ctx.run_child(_collect_socks, "home-clean-socks", "Collecting Scattered Socks",
                        description="Room-by-room sock recovery mission.",
                        on_child_progress=cb)
    await ctx.run_child(_wash_dishes, "home-clean-dishes", "Washing the Dishes",
                        description="Batch-by-batch dish assault.",
                        on_child_progress=cb)
    await ctx.run_child(_vacuum_couch, "home-clean-vacuum", "Vacuuming Under the Couch",
                        description="Archaeological expedition beneath the cushions.",
                        on_child_progress=cb)


# ---------------------------------------------------------------------------
# Phase 2: Triaging Your Clothes (~4 min)
# ---------------------------------------------------------------------------

async def _sort_shirts(ctx):
    colors = ["Whites", "Darks", "Colors", "The grey area (literally)",
              "Band t-shirts (sacred, do not fold)", "Work shirts"]
    for i, color in enumerate(colors):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(colors) * 100, f"Sorting: {color}")
        await asyncio.sleep(5)


async def _fold_pants(ctx):
    types = ["Jeans (the easy ones)", "Dress pants (careful with the crease)",
             "Sweatpants (just roll them)", "Shorts (summer optimism)",
             "The pants that don't fit but you're keeping 'just in case'"]
    for i, ptype in enumerate(types):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(types) * 100, f"Folding: {ptype}")
        await asyncio.sleep(6)


async def _decide_throwaway(ctx):
    """Always fails — cannot decide what to throw away."""
    items = ["That hoodie from 2015", "The shirt with the stain you can't identify"]
    for i, item in enumerate(items):
        ctx.report_progress((i + 1) / len(items) * 50, f"Considering: {item}...")
        await asyncio.sleep(5)
    raise Exception("Cannot decide — emotional attachment too strong! (This failure is intentional — testing survive_failure)")


async def _iron_fancy(ctx):
    items = ["Interview shirt", "Date night blouse", "The linen pants (impossible)",
             "Tablecloth (why is this with the clothes?)"]
    for i, item in enumerate(items):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(items) * 100, f"Ironing: {item}")
        await asyncio.sleep(7)


async def _triage_phase(ctx):
    cb = average_progress(ctx)
    async with ctx.parallel_group(survive_failure=True, on_child_progress=cb) as group:
        await group.spawn(_sort_shirts, "home-triage-shirts", "Sorting Shirts by Color",
                          description="Color-coded organization system.")
        await group.spawn(_fold_pants, "home-triage-pants", "Folding Pants",
                          description="Various pants, various techniques.")
        await group.spawn(_decide_throwaway, "home-triage-decide", "Deciding What to Throw Away",
                          description="This task always fails. Emotional attachment wins every time. Tests survive_failure in parallel group.")
        await group.spawn(_iron_fancy, "home-triage-iron", "Ironing the Fancy Stuff",
                          description="The clothes you actually want to look good in.")

    failed = [r for r in group.results if r.state != "done"]
    if failed:
        ctx.report_progress(None, f"Triage mostly done. Couldn't complete: {', '.join(r.process_id for r in failed)}")


# ---------------------------------------------------------------------------
# Phase 3: Petting Your Cats (~3 min)
# None of these children check should_continue() — they ignore cancellation.
# ---------------------------------------------------------------------------

async def _locate_whiskers(ctx):
    """Indeterminate progress — cat location unknown."""
    locations = [
        "Checking behind the couch...", "Checking on top of the fridge...",
        "Checking inside the laundry basket...", "Checking the bathtub (why?)...",
        "Checking the neighbor's yard...", "Found Mr. Whiskers! (He was on the bookshelf the whole time)",
    ]
    for i, loc in enumerate(locations):
        if i < len(locations) - 1:
            ctx.report_progress(None, loc)  # indeterminate — we don't know how long this takes
        else:
            ctx.report_progress(100, loc)
        await asyncio.sleep(5)


async def _belly_rub(ctx):
    """Long task, does NOT check should_continue()."""
    phases = [
        "Approaching cautiously...", "Initial ear scratch — purring detected",
        "Moving to belly — risky maneuver", "Belly rub accepted! Purring intensifies",
        "Cat has entered zen mode", "You have also entered zen mode",
        "Time has lost all meaning", "Cat kicks your hand — session over",
    ]
    for i, phase in enumerate(phases):
        ctx.report_progress((i + 1) / len(phases) * 100, phase)
        await asyncio.sleep(6)


async def _negotiate_treats(ctx):
    """Does NOT check should_continue()."""
    stages = [
        "Mr. Whiskers: staring intently at treat cupboard",
        "You: 'You already had treats today'",
        "Mr. Whiskers: meowing louder",
        "You: 'Fine, just one'",
        "Mr. Whiskers: inhales three treats before you can react",
        "Treaty signed: 3 treats per session, max 2 sessions per day",
    ]
    for i, stage in enumerate(stages):
        ctx.report_progress((i + 1) / len(stages) * 100, stage)
        await asyncio.sleep(5)


async def _cat_phase(ctx):
    cb = sequential_progress(ctx, 3)
    await ctx.run_child(_locate_whiskers, "home-cat-locate", "Locating Mr. Whiskers",
                        description="Indeterminate progress — cat's location is fundamentally unknowable until observed.",
                        on_child_progress=cb)
    await ctx.run_child(_belly_rub, "home-cat-belly", "Extended Belly Rub Session",
                        description="Does not check should_continue(). Cannot be interrupted. This is the way.",
                        on_child_progress=cb)
    await ctx.run_child(_negotiate_treats, "home-cat-treats", "Negotiating Treat Distribution",
                        description="Diplomatic negotiations between human and feline. Ignores cancellation.",
                        on_child_progress=cb)


# ---------------------------------------------------------------------------
# Top-level task
# ---------------------------------------------------------------------------

async def _organizing_home(ctx):
    cb = sequential_progress(ctx, 3)

    ctx.report_progress(0, "Taking a deep breath... here we go.")
    await ctx.run_child(_cleaning_phase, "home-phase-clean", "Phase 1: Cleaning Up Your Mess",
                        description="Sock recovery, dishwashing, couch archaeology. ~3 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_triage_phase, "home-phase-triage", "Phase 2: Triaging Your Clothes",
                        description="Parallel sorting/folding/ironing. One child always fails (emotional attachment). ~4 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_cat_phase, "home-phase-cats", "Phase 3: Petting Your Cats",
                        description="Non-cancellable phase. Children ignore should_continue(). Indeterminate progress. ~3 minutes.",
                        on_child_progress=cb)

    ctx.report_progress(100, "Home is... acceptable. Mr. Whiskers approves.")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_organizing_home,
            process_id="organizing-home",
            name="Organizing Your Home",
            description=(
                "Domestic chaos management. Exercises mixed sequential/parallel children, "
                "survive_failure, indeterminate progress, cancellable=False (dashboard hides "
                "cancel button), and children that ignore cancellation. ~10 minutes."
            ),
            metadata={"location": "home", "difficulty": "extreme"},
            cancellable=False,
        ),
    ]
