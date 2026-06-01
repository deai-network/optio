"""Public data types for optio-claudecode consumers.

The generic ``DeliverableCallback`` / ``HookCallback`` types are owned by
``optio-agents`` and ``SSHConfig`` by ``optio-host``. This module
re-exports them alongside the package-specific ``ClaudeCodeTaskConfig``.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from optio_agents.protocol.session import DeliverableCallback, HookCallback
from optio_host.types import SSHConfig


__all__ = [
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "ClaudeCodeTaskConfig",
    "PermissionMode",
]


PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions"]
_VALID_PERMISSION_MODES = {"default", "plan", "acceptEdits", "bypassPermissions"}


@dataclass
class ClaudeCodeTaskConfig:
    """Configuration for one optio-claudecode task instance.

    See ``docs/2026-05-28-optio-claudecode-design.md`` for full field
    semantics.
    """

    consumer_instructions: str

    credentials_json: dict[str, Any] | bytes | str | None = None
    claude_config: dict[str, Any] | None = None
    env: dict[str, str] | None = None
    # Glob patterns (fnmatch) of env var NAMES to strip from the Claude Code
    # subprocess, so inherited provider creds (e.g. ANTHROPIC_API_KEY) don't
    # override the customer's subscription seed. e.g. ["*_API_KEY", "*_TOKEN"].
    scrub_env: list[str] | None = None

    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    # When True, a fresh launch passes a trailing positional prompt
    # ("Read CLAUDE.md and execute the task it describes") so claude starts the
    # task unattended. Suppressed on resume (--continue) to avoid re-triggering.
    auto_start: bool = False
    # When True, run claude in focus view + fullscreen TUI (settings.json
    # tui=fullscreen, viewMode=focus) with CLAUDE_CODE_NO_FLICKER=1 in the launch
    # env, so tool calls collapse to one-line summaries instead of showing every
    # bash command/output. Layered onto any consumer-supplied claude_config.
    focus_mode: bool = False

    ssh: SSHConfig | None = None

    install_if_missing: bool = True
    install_ttyd_if_missing: bool = True
    # Override for the optio-owned claude **version cache** directory (where
    # claude version binaries are installed/cached on the worker, via the
    # per-task home/.local/share/claude/versions symlink). None → the worker's
    # ``OPTIO_CLAUDECODE_CACHE_DIR`` or ``${XDG_CACHE_HOME:-$HOME/.cache}/
    # optio-claudecode/versions``. Never the host user's ~/.local/~/.claude.
    claude_install_dir: str | None = None
    ttyd_install_dir: str | None = None

    before_execute: HookCallback | None = None
    after_execute: HookCallback | None = None
    on_deliverable: DeliverableCallback | None = None

    # --- resume surface (mirrors OpencodeTaskConfig) --------------------
    supports_resume: bool = True
    workdir_exclude: list[str] | None = None
    # Optional pair of synchronous bytes->bytes transforms wrapping the
    # home/.claude session tar at GridFS write/read. Both set → encrypted
    # at rest; both None (default) → plaintext. Setting only one is a
    # config error (asymmetric usage is always a mistake).
    session_blob_encrypt: Callable[[bytes], bytes] | None = None
    session_blob_decrypt: Callable[[bytes], bytes] | None = None
    # Optional hook fired on resume only (never on fresh start). Receives
    # the original config; returns a (possibly mutated) config. The harness
    # re-renders CLAUDE.md from the returned config and writes it back only
    # when it differs from the file on disk, tagging the next resume.log
    # line with `REFRESHED:CLAUDE.md`. None (default) → no refresh.
    on_resume_refresh: "Callable[[ClaudeCodeTaskConfig], ClaudeCodeTaskConfig] | None" = None

    # --- seed surface (start fresh from a stored environment) -----------
    # Consumed (default/fallback): merge this seed's environment into a
    # fresh workdir before launch, beginning a NEW conversation (no
    # --continue). Baked at task-creation time; no per-launch channel.
    seed_id: str | None = None
    # Capture intent: a (sync or async) callback fired on teardown of a fresh
    # session after a successful capture, with two args: (seed_id, info).
    # ``info`` is a human-readable account summary derived from the seeded
    # OAuth token (e.g. "Plan: Claude Max 20x for Jane Doe <jane@x.com>"), or
    # None if it could not be resolved. Its presence is what enables seed
    # capture. Both default None, so existing consumers are unaffected. Both
    # are ignored on resume.
    on_seed_saved: "Callable[[str, str | None], Awaitable[None] | None] | None" = None

    def __post_init__(self) -> None:
        if self.permission_mode is not None and self.permission_mode not in _VALID_PERMISSION_MODES:
            raise ValueError(
                f"ClaudeCodeTaskConfig.permission_mode={self.permission_mode!r} "
                f"is not one of {sorted(_VALID_PERMISSION_MODES)}"
            )
        for field_name in ("claude_install_dir", "ttyd_install_dir"):
            val = getattr(self, field_name)
            if val is not None and not val.startswith("/") and not val.startswith("~"):
                raise ValueError(
                    f"ClaudeCodeTaskConfig.{field_name}={val!r} must be an "
                    f"absolute path (start with '/' or '~')."
                )
        e = self.session_blob_encrypt is not None
        d = self.session_blob_decrypt is not None
        if e != d:
            raise ValueError(
                "ClaudeCodeTaskConfig: session_blob_encrypt and "
                "session_blob_decrypt must be set together (both callables) "
                "or both left as None; one without the other is a config error."
            )
