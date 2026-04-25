"""Public data types for optio-opencode consumers."""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


DeliverableCallback = Callable[[str, str], Awaitable[None]]
"""Consumer callback invoked per fetched DELIVERABLE.

Arguments: (remote_path, decoded_text). remote_path is the path the LLM
wrote in the DELIVERABLE log line (interpreted relative to the workdir
if not absolute). text is the file contents decoded as UTF-8.
"""


@dataclass
class SSHConfig:
    """SSH connection parameters for remote-mode optio-opencode.

    Known-hosts verification is disabled in MVP; asyncssh's
    ``known_hosts=None`` equivalent is used by the host layer.
    """
    host: str
    user: str
    key_path: str
    port: int = 22


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    workdir_exclude: list[str] | None = None
