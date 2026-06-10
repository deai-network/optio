# Protocol Variation API — `get_protocol(browser=…)` in optio-agents

> **Note (2026-06-11):** `DOMAIN_MESSAGE:` has been split into the opt-in `CLIENT_MESSAGE:` / `CALLER_MESSAGE:` keywords — see `docs/2026-06-11-optio-message-split-design.md`. `get_protocol` now also takes `client_messages` / `caller_messages`, and `Protocol.browser` became `Protocol.features` (a `ProtocolFeatures` value object).

This spec was written against the following baseline:

**Base revision:** `aa697b32ab6a1f5fd9f230529b87c7c12b46305d` on branch `main` (as of 2026-05-29T22:17:17Z)

## Summary

optio-agents owns the optio.log **keyword protocol**: the documentation given
to the agent (in AGENTS.md), the parser that turns optio.log lines into events,
and the session driver that dispatches those events. Today this protocol is
**fixed** — one static documentation string, one parser recognizing every
keyword, and the agent's browser-open handling bolted on separately per agent.

This is wrong for one keyword: **`BROWSER:`**. Agents legitimately differ in how
a browser-open attempt on the worker should be handled, and there is no real
browser on the worker:

- **claudecode** wants the interactive `/login` URL **redirected** to the
  operator's client (the agent's `xdg-open` is shimmed to emit a `BROWSER:`
  marker, which the driver surfaces via `ctx.request_browser_open`).
- **opencode** wants browser-opens **suppressed** (silent no-op stubs shadow the
  real openers) and its agent told not to attempt them.
- A future / generic agent may want to do nothing special (**ignore**) and let
  the real opener run.

Today opencode even **advertises `BROWSER:` to its agent while suppressing it** —
it embeds the same all-keywords documentation but installs suppression stubs.
That inconsistency is the concrete bug this spec removes.

This spec introduces a single optio-agents factory **`get_protocol(*, browser:
BrowserMode = "ignore")`** returning a frozen **`Protocol`** value object that
binds the three facets that vary together — **documentation**, **parser**, and
**browser-open shim behavior** — to one decision made in one place. The session
**driver** (`run_log_protocol_session`) accepts the `Protocol` and applies the
parser + installs the shims itself, so the agent's body never grows a new step.

## Motivation

While finishing seed support (see
`docs/2026-05-29-optio-claudecode-seed-design.md`), the seed-setup session needed
the interactive `/login` URL surfaced to the operator. The seed spec modeled this
as a per-task `browser_capture: bool` opt-in flag on `ClaudeCodeTaskConfig`. That
is the wrong layer:

1. **It's not optional for claudecode.** The worker has no browser; capturing the
   URL is the only sensible behavior. There is no claudecode scenario where the
   dead `xdg-open` is preferable. A per-task flag models a constant as a choice.
2. **The real variation is per-*agent*, not per-*task*.** claudecode redirects,
   opencode suppresses. That is decided by *which package*, and it covers three
   coupled concerns (docs, parser, shim) that a single bool on one config can't
   express coherently.
3. **The three concerns can drift.** Docs, parser, and shim were configured in
   three places (or not at all — the docs were never varied). Nothing tied them
   together, which is exactly how opencode ended up advertising a keyword it
   suppresses.

Centralizing the decision in `get_protocol` makes the three facets impossible to
desynchronize and opens a clean path for future per-agent protocol variation.

## Goals

- One factory `get_protocol(browser=…)` returning a `Protocol` that binds
  documentation + parser + shim behavior to a single `BrowserMode`.
- The session driver applies the parser and installs the shims; the agent body
  gains no new step (it already hands the protocol to the driver for the parser).
- Three browser modes: `ignore`, `suppress`, `redirect`, with distinct,
  well-defined effects across all three facets.
- Unify the two existing shim implementations (claudecode capture +
  opencode suppression) into one mode-switch, removing the near-duplicate.
- Fix the live inconsistency: opencode stops advertising `BROWSER:`.
- Keyword-only signature so future axes can be added without breaking callers.

## Non-goals

