# optio-opencode Multi-User Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring optio-opencode to claudecode parity — binary cache (no host-`~`), HOME/XDG isolation, seeding via the generic `optio_agents.seeds` engine, and auto-start via the opencode API.

**Architecture:** This is a **parity feature**: claudecode already implements the same patterns and is the reference. Each task pairs opencode-specific load-bearing code with the exact claudecode file to mirror; executing agents read both the claudecode reference and the opencode target. The binary cache mirrors claudecode's runtime cache (simpler — single binary, no symlink/autoupdate); seeding reuses the unchanged generic engine with an opencode manifest; isolation sets `HOME`+`XDG_*` in the launch/export/import env; auto-start POSTs the kickoff to the pre-created session.

**Tech Stack:** Python 3.11+, optio-host `Host`, opencode CLI (smart-install fork) + HTTP API, motor+GridFS, pytest + MongoDB-via-Docker, the opencode `fake_opencode.py` test harness.

**Source spec:** `docs/2026-05-31-optio-opencode-parity-design.md` (read its "Experiments / open questions" — the login-browser behavior is **deferred to a live experiment, not built here**; seed-setup ships browser `suppress`).

---

## ⚠️ Execution model: PARALLEL-SHAPED, verification deferred

Standing preference: Phases 1–3 run **no tests, no git** (workflow agents make file changes only); all pytest + grep guards happen in Phase 4; the maintainer commits. Tasks within a phase touch disjoint files. Because this mirrors claudecode, each task names the **claudecode reference file** to read and adapt — agents implement by mirroring the proven pattern into the opencode target, using the opencode-specific code given here as the authority for names/signatures.

**Not in this build (experiment, manual/live):** the opencode login-browser behavior (§Experiments). Seed-setup uses `suppress`. Do not add redirect/OAuth handling.

---

## Shared contracts (frozen — all tasks conform)

```python
# optio_opencode/host_actions.py
# Cache dir resolved on the WORKER (real env), BEFORE isolation. opencode_install_dir overrides.
_OPENCODE_CACHE_SHELL_DEFAULT = (
    '${OPENCODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin}'
)
async def _resolve_install_dir(host, install_dir): ...   # override → it; else echo the default on the host

# Per-task XDG/HOME isolation env, derived from host.workdir. Merged into the
# launch env AND the export/import env so opencode's auth/config/data go per-task.
def _isolation_env(host) -> dict[str, str]:
    home = f"{host.workdir.rstrip('/')}/home"
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
    }

# optio_opencode/seed_manifest.py  (mirror optio_claudecode/seed_manifest.py)
OPENCODE_SEED_SUFFIX = "_opencode_seeds"
OPENCODE_SEED_MANIFEST = seeds.SeedManifest(
    home_subdir="home",
    include=[
        ".local/share/opencode/auth.json",
        ".config/opencode/opencode.json",
        ".config/opencode/plugins",
    ],
    version=1,
    consume_transform=None,   # no cwd-rekey for opencode
)
async def delete_seed(db, prefix, seed_id): ...   # binds suffix → seeds.delete_seed
async def list_seeds(db, prefix): ...
async def purge_seed(db, prefix, seed_id): ...    # binds suffix → seeds.purge_seed

# optio_opencode/types.py — OpencodeTaskConfig new fields
seed_id: str | None = None
on_seed_saved: "Callable[[str], Awaitable[None] | None] | None" = None
auto_start: bool = False
# opencode_install_dir documented as the binary-cache override.

# auto-start: POST the kickoff to the pre-created session (fresh only).
AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"
# endpoint: POST http://127.0.0.1:<worker_port>/api/session/<session_id>/prompt
# auth: Basic base64("opencode:<password>"); body shape confirmed by the Task-7 spike.
```

---

## File structure

