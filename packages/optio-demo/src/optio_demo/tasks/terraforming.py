"""Terraforming Mars — the big showcase task tree.

Exercises: sequential_progress, average_progress, mapped_progress,
parallel groups with max_concurrency, 4-level nesting, survive_failure,
cooperative cancellation cascade, metadata inheritance, descriptions.
~30 minutes total runtime.
"""

import asyncio
from optio_core.models import TaskInstance
from optio_core.progress_helpers import sequential_progress, average_progress, mapped_progress


# ---------------------------------------------------------------------------
# Leaf-level helpers
# ---------------------------------------------------------------------------

async def _timed_work(ctx, steps: int, delay: float, messages: list[str] | None = None):
    """Generic worker that reports progress over `steps` iterations."""
    for i in range(steps):
        if not ctx.should_continue():
            return
        pct = (i + 1) / steps * 100
        msg = messages[i] if messages and i < len(messages) else f"Step {i + 1}/{steps}"
        ctx.report_progress(pct, msg)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Phase 1: Survey the Planet (~5 min)
# ---------------------------------------------------------------------------

async def _map_geology(ctx):
    sectors = 12
    for i in range(sectors):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / sectors * 100, f"Sector {i + 1}/{sectors} mapped — found basalt formations")
        await asyncio.sleep(5)


async def _analyze_atmosphere(ctx):
    gases = [
        ("CO2", "95.3%"), ("N2", "2.7%"), ("Ar", "1.6%"),
        ("O2", "0.13%"), ("CO", "0.07%"), ("H2O", "0.03%"),
    ]
    for i, (gas, pct) in enumerate(gases):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(gases) * 100, f"Measuring {gas}: {pct} of atmosphere")
        await asyncio.sleep(7)


async def _detect_water(ctx):
    layers = ["Surface scan", "10m depth", "50m depth", "200m depth", "1km depth — aquifer detected!"]
    for i, layer in enumerate(layers):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(layers) * 100, layer)
        await asyncio.sleep(8)


async def _catalog_minerals(ctx):
    regions = [
        "Olympus Mons slope", "Valles Marineris floor", "Hellas Basin",
        "Utopia Planitia", "Jezero Crater", "Syrtis Major",
    ]
    for i, region in enumerate(regions):
        if not ctx.should_continue():
            return
        ctx.report_progress((i + 1) / len(regions) * 100, f"{region}: iron oxide, silicates, perchlorates")
        await asyncio.sleep(6)


async def _survey_phase(ctx):
    cb = sequential_progress(ctx, 4)
    await ctx.run_child(_map_geology, "tf-survey-geology", "Mapping Geological Structures",
                        description="Orbital and ground-based geological survey of 12 sectors.",
                        on_child_progress=cb)
    await ctx.run_child(_analyze_atmosphere, "tf-survey-atmo", "Analyzing Atmospheric Composition",
                        description="Mass spectrometry analysis of atmospheric gases.",
                        on_child_progress=cb)
    await ctx.run_child(_detect_water, "tf-survey-water", "Detecting Subsurface Water",
                        description="Ground-penetrating radar sweep to 1km depth.",
                        on_child_progress=cb)
    await ctx.run_child(_catalog_minerals, "tf-survey-minerals", "Cataloging Mineral Deposits",
                        description="Resource assessment across 6 key regions.",
                        on_child_progress=cb)


# ---------------------------------------------------------------------------
# Phase 2: Build Infrastructure (~10 min)
# ---------------------------------------------------------------------------

async def _build_habitat_domes(ctx):
    """Builds 6 domes sequentially — nested children."""
    cb = sequential_progress(ctx, 6)
    for i in range(6):
        if not ctx.should_continue():
            return

        async def _build_one_dome(dome_ctx, dome_num=i + 1):
            stages = ["Foundation", "Frame assembly", "Pressure seal", "Life support install"]
            for j, stage in enumerate(stages):
                if not dome_ctx.should_continue():
                    return
                dome_ctx.report_progress((j + 1) / len(stages) * 100, f"Dome {dome_num}: {stage}")
                await asyncio.sleep(5)

        await ctx.run_child(_build_one_dome, f"tf-dome-{i+1}", f"Habitat Dome {i+1}",
                            description=f"Constructing dome {i+1} of 6 with full life support.",
                            on_child_progress=cb)


async def _deploy_robots(ctx):
    """Deploys 10 mining robots in parallel — nested parallel group."""
    cb = average_progress(ctx)
    async with ctx.parallel_group(max_concurrency=5, on_child_progress=cb) as group:
        for i in range(10):
            async def _init_robot(r_ctx, robot_id=i + 1):
                steps = ["Unpacking", "Calibrating sensors", "Test drill", "Deploying to sector"]
                for j, step in enumerate(steps):
                    if not r_ctx.should_continue():
                        return
                    r_ctx.report_progress((j + 1) / len(steps) * 100, f"Robot #{robot_id}: {step}")
                    await asyncio.sleep(3)

            await group.spawn(_init_robot, f"tf-robot-{i+1}", f"Mining Robot #{i+1}",
                              description=f"Self-replicating mining robot #{i+1}. Deploys to assigned sector.")


