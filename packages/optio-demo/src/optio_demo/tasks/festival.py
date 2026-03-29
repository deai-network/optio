"""Intergalactic Music Festival — generated tasks from template.

Exercises: generated tasks via for-loop, params access, metadata (varied per task),
metadata inheritance to children, conditional child execution. ~2 minutes per concert.
"""

import asyncio
import random
from optio_core.models import TaskInstance
from optio_core.progress_helpers import sequential_progress


VENUES = [
    {"id": "europa", "name": "Europa", "genre": "Space Jazz", "audience": 5000, "songs": 8, "encore": True},
    {"id": "titan", "name": "Titan", "genre": "Methane Blues", "audience": 12000, "songs": 12, "encore": True},
    {"id": "ganymede", "name": "Ganymede", "genre": "Low-G Punk", "audience": 3000, "songs": 6, "encore": False},
    {"id": "callisto", "name": "Callisto", "genre": "Cryo-Folk", "audience": 8000, "songs": 10, "encore": True},
    {"id": "io", "name": "Io", "genre": "Volcanic Metal", "audience": 2000, "songs": 5, "encore": False},
    {"id": "enceladus", "name": "Enceladus", "genre": "Geyser Ambient", "audience": 15000, "songs": 15, "encore": True},
    {"id": "triton", "name": "Triton", "genre": "Retrograde Techno", "audience": 7000, "songs": 9, "encore": True},
    {"id": "phobos", "name": "Phobos", "genre": "Orbital Ska", "audience": 1000, "songs": 4, "encore": False},
]

SONG_NAMES = [
    "Stellar Drift", "Nebula Rain", "Cosmic Lullaby", "Gravity Well Blues",
    "Ion Storm Serenade", "Redshift Romance", "Pulsar Heartbeat", "Dark Matter Waltz",
    "Solar Flare Stomp", "Asteroid Belt Shuffle", "Wormhole Express", "Supernova Sunrise",
    "Quasar Quickstep", "Comet Tail Tango", "Black Hole Ballad",
]


async def _sound_check(ctx):
    checks = ["Testing microphones", "Checking speakers", "Tuning instruments",
              "Adjusting monitor mix", "Sound check complete — levels are perfect"]
    for i, check in enumerate(checks):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(checks) * 100, check)
        await asyncio.sleep(2)


async def _opening_act(ctx):
    steps = ["Opening act takes the stage", "Playing their hit single",
             "Crowd warming up...", "Standing ovation (polite)", "Opening act exits — main event incoming!"]
    for i, step in enumerate(steps):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(steps) * 100, step)
        await asyncio.sleep(3)


async def _play_song(ctx):
    """Plays one song. Song name comes from params."""
    song_name = ctx.params.get("song_name", "Untitled")
    song_num = ctx.params.get("song_num", 1)
    total = ctx.params.get("total_songs", 1)
    genre = ctx.metadata.get("genre", "Music")

    phases = [
        f"({genre}) {song_name} — intro",
        f"({genre}) {song_name} — verse 1",
        f"({genre}) {song_name} — chorus",
        f"({genre}) {song_name} — bridge",
        f"({genre}) {song_name} — finale!",
    ]
    for i, phase in enumerate(phases):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(phases) * 100, phase)
        await asyncio.sleep(1.5)


async def _encore(ctx):
    steps = ["Crowd chanting 'ENCORE! ENCORE!'", "Band returns to stage",
             "Playing the fan favorite...", "Extended guitar solo",
             "Fireworks! Confetti! Standing ovation!", "Best concert in the solar system!"]
    for i, step in enumerate(steps):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(steps) * 100, step)
        await asyncio.sleep(2)


async def _concert(ctx):
    """Generic concert execute function. Reads params for customization."""
    num_songs = ctx.params.get("num_songs", 5)
    do_encore = ctx.params.get("encore", False)
    venue = ctx.params.get("venue", "Unknown")

    # +2 for sound check and opening act, +1 for encore if applicable
    total_phases = 2 + num_songs + (1 if do_encore else 0)
    cb = sequential_progress(ctx, total_phases)

    ctx.report_progress(0, f"Welcome to {venue}! {ctx.params.get('audience_size', '???')} fans in attendance!")

    await ctx.run_child(_sound_check, f"concert-{ctx.process_id}-soundcheck", "Sound Check",
                        description=f"Audio setup for {venue} venue.", on_child_progress=cb)

    await ctx.run_child(_opening_act, f"concert-{ctx.process_id}-opener", "Opening Act",
                        description="Local band warming up the crowd.", on_child_progress=cb)

    # Shuffle song names for variety
    rng = random.Random(ctx.process_id)  # deterministic per venue
    songs = rng.sample(SONG_NAMES, min(num_songs, len(SONG_NAMES)))

    for i, song_name in enumerate(songs):
        await ctx.run_child(
            _play_song,
            f"concert-{ctx.process_id}-song-{i+1}",
            f"Song {i+1}/{num_songs}: {song_name}",
            params={"song_name": song_name, "song_num": i + 1, "total_songs": num_songs},
            description=f"Track {i+1} of the main set.",
            on_child_progress=cb,
        )

    if do_encore:
        await ctx.run_child(_encore, f"concert-{ctx.process_id}-encore", "Encore!",
                            description="The crowd demands more!", on_child_progress=cb)


def get_tasks() -> list[TaskInstance]:
    tasks = []
    for venue in VENUES:
        encore_text = ", plus encore" if venue["encore"] else ""
        tasks.append(TaskInstance(
            execute=_concert,
            process_id=f"concert-{venue['id']}",
            name=f"Concert on {venue['name']}",
            description=(
                f"A {venue['genre']} concert for {venue['audience']} fans. "
                f"{venue['songs']} songs planned{encore_text}. "
                f"Generated task — same execute function, different params and metadata."
            ),
            params={
                "venue": venue["name"],
                "audience_size": venue["audience"],
                "num_songs": venue["songs"],
                "encore": venue["encore"],
            },
            metadata={
                "venue": venue["name"],
                "sector": "outer-solar-system",
                "genre": venue["genre"],
            },
        ))
    return tasks
