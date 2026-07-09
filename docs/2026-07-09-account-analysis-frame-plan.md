# Account Analysis — Frame + claudecode + excavator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the unified `AccountInfo` type + `is_limited` gate + per-engine `analyze_account` seam in `optio-agents`, refactor claudecode onto it as the reference implementation, and update excavator (the main consumer) to the new verify return + metadata shape — the coupled breaking unit that must ship together.

**Architecture:** A shared normalized `AccountInfo`/`UsageWindow` type and a model-aware `is_limited(info, now, models)` gate live in `optio_agents.account`. Each wrapper owns an `async analyze_account(creds) -> AccountInfo` (fail-soft, vendor-specific). `verify_and_refresh_seed` calls it and returns a uniform `{alive, account}`; the live-capture path stamps the same `metadata.account`. Excavator reads the normalized shape and calls the shared gate.

**Tech Stack:** Python 3.11+ (async, `dataclasses`, `urllib`), pytest, motor/GridFS (MongoDB), the optio monorepo (pnpm workspace + per-package `.venv`).

## Global Constraints

- **Spec:** `docs/2026-07-09-account-analysis-design.md`.
- **Fail-soft, always:** `analyze_account` never raises → returns `account.EMPTY`; `is_limited` returns `False` on `EMPTY`/unknown. Account-analysis failure must never block seed capture or launch.
- **Field set = claudecode's current output + `next_reset()`.** No extra fields.
- **Model-aware limit preserved:** global windows always gated; `seven_day_<model>`-style per-model windows gated only for models in the caller's `models` list.
- **Injected `now`:** `is_limited` takes a tz-aware `now`; no wall-clock reads inside assertions (repo AGENTS.md test rule).
- **Real-API rule:** the claudecode analyzer's mapping is validated against a **real payload captured from a live claude seed** (Task 4 step 1), not an assumed shape.
- **Test runner (IMPORTANT — overrides every per-task command below):** there is **NO per-package `.venv`**. Use the **root venv** from the repo root: `.venv/bin/pytest packages/<pkg>/tests/<file>::<test> -q`. The root `.venv` is editable — local `src/` edits are seen immediately, no reinstall. Full suite = `make test` (`pnpm -r test`). Wherever a task step says `cd packages/X && .venv/bin/pytest tests/...`, read it as `.venv/bin/pytest packages/X/tests/...` from repo root. **Excavator** uses its own venv: `~/deai/excavator/packages/engine/.venv/bin/pytest`.
- **Commits:** no `Co-Authored-By` trailer.
- **Seed source + safety (live seeds):** the seed source is **excavator's real MongoDB** (`~/deai/excavator`; has all agents but kimi). **Never copy a seed into a throwaway db and refresh it** — a refresh rotates the single-use refresh token **upstream at the vendor**, so save-back to any db other than the real one kills the original seed. Rules for every mutating step:
  - Any `verify_and_refresh_seed` runs **against excavator's real db**, wired with excavator's real `encrypt`/`decrypt` (trace `session_blob_encrypt`/`session_blob_decrypt` / key), so a rotation immediately saves back to the real db and the seed stays alive — mirroring the production gimme path.
  - **Lease the seed first, operate, release** (the function's own contract: FREE seed or one whose lease you hold). Leasing keeps concurrent excavator work off it.
  - **Prefer freshly-verified seeds** so the stored access token is still valid and `verify_and_refresh_seed` skips the refresh (only stamps `metadata.account` → zero rotation). Refresh fires only when unavoidable, and save-back to the real db protects it.
  - **Read-only capture** uses the stored access token as-is (no refresh) — safe against the real db.

---

## File Structure

- `packages/optio-agents/src/optio_agents/account.py` — **new.** `UsageWindow`, `AccountInfo`, `EMPTY`, `is_limited`. Shared type + gate. No vendor code.
- `packages/optio-agents/src/optio_agents/__init__.py` — export the above.
- `packages/optio-agents/tests/test_account.py` — **new.** Type + `is_limited` tests.
- `packages/optio-claudecode/src/optio_claudecode/account.py` — **refactor.** Add `analyze_account(creds)`; keep the profile/plan formatting, drop the summary-only public surface where the shared type replaces it.
- `packages/optio-claudecode/src/optio_claudecode/oauth.py` — **modify.** `verify_and_refresh_seed` → `{alive, account}`; delete the module-local `usage_limited` (moved to `optio_agents.account.is_limited`); delete `summarize_profile` (folded into `analyze_account`).
- `packages/optio-claudecode/src/optio_claudecode/session.py` — **modify.** Capture path stamps `metadata.account` and passes `info.summary`.
- `packages/optio-claudecode/tests/` — analyzer + verify-return tests, with a real-payload fixture.
- `packages/optio-agents-all/src/optio_agents_all/factory.py` — **modify.** Add `analyze_account(agent_type, creds)` dispatcher + `_ANALYZE_REGISTRY`.
- `packages/optio-agents-all/src/optio_agents_all/__init__.py` — export the dispatcher.
- **excavator** (`~/deai/excavator`): `packages/engine/src/engine/agents/registry.py`, `.../free_style/seed_provider.py`, `.../handlers/agent_seed_pool.py`.

---

## Task 1: `UsageWindow` + `AccountInfo` types

**Files:**
- Create: `packages/optio-agents/src/optio_agents/account.py`
- Test: `packages/optio-agents/tests/test_account.py`

**Interfaces:**
- Produces: `UsageWindow(label:str, pct:float, resets_at:datetime|None, model:str|None)`; `AccountInfo(name, email, plan, account_id, windows:list[UsageWindow], raw:dict)` with `.summary -> str|None`, `.next_reset() -> datetime|None`, `.to_dict() -> dict`, `AccountInfo.from_dict(d) -> AccountInfo`; `EMPTY: AccountInfo`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-agents/tests/test_account.py
from datetime import datetime, timezone
from optio_agents.account import AccountInfo, UsageWindow, EMPTY


def _dt(h): return datetime(2026, 7, 9, h, 0, tzinfo=timezone.utc)


def test_summary_full():
    info = AccountInfo(name="Jane Doe", email="jane@x.com", plan="Claude Max 20x")
    assert info.summary == "Plan: Claude Max 20x for Jane Doe <jane@x.com>"


def test_summary_no_name():
    info = AccountInfo(email="jane@x.com", plan="Claude Max 20x")
    assert info.summary == "Plan: Claude Max 20x for <jane@x.com>"


def test_summary_none_when_incomplete():
    assert AccountInfo(plan="X").summary is None      # no email
    assert AccountInfo(email="jane@x.com").summary is None  # no plan
    assert EMPTY.summary is None


def test_next_reset_soonest_maxed_only():
    info = AccountInfo(windows=[
        UsageWindow("five_hour", 100.0, _dt(15), None),
        UsageWindow("seven_day", 100.0, _dt(12), None),
        UsageWindow("seven_day_opus", 40.0, _dt(9), "opus"),  # not maxed -> ignored
    ])
    assert info.next_reset() == _dt(12)


def test_next_reset_none_when_nothing_maxed():
    info = AccountInfo(windows=[UsageWindow("five_hour", 50.0, _dt(15), None)])
    assert info.next_reset() is None


def test_roundtrip_to_from_dict():
    info = AccountInfo(name="Jane", email="j@x.com", plan="P", account_id="u1",
                       windows=[UsageWindow("five_hour", 100.0, _dt(15), None)],
                       raw={"k": "v"})
    assert AccountInfo.from_dict(info.to_dict()) == info


def test_empty_roundtrips():
    assert AccountInfo.from_dict(EMPTY.to_dict()) == EMPTY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && .venv/bin/pytest tests/test_account.py -q`
Expected: FAIL — `ModuleNotFoundError: optio_agents.account`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/optio-agents/src/optio_agents/account.py
"""Normalized cross-engine account identity + usage/limit status.

One vendor-agnostic ``AccountInfo`` every engine's ``analyze_account`` returns,
plus the shared ``is_limited`` gate. No vendor code lives here — the per-engine
analyzers map their vendor payload into this shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class UsageWindow:
    label: str                  # vendor window id, e.g. "five_hour", "seven_day_opus"
    pct: float                  # utilization 0-100
    resets_at: datetime | None  # tz-aware; None if the vendor omits it
    model: str | None = None    # set for per-model windows, else None (global)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "pct": self.pct,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UsageWindow":
        ra = d.get("resets_at")
        return cls(
            label=d["label"], pct=d["pct"],
            resets_at=datetime.fromisoformat(ra) if ra else None,
            model=d.get("model"),
        )


@dataclass(frozen=True)
class AccountInfo:
    name: str | None = None
    email: str | None = None
    plan: str | None = None
    account_id: str | None = None
    windows: tuple[UsageWindow, ...] = field(default_factory=tuple)
    raw: dict = field(default_factory=dict)

    @property
    def summary(self) -> str | None:
        """``"Plan: <plan> for <name> <<email>>"`` (name optional). Requires a
        plan and an email, else None. Generalized form of claudecode's
        ``format_account_summary``."""
        if not self.plan or not self.email:
            return None
        if self.name:
            return f"Plan: {self.plan} for {self.name} <{self.email}>"
        return f"Plan: {self.plan} for <{self.email}>"

    def next_reset(self) -> datetime | None:
        """Soonest ``resets_at`` among currently-maxed (pct >= 100) windows that
        have one; None if nothing is maxed / no reset times."""
        candidates = [w.resets_at for w in self.windows if w.pct >= 100 and w.resets_at]
        return min(candidates) if candidates else None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "email": self.email, "plan": self.plan,
            "account_id": self.account_id,
            "windows": [w.to_dict() for w in self.windows],
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AccountInfo":
        return cls(
            name=d.get("name"), email=d.get("email"), plan=d.get("plan"),
            account_id=d.get("account_id"),
            windows=tuple(UsageWindow.from_dict(w) for w in d.get("windows") or ()),
            raw=d.get("raw") or {},
        )


EMPTY = AccountInfo()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && .venv/bin/pytest tests/test_account.py -q`
Expected: PASS (6 tests). Note: `windows` is a tuple; if a test constructs `windows=[...]` a list, equality still holds only after `from_dict` (which builds a tuple) — the roundtrip test compares tuple-to-tuple, so keep constructor inputs as lists only in inputs that go through `to_dict`/`from_dict`. If `test_next_reset_*` fail on list-vs-tuple, wrap the literal in `tuple(...)`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/account.py packages/optio-agents/tests/test_account.py
git commit -m "feat(optio-agents): AccountInfo + UsageWindow normalized account type"
```

---

## Task 2: `is_limited` gate

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/account.py`
- Test: `packages/optio-agents/tests/test_account.py`

**Interfaces:**
- Consumes: `AccountInfo`, `UsageWindow`, `EMPTY` (Task 1).
- Produces: `is_limited(info: AccountInfo, now: datetime, models: list[str] = ()) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# append to packages/optio-agents/tests/test_account.py
from optio_agents.account import is_limited


def test_limited_global_maxed_unreset():
    info = AccountInfo(windows=[UsageWindow("seven_day", 100.0, _dt(15), None)])
    assert is_limited(info, _dt(12)) is True          # resets in future


def test_not_limited_when_reset_passed():
    info = AccountInfo(windows=[UsageWindow("seven_day", 100.0, _dt(9), None)])
    assert is_limited(info, _dt(12)) is False          # window already reset


def test_maxed_no_reset_time_is_limited():
    info = AccountInfo(windows=[UsageWindow("seven_day", 100.0, None, None)])
    assert is_limited(info, _dt(12)) is True


def test_not_limited_below_100():
    info = AccountInfo(windows=[UsageWindow("seven_day", 99.9, _dt(15), None)])
    assert is_limited(info, _dt(12)) is False


def test_per_model_gated_only_when_requested():
    info = AccountInfo(windows=[UsageWindow("seven_day_opus", 100.0, _dt(15), "opus")])
    assert is_limited(info, _dt(12)) is False               # opus not required
    assert is_limited(info, _dt(12), ["opus"]) is True       # opus required
    assert is_limited(info, _dt(12), ["sonnet"]) is False    # different model


def test_empty_not_limited():
    assert is_limited(EMPTY, _dt(12)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && .venv/bin/pytest tests/test_account.py -q`
Expected: FAIL — `ImportError: cannot import name 'is_limited'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to packages/optio-agents/src/optio_agents/account.py
def is_limited(info: AccountInfo, now: datetime, models: list[str] = ()) -> bool:
    """True if a relevant window is maxed (pct >= 100) and not yet reset
    (``resets_at`` in the future, or absent → treated as limited).

    Gates every global window (``model is None``) always, plus each per-model
    window whose ``model`` is in ``models``. ``now`` is tz-aware. Returns False
    on ``EMPTY``/unknown — generalization of claudecode's ``usage_limited``."""
    wanted = set(models or ())
    for w in info.windows:
        if w.model is not None and w.model not in wanted:
            continue                       # per-model window for a model we don't need
        if w.pct < 100:
            continue
        if w.resets_at is None or w.resets_at > now:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && .venv/bin/pytest tests/test_account.py -q`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/account.py packages/optio-agents/tests/test_account.py
git commit -m "feat(optio-agents): model-aware is_limited gate"
```

---

## Task 3: Export from `optio_agents`

**Files:**
- Modify: `packages/optio-agents/src/optio_agents/__init__.py`

**Interfaces:**
- Produces: `from optio_agents import AccountInfo, UsageWindow, EMPTY, is_limited` (or `from optio_agents.account import ...`).

- [ ] **Step 1: Write the failing test**

```python
# append to packages/optio-agents/tests/test_account.py
def test_public_exports():
    import optio_agents
    assert optio_agents.AccountInfo is not None
    assert optio_agents.is_limited is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents && .venv/bin/pytest tests/test_account.py::test_public_exports -q`
Expected: FAIL — `AttributeError: module 'optio_agents' has no attribute 'AccountInfo'`.

- [ ] **Step 3: Write minimal implementation**

Add to `packages/optio-agents/src/optio_agents/__init__.py` (follow the file's existing import/`__all__` style):

```python
from optio_agents.account import AccountInfo, UsageWindow, EMPTY, is_limited
```
and add `"AccountInfo"`, `"UsageWindow"`, `"EMPTY"`, `"is_limited"` to `__all__` if the file defines one.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents && .venv/bin/pytest tests/test_account.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents/src/optio_agents/__init__.py packages/optio-agents/tests/test_account.py
git commit -m "feat(optio-agents): export account API"
```

---

## Task 4: claudecode `analyze_account` (reference impl)

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/account.py`
- Create: `packages/optio-claudecode/tests/fixtures/claude_profile.json`, `packages/optio-claudecode/tests/fixtures/claude_usage.json`
- Test: `packages/optio-claudecode/tests/test_analyze_account.py`

**Interfaces:**
- Consumes: `optio_agents.account.AccountInfo`, `UsageWindow`, `EMPTY`.
- Produces: `async analyze_account(access_token: str) -> AccountInfo` in `optio_claudecode.account`; keep `_format_plan`. `format_account_summary` is removed (the shared `AccountInfo.summary` replaces it).

- [x] **Step 1: Capture real payloads from a live claude seed (real-API rule) — DONE 2026-07-09**

Already captured against a live claude Max 20x seed (excavator `gm_claudecode_seeds`, decrypted with `~/excavator-trinkets/instance.age`, read-only — stored token, no refresh) and committed:
- `packages/optio-claudecode/tests/fixtures/claude_profile.json` — PII scrubbed to `Jane Doe`/`jane@example.com`/placeholder uuids; `organization.rate_limit_tier="default_claude_max_20x"` kept verbatim.
- `packages/optio-claudecode/tests/fixtures/claude_usage.json` — verbatim (no PII): top-level `five_hour`/`seven_day` `{utilization, resets_at}`, all `seven_day_<model>` = `null`, and the authoritative `limits[]` array (see the real-payload note in Step 4).

Re-capture only if the vendor shape appears to have changed again; otherwise use these fixtures.

- [ ] **Step 2: Write the failing test**

```python
# packages/optio-claudecode/tests/test_analyze_account.py
import json, pathlib, pytest
from optio_agents.account import AccountInfo, EMPTY
from optio_claudecode import account as acct

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_maps_profile_and_usage(monkeypatch):
    profile = json.loads((FIX / "claude_profile.json").read_text())
    usage = json.loads((FIX / "claude_usage.json").read_text())

    async def fake_profile(_tok): return profile
    async def fake_usage(_tok): return usage
    monkeypatch.setattr(acct, "_fetch_profile", fake_profile)
    monkeypatch.setattr(acct, "_fetch_usage", fake_usage)

    info = anyio_run(acct.analyze_account("tok"))
    assert isinstance(info, AccountInfo)
    assert info.email == profile["account"]["email"]
    assert info.plan == "Claude Max 20x"                       # from rate_limit_tier
    assert info.account_id == profile["account"]["uuid"]
    # windows come from limits[]: at least the global session/weekly windows
    labels = {w.label for w in info.windows}
    assert labels & {"session", "weekly_all"}
    # any window whose source limit had a model scope carries a model tag;
    # global limits (scope=None) have model=None
    assert any(w.model is None for w in info.windows)          # global window present
    scoped = [l for l in usage.get("limits", []) if (l.get("scope") or {}).get("model")]
    if scoped:
        assert any(w.model is not None for w in info.windows)


def test_failsoft_on_fetch_error(monkeypatch):
    async def boom(_tok): raise OSError("network")
    monkeypatch.setattr(acct, "_fetch_profile", boom)
    monkeypatch.setattr(acct, "_fetch_usage", boom)
    assert anyio_run(acct.analyze_account("tok")) == EMPTY
```

Use the repo's existing async-test convention for `anyio_run` (mirror another `optio-claudecode` async test; if the suite uses `@pytest.mark.anyio`, apply the same marker + `await` instead of `anyio_run`).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd packages/optio-claudecode && .venv/bin/pytest tests/test_analyze_account.py -q`
Expected: FAIL — `analyze_account` / `_fetch_profile` / `_fetch_usage` not defined.

- [ ] **Step 4: Write the implementation**

Refactor `account.py`: keep `_format_plan` and the `_PLAN_TOKENS` prettifier; replace the summary-string surface with `analyze_account`. Add `_fetch_profile`/`_fetch_usage` (async wrappers around the existing sync `urllib` calls, reused from `oauth.py`'s `_profile_sync`/`_usage_sync` — import them or move them here; do not duplicate the request builder). Map:

> **Real-payload note (verified 2026-07-09 against a live claude Max 20x seed — see the committed fixtures `tests/fixtures/claude_profile.json` / `claude_usage.json`):** the `/api/oauth/usage` shape has evolved. The legacy top-level `seven_day_<model>` keys are now **`null`** — per-model data lives in a new authoritative **`limits[]`** array: `{kind, group, percent, resets_at, scope:{model:{id, display_name}}|null, is_active, severity}`. The top-level `five_hour`/`seven_day` still carry `{utilization, resets_at}` and mirror the `session`/`weekly_all` limits. **Map from `limits[]`** (parsing the legacy `seven_day_<model>` keys would silently drop all model-aware gating — a regression already live in the old `usage_limited`). Global windows = `scope is None`; per-model windows = `scope.model.id` (fall back to `display_name` lowercased when `id` is null, e.g. the `weekly_scoped`/`Fable` entry).

```python
# packages/optio-claudecode/src/optio_claudecode/account.py  (key additions)
from optio_agents.account import AccountInfo, UsageWindow, EMPTY


def _parse_reset(ra) -> "datetime | None":
    if not isinstance(ra, str) or not ra:
        return None
    try:
        return datetime.fromisoformat(ra)
    except ValueError:
        return None


def _windows_from_usage(usage: dict) -> list[UsageWindow]:
    """Build windows from the authoritative ``limits[]`` array (current claude
    usage shape). Each limit → one UsageWindow; ``scope.model`` → per-model tag."""
    out = []
    for lim in (usage or {}).get("limits") or []:
        if not isinstance(lim, dict):
            continue
        pct = lim.get("percent")
        if not isinstance(pct, (int, float)):
            continue
        scope = lim.get("scope") or {}
        model_obj = scope.get("model") if isinstance(scope, dict) else None
        model = None
        if isinstance(model_obj, dict):
            model = model_obj.get("id") or (
                (model_obj.get("display_name") or "").lower() or None
            )
        out.append(UsageWindow(
            label=lim.get("kind") or lim.get("group") or "limit",
            pct=float(pct),
            resets_at=_parse_reset(lim.get("resets_at")),
            model=model,
        ))
    return out


def _info_from(profile: dict, usage: dict | None) -> AccountInfo:
    account = profile.get("account") if isinstance(profile.get("account"), dict) else {}
    return AccountInfo(
        name=account.get("full_name") or None,
        email=account.get("email") or None,
        plan=_format_plan(profile),
        account_id=account.get("uuid") or None,
        windows=tuple(_windows_from_usage(usage or {})),
        raw={"profile": profile, "usage": usage},
    )


async def analyze_account(access_token: str) -> AccountInfo:
    """Best-effort claude AccountInfo from a live OAuth access token. Never
    raises → EMPTY on any failure."""
    try:
        profile = await _fetch_profile(access_token)
        if not isinstance(profile, dict):
            return EMPTY
        usage = await _fetch_usage(access_token)
        return _info_from(profile, usage if isinstance(usage, dict) else None)
    except Exception:  # noqa: BLE001 — fail-soft
        return EMPTY
```

Add `from datetime import datetime` and the async `_fetch_profile`/`_fetch_usage` (delegating to the executor-wrapped sync fetchers). Remove `format_account_summary` and `resolve_account_summary`'s old signature per Task 6.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/optio-claudecode && .venv/bin/pytest tests/test_analyze_account.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/account.py \
        packages/optio-claudecode/tests/test_analyze_account.py \
        packages/optio-claudecode/tests/fixtures/claude_profile.json \
        packages/optio-claudecode/tests/fixtures/claude_usage.json
git commit -m "feat(optio-claudecode): analyze_account reference impl over AccountInfo"
```

---

## Task 5: claudecode `verify_and_refresh_seed` uniform return

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/oauth.py`
- Test: `packages/optio-claudecode/tests/` (extend the existing verify test module)

**Interfaces:**
- Consumes: `analyze_account` (Task 4), `optio_agents.account.AccountInfo`.
- Produces: `verify_and_refresh_seed(...) -> {"alive": bool, "account": AccountInfo | None}`; stamps `metadata.account = account.to_dict()`. Module-local `usage_limited` and `summarize_profile` **deleted**.

- [ ] **Step 1: Write the failing test**

```python
# in the existing verify test module (mirror its db/seed fixtures)
async def test_verify_returns_alive_and_account(seeded_live_claude_db):
    db, prefix, seed_id = seeded_live_claude_db
    res = await oauth.verify_and_refresh_seed(
        db, prefix=prefix, seed_id=seed_id, encrypt=None, decrypt=None)
    assert res["alive"] is True
    assert isinstance(res["account"], AccountInfo)
    doc = await seeds.load_seed(db, prefix=prefix, suffix=CLAUDE_SEED_SUFFIX, seed_id=seed_id)
    assert doc["metadata"]["account"]["email"]  # stamped normalized shape
```

**Do NOT copy the seed into an ephemeral db for this test** — `verify_and_refresh_seed` may rotate the single-use refresh token, and a save-back to anywhere but the real db kills the seed (see Global Constraints). Two safe options:
- **Preferred (no rotation):** unit-test the tail in isolation — monkeypatch `analyze_account` to return a fixed `AccountInfo`, monkeypatch the refresh path to be skipped (fresh/valid token), and assert the return shape + `declare_metadata` payload. No live vendor call, no rotation.
- **Full integration:** run against **excavator's real db** with its real `encrypt`/`decrypt`, on a **leased** freshly-verified seed (valid token → refresh skipped), then release. This writes `metadata.account` to the real doc (the feature) and does not rotate. Mirror the existing `optio-claudecode` seed-test setup for db/lease wiring.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-claudecode && .venv/bin/pytest tests/ -k verify_returns_alive -q`
Expected: FAIL — return is the old `{alive, usage, account}` dict; `res["account"]` is not an `AccountInfo`.

- [ ] **Step 3: Write the implementation**

In `verify_and_refresh_seed`, replace the `usage`/`summarize_profile` tail:

```python
# after `access` is the fresh token:
from optio_claudecode.account import analyze_account
from optio_agents.account import EMPTY

account = await analyze_account(access)
await seeds.declare_metadata(
    db, prefix=prefix, suffix=suffix, seed_id=seed_id,
    metadata={
        "account": account.to_dict(),
        "accountFetchedAt": datetime.now(timezone.utc),
        "signature": seed_signature(plain),
    },
)
return {"alive": True, "account": account}
```

Update the three early `return {"alive": False, ...}` sites to `return {"alive": False, "account": None}`. Delete the module-local `def usage_limited(...)` and `async def summarize_profile(...)` and their now-unused sync helpers (`_usage_sync`, and `_profile_sync` if fully moved to `account.py`). Remove the `from optio_claudecode.account import format_account_summary` import.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-claudecode && .venv/bin/pytest tests/ -q`
Expected: PASS. Fix any test that imported `oauth.usage_limited`/`summarize_profile` — repoint to `optio_agents.account.is_limited` / `account.analyze_account`.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/oauth.py packages/optio-claudecode/tests/
git commit -m "refactor(optio-claudecode): verify returns {alive, account:AccountInfo}; drop usage_limited/summarize_profile"
```

---

## Task 6: claudecode capture-time stamp

**Files:**
- Modify: `packages/optio-claudecode/src/optio_claudecode/session.py:915-942`
- Modify: `packages/optio-claudecode/src/optio_claudecode/account.py` (replace `resolve_account_summary(host)`)

**Interfaces:**
- Consumes: `analyze_account` (Task 4), `optio_agents.account.AccountInfo`.
- Produces: capture path stamps `metadata.account = info.to_dict()` via `seeds.declare_metadata`, then fires `on_seed_saved(seed_id, info.summary)`. New helper `resolve_capture_account(host) -> AccountInfo` in `account.py` (reads live-workdir creds → token → `analyze_account`).

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-claudecode/tests/test_capture_stamp.py
async def test_capture_stamps_account(monkeypatch, fake_host_with_live_creds, capture_db):
    # on_seed_saved captures its 2nd arg; assert metadata.account stamped + summary passed
    seen = {}
    async def cb(seed_id, info): seen["info"] = info
    ... # drive the session finally-block capture path with on_seed_saved=cb
    assert seen["info"] == expected_summary_str
    doc = await seeds.load_seed(...)
    assert doc["metadata"]["account"]["email"]
```

Mirror the existing session-capture test harness in `optio-claudecode/tests`; if none isolates the capture block, test `resolve_capture_account` + a small stamp helper directly instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-claudecode && .venv/bin/pytest tests/test_capture_stamp.py -q`
Expected: FAIL — no capture-time stamp; `resolve_capture_account` undefined.

- [ ] **Step 3: Write the implementation**

In `account.py`, replace `resolve_account_summary(host)` with:

```python
async def resolve_capture_account(host) -> AccountInfo:
    """Live-host variant: read the isolated HOME creds token, then analyze.
    Fail-soft → EMPTY."""
    path = f"{host.workdir.rstrip('/')}/{_CREDENTIALS_RELPATH}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
        token = (data.get("claudeAiOauth") or {}).get("accessToken")
    except Exception:  # noqa: BLE001
        return EMPTY
    if not isinstance(token, str) or not token:
        return EMPTY
    return await analyze_account(token)
```

In `session.py`, replace the `summary = await resolve_account_summary(host)` block:

```python
info = await resolve_capture_account(host)
await _seeds.declare_metadata(
    ctx.db, prefix=ctx.optio_prefix, suffix=CLAUDE_SEED_SUFFIX,
    seed_id=seed_id, metadata={"account": info.to_dict()},
)
await _call_maybe_async(config.on_seed_saved, seed_id, info.summary)
_trace("finally: on_seed_saved fired (summary=%s)", info.summary)
```

Confirm the correct db/prefix handles in scope at that call site (match how other `declare_metadata` calls in the package are parameterized); adjust `_seeds`/import as needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-claudecode && .venv/bin/pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-claudecode/src/optio_claudecode/session.py packages/optio-claudecode/src/optio_claudecode/account.py packages/optio-claudecode/tests/test_capture_stamp.py
git commit -m "feat(optio-claudecode): capture path stamps normalized metadata.account"
```

---

## Task 7: `optio-agents-all` analyze dispatcher

**Files:**
- Modify: `packages/optio-agents-all/src/optio_agents_all/factory.py`
- Modify: `packages/optio-agents-all/src/optio_agents_all/__init__.py`
- Test: `packages/optio-agents-all/tests/test_analyze_dispatch.py`

**Interfaces:**
- Consumes: each engine's `analyze_account` (claudecode real; the other six land in follow-up plans — until then they may be absent from the registry).
- Produces: `async analyze_account(agent_type: AgentType, creds) -> AccountInfo` dispatching by `agent_type`, mirroring `create_task`'s `_REGISTRY`.

- [ ] **Step 1: Write the failing test**

```python
# packages/optio-agents-all/tests/test_analyze_dispatch.py
import pytest
from optio_agents.account import AccountInfo
from optio_agents_all import analyze_account


async def test_dispatch_claudecode(monkeypatch):
    from optio_agents_all import factory
    async def fake(creds): return AccountInfo(email="j@x.com", plan="P")
    monkeypatch.setitem(factory._ANALYZE_REGISTRY, "claudecode", fake)
    info = await analyze_account("claudecode", "tok")
    assert info.plan == "P"


async def test_dispatch_unknown_raises():
    with pytest.raises(ValueError):
        await analyze_account("nope", "tok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/optio-agents-all && .venv/bin/pytest tests/test_analyze_dispatch.py -q`
Expected: FAIL — `ImportError: cannot import name 'analyze_account'`.

- [ ] **Step 3: Write the implementation**

```python
# packages/optio-agents-all/src/optio_agents_all/factory.py  (additions)
from optio_claudecode import analyze_account as _claudecode_analyze
# (other six added by their follow-up plans as they gain analyze_account)

_ANALYZE_REGISTRY: dict[AgentType, Callable] = {
    "claudecode": _claudecode_analyze,
}


async def analyze_account(agent_type: AgentType, creds):
    """Dispatch to the per-engine analyzer by agent_type. Raises ValueError for
    an unknown/not-yet-implemented engine."""
    fn = _ANALYZE_REGISTRY.get(agent_type)
    if fn is None:
        raise ValueError(f"no analyze_account for agent_type: {agent_type!r}")
    return await fn(creds)
```
Export `analyze_account` from `__init__.py` (mirror the `create_task` export). Confirm `optio_claudecode` re-exports `analyze_account` from its package `__init__`; add it if missing.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/optio-agents-all && .venv/bin/pytest tests/test_analyze_dispatch.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/optio-agents-all/src/optio_agents_all/factory.py packages/optio-agents-all/src/optio_agents_all/__init__.py packages/optio-agents-all/tests/test_analyze_dispatch.py
git commit -m "feat(optio-agents-all): analyze_account dispatcher by agent_type"
```

---

## Task 8: excavator — registry + gate import

**Files:**
- Modify: `~/deai/excavator/packages/engine/src/engine/agents/registry.py`
- Test: excavator's engine test suite (mirror existing registry tests)

**Interfaces:**
- Consumes: `optio_agents.account.is_limited`, `AccountInfo`; new verify return `{alive, account}`.

- [ ] **Step 1: Write the failing test**

Add a test asserting `_account_label` reads the normalized shape and that `_account_metadata`/`seed_metadata` no longer exists:

```python
def test_account_label_reads_normalized():
    from engine.agents.registry import _account_label
    md = {"account": {"summary": "Plan: X for <j@x.com>", "account_id": "u1"}}
    assert _account_label(md) == "Plan: X for <j@x.com>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/excavator/packages/engine && .venv/bin/pytest -k account_label -q`
Expected: FAIL if `_account_label` still reads `acc.get("uuid")` fallback name that no longer exists, or once `_account_metadata` removal breaks import.

- [ ] **Step 3: Write the implementation**

- Replace `from optio_claudecode.oauth import usage_limited` usages by importing `from optio_agents.account import is_limited, AccountInfo` (this file if referenced; primarily Tasks 9-10).
- `_account_label`: `return acc.get("summary") or acc.get("account_id")`.
- **Delete** `_account_metadata` and remove `seed_metadata=_account_metadata` from every `AgentDescriptor` (optio now owns the capture stamp). Remove `seed_metadata` from the dataclass if nothing else uses it (check `_model_metadata` for opencode — keep the field if opencode still needs `_model_metadata`; otherwise drop the field entirely). If the field stays for opencode, set it `None` for account-based engines.
- Leave `supports_usage_gating`/`supports_accounts` `True` for claudecode only (the six flip in their follow-up plans).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/deai/excavator/packages/engine && .venv/bin/pytest -k "registry or account" -q`
Expected: PASS.

- [ ] **Step 5: Commit** (in the excavator repo)

```bash
cd ~/deai/excavator && git add packages/engine/src/engine/agents/registry.py packages/engine/... \
  && git commit -m "refactor(engine): read normalized account metadata; drop capture-time stamp"
```

---

## Task 9: excavator — gimme provider

**Files:**
- Modify: `~/deai/excavator/packages/engine/src/engine/free_style/seed_provider.py:73-82`

**Interfaces:**
- Consumes: new verify return `{alive, account}`; `is_limited`.

- [ ] **Step 1: Write the failing test**

Mirror the existing gimme test; assert a seed whose `account` reports a maxed unreset global window is released+retried, and one that isn't is returned.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/excavator/packages/engine && .venv/bin/pytest -k gimme -q`
Expected: FAIL — code reads `res.get("usage")` / `usage_limited`, now absent.

- [ ] **Step 3: Write the implementation**

```python
from optio_agents.account import is_limited
...
account = res.get("account")
if pool.supports_usage_gating and account is not None and is_limited(
    account, datetime.now(timezone.utc), models_required,
):
    logger.warning("gimme[%s]: seed=%s LIMITED -> release + retry", pool.key, seed)
    await seeds.release(db, prefix=prefix, suffix=pool.suffix, seed_id=seed, holder=process_id)
    continue
```
Remove `from optio_claudecode.oauth import usage_limited`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/deai/excavator/packages/engine && .venv/bin/pytest -k gimme -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/deai/excavator && git add packages/engine/src/engine/free_style/seed_provider.py \
  && git commit -m "refactor(engine): gimme gates on is_limited(account)"
```

---

## Task 10: excavator — pool stats

**Files:**
- Modify: `~/deai/excavator/packages/engine/src/engine/handlers/agent_seed_pool.py:55-90`

**Interfaces:**
- Consumes: `is_limited`, `AccountInfo`; normalized `metadata.account`.

- [ ] **Step 1: Write the failing test**

Assert `handle_pool_stats` for a claude pool computes `usable`/`limited`/`accounts` from `metadata.account` (normalized), given rows with maxed/not-maxed windows.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/deai/excavator/packages/engine && .venv/bin/pytest -k pool_stats -q`
Expected: FAIL — `_limited` reads `metadata["usage"]` via `usage_limited`.

- [ ] **Step 3: Write the implementation**

```python
from optio_agents.account import is_limited, AccountInfo
...
def _limited(r):
    if not pool.supports_usage_gating:
        return False
    acc = (r["metadata"] or {}).get("account")
    return acc is not None and is_limited(AccountInfo.from_dict(acc), now)
...
# accounts list:
if pool.supports_accounts:
    accounts = {}
    for r in rows:
        acc = (r["metadata"] or {}).get("account") or {}
        aid = acc.get("account_id")
        if aid:
            accounts[aid] = acc.get("summary")
    out["accounts"] = [{"account_id": a, "summary": s} for a, s in accounts.items()]
```
Remove `from optio_claudecode.oauth import seed_signature, usage_limited` → keep `seed_signature` (still used) via `from optio_claudecode.oauth import seed_signature`; drop `usage_limited`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/deai/excavator/packages/engine && .venv/bin/pytest -k pool_stats -q`
Expected: PASS. If the frontend consumes `accounts[].uuid`, either keep the `uuid` key alias or update the frontend reader (note for the frontend polish item).

- [ ] **Step 5: Commit**

```bash
cd ~/deai/excavator && git add packages/engine/src/engine/handlers/agent_seed_pool.py \
  && git commit -m "refactor(engine): pool stats gate + accounts from normalized metadata"
```

---

## Task 11: Full-suite verification

- [ ] **Step 1: optio suite**

Run: `cd /home/csillag/deai/optio && make test` (or the affected packages: optio-agents, optio-claudecode, optio-agents-all).
Expected: green. Fix any residual importer of `optio_claudecode.oauth.usage_limited`/`summarize_profile`.

- [ ] **Step 2: excavator suite**

Run: `cd ~/deai/excavator && make test` (per its convention).
Expected: green.

- [ ] **Step 3: grep for stragglers**

Run: `rg -n "usage_limited|summarize_profile|format_account_summary|resolve_account_summary" ~/deai/optio ~/deai/excavator --glob '!**/.venv/**'`
Expected: only definitions/tests intentionally kept (none in claudecode `oauth.py`).

---

## Self-Review

- **Spec coverage:** AccountInfo/UsageWindow/next_reset (T1) ✓; is_limited model-aware (T2) ✓; export (T3) ✓; per-engine analyze_account seam + claudecode reference incl. real-payload capture (T4) ✓; uniform verify return + metadata stamp (T5) ✓; capture-time optio-owned stamp + on_seed_saved notify hook (T6) ✓; agents-all dispatcher (T7) ✓; excavator registry/gimme/pool + flag posture + dropped `_account_metadata` (T8-10) ✓; fail-soft throughout ✓; testing incl. live-seed integration (T4.1, T5.1, T11) ✓. **Gap by design:** the six non-claudecode analyzers are research-gated follow-up plans (below), each non-breaking (adds an analyzer + flips two flags).
- **Placeholders:** excavator test bodies (T8-10 step 1) are sketched against the existing engine test harness rather than fully coded — the harness (mongo fixtures, pool descriptors) isn't in the optio repo I can quote verbatim; the implementer mirrors the neighbouring registry/gimme/pool tests. Flagged, not hidden.
- **Type consistency:** `AccountInfo.from_dict`/`to_dict` symmetric; `is_limited(info, now, models)` signature identical across T2/T9/T10; `analyze_account(creds)` per-engine ↔ dispatcher `analyze_account(agent_type, creds)` distinct names-by-arity, intentional.

## Follow-up plans (research-gated, one per engine)

Each of opencode, codex, cursor, grok, kimicode, antigravity gets its own plan, written **after** a live-seed payload capture for that vendor. Per-engine template:
1. Capture the vendor's account/usage payload from a verified-live seed → fixture.
2. Build `optio_<engine>/account.py::analyze_account(creds) -> AccountInfo` (fail-soft), mapping identity/plan/windows; vendors without usage windows return identity-only (empty `windows` → never limited).
3. Wire into that engine's `verify_and_refresh_seed` (uniform `{alive, account}`) + capture path (stamp `metadata.account`).
4. Register in `optio_agents_all.factory._ANALYZE_REGISTRY`.
5. Excavator: flip `supports_accounts` (+ `supports_usage_gating` if the vendor exposes limits) for that engine.
6. Tests: analyzer unit (fixture), fail-soft, live-seed integration.