async def _install_power_grid(ctx):
    cb = sequential_progress(ctx, 3)

    async def _solar_panels(s_ctx):
        for i in range(8):
            if not s_ctx.should_continue():
                return
            s_ctx.report_progress((i + 1) / 8 * 100, f"Solar array {i + 1}/8 deployed")
            await asyncio.sleep(5)

    async def _nuclear_reactor(n_ctx):
        stages = ["Excavating containment pit", "Assembling reactor core", "Loading fuel rods",
                  "Connecting coolant loops", "Running safety checks", "Reactor online!"]
        for i, stage in enumerate(stages):
            if not n_ctx.should_continue():
                return
            n_ctx.report_progress((i + 1) / len(stages) * 100, stage)
            await asyncio.sleep(6)

    async def _grid_connection(g_ctx):
        await _timed_work(g_ctx, 5, 4, [
            "Running main trunk line", "Connecting habitat domes",
            "Connecting spaceport", "Connecting mining stations", "Grid test — all green!",
        ])

    await ctx.run_child(_solar_panels, "tf-solar", "Solar Panel Arrays",
                        description="8 solar arrays providing backup power.", on_child_progress=cb)
    await ctx.run_child(_nuclear_reactor, "tf-reactor", "Nuclear Reactor",
                        description="Primary power source. 6-stage construction.", on_child_progress=cb)
    await ctx.run_child(_grid_connection, "tf-grid", "Grid Connection",
                        description="Connecting all infrastructure to the power grid.", on_child_progress=cb)


async def _comms_array(ctx):
    await _timed_work(ctx, 6, 5, [
        "Erecting antenna tower", "Installing dish array", "Calibrating deep-space link",
        "Establishing Mars-Earth relay", "Testing bandwidth", "Communications online!",
    ])


async def _build_spaceport(ctx):
    await _timed_work(ctx, 8, 4, [
        "Grading landing pad", "Pouring heat-resistant surface", "Building control tower",
        "Installing fuel depot", "Setting up cargo handling", "Painting runway markings",
        "Testing landing guidance", "Spaceport operational!",
    ])


async def _fight_aliens(ctx):
    """Always fails — exercises survive_failure."""
    ctx.report_progress(10, "Detecting alien signals...")
    await asyncio.sleep(3)
    ctx.report_progress(30, "Alien warship entering orbit!")
    await asyncio.sleep(3)
    ctx.report_progress(50, "Activating defense grid...")
    await asyncio.sleep(3)
    raise Exception("Defense grid overwhelmed! Alien invaders too powerful! (This failure is intentional — testing survive_failure)")


async def _infrastructure_phase(ctx):
    cb = average_progress(ctx)
    async with ctx.parallel_group(max_concurrency=3, survive_failure=True, on_child_progress=cb) as group:
        await group.spawn(_build_habitat_domes, "tf-infra-domes", "Constructing Habitat Domes",
                          description="6 pressurized domes with full life support. Sequential nested children.")
        await group.spawn(_deploy_robots, "tf-infra-robots", "Deploying Mining Robot Swarm",
                          description="10 robots deployed in parallel (max 5 concurrent). Nested parallel group.")
        await group.spawn(_install_power_grid, "tf-infra-power", "Installing Power Grid",
                          description="Solar + nuclear + grid connection. Sequential nested children.")
        await group.spawn(_comms_array, "tf-infra-comms", "Establishing Communications Array",
                          description="Deep-space communication link to Earth.")
        await group.spawn(_build_spaceport, "tf-infra-spaceport", "Building Spaceport",
                          description="Landing facilities and cargo handling.")
        await group.spawn(_fight_aliens, "tf-infra-aliens", "Fighting Off Alien Invaders",
                          description="This task always fails. Exercises survive_failure — parent continues despite this failure.")

    # Log which children failed
    failed = [r for r in group.results if r.state != "done"]
    if failed:
        ctx.report_progress(None, f"Infrastructure complete with {len(failed)} issue(s): {', '.join(r.process_id for r in failed)}")


# ---------------------------------------------------------------------------
# Phase 3: Terraform (~15 min)
# ---------------------------------------------------------------------------

