# Deliverable metadata push: optio implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `on_deliverable` callback can take, as an optional fourth argument, a clamped list of `{key, value, source}` entries that says which agent and model delivered the file. The list is snapshotted when optio accepts the `DELIVERABLE:` line.

**Architecture:**
- A leaf module, `optio_agents.deliverable_meta`, holds the entry type, the key and source constants, `meta_get`, `clamp`, the entry builders, the snapshot and the one invocation helper.
- The shared driver `run_log_protocol_session` takes an optional provider. When it accepts a line it takes the snapshot (its own `origin`, `delivered_at` and `resumed` first), queues the list with the deliverable, and calls the consumer through `invoke_on_deliverable`. The claustrum notice uses the same helper, as the harness.
- optio-claudecode supplies the full provider: its conversation state, plus the seed's non-secret provider and region. The other six wrappers supply the base entries.

**Tech Stack:** Python 3.11 asyncio, pytest + pytest-asyncio, `optio-claudecode/tests/fake_claude.py`, and session tests with their own Mongo database each.

**Spec:** `/home/csillag/requesty-work/optio/docs/2026-11-18-deliverable-metadata-design.md` (owner-approved). Research with file:line evidence: `/home/csillag/requesty-work/notes/2026-11-18-deliverable-metadata-push.md`.

## Findings and decisions (read first)

