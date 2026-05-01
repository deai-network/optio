"""Public data types for optio-host."""

from dataclasses import dataclass


@dataclass
class SSHConfig:
    """SSH connection parameters for remote-mode hosts.

    Known-hosts verification is disabled in MVP; asyncssh's
    ``known_hosts=None`` equivalent is used by the host layer.
    """
    host: str
    user: str
    key_path: str
    port: int = 22
