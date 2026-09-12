"""Guard: no wrapper carries instructions-file prompt text of its own.

All of it lives in ``optio_agents.prompt``; a wrapper's ``prompt.py`` holds a
``PROFILE`` and a thin ``compose_agents_md``. The phrases below occur only in
the shared templates, so a copy-pasted resume section, intro or task framing
fails here instead of drifting for months."""

import importlib
import inspect

import pytest

WRAPPERS = (
    "optio_opencode", "optio_claudecode", "optio_codex", "optio_cursor",
    "optio_grok", "optio_kimicode", "optio_antigravity",
)
SHARED_ONLY_PHRASES = (
    "This harness may pause your session",
    "You are running inside a coordination harness",
    "Here comes the description of your actual task",
    "### Detecting a resume",
    "originate from the harness coordinating this session",
    "**Filesystem access:**",
)


@pytest.mark.parametrize("pkg", WRAPPERS)
def test_wrapper_prompt_module_has_no_shared_prompt_text(pkg):
    mod = importlib.import_module(f"{pkg}.prompt")
    src = inspect.getsource(mod)
    for phrase in SHARED_ONLY_PHRASES:
        assert phrase not in src, f"{pkg}.prompt carries shared prompt text: {phrase!r}"
    assert hasattr(mod, "PROFILE") and hasattr(mod, "compose_agents_md")


@pytest.mark.parametrize("pkg", WRAPPERS)
def test_wrapper_compose_signature_is_uniform(pkg):
    mod = importlib.import_module(f"{pkg}.prompt")
    params = list(inspect.signature(mod.compose_agents_md).parameters)
    assert params == [
        "consumer_instructions", "workdir_exclude", "documentation", "supports_resume",
        "host_protocol", "omit_task_framing", "fs_isolation_dirs", "file_download",
        "check_resume_log_every_message",
    ]
