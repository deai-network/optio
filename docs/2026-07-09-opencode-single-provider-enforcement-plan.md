# opencode Single-Provider Seed Enforcement — Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax. Each task ends with an independently testable deliverable. Execute task-by-task.

**Goal:** Guarantee every stored opencode seed carries exactly one provider, so the single default-provider verify/refresh always targets the correct credential — no meta-verify needed.

**Architecture:** opencode's `auth.json` may hold several providers, but a seed's usable identity is the provider of its configured default model (`small_model || model` in `opencode.json`). Enforce the invariant by *slimming* `auth.json` to that one provider at both points where it enters the seed blob — capture and in-session save-back — dropping the rest. Un-sliceable seeds (model & small_model on different providers, or the selected provider absent) are refused. A docs note tells operators the behavior. Slimming is opencode-specific and lives entirely in `optio-opencode`; it rewrites the live `auth.json` on the host *before* the generic seed-capture machinery reads it, so no opencode knowledge leaks into shared `optio-agents` seed code.

**Tech Stack:** Python (optio-opencode, optio-host `Host` abstraction), pytest-asyncio + LocalHost + mongo fixtures; TypeScript/i18n JSON (excavator frontend) for the docs note.

**Branches (already feature branches, not main — continue in place):**
- optio changes: `csillag/account-analysis` (builds on the unmerged meta-analyzer).
- excavator docs note: `csillag/multi-agent`.

## Global Constraints

