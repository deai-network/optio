# optio-agents Package Extraction (Phase 1)

This spec was written against the following baseline:

**Base revision:** `4f2f00a3a7136bf66f728751152f29ec4dfb89d0` on branch `main` (as of 2026-05-29T13:29:40Z)

## Summary

Extract a new Python package **`optio-agents`** from `optio-host`. Today
`optio-host` mixes two concerns: **host transport** (run a process local or
remote, move files, tail a file, tunnels) and **agent coordination** (the
optio.log keyword protocol and the driver that parses and dispatches it). The
agent-coordination half is where new work keeps accumulating. This phase splits
it out cleanly and makes optio-agents the **single source of truth (SSOT)** for
the LLM-facing keyword-protocol documentation, which is currently hand-duplicated
in each agent package's prompt builder.

This is a **pure structural refactor**: the protocol driver's runtime behavior is
unchanged; the LLM-facing prompt text is consolidated (and may be lightly reworded
but stays semantically equivalent). All existing tests pass after the move.

**This is Phase 1 of a 3-phase program** (extract → build features → finish the
claudecode branch). Phases 2 and 3 are out of scope here.

## Motivation

- optio-host's own README bills it as "host abstraction **+** log/deliverables
  protocol" — the `+` is the conflation.
- Queued features (browser-open surfacing, attention, domain messages) are all
  agent-coordination concerns; adding them to optio-host deepens the mixing.
- The LLM-facing keyword documentation (`STATUS:`/`DELIVERABLE:`/`DONE`/`ERROR`)
  is hand-written per agent (e.g. `optio-opencode/.../prompt.py` `BASE_PROMPT_PRE`)
  and duplicated across agents — it can drift from the `parser.py` regexes that
  enforce it. The doc and the parser must live together. optio-agents is their
  home.

## The boundary

**optio-agents (new) — agent coordination:**
- `optio_agents/protocol/parser.py` — the keyword parser (moved verbatim).
- `optio_agents/protocol/session.py` — the coordination driver
  (`run_log_protocol_session`, deliverable fetch loop, etc.) (moved verbatim).
- `optio_agents/context.py` — `HookContext` + `HookContextProtocol` (moved): the
  handle passed to agent task hooks. It exists only because the coordination
  driver creates it; it belongs with the coordination layer.
- `optio_agents/protocol/prompt.py` (new) — the **SSOT** for the LLM-facing
  keyword-protocol text, co-located with the parser regexes it documents.

**optio-host — host transport (unchanged role):**
- `host.py` (`Host`/`LocalHost`/`RemoteHost`, including `tail_file`,
  `run_command`, file transfer, tunnels), `archive.py`, `download.py`,
  `paths.py`, `types.py`.
- `RunResult` and `HostCommandError` (transport result/error types returned by
  `Host.run_command`) **stay in optio-host, relocated into `host.py`** — their
  natural home (what `run_command` produces). This also fixes the current
  inversion where `host.py:21` imports `RunResult` back from `context.py`.
  `optio_host/__init__.py` already re-exports both, so `from optio_host import
  RunResult` users are unaffected; the direct `from optio_host.context import
  RunResult` sites (opencode tests `test_session_hooks.py:100`,
  `test_smart_install.py:5`) repoint to `optio_host.host`. `HookContext` (now in
  optio-agents) imports them from optio-host. After this, `context.py` has no
  remaining residents and is deleted.

**Dependency direction (no cycle):** `optio-agents → {optio-host, optio-core}`.
parser.py is pure stdlib; session.py uses `optio_host.host.Host` and
`optio_core.context.ProcessContext` (TYPE_CHECKING only).

## Symbols moved to optio-agents

From `protocol/parser.py`: `parse_log_line`, `StatusEvent`, `DeliverableEvent`,
`DoneEvent`, `ErrorEvent`, `UnknownLine`, `LogEvent`, `validate_deliverable_path`,
`relativize_deliverable_path`, `DELIVERABLES_SUBDIR`.

From `protocol/session.py`: `run_log_protocol_session`, `DeliverableCallback`,
`HookCallback`, `fetch_deliverable_text`, `DELIVERABLE_QUEUE_BOUND`,
`_SessionFailed`.

From `context.py`: `HookContext`, `HookContextProtocol`.

The package keeps the same internal layout (`optio_agents/protocol/__init__.py`
re-exports the protocol surface; `optio_agents/__init__.py` exports `HookContext`,
`HookContextProtocol`, and the protocol surface as appropriate).

## Call sites to repoint

