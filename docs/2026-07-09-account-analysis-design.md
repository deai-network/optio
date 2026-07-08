# Unified account analysis across all agents — design

**Date:** 2026-07-09
**Status:** approved (design), pending implementation plan

## Problem

Two per-seed capabilities exist only in `optio-claudecode`:

1. **Account identity** — plan tier + account holder name/email, resolved from the
   seeded OAuth token (`GET /api/oauth/profile`), surfaced as the `on_seed_saved`
   2nd arg and stamped into `metadata.account`.
2. **Usage-limit status** — `usage_limited(usage, now, models)` gates whether a
   seed is currently rate/quota-blocked; drives the seed pool's `usable`/`limited`
   counts and the gimme provider's limited-retry.

The config-surface harmonization unified `TaskConfig`, `create_task`, `AgentInfo`,
and claustrum — but not the behavior behind the seed callbacks. The other six
wrappers (opencode, codex, cursor, grok, kimicode, antigravity) have a uniform
`on_seed_saved` *signature* but fill neither the account slot nor a usage gate.
Surfaced live when an antigravity seed hit "Individual quota reached. Resets in
121h" yet nothing detected it — the seed read as usable.

## Goal

A single **account-analysis** capability for all seven agents — account holder
identity, plan name, and current usage/limitation status — behind one normalized
type, with a per-engine analyzer implemented against each vendor's own endpoints.
Field set matches what claudecode produces today, plus an upcoming-reset time.

## Scope decisions (settled in brainstorming)

- **Field set = claudecode's current output + `resets_at`.** Nothing more.
- **Model-aware limit** preserved (not a flat bool): claudecode gates global
  windows *plus* `seven_day_<model>` per required model; the generalized gate
  keeps this.
- **Home:** a standalone per-engine `analyze_account`, **called from**
  `verify_and_refresh_seed`. `verify_and_refresh_seed` is the on-demand trigger;
  no separate analyze entry point.
- **Full course:** real analyzers for all seven, each researched against the
  vendor's live API and verified against a **live seed** (real-API rule — built
  and verified against real authed seeds, never assumed payload shapes). Live
  seeds are available for all seven.
- **Fail-soft end to end:** an analyzer never raises; any missing/unavailable
  data yields empty fields / not-limited and must never block seed capture or
  launch.
- **Breaking changes are acceptable**, paid for by the phase-2 excavator update
  (the main consumer).

## Architecture

### 1. Shared type + gate — new `optio_agents/account.py`

```python
@dataclass(frozen=True)
class UsageWindow:
    label: str                 # vendor window id, e.g. "five_hour", "seven_day", "seven_day_opus"
    pct: float                 # utilization 0-100
    resets_at: datetime | None # tz-aware
    model: str | None          # set for per-model windows, else None

@dataclass(frozen=True)
class AccountInfo:
    name: str | None = None
    email: str | None = None
    plan: str | None = None
    account_id: str | None = None          # vendor account uuid, when exposed
    windows: list[UsageWindow] = ()        # empty when the vendor exposes no usage
    raw: dict = {}                         # vendor payload escape hatch

    @property
    def summary(self) -> str | None:
        # "Plan: <plan> for <name> <<email>>" | "Plan: <plan> for <<email>>" | None
        # (generalized form of claudecode's format_account_summary)

    def next_reset(self) -> datetime | None:
        # soonest resets_at among currently-maxed windows; None if none maxed

    def to_dict(self) -> dict            # metadata serialization
    @classmethod
    def from_dict(cls, d) -> "AccountInfo"

EMPTY = AccountInfo()                     # the fail-soft value

def is_limited(info: AccountInfo, now: datetime, models: list[str] = ()) -> bool:
    """Generalized usage_limited. True if a relevant window is maxed
    (pct >= 100) and unreset (resets_at in the future, or absent).
    Gates every global window (model is None) always, plus each per-model
    window whose model is in `models`. `now` is injected (tz-aware).
    Returns False on EMPTY / unknown — never blocks."""
```

`next_reset()` supplies the upcoming-reset time (the one new field vs today).
`is_limited(info, now)` with no models = the flat "limited right now?" the
dashboard wants; with models = claudecode's exact model-aware behavior.

### 2. Per-engine analyzer — one per wrapper package

```python
async def analyze_account(creds) -> AccountInfo:
    """Hit this vendor's account/usage endpoints; map to AccountInfo.
    Fail-soft: never raises; any error -> account.EMPTY."""
```

- Lives in each wrapper (`optio_<engine>/account.py`); vendor endpoints differ.
- `creds` = the engine's own credential form (access token / token store) — the
  extraction is engine- and call-path-specific; the analyzer receives already-
  extracted creds so it is agnostic to *where* they came from.
- **claudecode = reference implementation:** existing `account.py`
  (`resolve_account_summary`, `format_account_summary`) + `oauth.py`
  (`summarize_profile`, `usage_limited`) refactored into `analyze_account` +
  the shared `is_limited`. Behavior preserved exactly, including model-aware
  gating and the plan-tier prettifier.

