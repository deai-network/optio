# Claude Code through an LLM gateway, with Requesty as the first provider

Date: 2026-11-17. Status: design agreed with the owner in a brainstorm; spec for review.
Companion spec (the consumer side): excavator `docs/2026-11-17-claude-code-requesty-design.md`.

## Why

Customers want to run Claude Code sessions through an LLM gateway they pay for themselves:
[Requesty](https://docs.requesty.ai) today, with an API key, a region and a choice of models,
including models from other labs (Kimi, GLM, DeepSeek) on the customer's own restricted list.
There is no OAuth login and no expiring token, so a seed of this kind needs no exclusive lease.

optio-claudecode assumes an Anthropic OAuth seed throughout, so none of this works today:

- a session captures a seed only when it holds an OAuth refresh token (`session.py:1270`);
- the resume snapshot is refused unless `home/.claude/.credentials.json` is non-empty
  (`session.py:2144-2156`);
- the model catalog comes from the CLI's `initialize` answer (`models.py:73`), and
  `_flag_settings` refuses any model not in it (`conversation.py:535-537`);
- a resumed session's model is restored through a `claude-<family>-<version>` pattern
  (`models.py:170-191`), and a continued session launches with `--model default`;
- validation (`oauth.py` `verify_and_refresh_seed`) and account analysis are OAuth calls.

## Decisions (owner, 2026-11-17)

1. **Generic gateway mode** in optio-claudecode, with a small **Requesty provider** on top. The
   agent type is Requesty-specific, so a consumer can offer "Claude Code + Requesty" next to
   "Claude Code"; a later gateway (OpenRouter, a company proxy) adds a provider and an agent type.
2. **Several models per configuration**, chosen through the existing session switcher. The
   switcher offers exactly the models that passed validation; effort levels come from the
   provider's model metadata where it publishes them, and the effort control is hidden where
   it does not.
3. **Validation** runs, for each chosen model, a short real Claude Code session through the
   gateway whose prompt needs one tool call. Models run in parallel. A failing model is dropped
   with its reason; if the default model fails, nothing is saved.
4. **No leasing**: a gateway seed serves any number of concurrent sessions.
5. **The live switch stays as it is.** Since optio-claudecode 0.7.0 (2026-10-12) model and effort
   switch live through the stream-json control request `apply_flag_settings`, with a
   `get_settings` readback; the relaunch path (`begin_restart`) has no caller. The owner
   believed it still relaunched; checked, it does not.

## Facts established on 2026-11-17

Probed with Claude Code in optio's conversation mode (stream-json in and out, scratch HOME,
key only in the child's environment) against Requesty's EU endpoint:

- Pinned ids (`lyceum/kimi-k3.1-pro`) and bare managed names (`kimi-k3.1-pro`) work as
  `--model`, including tool calls and thinking. Policy ids (`policy/<name>`) were not tried.
- `apply_flag_settings` to another model and effort succeeds and the readback confirms it; the
  next turn runs on the new model, with the previous model's thinking and tool use in the
  history (Kimi to GLM and back). `/model <id>` and `/effort <level>` sent as user messages
  also switch, but each costs a turn and stays in the conversation.
- Behind a gateway, `initialize` offers `default, opus, fable, sonnet, haiku` plus the launch
  model, each with all five effort levels. That list is wrong for a restricted gateway account.
- Claude Code's own background calls use the `haiku` alias, and the other aliases resolve the
  same way (`ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`); on a restricted list those
  calls fail unless the aliases point at an allowed model.
- Claude Code sends no effort for model ids it does not recognize unless
  `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1` is set.
- `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` strips credentials (matched by name or value,
  `ANTHROPIC_AUTH_TOKEN` included) from Bash commands, hooks and stdio MCP servers while Claude
  keeps them for its own calls. On Linux it also runs Bash in its own PID namespace.
- With a non-Claude model, the transcript's thinking blocks carry fields Anthropic's own API
  rejects (`index`, `text`, `citations`, empty `signature`). A gateway session can therefore not
  be continued against Anthropic directly; it stays on its gateway agent type.
- Requesty: `GET /v1/models` with the key lists the account's pinned models and its policies
  (`policy/<name>`); `GET /v1/models/managed` lists the bare managed names, some with an `@eu`
  suffix (EU endpoint only). Without a key `/v1/models` returns the full public catalogue. Some
  pinned entries carry `reasoning.supported_efforts`; bare names and policies do not, and
  policies mark every feature as supported by design. Both regions return the same lists.

## Design

### The agent type

- New agent type `claudecode_requesty`, a plain identifier because optio-agents-ui's generated
  `AgentType` uses slugs as object keys. `AGENT_INFO` name "Claude Code + Requesty".
- Its config class shares every `ClaudeCodeTaskConfig` field, with `agent_type` fixed to
  `claudecode_requesty`. Gateway mode needs `mode="conversation"`; iframe mode is refused at
  config time, because it puts the launch environment on the tmux command line, where process
  listings show it.
- Registered in optio-agents-all: the `AgentType` literal and config union, `_REGISTRY`,
  `info.py`, both capability sets (`MCP_AGENT_TYPES`, `CUSTOM_CONTROLS_AGENT_TYPES`, needed by
  data chats) and the analyze registry (accounts: `EMPTY`). `make codegen` regenerates
  optio-agents-ui.

### The gateway seed

- Stored like every other seed: a document in `{prefix}_claudecode_requesty_seeds` with a blob
  encrypted by the consumer's seed cipher pair. It is never leased: consumers pass its id as a
  plain string, which optio already treats as unleased.
- The blob is one versioned JSON document: provider, region, base URL, token, the validated
  models (id, label, group, effort levels or none, status, failure reason), the default model,
  the test time, and, for data chats, the Fast and Extensive presets (model and effort each).
- The document's metadata holds the non-secret part (provider, region, the key's last four
  characters, default model, model ids with their status, test time), so a consumer can list
  and label it without decrypting anything.
