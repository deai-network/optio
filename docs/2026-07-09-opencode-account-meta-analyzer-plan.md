# opencode account meta-analyzer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Analyze **all** account(s) behind an opencode seed (one per configured provider) by dispatching to per-provider handlers that reuse the existing vendor analyzers — which requires generalizing the account seam from singular to a **list of accounts**.

**Architecture:** Phase 1 makes the whole account seam plural (`analyze_accounts -> list[AccountInfo]`, `metadata.accounts`, verify `{alive, accounts}`) with the 6 shipped engines emitting 1-element lists and the excavator rollup flattening across seeds. Phase 2 builds the opencode meta-analyzer (provider registry + dispatch + placeholder) reusing extracted vendor map-helpers. Phase 3 adds the new provider handlers (openrouter, deepseek, github-copilot). Phase 4 live-verifies.

**Tech Stack:** Python 3.11+ (async, dataclasses, urllib), pytest (root `.venv`, `asyncio_mode=auto`), motor/GridFS, the optio monorepo; excavator engine (py3.13 root `.venv`) + TS contracts/frontend.

## Global Constraints

- **Spec:** `docs/2026-07-09-opencode-account-meta-analyzer-design.md`.
- **Frame:** `optio_agents.account` (`AccountInfo`, `UsageWindow`, `EMPTY`, `is_limited`).
- **Test runner:** root `.venv/bin/pytest packages/<pkg>/tests/<file> -q` from repo root (NO per-package venv). Excavator: `~/deai/excavator/.venv/bin/pytest` (py3.13, optio editable-linked). Same-basename `test_account.py` across packages collides — run one package's tests at a time.
- **Fail-soft everywhere:** a provider handler never raises (→ placeholder); `analyze_accounts` returns `[]` only for empty/unreadable auth; never blocks capture/launch.
- **Roster:** anthropic, openai, xai, github-copilot, openrouter, deepseek. Every other provider → placeholder `AccountInfo` ("Unknown account · <provider>").
- **Seed safety:** any live capture/verify is read-only against a real seed's stored token (no refresh) or a leased production-path verify; never copy-and-refresh (see `docs/…seed-safety`). Excavator mongo is the seed source.
- **Excavator = separate repo/release**, updated in lockstep (breaking metadata shape). No `Co-Authored-By`. `git add` own paths only.

---

## Phase 1 — Frame: plural account seam

### Task 1: `analyze_accounts` + plural metadata helpers in `optio_agents.account`

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/account.py`
- Test: `packages/optio-agents/tests/test_account.py`

**Interfaces:**
- Consumes: `AccountInfo`, `is_limited`.
- Produces: `accounts_to_metadata(list[AccountInfo]) -> list[dict]`; `accounts_from_metadata(meta: dict) -> list[AccountInfo]` (reads `meta["accounts"]`, else `[meta["account"]]` if present — back-compat, else `[]`); `any_usable(list[AccountInfo], now, models=()) -> bool` (True if ≥1 account is not `is_limited`).

- [ ] **Step 1: Write the failing test**
```python
# append to packages/optio-agents/tests/test_account.py
from optio_agents.account import accounts_from_metadata, accounts_to_metadata, any_usable

def test_accounts_from_metadata_prefers_plural_falls_back_to_singular():
    a = AccountInfo(plan="P", email="e@x.com")
    assert accounts_from_metadata({"accounts": [a.to_dict()]}) == [a]
    assert accounts_from_metadata({"account": a.to_dict()}) == [a]   # legacy
    assert accounts_from_metadata({}) == []

def test_accounts_to_metadata_roundtrip():
    a = AccountInfo(plan="P", email="e@x.com")
    assert accounts_from_metadata({"accounts": accounts_to_metadata([a])}) == [a]

def test_any_usable(_now=None):
    from datetime import datetime, timezone
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
    maxed = AccountInfo(windows=(UsageWindow("w", 100.0, None, None),))
    ok = AccountInfo(plan="P")
    assert any_usable([maxed, ok], now) is True
    assert any_usable([maxed], now) is False
    assert any_usable([], now) is False
