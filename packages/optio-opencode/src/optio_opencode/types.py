"""Public data types for optio-opencode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` (they describe the log/deliverables protocol); ``SSHConfig``
is owned by ``optio-host``. This module re-exports them so existing
``from optio_opencode.types import ...`` imports keep working unchanged.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from optio_agents.protocol.session import (
    CallerMessageCallback,
    DeliverableCallback,
    HookCallback,
)
from optio_agents.uploads import UploadCallback
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
# Visibility of reasoning/thinking traces in the conversation widget. Task-level,
# mirrors ToolVerbosity; the UI never decides. (opencode reasoning is not yet wired
# to a distinct thinking row, but the option ships for cross-engine parity.)
ThinkingVerbosity = Literal["hidden", "visible"]
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


__all__ = [
    "CallerMessageCallback",
    "DeliverableCallback",
    "HookCallback",
    "UploadCallback",
    "SSHConfig",
    "SeedProvider",
    "ConversationMode",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "OpencodeTaskConfig",
]


def _identity_resume_refresh(config: "OpencodeTaskConfig") -> "OpencodeTaskConfig":
    """Default ``on_resume_refresh``: recompose AGENTS.md from the unchanged
    config on resume, so a resumed session picks up instruction/template
    changes instead of freezing at first launch (no config mutation)."""
    return config


@dataclass
class OpencodeTaskConfig:
    """Configuration for one optio-opencode task instance."""
    consumer_instructions: str
    opencode_config: dict[str, Any] = field(default_factory=dict)
    ssh: SSHConfig | None = None
    on_deliverable: DeliverableCallback | None = None
    # Enable the CLIENT_MESSAGE keyword: agent-pushed messages routed to the
    # originating browser session's frontend (stored as sessionEvents,
    # surfaced via optio-ui's onClientMessage). Off by default.
    use_client_messages: bool = False
    # Enable the CALLER_MESSAGE keyword: agent-pushed messages routed to this
    # callback in the embedding application. A non-None return value is sent
    # back to the agent as feedback. Off (None) by default.
    on_caller_message: CallerMessageCallback | None = None
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
    # Hook fired on resume only (never on fresh start). Receives the original
    # task config; returns a (possibly mutated/replaced) config. The harness
    # re-renders AGENTS.md from the returned config and writes it back only
    # when it differs from the file on disk, tagging the next resume.log line
    # with `REFRESHED:AGENTS.md` so the agent re-reads. Default = identity
    # (recompose from the same config, so instruction/template changes reach a
    # resumed session instead of freezing at first launch). Pass None to
    # disable refresh and keep the original AGENTS.md.
    on_resume_refresh: Callable[["OpencodeTaskConfig"], "OpencodeTaskConfig"] | None = _identity_resume_refresh

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
    # Whether the conversation widget shows the agent's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"
    # Default model for a fresh conversation session, "providerID/modelID".
    # Forwarded to the widget, which applies it once at the start of a non-
    # resumed session (history empty) and only if present in the live model
    # list. Effective regardless of show_session_controls. Requires
    # conversation_ui=True.
    default_model: str | None = None
    # Show the model picker in the conversation widget. Requires
    # conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with opencode's on-brand native
    # spinner in the conversation widget. Requires mode='conversation' and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-attach control in the conversation widget. Requires
    # conversation_ui=True. Uploads flow through the generic optio-api
    # /api/widget-upload route → materializeUpload RPC → the per-task writer,
    # which lands each file in <workdir>/uploads/<name>.
    show_file_upload: bool = False
    # Per-file size cap enforced client-side before the file is POSTed.
    max_upload_bytes: int = 10_000_000
    # Optional per-task callback fired after each upload materializes, with
    # (hook_ctx, "uploads/<name>"). Additive to the System: LLM announce.
    on_upload: UploadCallback | None = None
    # Let the agent hand produced files to the user as one-click downloads
    # (optio-file: sentinel links). Requires conversation_ui=True. Adds the
    # downloadables instruction to AGENTS.md and the widget download handler.
    file_download: bool = False
    max_download_bytes: int = 10_000_000

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
        if self.show_session_controls and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: show_session_controls=True requires "
                "conversation_ui=True."
            )
        if self.native_spinner and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: show_file_upload=True requires conversation_ui=True."
            )
        if self.file_download and not self.conversation_ui:
            raise ValueError(
                "OpencodeTaskConfig: file_download=True requires conversation_ui=True."
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
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"OpencodeTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
