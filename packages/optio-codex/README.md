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

optio-codex launches `codex` inside a detached tmux session, serves the
TUI over `ttyd`, and coordinates with the host harness through the
`optio.log` keyword channel (STATUS / DELIVERABLE / DONE / ERROR). The
agent reads its task from an `AGENTS.md` file planted in the workdir.
The tmux+ttyd machinery follows the optio-claudecode pattern; browser
handling deliberately differs (`suppress` — codex login is handled via
env/API key or interactively, not via surfaced browser URLs).

### Isolation

Each task runs under an isolated `HOME` (`<workdir>/home`, created at
prepare time) with `CODEX_HOME` pointing at `<workdir>/home/.codex`, so
the operator's real `~/.codex` identity and config do not leak into the
session. The codex binary is launched via a per-task path
(`<workdir>/home/.local/bin/codex`), so teardown only ever kills this
task's process.

### Authentication (Stage 0)

The isolated home starts empty — codex is NOT logged in. Either pass an
API key into the session env (`CodexTaskConfig(env={"OPENAI_API_KEY": …})`)
or log in interactively (`codex login`) inside the embedded terminal.
Seed-based provisioning (log in once, reuse for every task) arrives with
the seeds stage.

## Status — Stages 0–2 (iframe, remote SSH, resume)

Shipped:

- iframe/ttyd mode on the local host
- `optio.log` keyword-protocol coordination + exit-status DONE/ERROR channel
- per-task `HOME` / `CODEX_HOME` isolation (tree provisioned at prepare)
- task-scoped teardown (per-task codex path; orphan-ttyd reap)
- `create_codex_task`, `run_codex_session`, `CodexTaskConfig`
- demo task in optio-demo (`Codex demo — iframe`)
- remote SSH workers (`ssh=SSHConfig(...)` routes to `RemoteHost`; verified
  end-to-end against a docker-sshd harness)
- resume / workdir snapshots: session-id-keyed relaunch (`codex resume <id>`,
  never `resume --last`), Mongo snapshot store (retention 5, single workdir
  GridFS blob carrying `home/.codex/sessions`), `resume.log` + AGENTS.md
  resume section synced to the snapshot exclude list
  (`workdir_exclude`; defaults drop `home/.codex/packages`, `*.sqlite*`,
  caches — never `home/.codex/sessions`)

Still missing (tracked gaps toward Appendix A parity, staged plans B–E):

- crash-orphan rescue (snapshot capture for a crashed engine)
- seeds, pool/leases, credential save-back, seed verify/refresh
- conversation mode (`codex exec --json` / app-server) + conversation-ui widget
- model switching; file upload/download; tool verbosity
- optio-owned binary cache + auto-install (`install_if_missing` becomes real there)
- filesystem isolation (Landlock / claustrum) reconciled with codex's native sandbox
- demo trio completion (seed-setup + seed-pinned iframe & conversation)
- PyPI release