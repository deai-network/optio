# optio-kimicode

Run Kimi Code CLI as an `optio` task — either as a local subprocess
or on a remote host over SSH — with the interactive TUI embedded in the
optio dashboard via an iframe widget served by `ttyd`.

## Install

```bash
pip install optio-kimicode
```

Requires Python 3.11+. Pulls `optio-core`, `optio-host`, `optio-agents`,
`asyncssh`, and `aiohttp`.