- Configuring the non-browser keywords (`STATUS:`/`DELIVERABLE:`/`DONE`/`ERROR`/
  `ATTENTION:`/`DOMAIN_MESSAGE:`). They are not agent-specific; both agents want
  them, always documented + always parsed. The signature is extensible if that
  ever changes.
- Per-task browser configuration. The mode is a per-agent constant chosen by the
  agent package, not a task config field.
- opencode HOME/XDG isolation (tracked in the seed spec's opencode-parity
  section). Browser shims are PATH-based and orthogonal to HOME isolation — they
  work for opencode today, isolated or not.
- Redesigning the client-directed events feature (`BROWSER:` / `ATTENTION:` /
  `DOMAIN_MESSAGE:` parsing/dispatch and `ctx.request_browser_open` /
  `need_attention` / `domain_message`). Those ship on `main` and are reused as-is.

## Architecture

### Public surface (optio-agents)

```python
BrowserMode = Literal["ignore", "suppress", "redirect"]   # default "ignore"

@dataclass(frozen=True)
class Protocol:
    documentation: str                             # keyword docs for AGENTS.md, mode-specific
    parse_log_line: Callable[[str], LogEvent]       # parser variant
    browser: BrowserMode
    async def prepare_browser_shims(self, host: "Host") -> dict[str, str] | None:
        ...

def get_protocol(*, browser: BrowserMode = "ignore") -> Protocol:
    ...
```

`get_protocol` is the single decision point. The returned `Protocol` carries the
three facets bound to the chosen mode. `prepare_browser_shims` is a method (not a
free function) so the mode it acts on cannot diverge from the mode the docs/parser
were built for.

### Mode semantics

| `browser` | shim installed under `<workdir>/bin` | `BROWSER:` in agent docs | parser emits `BrowserEvent` | trailing docs note | net effect |
|---|---|---|---|---|---|
| `ignore` | none (`prepare_browser_shims` returns `None`) | no | no | none | real opener runs on the worker; agent not told about the browser keyword |
| `suppress` | silent no-op stubs; returns `{BROWSER, PATH}` | no | no | yes (see below) | browser-opens swallowed |
| `redirect` | capture stubs emitting `BROWSER: "<url>"` to optio.log; returns `{BROWSER, PATH}` | yes | yes | none | `/login` URL → `BROWSER:` marker → `ctx.request_browser_open` |

- `BROWSER:` appears in the documentation **and** is recognized by the parser
  **only** for `redirect`. For `ignore`/`suppress`, a stray `BROWSER:` line parses
  to `UnknownLine` (inert) — an agent can't trigger a browser-open it has no shim
  for.
- `suppress` vs `ignore` differ **only** in the shim: `suppress` writes no-op
  stubs that shadow real openers (and returns the `PATH`/`BROWSER` env to activate
  them); `ignore` installs nothing.
- The `suppress` **trailing documentation paragraph** is placed *after* the
  keyword list (not mixed into it): *"In this environment, it's impossible to
  launch a browser, so don't try to run `xdg-open` or similar."*
- `STATUS:`/`DELIVERABLE:`/`DONE`/`ERROR`/`ATTENTION:`/`DOMAIN_MESSAGE:` are always
  documented and always parsed, in every mode.

### Driver integration (`run_log_protocol_session`)

The driver lives in `optio_agents/protocol/session.py` and is the generic harness
both agents plug a `body` callback into. It already (a) calls the parser in
`_tail_and_dispatch` and (b) owns `host.setup_workdir()`. It gains a `protocol`
parameter and applies both facets itself:

```python
async def run_log_protocol_session(
    host, ctx, *, body, on_deliverable=None, before_execute=None,
    after_execute=None, protocol: "Protocol | None" = None,
) -> None:
    protocol = protocol or get_protocol()        # default: ignore
    ...
    await host.setup_workdir()
    # ... create deliverables/ + empty optio.log ...
    hook_ctx.browser_launch_env = await protocol.prepare_browser_shims(host)
    ...
    # _tail_and_dispatch uses protocol.parse_log_line instead of the
    # module-global parse_log_line.
```

- `protocol` defaults to `None` → `get_protocol()` (`ignore`): no shims, no
  `BROWSER:` parsing. Note this is a **behavior change** for any caller that did
  not pass a protocol — previously the shared driver parsed `BROWSER:` for
  everyone. All real callers are migrated in this spec.
