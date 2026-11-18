# Deliverable metadata push (design)

2026-11-18. Owner-approved design for optio (`optio-agents` and the agent wrappers) and its
consumer excavator. Branches: optio `csillag/deliverable-metadata` (on
`csillag/claudecode-gateway`), excavator `csillag/deliverable-metadata` (on
`csillag/claude-code-requesty`). Research with file:line evidence:
`/home/csillag/requesty-work/notes/2026-11-18-deliverable-metadata-push.md`.

## Why

Excavator names a recipe's author from the `generator` header the agent writes into the recipe
(`free_style/hooks.py:526-529`; asked for by `free_style_extraction.md:566-568`). In the live test a
Kimi model behind Claude Code + Requesty wrote `generator: claude-fable-5`, although optio had
launched it with `--model lyceum/kimi-k3.1-pro` and Claude Code told it so. A model's account of
its own identity is unreliable on any provider. Optio already knows the real values.

## The owner's direction and decisions

- Owner: "instead of pulling, make it a push: all deliverable callbacks should get an array of
  metadata together with them, including model settings".
- 1. The recipe's analyzer name shows the human-readable model label (e.g. "Kimi K3.1 Pro"), from
  the pushed metadata; the exact ids stay in the stored metadata.
- 2. The `generator` header is dropped from the prompt and from the recipe enable check; an old or
  restored file that still has it is parsed as before and the value is ignored.
- 3. A re-delivery whose body is unchanged keeps its existing credit.
- 4. Exception to 3, for the recipes that carry wrong self-reported credit today: a recipe version
  whose credit has no pushed metadata (legacy, self-reported) is re-stamped by any delivery of it,
  even when the body is unchanged, so re-delivering everything once fixes the attribution. From
  then on 3 applies. Consequence the owner accepted with the proposal: a legacy recipe delivered
  unchanged by a different model is credited to that model (the original author cannot be
  recovered reliably).
- 5. The metadata is an optional fourth callback argument; three-argument callbacks keep working.
- 6. Every deliverable kind records it: recipes, entity descriptors, source context, bug reports,
  and the insights `after_execute` reads from `data-source.md`.
- 7. Claude Code first (native and Requesty); the shared driver gives every wrapper the base
  entries at once; the other wrappers' model entries follow later.
- 8. Built on new branches stacked on the two Requesty branches, tested in the same live stack.

## Design

### Shape

A list of entries, each `{key: str, value: str | int | float | bool | None, source: str}`.
`source` is one of `config`, `optio`, `agent`, `seed`, `harness`. The same fact may appear from
several sources (the model optio set and the model the CLI reports): consumers pick with
`meta_get(md, key, prefer=(...))`; the key names are constants defined once in `optio_agents`;
consumers never spell them. Clamped: at most 32 entries, each value at most 256 characters,
scalars only.

### Keys (a wrapper adds only what it knows)

`origin` (`agent` / `harness`), `agent.type`, `agent.name`, `agent.version`, `agent.session`,
`provider`, `gateway.region`, `model` (source `optio`: what optio launched or switched to; source
`agent`: what the CLI reports, from `system/init` or the assistant message), `model.label`,
`effort`, `delivered_at` (ISO-8601 UTC), `resumed`.

Never included: the gateway token, `base_url`, the key hint, any environment or `--settings`
content, account or personal details (codex account, OAuth seed info).

### Where optio assembles it

- `optio_agents/deliverable_meta.py`: the entry type, the key constants, `meta_get`, `clamp`, and
  `agent_entries(config, info)` (the common entries every wrapper adds).
- The shared driver `run_log_protocol_session` takes an optional provider callable. It snapshots
  the metadata when it accepts a `DELIVERABLE:` line (not when the callback runs: the agent ends
  its turn right after the line and a live switch can land in between), adds `origin`,
  `delivered_at`, `resumed`, clamps, and queues it with the deliverable. A provider failure never
  fails a delivery: the driver's own entries are sent and the failure is logged.
- One invocation helper calls a consumer callback with the metadata when the callback takes a
  fourth positional parameter, a parameter named `metadata`, or `*args`/`**kwargs`, else with
  three arguments. The claustrum notice (`claustrum.py`) uses it with `origin: harness`.
- optio-claudecode: keeps `system/init`'s `session_id`, `claude_code_version` and model, the last
  assistant `message.model`, and a per-turn model that a switch does not clear; its provider
  reports these plus `conversation.model`, `effort`, the catalog label, and under a gateway the
  provider and region from the seed. Iframe mode reports what it knows (`config.model`).
- Other wrappers in this round: the base entries only (agent type and name, origin, delivered_at,
  resumed); their model entries are a later step.

### How excavator uses it

- `make_on_deliverable`'s callback takes `metadata=None`.
- The recipe's `createdBy.analyzer.name` is "Free-style <agent label> analyzer with <model label>"
  from the metadata (label preferred, else the model id; `agent` source preferred over `optio`);
  without a model entry it is "Free-style <agent label> analyzer".
- The whole list is stored as `createdBy.delivery` on recipes (added to `RecipeCreatedBySchema`) and
  as an equivalent author record on entity descriptors, source context, bug reports and insights.
  Insights come from `after_execute`, which records the metadata of the session's last delivery.
- Decision 4: when `save_recipe` finds the body unchanged and the existing version has no
  `createdBy.delivery`, it updates that version's `createdBy` in place (no new version).
- The prompt's `generator` lines and the enable check's `generator` requirement are removed.

### To measure during the work

What Requesty returns as the model in `system/init.model` and in `message.model` (the routed id or
the upstream's own name): measure once with the real CLI through Requesty before choosing which
value names the recipe.

### Out of scope

Exact time-of-writing attribution (a file written in an earlier turn than its announcement), the
other five wrappers' model entries, an optio release (only after the owner's clearance,
merge-before-release).
