"""Protocol feature flags — the value object describing per-agent variation.

Lives in its own module so both ``parser.py`` and ``protocol.py`` can
import it (``protocol.py`` already imports ``parser.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from optio_agents.browser_shims import BrowserMode


@dataclass(frozen=True)
class ProtocolFeatures:
    """Which optional protocol facets are active for one agent.

    ``browser="redirect"`` enables the ``BROWSER:`` keyword;
    ``client_messages`` enables ``CLIENT_MESSAGE:`` (frontend-routed);
    ``caller_messages`` enables ``CALLER_MESSAGE:`` (embedding-app callback).
    Disabled keywords are excluded from BOTH the parser and the LLM-facing
    documentation. Defaults are conservative: everything off.
    """

    browser: BrowserMode = "ignore"
    client_messages: bool = False
    caller_messages: bool = False