```

- [ ] **Step 2: Run to verify it fails**
Run: `.venv/bin/pytest packages/optio-agents/tests/test_account.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement**
```python
# packages/optio-agents/src/optio_agents/account.py (append)
def accounts_to_metadata(accounts: "list[AccountInfo]") -> list[dict]:
    return [a.to_dict() for a in accounts]

def accounts_from_metadata(meta: dict) -> "list[AccountInfo]":
    """Read the plural metadata.accounts; fall back to a legacy singular
    metadata.account (a 1-element list) so pools stamped before the plural
    migration still render until re-verified. [] when neither present."""
    if not isinstance(meta, dict):
        return []
    raw = meta.get("accounts")
    if isinstance(raw, list):
        return [AccountInfo.from_dict(d) for d in raw if isinstance(d, dict)]
    one = meta.get("account")
    return [AccountInfo.from_dict(one)] if isinstance(one, dict) else []

def any_usable(accounts, now, models=()) -> bool:
    """A seed with N accounts is usable iff at least ONE account is not limited
    for the required models (opencode: any provider account can serve)."""
    return any(not is_limited(a, now, models) for a in accounts)
```

- [ ] **Step 4: Run to verify pass** — `.venv/bin/pytest packages/optio-agents/tests/test_account.py -q` → PASS.
- [ ] **Step 5: Commit**
```bash
git add packages/optio-agents/src/optio_agents/account.py packages/optio-agents/tests/test_account.py
git commit -m "feat(optio-agents): plural account metadata helpers + any_usable"
```

### Task 2: extract reusable map-helpers from claudecode / codex / grok

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/account.py`, `.../optio-codex/…/account.py`, `.../optio-grok/…/account.py`
- Test: each package's `tests/test_account.py`

**Interfaces:**
- Produces: `optio_claudecode.account.account_from_oauth_token(access_token: str) -> AccountInfo`; `optio_codex.account.account_from_openai(access_token: str, account_id: str | None) -> AccountInfo` (identity via `GET /backend-api/me`, usage via `wham/usage` — NOT the id_token path); `optio_grok.account.account_from_xai(access_token: str) -> AccountInfo`.
- Consumes: existing `_fetch_*` + `_info_from`/`_windows_from_usage` of each.

- [ ] **Step 1: Write failing tests** (one per package, over the committed fixtures)
```python
# packages/optio-grok/tests/test_account.py (append)
async def test_account_from_xai_maps_identity(monkeypatch):
    import json, pathlib
    from optio_grok import account as acct
    fix = json.loads((pathlib.Path(__file__).parent / "fixtures/account_capture.json").read_text())
    monkeypatch.setattr(acct, "_fetch_userinfo", lambda t: fix["userinfo"])
    monkeypatch.setattr(acct, "_fetch_v1_me", lambda t: fix.get("v1_me"))
    monkeypatch.setattr(acct, "_fetch_subscriptions", lambda t: fix["subscriptions"])
    info = await acct.account_from_xai("tok")
    assert info.account_id and info.plan
