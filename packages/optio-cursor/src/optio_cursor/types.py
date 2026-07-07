"""Public data types for optio-cursor consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``CursorTaskConfig``.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from optio_agents import (
    AllowedDir,
    ConversationMode,
    SeedProvider,
    SeedUnavailableError,
    ThinkingVerbosity,
    ToolVerbosity,
)
from optio_agents.protocol.session import (
    CallerMessageCallback,
    DeliverableCallback,
    HookCallback,
)
from optio_agents.uploads import UploadCallback
from optio_host.types import SSHConfig


__all__ = [
    "CallerMessageCallback",
    "DeliverableCallback",
    "HookCallback",
    "UploadCallback",
    "SSHConfig",
    "CursorTaskConfig",
    "SandboxMode",
    "ConversationMode",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "SeedProvider",
    "SeedUnavailableError",
    "AllowedDir",
]


# Passed through as ``--sandbox``. cursor-agent's own filesystem sandbox
# toggle; None omits the flag (cursor's default applies). Engine-native — kept
# local (not part of the shared config vocabulary).
SandboxMode = Literal["enabled", "disabled"]
_VALID_SANDBOX_MODES = {"enabled", "disabled"}

# Validation sets for the shared ToolVerbosity/ThinkingVerbosity Literals,
# used by ``__post_init__`` (the aliases themselves come from optio_agents).
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


def _identity_resume_refresh(config: "CursorTaskConfig") -> "CursorTaskConfig":
    """Default ``on_resume_refresh``: recompose AGENTS.md from the unchanged
    config on resume, so a resumed session picks up instruction/template
    changes instead of freezing at first launch (no config mutation)."""
    return config


@dataclass
class CursorTaskConfig:
    """Configuration for one optio-cursor task instance (Stage 0).

    Stage 0 covers iframe/ttyd mode on the local host. Resume, seeds,
    conversation mode, and filesystem isolation arrive in later stages.
    """

    consumer_instructions: str

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the cursor-agent
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    # Permission rules are config-planted, not argv: cursor-agent has no
    # --allow/--deny flags. These map to ``permissions.allow`` /
    # ``permissions.deny`` in ``<home>/.cursor/cli-config.json``.
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # ``--force``: skip cursor's interactive confirmation prompts.
    force: bool = False
    # ``--auto-review``: cursor reviews its own edits before applying.
    auto_review: bool = False
    # ``--sandbox enabled|disabled``: cursor-agent's native sandbox toggle.
    sandbox: SandboxMode | None = None
    # Passed through as ``--model``. Not validated — vendor strings change.
    model: str | None = None
    # Injected as ``CURSOR_API_KEY`` in the launch env, never argv.
    api_key: str | None = None

    # --- filesystem isolation (Stage 8, claustrum) ----------------------
    # When True (default), cursor-agent runs confined to an explicit
    # filesystem allowlist (task workdir + temp dirs + explicit grants),
    # kernel-enforced via the claustrum Landlock sandbox wrapping the WHOLE
    # cursor-agent process tree. Fail-closed: if claustrum cannot be
    # provisioned or the kernel lacks Landlock, the task refuses to launch
    # rather than run unconfined. Set False to opt a single task out.
    #
    # (Cursor's own ``--sandbox enabled`` is NOT used for this: it is a
    # per-shell-command wrapper only, so the agent's in-process Write/Edit
    # tools escape it. See the Stage-8 design doc Decision 6.)
    fs_isolation: bool = True
    # Additional path grants beyond the workdir + temp dirs (never masks the
    # baseline). ``~/`` expands against the real host home at launch. Ignored
    # when fs_isolation=False.
    extra_allowed_dirs: list["AllowedDir"] | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for where the ``cursor-agent`` binary is resolved on the host.
    install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch passes a trailing positional prompt
    # ("Read AGENTS.md and execute the task it describes") so cursor-agent
    # starts the task unattended. Defaults False (parity with
    # claudecode/grok/codex): an interactive/conversation task that does not
    # set this must NOT auto-fire a kickoff — it would start an agentic loop
    # that blocks the operator's first real chat message. Task-execution
    # surfaces opt in explicitly.
    auto_start: bool = False

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None
    # Enable the CLIENT_MESSAGE keyword: agent-pushed messages routed to the
    # originating browser session's frontend (stored as sessionEvents,
    # surfaced via optio-ui's onClientMessage). Off by default.
    use_client_messages: bool = False
    # Enable the CALLER_MESSAGE keyword: agent-pushed messages routed to this
    # callback in the embedding application. A non-None return value is sent
    # back to the agent as feedback. Off (None) by default.
    on_caller_message: CallerMessageCallback | None = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (cursor
    # auth.json + cli-config.json) into a fresh workdir before launch,
    # beginning a NEW session already logged-in. A plain string is used as-is;
    # a SeedProvider callable is awaited at launch to resolve one (Stage 4
    # lease path). Baked at task-creation time; ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary (None in Stage 3; resolved
    # in a later stage). Its presence is what enables seed capture. Both
    # default None, so existing consumers are unaffected. Both are ignored on
    # resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: cursor persists its chat
    # state under $HOME/.cursor inside the workdir, so restoring the workdir
    # tar + passing --continue rehydrates the conversation.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep home/.cursor
    # OUT of this list: it carries the cursor chat state --continue needs.
    workdir_exclude: list[str] | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the resume
    # snapshot's workdir tar at GridFS write/read (cursor persists its whole
    # session — including home/.cursor chat state — in that single tar). Both
    # set → encrypted at rest; both None (default) → plaintext. Setting only one
    # is a config error (asymmetric usage is always a mistake).
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    # Hook fired on resume only (never on fresh start). Receives the original
    # config; returns a (possibly mutated) config. The harness re-renders
    # AGENTS.md from the returned config and writes it back only when it differs
    # from the file on disk, tagging the next resume.log line with
    # ``REFRESHED:AGENTS.md`` so the agent re-reads. Default = identity
    # (recompose from the same config, so instruction/template changes reach a
    # resumed session instead of freezing at first launch). Pass None to disable.
    on_resume_refresh: "Callable[[CursorTaskConfig], CursorTaskConfig] | None" = _identity_resume_refresh

    # "iframe" = ttyd TUI (Stages 0-5). "conversation" = headless ACP
    # (cursor-agent acp); the task publishes a live CursorConversation
    # (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route cursor's ACP session/request_permission to
    # the published conversation's on_permission_request handler (the caller
    # registers one). When False, cursor launches with --force so tools run
    # without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Group 6b). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"
    # Whether the conversation widget shows cursor's reasoning/thinking traces
    # (agent_thought_chunk). Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Show the engine-neutral session controls (the model picker) in the
    # conversation widget. Cursor switches model inline over ACP
    # (session/set_model — grok's live-pinned mechanism; cursor
    # runtime-unverified, see models.py) — no process restart. Requires
    # mode="conversation" and conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with cursor's on-brand native spinner
    # in the conversation widget. Requires mode='conversation' and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes are written under
    # <workdir>/uploads and referenced to cursor via a System: path line so it
    # reads them with its own tools (headless cursor has no inline ingest).
    # Requires mode="conversation" and conversation_ui=True.
    show_file_upload: bool = False
    # Per-task upload hook, fired (additively to the System: path line) after an
    # uploaded file lands under <workdir>/uploads. Mirrors on_deliverable minus
    # the text arg: ``async on_upload(hook_ctx, "uploads/<name>")``. Optional;
    # the workdir write + System: reference happen whether or not it is set.
    on_upload: UploadCallback | None = None
    # Upper bound (bytes) on a single uploaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files cursor marks with the optio-file: sentinel.
    # The listener serves GET /download for paths confined under <workdir>.
    # Requires mode="conversation" and conversation_ui=True.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    def __post_init__(self) -> None:
        if self.sandbox is not None and self.sandbox not in _VALID_SANDBOX_MODES:
            raise ValueError(
                f"CursorTaskConfig.sandbox={self.sandbox!r} "
                f"is not one of {sorted(_VALID_SANDBOX_MODES)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"CursorTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "CursorTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "CursorTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "CursorTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"CursorTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"CursorTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense with
        # the conversation UI wired (mirrors optio-grok/claudecode).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "CursorTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "CursorTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "CursorTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "CursorTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        for field_name in ("install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"CursorTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "CursorTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
        for ad in self.extra_allowed_dirs or []:
            if ad.mode not in ("ro", "rw", "rox", "rwx"):
                raise ValueError(
                    f"CursorTaskConfig.extra_allowed_dirs: mode={ad.mode!r} "
                    f"must be one of 'ro', 'rw', 'rox', 'rwx' (path={ad.path!r})."
                )
