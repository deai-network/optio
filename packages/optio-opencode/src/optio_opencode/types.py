"""Public data types for optio-opencode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types and
``SSHConfig`` are owned by ``optio-host`` (since they describe the
log/deliverables protocol and SSH config in general). This module
re-exports them so existing ``from optio_opencode.types import ...``
imports keep working unchanged.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from optio_host.protocol.session import DeliverableCallback, HookCallback
from optio_host.types import SSHConfig


__all__ = ["DeliverableCallback", "HookCallback", "SSHConfig", "OpencodeTaskConfig"]


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    # Absolute path on the host where the opencode binary is/should be
    # installed. ``None`` (default) → ``~/.local/bin`` (user home resolved
    # at task start). The same directory is used for installation, for
    # smart-install's PATH lookup, and for the post-"ok" ``command -v``
    # resolution, so an explicit override stays consistent across all
    # three. Must be an absolute path when set.
    opencode_install_dir: str | None = None
    workdir_exclude: list[str] | None = None
    supports_resume: bool = True
    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the
    # opencode session JSON blob at GridFS write/read. When both are set,
    # the snapshot session blob is encrypted at rest. When both are None
    # (default), plaintext is used (backward-compatible). Setting only one
    # raises a config error: asymmetric usage is always a mistake.
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None

    def __post_init__(self) -> None:
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "OpencodeTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
