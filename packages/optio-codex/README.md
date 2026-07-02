# optio-codex

Run OpenAI Codex as an `optio` task — local subprocess with the interactive
TUI embedded in the optio dashboard via an iframe widget served by `ttyd`.

## Install

```bash
pip install optio-codex
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, `optio-agents`,
`asyncssh`, and `aiohttp`.

## What it does

optio-codex adapts the `optio-claudecode` iframe machinery (see also
`optio-opencode` for shared log-protocol driver patterns):
it launches `codex` inside a detached tmux session, serves the TUI over
`ttyd`, and coordinates with the host harness through the `optio.log`
keyword channel (STATUS / DELIVERABLE / DONE / ERROR). The agent reads its
task from an `AGENTS.md` file planted in the workdir.

### Isolation

Each task runs under an isolated `HOME` (`<workdir>/home`) with
`CODEX_HOME` pointing at `<workdir>/home/.codex`, so the operator's real
`~/.codex` identity and config do not leak into the session.

## Status — Stage 0 (MVP)

Shipped in this release:

- iframe/ttyd mode on the local host
- `optio.log` keyword-protocol coordination
- per-task `HOME` / `CODEX_HOME` isolation
- `create_codex_task`, `run_codex_session`, `CodexTaskConfig`

Still missing (tracked gaps toward Appendix A parity):

- remote SSH host
- resume / workdir snapshots
- seeds and OAuth provisioning
- credential save-back for rotating refresh tokens
- conversation mode (`codex exec --json` or Codex app-server)
- conversation-ui reducer and dashboard chat widget
- filesystem isolation (Landlock / claustrum)
- optio-owned binary cache and headless auto-install
- demo-task wiring and PyPI release