async def _atmosphere_processing(ctx):
    """Deep nesting: atmosphere -> gas injection -> individual gases."""
    cb = sequential_progress(ctx, 3)

    async def _gas_injection(g_ctx):
        gcb = sequential_progress(g_ctx, 4)

        async def _inject_gas(ig_ctx, gas_name, duration):
            steps = int(duration / 3)
            for i in range(steps):
                if not ig_ctx.should_continue():
                    return
                ig_ctx.report_progress((i + 1) / steps * 100, f"Injecting {gas_name}: {(i + 1) * 100 // steps}% of target volume")
                await asyncio.sleep(3)

        await g_ctx.run_child(lambda c: _inject_gas(c, "O2", 30), "tf-gas-o2", "Oxygen Generation",
                              description="Electrolyzing water ice to produce O2.", on_child_progress=gcb)
        await g_ctx.run_child(lambda c: _inject_gas(c, "N2", 24), "tf-gas-n2", "Nitrogen Release",
                              description="Heating nitrate minerals to release N2.", on_child_progress=gcb)
        await g_ctx.run_child(lambda c: _inject_gas(c, "CO2 conversion", 21), "tf-gas-co2", "CO2 Conversion",
                              description="Converting excess CO2 to O2 via catalytic process.", on_child_progress=gcb)
        await g_ctx.run_child(lambda c: _inject_gas(c, "Water vapor", 18), "tf-gas-h2o", "Water Vapor Injection",
                              description="Sublimating polar ice for greenhouse effect.", on_child_progress=gcb)

    async def _pressure_monitoring(p_ctx):
        for i in range(10):
            if not p_ctx.should_continue():
                return
            pressure = 6.1 + i * 5  # millibars
            p_ctx.report_progress((i + 1) / 10 * 100, f"Atmospheric pressure: {pressure:.1f} mbar (target: 56 mbar)")
            await asyncio.sleep(6)

    async def _ozone_layer(o_ctx):
        await _timed_work(o_ctx, 8, 5, [
            "Deploying ozone generators to stratosphere", "UV catalysis initiated",
            "Ozone layer forming at 25km", "Coverage: 25%", "Coverage: 50%",
            "Coverage: 75%", "Coverage: 95%", "Ozone layer stable — UV shielding active!",
        ])

    await ctx.run_child(_gas_injection, "tf-atmo-inject", "Gas Injection Sequence",
                        description="4-gas injection pipeline. 4th-level nesting.", on_child_progress=cb)
    await ctx.run_child(_pressure_monitoring, "tf-atmo-pressure", "Atmospheric Pressure Monitoring",
                        description="Tracking pressure rise toward habitable levels.", on_child_progress=cb)
    await ctx.run_child(_ozone_layer, "tf-atmo-ozone", "Ozone Layer Formation",
                        description="UV shielding via stratospheric ozone generators.", on_child_progress=cb)


async def _temperature_regulation(ctx):
    cb_mirrors = mapped_progress(ctx, 0.0, 0.5)
    cb_thermal = mapped_progress(ctx, 0.5, 1.0)

    async def _orbital_mirrors(m_ctx):
        m_cb = average_progress(m_ctx)
        async with m_ctx.parallel_group(max_concurrency=3, on_child_progress=m_cb) as group:
            for i in range(6):
                async def _deploy_mirror(dm_ctx, mirror_id=i + 1):
                    await _timed_work(dm_ctx, 4, 5, [
                        f"Mirror {mirror_id}: Launching to orbit",
                        f"Mirror {mirror_id}: Unfolding reflective surface",
                        f"Mirror {mirror_id}: Aligning focus point",
                        f"Mirror {mirror_id}: Operational — warming sector {mirror_id}",
                    ])
                await group.spawn(_deploy_mirror, f"tf-mirror-{i+1}", f"Orbital Mirror {i+1}",
                                  description=f"Solar reflector #{i+1}. Focuses sunlight on polar regions.")

    async def _thermal_generators(t_ctx):
        t_cb = sequential_progress(t_ctx, 4)
        for i in range(4):
            async def _build_gen(bg_ctx, gen_id=i + 1):
                await _timed_work(bg_ctx, 5, 4, [
                    f"Generator {gen_id}: Drilling geothermal well",
                    f"Generator {gen_id}: Installing heat exchanger",
                    f"Generator {gen_id}: Connecting to grid",
                    f"Generator {gen_id}: Warming surface zone",
                    f"Generator {gen_id}: Target temperature reached!",
                ])
            await t_ctx.run_child(_build_gen, f"tf-therm-{i+1}", f"Thermal Generator {i+1}",
                                  description=f"Geothermal generator #{i+1}. Sequential build.",
                                  on_child_progress=t_cb)

    await ctx.run_child(_orbital_mirrors, "tf-temp-mirrors", "Deploying Orbital Mirrors",
                        description="6 mirrors in parallel (max 3 concurrent). Uses average_progress.",
                        on_child_progress=cb_mirrors)
    await ctx.run_child(_thermal_generators, "tf-temp-thermal", "Activating Thermal Generators",
                        description="4 geothermal generators built sequentially.",
                        on_child_progress=cb_thermal)