- `prepare_browser_shims` runs right after `setup_workdir` (workdir exists) and
  before the body launches its subprocess. The agent body does **not** call it.

### HookContext channel (A1)

The shim's launch env must reach the launched subprocess, whose env is assembled
**inside the agent's body** (`build_ttyd_argv` for claude, `launch_subprocess`
env for opencode). The driver constructs the `HookContext` and passes the same
instance to the body, so it is the delivery channel:

- `HookContext` (`optio_agents/context.py`) gains
  `browser_launch_env: dict[str, str] | None = None`.
- The driver sets `hook_ctx.browser_launch_env` from `prepare_browser_shims`.
- The agent body merges it at the exact spot it already assembles the launch env:
  `extra_env = {**(config.env or {}), **(hook_ctx.browser_launch_env or {})}`.

This is the one residual touch on the agent — a merge of a provided dict at the
launch site, not a decision or an install. (Considered and rejected: registering
the env on the `Host` so the launch helpers fold it in automatically — zero body
change, but adds mutable launch-env state to optio-host and touches both launch
helpers; not worth the one-line saving.)

### PATH robustness (both agents, isolated or not)

`prepare_browser_shims` returns an **absolute** `PATH=<workdir>/bin:<tail>` and an
**absolute** `BROWSER=<workdir>/bin/xdg-open`. Because both are absolute, the stub
wins regardless of:

- **HOME isolation** — PATH is independent of `$HOME`; works for claude
  (`HOME=<workdir>/home`) and opencode (real `~`, not yet isolated) alike.
- **Local vs remote** — works for `LocalHost` and `RemoteHost` (SSH).

The `<tail>` is built from the optio process's `os.environ["PATH"]` (pre-existing
behavior). Harmless: the absolute prepend + absolute `BROWSER` guarantee the stub
is hit; the tail merely preserves the rest of PATH.

## Module organization (optio-agents)

- `optio_agents/protocol/` — home of the protocol concern. Add `Protocol`,
  `BrowserMode`, and `get_protocol` here (exported from the package root next to
  the existing `parse_log_line` / `run_log_protocol_session` exports).
- `optio_agents/protocol/prompt.py` — replace the static `LOG_CHANNEL_PROMPT`
  with `build_log_channel_prompt(browser: BrowserMode) -> str` (keyword list +
  conditional `BROWSER:` entry + conditional `suppress` trailing paragraph).
  `get_protocol` calls it to fill `Protocol.documentation`.
- `optio_agents/protocol/parser.py` — provide a parser variant that recognizes
  `BROWSER:` only when requested (`redirect`). `ATTENTION:`/`DOMAIN_MESSAGE:` and
  the core keywords are always recognized.
- `optio_agents/browser_capture.py` → rename to `optio_agents/browser_shims.py`
  (cleanup) — the shim-builder owning all three behaviors. The current
  `enable(host)` folds into the `redirect` branch; the `suppress` branch is
  opencode's no-op stubs, moved here. `Protocol.prepare_browser_shims` dispatches
  to it by mode.
- `optio_agents/prompt.py` — `compose_agents_md` takes a `documentation: str`
  argument instead of embedding `LOG_CHANNEL_PROMPT`.

## Adoption

### claudecode

- `session.py`: `protocol = get_protocol(browser="redirect")`; pass
  `protocol=protocol` to `run_log_protocol_session`;
  `compose_agents_md(..., documentation=protocol.documentation)`; at launch,
  `extra_env = {**(config.env or {}), **(hook_ctx.browser_launch_env or {})}`.