- All host I/O via the `Host` abstraction only: `host.fetch_bytes_from_host(abs_path)`, `host.write_text(relpath, content)`, `host.run_command(...)`. Never bare `open()`.
- Provider id == `auth.json` key == the substring of a model string before the first `/` (e.g. `xai/grok-4.3` → `xai`).
- The slim helper is fail-soft on *absence* (missing/invalid/≤1-provider `auth.json` → no-op) but fail-closed on *ambiguity* (can't determine the single provider → raise `UnsliceableSeed`).
- Slimming rewrites the on-host `auth.json` only; it never touches `opencode.json` or the DB directly — the existing capture/save-back path persists the slimmed file.
- No new dependencies.

---

### Task 1: `slim_auth_to_selected_provider` helper + `UnsliceableSeed`

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/cred_watcher.py`
- Test: `packages/optio-opencode/tests/test_cred_watcher.py`

**Interfaces:**
- Produces:
  - `class UnsliceableSeed(Exception)` — raised when the seed cannot be reduced to one provider.
  - `async def slim_auth_to_selected_provider(host: Host) -> bool` — returns `True` if it rewrote `auth.json` (dropped providers), `False` if it was a no-op. Raises `UnsliceableSeed` on ambiguity.
- Consumes: existing module constants `_CRED_RELPATH = "home/.local/share/opencode/auth.json"`, `_MODEL_RELPATH = "home/.config/opencode/opencode.json"`.

- [ ] **Step 1: Write the failing tests**

Add to `test_cred_watcher.py` (reuses the existing `host` fixture — `LocalHost(taskdir=…)`). Helper to seed files on the host:

```python
import json
from optio_opencode.cred_watcher import (
    slim_auth_to_selected_provider, UnsliceableSeed,
)

async def _write(host, relpath, obj):
    await host.write_text(relpath, json.dumps(obj))

_AUTH = "home/.local/share/opencode/auth.json"
_CFG = "home/.config/opencode/opencode.json"


@pytest.mark.asyncio
async def test_slim_drops_unselected_providers(host):
    await _write(host, _AUTH, {
        "xai": {"type": "oauth", "access": "A"},
        "anthropic": {"type": "oauth", "access": "B"},
    })
    await _write(host, _CFG, {"model": "xai/grok-4.3"})
    assert await slim_auth_to_selected_provider(host) is True
    raw = await host.fetch_bytes_from_host(f"{host.workdir.rstrip('/')}/{_AUTH}")
    assert json.loads(raw) == {"xai": {"type": "oauth", "access": "A"}}


@pytest.mark.asyncio
async def test_slim_noop_when_single_provider(host):
    await _write(host, _AUTH, {"xai": {"type": "oauth", "access": "A"}})
    await _write(host, _CFG, {"model": "xai/grok-4.3"})
    assert await slim_auth_to_selected_provider(host) is False


@pytest.mark.asyncio
async def test_slim_prefers_small_model_but_refuses_provider_split(host):
    await _write(host, _AUTH, {"xai": {}, "anthropic": {}})
    await _write(host, _CFG, {"model": "xai/grok-4.3",
                              "small_model": "anthropic/claude-haiku-4-5"})
    with pytest.raises(UnsliceableSeed):
        await slim_auth_to_selected_provider(host)


@pytest.mark.asyncio
async def test_slim_refuses_when_selected_provider_absent(host):
    await _write(host, _AUTH, {"anthropic": {}, "openai": {}})
    await _write(host, _CFG, {"model": "xai/grok-4.3"})
    with pytest.raises(UnsliceableSeed):
        await slim_auth_to_selected_provider(host)


@pytest.mark.asyncio
async def test_slim_refuses_malformed_model_with_multi_provider(host):
    await _write(host, _AUTH, {"xai": {}, "anthropic": {}})
    await _write(host, _CFG, {"model": "grok-4.3"})  # no provider prefix
    with pytest.raises(UnsliceableSeed):
        await slim_auth_to_selected_provider(host)


@pytest.mark.asyncio
async def test_slim_noop_when_auth_missing(host):
    await _write(host, _CFG, {"model": "xai/grok-4.3"})
    assert await slim_auth_to_selected_provider(host) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-opencode/tests/test_cred_watcher.py -k slim -v`
Expected: FAIL — `ImportError: cannot import name 'slim_auth_to_selected_provider'`.

- [ ] **Step 3: Implement the helper**

Add to `cred_watcher.py` (after `capture_gate_ok`):

```python
class UnsliceableSeed(Exception):
    """The seed's auth.json holds several providers but cannot be safely
    reduced to the one backing the configured default model."""


def _provider_of(model: str | None) -> str | None:
    """The provider id of a `provider/model` string, or None if it carries no
    `provider/` prefix."""
    if not model or "/" not in model:
        return None
    return model.split("/", 1)[0]


async def _read_json(host: Host, relpath: str) -> dict | None:
    path = f"{host.workdir.rstrip('/')}/{relpath}"
    try:
        raw = await host.fetch_bytes_from_host(path)
        data = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


async def slim_auth_to_selected_provider(host: Host) -> bool:
    """Enforce one-provider-per-seed: prune the live auth.json to the single
    provider backing the configured default model (`small_model || model`),
    dropping the rest. Returns True if it rewrote auth.json, False on no-op
    (auth absent/invalid, or already one provider).

    Raises UnsliceableSeed when the seed cannot be reduced to one provider:
    the selected model has no `provider/` prefix, `model` and `small_model`
    resolve to different providers, or the selected provider is absent from
    auth.json. The caller decides what an un-sliceable seed means (capture
    refuses it; save-back leaves the seed untouched)."""
    auth = await _read_json(host, _CRED_RELPATH)
    if not auth:                      # missing/invalid/empty -> nothing to slim
        return False
    if len(auth) <= 1:                # already single-provider
        return False

    cfg = await _read_json(host, _MODEL_RELPATH) or {}
    selected = _provider_of(cfg.get("model"))
    if selected is None:
        raise UnsliceableSeed("no provider-qualified model in opencode.json")
    small = _provider_of(cfg.get("small_model"))
    if small is not None and small != selected:
        raise UnsliceableSeed(
            f"model provider {selected!r} != small_model provider {small!r}")
    if selected not in auth:
        raise UnsliceableSeed(
            f"selected provider {selected!r} not in auth.json {sorted(auth)}")

    dropped = sorted(k for k in auth if k != selected)
    await host.write_text(_CRED_RELPATH, json.dumps({selected: auth[selected]}))
    _LOG.info("slimmed seed auth to provider %r; dropped %s", selected, dropped)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest packages/optio-opencode/tests/test_cred_watcher.py -k slim -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/cred_watcher.py packages/optio-opencode/tests/test_cred_watcher.py
git commit -m "feat(opencode): slim_auth_to_selected_provider — one-provider-per-seed helper"
```

---

### Task 2: Enforce slim at seed capture

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/session.py` (around lines 773–807, the `on_seed_saved` capture block)
- Test: `packages/optio-opencode/tests/test_session_seed.py`

**Interfaces:**
- Consumes: `cred_watcher.slim_auth_to_selected_provider`, `cred_watcher.UnsliceableSeed` (Task 1).

- [ ] **Step 1: Write the failing tests**

In `test_session_seed.py`, mirror the existing `test_capture_stamps_metadata_accounts` scaffolding (same imports, `mongo_db` fixture, `run_opencode_session`, and the auth/opencode.json-writing session hooks), adding two cases. The session hook must write a **two-provider** `auth.json` plus an `opencode.json` whose `model` selects one provider.

```python
@pytest.mark.asyncio
async def test_capture_slims_multi_provider_auth_to_selected(mongo_db, tmp_path):
    # hook writes auth.json = {"xai": {...}, "anthropic": {...}} and
    # opencode.json model = "xai/grok-4.3" (mirror the sibling capture tests
    # for ctx/config/run_opencode_session wiring + captured `seed_id`).
    ...
    doc = await seeds.load_seed(mongo_db, prefix=..., suffix=OPENCODE_SEED_SUFFIX, seed_id=captured_id)
    auth = _extract_auth_json_from_seed(doc)   # decrypt+untar per sibling tests
    assert set(auth) == {"xai"}


@pytest.mark.asyncio
async def test_capture_skipped_when_unsliceable(mongo_db, tmp_path):
    # auth.json = {"xai": {...}, "anthropic": {...}} but opencode.json
    # model = "xai/..." and small_model = "anthropic/..." (provider split).
    saved = []
    async def _on_seed_saved(seed_id, info=None):
        saved.append(seed_id)
    # run_opencode_session with on_seed_saved=_on_seed_saved ...
    assert saved == []            # capture refused; callback never fired
```

(If a captured-seed auth extraction helper does not already exist in the test module, factor the decrypt+untar snippet used by `test_capture_synthesises_model_into_opencode_json` into a local `_extract_member(doc, member)` helper and reuse it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest packages/optio-opencode/tests/test_session_seed.py -k "slims or unsliceable" -v`
Expected: FAIL — capture currently stores both providers / still fires the callback.

- [ ] **Step 3: Implement the capture wiring**

In `session.py`, restructure the capture block so slim runs after the model config is written and before the gate/capture. Replace:

```python
                if seed_model is not None:
                    await _write_seed_model_config(host, seed_model)
                if not await cred_watcher.capture_gate_ok(host):
                    _LOG.warning(
                        "seed capture skipped: auth.json invalid/absent or no "
                        "model in opencode.json (unusable seed)",
                    )
                else:
                    seed_id_out = await _seeds.capture_seed(
```

with:

```python
                if seed_model is not None:
                    await _write_seed_model_config(host, seed_model)
                try:
                    await cred_watcher.slim_auth_to_selected_provider(host)
                    sliceable = True
                except cred_watcher.UnsliceableSeed as e:
                    _LOG.warning(
                        "seed capture skipped: cannot reduce to one provider "
                        "(%s) — configure one provider per seed", e,
                    )
                    sliceable = False
                if not sliceable or not await cred_watcher.capture_gate_ok(host):
                    if sliceable:
                        _LOG.warning(
                            "seed capture skipped: auth.json invalid/absent or "
                            "no model in opencode.json (unusable seed)",
                        )
                else:
                    seed_id_out = await _seeds.capture_seed(
```

(The remainder of the `else` block — `capture_seed`, `resolve_capture_accounts`, `declare_metadata`, `on_seed_saved` — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest packages/optio-opencode/tests/test_session_seed.py -v`
Expected: PASS (existing capture tests + the 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/session.py packages/optio-opencode/tests/test_session_seed.py
git commit -m "feat(opencode): slim auth to one provider at seed capture; refuse un-sliceable"
```

---

### Task 3: Enforce slim at in-session save-back

**Files:**
- Modify: `packages/optio-opencode/src/optio_opencode/cred_watcher.py` (`save_back_if_changed`)
- Test: `packages/optio-opencode/tests/test_session_seed_saveback.py`

**Interfaces:**
- Consumes: `slim_auth_to_selected_provider`, `UnsliceableSeed` (Task 1, same module).

- [ ] **Step 1: Write the failing test**

Mirror `test_rotation_during_session_updates_seed`. The run-2 session hook writes a `auth.json` that gains a *second* provider (plus an `opencode.json` selecting the first). After the session, the saved-back seed's `auth.json` must carry only the selected provider.

```python
@pytest.mark.asyncio
async def test_saveback_slims_added_provider(mongo_db, tmp_path):
    # run session; mid-session the hook writes auth.json with two providers,
    # opencode.json model = "xai/...". After teardown, merge seed into a fresh
    # LocalHost (as the sibling test does) and assert:
    assert set(saved_auth) == {"xai"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest packages/optio-opencode/tests/test_session_seed_saveback.py -k slims -v`
Expected: FAIL — both providers saved back.

- [ ] **Step 3: Implement**

In `save_back_if_changed`, slim before fingerprinting/refresh; an un-sliceable file is left untouched (do not save a multi-provider blob back):

```python
    try:
        await slim_auth_to_selected_provider(host)
    except UnsliceableSeed as e:
        _LOG.warning("seed %s: save-back skipped, un-sliceable auth (%s)", seed_id, e)
        return baseline
    fp = await cred_fingerprint(host)
    if fp is None or fp == baseline:
        return baseline
    ...
```

(Insert the `try/except` immediately at the top of `save_back_if_changed`, before the existing `fp = await cred_fingerprint(host)` line.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest packages/optio-opencode/tests/test_session_seed_saveback.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add packages/optio-opencode/src/optio_opencode/cred_watcher.py packages/optio-opencode/tests/test_session_seed_saveback.py
git commit -m "feat(opencode): slim auth to one provider on in-session save-back"
```

---

### Task 4: Docs note — one provider per seed (excavator)

**Files:**
- Modify: `packages/frontend/src/i18n/en.json`, `packages/frontend/src/i18n/de.json`, `packages/frontend/src/i18n/hu.json` (key `settings.agentConfig.modal.opencodeIntro`)

**Interfaces:** none (copy only).

- [ ] **Step 1: Append the warning to `opencodeIntro` in all three locales**

en.json — append to the existing string (keep the existing text, add the sentence at the end):

```
 … Then press the **Done** button below.\n\n⚠️ **One provider per seed.** Only the provider of the model you select here is kept — if you connect several, the rest are dropped when the seed is saved. Create a separate seed for each provider you want to pool.
```

de.json — append the German equivalent:

```
 … \n\n⚠️ **Ein Anbieter pro Seed.** Es wird nur der Anbieter des hier gewählten Modells behalten — verbinden Sie mehrere, werden die übrigen beim Speichern des Seeds verworfen. Legen Sie für jeden Anbieter, den Sie poolen möchten, einen eigenen Seed an.
```

hu.json — append the Hungarian equivalent:

```
 … \n\n⚠️ **Seedenként egy szolgáltató.** Csak az itt kiválasztott modell szolgáltatója marad meg — ha többet csatlakoztat, a többit a seed mentésekor eldobjuk. Minden poolba szánt szolgáltatóhoz hozzon létre külön seedet.
```

- [ ] **Step 2: Verify JSON validity**

Run: `cd packages/frontend && node -e "for (const f of ['en','de','hu']) JSON.parse(require('fs').readFileSync('src/i18n/'+f+'.json')); console.log('json ok')"`
Expected: `json ok`

- [ ] **Step 3: Commit**

```bash
git add packages/frontend/src/i18n/en.json packages/frontend/src/i18n/de.json packages/frontend/src/i18n/hu.json
git commit -m "docs(opencode): warn one-provider-per-seed in the setup dialog"
```

---

## Self-Review

- **Spec coverage:** slim helper (T1) ✓; capture enforcement + refuse (T2) ✓; save-back enforcement (T3) ✓; docs note (T4) ✓; both write points covered; un-sliceable edge refused at both.
- **Type/name consistency:** `slim_auth_to_selected_provider`, `UnsliceableSeed`, `_provider_of`, `_read_json` used identically across T1–T3; `_CRED_RELPATH`/`_MODEL_RELPATH` are the existing module constants.
- **Host abstraction:** only `fetch_bytes_from_host` / `write_text` / `run_command`. No bare `open()`.
- **No shared-code leakage:** all slim logic in `optio-opencode`; `optio-agents` seed machinery untouched.
