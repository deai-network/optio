# optio-agents

The agent-coordination layer for [optio](https://github.com/deai-network/optio) task types.

`optio-agents` owns the **log/deliverables keyword protocol** that long-running on-host agents use to talk back to optio, the **session driver** that parses and dispatches it, the **`HookContext`** handle passed to agent task hooks, and the **single source of truth** for the LLM-facing keyword documentation.

## What's in the box

- **`optio_agents.protocol`** — a line-oriented session driver. A long-running agent on the host writes lines prefixed `STATUS:`, `DELIVERABLE:`, `DONE`, or `ERROR` to `./optio.log`. `run_log_protocol_session` tails the log, dispatches progress events, fetches deliverable files, and resolves the session on `DONE` / `ERROR`.
- **`optio_agents.protocol.parser`** — the keyword parser (`parse_log_line`, the typed `*Event` dataclasses, deliverable-path validation).
- **`optio_agents.protocol.prompt`** — `LOG_CHANNEL_PROMPT`, the canonical LLM-facing documentation of the keywords, co-located with the parser regexes it documents so the two cannot drift. Consumers compose it into their own agent-facing prompt.
- **`HookContext` / `HookContextProtocol`** — the handle passed into task hooks and `on_deliverable` callbacks, wrapping a `ProcessContext` plus host primitives (`run_on_host`, `copy_file`, `read_from_host`, `download_file`).

## Dependency direction

`optio-agents` depends on `optio-host` (host transport: running commands, file transfer, tunnels) and `optio-core`. It is consumed by agent task packages such as `optio-opencode`.

## Installation

```bash
pip install optio-agents
```

Python 3.11+.

## License

Apache-2.0.