- `optio_opencode/host_actions.py` — cache resolution; `_isolation_env`; `launch_opencode` + `opencode_export` + `opencode_import` merge the isolation env.
- `optio_opencode/seed_manifest.py` — NEW (mirror claudecode).
- `optio_opencode/types.py` — `seed_id`/`on_seed_saved`/`auto_start`; cache-override doc.
- `optio_opencode/session.py` — seed merge (fresh)/capture (finally); auto-start POST; uses host_actions isolation.
- `optio_opencode/__init__.py` — export seed surface.
- `optio-demo/.../tasks/opencode.py` — setup-seed task + seeded tasks (mirror `tasks/claudecode.py`).
- tests under `packages/optio-opencode/tests/`.

---

# Phase 1 — implementation (parallel; disjoint files)

## Task 1: host_actions — binary cache + XDG isolation env

**Reference:** `optio_claudecode/host_actions.py` `_resolve_cache_dir` (cache pattern).
**File:** `packages/optio-opencode/src/optio_opencode/host_actions.py`

- [ ] **Step 1 — retarget the install dir to a worker-resolved cache.** Replace `_resolve_install_dir` (currently `install_dir or <host_home>/.local/bin`) so the default is the cache:

```python
_OPENCODE_CACHE_SHELL_DEFAULT = (
    '${OPENCODE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin}'
)

async def _resolve_install_dir(host: "Host", install_dir: str | None) -> str:
    """Resolve the opencode binary-cache dir as an absolute path on the worker.

    ``install_dir`` (config.opencode_install_dir) overrides. Else the worker's
    OPENCODE_CACHE_DIR / XDG_CACHE_HOME / $HOME decide it — resolved via a shell
    echo so RemoteHost gets the remote cache. Resolved from the worker's REAL env
    (this runs before per-task XDG isolation), so the cache stays shared and
    outside any workdir → never snapshotted; evictable → smart-install re-downloads."""
    if install_dir is not None:
        return install_dir.rstrip("/")
    r = await host.run_command(f'printf %s "{_OPENCODE_CACHE_SHELL_DEFAULT}"')
    path = r.stdout.strip()
    if r.exit_code != 0 or not path:
        raise RuntimeError(
            f"failed to resolve opencode cache dir on host (exit {r.exit_code}): "
            f"{r.stderr.strip()[:200]}"
        )
    return path.rstrip("/")
```
(`ensure_opencode_installed` / `_smart_install_check` / `_install_opencode_from_zip` keep using `resolved_install_dir` — now the cache; the smart-install `--check` + zip-extract write the binary into the cache. `mkdir -p` the cache dir in `_install_opencode_from_zip` before extracting if not already done.)

- [ ] **Step 2 — add `_isolation_env`.** Add the helper from Shared contracts (HOME + the three XDG dirs under `<workdir>/home`).

- [ ] **Step 3 — `launch_opencode` merges the isolation env.** In `launch_opencode`, change the `env` dict so it includes the isolation env (keep `OPENCODE_DB` + `extra_env`):

```python
    env = {
        **_isolation_env(host),
        "OPENCODE_DB": f"{host.taskdir}/opencode.db",
        **(extra_env or {}),
    }
```

- [ ] **Step 4 — `opencode_export` + `opencode_import` merge the isolation env.** Both currently pass `env={"OPENCODE_DB": opencode_db_path}`. Change both to:

```python
            env={**_isolation_env(host), "OPENCODE_DB": opencode_db_path},
```
(so export/import read/write the isolated `~/.local/share/opencode` — `host` is the first arg of both; use `_isolation_env(host)`.)

- [ ] **Step 5 — commit (Phase 4 only).**

## Task 2: types — seed_id / on_seed_saved / auto_start

**Reference:** `optio_claudecode/types.py` (`seed_id`, `on_seed_saved`, `auto_start` fields).
**File:** `packages/optio-opencode/src/optio_opencode/types.py`

- [ ] **Step 1 — add the fields** to `OpencodeTaskConfig` (after the resume fields), import `Awaitable` if missing:

```python
    # --- seed surface (mirrors optio-claudecode) ---
    seed_id: str | None = None
    on_seed_saved: "Callable[[str], Awaitable[None] | None] | None" = None
    # Fresh launch kicks the agent off unattended via the opencode session API
    # (POST /api/session/<id>/prompt "Read AGENTS.md and execute the task it
    # describes"); suppressed on resume.
    auto_start: bool = False
```

