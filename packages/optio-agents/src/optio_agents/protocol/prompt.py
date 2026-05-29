"""Single source of truth for the LLM-facing keyword-protocol documentation.

This module lives next to ``parser.py`` so the prose that teaches an agent
how to speak the protocol cannot drift from the regexes that enforce it.

``build_log_channel_prompt(browser)`` assembles the mode-specific block:
the ``BROWSER:`` keyword is documented only for ``redirect``; a trailing
"no browser here" paragraph is appended only for ``suppress``. All other
keywords (``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR`` /
``ATTENTION:`` / ``DOMAIN_MESSAGE:``) are documented in every mode.
"""

from __future__ import annotations

from optio_agents.browser_shims import BrowserMode


_HEADER = """## Log channel

Append one line per entry to `./optio.log` in this directory. Each line
must start with one of:

- `STATUS:` — progress update for the human. Optional leading percent,
  e.g. `STATUS: 50% counting my fingers`.
- `DELIVERABLE:` — absolute or workdir-relative path to a file you've
  just produced, e.g. `DELIVERABLE: ./deliverables/summary.md`.
- `DONE` — you have finished the task. May be followed by an optional
  summary on the same line: `DONE: wrote the report`.
- `ERROR` — you cannot continue. May be followed by an optional
  message: `ERROR: provider auth failed`.
"""

_BROWSER_BULLET = """- `BROWSER:` — ask the operator's browser to open a URL, e.g.
  `BROWSER: https://example.com/login`. Use for flows that require the
  human to visit a page (e.g. an auth/login URL).
"""

_TAIL_BULLETS = """- `ATTENTION:` — request human attention with a short reason, e.g.
  `ATTENTION: waiting for your approval`.
- `DOMAIN_MESSAGE:` — push an application-specific message: a keyword
  token followed by single-line JSON, e.g.
  `DOMAIN_MESSAGE: build-finished {"artifact":"app.zip"}`. The JSON must
  be valid and on one line; malformed JSON is dropped.
"""

_SUPPRESS_NOTE = """
In this environment, it's impossible to launch a browser, so don't try to
run `xdg-open` or similar.
"""

_RULES = """
**Every entry must end with a newline character (`\\n`).** The host
reads `optio.log` with a line-oriented tailer that only emits a line
once it sees `\\n`; an entry written without a trailing newline (e.g.
via `printf 'DONE'`) will be buffered indefinitely and never reach the
host. Use `echo`, `>>` redirection of a heredoc, or any other mechanism
that guarantees a trailing newline. If unsure, double-check with
`tail -c 1 ./optio.log` — the result must be a newline.

After writing `DONE` or `ERROR`, the session will terminate. Do not
write further lines.

## Deliverables

Place files you want to hand back to the host under `./deliverables/`.
For each file, write a `DELIVERABLE:` log line *after* the file exists
and its contents are final. The host fetches files by reading these
log lines.
"""


def build_log_channel_prompt(browser: BrowserMode = "ignore") -> str:
    """Build the keyword-protocol documentation block for ``browser`` mode."""
    browser_bullet = _BROWSER_BULLET if browser == "redirect" else ""
    suppress_note = _SUPPRESS_NOTE if browser == "suppress" else ""
    return _HEADER + browser_bullet + _TAIL_BULLETS + suppress_note + _RULES
