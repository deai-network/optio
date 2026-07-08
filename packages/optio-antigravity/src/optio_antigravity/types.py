"""Public data types for optio-antigravity consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``AntigravityTaskConfig``.

Mirrors ``optio_grok.types`` — the config surface is kept parallel so the
generic session/snapshot/seed machinery reads identically. Only the
backend-specific pieces diverge (``agy``'s binary permission surface, its
state tree under ``~/.gemini``, its transcript-driven conversation mode).
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
from optio_agents.config_types import ClaustrumConfigMixin
from optio_agents.protocol.session import (
    CallerMessageCallback,
    DeliverableCallback,
    HookCallback,
)
from optio_agents.uploads import UploadCallback
from optio_host.types import SSHConfig

# AllowedDir, ConversationMode, ToolVerbosity, ThinkingVerbosity, SeedProvider
# and SeedUnavailableError are the shared config vocabulary owned by
# ``optio_agents``; they are imported above and re-exported here (see __all__)
# so existing ``from optio_antigravity.types import AllowedDir, …`` sites (in
# ``fs_allowlist.py``/``session.py``) keep working. The shared ``AllowedDir``
# validates ``mode`` at construction against the 4-value superset
# (``ro``/``rw``/``rox``/``rwx``); this Landlock-only claustrum treats
# ``rox``≡``ro`` and ``rwx``≡``rw`` (a Landlock read/write grant already covers
# execution). A leading ``~/`` in an ``AllowedDir.path`` is expanded against the
# REAL host home at launch time (the ``agy`` process runs under an isolated
# ``$HOME``, so grants cannot rely on its shell expansion).


__all__ = [
    "CallerMessageCallback",
    "DeliverableCallback",
    "HookCallback",
    "UploadCallback",
    "SSHConfig",
    "AntigravityTaskConfig",
    "PermissionMode",
    "ConversationMode",
    "ToolVerbosity",
    "ThinkingVerbosity",
    "SeedProvider",
    "SeedUnavailableError",
    "AllowedDir",
]


# agy's native permission surface is binary: normal prompting ("default") or
# ``--dangerously-skip-permissions`` (auto-approve every tool). optio's generic
# callers pass the claudecode-style ``bypassPermissions``; we accept it as an
# alias and map it to the skip flag at launch (see host_actions, Task 0.3+).
# NATIVE GAP: agy exposes no per-tool allow/deny grammar (only this binary
# skip), so — unlike claude/grok/cursor — this wrapper carries no
# ``allowed_tools``/``disallowed_tools`` fields (there is nothing to drive).
PermissionMode = Literal["default", "dangerously-skip-permissions"]
_PERMISSION_MODE_ALIASES = {"bypassPermissions": "dangerously-skip-permissions"}
_VALID_PERMISSION_MODES = {
    "default",
    "dangerously-skip-permissions",
} | set(_PERMISSION_MODE_ALIASES)

# Local-validation supersets for the shared ``ToolVerbosity``/``ThinkingVerbosity``
# Literals (the aliases themselves are imported from ``optio_agents`` above).
_VALID_TOOL_VERBOSITY = {"silent", "description-only", "verbose"}
_VALID_THINKING_VERBOSITY = {"hidden", "visible"}


def _identity_resume_refresh(
    config: "AntigravityTaskConfig",
) -> "AntigravityTaskConfig":
    """Default ``on_resume_refresh``: recompose AGENTS.md from the unchanged
    config on resume, so a resumed session picks up instruction/template
    changes instead of freezing at first launch (no config mutation)."""
    return config


@dataclass(frozen=True, kw_only=True)
class AntigravityTaskConfig(ClaustrumConfigMixin):
    """Configuration for one optio-antigravity task instance (Stage 0).

    Stage 0 covers iframe/ttyd mode on the local host. Resume, seeds,
    conversation mode, and filesystem isolation arrive in later stages.

    The claustrum filesystem-isolation triad (``fs_isolation`` /
    ``extra_allowed_dirs`` / ``delivery_type``) is inherited from the shared
    ``ClaustrumConfigMixin`` — those fields stay top-level here, so callers still
    write ``fs_isolation=`` / ``delivery_type=`` verbatim. The base is a frozen,
    keyword-only dataclass, so this config is too (all construction is by keyword,
    which every caller already does).
    """

    consumer_instructions: str

    agent_type: Literal["antigravity"] = "antigravity"

    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the agy
    # subprocess, so inherited provider creds don't override the task's
    # own configuration. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    # Passed through as ``--model``. Not validated — vendor strings change
    # (Gemini ids plus BYO Claude/GPT model ids exposed by ``agy models``). Also
    # the conversation model-picker's initial value (Stage 7): config.model
    # drives both the launch ``--model`` flag and the picker default.
    model: str | None = None

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for the optio-owned ``agy`` binary-cache directory on the host.
    install_dir: str | None = None
    ttyd_install_dir: str | None = None

    # When True, a fresh launch kicks off the first turn itself — iframe mode
    # types a trailing positional prompt, conversation mode sends the
    # AUTO_START_PROMPT ("Read AGENTS.md and execute the task it describes").
    # This is for UNATTENDED task execution; a task must opt in. Defaults to
    # False (parity with claudecode): a conversation/chat task must NOT
    # auto-fire a kickoff, or agy starts an agentic loop on launch and blocks
    # the operator's first real prompt (queued behind it).
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

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment (agy's Google
    # OAuth token store + ~/.gemini/antigravity-cli/settings.json) into a fresh
    # workdir before launch, beginning a NEW session already logged-in. A plain
    # string is used as-is; a SeedProvider callable is awaited at launch to
    # resolve one (Stage 4 lease path). Baked at task-creation time; ignored on
    # resume.
    seed_id: "str | SeedProvider | None" = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary (None in Stage 3; resolved
    # in a later stage). Its presence is what enables seed capture. Both
    # default None, so existing consumers are unaffected. Both are ignored on
    # resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    # Resume machinery (Stage 2). ON by default: agy persists its conversation
    # under ~/.gemini/antigravity inside the per-task home, so restoring the
    # workdir tar + passing --continue/--conversation rehydrates the session.
    supports_resume: bool = True
    # fnmatch patterns of workdir paths to omit from the resume snapshot tar.
    # None → the framework defaults (see optio_host.archive). Keep the
    # ~/.gemini state OUT of this list: it carries the transcript + settings
    # that --continue/--conversation need.
    workdir_exclude: list[str] | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the resume
    # workdir tar (which carries agy's ~/.gemini conversation state, so it IS
    # the session blob here) at the GridFS write/read. Both set → encrypted at
    # rest; both None (default) → plaintext streamed unchanged. Setting only one
    # is a config error (asymmetric usage is always a mistake).
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    # Hook fired on resume only (never on fresh start). Receives the original
    # config; returns a (possibly mutated) config. The harness re-renders
    # AGENTS.md from the returned config and writes it back only when it differs
    # from the restored file, tagging the next resume.log line
    # `REFRESHED:AGENTS.md` so the agent re-reads. Default = identity (recompose
    # from the same config, so instruction/template changes reach a resumed
    # session instead of freezing at first launch). Pass None to disable.
    on_resume_refresh: "Callable[[AntigravityTaskConfig], AntigravityTaskConfig] | None" = _identity_resume_refresh

    # "iframe" = ttyd TUI (Stages 0-5). "conversation" = synthetic
    # transcript-driven turns; the task publishes a live AntigravityConversation
    # (Stage 6).
    mode: ConversationMode = "iframe"
    # Opt-out for the optio.log keyword channel (STATUS/DELIVERABLE/DONE/…).
    # iframe mode requires True (it is the only completion signal there);
    # conversation mode may set it False (the Conversation drives completion).
    host_protocol: bool = True

    # --- conversation surface (Stage 6) ---------------------------------
    # Conversation mode only: route a per-turn permission request to the
    # published conversation's on_permission_request handler. agy turns run
    # ``--dangerously-skip-permissions`` (turn-level, no interactive gate — see
    # design §7), so this is largely advisory; kept for surface parity.
    permission_gate: bool = False
    # Opt-in dashboard conversation UI: the task starts a per-task listener and
    # publishes a live chat widget (wired in Stage 6). Conversation mode only.
    conversation_ui: bool = False
    # How much tool-call detail the conversation widget renders; only affects
    # conversation_ui rendering.
    tool_verbosity: ToolVerbosity = "description-only"

    # Whether the conversation widget shows agy's reasoning/thinking traces.
    # Default hidden — thinking is noisy; opt in per task.
    thinking_verbosity: ThinkingVerbosity = "hidden"

    # --- conversation frontend parity (Stage 7) -------------------------
    # The widget's model picker preselects ``config.model`` (falling back to the
    # live ``agy models`` default); there is no separate ``default_model`` knob.
    # Show the engine-neutral session controls in the conversation widget.
    # agy switches model by restarting the session with --model <new> +
    # --continue (claudecode precedent — no inline switch). Requires
    # mode="conversation" and conversation_ui=True.
    show_session_controls: bool = False
    # Replace the generic working-spinner with antigravity's on-brand native
    # spinner (agy's braille "dots") in the conversation widget. Requires
    # mode="conversation" and conversation_ui=True.
    native_spinner: bool = False
    # Show the file-upload control. Uploaded bytes are written under
    # <workdir>/uploads and referenced to agy via a System: path line so it
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
    # Offer download links for files agy marks with the optio-file: sentinel
    # (deliverables also land in ~/.gemini/antigravity/artifacts/). The listener
    # serves GET /download for paths confined under <workdir>. Requires
    # mode="conversation" and conversation_ui=True.
    file_download: bool = False
    # Upper bound (bytes) on a single downloaded file; the listener rejects
    # anything larger with HTTP 413. Mirrored to the widget via widgetData.
    max_download_bytes: int = 10_000_000

    # --- filesystem isolation (Stage 8) ---------------------------------
    # ``fs_isolation`` / ``extra_allowed_dirs`` / ``delivery_type`` are inherited
    # from ``ClaustrumConfigMixin``. Claustrum (Landlock, fail-CLOSED) confines
    # the agy process and every tool/subprocess it spawns to the task workdir +
    # explicit grants; if the kernel can't apply the sandbox the launch refuses
    # to start. ``delivery_type`` is MANDATORY while ``fs_isolation`` is on — it
    # routes the "a newer claustrum release is available" security notice through
    # ``on_deliverable`` (see ``_validate_claustrum``).

    def __post_init__(self) -> None:
        # Fail fast on a missing delivery_type (or a bad extra_allowed_dirs mode)
        # before any other check.
        self._validate_claustrum()
        if (
            self.permission_mode is not None
            and self.permission_mode not in _VALID_PERMISSION_MODES
        ):
            raise ValueError(
                f"AntigravityTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        if self.mode not in ("iframe", "conversation"):
            raise ValueError(
                f"AntigravityTaskConfig.mode={self.mode!r} is not one of "
                "['iframe', 'conversation']"
            )
        if self.mode == "iframe" and not self.host_protocol:
            raise ValueError(
                "AntigravityTaskConfig: host_protocol=False requires "
                "mode='conversation' (in iframe mode the optio.log keyword "
                "channel is the only completion signal)."
            )
        if self.permission_gate and self.mode != "conversation":
            raise ValueError(
                "AntigravityTaskConfig: permission_gate=True requires "
                "mode='conversation'."
            )
        if self.conversation_ui and self.mode != "conversation":
            raise ValueError(
                "AntigravityTaskConfig: conversation_ui=True requires "
                "mode='conversation'."
            )
        if self.tool_verbosity not in _VALID_TOOL_VERBOSITY:
            raise ValueError(
                f"AntigravityTaskConfig.tool_verbosity={self.tool_verbosity!r} "
                f"is not one of {sorted(_VALID_TOOL_VERBOSITY)}"
            )
        if self.thinking_verbosity not in _VALID_THINKING_VERBOSITY:
            raise ValueError(
                f"AntigravityTaskConfig.thinking_verbosity="
                f"{self.thinking_verbosity!r} is not one of "
                f"{sorted(_VALID_THINKING_VERBOSITY)}"
            )
        # Frontend-parity features are opt-in flags that only make sense with
        # the conversation UI wired (mirrors optio-claudecode).
        conv_ui = self.mode == "conversation" and self.conversation_ui
        if self.show_session_controls and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: show_session_controls=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.native_spinner and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: native_spinner=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.show_file_upload and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: show_file_upload=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        if self.file_download and not conv_ui:
            raise ValueError(
                "AntigravityTaskConfig: file_download=True requires "
                "mode='conversation' and conversation_ui=True."
            )
        # extra_allowed_dirs entries self-validate at construction (the shared
        # AllowedDir __post_init__ rejects any mode outside the ro/rw/rox/rwx
        # superset), so no extra per-entry check is needed here.
        for field_name in ("install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"AntigravityTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
        # Session-blob transforms are all-or-nothing (mirrors optio-claudecode).
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "AntigravityTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together or both left None; "
                "one without the other is a config error."
            )