```
(Analogous for codex `account_from_openai` over `codex_*` fixtures with `/backend-api/me` for name, and claudecode `account_from_oauth_token` over its profile/usage fixtures. Mirror each package's existing monkeypatch seams.)

- [ ] **Step 2: Run → FAIL** (functions undefined).
- [ ] **Step 3: Implement** — in each `account.py`, refactor so the existing `analyze_account` becomes a thin wrapper that extracts the token from its own creds then calls the new `account_from_*(token…)`; the fetch+map body moves into `account_from_*`. Codex: `account_from_openai` fetches identity from `GET /backend-api/me` (opencode has no id_token) with `account_id` as the `ChatGPT-Account-Id` header + `AccountInfo.account_id`; keep codex's own `analyze_account` using the id_token path unchanged (it still has one) OR route both through `account_from_openai` when `/backend-api/me` is preferred — keep codex's existing tests green either way.
- [ ] **Step 4: Run each package's suite → PASS.**
- [ ] **Step 5: Commit** (one commit; `git add` the three account.py + their tests)
```bash
git commit -m "refactor(claudecode,codex,grok): expose reusable account_from_* map helpers"
```

### Task 3: verify returns `{alive, accounts}`; stamp `metadata.accounts` — all 7 engines

**Files:**
- Modify: `verify.py`/`oauth.py` + `session.py` of all 7 wrappers (claudecode, codex, cursor, grok, kimicode, antigravity, opencode)
- Test: each package's verify/session tests

**Interfaces:**
- Produces: `verify_and_refresh_seed(...) -> {"alive": bool, "accounts": list[AccountInfo]}`; stamps `metadata.accounts = accounts_to_metadata(accounts)`. Single-account engines wrap their existing analyzer result: `accounts = [info] if info is not None and info != EMPTY else []`.

> This is a **wide, mechanical** change. Fan out one agent per wrapper (recursive per-file fan-out): each converts its own `verify.py` + `session.py` + tests from `{alive, account}`/`metadata.account` to `{alive, accounts}`/`metadata.accounts`, wrapping the single AccountInfo in a list. opencode's is written in Phase 2 (leave its `account` returning `[]` here, real in Task 6).

- [ ] **Step 1–5 (per engine):** RED (assert the new shape), convert verify tail + session capture stamp + tests, GREEN, commit own package by path. Message: `refactor(<engine>): verify returns {alive, accounts}; stamp metadata.accounts`.

### Task 4: `optio-agents-all` dispatcher → `analyze_accounts`

**Files:** `packages/optio-agents-all/src/optio_agents_all/factory.py`, `__init__.py`; `tests/test_analyze_dispatch.py`
**Interfaces:** Produces `async analyze_accounts(agent_type, creds) -> list[AccountInfo]`; `_ANALYZE_REGISTRY` maps each engine to its plural entry (single engines: a 1-element wrapper around `analyze_account`; opencode: its `analyze_accounts`).

- [ ] RED (import `analyze_accounts`), implement dispatcher + registry, GREEN, commit.

---

## Phase 1b — Excavator: consume plural metadata

### Task 5: pool rollup flattens `metadata.accounts`; gimme + usable gate on `any_usable`

**Files (excavator, `~/deai/excavator`):**
- Modify: `packages/engine/src/engine/handlers/agent_seed_pool.py`, `.../free_style/seed_provider.py`
- Test: mirror existing engine tests

**Interfaces:** Consumes `optio_agents.account.accounts_from_metadata`, `any_usable`, `is_limited`.

- [ ] **Step 1: RED** — pool-stats test: a claude seed stamped `metadata.accounts=[{…}]` (and a legacy `metadata.account` seed via fallback) both contribute to `accounts` rollup + usable count; a seed whose only account is limited counts as not-usable.
- [ ] **Step 3: Implement**
  - `handle_pool_stats`: build `accounts` by **flattening** `accounts_from_metadata(r["metadata"])` over all rows (dedupe by `account_id`, keep soonest `resets_at`); `_limited(r) = not any_usable(accounts_from_metadata(r["metadata"]), now)` when `supports_usage_gating`.
  - `seed_provider.py` (gimme): read `res["accounts"]`; a seed is limited iff `not any_usable(res["accounts"], now, models_required)`.
- [ ] **Step 4: GREEN** — `~/deai/excavator/.venv/bin/pytest packages/engine/tests -k "pool_stats or gimme or account" -q`.
- [ ] **Step 5: Commit** (excavator): `refactor(engine): pool stats/gimme flatten metadata.accounts (plural)`.
- No contract/codegen change: `accounts[]` output shape (`{account_id, summary, resets_at}`) is unchanged.

---

## Phase 2 — opencode meta-analyzer core

### Task 6: registry + dispatch + placeholder + 3 reuse handlers

**Files:**
- Create: `packages/optio-opencode/src/optio_opencode/account.py`, `.../providers/__init__.py`, `providers/{anthropic,openai,xai}.py`
- Test: `packages/optio-opencode/tests/test_account.py` + `tests/fixtures/opencode_auth.json`

**Interfaces:**
- Consumes: `optio_claudecode.account.account_from_oauth_token`, `optio_codex.account.account_from_openai`, `optio_grok.account.account_from_xai`, frame `AccountInfo`.
- Produces: `optio_opencode.account.analyze_accounts(auth: dict) -> list[AccountInfo]`; `resolve_capture_accounts(host) -> list[AccountInfo]` (reads live `home/.local/share/opencode/auth.json`).

- [ ] **Step 1: Write failing test** (fixture = a real read-only opencode auth.json capture: openai-oauth + xai-api + an unknown provider)
```python
async def test_analyze_accounts_dispatches_and_placeholders(monkeypatch):
    from optio_opencode import account as acct
    auth = json.loads((FIX / "opencode_auth.json").read_text())
    # monkeypatch the reused vendor helpers to fixed AccountInfos
    ...
    infos = await acct.analyze_accounts(auth)
    provs = {i.raw.get("provider") for i in infos}
    assert "openai" in provs and "xai" in provs
    unknown = [i for i in infos if i.raw.get("unanalyzed")]
    assert unknown and unknown[0].summary.startswith("Unknown account")
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement** — `analyze_accounts` iterates `auth.items()`, dispatches via `_REGISTRY[provider_id]`, else `_placeholder(provider_id, entry)`. Each `providers/<p>.py` `async def handle(entry) -> AccountInfo | None` dispatches on `entry["type"]` (oauth→`entry["access"]`(+`accountId`); api→`entry["key"]`), calls the reused vendor helper, fail-soft → None (→ placeholder). `_placeholder`:
```python
def _placeholder(provider_id, entry):
    return AccountInfo(account_id=entry.get("accountId"),
                       raw={"provider": provider_id, "unanalyzed": True})
```
Add the "Unknown account · <provider>" branch to `AccountInfo.summary`? — NO (keep frame generic): instead the placeholder sets `plan=f"Unknown account · {provider_id}"` so the existing `summary` (plan-only) renders it. Confirm that reads well in the popover.

