"""Public data types for optio-opencode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` (they describe the log/deliverables protocol); ``SSHConfig``
is owned by ``optio-host``. This module re-exports them so existing
``from optio_opencode.types import ...`` imports keep working unchanged.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from optio_agents.protocol.session import DeliverableCallback, HookCallback
from optio_host.types import SSHConfig


# Async resolver used as the callable form of ``seed_id``: receives the
# process_id, returns the seed to consume. The consuming app's resolver
# typically acquires a pooled seed lease inside (holder = process_id);
# the session then renews that lease for the lifetime of the run and
# releases it at teardown. Mirrors optio-claudecode.
SeedProvider = Callable[[str], Awaitable[str]]


# Conversation-mode vocabulary (mirrors optio-claudecode).
ConversationMode = Literal["iframe", "conversation"]
ToolVerbosity = Literal["silent", "description-only", "verbose"]
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "SeedProvider",
    "ConversationMode",
    "ToolVerbosity",
    "OpencodeTaskConfig",
]


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    install_if_missing: bool = True
    # Override for the optio-owned opencode binary **cache** directory (where
    # the opencode binary is installed/cached on the worker). ``None``
    # (default) → the worker's ``OPENCODE_CACHE_DIR`` or
    # ``${XDG_CACHE_HOME:-$HOME/.cache}/optio-opencode/bin``. Never the host
    # user's ``~/.local/bin``. The same directory is used for installation,
    # for smart-install's ``--check`` lookup, and for the post-"ok"
    # ``command -v`` resolution, so an explicit override stays consistent
    # across all three. Must be an absolute path when set.
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
    # Optional hook fired on resume only (never on fresh start). Receives
    # the original task config; returns a (possibly mutated/replaced) config.
    # The harness re-renders AGENTS.md from the returned config and writes
    # it back to the workdir only when it differs from the file on disk.
    # When written, the harness tags the new line in resume.log with
    # `REFRESHED:AGENTS.md` so the agent knows to re-read. None (default)
    # → no refresh; the resumed session keeps its original AGENTS.md.
    on_resume_refresh: Callable[["OpencodeTaskConfig"], "OpencodeTaskConfig"] | None = None

    # --- seed surface (mirrors optio-claudecode) ---
    seed_id: "str | SeedProvider | None" = None
    # Fired on teardown of a fresh session after a successful capture, with
    # two args: (seed_id, info). ``info`` is a human-readable summary of the
    # captured configuration — for opencode the resolved "providerID/modelID"
    # (or None if no model was used), mirroring claudecode's account summary.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None
    # Fresh launch kicks the agent off unattended via the opencode session API
    # (POST /api/session/<id>/prompt "Read AGENTS.md and execute the task it
    # describes"); suppressed on resume.
    auto_start: bool = False
    # Glob patterns (fnmatch) of env var NAMES to strip from the opencode
    # subprocess, so inherited provider creds don't override the seed. e.g.
    # ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    # --- conversation mode (mirrors optio-claudecode) ---
    # "iframe": today's behavior — embedded opencode web SPA, keyword-channel
    # completion. "conversation": generic gateway — the caller receives a live
    # Conversation (optio_agents.conversation) via ctx.publish_result().
    mode: ConversationMode = "iframe"
    # Keep the optio.log keyword channel running. May only be disabled in
    # conversation mode, where close() is the alternative completion signal.
    host_protocol: bool = True
    # Register the conversation widget (ui_widget="conversation"). Requires
    # mode="conversation". The opencode server itself is the widget upstream.
    conversation_ui: bool = False
    # Rendering hint forwarded to the widget via widgetData; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
    # Default model for a fresh conversation session, "providerID/modelID".
    # Forwarded to the widget, which applies it once at the start of a non-
    # resumed session (history empty) and only if present in the live model
    # list. Effective regardless of show_model_selector. Requires
    # conversation_ui=True.
    default_model: str | None = None
    # Show the model picker in the conversation widget. Requires
    # conversation_ui=True.
    show_model_selector: bool = False

    def __post_init__(self) -> None:
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "OpencodeTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"OpencodeTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "OpencodeTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "OpencodeTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.show_model_selector and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: show_model_selector=True requires "
                "conversation_ui=True."
            )
        if self.default_model is not None and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: default_model requires conversation_ui=True."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"OpencodeTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
