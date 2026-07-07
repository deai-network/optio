"""Cross-engine config-parity guard (Spec A).

Introspects all 7 wrapper TaskConfig dataclasses and asserts the harmonized
common core is present everywhere, with no leftover per-engine field names.
Pure/xdist-safe. Lives in optio-demo because only optio-demo depends on all 7
wrappers (the wrappers depend on optio-agents, not each other)."""
import dataclasses

from optio_antigravity.types import AntigravityTaskConfig
from optio_claudecode.types import ClaudeCodeTaskConfig
from optio_codex.types import CodexTaskConfig
from optio_cursor.types import CursorTaskConfig
from optio_grok.types import GrokTaskConfig
from optio_kimicode.types import KimiCodeTaskConfig
from optio_opencode.types import OpencodeTaskConfig

CONFIGS = [
    KimiCodeTaskConfig, GrokTaskConfig, CursorTaskConfig, ClaudeCodeTaskConfig,
    CodexTaskConfig, OpencodeTaskConfig, AntigravityTaskConfig,
]

# The harmonized common core: every engine MUST expose these.
CORE = {
    "consumer_instructions", "env", "scrub_env", "ssh", "install_if_missing",
    "before_execute", "after_execute", "on_deliverable", "seed_id", "on_seed_saved",
    "supports_resume", "workdir_exclude", "mode", "host_protocol", "conversation_ui",
    "tool_verbosity", "thinking_verbosity", "model", "show_session_controls",
    "show_file_upload", "on_upload", "max_upload_bytes", "file_download",
    "max_download_bytes", "auto_start", "native_spinner", "install_dir",
    "session_blob_encrypt", "session_blob_decrypt", "on_resume_refresh",
    "use_client_messages", "on_caller_message",
}


def _fields(cls):
    return {f.name: f for f in dataclasses.fields(cls)}


def test_every_engine_has_the_core():
    for cls in CONFIGS:
        missing = CORE - set(_fields(cls))
        assert not missing, f"{cls.__name__} missing core fields: {sorted(missing)}"


def test_no_default_model_field_remains():
    for cls in CONFIGS:
        assert "default_model" not in _fields(cls), f"{cls.__name__} still has default_model"


def test_no_per_engine_install_dir_name():
    # The agent binary's cache dir is the canonical `install_dir`. `ttyd_install_dir`
    # is a legitimately separate field (the ttyd binary, kept per Spec A) and is
    # allowed on the ttyd engines.
    for cls in CONFIGS:
        names = set(_fields(cls))
        stray = [n for n in names
                 if n.endswith("_install_dir") and n not in ("install_dir", "ttyd_install_dir")]
        assert not stray, f"{cls.__name__} has non-canonical install-dir field(s): {stray}"


def test_antigravity_effort_removed():
    names = set(_fields(AntigravityTaskConfig))
    assert "effort" not in names and "reasoning_effort" not in names