- [ ] **Step 4: GREEN.**
- [ ] **Step 5: Commit** (optio-opencode files): `feat(optio-opencode): account meta-analyzer (per-provider dispatch + placeholder + reuse)`.

### Task 7: wire opencode verify + capture + dispatcher + excavator flag

- [ ] opencode `verify.py`: stamp `metadata.accounts` from `analyze_accounts(auth)` on alive; return `{alive, accounts}`. `session.py`: capture-path stamp. (RED→GREEN→commit.)
- [ ] `optio-agents-all`: register `"opencode": optio_opencode.analyze_accounts`.
- [ ] excavator `registry.py`: opencode `supports_accounts=True` (usage gating stays False initially — per-provider usage lands with its handlers); update `test_registry`. Commit both repos.

---

## Phase 3 — new provider handlers (research-gated, one task each)

Each: research the endpoint against a **live opencode seed** carrying that provider (read-only), write a fixture, build the handler, test.

### Task 8: openrouter (api-key)
- Endpoints (documented): `GET https://openrouter.ai/api/v1/key` → `{data:{label, usage, limit, is_free_tier, …}}`; `GET /api/v1/credits` → `{data:{total_credits, total_usage}}`. Map: plan=`is_free_tier?"Free":"Paid"`, account_id=label, one UsageWindow (usage/limit → pct; no reset). Fail-soft.
- TDD over a captured fixture; commit `feat(optio-opencode): openrouter account handler`.

### Task 9: deepseek (api-key)
- `GET https://api.deepseek.com/user/balance` → balance/credits. Map to identity-less AccountInfo (balance in `raw`; no windows unless a usage endpoint exists). TDD + fixture. Commit.

### Task 10: github-copilot (oauth) — RESEARCH FIRST
- Discover GitHub `/user` + copilot quota endpoints against a live copilot seed (the token opencode stores). Capture fixture → build handler → TDD. If no quota endpoint is reachable, ship identity-only. Commit.

### Task 11: register new handlers + per-provider usage gating
- Add openrouter/deepseek/copilot to `_REGISTRY`. If any exposes usage windows, flip excavator opencode `supports_usage_gating=True` (its `is_limited` already gates via `any_usable`). Commit.

---

## Phase 4 — verification

### Task 12: live-verify + full suites
- [ ] Live: a real opencode seed with ≥2 providers → `verify_and_refresh_seed` (leased, real-db) stamps `metadata.accounts` with each provider's account; dashboard popover lists them (analyzed + placeholder), reset times where present.
- [ ] `make test` (optio) green; `~/deai/excavator/.venv/bin/pytest packages/engine/tests` green; frontend `vitest` + `tsc` green.
- [ ] Straggler grep: no reader still expects singular `metadata.account` except the fallback in `accounts_from_metadata`.

## Self-Review

- **Spec coverage:** plural seam (T1–T5) ✓; reuse map-helpers (T2) ✓; meta-analyzer + placeholder + 3 reuse handlers (T6–T7) ✓; new handlers openrouter/deepseek/copilot (T8–T10) ✓; excavator flatten + gate (T5, T7, T11) ✓; live-verify (T12) ✓.
- **Placeholders:** the Phase-3 handler *endpoint mappings* for copilot are research-gated (flagged); openrouter/deepseek use documented endpoints. Per-engine Task-3 fan-out steps are templated (each engine mirrors the same shape) — not fabricated code, a repeated mechanical conversion.
- **Type consistency:** `analyze_accounts -> list[AccountInfo]`, `metadata.accounts: list[dict]`, verify `{alive, accounts}`, `any_usable(list, now, models)` used identically in frame (T1), dispatcher (T4), excavator (T5), gimme (T5).

## Execution note

Phase 1 Task 3 (7-engine verify conversion) and Phase 3 (per-provider handlers) are per-file fan-outs — one agent per engine/provider, package-local, commit own paths. Phase 1 Tasks 1–2, 4 and Phase 1b Task 5 are sequential (shared frame + excavator files). Excavator ships in lockstep (breaking metadata shape).
