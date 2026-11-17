# Claude Code through an LLM gateway, with Requesty first: optio implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** optio-claudecode runs Claude Code conversation sessions through Requesty, using a validated and encrypted gateway seed. The agent type is `claudecode_requesty`.

**Architecture:** A gateway seed is a versioned JSON blob, encrypted, stored in `{prefix}_claudecode_requesty_seeds`. It is written `pending`. A setup task then runs one short real Claude Code conversation per model and makes the seed `alive`, or removes it. `ClaudeCodeRequestyTaskConfig`, a subclass of `ClaudeCodeTaskConfig`, switches the existing conversation session into gateway mode: the seed supplies the child's environment, the model catalog and the effort levels, and every OAuth-only path is skipped. A small Requesty provider lists the account's models for the consumer's form.

**Tech Stack:** Python 3.11 asyncio, Motor + GridFS, aiohttp, pytest + pytest-asyncio (each test gets its own throwaway database on the host's Mongo), `tests/fake_claude.py`; TypeScript vitest (optio-conversation-ui, optio-agents-ui).

**Spec:** `/home/csillag/requesty-work/optio/docs/2026-11-17-claudecode-gateway-design.md`. Consumer side: `/home/csillag/requesty-work/excavator/docs/2026-11-17-claude-code-requesty-design.md`.

## Findings against the interface contract (read first)

1. **The token would be visible on the remote command line.** `RemoteHost.launch_subprocess` (optio-host `host.py:1155-1167`) writes `env=` as `export K=V; ...` into the command string that sshd runs. A token passed in `env=` therefore sits in the remote shell's argv for the whole session, and excavator runs analyses over SSH (`free_style/analyze.py:103`). So the token never goes into `env=`. The launch command starts with `IFS= read -r ANTHROPIC_AUTH_TOKEN && export ANTHROPIC_AUTH_TOKEN && exec ...`, and optio writes the token as the first line on stdin, on both hosts (Task 6). The child still receives the token only through its environment.
2. **The effort flag cannot follow a live switch.** The contract sets `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1` "when the session's model has levels". The environment is fixed at launch, but models switch live. The plan sets the variable when any usable model in the seed has levels, and sends no effort for a model without levels. Task 5 settles the exact form of "no effort" against the real CLI.
3. **The pool can hand out a pending seed.** `seeds.acquire` skips only `status == "dead"` today (`optio-agents seeds.py:433`). Task 1 makes it skip `pending` too.
4. **Requesty's response shapes are not in the repo.** The parser assumes the OpenAI-style `{"data": [{"id", "model_canonical_name"?, "reasoning": {"supported_efforts": [...]}?}]}` and also accepts a bare list. The live test must confirm this.
5. **Additions (nothing renamed):**
   - `list_models(..., base_url=None)` and `requesty.new_seed(...)`;
   - in `gateway`: `gateway_seed_status`, `seed_summary`, `GatewaySeed.effective_default_model()`, and the launch helpers (Task 6);
   - `validate_gateway_models(..., install_dir=None)`;
   - `create_gateway_setup_task(..., install_dir=None, per_model_timeout=90.0)`;
   - `optio_agents.model_probe.probe_turn`, the public name of `_probe_turn`;
   - `GatewaySeed.token` is declared `field(repr=False)`.
6. **Decided here, since the contract leaves it open:**
   - An exception from `on_seed_saved` fails the setup task. The seed stays alive but unassigned.
   - `retest=True` on a pending seed raises `ValueError`, and so does `retest=False` on a seed that is not pending.
   - A pending setup that is cancelled or fails removes its seed.
   - The gateway config refuses `credentials_json`, `on_seed_saved`, a `SeedProvider` `seed_id`, and `ANTHROPIC_*` keys in `env`.
7. **How the setup task reaches Mongo.** It uses `ctx._db` with the `prefix` and `suffix` it was given, as `seeds.capture_seed` (`seeds.py:298-301`) and the session's capture (`session.py:1299-1302`) do. Blobs go through `AsyncIOMotorGridFSBucket(db)`, as in `seeds.plant_seed` (`seeds.py:345-346`) and `seeds.overwrite_seed_member`. Sessions read the seed with `ctx._prefix`, so the consumer must pass its optio prefix.
8. **Settled spec "to verify" item:** gateway sessions need no `initialize`. optio uses `initialize` only for the model list (`models.py:73`, `conversation.py:531`, `session.py:878`), and non-UI conversation sessions already run without it.
9. **The snapshot guard uses a marker file.** In gateway mode the guard tests for `home/.claude/.optio-gateway`, which is written right after the seed is read. Its fixed content is `gateway\n`, so nothing from the seed is planted. Crash-orphan rescue and Resurrect run in later runs, and the marker is what lets them apply the same guard.

## Global Constraints

Values from the spec:
- Agent type `claudecode_requesty`, AgentInfo name "Claude Code + Requesty", url "https://requesty.ai". `REQUESTY_SEED_SUFFIX = "_claudecode_requesty_seeds"`.
- Regions: `global` is `https://router.requesty.ai`, `eu` is `https://router.eu.requesty.ai`.
- Child environment:
  - `ANTHROPIC_BASE_URL`;
  - `ANTHROPIC_AUTH_TOKEN`, delivered over stdin (Finding 1);
  - `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`, set to the effective default model;
  - `CLAUDE_CODE_AUTO_MODE_SERVER=0` and `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`;
  - `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1` (Finding 2).
  - Inherited `ANTHROPIC_*` and `CLAUDE_CODE_OAUTH_TOKEN` are scrubbed.
- The token never appears in argv, a command line, a file, a snapshot, a repr, a log line or an error.
- `--model` is an exact id. Never the `default` alias, never `models.restore_model`.
- Effort levels are Claude Code's, in this order: `low, medium, high, xhigh, max`. Requesty levels outside this set are dropped.
- Validation: 3 at once, 90 s per model, the file `optio-check.txt`. A model passes when there is no error and the word is in the answer (case-insensitive).
- The seed document's `status` is `pending | alive | dead`, and the insert itself sets it.

Repo and test rules:
- Edit in `/home/csillag/requesty-work/optio`, branch `csillag/claudecode-gateway`.
- No release, no version bumps. At release time optio-claudecode needs the optio-agents release from Task 1.
- Commits follow the repo style `feat(<pkg>): ...`, with no Co-Authored-By line.
- Tests use no network and do not depend on wall-clock time: awaited conditions get 60 s ceilings. Each task runs only its own test files.

Shell rules:
- One command per call.
- Before syncing, a new file needs `git -C /home/csillag/requesty-work/optio add -N <absolute path>`.
- Commit messages are written with the Write tool.

Abbreviations: write every one out literally in the actual call, because the shell rules forbid variables.
- `REPO` = `/home/csillag/requesty-work/optio`
- `PKG` = `REPO/packages/optio-claudecode`
- `SP` = `/tmp/claude-1000/-home-csillag-chat/b5ba32aa-520f-41ce-9bf9-2783e52c7ce6/scratchpad`
- `SYNC` = `python3 SP/sync_requesty.py optio`
- `RT <args>` = `ssh -F /home/csillag/share/fleet-ssh/config excavator bash /home/csillag/requesty-work/bin/rt.sh optio <args>`
- `RUN <file>` = `RT pytest <file> -q`. Test paths are absolute (`REPO/...`); if rt.sh expects repo-relative paths, drop the `REPO/` prefix.

## Review Focus

- **RF1: token leaks outside the child's environment.** Places to check: the remote shell command line, argv, the workdir, the snapshot blobs, `repr(GatewaySeed)`, provider errors, validation reasons, logs.
- **RF2: a pending seed reaching a session.** Places to check: a document briefly without `status`, `acquire` (which filters only `dead`), a consumer passing the pending id.
- **RF3: effort where a model publishes none.** Places to check: `--effort` at launch, live switches in both directions, a Requesty level Claude Code does not know (`minimal`).
- **RF4: model drift between runs.** Cases: a resumed session whose model failed Test again; a default that failed; a non-Claude id going through code that expects `claude-<family>` or `default`.
- **RF5: one model failing inside a batch.** A check that raises, a hang, a crashed claude, or a cancelled setup must not sink the other models, and must leave no claude process, no workdir and no pending seed behind.

---

### Task 1: optio-agents: no pending seed from the pool; `probe_turn` public

**Files:**
- Modify `REPO/packages/optio-agents/src/optio_agents/seeds.py:421-455` (the `acquire` filter, line 433; the `mark_seed_status` docstring).
- Modify `REPO/packages/optio-agents/src/optio_agents/model_probe.py:45`.
- Modify `REPO/packages/optio-agents/AGENTS.md`: add a short "Seed statuses and the probe turn" section.
- Test: `REPO/packages/optio-agents/tests/test_seeds.py`, `REPO/packages/optio-agents/tests/test_model_probe.py`.

**Interfaces:**
- Produces: `seeds.acquire(...)` never returns a seed whose `status` is `"dead"` or `"pending"`.
- Produces: `model_probe.probe_turn(conversation, question: str, timeout: float, *, error_from_event=None) -> tuple[str, Any]`, with `_probe_turn = probe_turn` kept as an alias.

- [ ] Write the failing tests:
```python
async def test_acquire_skips_pending_seeds(mongo_db):          # RF2
    sid = await _pooled_seed(mongo_db, "p-pending")
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="pending")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p-pending", holder="h1") is None
    await seeds.mark_seed_status(mongo_db, prefix="t", suffix=SUFFIX, seed_id=sid, status="alive")
    assert await seeds.acquire(mongo_db, prefix="t", suffix=SUFFIX, poolKey="p-pending", holder="h1") == sid

def test_probe_turn_is_public():
    from optio_agents.model_probe import _probe_turn, probe_turn
    assert probe_turn is _probe_turn
```
- [ ] Run `SYNC`, then `RUN REPO/packages/optio-agents/tests/test_seeds.py` and `RUN REPO/packages/optio-agents/tests/test_model_probe.py`. Expected: FAIL (a pending seed is leased; ImportError).
- [ ] Implement:
  - in `acquire`, `"status": {"$nin": ["dead", "pending"]}`;
  - rename `def _probe_turn` to `def probe_turn` and add `_probe_turn = probe_turn`;
  - in the AGENTS.md note: seeds may be `pending` (never leased), and engines reuse `probe_turn`.
- [ ] `SYNC`, then both RUNs. Expected: PASS.
- [ ] Commit:
  - write `SP/msg-t1.txt`: `feat(optio-agents): the seed pool never hands out a pending seed; probe_turn is public`;
  - `git -C REPO add REPO/packages/optio-agents/src/optio_agents/seeds.py REPO/packages/optio-agents/src/optio_agents/model_probe.py REPO/packages/optio-agents/AGENTS.md REPO/packages/optio-agents/tests/test_seeds.py REPO/packages/optio-agents/tests/test_model_probe.py`;
  - `git -C REPO commit -F SP/msg-t1.txt`.

### Task 2: the gateway seed (storage)

**Files:**
- Create `PKG/src/optio_claudecode/gateway.py`.
- Modify `PKG/src/optio_claudecode/seed_manifest.py`: add `REQUESTY_SEED_SUFFIX = "_claudecode_requesty_seeds"` after line 19.
- Modify `PKG/AGENTS.md`: new section "## Gateway mode (Claude Code + Requesty)", part "Gateway seeds".
- Test: create `PKG/tests/test_gateway_seed.py`.

**Interfaces:** produces exactly the contract's `GatewayModel`, `GatewayPreset`, `GatewaySeed` (`token: str = field(repr=False)`), `usable_models()`, the four errors, `write_gateway_seed`, `read_gateway_seed` and `update_gateway_seed`. Also:
- `GatewaySeed.effective_default_model() -> str | None`: the default if usable, else the first usable model, else None.
- `async def gateway_seed_status(db, *, prefix: str, suffix: str, seed_id: str) -> str | None`: None for an unknown id.
- `def seed_summary(seed: GatewaySeed) -> str`, giving `f"{provider} {region}, default {default_model}, key …{token[-4:]}"`.
- `GATEWAY_SEED_VERSION = 1`.

**Algorithm:**
- **Blob:** JSON `{"version": 1, "provider", "region", "baseUrl", "token", "models": [{"id", "label", "group", "efforts": [..] | null, "status", "reason"}], "defaultModel", "presets": {"fast"|"extensive": {"model", "effort"}} | null, "testedAt": ISO-UTC | null}`.
- **`write_gateway_seed`:**
  1. Check the input: the default is one of the model ids; preset keys are a subset of {"fast","extensive"}; every preset's model is one of the ids. Otherwise `ValueError`.
  2. Encrypt the JSON (`encrypt` None means plaintext) and upload it with `AsyncIOMotorGridFSBucket(db).upload_from_stream("seed", payload)`.
  3. Write the document in ONE `insert_one`: `{createdAt, blobId, status, metadata: {gateway: {...}}}`. This is RF2: no window where the document exists without a status.
- **`update_gateway_seed`:**
  1. Load the document (`KeyError` if it is missing).
  2. Upload the new blob.
  3. `update_one` with `$set` of `blobId`, `metadata.gateway` and `updatedAt`, plus `status` when it is given.
  4. Delete the old blob, tolerating a missing one.
- **`read_gateway_seed`:** download the blob, decrypt it, parse it. A version other than 1 raises `ValueError`. `efforts` come back as tuples, `testedAt` as an aware datetime.
- **`metadata.gateway`:** `{"provider", "region", "keyHint": token[-4:], "defaultModel", "models": [{"id","label","status","reason"}], "testedAt"}`.

- [ ] Write the failing tests. Fixtures: `SEED` with `token="rq-test-0123456789abcd"` and two models, `prov-a/kimi-k3.1-pro` (pinned, `("low", "high")`) and `glm-flash` (managed, None), default `prov-a/kimi-k3.1-pro`. The toy cipher is `enc = lambda b: b"X" + bytes(reversed(b))` with its inverse `dec`.
```python
async def test_a_written_seed_reads_back_through_the_cipher(mongo_db):      # RF1, RF2
    sid = await write_gateway_seed(mongo_db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed=SEED, encrypt=enc)
    assert await read_gateway_seed(mongo_db, prefix="t", suffix=REQUESTY_SEED_SUFFIX, seed_id=sid, decrypt=dec) == SEED
    doc = await mongo_db["t_claudecode_requesty_seeds"].find_one()
    assert doc["status"] == "pending"
    assert SEED.token.encode() not in await _blob_bytes(mongo_db, doc["blobId"])
    assert SEED.token not in str(doc) and SEED.token not in repr(SEED)

async def test_the_document_carries_the_non_secret_part(mongo_db):
    ...
    assert doc["metadata"]["gateway"] == {"provider": "requesty", "region": "eu", "keyHint": "abcd",
        "defaultModel": "prov-a/kimi-k3.1-pro", "testedAt": None, "models": [
        {"id": "prov-a/kimi-k3.1-pro", "label": "prov-a/kimi-k3.1-pro", "status": "untested", "reason": None},
        {"id": "glm-flash", "label": "glm-flash", "status": "untested", "reason": None}]}
```
  Further tests:
  - `test_update_swaps_the_blob_and_sets_the_status`: `status=None` keeps `pending`; `status="alive"` sets it; the old blob is gone; `testedAt` is stored.
  - `test_usable_models_and_the_effective_default`: with only glm ok, `effective_default_model() == "glm-flash"`; with nothing usable it is None.
  - `test_unknown_seed_and_unknown_version`: `KeyError`; a blob with version 2 raises `ValueError`.
  - `test_write_refuses_a_default_outside_the_models`: `ValueError`.
  - `test_seed_summary`: `== "requesty eu, default prov-a/kimi-k3.1-pro, key …abcd"`.
- [ ] `git -C REPO add -N REPO/packages/optio-claudecode/src/optio_claudecode/gateway.py`, `git -C REPO add -N REPO/packages/optio-claudecode/tests/test_gateway_seed.py`, `SYNC`, `RUN PKG/tests/test_gateway_seed.py`. Expected: FAIL (ImportError).
- [ ] Implement as above, and document the seed layout in AGENTS.md.
- [ ] `SYNC`, RUN. Expected: PASS.
- [ ] Commit:
  - write `SP/msg-t2.txt`: `feat(optio-claudecode): gateway seeds, stored pending, encrypted, with a non-secret summary`;
  - `git -C REPO add` gateway.py, seed_manifest.py, AGENTS.md and test_gateway_seed.py (absolute paths);
  - `git -C REPO commit -F SP/msg-t2.txt`.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F8, exact signatures: `@dataclass(frozen=True) class GatewayModel: id: str; label: str; group: Literal["managed", "pinned", "policy"]; efforts: tuple[str, ...] | None; status: Literal["untested", "ok", "failed"] = "untested"; reason: str | None = None`. `@dataclass(frozen=True) class GatewayPreset: model: str; effort: str | None = None`. `@dataclass(frozen=True) class GatewaySeed: provider: str; region: str; base_url: str; token: str = field(repr=False); models: tuple[GatewayModel, ...]; default_model: str; presets: dict[str, GatewayPreset] | None = None; tested_at: datetime | None = None` (fields in this order).
- F8, errors: `class GatewayError(Exception)`; `GatewayAuthError(GatewayError)`, `GatewayUnreachable(GatewayError)`, `GatewayEmptyList(GatewayError)`. Any other failure (an unexpected status such as 429, a malformed body) raises plain `GatewayError`. No message ever contains the token.
- F8, storage: `async def write_gateway_seed(db, *, prefix: str, suffix: str, seed: GatewaySeed, encrypt, status: Literal["pending", "alive", "dead"] = "pending") -> str` and `async def update_gateway_seed(db, *, prefix: str, suffix: str, seed_id: str, seed: GatewaySeed, encrypt, status: Literal["pending", "alive", "dead"] | None = None) -> None`.
- F18: `metadata.gateway` also carries `"presets"`: `{"fast": {"model", "effort"}, "extensive": {"model", "effort"}}` from `seed.presets`, or `None`. Consumers read the presets there; nobody stores a second copy.

### Task 3: the Requesty provider

**Files:**
- Create `PKG/src/optio_claudecode/gateways/__init__.py` (a docstring only) and `PKG/src/optio_claudecode/gateways/requesty.py`.
- Modify `PKG/AGENTS.md`, part "Requesty provider".
- Test: create `PKG/tests/test_requesty.py`, with an in-file aiohttp server fixture (`web.AppRunner`, `web.TCPSite(runner, "127.0.0.1", 0)`, port from `runner.addresses[0][1]`) that records the `Authorization` header.

**Interfaces:**
- Consumes the Task 2 errors, `GatewayModel`, `GatewaySeed` and `models.EFFORT_LEVELS`.
- Produces `PROVIDER`, `REGIONS` and `ModelChoice` per the contract, plus:
  - `async def list_models(token, region, *, timeout=15.0, base_url=None) -> list[ModelChoice]`;
  - `def new_seed(*, token, region, models, default_model, presets=None) -> GatewaySeed`, with `base_url=REGIONS[region]`; an unknown region raises `ValueError`.

**Algorithm:**
- **Requests:** one `aiohttp.ClientSession(timeout=ClientTimeout(total=timeout))` sends GET `{base}/v1/models` and GET `{base}/v1/models/managed`, both with `Authorization: Bearer <token>`.
- **Errors:**

  | Cause | Raises |
  |---|---|
  | HTTP 401 or 403 | `GatewayAuthError` |
  | HTTP ≥ 500, a connection error, a timeout | `GatewayUnreachable` |
  | any other non-200 status, bad JSON or a bad shape | `GatewayError` |

  Every error is raised `from None`, and its message names only the path and the status.
- **Grouping:** entries from the managed endpoint are `managed`. In `/v1/models`, `policy/...` is `policy`, an id containing `/` is `pinned`, anything else is `managed`. Duplicate ids keep the first entry.
- **EU names:** `eu_only = id.endswith("@eu")`; such entries are kept only for region `eu`.
- **Efforts:** `reasoning.supported_efforts`, filtered to `EFFORT_LEVELS` in that order. An entry without them borrows them through `model_canonical_name` from a non-policy entry that has them. Policies always get None.
- **Result:** ordered managed, then pinned, then policy, each sorted by id; `label = id`. An empty result raises `GatewayEmptyList("the account allows no models")`.

- [ ] Write the failing tests. Synthetic fixtures; no real account or policy names:
```python
MODELS = {"data": [
    {"id": "prov-a/kimi-k3.1-pro", "model_canonical_name": "kimi-k3.1-pro",
     "reasoning": {"supported_efforts": ["minimal", "low", "high"]}},
    {"id": "prov-b/glm-5", "model_canonical_name": "glm-5"},
    {"id": "policy/example-policy", "reasoning": {"supported_efforts": ["low"]}}]}
MANAGED = {"data": [{"id": "kimi-k3.1-pro", "model_canonical_name": "kimi-k3.1-pro"},
                    {"id": "glm-5@eu", "model_canonical_name": "glm-5"}]}

async def test_eu_lists_the_three_groups(requesty):                         # RF3
    got = await list_models("rq-secret-9876", "eu", base_url=requesty.url)
    assert got == [ModelChoice("glm-5@eu", "glm-5@eu", "managed", None, True),
                   ModelChoice("kimi-k3.1-pro", "kimi-k3.1-pro", "managed", ("low", "high"), False),
                   ModelChoice("prov-a/kimi-k3.1-pro", "prov-a/kimi-k3.1-pro", "pinned", ("low", "high"), False),
                   ModelChoice("prov-b/glm-5", "prov-b/glm-5", "pinned", None, False),
                   ModelChoice("policy/example-policy", "policy/example-policy", "policy", None, False)]
    assert requesty.auth == ["Bearer rq-secret-9876"] * 2
```
  Further tests:
  - `test_global_leaves_out_eu_names`.
  - `test_errors_are_typed_and_carry_no_token`, parametrized:

    | Case | Expected |
    |---|---|
    | 401, 403 | `GatewayAuthError` |
    | 503 | `GatewayUnreachable` |
    | a closed port | `GatewayUnreachable` |
    | a handler awaiting a never-set Event, with `timeout=0.2` | `GatewayUnreachable` |
    | both lists empty | `GatewayEmptyList` |
    | `{"data": "x"}` | `GatewayError` |

    In every case assert that the token is in neither `str(exc)`, `repr(exc)` nor `caplog.text` (RF1).
  - `test_new_seed_takes_the_regions_url`: `new_seed(..., region="global").base_url == "https://router.requesty.ai"`; region `"mars"` raises `ValueError`.
- [ ] `git -C REPO add -N` the three new files, `SYNC`, `RUN PKG/tests/test_requesty.py`. Expected: FAIL.
- [ ] Implement as above.
- [ ] `SYNC`, RUN. Expected: PASS.
- [ ] Commit `SP/msg-t3.txt`: `feat(optio-claudecode): the Requesty provider lists an account's models in three groups`.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F8: `def new_seed(*, token: str, region: str, models: Sequence[GatewayModel], default_model: str, presets: dict[str, GatewayPreset] | None = None) -> GatewaySeed`, which sets `provider=PROVIDER` and `base_url=REGIONS[region]`; an unknown region raises `ValueError`.
- F15: an unexpected answer (a status other than 2xx/401/403, a malformed body) raises plain `GatewayError`; add one test for it (a 429).
- Commit steps in Tasks 3-12: `git add` names exactly the task's Files (as Tasks 1-2 do).

### Task 4: the agent type `claudecode_requesty`

**Files:**
- Modify `PKG/src/optio_claudecode/types.py`:
  - on `ClaudeCodeTaskConfig`: `gateway_provider: ClassVar[str | None] = None` and `gateway_seed_suffix: ClassVar[str | None] = None`;
  - `ClaudeCodeRequestyTaskConfig` after line 444.
- Modify `PKG/src/optio_claudecode/info.py`: add `REQUESTY_AGENT_INFO`.
- Modify `PKG/src/optio_claudecode/session.py`: add `create_claudecode_requesty_task` after line 2453.
- Modify `PKG/src/optio_claudecode/__init__.py`: add the exports.
- Modify `PKG/AGENTS.md`: Public API, and a field-semantics bullet for the gateway config.
- Modify `REPO/AGENTS.md:826-830`: name `ClaudeCodeRequestyTaskConfig` as the `ClaudeCodeTaskConfig` subclass for Claude Code through Requesty.
- Test: create `PKG/tests/test_requesty_config.py`; modify `PKG/tests/test_agent_info.py`.

**Interfaces:**
- `@dataclass(frozen=True, kw_only=True) class ClaudeCodeRequestyTaskConfig(ClaudeCodeTaskConfig)`:
  - `agent_type: Literal["claudecode_requesty"] = "claudecode_requesty"`;
  - `mode` defaults to `"conversation"`;
  - ClassVars `gateway_provider = "requesty"` and `gateway_seed_suffix = REQUESTY_SEED_SUFFIX`.
- Its `__post_init__` refuses, before calling `super().__post_init__()`:
  - `mode != "conversation"`, with a message naming the tmux command line;
  - a callable `seed_id`;
  - `credentials_json`;
  - `on_seed_saved`;
  - `env` keys starting with `ANTHROPIC_`.
- `create_claudecode_requesty_task(process_id, name, config, description=None, metadata=None) -> TaskInstance`: `TypeError` unless the config is a `ClaudeCodeRequestyTaskConfig`, then delegates to `create_claudecode_task`.
- `REQUESTY_AGENT_INFO = AgentInfo(slug="claudecode_requesty", name="Claude Code + Requesty", url="https://requesty.ai")`.
- Package exports: `ClaudeCodeRequestyTaskConfig`, `create_claudecode_requesty_task`, `REQUESTY_AGENT_INFO`, `REQUESTY_SEED_SUFFIX`.

- [ ] Write the failing tests. Helper: `_cfg(**kw)` builds `ClaudeCodeRequestyTaskConfig(consumer_instructions="x", fs_isolation=False, permission_mode="dontAsk", **kw)`.
  - `test_defaults`: agent_type, mode `"conversation"`, `gateway_provider == "requesty"`, suffix; it is an instance of `ClaudeCodeTaskConfig`; `ClaudeCodeTaskConfig.gateway_provider is None`.
  - `test_iframe_mode_is_refused`: `pytest.raises(ValueError, match="conversation")` on `_cfg(mode="iframe")`.
  - `test_oauth_inputs_are_refused`, parametrized over `{"credentials_json": {"a": 1}}`, `{"on_seed_saved": lambda s, i: None}`, `{"seed_id": _provider}` and `{"env": {"ANTHROPIC_BASE_URL": "x"}}`: each raises `ValueError`.
  - `test_the_factory`: `create_claudecode_requesty_task("p", "n", _cfg(conversation_ui=True)).ui_widget == "conversation"`; a plain `ClaudeCodeTaskConfig` raises `TypeError`.
  - In `test_agent_info.py`: `REQUESTY_AGENT_INFO` equals the value above, and its slug equals `ClaudeCodeRequestyTaskConfig.__dataclass_fields__["agent_type"].default`.
- [ ] `git -C REPO add -N REPO/packages/optio-claudecode/tests/test_requesty_config.py`, `SYNC`, then `RUN PKG/tests/test_requesty_config.py` and `RUN PKG/tests/test_agent_info.py`. Expected: FAIL.
- [ ] Implement as above.
- [ ] `SYNC`, `RUN PKG/tests/test_requesty_config.py`, `RUN PKG/tests/test_agent_info.py`, `RUN PKG/tests/test_types.py`. Expected: PASS.
- [ ] Commit `SP/msg-t4.txt`: `feat(optio-claudecode): ClaudeCodeRequestyTaskConfig, the claudecode_requesty agent type`.

### Task 5: no effort for a model without levels (checked against the real CLI)

**Files:**
- Create `PKG/tests/live/probe_gateway_effort.py`: not collected by pytest; documented in AGENTS.md Testing.
- Modify `PKG/src/optio_claudecode/conversation.py`: an attribute after line 116; `_flag_settings` (522-549); `_apply_settings` (518).
- Modify `PKG/AGENTS.md`: the session-controls paragraph (lines 351-386) and Testing.
- Test: `PKG/tests/test_live_switch.py`.

**Interfaces:**
- `ClaudeCodeConversation.effort_needs_levels: bool = False`. When True, a model whose catalog entry lists no `effort` is switched to with `NO_EFFORT` settings. The probe below decides the form; the default is `{"model": target}` with no `effortLevel` key. In that mode, an `effort` argument for such a model raises `ValueError`, and `self.effort` becomes None.
- `_apply_settings` sets `self.effort = settings.get("effortLevel")`.

Steps:
- [ ] **Write the probe.** It uses only the loopback; no network.
  - It finds the newest binary under `${OPTIO_CLAUDECODE_CACHE_DIR:-~/.cache/optio-claudecode/versions}`. If there is none, it prints `no cached claude binary` and exits 2.
  - It runs an aiohttp server on 127.0.0.1:0 that records every POST body and answers `/v1/messages*` with 400 `{"type":"error","error":{"type":"invalid_request_error","message":"probe"}}`.
  - Each scenario gets a fresh HOME and the env `HOME`, `PATH`, `DISABLE_AUTOUPDATER=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`, `ANTHROPIC_AUTH_TOKEN=probe-token`, `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1`.
  - Each scenario runs `claude -p --input-format stream-json --output-format stream-json --verbose --model <m> [--effort high]`. It optionally writes one `apply_flag_settings` and waits for its answer, sends one user message, and waits for `result` (120 s ceiling).
  - It prints, per scenario, every key path containing `effort` (and `thinking`) in the bodies whose `model` is the one under test.
  - The scenarios:

    | Scenario | Extra env | Launch | Then apply_flag_settings |
    |---|---|---|---|
    | A | | `--model gw/plain` | none |
    | B | | `--model gw/levels --effort high` | `{"model": "gw/plain"}` |
    | C | | as B | `{"model": "gw/plain", "effortLevel": null}` |
    | D | `CLAUDE_CODE_EFFORT_LEVEL=unset` | `--model gw/levels --effort high` | none |
    | E | `CLAUDE_CODE_EFFORT_LEVEL=unset` | `--model gw/plain` | `{"model": "gw/levels", "effortLevel": "high"}` |
- [ ] **Run the probe:** `git -C REPO add -N REPO/packages/optio-claudecode/tests/live/probe_gateway_effort.py`, `SYNC`, `RT py PKG/tests/live/probe_gateway_effort.py`. Expected: a five-row table, or `no cached claude binary`.
- [ ] **Decide `NO_EFFORT`:**

  | Probe result | Decision |
  |---|---|
  | A and B send no effort | omit the key (the default) |
  | A sends none, B does, C sends none | `{"model": t, "effortLevel": None}` |
  | otherwise, and D and E both send `high` | add `"CLAUDE_CODE_EFFORT_LEVEL": "unset"` to `gateway_launch_env` in Task 6 and keep omission |
  | otherwise | stop and report the table to the controller (owner decision) |
  | no cached binary | omission; add "effort for a model without levels" to the live-test list |

  Write the result (CLI version, date, table) into the Testing section of AGENTS.md.
- [ ] **Write the failing tests** in `test_live_switch.py`:
```python
GATEWAY = [{"id": "prov-a/kimi-k3.1-pro", "label": "prov-a/kimi-k3.1-pro", "effort": ["low", "high"]},
           {"id": "glm-flash", "label": "glm-flash"}]

def _gw(model, effort):
    conv, handle, reader = _conv(model=model, effort=effort)
    conv.models_offered, conv.effort_needs_levels = GATEWAY, True
    return conv, handle, reader

async def test_gateway_a_model_without_levels_is_sent_no_effort():          # RF3
    conv, handle, reader = _gw("prov-a/kimi-k3.1-pro", "high")
    switch = asyncio.create_task(conv.apply_settings(model="glm-flash"))
    req = await _next_request(handle)
    assert req["request"]["settings"] == {"model": "glm-flash"}             # NO_EFFORT form
    _answer(handle, req); await _answer_readback(handle); await asyncio.wait_for(switch, 60)
    assert (conv.model, conv.effort) == ("glm-flash", None)
    await _done(handle, reader)
```
  Further tests:
  - `test_gateway_back_to_a_model_with_levels_sends_its_preselected_level`: settings `== {"model": "prov-a/kimi-k3.1-pro", "effortLevel": "high"}`.
  - `test_gateway_an_effort_for_a_model_without_levels_is_refused`: `set_control("reasoning_effort", "high")` on glm raises `ValueError`, and `handle.stdin.lines.empty()`.
  - `test_gateway_a_model_outside_the_seed_is_refused`: `apply_settings(model="claude-opus-5")` raises `ValueError`.
  - The existing `test_a_model_without_effort_levels_keeps_the_running_level` stays green: the flag is off there.
- [ ] `SYNC`, `RUN PKG/tests/test_live_switch.py`. Expected: FAIL (`effortLevel` present).
- [ ] Implement as above.
- [ ] `SYNC`, RUN. Expected: PASS.
- [ ] Commit `SP/msg-t5.txt`: `feat(optio-claudecode): a gateway model without effort levels is switched to without an effort`. Include the probe and AGENTS.md.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F5: add scenario F: D's environment with B's launch and live switch. The adopted no-effort form must make F send no effort. If no form achieves that against the real CLI, keep the omission form, mark it for the live test in the report, and say so.

### Task 6: launch helpers and the stdin secret

**Files:**
- Modify `PKG/src/optio_claudecode/gateway.py`: the launch helpers.
- Modify `PKG/src/optio_claudecode/host_actions.py`: after `conversation_launch_env`, line 1332.
- Test: create `PKG/tests/test_gateway_launch.py`.

**Interfaces:**
- `GATEWAY_SECRET_ENV = ("ANTHROPIC_AUTH_TOKEN",)`
- `GATEWAY_ENV_REMOVE = ("ANTHROPIC_*", "CLAUDE_CODE_OAUTH_TOKEN")`
- `def gateway_catalog(seed) -> list[dict]`: the usable models as `{"id", "label", "effort"?: list}`, the `models.parse_cli_models` shape.
- `def gateway_launch_env(seed) -> dict[str, str]`: the non-secret environment from the Global Constraints. The `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT` key appears only when some usable model has levels, and the effective default model goes into `ANTHROPIC_DEFAULT_*`.
- `def launch_model(seed, preferred: str | None) -> str`: `preferred` if it is usable, else `effective_default_model()`. `GatewayError` when nothing is usable.
- `def launch_effort(seed, model: str, preferred: str | None) -> str | None`: None when the model has no levels; `preferred` if listed; else `"high"` if listed, else the highest level.
- `host_actions.with_stdin_secrets(command: str, names: Sequence[str]) -> str`, which gives `"IFS= read -r N && export N && ... exec " + command`. A name not matching `^[A-Za-z_][A-Za-z0-9_]*$` raises `ValueError`.
- `async def host_actions.write_stdin_secrets(handle, values: Sequence[str]) -> None`: one line per value, then drain. A value containing `\n`, `\r` or `\0` raises `ValueError`, and the message does not include the value.

- [ ] Write the failing tests:
  - `test_the_catalog_is_the_usable_models` (RF4): a seed with a third, failed model gives `[{"id": "prov-a/kimi-k3.1-pro", "label": "prov-a/kimi-k3.1-pro", "effort": ["low", "high"]}, {"id": "glm-flash", "label": "glm-flash"}]`.
  - `test_the_launch_env`: exact dict, with `ANTHROPIC_BASE_URL == "https://router.eu.requesty.ai"`, the four `ANTHROPIC_DEFAULT_*_MODEL == "prov-a/kimi-k3.1-pro"`, `"CLAUDE_CODE_AUTO_MODE_SERVER": "0"`, `"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"` and `"CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1"` (plus `CLAUDE_CODE_EFFORT_LEVEL` if Task 5 chose that branch). The token is in no value. A seed with no levels anywhere has no `ALWAYS_ENABLE` key.
  - `test_launch_model` (RF4):

    | preferred | Expected |
    |---|---|
    | usable `glm-flash` | `glm-flash` |
    | failed `prov-b/broken` | default |
    | `"default"` | `prov-a/kimi-k3.1-pro`, never `"default"` |
    | None, with the default failed | the first usable model |

  - `test_launch_effort` (RF3): `(kimi, "low")` gives `"low"`; `(kimi, "xhigh")` gives `"high"`; `(kimi, None)` gives `"high"`; `(glm, "high")` gives None.
  - `test_with_stdin_secrets`: `with_stdin_secrets("claude -p", ["ANTHROPIC_AUTH_TOKEN"]) == "IFS= read -r ANTHROPIC_AUTH_TOKEN && export ANTHROPIC_AUTH_TOKEN && exec claude -p"`; the name `"A B"` raises `ValueError`.
  - `test_a_local_launch_gets_the_secret_in_its_env_only` (`tmp_path`, LocalHost): launch `with_stdin_secrets(python3 -c <prints env SECRET_X, /proc/self/cmdline, next stdin line>, ["SECRET_X"])` with `stdin=True`, then write `["s3cret"]` and then `b"after\n"`. The output lines are `s3cret`, a cmdline without `s3cret`, and `after`.
  - `test_a_remote_launch_carries_no_secret_on_its_command_line` (RF1): a `RemoteHost(SSHConfig(host="h", user="u", key_path="/k"), "/tmp/x")` with a fake `_conn.create_process(full, encoding=None)` that records `full` and the stdin writes, launched with `env=gateway_launch_env(seed)`. Assert `"rq-" not in full` and that the first stdin bytes are `token.encode() + b"\n"`.
  - `test_write_stdin_secrets_refuses_a_line_break`: `ValueError`, and `"x\ny"` is not in its message.
- [ ] `git -C REPO add -N REPO/packages/optio-claudecode/tests/test_gateway_launch.py`, `SYNC`, `RUN PKG/tests/test_gateway_launch.py`. Expected: FAIL.
- [ ] Implement as above.
- [ ] `SYNC`, RUN. Expected: PASS.
- [ ] Commit `SP/msg-t6.txt`: `feat(optio-claudecode): gateway launch values from the seed; secrets reach the child over stdin`.

### Task 7: gateway mode in the conversation session

**Files:**
- Modify `PKG/src/optio_claudecode/session.py`:
  - add `_GATEWAY_MARKER = "home/.claude/.optio-gateway"`, `_snapshot_guard_file(config) -> str` (`".optio-gateway"` or `".credentials.json"`) and `async def _load_gateway_seed(ctx, config, seed_id) -> GatewaySeed`, near line 1629;
  - `_conversation_body` (508-1000);
  - `_spawn` (642-674);
  - the teardown (1251-1317);
  - `_capture_snapshot(..., guard_file: str = ".credentials.json")` (2111-2156).
- Modify `PKG/tests/fake_claude.py`: knob `FAKE_CLAUDE_ENV_LOG`, which appends one JSON line of the `ANTHROPIC_*` / `CLAUDE_CODE_*` variables at stream-json start.
- Modify `PKG/AGENTS.md`: gateway launch.
- Test: create `PKG/tests/test_gateway_session.py`, reusing the helpers of `test_conversation_ui_session.py`: `_make_optio`, `_wait_terminal`, `_wait_widget_upstream`, `_wait_widget_data`, `_read_until`, `_basic`, `_has_control`, `_controls`, `_remember_workdir`, `_log_lines`.

**Interfaces:**
- Consumes the Tasks 2, 4, 5 and 6 helpers.
- `_load_gateway_seed` raises `RuntimeError` in these cases:

  | Case | Message |
  |---|---|
  | `seed_id` is None | `"Claude Code + Requesty: no gateway seed configured"` |
  | unknown id | `"... unknown gateway seed <id>"` |
  | `pending` | `"... gateway seed <id> is still being tested"` |
  | no usable model | `"... gateway seed <id> has no usable model (run Test again)"` |

  Otherwise it returns `read_gateway_seed(ctx._db, prefix=ctx._prefix, suffix=config.gateway_seed_suffix, seed_id=..., decrypt=config.seed_decrypt)`.

**Algorithm, gateway branch only (`config.gateway_provider`):**
1. Load the seed right after `resolved_seed_id`.
2. Call `_plant_session_content(..., resolved_seed_id=None)`, so there is no `merge_seed`, then write the marker (`"gateway\n"`).
3. Set the model and effort:
   - `current_model = gateway.launch_model(seed, saved_picks.model if saved_picks else base_model)`;
   - `current_effort = gateway.launch_effort(seed, current_model, (saved_picks.effort if saved_picks else None) or base_effort)`.
   - When the preferred model is not usable, report progress `f"{AGENT_INFO.name} runs {current_model}: {preferred} is not usable in this gateway configuration"`.
4. Set `conversation.models_offered = cli_models = gateway_catalog(seed)`, `conversation.task_models = frozenset()` and `conversation.effort_needs_levels = True`.
5. Skip `_probe_cli_models` (line 741) and `fetch_cli_models` (line 878). `_restore_picks` is not used.
6. Launch:
   - `env` = `conversation_launch_env` over `{**config.env, **focus_env, **browser_env, **gateway_launch_env(seed)}`;
   - `_spawn` wraps `cmd` with `with_stdin_secrets(cmd, GATEWAY_SECRET_ENV)`, uses `env_remove=[*(config.scrub_env or []), *GATEWAY_ENV_REMOVE]`, and calls `write_stdin_secrets(handle, [seed.token])` before `conversation.attach`.
7. Turn the OAuth paths off: no credential watcher (line 992), no save-back (line 1251), no seed capture (line 1264); each is guarded with `not config.gateway_provider`.
8. Every `_capture_snapshot` call passes `guard_file=_snapshot_guard_file(config)`.

- [ ] Write the failing tests. Fixture `_gw_task(mongo_db, prefix, pid, shim, cache, status="alive", models=..., **cfg)`:
  - writes a seed with `token=TOKEN="rq-live-token-7f3a9c2e5b1d"` and three models: `prov-a/kimi-k3.1-pro` ok `("low","high")`, `glm-flash` ok None, `prov-b/broken` failed;
  - builds `ClaudeCodeRequestyTaskConfig(conversation_ui=True, show_session_controls=True, permission_mode="bypassPermissions", fs_isolation=False, install_dir=cache, ttyd_install_dir=shim, seed_id=sid, ...)`.
```python
async def test_a_gateway_session_gets_the_seed_in_its_environment_only(..., monkeypatch, tmp_path):  # RF1
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-key")
    ...  # env={"FAKE_CLAUDE_ENV_LOG": ..., "FAKE_CLAUDE_ARGV_LOG": ...}, before_execute=_remember_workdir(seen)
    env, argv = _log_lines(env_log)[0], _log_lines(argv_log)[0]
    assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN and "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_BASE_URL"] == "https://router.eu.requesty.ai"
    assert {env[f"ANTHROPIC_DEFAULT_{f}_MODEL"] for f in ("OPUS", "SONNET", "HAIKU", "FABLE")} == {"prov-a/kimi-k3.1-pro"}
    assert (env["CLAUDE_CODE_AUTO_MODE_SERVER"], env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"],
            env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"]) == ("0", "1", "1")
    assert argv[argv.index("--model") + 1] == "prov-a/kimi-k3.1-pro" and argv[argv.index("--effort") + 1] == "high"
    assert TOKEN not in json.dumps(argv)
    assert _files_containing(pathlib.Path(seen["workdir"]), TOKEN) == []    # skips symlinks
    assert not (pathlib.Path(seen["workdir"]) / "home/.claude/.credentials.json").exists()
    await conv.close(); proc = await _wait_terminal(optio, pid)
    assert proc["hasSavedState"] is True                                     # the marker passed the guard
    for blob in await _snapshot_blobs(mongo_db, prefix, pid):                # gunzipped session/workdir, conversation
        assert TOKEN.encode() not in blob
```
  Further tests:
  - `test_the_switcher_offers_the_seeds_usable_models` (RF3):
    - the `widgetData` model options are `["prov-a/kimi-k3.1-pro", "glm-flash"]`, and `reasoning_effort` has `levels == ["low", "high"]`;
    - POST `/control` model `glm-flash` gives an update without `reasoning_effort`, and the control log has `apply_flag_settings` `{"model": "glm-flash"}` (in the Task 5 form);
    - POST model `prov-b/broken` and model `claude-opus-5` each answer 400;
    - POST back to kimi carries `"effortLevel": "high"`.
  - `test_no_oauth_path_runs`: monkeypatch recorders over `session.cred_watcher.run_credential_watcher`, `session.cred_watcher.save_back_if_changed`, `session._seeds.merge_seed` and `session._seeds.capture_seed`. All stay empty, and the control log has no `initialize`.
  - `test_a_launch_refuses_a_pending_missing_or_dead_seed` (RF2), parametrized over pending / `seed_id=None` / every model failed: `launch_and_await_result` raises `ResultNotPublished`, and the terminal record is `failed` with the error substring from the table above.
- [ ] `git -C REPO add -N REPO/packages/optio-claudecode/tests/test_gateway_session.py`, `SYNC`, `RUN PKG/tests/test_gateway_session.py`. Expected: FAIL.
- [ ] Implement as above.
- [ ] `SYNC`, then `RUN PKG/tests/test_gateway_session.py`, `RUN PKG/tests/test_conversation_ui_session.py` and `RUN PKG/tests/test_session_seed_capture.py` (unchanged OAuth paths). Expected: PASS.
- [ ] Commit `SP/msg-t7.txt`: `feat(optio-claudecode): gateway mode: the seed's environment, catalog and effort levels; no OAuth paths`.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F9: the progress text uses `REQUESTY_AGENT_INFO.name` ("Claude Code + Requesty"), not `AGENT_INFO.name`.
- `test_no_oauth_path_runs` drops the `capture_seed` recorder (vacuous: Task 4 already refuses `on_seed_saved`); it keeps the watcher, save-back and `merge_seed` recorders.
- The test helpers `_files_containing` and `_snapshot_blobs` do not exist yet: write them in the new test file.

### Task 8: resume, rescue and Resurrect in gateway mode

**Files:**
- Modify `PKG/src/optio_claudecode/session.py`: the saved-picks branch (553-561, 741-746) takes the gateway path from Task 7; `_rescue_orphan_if_present` (1968-1974) passes `guard_file`.
- Modify `PKG/src/optio_claudecode/resurrect.py`: `_credentials_present` (97-103) becomes `_session_state_present(host, guard_file: str) -> bool`; the uses at lines 143 and 162 pass `S._snapshot_guard_file(config)`.
- Modify `PKG/AGENTS.md`: "Snapshot capture" (line 89) and "Resurrect" step 4 name the gateway marker.
- Test: `PKG/tests/test_gateway_session.py`.

**Interfaces:**
- Consumes `gateway.launch_model` and `gateway.launch_effort` and the picks file.
- Produces: a resumed gateway session runs `--continue --model <exact id>`, the saved pick when it is usable, else the effective default.

- [ ] Write the failing tests:
  - `test_a_resumed_gateway_session_runs_the_model_it_was_switched_to` (RF4):
    - first run: `before_execute=_plant_transcript_on("prov-a/kimi-k3.1-pro")`, POST model `glm-flash`, close;
    - resume: the second argv-log line has `--continue`, `--model glm-flash`, no `--effort`, and no `"default"`;
    - the `widgetData` model value is `glm-flash`, with no `reasoning_effort`.
  - `test_a_resumed_gateway_session_whose_model_failed_runs_the_default` (RF4): same first run, then `update_gateway_seed` marks `glm-flash` failed. The resume runs `--model prov-a/kimi-k3.1-pro --effort high`, and the process log contains `glm-flash is not usable`.
  - `test_the_snapshot_guard_takes_the_gateway_marker` (`ctx_and_captures`, LocalHost under `tmp_path`):
    - a workdir holding only `home/.claude/.optio-gateway`: `_capture_snapshot(ctx, host, end_state="done", workdir_exclude=None, guard_file=".optio-gateway") is True`;
    - the same workdir without the marker: False;
    - `resurrect._session_state_present(host, ".optio-gateway")` follows the file.
- [ ] `SYNC`, then `RUN PKG/tests/test_gateway_session.py` and `RUN PKG/tests/test_resurrect_hook.py`. Expected: FAIL (new tests only).
- [ ] Implement as above.
- [ ] `SYNC`, then the same two RUNs plus `RUN PKG/tests/test_rescue_orphan.py`. Expected: PASS.
- [ ] Commit `SP/msg-t8.txt`: `feat(optio-claudecode): a resumed gateway session keeps its exact model; the snapshot guard checks the gateway marker`.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F2: Files includes `PKG/tests/test_resurrect_hook.py`: update its `R._credentials_present` references (around lines 119, 125, 408) to the new name. That file then passes; only the new tests are RED before the change.

### Task 9: validation, one short conversation per model

**Files:**
- Create `PKG/src/optio_claudecode/gateway_setup.py`: the validation part.
- Modify `PKG/tests/fake_claude.py`: knob `FAKE_CLAUDE_GATEWAY_MODELS`, a JSON map from model id to behaviour. On a user message:

  | Behaviour | What the fake does |
  |---|---|
  | `pass` | replies with the stripped content of `./optio-check.txt` |
  | `wrong` | `I could not read it` |
  | `error` | result `is_error`, `"API Error: 404 model not available"` |
  | `error-with-token` | the same, with `$ANTHROPIC_AUTH_TOKEN` appended |
  | `hang` | reads stdin until EOF and never answers |
  | `exit` | `exit(3)` |

- Modify `PKG/AGENTS.md`: validation.
- Test: create `PKG/tests/test_gateway_validation.py`.

**Interfaces:**
- `CHECK_FILE = "optio-check.txt"`.
- `async def validate_gateway_models(seed, model_ids, *, ssh=None, concurrency=3, per_model_timeout=90.0, report=None, install_dir=None) -> dict[str, tuple[bool, str | None]]`.
- `report(done: int, total: int, model_id: str, ok: bool, reason: str | None)` is called after each model.
- `async def _check_model(seed, model_id, *, ssh, install_dir, per_model_timeout, install_lock) -> tuple[bool, str | None]`: the monkeypatch seam.

**Algorithm, one model:**
1. Create the host: `task_dir(ssh=ssh, process_id=f"gateway-check-{secrets.token_hex(6)}", consumer_name="optio-claudecode")`, LocalHost or RemoteHost, then `connect` and `setup_workdir`.
2. Install claude: `ensure_claude_installed(HookContext(_Quiet(), host), install_dir=install_dir, check_update=first)`, under `install_lock`, so the first model installs and the rest hit the cache.
3. Write `CHECK_FILE`, holding a random word (`secrets.choice(_WORDS) + str(1000 + secrets.randbelow(9000))`).
4. Build the argv with `build_claude_flags(permission_mode="dontAsk", allowed_tools=["Read"], tools=["Read"], model=model_id, effort=launch_effort(seed, model_id, None))` and `build_conversation_argv`.
5. Launch through `with_stdin_secrets` with `env=conversation_launch_env(workdir, gateway_launch_env(seed))` and `env_remove=GATEWAY_ENV_REMOVE`, then `write_stdin_secrets`. Attach a `ClaudeCodeConversation`.
6. Race `probe_turn(conv, CHECK_PROMPT, per_model_timeout, error_from_event=<result with is_error → its text>)` against `proc_wait`.
7. Map the outcome:

   | Outcome | Result |
   |---|---|
   | the word is in the answer (case-insensitive) and there is no error | `(True, None)` |
   | an error | `(False, error)` |
   | an answer without the word | `(False, f"did not answer with the word (answered {a[:120]!r})")` |
   | no answer | `(False, f"no answer within {t:g} s")` |
   | claude exited first | `(False, f"claude exited (exit {rc}) before answering")` |

8. In a `finally`: `terminate_subprocess(aggressive=True)`, cancel the reader, `cleanup_taskdir(aggressive=True)`, `disconnect`.
9. Every reason goes through `reason.replace(seed.token, "***")`.

**Algorithm, the batch:** `asyncio.Semaphore(concurrency)` and `gather`. A raising check gives `(False, f"check failed: {type(e).__name__}: {msg[:200]}")`, also redacted.

`CHECK_PROMPT = "Use the Read tool to read the file optio-check.txt in the current directory, then reply with only the word it contains."`

- [ ] Write the failing tests. Each uses `task_root`, `claude_cache_dir` and `monkeypatch.setenv("FAKE_CLAUDE_GATEWAY_MODELS", ...)`:
```python
async def test_each_model_passes_or_fails_with_its_reason(task_root, claude_cache_dir, monkeypatch):
    out = await validate_gateway_models(seed, ["m/pass", "m/wrong", "m/error"],
                                        install_dir=str(claude_cache_dir), per_model_timeout=60)
    assert out["m/pass"] == (True, None)
    assert out["m/wrong"][0] is False and "did not answer with the word" in out["m/wrong"][1]
    assert out["m/error"][0] is False and "404" in out["m/error"][1]
    assert os.listdir(task_root) == []                                      # RF5: workdirs removed
```
  Further tests:
  - `test_a_model_that_never_answers_times_out`: `hang` with `per_model_timeout=0.5` gives `(False, "no answer within 0.5 s")`. This is a product timeout, and a hang never answers, so the result does not depend on the clock.
  - `test_a_claude_that_exits_fails_its_model`: the reason starts with `claude exited`.
  - `test_the_token_never_reaches_a_reason` (RF1): `error-with-token` gives a reason without the token and with `***`.
  - `test_at_most_concurrency_checks_run_at_once` (RF5): monkeypatch `gateway_setup._check_model` with a coroutine that does `running += 1`, records the maximum, runs `await asyncio.sleep(0)` five times, then `running -= 1`. With 7 models the maximum is `3` (`concurrency=2` gives `2`). This is deterministic, because without the cap all 7 would start before any ends.
  - `test_a_check_that_raises_fails_only_its_model` (RF5): `(False, "check failed: RuntimeError: boom")` for that model; the others pass.
  - `test_report_follows_each_model`: one call per model; the last call has `done == total`.
- [ ] `git -C REPO add -N` gateway_setup.py and test_gateway_validation.py, `SYNC`, `RUN PKG/tests/test_gateway_validation.py`. Expected: FAIL.
- [ ] Implement as above.
- [ ] `SYNC`, RUN. Expected: PASS.
- [ ] Commit `SP/msg-t9.txt`: `feat(optio-claudecode): validate gateway models with one short real conversation each, three at once`.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F1: call `build_claude_flags(..., disallowed_tools=None, ...)` (a required keyword).
- F6: `_check_model` reads `is_error` from the `result` event itself (an `is_error` result also fires `on_message`, before the event dispatch), and the error test asserts the exact reason string.

### Task 10: the setup task

**Files:**
- Modify `PKG/src/optio_claudecode/gateway_setup.py`: `create_gateway_setup_task`.
- Modify `PKG/src/optio_claudecode/oauth.py:140`: `verify_and_refresh_seed` raises `ValueError("gateway seeds are tested by the setup task (create_gateway_setup_task)")` when `suffix == REQUESTY_SEED_SUFFIX`.
- Modify `PKG/tests/fake_claude.py`: knob `FAKE_CLAUDE_STARTED_DIR`, which touches `<dir>/<pid>` at start.
- Modify `PKG/AGENTS.md`: the setup task.
- Test: create `PKG/tests/test_gateway_setup.py`.

**Interfaces:** the contract's `create_gateway_setup_task(process_id, name, *, seed_id, prefix, suffix, encrypt, decrypt, ssh=None, on_seed_saved=None, retest=False, description=None, metadata=None, install_dir=None, per_model_timeout=90.0) -> TaskInstance` (`cancellable=True`, `supports_resume=False`).

**Algorithm (execute):**
1. Read `db = ctx._db` (Finding 7), `status = await gateway_seed_status(...)` and the seed.
2. Refuse a flow that does not fit: `retest` with `pending`, or not `retest` with anything other than `pending`, raises `ValueError`.
3. Validate every model in the seed, reporting `ctx.report_progress(100*done/total, f"{id}: passed")` or `f"{id}: failed ({reason})"`.
4. Build the updated seed: `dataclasses.replace(...)` with each model's `ok`/`failed` status and reason, and `tested_at = datetime.now(timezone.utc)`.
5. Write the result:

   | Flow | Condition | Effect | Status published |
   |---|---|---|---|
   | pending | default model passed | `update_gateway_seed(status="alive")`, then `on_seed_saved(seed_id, seed_summary(seed))` (sync or async) | `alive` |
   | pending | default model failed | `seeds.purge_seed`, then the task FAILS (see below) | none |
   | retest | some model passed | `update_gateway_seed(status="alive")`, then `on_seed_saved(seed_id, seed_summary(seed))` | `alive` |
   | retest | every model failed | `update_gateway_seed(status="dead")`, then `on_seed_saved(seed_id, seed_summary(seed))` | `dead` |

   Controller's reconciliation with excavator's plan (2026-11-17, R1 and R2): the browser cannot read a published result (it lives in memory only), so a removed pending seed must END THE PROCESS `failed`, with an error `RuntimeError(f"default model {default}: {reason}")` after the purge; nothing is published for it. And `on_seed_saved(seed_id, summary)` (two arguments, as optio's capture path already calls it) runs after every run that leaves the seed alive or dead, the retests included, so the consumer can resync tasks when a retest kills the seed. The per-model progress lines of step 3 are what the consumer shows.
6. Publish `{"seedId", "status", "models": [{"id", "status", "reason"}]}` for the alive and dead outcomes.
7. Pending flow: a cancellation or an exception before step 5 purges the seed in a `finally` (RF5).

- [ ] Write the failing tests. Each runs through `_make_optio`, `adhoc_define` and `launch_and_await_result(timeout=120)`, with `install_dir=str(claude_cache_dir)`:
```python
async def test_a_pending_seed_whose_default_passes_becomes_alive(...):
    # FAKE: {"prov-a/kimi-k3.1-pro": "pass", "glm-flash": "error"}
    assert result["status"] == "alive" and result["seedId"] == sid
    assert [(m["id"], m["status"]) for m in result["models"]] == [("prov-a/kimi-k3.1-pro", "ok"), ("glm-flash", "failed")]
    assert "404" in result["models"][1]["reason"]
    doc = await mongo_db[f"{prefix}_claudecode_requesty_seeds"].find_one()
    assert doc["status"] == "alive" and isinstance(doc["metadata"]["gateway"]["testedAt"], datetime)
    assert saved == [(sid, "requesty eu, default prov-a/kimi-k3.1-pro, key …9c2e")]
```
  Further tests:
  - `test_a_pending_seed_whose_default_fails_is_removed`: `launch_and_await_result` raises `ResultNotPublished`; the terminal record is `failed` and its error names the default model and its reason; the document is gone; the GridFS blob is gone; `saved == []`.
  - `test_test_again_marks_the_failing_model_and_keeps_the_seed_alive`: `retest=True` on an alive seed whose glm now fails gives `alive`, glm `failed`, and `saved == [(sid, <its summary>)]`.
  - `test_test_again_with_every_model_failing_marks_the_seed_dead`.
  - `test_the_flow_must_fit_the_seed_status`: both mismatches end the process `failed`, with `ValueError` in the error.
  - `test_a_cancelled_setup_removes_its_pending_seed` (RF5):
    1. Set FAKE to all `hang` and point `FAKE_CLAUDE_STARTED_DIR` at a directory.
    2. `await optio.launch(pid, session_id=None)`.
    3. Poll until a file appears in that directory (60 s ceiling).
    4. `await optio.cancel(pid)` and `_wait_terminal`.
    5. The seed document is gone and `os.listdir(task_root) == []`.
  - `test_verify_and_refresh_seed_refuses_a_gateway_seed`: `pytest.raises(ValueError, match="setup task")`.
- [ ] `git -C REPO add -N REPO/packages/optio-claudecode/tests/test_gateway_setup.py`, `SYNC`, `RUN PKG/tests/test_gateway_setup.py`. Expected: FAIL.
- [ ] Implement as above.
- [ ] `SYNC`, then `RUN PKG/tests/test_gateway_setup.py` and `RUN PKG/tests/test_oauth.py`. Expected: PASS.
- [ ] Commit `SP/msg-t10.txt`: `feat(optio-claudecode): the gateway setup task: test, then alive, removed or dead`.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F3: `test_the_flow_must_fit_the_seed_status` asserts the ValueError's message text in the terminal record's error (the record stores `str(e)`, no class name).
- F4: name T10's own fixture token, e.g. `TOKEN = "rq-setup-token-1234abcd"`, and assert the summary's key hint from its last four characters (`key …abcd`).
- F10: the pending flow keeps a `purged` flag; the `finally` purges only when not purged yet and tolerates `KeyError`, so the error text stays `default model <id>: <reason>`.
- Spec wording (both specs: a failing model is dropped / only passing models are saved): in the PENDING flow, failed models are NOT kept in the alive seed: it holds only the models that passed; their failures live in the progress lines (and in the failed process's error when the default failed). In a RETEST, a model that now fails stays in the seed with status `failed` (unusable). So `test_a_pending_seed_whose_default_passes_becomes_alive` asserts the document's `metadata.gateway.models` lists only kimi, while the published result's `models` lists both with their statuses.

### Task 11: registration in optio-agents-all and optio-agents-ui

**Files:**
- Modify `REPO/packages/optio-agents-all/src/optio_agents_all/types.py`: the `AgentType` literal and the union.
- Modify `REPO/packages/optio-agents-all/src/optio_agents_all/factory.py`:
  - `_REGISTRY`;
  - `async def _no_accounts(creds) -> AccountInfo: return EMPTY` in `_ANALYZE_REGISTRY`;
  - `_single(_no_accounts)` in `_ANALYZE_ACCOUNTS_REGISTRY`.
- Modify `REPO/packages/optio-agents-all/src/optio_agents_all/info.py`, `capabilities.py` (both sets) and `__init__.py` (exports).
- Modify `REPO/packages/optio-agents-all/AGENTS.md`: 8 slugs, the capability sets.
- Generated: `REPO/packages/optio-agents-ui/src/agents.generated.ts`.
- Modify `REPO/packages/optio-agents-ui/src/__tests__/agents.test.ts`.
- Test: `REPO/packages/optio-agents-all/tests/` `test_meta_factory.py`, `test_agent_info.py`, `test_capabilities.py`, `test_config_uniformity.py` and `test_analyze_dispatch.py`.

**Interfaces:** `create_task` dispatches `"claudecode_requesty"` to `create_claudecode_requesty_task`; `MCP_AGENT_TYPES == CUSTOM_CONTROLS_AGENT_TYPES == {"claudecode", "claudecode_requesty"}`.

- [ ] Write the failing tests:
  - in `test_meta_factory.py`: the union has 8 members; `aa.ClaudeCodeRequestyTaskConfig(consumer_instructions="x", fs_isolation=False, permission_mode="dontAsk").agent_type == "claudecode_requesty"`; `_REGISTRY["claudecode_requesty"] is aa.create_claudecode_requesty_task`;
  - in `test_agent_info.py`: `get_agent_info("claudecode_requesty").url == "https://requesty.ai"`;
  - in `test_capabilities.py`: both sets as above; `UNSUPPORTED_CONFIG_TYPES` is derived from `agent_type not in MCP_AGENT_TYPES`; the two "accepts" tests are parametrized over both Claude Code configs, with `permission_mode="dontAsk"`;
  - in `test_config_uniformity.py`: `_make` adds `{ClaudeCodeRequestyTaskConfig: {"permission_mode": "dontAsk"}}.get(cls, {})`;
  - in `test_analyze_dispatch.py`: `await analyze_account("claudecode_requesty", None) is EMPTY` and `await analyze_accounts("claudecode_requesty", None) == []`;
  - in `agents.test.ts`: EXPECTED gains `claudecode_requesty: { name: 'Claude Code + Requesty', url: 'https://requesty.ai' }`, and the title says 8 engines.
- [ ] `SYNC`, then `RUN REPO/packages/optio-agents-all/tests/test_meta_factory.py` (and the other four files, one RUN each). Expected: FAIL.
- [ ] Implement the Python side.
- [ ] Regenerate the TS catalog. This is the step `make codegen` runs (Makefile:94):
  - `SYNC`;
  - `RT py REPO/packages/optio-agents-ui/scripts/generate.py`;
  - `python3 SP/fetch_requesty.py optio packages/optio-agents-ui/src/agents.generated.ts`.

  Expected: `AgentType` gains `'claudecode_requesty'`.
- [ ] `SYNC`, the five RUNs, then `RT vitest:REPO/packages/optio-agents-ui REPO/packages/optio-agents-ui/src/__tests__/agents.test.ts` and `RT tsc:REPO/packages/optio-agents-ui`. Expected: PASS.
- [ ] Commit `SP/msg-t11.txt`: `feat(optio-agents-all,optio-agents-ui): register claudecode_requesty`.

**Controller rulings (pre-flight scan, 2026-11-17; binding over the text above where they differ):**
- F7: after the codegen step, build the package's dist in the mirror so excavator's TypeScript sees the new `AgentType`: `ssh -F /home/csillag/share/fleet-ssh/config excavator bash /home/csillag/requesty-work/bin/rt.sh optio build:packages/optio-agents-ui` (the package's own `tsc`; never pnpm inside the sibling optio copy). dist is not committed.

### Task 12: optio-conversation-ui, no effort control for a model without levels

**Files:**
- Test: `REPO/packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts`, after line 665.
- Modify `src/chat.ts:447-462` only if the test fails.

**Interfaces:**
- Consumes the engine's `x-optio-control-update` snapshot: Task 7 omits `reasoning_effort` for a model without levels.
- Produces no new API.

- [ ] Write the test:
```ts
it('a control snapshot without reasoning_effort drops the effort control (a gateway model without levels)', () => {
  const model = (value: string) => ({ id: 'model', kind: 'select', label: 'Model', value,
    options: [{ value: 'prov-a/kimi-k3.1-pro', label: 'prov-a/kimi-k3.1-pro' }, { value: 'glm-flash', label: 'glm-flash' }] });
  const start = { ...initialChatState, controls: [model('prov-a/kimi-k3.1-pro'),
    { id: 'reasoning_effort', kind: 'slider', label: 'Effort', value: 'high', levels: ['low', 'high'] }] } as ChatState;
  const s = reduceEvent(start, { type: 'x-optio-control-update', controls: [model('glm-flash')] }, 9);
  expect(s.controls.map((c) => c.id)).toEqual(['model']);
});
```
- [ ] `SYNC`, `RT vitest:REPO/packages/optio-conversation-ui REPO/packages/optio-conversation-ui/src/__tests__/claudecode-events.test.ts`. Expected: PASS, because `foldControlUpdate` replaces the controls with the engine's snapshot. On FAIL, fix `foldControlUpdate` and run again.
- [ ] Commit `SP/msg-t12.txt`: `test(optio-conversation-ui): a gateway model without effort levels shows no effort control`.

---

## Live test list (excavator host, the owner's key)

- `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` and its PID namespace under claustrum.
- Policy ids as `--model`.
- The Requesty response shapes (Finding 4).
- Task 5's effort case, if the probe could not run.
- A real snapshot holds neither the token nor its last 20 characters (Claude Code caches the tail of an approved `ANTHROPIC_API_KEY` in `.claude.json`; check that nothing similar happens for `ANTHROPIC_AUTH_TOKEN`).

## Self-review against the spec

**Coverage:**

| Spec item | Tasks |
|---|---|
| Agent type, iframe refusal, registration, codegen | 4, 11 |
| Seed: pending first, encrypted blob, non-secret metadata, never leased | 1, 2 |
| Launch environment, token only in the environment, nothing planted, `claude_config` still merged | 6, 7 |
| `--model` exact id | 7, 8 |
| Catalog from the seed, switches checked against it | 7 |
| Effort with and without levels, verified against the CLI | 5, 6, 7 |
| Resume | 8 |
| OAuth paths off, snapshot guard | 7, 8, 10 (`verify_and_refresh_seed`) |
| Requesty provider: merge, groups, `@eu`, borrowed levels, policies, typed errors | 3 |
| Validation: per-model conversation, Read-tool word, parallel cap 3, time limit, `probe_turn` reused | 9 |
| Setup task: load, validate with progress, write, alive or removed, `on_seed_saved`, Test again, dead | 10 |
| conversation-ui | 12 |
| Docs: package AGENTS.md (each task), optio-agents and optio-agents-all AGENTS.md, root AGENTS.md | each task, 1, 11, 4 |
| Spec testing list | 3, 5, 7, 8, 9, 10 |

**Name consistency:**
- The contract names are used verbatim: `GatewayModel`, `GatewayPreset`, `GatewaySeed`, `usable_models`, `Gateway*Error`, `write_gateway_seed`, `read_gateway_seed`, `update_gateway_seed`, `PROVIDER`, `REGIONS`, `ModelChoice`, `list_models`, `create_gateway_setup_task`, `validate_gateway_models`, `ClaudeCodeRequestyTaskConfig`, `create_claudecode_requesty_task`, `REQUESTY_AGENT_INFO`, `REQUESTY_SEED_SUFFIX`.
- The additions are listed in Finding 5.
- Helpers shared across tasks keep one name: `launch_model`, `launch_effort`, `gateway_catalog`, `gateway_launch_env`, `with_stdin_secrets`, `write_stdin_secrets`, `GATEWAY_ENV_REMOVE`, `_snapshot_guard_file`, `_check_model`, `CHECK_FILE`.

**Review Focus, where each is tested:**

| Item | Tasks |
|---|---|
| RF1 | 2, 3, 6, 7, 9 |
| RF2 | 1, 2, 7 |
| RF3 | 3, 5, 6, 7 |
| RF4 | 6, 8 |
| RF5 | 9, 10 |

### Critical Files for Implementation
- /home/csillag/requesty-work/optio/packages/optio-claudecode/src/optio_claudecode/session.py
- /home/csillag/requesty-work/optio/packages/optio-claudecode/src/optio_claudecode/conversation.py
- /home/csillag/requesty-work/optio/packages/optio-claudecode/src/optio_claudecode/types.py
- /home/csillag/requesty-work/optio/packages/optio-agents/src/optio_agents/seeds.py
- /home/csillag/requesty-work/optio/packages/optio-claudecode/tests/fake_claude.py
