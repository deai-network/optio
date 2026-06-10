"""Single source of truth for the LLM-facing keyword-protocol documentation.

This module lives next to ``parser.py`` so the prose that teaches an agent
how to speak the protocol cannot drift from the regexes that enforce it.

``build_log_channel_prompt(features)`` assembles the feature-specific block:
the ``BROWSER:`` keyword is documented only for ``browser="redirect"``; a
trailing "no browser here" paragraph is appended only for
``browser="suppress"``; ``CLIENT_MESSAGE:`` / ``CALLER_MESSAGE:`` are
documented only when the corresponding ``ProtocolFeatures`` flag is set.
The core keywords (``STATUS:`` / ``DELIVERABLE:`` / ``DONE`` / ``ERROR`` /
``ATTENTION:``) are documented in every mode.
"""

from __future__ import annotations

from optio_agents.protocol.features import ProtocolFeatures

# The push notification a backend sends a resumed agent over the channel.
# Shared so the doc prose and both backends agree on one string.
RESUME_NOTICE = "you have been resumed"


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

_ATTENTION_BULLET = """- `ATTENTION:` — request human attention with a short reason, e.g.
  `ATTENTION: waiting for your approval`.
"""

_CLIENT_MESSAGE_BULLET = """- `CLIENT_MESSAGE:` — push a message to the user interface of the
  application that launched you: a keyword token followed by single-line
  JSON, e.g. `CLIENT_MESSAGE: build-finished {"artifact":"app.zip"}`.
  The JSON must be valid and on one line; malformed JSON is dropped.
"""

_CALLER_MESSAGE_BULLET = """- `CALLER_MESSAGE:` — send a message to the controlling application:
  a keyword token followed by single-line JSON, e.g.
  `CALLER_MESSAGE: tests-passed {"suite":"unit"}`. The JSON must be valid
  and on one line; malformed JSON is dropped. The application may answer
  with a `System:` message on your input channel.
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

_FEEDBACK = """
## Messages from the harness

The harness replies to you on the same input channel where the user normally
talks — that channel carries both genuine user input and harness messages.
Every harness message is prefixed `System:`.

**After writing a `DELIVERABLE:` line, end your turn and wait.** The harness
acknowledges every deliverable with a `System: deliverable <name>: ...` reply.
Do not declare `DONE` until every deliverable you have emitted has been
accepted. Handle each reply by its content:

- **Accepted** (e.g. `accepted. thanks for the good work.`) — you may proceed;
  emit `DONE` once all your deliverables are accepted.
- **Points out something specific about the deliverable** — treat it as a
  revision request: fix the artifact and emit its `DELIVERABLE:` line again.
- **Reports trouble on the harness side** (it says the problem is on its end,
  not yours) — do not keep retrying now; remember to emit that deliverable
  again later, after you are next resumed.
"""


def build_log_channel_prompt(
    features: ProtocolFeatures = ProtocolFeatures(),
) -> str:
    """Build the keyword-protocol documentation block for ``features``."""
    browser_bullet = _BROWSER_BULLET if features.browser == "redirect" else ""
    client_bullet = _CLIENT_MESSAGE_BULLET if features.client_messages else ""
    caller_bullet = _CALLER_MESSAGE_BULLET if features.caller_messages else ""
    suppress_note = _SUPPRESS_NOTE if features.browser == "suppress" else ""
    return (
        _HEADER
        + browser_bullet
        + _ATTENTION_BULLET
        + client_bullet
        + caller_bullet
        + suppress_note
        + _RULES
        + _FEEDBACK
    )