1. **Measured (spec "to measure").** With the real CLI 2.1.326 through Requesty, three fields report the routed id (e.g. `lyceum/kimi-k3.1-pro`): `system/init.model`, every assistant `message.model`, and `result.modelUsage`. So the `agent`-source `model` is that id. The label comes from the catalog: `gateway_catalog` under a gateway, the CLI's model list natively.
2. **The provider never sees the seed.** The session hands the provider only `(seed.provider, seed.region)`. The token, `base_url` and the key hint are therefore out of its reach by construction. The tests still check this (RF1).
3. **The turn's model is the main conversation's.** An assistant event whose `parent_tool_use_id` is set (a subagent's message) moves neither `message_model` nor `turn_model`.
4. **The label belongs to the model the CLI reports.** It is the catalog entry for `turn_model`, matched by id or by `resolved`. Before the CLI has named a model, it is the entry for `conversation.model`, or for `default` when that is None. Its source is `seed` under a gateway and `agent` natively.
5. **Unknown values are left out.** There is no `optio` `model` entry while `conversation.model` is None, and no `agent.version` or `agent.session` before the first `system/init`. `effort` is the exception: in conversation mode it is always reported, and None means no explicit level.
6. **The claustrum notice** carries `origin` and `delivered_at`, both with source `harness`. It has no agent and no model entries.
7. **Additions beyond the binding names (nothing renamed):**
   - in `optio_agents.deliverable_meta`: `ORIGIN_AGENT = "agent"`, `ORIGIN_HARNESS = "harness"`, `MAX_ENTRIES = 32`, `MAX_VALUE_CHARS = 256`, `PROVIDER_TIMEOUT_S = 2.0`, `DEFAULT_PREFER`, `MetaValue`, `MetadataProvider`, `entry()`, `delivery_entries()` and `snapshot_metadata()`;
   - in optio-claudecode: `models.catalog_label()`, the conversation attributes `cli_session_id`, `cli_version`, `message_model` and `turn_model`, and the module `deliverable_facts`.
8. **Insights (spec decision 6) need no optio change.** Excavator's callback closure keeps the last delivery's list for its `after_execute`.
9. **A sync provider cannot be timed out.** A provider reads in-memory state only. Every provider in this round does.
10. **Import order.** `optio_agents/__init__.py` imports `protocol`, and `protocol/session.py` will import `deliverable_meta`. So `deliverable_meta` imports only submodules (`from optio_agents.agent_info import AgentInfo`), never `from optio_agents import ...`.

## Global Constraints

Values from the spec:
- **Entry.** `{key: str, value: str | int | float | bool | None, source: str}`. `source` is one of `config`, `optio`, `agent`, `seed`, `harness`.
- **Keys.** `origin` (`agent` / `harness`), `agent.type`, `agent.name`, `agent.version`, `agent.session`, `provider`, `gateway.region`, `model`, `model.label`, `effort`, `delivered_at` (ISO-8601 UTC), `resumed`.
  - `model` with source `optio` is what optio launched or switched to.
  - `model` with source `agent` is what the CLI reports.
- **Clamp.** At most 32 entries, each value at most 256 characters, scalars only.
- **Never included:**
  - the gateway token, `base_url`, the key hint;
  - any environment or `--settings` content;
  - account or personal details (codex account, OAuth seed info).
- **Snapshot time.** The snapshot is taken when the driver accepts a `DELIVERABLE:` line, not when the callback runs.
- **Failures.** A provider failure never fails a delivery: the driver's own entries are sent, and the failure is logged.
- **Calling the callback.** The metadata goes in only when the callback takes a fourth positional parameter, a parameter named `metadata`, or `*args`/`**kwargs`. Any other callback gets three arguments, as today.
- **Consumers never spell the key strings.** They use the constants exported from `optio_agents`.
- **Wrappers.** Claude Code (native and Requesty) gets the full provider. The other wrappers get the base entries only.

Repo and test rules:
- Edit in `/home/csillag/requesty-work/optio`, on the branch `csillag/deliverable-metadata`.
- No release and no version bumps (merge-before-release).
- Commits follow the repo style `feat(<pkg>): ...`, with no Co-Authored-By line. One commit per task.
- Tests use no network and do not depend on wall-clock time:
  - awaited conditions get 60 s ceilings;
  - a timeout test uses a provider that never answers.
- Each task runs only the test files it names, never the full suite.
- Logs name keys and exception types only, never a value or an exception's text.

## Procedures (every command is one plain call with literal absolute paths)

Abbreviations: write every one out literally in the actual call, because the shell rules forbid variables.
- `REPO` = `/home/csillag/requesty-work/optio`
- `AG` = `REPO/packages/optio-agents`
- `CC` = `REPO/packages/optio-claudecode`
- `SP` = `/tmp/claude-1000/-home-csillag-chat/b5ba32aa-520f-41ce-9bf9-2783e52c7ce6/scratchpad`

Procedures:
- **RT**: shorthand for `ssh -F /home/csillag/share/fleet-ssh/config excavator bash /home/csillag/requesty-work/bin/rt.sh optio`. Always write it out in full.
  - `RUN <file>` = `RT pytest <file> -q`: one file per call.
  - Paths are absolute (`REPO/...`). If rt.sh expects repo-relative paths, drop the `REPO/` prefix.
- **S (sync)**:
  1. For every new file, first run `git -C /home/csillag/requesty-work/optio add -N <abs path>`, one call per file. The sync copies tracked files only.
  2. Run `python3 /tmp/claude-1000/-home-csillag-chat/b5ba32aa-520f-41ce-9bf9-2783e52c7ce6/scratchpad/sync_requesty.py optio`.
  3. Every sync restarts the live dev stack's engine. Sync only right before a RUN, and not while the controller reports a live test in progress.
- **C (commit)**:
  1. Write the message to `SP/commit-msg.txt` with the Write tool.
  2. `git -C /home/csillag/requesty-work/optio add <the task's Files, absolute paths>`
  3. `git -C /home/csillag/requesty-work/optio commit -F /tmp/claude-1000/-home-csillag-chat/b5ba32aa-520f-41ce-9bf9-2783e52c7ce6/scratchpad/commit-msg.txt`

Shell rules, for briefs:
- One plain command per call, with no `;`, `&&`, `||`, pipes or `( )`.
- No `~`, globs, `$`, backticks or backslashes, and no `#`.
- No `cd`, and no code on the command line.
- Read files with the Read tool. Search with `grep -rn <word> <literal absolute dir>`.

## Review Focus

- **RF1: a secret in the metadata.** A sentinel gateway token never appears in the serialized metadata, and neither do its last four characters, `base_url` or any `ANTHROPIC_` name. Provider exception text never reaches a log. Tests in Tasks 3 and 5.
- **RF2: a provider that raises, hangs or answers garbage.** The delivery still happens, the callback gets the driver's entries, and the reply to the agent is unchanged. Tests in Task 3.
- **RF3: a three-argument callback.** It still gets exactly three arguments. This covers excavator's existing callbacks, the demo, and the claustrum path. Tests in Tasks 2, 3 and 7.
- **RF4: a live model switch.** A switch between the `DELIVERABLE:` line and the callback does not change the snapshot. A switch also does not clear the turn's reported model. Tests in Tasks 3 and 4.
- **RF5: account or personal data.** The metadata never carries it: providers emit exactly the listed entries, so config `env`, OAuth seed info and codex `account` cannot leak in. `clamp` drops extra keys inside an entry. Tests in Tasks 1, 5 and 6.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `AG/src/optio_agents/deliverable_meta.py` (new) | entry type, constants, `meta_get`, `clamp`, builders, `invoke_on_deliverable`, `snapshot_metadata` | 1, 2, 3 |
| `AG/src/optio_agents/__init__.py` | exports | 1, 2 |
| `AG/src/optio_agents/protocol/session.py` | `DeliverableCallback`; the driver's snapshot, queue and invocation | 2, 3 |
| `AG/src/optio_agents/claustrum.py` | the notice goes through `invoke_on_deliverable` as the harness | 2 |
| `CC/src/optio_claudecode/conversation.py` | keeps the CLI's session id, version, message model and turn model | 4 |
| `CC/src/optio_claudecode/models.py` | `catalog_label` | 5 |
| `CC/src/optio_claudecode/deliverable_facts.py` (new) | the Claude Code provider (a pure function) | 5 |
| `CC/src/optio_claudecode/session.py` | the gateway holder and the provider wiring | 5 |
| the six other wrappers' `session.py` | base entries | 6 |
| `REPO/AGENTS.md`, `REPO/docs/writing-agent-wrappers.md`, `optio-demo/.../_feedback.py` | docs, and the demo prints the metadata | 7 |

---

### Task 1: optio-agents: the entry type, constants, `meta_get`, `clamp`, the builders

**Files:**
- Create `AG/src/optio_agents/deliverable_meta.py`.
- Modify `AG/src/optio_agents/__init__.py`:
  - add `from optio_agents import deliverable_meta` and `from optio_agents.deliverable_meta import (...)` after the `mcp` import (line 41);
  - extend `__all__` (lines 89-169).
- Modify `AG/AGENTS.md`: a new section "Deliverable metadata", with the shape, the keys and sources, `meta_get` and the clamp limits.
- Test: create `AG/tests/test_deliverable_meta.py`; modify `AG/tests/test_package_exports.py`.

**Interfaces (produces):**
- `MetaValue = str | int | float | bool | None`
- `DeliverableMeta = TypedDict("DeliverableMeta", {"key": str, "value": MetaValue, "source": str})`
- `DeliverableMetadata = list[DeliverableMeta]`
- The twelve `KEY_*` and five `SOURCE_*` constants exactly as listed in the Global Constraints, plus `ORIGIN_AGENT` and `ORIGIN_HARNESS`.
- `MAX_ENTRIES = 32`, `MAX_VALUE_CHARS = 256`, `DEFAULT_PREFER = ("agent", "optio", "seed", "config", "harness")`.
- `def entry(key: str, value: MetaValue, source: str) -> DeliverableMeta`
- `def meta_get(md: DeliverableMetadata | None, key: str, prefer: tuple[str, ...] = DEFAULT_PREFER) -> MetaValue`:
  - the value of the first entry with that key whose source comes earliest in `prefer`;
  - sources outside `prefer` are ignored;
  - None when there is no such entry.
- `def clamp(md: DeliverableMetadata) -> DeliverableMetadata`:
  - returns new dicts holding only `key`, `value` and `source`;
  - drops non-dict entries, entries whose `key` or `source` is not a str, and entries whose value is not a scalar;
  - cuts string values to 256 characters;
  - keeps the first 32 in order;
  - never mutates its input.
- `def agent_entries(config, info: AgentInfo) -> DeliverableMetadata`: `[agent.type = config.agent_type, agent.name = info.name]`, both with source `config`.
- `def delivery_entries(*, origin: str, source: str, resumed: bool | None, now: datetime | None = None) -> DeliverableMetadata`:
  - `origin`, then `delivered_at` = `(now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="milliseconds")`, then `resumed` when it is not None;
  - all with `source`.
- Top-level exports: the module `deliverable_meta`, `DeliverableMeta`, `DeliverableMetadata`, all `KEY_*`, `SOURCE_*` and `ORIGIN_*` constants, `meta_get`, `clamp` and `agent_entries`.

- [ ] **Step 1: Write the failing tests** in `test_deliverable_meta.py`:
```python
MD = [entry("model", "claude-opus-5", "optio"), entry("model", "lyceum/kimi-k3.1-pro", "agent"),
      entry("model.label", "Kimi K3.1 Pro", "seed")]

def test_the_key_and_source_names():
    assert (KEY_ORIGIN, KEY_AGENT_TYPE, KEY_AGENT_NAME, KEY_AGENT_VERSION, KEY_AGENT_SESSION, KEY_PROVIDER,
            KEY_GATEWAY_REGION, KEY_MODEL, KEY_MODEL_LABEL, KEY_EFFORT, KEY_DELIVERED_AT, KEY_RESUMED) == (
        "origin", "agent.type", "agent.name", "agent.version", "agent.session", "provider",
        "gateway.region", "model", "model.label", "effort", "delivered_at", "resumed")
    assert (SOURCE_CONFIG, SOURCE_OPTIO, SOURCE_AGENT, SOURCE_SEED, SOURCE_HARNESS) == (
        "config", "optio", "agent", "seed", "harness")

def test_meta_get_picks_by_source_preference():
    assert meta_get(MD, KEY_MODEL) == "lyceum/kimi-k3.1-pro"
    assert meta_get(MD, KEY_MODEL, prefer=(SOURCE_OPTIO, SOURCE_AGENT)) == "claude-opus-5"
    assert meta_get(MD, KEY_MODEL, prefer=(SOURCE_SEED,)) is None
    assert meta_get(MD, KEY_MODEL_LABEL) == "Kimi K3.1 Pro"
    assert meta_get(MD, KEY_AGENT_VERSION) is None
    assert meta_get(None, KEY_MODEL) is None and meta_get([], KEY_MODEL) is None
    assert meta_get([entry("model", "a", "agent"), entry("model", "b", "agent")], KEY_MODEL) == "a"

def test_clamp_keeps_scalars_only_and_only_the_three_fields():        # RF5
    md = [entry("a", "x", "optio"), {"key": "b", "value": {"x": 1}, "source": "agent"},
          {"key": "c", "value": [1], "source": "agent"}, entry("d", 3, "agent"), entry("e", 1.5, "agent"),
          entry("f", True, "agent"), entry("g", None, "agent"), "junk", {"key": 1, "value": "x", "source": "agent"},
          {"key": "h", "value": "y", "source": "agent", "token": "rq-sentinel"}]
    assert clamp(md) == [entry("a", "x", "optio"), entry("d", 3, "agent"), entry("e", 1.5, "agent"),
                         entry("f", True, "agent"), entry("g", None, "agent"), entry("h", "y", "agent")]
```
  Further tests:
  - `test_clamp_cuts_values_and_the_count`:
    - `clamp([entry("a", "x" * 300, "agent")])[0]["value"] == "x" * 256`;
    - 40 entries keep `k0`…`k31`;
    - the input list and its dicts are unchanged afterwards.
  - `test_agent_entries`: `agent_entries(SimpleNamespace(agent_type="claudecode_requesty"), AgentInfo("claudecode_requesty", "Claude Code + Requesty", "https://requesty.ai"))` equals `[entry("agent.type", "claudecode_requesty", "config"), entry("agent.name", "Claude Code + Requesty", "config")]`.
  - `test_delivery_entries`, with `now = datetime(2026, 11, 18, 9, 30, 5, 123000, tzinfo=timezone.utc)`:
    - `delivery_entries(origin=ORIGIN_AGENT, source=SOURCE_OPTIO, resumed=True, now=now)` equals `[entry("origin", "agent", "optio"), entry("delivered_at", "2026-11-18T09:30:05.123+00:00", "optio"), entry("resumed", True, "optio")]`;
    - with `origin=ORIGIN_HARNESS, source=SOURCE_HARNESS, resumed=None` the keys are `["origin", "delivered_at"]`.
  - In `test_package_exports.py`, `test_top_level_exports_deliverable_metadata`: `meta_get`, `clamp`, `agent_entries`, `DeliverableMetadata`, `KEY_MODEL`, `SOURCE_AGENT` and `ORIGIN_HARNESS` are attributes of `optio_agents`.
- [ ] **Step 2:** S (`add -N` for both new files), then `RUN AG/tests/test_deliverable_meta.py` and `RUN AG/tests/test_package_exports.py`. Expected: FAIL (ImportError).
- [ ] **Step 3:** Implement as above. Follow Finding 10 (submodule imports only), and write the AGENTS.md section.
- [ ] **Step 4:** S, then both RUNs. Expected: PASS.
- [ ] **Step 5:** C with the message `feat(optio-agents): deliverable metadata entries, key constants, meta_get and clamp`.

### Task 2: optio-agents: `invoke_on_deliverable`, the widened callback type, the claustrum notice

**Files:**
- Modify `AG/src/optio_agents/deliverable_meta.py`: `invoke_on_deliverable`.
- Modify `AG/src/optio_agents/protocol/session.py:107-122`: `DeliverableCallback` and its docstring.
- Modify `AG/src/optio_agents/claustrum.py:187-206`: `emit_claustrum_update_notice`.
- Modify `AG/src/optio_agents/__init__.py`: export `invoke_on_deliverable`.
- Modify `AG/AGENTS.md`: the "Deliverable metadata" section gets the calling rule.
- Test: `AG/tests/test_deliverable_meta.py`, `AG/tests/test_claustrum_wrap_notice.py`, `AG/tests/test_package_exports.py`.

**Interfaces:**
- Consumes `entry`, `delivery_entries`, `ORIGIN_HARNESS`, `SOURCE_HARNESS` and `DeliverableMetadata` (Task 1).
- Produces `async def invoke_on_deliverable(callback: Callable[..., Awaitable[str | None]], hook_ctx, path: str, text: str, metadata: DeliverableMetadata) -> str | None`. It awaits and returns the callback's result.
- How the metadata is passed is decided once per callback by `_metadata_mode(callback) -> Literal["positional", "keyword", "none"]`. The result is cached in a module `weakref.WeakKeyDictionary`; a callable that cannot be weakly referenced is inspected each time.

  | `inspect.signature(callback)` | Mode | Call |
  |---|---|---|
  | raises `ValueError` or `TypeError` | `none` | `cb(h, p, t)` |
  | 4 or more POSITIONAL_ONLY / POSITIONAL_OR_KEYWORD parameters, or a VAR_POSITIONAL | `positional` | `cb(h, p, t, md)` |
  | otherwise a KEYWORD_ONLY parameter named `metadata`, or a VAR_KEYWORD | `keyword` | `cb(h, p, t, metadata=md)` |
  | otherwise | `none` | `cb(h, p, t)` |

- Produces `DeliverableCallback = Callable[["HookContext", str, str], Awaitable["str | None"]] | Callable[["HookContext", str, str, DeliverableMetadata], Awaitable["str | None"]]`. Every wrapper's `on_deliverable` field already uses this alias.
- Produces: `emit_claustrum_update_notice` keeps its signature. It calls `invoke_on_deliverable(on_deliverable, hook_ctx, rel, text, clamp(delivery_entries(origin=ORIGIN_HARNESS, source=SOURCE_HARNESS, resumed=None)))`.

- [ ] **Step 1: Write the failing tests:**
```python
async def _three(h, p, t): CALLS.append((h, p, t)); return "r3"
async def _four(h, p, t, md): CALLS.append((h, p, t, md))
async def _excavator_form(h, p, t, metadata=None): CALLS.append((h, p, t, metadata))
async def _kw_only(h, p, t, *, metadata): CALLS.append((h, p, t, {"metadata": metadata}))
async def _var_args(*args): CALLS.append(args)
async def _var_kwargs(h, p, t, **kw): CALLS.append((h, p, t, kw))
class _Bound:
    async def cb(self, h, p, t): CALLS.append((h, p, t))
async def _extra_first(extra, h, p, t, md): CALLS.append((h, p, t, md))

MDX = [entry("model", "m", "agent")]

@pytest.mark.parametrize("cb, expected", [
    (_three, ("H", "p", "t")),                                          # RF3
    (_Bound().cb, ("H", "p", "t")),                                     # RF3
    (_four, ("H", "p", "t", MDX)), (_excavator_form, ("H", "p", "t", MDX)),
    (_kw_only, ("H", "p", "t", {"metadata": MDX})), (_var_args, ("H", "p", "t", MDX)),
    (_var_kwargs, ("H", "p", "t", {"metadata": MDX})), (functools.partial(_extra_first, "x"), ("H", "p", "t", MDX)),
])
async def test_the_metadata_goes_only_to_a_callback_that_takes_it(cb, expected):
    CALLS.clear()
    await invoke_on_deliverable(cb, "H", "p", "t", MDX)
    assert CALLS == [expected]
```
  Further tests:
  - `test_the_callbacks_answer_is_returned`: `await invoke_on_deliverable(_three, ...) == "r3"`.
  - `test_the_signature_is_read_once_per_callback`: monkeypatch `inspect.signature` with a counting wrapper. Two invocations of the same `_four` give a count of 1.
  - `test_an_unreadable_signature_gets_three_arguments`: monkeypatch `inspect.signature` to raise `ValueError` for a fresh callback, which is then called with 3 arguments.
  - In `test_claustrum_wrap_notice.py`:
    - `test_the_notice_reaches_a_metadata_callback_as_the_harness`: `async def on_deliverable(ctx, rel, text, metadata=None)` receives keys `["origin", "delivered_at"]`, `meta_get(md, KEY_ORIGIN) == ORIGIN_HARNESS`, and `{e["source"] for e in md} == {SOURCE_HARNESS}`.
    - The existing three-argument `test_emit_notice_writes_calls_and_cleans_up` stays unchanged and green (RF3).
  - In `test_package_exports.py`: `invoke_on_deliverable` is exported, and `len(typing.get_args(optio_agents.DeliverableCallback)) == 2`.
- [ ] **Step 2:** S, then `RUN AG/tests/test_deliverable_meta.py`, `RUN AG/tests/test_claustrum_wrap_notice.py` and `RUN AG/tests/test_package_exports.py`. Expected: FAIL.
- [ ] **Step 3:** Implement as above.
- [ ] **Step 4:** S, then the three RUNs. Expected: PASS.
- [ ] **Step 5:** C with the message `feat(optio-agents): on_deliverable may take the metadata as a fourth argument; the claustrum notice sends it as the harness`.

### Task 3: optio-agents: the driver snapshots at line acceptance

**Files:**
- Modify `AG/src/optio_agents/deliverable_meta.py`: `PROVIDER_TIMEOUT_S`, `MetadataProvider` and `snapshot_metadata`.
- Modify `AG/src/optio_agents/protocol/session.py`:
  - the `run_log_protocol_session` signature and docstring (172-247);
  - the queue type (302-304);
  - the `_tail_and_dispatch` task creation (318-324);
  - `_tail_and_dispatch` (423-473);
  - `_deliverable_fetch_loop` (501-578).
- Modify `AG/tests/test_deliverable_routing.py:31`: the queued item becomes `("/abs/x.md", "x.md", [])`.
- Modify `AG/AGENTS.md`: the "Deliverable metadata" section gets the snapshot time, the failure rule and the provider contract.
- Test: create `AG/tests/test_deliverable_snapshot.py`.

**Interfaces:**
- Consumes `delivery_entries`, `clamp`, `ORIGIN_AGENT`, `SOURCE_OPTIO` (Task 1) and `invoke_on_deliverable` (Task 2).
- `MetadataProvider = Callable[[], Awaitable[DeliverableMetadata] | DeliverableMetadata]`, and `PROVIDER_TIMEOUT_S = 2.0`.
- `async def snapshot_metadata(provider: MetadataProvider | None, *, resumed: bool, timeout: float = PROVIDER_TIMEOUT_S, now: datetime | None = None) -> DeliverableMetadata`:
  - It returns `clamp(delivery_entries(origin=ORIGIN_AGENT, source=SOURCE_OPTIO, resumed=resumed, now=now) + provided)`.
  - The provider's result is awaited under `asyncio.wait_for(..., timeout)` when it is awaitable.
  - These count as failures, and leave `provided = []`: an `Exception` (a timeout included), and a result that is not a list.
  - A failure is logged at WARNING with the exception's type name and the keys sent, never its text or any value. `CancelledError` propagates.
- `run_log_protocol_session(..., keywords: bool = True, deliverable_metadata: MetadataProvider | None = None)`.
- `_tail_and_dispatch(..., browser_url_rewrite=None, deliverable_metadata: MetadataProvider | None = None)`:
  - After `Deliverable: <display>` is reported, it calls `snapshot_metadata(deliverable_metadata, resumed=bool(getattr(ctx, "resume", False)))` and queues `(absolute, display, metadata)`.
  - Rejected lines take no snapshot.
- `_deliverable_fetch_loop` unpacks the three values and calls `invoke_on_deliverable(callback, hook_ctx, display, text, metadata)`. The reply rules are unchanged.

- [ ] **Step 1: Write the failing tests** in `test_deliverable_snapshot.py`. Use a fake host with `workdir = "/wd"`, `tail_file` yielding given lines and `fetch_bytes_from_host` returning `b"body"`; copy the shapes from `test_client_directed_dispatch.py` and `test_deliverable_routing.py`, since the tests directory is not a package. The ctx has `report_progress`, `resume` and (for the LocalHost session tests) `should_continue`.
```python
async def test_a_switch_after_the_line_does_not_change_the_snapshot():          # RF4
    state = {"model": "lyceum/kimi-k3.1-pro"}
    provider = lambda: [entry(KEY_MODEL, state["model"], SOURCE_AGENT)]
    q = asyncio.Queue()
    host = _Host(["DELIVERABLE: ./deliverables/x.md\n", "DONE\n"])
    await S._tail_and_dispatch(host, _Ctx(resume=False), q, asyncio.Queue(), asyncio.Event(), [],
                               get_protocol().parse_log_line, _hook_ctx(), None, deliverable_metadata=provider)
    state["model"] = "claude-opus-5"                         # the operator switches before the callback runs
    got = []
    async def cb(h, p, t, metadata=None): got.append(metadata)
    loop = asyncio.create_task(S._deliverable_fetch_loop(host, cb, q, _Ctx(resume=False), _hook_ctx()))
    await q.join(); loop.cancel()
    assert meta_get(got[0], KEY_MODEL) == "lyceum/kimi-k3.1-pro"
    assert [e["key"] for e in got[0]][:3] == ["origin", "delivered_at", "resumed"]

async def test_a_raising_provider_still_delivers_and_logs_no_value(caplog):     # RF1, RF2
    def provider(): raise RuntimeError("rq-sentinel-token-0000")
    md = await snapshot_metadata(provider, resumed=False)
    assert [e["key"] for e in md] == ["origin", "delivered_at", "resumed"]
    assert "rq-sentinel-token-0000" not in caplog.text and "RuntimeError" in caplog.text
```
  Further tests:
  - `test_the_drivers_entries_come_first`: a sync provider returning `[entry("model", "m", "optio")]` with a fixed `now` gives the keys `["origin", "delivered_at", "resumed", "model"]`, `origin == "agent"` and `resumed is True` when `resumed=True`.
  - `test_an_async_provider_is_awaited`.
  - `test_a_hanging_provider_times_out` (RF2): `async def provider(): await asyncio.Event().wait()` with `timeout=0.05` gives the driver's three entries. The provider never answers, so the result does not depend on the clock.
  - `test_a_non_list_answer_counts_as_a_failure` (RF2): `{"model": "x"}` gives the driver's three entries.
  - `test_without_a_provider_only_the_drivers_entries`.
  - `test_a_session_delivers_the_snapshot` (LocalHost under `tmp_workdir`), parametrized over a three-argument and a four-argument callback (RF3):
    - The body writes `deliverables/x.md`, then appends `DELIVERABLE: ./deliverables/x.md` and `DONE` to `optio.log`.
    - The ctx has `resume=True`.
    - The four-argument callback gets `resumed` True, `origin` `agent` and the provider's entry. The three-argument one is called with exactly three.
  - `test_a_session_whose_provider_hangs_still_delivers` (RF2): the same body with a provider that never answers. The callback is called once, with the driver's three entries; the real 2 s limit applies.
- [ ] **Step 2:** S (`add -N` for the new file), then `RUN AG/tests/test_deliverable_snapshot.py`. Expected: FAIL.
- [ ] **Step 3:** Implement as above.
- [ ] **Step 4:** S, then `RUN AG/tests/test_deliverable_snapshot.py`, `RUN AG/tests/test_deliverable_routing.py`, `RUN AG/tests/test_client_directed_dispatch.py` and `RUN AG/tests/test_driver_preserves_workdir.py`. Expected: PASS.
- [ ] **Step 5:** C with the message `feat(optio-agents): the driver snapshots deliverable metadata when it accepts a DELIVERABLE line`.

### Task 4: optio-claudecode: the conversation keeps what the CLI reports

**Files:**
- Modify `CC/src/optio_claudecode/conversation.py`:
  - attributes after line 141;
  - the `system/init` branch of `_route` (266-274);
  - a new `assistant` branch before line 302.
- Modify `CC/tests/fake_claude.py`, as the real CLI does:
  - `init_event()` (412-414) gains `"claude_code_version": "2.1.326"`;
  - the reply's assistant event (565-567) gains `"model": running_model` in `message`;
  - the docstring (352-356) says so.
- Test: `CC/tests/test_live_switch.py`, in the section "what the running claude reports" (after line 341).

**Interfaces (produces), on `ClaudeCodeConversation`:**
- `cli_session_id: str | None` and `cli_version: str | None`, from `system/init` `session_id` and `claude_code_version`.
- `message_model: str | None`: the last `message.model` of a main-thread assistant event.
- `turn_model: str | None`: the latest of `system/init.model` and a main-thread `message.model`.
- Only non-empty strings update these four. An assistant event with a non-null `parent_tool_use_id` updates none of them (Finding 3). Neither `attach()` nor `apply_settings` clears any of them, while `runtime_model` keeps today's clearing.

- [ ] **Step 1: Write the failing tests:**
```python
async def test_the_stream_names_the_session_version_and_the_turns_model():
    conv, handle, reader = _conv(model="sonnet")
    conv._route({"type": "system", "subtype": "init", "model": "lyceum/kimi-k3.1-pro",
                 "session_id": "s-1", "claude_code_version": "2.1.326"})
    assert (conv.cli_session_id, conv.cli_version, conv.turn_model) == ("s-1", "2.1.326", "lyceum/kimi-k3.1-pro")
    conv._route({"type": "assistant", "parent_tool_use_id": None,
                 "message": {"model": "lyceum/kimi-k3.1-pro-0915", "content": []}})
    assert (conv.message_model, conv.turn_model) == ("lyceum/kimi-k3.1-pro-0915",) * 2
    await _done(handle, reader)

async def test_a_switch_keeps_the_turn_model_until_the_next_turn_names_one():   # RF4
    conv, handle, reader = _conv(model="opus[1m]", effort="high")
    conv._route({"type": "system", "subtype": "init", "model": "claude-opus-5[1m]"})
    switch = asyncio.create_task(conv.apply_settings(model="sonnet"))
    _answer(handle, await _next_request(handle)); await _answer_readback(handle)
    await asyncio.wait_for(switch, 60)
    assert conv.runtime_model is None and conv.turn_model == "claude-opus-5[1m]"
    conv._route({"type": "system", "subtype": "init", "model": "claude-sonnet-5"})
    assert conv.turn_model == "claude-sonnet-5"
    await _done(handle, reader)
```
  Further tests:
  - `test_a_subagents_message_leaves_the_turn_model`: with `parent_tool_use_id: "toolu_1"` and `model: "claude-haiku-4-5"`, `turn_model` and `message_model` are unchanged.
  - `test_a_new_process_keeps_the_cli_facts`: a bare `ClaudeCodeConversation()` attached to a `_FakeHandle()`, routed one init, then `attach(_FakeHandle())` again. `cli_session_id`, `cli_version` and `turn_model` are unchanged, and `runtime_model is None`.
- [ ] **Step 2:** S, then `RUN CC/tests/test_live_switch.py`. Expected: FAIL (AttributeError).
- [ ] **Step 3:** Implement as above. The fake changes are additive fields.
- [ ] **Step 4:** S, then `RUN CC/tests/test_live_switch.py`, `RUN CC/tests/test_conversation_session.py` and `RUN CC/tests/test_conversation_ui_session.py`; the last two are the users of the changed fake. Expected: PASS.
- [ ] **Step 5:** C with the message `feat(optio-claudecode): the conversation keeps the CLI's session id, version and per-turn model`.

### Task 5: optio-claudecode: the provider and its wiring

**Files:**
- Create `CC/src/optio_claudecode/deliverable_facts.py`.
- Modify `CC/src/optio_claudecode/models.py`: `catalog_label`, after `offers` (106-109).
- Modify `CC/src/optio_claudecode/session.py`:
  - an outer `gateway_facts: tuple[str, str] | None = None`, next to `resolved_seed_id` (238);
  - add `gateway_facts` to `_conversation_body`'s `nonlocal` (516-517), and set `gateway_facts = (gateway_seed.provider, gateway_seed.region)` right after the seed load (529-530);
  - the driver call (1221-1232) gains `deliverable_metadata=lambda: deliverable_facts.deliverable_metadata(config, conversation, gateway_facts)`.
- Modify `CC/AGENTS.md`: a section "Deliverable metadata". It lists the entries per mode, the label rule, what is never read (the seed object, env, settings, account info), and the four conversation attributes.
- Test: create `CC/tests/test_deliverable_facts.py` (unit) and `CC/tests/test_deliverable_metadata_session.py` (`pytestmark = pytest.mark.serial`).

**Interfaces:**
- Consumes `entry`, `agent_entries`, `DeliverableMetadata` and the constants (Task 1); the four conversation attributes (Task 4); `run_log_protocol_session(deliverable_metadata=...)` (Task 3).
- `models.catalog_label(catalog: list[dict] | None, value: str | None) -> str | None`: the `label` of `_by_id(catalog, value) or _resolving_to(catalog, value)`, or None.
- `deliverable_facts.deliverable_metadata(config: ClaudeCodeTaskConfig, conversation: ClaudeCodeConversation | None, gateway: tuple[str, str] | None) -> DeliverableMetadata`, building entries in this order:
  1. `agent_entries(config, info)`, where `info = REQUESTY_AGENT_INFO if config.agent_type == REQUESTY_AGENT_INFO.slug else AGENT_INFO`.
  2. When `conversation` is None (iframe mode), only `model` = `config.model` (source `config`), and only when it is set. Stop there.
  3. `agent.version` = `cli_version` and `agent.session` = `cli_session_id`, both source `agent`, each when set.
  4. `model` = `conversation.model` (source `optio`) when set, then `model` = `turn_model` (source `agent`) when set.
  5. `model.label` = `catalog_label(conversation.models_offered, turn_model)` when `turn_model` is set; otherwise `catalog_label(..., conversation.model or cc_models.DEFAULT_MODEL)`. Its source is `seed` when `gateway` is set, else `agent`, and the entry is present only when a label was found.
  6. `effort` = `conversation.effort` (source `optio`), always, None included.
  7. When `gateway` is set: `provider` = `gateway[0]` and `gateway.region` = `gateway[1]`, both source `seed`.

- [ ] **Step 1: Write the failing unit tests** in `test_deliverable_facts.py`. `_conv(**attrs)` builds a bare `ClaudeCodeConversation()` and sets attributes (no handle).
```python
GW_CATALOG = [{"id": "lyceum/kimi-k3.1-pro", "label": "Kimi K3.1 Pro", "effort": ["low", "high"]},
              {"id": "glm-flash", "label": "GLM Flash"}]

def test_a_gateway_conversation_reports_exactly_these_entries():                  # RF1, RF5
    cfg = ClaudeCodeRequestyTaskConfig(consumer_instructions="x", fs_isolation=False, permission_mode="dontAsk")
    conv = _conv(models_offered=GW_CATALOG, model="lyceum/kimi-k3.1-pro", effort="high",
                 cli_session_id="s-1", cli_version="2.1.326", turn_model="lyceum/kimi-k3.1-pro")
    assert deliverable_metadata(cfg, conv, ("requesty", "eu")) == [
        entry("agent.type", "claudecode_requesty", "config"), entry("agent.name", "Claude Code + Requesty", "config"),
        entry("agent.version", "2.1.326", "agent"), entry("agent.session", "s-1", "agent"),
        entry("model", "lyceum/kimi-k3.1-pro", "optio"), entry("model", "lyceum/kimi-k3.1-pro", "agent"),
        entry("model.label", "Kimi K3.1 Pro", "seed"), entry("effort", "high", "optio"),
        entry("provider", "requesty", "seed"), entry("gateway.region", "eu", "seed")]

def test_a_native_conversation_labels_the_model_the_cli_reports():               # RF5
    cfg = ClaudeCodeTaskConfig(consumer_instructions="x", mode="conversation", permission_mode="bypassPermissions",
                               fs_isolation=False, env={"OPERATOR": "Jane Doe <jane@x.com>"})
    conv = _conv(models_offered=cc_models.parse_cli_models(CLI_MODELS), model=None, effort=None,
                 cli_session_id="s-2", cli_version="2.1.326", turn_model="claude-opus-5[1m]")
    md = deliverable_metadata(cfg, conv, None)
    assert md == [entry("agent.type", "claudecode", "config"), entry("agent.name", "Claude Code", "config"),
                  entry("agent.version", "2.1.326", "agent"), entry("agent.session", "s-2", "agent"),
                  entry("model", "claude-opus-5[1m]", "agent"), entry("model.label", "Opus (1M context)", "agent"),
                  entry("effort", None, "optio")]
    assert "jane@x.com" not in json.dumps(md)
```
  Further tests:
  - `test_before_the_cli_names_a_model_optios_values_stand`: `model="sonnet"`, with `turn_model`, `cli_version` and `cli_session_id` all None, gives agent entries, `entry("model", "sonnet", "optio")`, `entry("model.label", "Sonnet", "agent")` and `entry("effort", None, "optio")`.
  - `test_iframe_mode_reports_the_configured_model`: `deliverable_metadata(ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False, model="opus"), None, None)` gives agent entries plus `entry("model", "opus", "config")`; with `model=None`, agent entries only.
  - `test_catalog_label`: `catalog_label(CATALOG, "claude-sonnet-5") == "Sonnet"`, `catalog_label(CATALOG, "x") is None`, and `catalog_label(None, "sonnet") is None`.
- [ ] **Step 2: Write the failing session tests** in `test_deliverable_metadata_session.py`:
  - Reuse `_gw_task`, `TOKEN`, `KIMI` and `GLM` from `test_gateway_session.py`, and `_make_optio`, `_ui_config`, `_remember_workdir`, `_replies` and `_wait_terminal` from `test_conversation_ui_session.py`.
  - Helper `_deliver(workdir: str, rel: str) -> None`: writes `<workdir>/deliverables/<rel>`, then appends `DELIVERABLE: ./deliverables/<rel>\n` to `<workdir>/optio.log`.
  - Each test first completes one turn (`texts, replied = _replies(conv); await conv.send("ping"); await asyncio.wait_for(replied.wait(), 60)`), so the stream's init has been routed. Then it calls `_deliver`, and takes `(path, md)` from an `asyncio.Queue` that `on_deliverable(hook_ctx, path, text, metadata=None)` fills (60 s ceiling).
```python
async def test_a_gateway_deliverable_carries_the_seeds_label_and_no_secret(       # RF1
        shim_install_dir, claude_cache_dir, task_root, mongo_db, caplog):
    ...  # _gw_task(..., models=(replace(KIMI, label="Kimi K3.1 Pro"), GLM), on_deliverable=..., before_execute=_remember_workdir(seen))
    assert path == "recipes/r.md"
    assert meta_get(md, KEY_MODEL) == KIMI.id == meta_get(md, KEY_MODEL, prefer=(SOURCE_OPTIO,))
    assert meta_get(md, KEY_MODEL_LABEL) == "Kimi K3.1 Pro"
    assert (meta_get(md, KEY_PROVIDER), meta_get(md, KEY_GATEWAY_REGION)) == ("requesty", "eu")
    assert (meta_get(md, KEY_AGENT_VERSION), meta_get(md, KEY_AGENT_SESSION)) == ("2.1.326", "fake-session-0000")
    assert (meta_get(md, KEY_ORIGIN), meta_get(md, KEY_RESUMED)) == ("agent", False)
    dumped = json.dumps(md)
    for secret in (TOKEN, TOKEN[-4:], "https://router.eu.requesty.ai", "ANTHROPIC_"):
        assert secret not in dumped
    ...  # conv.close(), _wait_terminal, optio.shutdown
    assert TOKEN not in caplog.text
```
  Further test:
  - `test_a_native_deliverable_names_the_model_the_cli_runs`: `_ui_config(..., on_deliverable=..., before_execute=_remember_workdir(seen))`. The metadata has:
    - agent type `claudecode` and name `Claude Code`;
    - agent `model` `claude-opus-5[1m]` (the fake's default), label `Opus (1M context)`, version `2.1.326`;
    - no `provider` and no `gateway.region` entry.
- [ ] **Step 3:** S (`add -N` for the three new files), then `RUN CC/tests/test_deliverable_facts.py` and `RUN CC/tests/test_deliverable_metadata_session.py`. Expected: FAIL.
- [ ] **Step 4:** Implement as above.
- [ ] **Step 5:** S, then both RUNs and `RUN CC/tests/test_gateway_session.py`. Expected: PASS.
- [ ] **Step 6:** C with the message `feat(optio-claudecode): deliverable metadata from the conversation and the gateway seed`.

### Task 6: the other six wrappers send the base entries

**Files:**
- Modify the driver call of each wrapper's `session.py` (add one keyword): `optio-opencode` (714-724), `optio-codex` (696-706), `optio-cursor` (863-873), `optio-grok` (814-824), `optio-kimicode` (849-859) and `optio-antigravity` (644-654). Each passes `deliverable_metadata=lambda: agent_entries(config, AGENT_INFO)`; `AGENT_INFO` is already imported in each.
- Tests:
  - modify `REPO/packages/optio-cursor/tests/test_caller_message_wiring.py` and `REPO/packages/optio-antigravity/tests/test_ported_features.py`;
  - create `tests/test_deliverable_metadata_wiring.py` in `optio-opencode`, `optio-codex`, `optio-grok` and `optio-kimicode`.

**Interfaces:**
- Consumes `agent_entries`, `entry` and the constants (Task 1), and `run_log_protocol_session(deliverable_metadata=...)` (Task 3).
- Produces: every wrapper's deliveries carry `agent.type`, `agent.name`, `origin`, `delivered_at` and `resumed`. Model entries are out of scope this round.

- [ ] **Step 1: Write the failing tests.**
  - Cursor: reuse `_capture_wiring` in `test_caller_message_wiring.py`.
  - Antigravity: reuse the `fake_run` pattern of `test_caller_message_flags_flow_to_protocol`.
  - The four new files copy cursor's `_capture_wiring` shape: they stub `_build_host`, `get_protocol` and `run_log_protocol_session`, plus anything else the wrapper's outer shell awaits before the driver.
  - Each package's config is the smallest valid one, built as that package's existing tests build it.
```python
async def test_the_driver_gets_the_base_deliverable_entries():                   # RF5
    config = CursorTaskConfig(consumer_instructions="x", delivery_type="audit")
    _proto, run = await _capture_wiring(config)
    assert run["deliverable_metadata"]() == [
        entry(KEY_AGENT_TYPE, config.agent_type, SOURCE_CONFIG), entry(KEY_AGENT_NAME, AGENT_INFO.name, SOURCE_CONFIG)]
    assert config.agent_type == AGENT_INFO.slug
```
- [ ] **Step 2:** S (`add -N` for the four new files), then one RUN per test file: `REPO/packages/optio-cursor/tests/test_caller_message_wiring.py`, `REPO/packages/optio-antigravity/tests/test_ported_features.py`, and `REPO/packages/<pkg>/tests/test_deliverable_metadata_wiring.py` for opencode, codex, grok and kimicode. Expected: FAIL (`KeyError: 'deliverable_metadata'`).
- [ ] **Step 3:** Add the keyword at the six call sites.
- [ ] **Step 4:** S, then the same six RUNs. Expected: PASS.
- [ ] **Step 5:** C with the message `feat(agent wrappers): every wrapper sends the base deliverable metadata`.

### Task 7: docs and the demo callback

**Files:**
- Modify `REPO/AGENTS.md`: a new section "## Python: engine wrappers — deliverable callbacks and metadata", after the conversation-steering section (ends at line 917). It covers:
  - the two callback forms, `(hook_ctx, path, text)` and `(hook_ctx, path, text, metadata=None)`, and the detection rule;
  - the entry shape, the keys with their sources, `meta_get` with the exported constants, and the clamp limits;
  - when the snapshot is taken, `origin: harness` for the claustrum notice, the never-included list, and that a provider failure never fails a delivery;
  - which wrappers report model entries (Claude Code) and which the base ones.
- Modify `REPO/docs/writing-agent-wrappers.md`:
  - In Part 2 A (176-187), add a `deliverable_metadata` bullet: start from `agent_entries(config, AGENT_INFO)`; read only listed in-memory fields, never env, settings, seed objects or account info; stay fast (the 2 s limit); keep body-owned state in a holder the body fills, as claudecode's `gateway_facts`.
  - The Hooks line (1102) names the optional fourth argument.
- Modify `REPO/packages/optio-demo/src/optio_demo/tasks/_feedback.py:29-30`: `_on_deliverable(hook_ctx, path, text, metadata=None)` prints, after the body, one line per entry `[<tag>] metadata: <key>=<value> (<source>)`, or `[<tag>] metadata: none` when the list is None or empty.
- Test: `REPO/packages/optio-demo/tests/test_feedback_helper.py`.

**Interfaces:** consumes `entry` (Task 1) and the detection rule (Task 2). No new API.

- [ ] **Step 1: Write the failing test:**
```python
@pytest.mark.asyncio
async def test_prints_the_metadata(capsys):
    cb = make_feedback_on_deliverable("t")
    await cb(_Ctx("p-md"), "x.md", "done. Over and out.", [entry("model", "lyceum/kimi-k3.1-pro", "agent")])
    assert "[t] metadata: model=lyceum/kimi-k3.1-pro (agent)" in capsys.readouterr().out
```
  The existing three-argument tests stay unchanged (RF3).
- [ ] **Step 2:** S, then `RUN REPO/packages/optio-demo/tests/test_feedback_helper.py`. Expected: FAIL (TypeError: 4 arguments).
- [ ] **Step 3:** Implement the demo change and write both docs.
- [ ] **Step 4:** S, then the same RUN. Expected: PASS.
- [ ] **Step 5:** C with the message `docs: deliverable callbacks and their metadata; the demo callback prints it`.

---

## Self-review against the spec

**Coverage:**

| Spec item | Tasks |
|---|---|
| Shape, sources, constants, `meta_get`, clamp (32 / 256 / scalars) | 1 |
| `agent_entries` | 1, 6 |
| Optional fourth argument; three-argument callbacks unchanged (decision 5) | 2, 3, 7 |
| Driver provider; snapshot at line acceptance; `origin`, `delivered_at`, `resumed`; failure never fails a delivery | 3 |
| Claustrum notice with `origin: harness` | 2 |
| claudecode keeps init's `session_id`, `claude_code_version`, model, the last `message.model`, and a per-turn model a switch does not clear | 4 |
| claudecode provider: `conversation.model`, effort, catalog label, gateway provider and region; iframe `config.model` | 5 |
| Never included: token, `base_url`, key hint, env and settings, account details | 5 (sentinel test, exact entries), 1 (clamp), 6 (exact base entries) |
| Other wrappers: base entries only (decision 7) | 6 |
| Measure what Requesty reports | Finding 1 (measured 2026-11-18) |
| Docs and demo | 1, 2, 3, 5 (package AGENTS.md); 7 (root AGENTS.md, wrapper guide, demo) |
| Excavator use (decisions 1-4 and 6, insights) | excavator plan; Finding 8 |
| Out of scope (time of writing, other wrappers' model entries, release) | Global Constraints |

**Names:**
- The binding names are used verbatim: `DeliverableMeta`, `DeliverableMetadata`, the twelve `KEY_*`, the five `SOURCE_*`, `meta_get` (with the default preference order as given), `clamp`, `agent_entries`, `invoke_on_deliverable`, `DeliverableCallback`, and `run_log_protocol_session(deliverable_metadata=...)`.
- The additions are listed in Finding 7.

**Review Focus, where each is tested:**

| Item | Tasks |
|---|---|
| RF1 | 3, 5 |
| RF2 | 3 |
| RF3 | 2, 3, 7 |
| RF4 | 3, 4 |
| RF5 | 1, 5, 6 |
