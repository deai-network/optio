"""Backwards-compatible re-export of the shared in-session input listener.

The listener + lock helper + NAV_KEYS now live in :mod:`optio_agents.input_listener`
/ :mod:`optio_agents.tmux_input` (shared across every ttyd/tmux engine). This
module keeps the original ``optio_claudecode.input_listener`` import path stable.
See docs/2026-06-08-claudecode-input-channel-design.md for the design.
"""

from __future__ import annotations

from optio_agents.input_listener import serialized, start_input_listener
from optio_agents.tmux_input import NAV_KEYS

__all__ = ["serialized", "start_input_listener", "NAV_KEYS"]
