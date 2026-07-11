"""Public data types for optio-kimicode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``KimiCodeTaskConfig``.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from optio_agents import (
    AllowedDir,
    CallerMessageCallback,
    ConversationMode,
    SeedProvider,
    SeedUnavailableError,
    ThinkingVerbosity,
    TOOL_VERBOSITIES,
    ToolVerbosity,
)
from optio_agents.config_types import ClaustrumConfigMixin
from optio_agents.protocol.session import (
    DeliverableCallback,
    HookCallback,
)
from optio_agents.uploads import UploadCallback
from optio_host.types import SSHConfig


# The engine-neutral config vocabulary (``ConversationMode``/``ToolVerbosity``/
# ``ThinkingVerbosity``/``SeedProvider``/``SeedUnavailableError``/``AllowedDir``)
# is owned by ``optio_agents.config_types`` and imported above. It is re-exported
# here (kept in ``__all__``) so existing ``from optio_kimicode.types import
# ConversationMode, AllowedDir, …`` sites keep working unchanged. ``AllowedDir``
# now carries the 4-value superset mode (``ro``/``rw``/``rox``/``rwx``); kimi is
# Landlock-only (claustrum), so ``rox``≡``ro`` and ``rwx``≡``rw`` (claustrum's
# ``--rox``/``--rwx`` flags express the execute bit natively when supplied).
__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "UploadCallback",
    "CallerMessageCallback",
    "SSHConfig",
    "KimiCodeTaskConfig",
    "PermissionMode",
    "ConversationMode",
    "Effort",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "SeedProvider",
    "SeedUnavailableError",
    "AllowedDir",
]


def _identity_resume_refresh(config: "KimiCodeTaskConfig") -> "KimiCodeTaskConfig":
    """Default ``on_resume_refresh``: recompose AGENTS.md from the unchanged
    config on resume, so a resumed session picks up instruction/template changes
    instead of freezing at first launch (no config mutation)."""
    return config


# kimi's own permission modes (``PermissionModeSchema`` in kimi-code:
# agent-core/config/schema.ts). ``yolo`` = auto-approve every action (blanket
# permissions); ``auto`` = auto permission mode; ``manual`` = prompt. Wired to the
# task's ``config.toml`` ``default_permission_mode`` (host_actions.write_kimi_config),
# which the daemon applies to every session it creates — iframe and conversation
# alike (core-impl createSession: ``options.permission ?? config.defaultPermissionMode``).
PermissionMode = Literal["manual", "auto", "yolo"]
_VALID_PERMISSION_MODES = {"manual", "auto", "yolo"}

# ``ConversationMode`` ("iframe" = the native ``kimi web`` SPA, Stages 0-5;
# "conversation" = a headless ``kimi acp`` ACP-over-stdio session that publishes
# a live KimiCodeConversation, Stage 6) is imported from optio_agents; validated
# inline in __post_init__ against the literal tuple.

# Reasoning effort passed through as ``--effort``. kimi exposes a fixed enum
# (unlike a free-form vendor string); ``model`` stays an unvalidated alias.
Effort = Literal["low", "medium", "high", "xhigh", "max"]
_VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}

# Live reasoning-effort level for the graded ``thinking`` session control
# (fork ``kimi-code >= 0.23.1-csillag.2`` / ``csillag/acp-graded-thinking``).
# Distinct from ``effort`` (the launch flag): this seeds the live
# ``reasoning_effort`` slider's initial value (projected from the graded
# ``configOptions`` thinking option) and includes ``off`` — the level the
# fork exposes when a model can disable thinking entirely. Applied at launch
# like ``model`` (it overrides the control's initial displayed value; live
# changes route to ``session/set_config_option {configId:"thinking"}``).
ReasoningEffort = Literal["off", "low", "medium", "high", "xhigh", "max"]
_VALID_REASONING_EFFORTS = {"off", "low", "medium", "high", "xhigh", "max"}

# ``ToolVerbosity`` (tool-call rendering detail) and ``ThinkingVerbosity``
# (reasoning-trace visibility) are imported from optio_agents; the validation
# sets stay local (consumed by __post_init__).
_VALID_TOOL_VERBOSITY = TOOL_VERBOSITIES
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


@dataclass(frozen=True, kw_only=True)
class KimiCodeTaskConfig(ClaustrumConfigMixin):
    """Configuration for one optio-kimicode task instance.

    Stage 0 covers iframe (``kimi web``) mode on the local host. Resume,
    seeds, conversation mode, and filesystem isolation arrive in later stages.

    The claustrum filesystem-isolation triad (``fs_isolation`` /
    ``extra_allowed_dirs`` / ``delivery_type``) is inherited from the shared
    ``ClaustrumConfigMixin`` — the fields stay top-level (callers write
    ``fs_isolation=`` / ``delivery_type=`` verbatim). ``delivery_type`` is
    MANDATORY when ``fs_isolation`` is on (validated in ``__post_init__`` via
    ``_validate_claustrum``). The config is frozen (immutable) + keyword-only so
    the mixin's all-defaulted fields can precede the required
    ``consumer_instructions`` without a field-ordering clash.
    """

    consumer_instructions: str

    agent_type: Literal["kimicode"] = "kimicode"

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the kimi
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    # Per-tool allow/deny, wired to kimi's native permission grammar: each name
    # is emitted as a ``[[permission.rules]]`` table in the task's config.toml
    # (``{decision="deny"/"allow", pattern="<tool>"}``) by
    # ``host_actions.write_kimi_config``. A bare tool name matches that tool;
    # deny rules take precedence. None → no rules (permission falls to
    # ``permission_mode`` / kimi's default).
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # ``model`` is a kimi model ALIAS (not a raw id); it is not enum-validated
    # (the alias catalog changes). It is the single model field: it seeds the
    # conversation widget's model-picker initial value (conversation_ui), falling
    # back to the live ACP current model when None. ``effort`` is passed through
    # as ``--effort`` and IS validated against the fixed low..max enum.
    model: str | None = None
    effort: Effort | None = None
    # Live graded reasoning-effort seed for the ``reasoning_effort`` session
    # control (the fork's now-graded ``thinking`` configOption). Applied at
    # launch as the slider's initial value (like ``model``); live changes route
    # to ``session/set_config_option {configId:"thinking"}``. Validated against
    # the off..max enum. Independent of ``effort`` (the ``--effort`` launch flag).
    reasoning_effort: ReasoningEffort | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    # Override for where the ``kimi`` binary is resolved/cached on the host.
    install_dir: str | None = None

    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes"). This
    # is for UNATTENDED task execution; a task must opt in. Defaults to False
    # (parity with claudecode): a conversation/chat task must NOT auto-fire a
    # kickoff, or kimi starts an agentic loop on launch and blocks the
    # operator's first real prompt (queued behind it as task_already_running).
    auto_start: bool = False

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # Enable the CLIENT_MESSAGE keyword: agent-pushed messages routed to the
    # originating browser session's frontend (stored as sessionEvents, surfaced
    # via optio-ui's onClientMessage). Off by default.
    use_client_messages: bool = False
    # Enable the CALLER_MESSAGE keyword: agent-pushed messages routed to this
    # callback in the embedding application. A non-None return value is sent
    # back to the agent as feedback. Off (None) by default.
    on_caller_message: CallerMessageCallback | None = None

    # Optional pair of synchronous bytes->bytes transforms wrapping the kimi
    # session subtree tar at GridFS write/read (the two-blob snapshot's
    # sessionBlobId, mirrors optio-opencode/optio-claudecode). When both are
    # set, the session blob is encrypted AT REST; when both are None (default),
    # plaintext is used (backward-compatible). Setting only one raises a config
    # error: asymmetric usage is always a mistake.
    session_blob_encrypt: "Callable[[bytes], bytes] | None" = None
    session_blob_decrypt: "Callable[[bytes], bytes] | None" = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (kimi
    # credentials/kimi-code.json) into a fresh workdir before launch, beginning
    # a NEW session already logged-in. A plain string is used as-is; a
    # SeedProvider callable is awaited at launch to resolve one (Stage 4
    # lease path). Baked at task-creation time; ignored on resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary. Its presence is what enables
    # seed capture. Both default None, so existing consumers are unaffected.
    # Both are ignored on resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: kimi persists its session
    # under <KIMI_CODE_HOME>/sessions inside the workdir, so restoring the
    # workdir tar + passing --continue rehydrates the conversation.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep the kimi
    # session state that --continue needs IN the snapshot.
    workdir_exclude: list[str] | None = None
    # Hook fired on resume only (never on fresh start). Receives the original
    # config; returns a (possibly mutated) config. The harness re-renders
    # AGENTS.md from the returned config and writes it back only when it differs
    # from the file on disk, tagging the next resume.log line with
    # ``REFRESHED:AGENTS.md`` so the agent re-reads. Default = identity
    # (recompose from the same config, so instruction/template changes reach a
    # resumed session instead of freezing at first launch). Pass None to disable.
    on_resume_refresh: "Callable[[KimiCodeTaskConfig], KimiCodeTaskConfig] | None" = _identity_resume_refresh

    # "iframe" = native kimi web SPA (Stages 0-5). "conversation" = headless
    # ACP (kimi acp stdio); the task publishes a live KimiCodeConversation
    # (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route kimi's ACP session/request_permission to
    # the published conversation's on_permission_request handler (the caller
    # registers one). When False, kimi launches so tools run without prompting.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Group 5). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    # Whether the conversation widget shows kimi's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # Show the engine-neutral session-controls bar (model + thinking + mode) in
    # the conversation widget. kimi switches inline over ACP (session/set_model
    # for the model, session/set_config_option for thinking/mode — no process
    # restart). Requires mode="conversation" and conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with kimicode's on-brand native
    # spinner in the conversation widget. Requires mode="conversation" and
    # conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes are written under
    # <workdir>/uploads and referenced to kimi via a System: path line so it
    # reads them with its own tools. Requires mode="conversation" and
    # conversation_ui=True.
    show_file_upload: bool = False
    # Optional per-task callback fired AFTER an upload lands under
    # <workdir>/uploads (additive to the System: path line the view injects).
    # Mirrors on_deliverable minus the text arg: ``async on_upload(hook_ctx,
    # "uploads/<name>")``. A raising callback is logged, not fatal.
    on_upload: UploadCallback | None = None
    # Upper bound (bytes) on a single uploaded file; the client enforces it and
    # the generic optio-api upload route rejects oversize. Mirrored to the
    # widget via widgetData.
    max_upload_bytes: int = 10_000_000
    # Offer download links for files kimi marks with the optio-file: sentinel.
    # The listener serves GET /download for paths confined under <workdir>.
    # Requires mode="conversation" and conversation_ui=True.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    # --- filesystem isolation (Stage 8) ---------------------------------
    # The fs-isolation triad (fs_isolation / extra_allowed_dirs / delivery_type)
    # is inherited from ClaustrumConfigMixin. Confine the kimi process (and every
    # tool/subprocess it spawns) to the task workdir + temp dirs + explicit
    # grants, kernel-enforced via the claustrum Landlock sandbox. Fail-CLOSED and
    # default-ON; delivery_type is mandatory while fs_isolation is on.

    def __post_init__(self) -> None:
        # The claustrum triad is validated FIRST so a missing delivery_type fails
        # fast (before the engine-specific checks below).
        self._validate_claustrum()
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "KimiCodeTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
        if (
            self.permission_mode is not None
            and self.permission_mode not in _VALID_PERMISSION_MODES
        ):
            raise ValueError(
                f"KimiCodeTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        if self.effort is not None and self.effort not in _VALID_EFFORTS:
            raise ValueError(
                f"KimiCodeTaskConfig.effort={self.effort!r} is not one of "
                f"{sorted(_VALID_EFFORTS)}"
            )
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in _VALID_REASONING_EFFORTS
        ):
            raise ValueError(
                f"KimiCodeTaskConfig.reasoning_effort={self.reasoning_effort!r} "
                f"is not one of {sorted(_VALID_REASONING_EFFORTS)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"KimiCodeTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "KimiCodeTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "KimiCodeTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "KimiCodeTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"KimiCodeTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"KimiCodeTaskConfig.thinking_verbosity={self.thinking_verbosity!r} "
                f"is not one of {sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense with
        # the conversation UI wired (mirrors optio-claudecode).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "KimiCodeTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        # extra_allowed_dirs entries are mode-validated by AllowedDir.__post_init__
        # and by _validate_claustrum (called first above); kimi is Landlock-only,
        # so rox≡ro and rwx≡rw (claustrum expresses the execute bit natively).
        for field_name in ("install_dir",):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"KimiCodeTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