### 3. Two call paths, both per-engine, both feed the same analyzer

- **Re-verify (host-free, main path).** `verify_and_refresh_seed`, after the
  token refresh, calls `analyze_account(fresh_creds)` (reusing the just-minted
  access token — one refresh, one fetch) and stamps
  `metadata.account = info.to_dict()`.
- **Capture (live host).** The wrapper's `session.py` seed-save path extracts
  the live-workdir creds, calls the same `analyze_account`, stamps
  `metadata.account = info.to_dict()` itself, then fires
  `on_seed_saved(seed_id, info.summary)` as a **notify hook** (summary string,
  for logging/display back-compat). **optio owns the capture-time stamp** —
  consumers no longer stamp account metadata from the callback.

### 4. Uniform `verify_and_refresh_seed` return

All seven return the same shape:

```python
{"alive": bool, "account": AccountInfo | None}
```

- claudecode drops its current `{alive, usage, account}` (the `usage` raw-JSON
  key and the `account={uuid,summary}` sub-shape both go away — `account` is now
  an `AccountInfo`).
- The six bool-returners gain the `account` field.
- gimme reads `account` straight off the return (no metadata re-read); `alive`
  drives the dead/release path as today.

### 5. Dispatch — `optio-agents-all`

`analyze_account(agent_type, creds) -> AccountInfo` dispatcher, mirroring how
`create_task` dispatches by `agent_type`, for engine-agnostic callers that hold a
seed of a known type (dashboard, excavator).

## Consumers

- **Seed pool `usable`/`limited` counts** and **gimme limited-retry** call
  `is_limited(info, now, models)` (from `optio_agents.account`) instead of the
  claudecode-only `usage_limited`.
- **Dashboard** displays `summary` / plan / windows / a limited badge /
  `next_reset()`.

## Testing

- Per-analyzer unit tests over **captured real-payload fixtures** (one live-seed
  capture per vendor → fixture).
- `is_limited` table tests: maxed+unreset, maxed+reset-passed, per-model gating,
  EMPTY → False. `now` injected (no wall-clock reads in assertions).
- Fail-soft tests: network/HTTP/parse error → `EMPTY`, analyzer never raises,
  `verify_and_refresh_seed` still returns `alive=True`.
- Live-seed integration per engine: verify seed alive, run
  `verify_and_refresh_seed`, assert `metadata.account` populated.

## Error handling

Fail-soft throughout: `analyze_account` never raises (→ `EMPTY`), `is_limited`
defaults `False` on `EMPTY`/unknown, and neither seed capture nor launch is ever
blocked by an account-analysis failure.

## Phase 2 — excavator (`~/deai/excavator`) update

Breaking changes above require updating excavator, the main consumer. Three
engine-side files plus per-engine flags:

- **`packages/engine/src/engine/agents/registry.py`**
  - Import `is_limited` from `optio_agents.account` (drop
    `optio_claudecode.oauth.usage_limited`).
  - `_account_label` reads `AccountInfo.to_dict()` (`summary`/`account_id`).
  - **Remove** the `_account_metadata` / `seed_metadata` capture-time stamping
    plumbing — optio now owns the capture stamp (§3). `on_seed_saved` stays a
    notify-only hook.
  - Flip `supports_usage_gating` / `supports_accounts` to `True` per engine as
    each analyzer lands.
- **`packages/engine/src/engine/free_style/seed_provider.py`** (gimme)
  - Read `res["account"]` (an `AccountInfo`) from the new verify return; gate
    with `is_limited(account, now, models_required)` instead of
    `usage_limited(res["usage"], ...)`.
- **`packages/engine/src/engine/handlers/agent_seed_pool.py`**
  - `_limited(r)` → `is_limited(AccountInfo.from_dict(metadata["account"]), now)`.
  - `accounts` list built from `AccountInfo.to_dict()` (`account_id`/`summary`).
- **Frontend account display** (analyze trigger + i18n already generalized over
  the agent SSOT): optional polish to surface `next_reset()` / windows. Noted,
  not required for parity.

Excavator ships as part of this work (uploaded alongside), so the breaking
optio-side return/metadata changes never leave the consumer stranded.

## Plan shape

1. **Frame** (single unit): `optio_agents/account.py` (`AccountInfo`,
   `UsageWindow`, `is_limited`), the `optio-agents-all` dispatcher, claudecode
   refactored to the new seam as the reference impl, uniform verify return, and
   the capture-time stamp move.
2. **Per-engine analyzers** — fanned out one agent per engine (opencode, codex,
   cursor, grok, kimicode, antigravity), each: research vendor API against a live
   seed → capture fixture → build `analyze_account` → wire into that engine's
   `verify_and_refresh_seed` + capture path → test.
3. **Phase 2 — excavator** update + upload.
