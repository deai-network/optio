# optio-grok

Run Grok Build (xAI) as an `optio` task — either as a local subprocess
or on a remote host over SSH — with the interactive TUI embedded in the
optio dashboard via an iframe widget served by `ttyd`.

## Install

```bash
pip install optio-grok
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, `optio-agents`,
`asyncssh`, and `aiohttp`.

## What it does

Grok Build is a near-twin of Claude Code. optio-grok adapts the
`optio-claudecode` machinery: it launches `grok` inside a detached tmux
session, serves the TUI over `ttyd`, and coordinates with the host
harness through the `optio.log` keyword channel (STATUS / DELIVERABLE /
DONE / ERROR). The agent reads its task from an `AGENTS.md` file planted
in the workdir.

### Isolation

Each task runs under an isolated `HOME` (`<workdir>/home`) with
`GROK_HOME` pointing at `<workdir>/home/.grok`. Grok ships a
claude-compat layer, so `CLAUDE_CONFIG_DIR` is pinned at
`<workdir>/home/.claude` to keep the operator's real `~/.claude`
configuration, hooks, and instructions from leaking into the task.
`--no-leader` is always passed so tasks never share a grok backend.

## Status

Stage 0 (MVP): iframe/ttyd mode, local host. Resume, seeds,
conversation mode, and filesystem isolation arrive in later stages.
