# optio-antigravity

Run Google Antigravity (`agy`) as an `optio` task — either as a local
subprocess or on a remote host over SSH — with the interactive TUI
embedded in the optio dashboard via an iframe widget served by `ttyd`,
or as a transcript-driven conversation rendered in the dashboard chat
widget.

## Install

```bash
pip install optio-antigravity
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, `optio-agents`,
`asyncssh`, and `aiohttp`.

## What it does

Antigravity is Google's CLI coding agent (`agy`), sharing state with the
Gemini CLI under `~/.gemini`. optio-antigravity adapts the optio wrapper
machinery: it launches `agy` under a PTY, serves the TUI over `ttyd`, and
coordinates with the host harness through the `optio.log` keyword channel
(STATUS / DELIVERABLE / DONE / ERROR). The agent reads its task from an
`AGENTS.md` file planted in the workdir.

Because `agy --print` runs one-shot only (no ACP/stream-json) and swallows
stdout under a non-TTY, conversation mode is **synthetic**: each turn is
driven with `agy -p --conversation <id>` under a PTY and events are read
from `~/.gemini/antigravity/transcript.jsonl`.

## Status

Stage 0 (MVP): package scaffold. Iframe/ttyd mode, remote/SSH, resume,
seeds, conversation mode, and filesystem isolation arrive in later stages.
