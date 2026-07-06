"""Shared helpers to inject human/operator input into a tmux-hosted agent TUI.

The iframe-input widget (optio-ui ``iframe-input``) POSTs either a typed message
(``{text}``) or a single navigation keystroke (``{key}``) to the session's
control upstream; the engine's handler forwards it here to drive the agent's TUI
that runs inside tmux (claudecode, antigravity, …). Extracted from the original
claudecode implementation so every ttyd/tmux engine shares one copy.
"""

from __future__ import annotations

import shlex

from optio_host.host import Host


# Navigation keys the iframe-input widget may send (as ``{key: "<name>"}``) when
# the input box is empty, for driving the agent's TUI menus. These are tmux
# ``send-keys`` key names; only this exact set is accepted — never an arbitrary
# string (which would let a caller inject other key sequences).
NAV_KEYS = frozenset({"Up", "Down", "Left", "Right", "Enter", "Escape", "Tab"})


async def send_text_to_tmux(
    host: Host,
    tmux_path: str,
    tmux_socket: str,
    tmux_session: str,
    text: str,
    *,
    buffer: str = "optio-input",
    submit_settle: str = "1.0",
) -> None:
    """Fake-type a message into the tmux TUI and submit it.

    Uses ``set-buffer`` + ``paste-buffer`` (robust for arbitrary text incl.
    spaces, which ``send-keys -l`` would mistreat), then — after a brief settle —
    a single ``Enter`` to submit. The settle is essential: an ``Enter`` sent in
    the same burst as the paste lands while the TUI is still settling the
    (bracketed) paste, so the CR is consumed as a literal newline inside the input
    box rather than a submit — the message then sits unsent. Decoupling the Enter
    by ``submit_settle`` seconds makes it a distinct keypress that submits. Raises
    on a tmux failure (callers treat that as 'agent unreachable').

    ``buffer`` is an internal tmux buffer name (an engine constant, not caller
    input); ``submit_settle`` is a shell-literal seconds string for ``sleep``.
    """
    s = shlex.quote(tmux_socket)
    sess = shlex.quote(tmux_session)
    tp = shlex.quote(tmux_path)
    cmd = (
        f"{tp} -S {s} set-buffer -b {buffer} -- {shlex.quote(text)} && "
        f"{tp} -S {s} paste-buffer -d -b {buffer} -t {sess} && "
        f"sleep {submit_settle} && "
        f"{tp} -S {s} send-keys -t {sess} Enter"
    )
    result = await host.run_command(cmd)
    if result.exit_code != 0:
        raise RuntimeError(
            f"send_text_to_tmux: tmux injection failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )


async def send_key_to_tmux(
    host: Host,
    tmux_path: str,
    tmux_socket: str,
    tmux_session: str,
    key: str,
) -> None:
    """Send a single navigation keystroke into the tmux TUI (no paste/settle), for
    driving TUI menus from the iframe-input widget when the input box is empty.
    ``key`` must be one of :data:`NAV_KEYS` (a tmux key name); a disallowed key
    raises rather than reaching ``send-keys``."""
    if key not in NAV_KEYS:
        raise ValueError(f"send_key_to_tmux: disallowed key {key!r}")
    s = shlex.quote(tmux_socket)
    sess = shlex.quote(tmux_session)
    tp = shlex.quote(tmux_path)
    result = await host.run_command(f"{tp} -S {s} send-keys -t {sess} {key}")
    if result.exit_code != 0:
        raise RuntimeError(
            f"send_key_to_tmux: tmux send-keys {key} failed "
            f"(exit {result.exit_code}): {result.stderr!r}"
        )
