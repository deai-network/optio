"""The Protocol value object and its factory.

``get_protocol(browser=…, client_messages=…, caller_messages=…)`` is the
single decision point binding the facets that vary per agent — the keyword
documentation, the parser variant, and the browser-open shim behavior — to
one ``ProtocolFeatures``. The returned ``Protocol`` carries them so they
cannot drift apart.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from optio_agents.browser_shims import BrowserMode, prepare_browser_shims
from optio_agents.protocol.features import ProtocolFeatures
from optio_agents.protocol.parser import LogEvent, parse_log_line
from optio_agents.protocol.prompt import build_log_channel_prompt

if TYPE_CHECKING:
    from optio_host.host import Host


@dataclass(frozen=True)
class Protocol:
    """Per-agent protocol variation: docs + parser + browser-shim behavior."""

    documentation: str
    parse_log_line: Callable[[str], LogEvent]
    features: ProtocolFeatures

    @property
    def browser(self) -> BrowserMode:
        return self.features.browser

    async def prepare_browser_shims(self, host: "Host") -> dict[str, str] | None:
        """Install this mode's browser shims; return launch-env additions.

        Returns ``None`` for ``ignore`` (no shims). The session driver calls
        this after ``setup_workdir`` and stashes the result on
        ``HookContext.browser_launch_env``; the agent body merges it into
        the launched subprocess's env.
        """
        return await prepare_browser_shims(host, self.features.browser)


def get_protocol(
    *,
    browser: BrowserMode = "ignore",
    client_messages: bool = False,
    caller_messages: bool = False,
) -> Protocol:
    """Build the ``Protocol`` for the requested feature set."""
    features = ProtocolFeatures(
        browser=browser,
        client_messages=client_messages,
        caller_messages=caller_messages,
    )
    return Protocol(
        documentation=build_log_channel_prompt(features),
        parse_log_line=functools.partial(parse_log_line, features=features),
        features=features,
    )