- A seed is written in a `pending` state first. The setup task (below) validates it and either
  makes it alive or removes it. A pending seed is never picked for a session.
- Helpers in `optio_claudecode.gateway`: write a gateway seed, read one (decrypting), and update
  its model results.

### Launching in gateway mode

When the config is a gateway agent type:

- The seed is read at launch. Nothing from it is planted into HOME (no credential files);
  `claude_config` is still merged into `settings.json`.
- The child's environment, never argv and never a file, gets:
  - `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`;
  - `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL` set to the default model, so background
    calls and alias-based subagents stay on validated models;
  - `CLAUDE_CODE_AUTO_MODE_SERVER=0` (the server-side auto-mode classifier needs Anthropic's API);
  - `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0`. The design had `1`, so the token would not reach the
    agent's Bash commands, hooks or MCP servers. Owner decision, 2026-11-18, from the live test:
    on Claude Code 2.1.327 the scrub forces permission mode `default` (an analysis session
    launched with `bypassPermissions` had every write and output redirection denied, with no way
    to ask), and it requires the CLI's own Bash sandbox, which needs `socat` and runs nothing
    outside it. The cost: the agent's shell commands can read `ANTHROPIC_AUTH_TOKEN` from their
    environment, as a login agent can read its credentials file; optio's own file confinement
    still applies, and the token still arrives over stdin only. The key stays gateway-owned and
    is pinned at `0`, so neither the host environment nor a settings file can turn it on.
    Follow-up: re-enable it once Claude Code's sandbox can be configured for these sessions
    (explicit `allowedTools`, `socat`, a network allowlist);
  - the effort settings below.
- `--model` is the session's model, as an exact id.
- **Catalog:** `models_offered` is the seed's usable models with their labels and effort levels,
  not the CLI's `initialize` list, so the switcher shows them and `_flag_settings` checks
  switches against them.
- **Effort:** a model with published levels offers exactly those, and the session sets
  `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1` so Claude Code sends them. A model without published
  levels shows no effort control and must be sent no effort. Today `_flag_settings` always sends
  an `effortLevel`; gateway mode needs a no-effort case for these models, verified against the
  CLI during implementation.
- **Resume:** the session's exact model id is restored when it is still usable in the seed,
  otherwise the default model. Neither the `claude-<family>` pattern nor the `default` alias
  is used.
- **OAuth-only paths are off:** the credentials watcher and `refresh_seed`, seed capture at
  teardown, account resolution, `verify_and_refresh_seed`. The snapshot guard checks that the
  gateway seed was loaded instead of the credentials file. Snapshots contain no token, because
  the token only ever lives in the process environment.

### The Requesty provider

`optio_claudecode.gateways.requesty`, plain HTTP with timeouts; no token in logs or errors:

- Regions: `global` is `https://router.requesty.ai`, `eu` is `https://router.eu.requesty.ai`.
- `list_models(token, region)` merges `/v1/models` and `/v1/models/managed` into one list in
  three groups:
  - managed (bare names; Requesty picks the provider);
  - pinned (`provider/model`);
  - policies (`policy/<name>`).

  `@eu` entries appear only for the EU region. Effort levels come from
  `reasoning.supported_efforts`; an entry without them borrows them from an entry with the same
  `model_canonical_name`. Policies get none, because their flags are placeholders.
- Errors are typed: a rejected key (401/403), an unreachable endpoint, an empty list.

### Validation, and the setup task

- `validate_gateway_models(...)` runs, on the given host, a fresh short gateway-mode
  conversation per model:
  - the workdir holds a planted file with a random word;
  - the prompt asks Claude to read it with the Read tool and reply with only that word;
  - a model passes when the answer contains the word and the turn did not end in an error.

  Each model has a time limit, and models run in parallel with a cap of three at once, because
  every model is a Claude process on the session host. It reuses `model_probe`'s turn helper,
  not its sequential loop.
- A setup task factory for the agent type:
  1. loads a pending seed;
  2. validates its models, reporting per-model progress;
  3. writes the results into the seed;
  4. makes the seed alive when the default model passed, or removes it when it did not;
  5. calls `on_seed_saved`, as login-based setup tasks do.

  Run again on an alive seed, it re-tests the saved models ("Test again"): a failing model
  becomes unusable, and when every model fails the seed is marked dead.
- The session host is the host where sessions run, so validation also proves that host can
  reach the gateway.

## Testing

- Requesty provider: unit tests on synthetic fixtures shaped like the 2026-11-17 responses,
  with no real account or policy names in the repo. Cover the merge, the groups, `@eu`
  filtering, borrowed effort levels, and errors.
- Gateway launch, on `fake_claude.py`'s stream-json mode:
  - the token is in the child environment and absent from argv and files;
  - the catalog comes from the seed;
  - a switch to a model outside it is refused;
  - effort is handled with and without published levels;
  - resume restores the model;
  - no capture, no watcher, no credentials-file guard;
  - iframe mode is refused.
- Validation and the setup task, on `fake_claude.py`:
  - a pass, a wrong answer, an error and a timeout;
  - the concurrency cap;
  - a failing default removes the pending seed;
  - "Test again" marks models.
- Live: through excavator on the excavator host, with the owner entering his key in the UI.

## Out of scope

Other gateway providers, Requesty spending or balance display, policy management, key rotation
reminders, the graduation report.

## To verify during implementation

- How to send "no effort" for a model without levels: `apply_flag_settings` without an
  `effortLevel`, or `CLAUDE_CODE_EFFORT_LEVEL=unset`.
- That `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` and its PID namespace do not break analysis
  sessions under claustrum. Answered in the live test (2026-11-18, Claude Code 2.1.327): the
  scrub breaks analysis sessions, so gateway sessions run without it (Launching in gateway
  mode).
- Policy ids as `--model`.
- Whether gateway sessions still need `initialize` for its command list.