| File | Change |
|---|---|
| `optio-opencode/src/optio_opencode/types.py:13` | `DeliverableCallback, HookCallback` ← `optio_agents.protocol.session` |
| `optio-opencode/src/optio_opencode/session.py:32` | `HookContext` ← `optio_agents` (was `optio_host.context`) |
| `optio-opencode/src/optio_opencode/session.py:35` | `_SessionFailed, run_log_protocol_session` ← `optio_agents.protocol.session` |
| `optio-opencode/src/optio_opencode/__init__.py:6-7,33-34` | re-export `HookContext, HookContextProtocol` from `optio_agents` |
| `optio-host/src/optio_host/__init__.py:9-10` | drop `HookContext, HookContextProtocol` from imports + `__all__` (keep `RunResult`, `HostCommandError`) |
| `optio-opencode/pyproject.toml` | add `optio-agents>=0.1,<0.2` to `dependencies` |
| `Makefile:4` | `PY_PACKAGES := optio-core optio-host optio-agents optio-opencode` (optio-agents after optio-host, before optio-opencode) |

## Tests to move / repoint

- Move `optio-host/tests/test_protocol_parser.py` → `optio-agents/tests/` (repoint imports to `optio_agents.protocol.parser`).
- Move `optio-host/tests/test_context.py` (the `HookContext` test) → `optio-agents/tests/` (repoint to `optio_agents`).
- **Split `optio-host/tests/test_download.py`** (it has two groups): the `HookContext.download_file` routing tests (construct `HookContext`, assert path resolution / `run_child` spawn / escape rejection / `HookContextProtocol` surface — L86–174) move to `optio-agents/tests` (they test a `HookContext` method); the `DownloadFailed` / `create_download_task` factory tests (L4–57) stay in `optio-host/tests/test_download.py` (they test `download.py`). `HookContext.download_file` still calls `optio_host.download.create_download_task` — a clean optio-agents→optio-host dep.
- `optio-opencode/tests/test_host_local.py:139,149` and `test_session_hooks.py:258-259` — repoint `fetch_deliverable_text`, `_deliverable_fetch_loop`, `HookContext` to `optio_agents`.

## SSOT prompt consolidation

- Define the canonical LLM-facing keyword block in `optio_agents/protocol/prompt.py`
  (the "## Log channel" section documenting the **existing** keywords:
  `STATUS:`/`DELIVERABLE:`/`DONE`/`ERROR`, plus the trailing-newline requirement
  and deliverables convention). Expose it as a constant or a small composer
  function.
- `optio-opencode/.../prompt.py` `BASE_PROMPT_PRE` is rewritten to **compose** the
  optio-agents SSOT block, keeping opencode-specific framing (naming, task
  section) around it. The agent's emitted protocol section stays semantically
  equivalent.
- **Phase 2** adds the `BROWSER:`/`ATTENTION:`/`DOMAIN_MESSAGE:` keywords to this
  one block — not in this phase.

## Packaging

- New `packages/optio-agents/` with `pyproject.toml`:
  `dependencies = ["optio-core>=0.1,<0.2", "optio-host>=0.1,<0.2"]`, `[dev]`
  extras mirroring optio-host (pytest, pytest-asyncio), `[tool.setuptools.packages.find] where = ["src"]`,
  `asyncio_mode = "auto"`, `testpaths = ["tests"]`. Mirror optio-host's
  `pyproject.toml` structure.
- Add `optio-agents` to `Makefile` `PY_PACKAGES` between optio-host and
  optio-opencode so the editable-install loop installs it in dependency order.
- READMEs: optio-host README drops the "+ log/deliverables protocol" framing and
  points to optio-agents; add an optio-agents README describing the coordination
  protocol + the keyword SSOT.

## Testing / acceptance

Behavior-unchanged refactor. Acceptance = the full suite is green after the move,
from a clean editable reinstall (the package set changed):

- `make install` (picks up the new package), then per-package pytest:
  - `optio-agents` tests (moved parser + HookContext tests) pass.
  - `optio-host` tests pass (minus the moved files).
  - `optio-opencode` tests pass against the repointed imports.
- No import references to `optio_host.protocol` or `optio_host.context.HookContext`
  remain anywhere (grep clean).
- opencode's composed AGENTS.md still contains the keyword protocol section
  (now sourced from the SSOT).

## Out of scope (later phases)

- **Phase 2** — browser-open surfacing, attention (`need_attention`), domain
  messages (`domain_message`); the three new agent-emittable keywords
  (`BROWSER:`/`ATTENTION:`/`DOMAIN_MESSAGE:`) added to the SSOT; the required
  `sessionId` launch parameter; the session-scoped events SSE; optio-ui handlers.
  (Draft: `docs/2026-05-29-browser-open-surfacing-design.md`, to be retargeted to
  optio-agents.)
- **Phase 3** — finishing the parked claudecode branch on optio-agents.
- Any behavioral change to the protocol itself.
