# Canonical agent metadata (name + URL)

**Date:** 2026-07-08
**Status:** Design approved, pending implementation plan

## Problem

Each agent wrapper (claudecode, opencode, codex, cursor, grok, kimicode, antigravity)
is identified internally only by its machine slug (`agent_type`). There is no
canonical, user-facing display name and no canonical URL. As a result:

- User-facing display strings are retyped ad-hoc at each call site (e.g.
  `optio-demo` hardcodes `"Claude Code demo — …"`, `"Kimi Code demo — …"`), diverging
  from the slugs and from each other.
- User-facing log/status messages hardcode engine names inconsistently
  ("launching Codex", "Downloading Kimi Code"), sometimes using short forms
  ("Grok" for Grok Build, "Kimi" for Kimi Code).
- The internal `agent_label` (log-prefix) exists on only 5 of 7 conversation
  classes and uses inconsistent short tokens (`claude`, `kimi`).
- Consumers have no way to obtain a display name or a product URL for an engine.

## Goal

Give every agent one canonical user-facing **name** and one canonical **URL**,
stored as single-source-of-truth per engine, aggregated for consumers, and used
consistently in all user-facing communication.

## Canonical data

| slug | name | URL |
|---|---|---|
| claudecode | Claude Code | https://claude.com/product/claude-code |
| opencode | OpenCode | https://opencode.ai |
| codex | Codex | https://openai.com/codex |
| cursor | Cursor CLI | https://cursor.com/cli |
| grok | Grok Build | https://x.ai/cli |
| kimicode | Kimi Code | https://www.kimi.com/coding |
| antigravity | Antigravity CLI | https://antigravity.google |

## Architecture

**SSOT per engine + agents-all aggregates.** Name and URL are intrinsic to each
engine, so they live with each engine (a consumer using one wrapper alone still
gets them). `optio-agents-all` aggregates the seven constants into one map for
consumers that want a single lookup — the map only *references* the per-engine
constants, so there is zero duplication.

### A. `AgentInfo` type — in `optio-agents` (base)

A frozen dataclass in the base package (no engine dependencies), re-exported from
`optio_agents`:

```python
@dataclass(frozen=True)
class AgentInfo:
    slug: str    # machine id, == agent_type ("claudecode")
    name: str    # canonical user-facing name ("Claude Code")
    url: str     # canonical product URL
```

Living in the base gives every wrapper and agents-all one shared definition with
no circular dependency.

### B. Per-engine constant (SSOT)

Each wrapper declares one module-level constant, exported from its package
`__init__` (optionally via a small `info.py`):

```python
# optio_claudecode
AGENT_INFO = AgentInfo(
    slug="claudecode",
    name="Claude Code",
    url="https://claude.com/product/claude-code",
)
```

`slug` must equal the existing `agent_type` Literal on that engine's config.
Repeated for all seven engines with the canonical data above.

### C. Aggregation in `optio-agents-all`

`optio-agents-all` already depends on all seven wrappers. It imports each
`AGENT_INFO` and builds:

```python
AGENTS: dict[AgentType, AgentInfo] = {
    "claudecode": _claudecode_info,
    "opencode":   _opencode_info,
    "codex":      _codex_info,
    "cursor":     _cursor_info,
    "grok":       _grok_info,
    "kimicode":   _kimicode_info,
    "antigravity": _antigravity_info,
}

def get_agent_info(agent_type: AgentType) -> AgentInfo:
    return AGENTS[agent_type]
```

Both `AGENTS` and `get_agent_info` are re-exported from `optio_agents_all`. The map
is keyed by the same slug the `AgentType` union already uses.

**Guard test:** assert `set(AGENTS.keys())` equals the set of `AgentType` values, so
a forgotten engine fails CI.

### D. Unify internal `agent_label`

The internal `agent_label` (used only in debug log prefixes) is made uniform:

- Derive each engine's label from `AGENT_INFO.slug` (not a retyped literal), so the
  label becomes the slug (`claudecode`, `kimicode`, …) instead of the current short
  tokens (`claude`, `kimi`).
- Add a label to the two conversation classes that lack one (opencode,
  antigravity), so all seven are uniform.

This changes some debug-log prefixes slightly (debug-only, not user-facing —
acceptable).

### E. User-facing message sweep

Find and fix messages that embed hardcoded engine display strings and replace them
with `AGENT_INFO.name`.

- Per wrapper, grep for **every** name-token variant of that engine, including
  short forms: `Claude`/`Claude Code`, `opencode`/`OpenCode`, `Codex`,
  `Cursor`/`Cursor CLI`, `Grok`/`Grok Build`, `Kimi`/`Kimi Code`,
  `Antigravity`/`Antigravity CLI`.
- Judge intent per hit; replace user-facing literals (status lines, progress,
  download/launch notices, etc.) with `AGENT_INFO.name`.
- **Skip:** debug-log prefixes (covered by D), code that needs the raw slug,
  comments/docstrings.

This is a discovery sweep — the exact hit-list is found during implementation. It
fans out cleanly one engine per agent.

### F. Demo call-sites

`optio-demo` becomes a consumer of the SSOT: replace the retyped per-engine strings
(`f"Claude Code demo — {name}"`, etc.) with the canonical name via `AGENT_INFO.name`
or `get_agent_info(agent_type).name`.

## Testing

- Per-engine: assert `AGENT_INFO` is present with the correct slug/name/url, and
  that `AGENT_INFO.slug` matches the engine's `agent_type`.
- agents-all: the keys-match-`AgentType` guard test.
- Existing suites (`make test`, pytest-xdist two-phase) stay green.

## Out of scope

- Auto-defaulting the task `name` in `create_*_task` from the canonical name
  (explicitly skipped — `name` stays caller-supplied).
- Any change to the `agent_type` discriminant or the union itself.