- [ ] **Step 2 — document `opencode_install_dir` as the binary-cache override** (edit its existing doc comment to say it overrides the optio-owned opencode binary **cache** dir — `OPENCODE_CACHE_DIR` / `${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin` — never the host `~/.local/bin`). Keep the absolute-path validation in `__post_init__`.

- [ ] **Step 3 — commit (Phase 4).**

## Task 3: seed_manifest.py (NEW — mirror claudecode)

**Reference:** `optio_claudecode/src/optio_claudecode/seed_manifest.py` (the whole file — copy its structure: imports `from optio_agents import seeds`, the SUFFIX, the `SeedManifest`, the `delete_seed`/`list_seeds`/`purge_seed` thin wrappers).
**File (create):** `packages/optio-opencode/src/optio_opencode/seed_manifest.py`

- [ ] **Step 1 — create the module** with `OPENCODE_SEED_SUFFIX`, `OPENCODE_SEED_MANIFEST` (from Shared contracts — `home_subdir="home"`, the three `include` paths, `consume_transform=None`), and `delete_seed`/`list_seeds`/`purge_seed` wrappers binding `OPENCODE_SEED_SUFFIX` (identical to claudecode's, with the opencode names). No `_rekey_*` transform (opencode needs none).

- [ ] **Step 2 — commit (Phase 4).**

## Task 4: session.py — seed merge/capture + auto-start

**Reference:** `optio_claudecode/session.py` — the seed `merge_seed` call in the fresh-start body, the `capture_seed` block in the `finally` (before `_capture_snapshot`, gated on `config.on_seed_saved`), and the `_call_maybe_async` helper.
**File:** `packages/optio-opencode/src/optio_opencode/session.py`

- [ ] **Step 1 — imports.** Add `from optio_agents import seeds as _seeds`, `from optio_opencode.seed_manifest import OPENCODE_SEED_MANIFEST, OPENCODE_SEED_SUFFIX`, and an `_call_maybe_async` helper (copy from claudecode `session.py`).

- [ ] **Step 2 — seeded-fresh merge.** In `_opencode_body`, inside `if not resuming:`, after the `opencode.json` write (line ~182-184) and before launch, add (mirror claudecode):

```python
            if config.seed_id is not None:
                await _seeds.merge_seed(
                    ctx, host,
                    seed_id=config.seed_id,
                    manifest=OPENCODE_SEED_MANIFEST,
                    suffix=OPENCODE_SEED_SUFFIX,
                    decrypt=config.session_blob_decrypt,
                )
```
(merge writes the seed env into `<workdir>/home`, where the launch's `XDG_DATA_HOME` points → seeded `auth.json` is used.)

- [ ] **Step 3 — auto-start POST.** After the session is pre-created (`session_id` set, ~line 264) and before `await proc.wait()`, add (fresh only):

```python
        if config.auto_start and not resuming:
            await _post_opencode_prompt(
                worker_port, password, session_id,
                host_actions.AUTO_START_PROMPT,  # or a module const here
            )
```
Add a helper `_post_opencode_prompt(port, password, session_id, message)` next to `_create_opencode_session` (mirror that helper's HTTP+BasicAuth pattern): `POST http://127.0.0.1:<port>/api/session/<session_id>/prompt`. **The exact JSON body is confirmed by the Task-7 spike**; until then use the shape the spike finds (likely `{"parts": [{"type": "text", "text": message}]}` or `{"text": message}` — the spike decides). Define `AUTO_START_PROMPT = "Read AGENTS.md and execute the task it describes"` (module-level in session.py or import from host_actions).

- [ ] **Step 4 — seed capture in `finally`.** In the `finally`, after `terminate_subprocess` and **before** `_capture_snapshot`, add (mirror claudecode, gated + failure-swallowing):

```python
        if not resuming and config.on_seed_saved is not None:
            try:
                seed_id_out = await _seeds.capture_seed(
                    ctx, host,
                    manifest=OPENCODE_SEED_MANIFEST,
                    suffix=OPENCODE_SEED_SUFFIX,
                    encrypt=config.session_blob_encrypt,
                )
                await _call_maybe_async(config.on_seed_saved, seed_id_out)
            except Exception:
                _LOG.exception("opencode seed capture failed; callback not fired")
```
(capture reads from `<workdir>/home`, the isolated tree the seed manifest's `home_subdir` targets.)

- [ ] **Step 5 — commit (Phase 4).**

## Task 5: __init__ — export the seed surface

**Reference:** `optio_claudecode/__init__.py` (seed exports).
**File:** `packages/optio-opencode/src/optio_opencode/__init__.py`

- [ ] **Step 1 —** import + `__all__`-add `OPENCODE_SEED_MANIFEST`, `OPENCODE_SEED_SUFFIX`, `delete_seed`, `list_seeds`, `purge_seed` from `optio_opencode.seed_manifest`.
- [ ] **Step 2 — commit (Phase 4).**

# Phase 2 — demo (after Phase 1; one file)

## Task 6: demo opencode tasks — setup-seed + seeded tasks

**Reference:** `optio-demo/src/optio_demo/tasks/claudecode.py` (the WHOLE file — `_make_on_seed_saved`, `DEMO_SEED_COLLECTION_SUFFIX`, the setup task, the per-seed generated tasks, `get_tasks(services)`).
**File:** `packages/optio-demo/src/optio_demo/tasks/opencode.py`

- [ ] **Step 1 —** mirror claudecode's demo into the opencode demo: a `_demo_opencode_seeds` registry collection; a static **"Setup opencode seed"** task (`OpencodeTaskConfig` vanilla — no `seed_id` — with `on_seed_saved` wired to record `{seedId,name,createdAt}` + in-process `resync`; browser stays suppress, the opencode default; `supports_resume=False`); and per recorded seed a generated **"opencode demo — {name}"** task with `seed_id` baked in + `auto_start=True`. Keep the existing opencode demo task(s) additive. `get_tasks` becomes `async def get_tasks(services)` if it isn't already (mirror claudecode), reading `services["db"]/["prefix"]/["optio"]`. Confirm `tasks/__init__.py` awaits/passes `services` to the opencode generator (mirror how it does for claudecode).
- [ ] **Step 2 — commit (Phase 4).**

# Phase 3 — tests (parallel; disjoint files)

## Task 7: host_actions tests — cache + isolation + auto-start spike

**Reference:** `optio_claudecode/tests/test_host_actions.py` (cache-resolution + prep tests).
**File:** `packages/optio-opencode/tests/test_host_actions.py` (extend) — and a **spike note**.

- [ ] **Step 1 — `_isolation_env` unit test:** assert it returns `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` all under `<workdir>/home` for a fake host with `workdir="/wd"`.
- [ ] **Step 2 — cache resolution test:** override wins; default path is resolved via the `printf` host command (scripted fake host returns a cache path) — mirror claudecode.
- [ ] **Step 3 — `launch_opencode` env test:** with a recording fake host, assert the launch env carries the four isolation keys + `OPENCODE_DB`. (Reuse the opencode test fake-host pattern.)
- [ ] **Step 4 — auto-start body-shape SPIKE (run live, document, NOT an automated assertion):** against the cached opencode, `POST /api/session/<id>/prompt` with a candidate body and observe the accepted shape; record it in `_post_opencode_prompt` + a comment. (This is the implementation spike named in Task 4 Step 3.)
- [ ] **Step 5 — commit (Phase 4).**

## Task 8: seed config + manifest tests

**Reference:** `optio_claudecode/tests/test_seed_config.py`.
**File (create):** `packages/optio-opencode/tests/test_seed_config.py`

- [ ] **Step 1 —** `OpencodeTaskConfig` defaults: `seed_id is None`, `on_seed_saved is None`, `auto_start is False`. Manifest shape: `OPENCODE_SEED_SUFFIX == "_opencode_seeds"`, `home_subdir == "home"`, `auth.json` path in `include`. `delete_seed`/`list_seeds`/`purge_seed` exist + bind the suffix (round-trip vs a `mongo_db` fixture, mirror the optio-agents seed tests).
- [ ] **Step 2 — commit (Phase 4).**

## Task 9: session seed + auto-start integration tests

**Reference:** `optio_claudecode/tests/test_session_seed_capture.py` / `_consume.py`, and opencode's `test_session_local.py` (the `_supply_scenario` fake-opencode harness, `_make_ctx`).
**File (create):** `packages/optio-opencode/tests/test_session_seed.py`

- [ ] **Step 1 — capture:** a fresh opencode session with `on_seed_saved` → assert the callback fired with a hex id and a seed doc+blob exist (suffix `_opencode_seeds`); the seed tar contains `auth.json` only-include paths. Use the fake-opencode harness; plant a fake `home/.local/share/opencode/auth.json` in a `before_execute` or via the fake. (Mirror claudecode's capture test + fake_claude seed scenario — extend `fake_opencode.py` with a scenario that plants `$XDG_DATA_HOME/opencode/auth.json` if needed.)
- [ ] **Step 2 — consume:** capture, then a second fresh session (different process_id) with that `seed_id` → assert (via a `before_execute` probe) the isolated `home/.local/share/opencode/auth.json` is present.
- [ ] **Step 3 — auto_start gating:** assert (recording fake / monkeypatched `_post_opencode_prompt`) that a fresh `auto_start=True` session POSTs the prompt and a resume does not.
- [ ] **Step 4 — commit (Phase 4).**

# Phase 4 — VERIFICATION + commits

## Task 10: verify, then commit

- [ ] **Step 1 — opencode suite:** `cd packages/optio-opencode && OPTIO_SKIP_PREFLIGHT_TESTS=1 ../../.venv/bin/python -m pytest -q` → PASS (incl. existing session/resume tests — isolation env must not break them; the fake-opencode substitution ignores HOME/XDG).
- [ ] **Step 2 — smoke unaffected packages:** optio-agents + optio-host pytest → PASS (seed engine unchanged; only opencode adopts it).
- [ ] **Step 3 — demo import:** `python -c "import optio_demo.tasks.opencode"` → OK.
- [ ] **Step 4 — grep guard:** opencode binary is host-`~`-free —
  `! grep -rn "local/bin" packages/optio-opencode/src/optio_opencode/host_actions.py | grep -v cache` (the only `.local/bin` reference, if any, is in a comment/the old default which should be gone) and `grep -q "OPENCODE_CACHE_DIR" packages/optio-opencode/src/optio_opencode/host_actions.py`.
- [ ] **Step 5 — commit** in logical groups (host_actions+types+session+manifest+__init__ as the feature; tests; demo), no `Co-Authored-By`.

---

## Experiments / open questions (live, NOT automated; run during validation)

Per the spec: launch the opencode "Setup … seed" demo task; connect a provider in the web TUI (an `api`/token provider, and an `oauth` one if available); observe:
1. Does opencode attempt to spawn a browser during login (in web/headless mode), or surface a URL?
2. Does the `suppress` shim swallow anything needed for login?
3. Where does `auth.json` land (confirm it's the isolated `<workdir>/home/.local/share/opencode/auth.json`)?

Outcome decides whether a follow-up adds a redirect / launch-suppress↔login-redirect split. v1 ships suppress and is validated for the token-paste path. **Also**: confirm the `POST /api/session/:id/prompt` body shape (Task 7 Step 4) — wire the confirmed shape into `_post_opencode_prompt`.

---

## Spec coverage

- Binary cache → Task 1 (Steps 1) + Task 7; host-`~`-free guard → Task 10 Step 4.
- HOME/XDG isolation → Task 1 (Steps 2-4) + Task 7 Steps 1,3.
- Seeding (manifest + wiring + config + exports) → Tasks 2,3,4,5,8,9.
- auto-start → Task 2 (field), Task 4 Step 3 (POST), Task 7 Step 4 (spike), Task 9 Step 3.
- Demo setup+seeded tasks → Task 6.
- Resume unchanged → Task 10 Step 1 (existing resume tests pass).
- Experiments (login browser) → explicitly out of build; live section above.