- **No** `browser_capture` config field is added to `ClaudeCodeTaskConfig` (the
  seed spec's flag is dropped, not implemented).

### opencode

- `session.py`: `protocol = get_protocol(browser="suppress")`; pass to the driver;
  compose AGENTS.md with `protocol.documentation`; merge
  `hook_ctx.browser_launch_env` into its launch env.
- **Delete** opencode's hand-rolled suppression stubs + manual PATH prepend in
  `optio_opencode/host_actions.py` (currently ≈ lines 357-387). The driver now
  installs the suppress stubs; opencode's launch merges the provided env.
- opencode's agent docs lose `BROWSER:` and gain the suppress trailing note —
  fixing the advertise-but-suppress inconsistency.

### demo

- `optio_demo/tasks/client_directed.py`: `get_protocol(browser="redirect")` and
  drop the manual `browser_capture.enable(host)` call (the driver installs the
  shims now).

## Testing

**optio-agents** (`packages/optio-agents/tests/`):
- `get_protocol` per mode: `documentation` contains/omits the `BROWSER:` entry and
  the suppress trailing paragraph as specified; `browser` field matches.
- Parser variant: `BROWSER:` → `BrowserEvent` under `redirect`, → `UnknownLine`
  under `ignore`/`suppress`; `ATTENTION:`/`DOMAIN_MESSAGE:`/core keywords parse in
  every mode.
- `prepare_browser_shims` per mode: `ignore` → `None`, no files;
  `suppress` → no-op stubs + `{BROWSER, PATH}` (stub exits 0, writes nothing);
  `redirect` → capture stubs + `{BROWSER, PATH}` (stub appends `BROWSER: "<url>"`
  to optio.log, end-to-end as the existing browser_capture test asserts). Assert
  the returned `PATH`/`BROWSER` are absolute under `<workdir>/bin`.
- Driver: with a `redirect` protocol, `hook_ctx.browser_launch_env` is set after
  `setup_workdir` and a `BROWSER:` line in optio.log reaches
  `ctx.request_browser_open`; with `suppress`/default, a `BROWSER:` line does not.

**optio-claudecode / optio-opencode** (reuse existing session-test infra):
- claudecode session passes `redirect`; AGENTS.md documents `BROWSER:`; the launch
  env carries the absolute shim `PATH`/`BROWSER`.
- opencode session passes `suppress`; AGENTS.md omits `BROWSER:` and carries the
  trailing note; suppression stubs present; the deleted host_actions path no
  longer runs. Retarget the existing opencode browser-suppression test and the
  demo client-directed test.

## File structure

**optio-agents — modify/rename:**
- `src/optio_agents/protocol/` (likely `protocol/protocol.py` or in
  `protocol/__init__.py`) — `BrowserMode`, `Protocol`, `get_protocol`.
- `src/optio_agents/protocol/prompt.py` — `build_log_channel_prompt(browser)`.
- `src/optio_agents/protocol/parser.py` — `BROWSER:`-conditional parser variant.
- `src/optio_agents/protocol/session.py` — `protocol` param; use
  `protocol.parse_log_line`; call `prepare_browser_shims` after `setup_workdir`.
- `src/optio_agents/context.py` — `HookContext.browser_launch_env`.
- `src/optio_agents/prompt.py` — `compose_agents_md(documentation=…)`.
- `src/optio_agents/browser_capture.py` → `browser_shims.py` — three-mode builder.
- `src/optio_agents/__init__.py` — export `get_protocol`, `Protocol`, `BrowserMode`.

**optio-claudecode — modify:**
- `src/optio_claudecode/session.py` — adopt `get_protocol(browser="redirect")`.
- `src/optio_claudecode/prompt.py` — pass `documentation` through.

**optio-opencode — modify:**
- `src/optio_opencode/session.py` — adopt `get_protocol(browser="suppress")`.
- `src/optio_opencode/prompt.py` — pass `documentation` through.
- `src/optio_opencode/host_actions.py` — delete the hand-rolled suppression stubs.

**optio-demo — modify:**
- `src/optio_demo/tasks/client_directed.py` — `get_protocol(browser="redirect")`,
  drop the manual `enable` call.

## Relationship to the seed spec

This supersedes the `browser_capture` opt-in flag in
`docs/2026-05-29-optio-claudecode-seed-design.md` (its "Config surface and
seed_id resolution" → `browser_capture` field, and the "browser_capture opt-in"
paragraph). claudecode capture is now unconditional via
`get_protocol(browser="redirect")`; there is no per-task flag. A short addendum on
the seed spec records this. The seed engine relocation to `optio_agents/seeds.py`
(the other phase-3 delta) is independent of this spec.