async def _ecosystem_seeding(ctx):
    """4-level deep: seeding -> microbes -> plants -> animals."""
    cb = sequential_progress(ctx, 3)

    async def _microbe_deployment(m_ctx):
        microbes = ["Cyanobacteria", "Nitrogen-fixing bacteria", "Extremophile archaea",
                    "Soil-building fungi", "Methanotrophs"]
        for i, microbe in enumerate(microbes):
            if not m_ctx.should_continue():
                return
            m_ctx.report_progress((i + 1) / len(microbes) * 100, f"Seeding {microbe} colonies")
            await asyncio.sleep(6)

    async def _plant_introduction(p_ctx):
        plants = ["Hardy lichens", "Moss varieties", "Tundra grasses",
                  "Engineered shrubs", "Pine seedlings", "Flowering plants"]
        for i, plant in enumerate(plants):
            if not p_ctx.should_continue():
                return
            p_ctx.report_progress((i + 1) / len(plants) * 100, f"Planting {plant} — survival rate: {70 + i * 5}%")
            await asyncio.sleep(5)

    async def _animal_release(a_ctx):
        animals = ["Tardigrades (soil fauna)", "Earthworms", "Pollinating insects",
                   "Small birds", "Rabbits (controlled)", "Hardy goats"]
        for i, animal in enumerate(animals):
            if not a_ctx.should_continue():
                return
            a_ctx.report_progress((i + 1) / len(animals) * 100, f"Releasing {animal} into habitat zones")
            await asyncio.sleep(7)

    await ctx.run_child(_microbe_deployment, "tf-eco-microbes", "Microbe Deployment",
                        description="Foundation organisms for soil building.", on_child_progress=cb)
    await ctx.run_child(_plant_introduction, "tf-eco-plants", "Plant Introduction",
                        description="Progressive plant varieties from hardy to flowering.", on_child_progress=cb)
    await ctx.run_child(_animal_release, "tf-eco-animals", "Animal Release",
                        description="Fauna introduction from soil organisms to mammals.", on_child_progress=cb)


async def _terraform_phase(ctx):
    cb_atmo = mapped_progress(ctx, 0.0, 0.4)
    cb_temp = mapped_progress(ctx, 0.4, 0.7)
    cb_eco = mapped_progress(ctx, 0.7, 1.0)

    await ctx.run_child(_atmosphere_processing, "tf-terra-atmo", "Atmosphere Processing",
                        description="Gas injection, pressure monitoring, ozone formation. Deepest nesting (4 levels).",
                        on_child_progress=cb_atmo)
    await ctx.run_child(_temperature_regulation, "tf-terra-temp", "Temperature Regulation",
                        description="Orbital mirrors (parallel) + thermal generators (sequential). Uses mapped_progress.",
                        on_child_progress=cb_temp)
    await ctx.run_child(_ecosystem_seeding, "tf-terra-eco", "Ecosystem Seeding",
                        description="Microbes -> plants -> animals. Progressive biosphere.",
                        on_child_progress=cb_eco)


# ---------------------------------------------------------------------------
# Top-level task
# ---------------------------------------------------------------------------

async def _terraforming_mars(ctx):
    cb = sequential_progress(ctx, 3)

    ctx.report_progress(0, "Initiating Mars terraforming sequence...")
    await ctx.run_child(_survey_phase, "tf-phase-survey", "Phase 1: Survey the Planet",
                        description="Geological, atmospheric, hydrological, and mineral surveys. ~5 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_infrastructure_phase, "tf-phase-infra", "Phase 2: Build Infrastructure",
                        description="Parallel construction (max 3 concurrent). Includes intentional failure (aliens). ~10 minutes.",
                        on_child_progress=cb)

    await ctx.run_child(_terraform_phase, "tf-phase-terraform", "Phase 3: Terraform",
                        description="Atmosphere, temperature, and ecosystem transformation. Deepest nesting. ~15 minutes.",
                        on_child_progress=cb)

    ctx.report_progress(100, "Mars terraforming complete! Welcome to New Earth.")


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=_terraforming_mars,
            process_id="terraforming-mars",
            name="Terraforming Mars",
            description=(
                "The full terraforming pipeline: survey, build, terraform. "
                "Exercises deep nesting (4 levels), all three progress helpers, "
                "cooperative cancellation, survive_failure, parallel groups with "
                "max_concurrency, and metadata inheritance. ~30 minutes."
            ),
            metadata={"planet": "mars", "mission_type": "terraforming", "priority": "critical"},
        ),
    ]
