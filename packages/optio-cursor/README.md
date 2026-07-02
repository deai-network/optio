# optio-cursor

Run Cursor CLI (`cursor-agent`) as an `optio` task — either as a local
subprocess or on a remote host over SSH — with the interactive TUI
embedded in the optio dashboard via an iframe widget served by `ttyd`.

## Install

```bash
pip install optio-cursor
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, `optio-agents`,
`asyncssh`, and `aiohttp`.

## What it does

Cursor CLI is a near-twin of Grok Build / Claude Code. optio-cursor
adapts the `optio-grok` machinery: it launches `cursor-agent` inside a
detached tmux session, serves the TUI over `ttyd`, and coordinates with
the host harness through the `optio.log` keyword channel (STATUS /
DELIVERABLE / DONE / ERROR). The agent reads its task from an
`AGENTS.md` file planted in the workdir.

### Isolation

Each task runs under an isolated `HOME` (`<workdir>/home`) with the XDG
base dirs (`XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`) pinned
under it, so cursor's `~/.cursor` and `~/.cache` state never touches the
operator's real home. Permission rules are config-planted (cursor has no
`--allow`/`--deny` argv): they go into `<home>/.cursor/cli-config.json`.
`NO_OPEN_BROWSER=1` keeps login URLs on the transcript instead of
popping a browser.

## Status

Stage 0 (MVP): iframe/ttyd mode, local host. Resume, seeds,
conversation mode, and filesystem isolation arrive in later stages.
