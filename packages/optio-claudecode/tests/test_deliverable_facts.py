"""The Claude Code deliverable metadata provider (deliverable_facts) and
models.catalog_label: pure units, no session.

The provider reads the config, the conversation's in-memory state and, under a
gateway, the seed's non-secret ``(provider, region)`` only; it emits exactly
the listed entries, so the config's env never reaches the metadata (RF1, RF5).
"""

import json

from optio_agents.deliverable_meta import entry
from optio_claudecode import ClaudeCodeRequestyTaskConfig, ClaudeCodeTaskConfig
from optio_claudecode import models as cc_models
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.deliverable_facts import deliverable_metadata
from optio_claudecode.models import catalog_label

from .fake_claude import CLI_MODELS

CATALOG = cc_models.parse_cli_models(CLI_MODELS)
GW_CATALOG = [{"id": "lyceum/kimi-k3.1-pro", "label": "Kimi K3.1 Pro", "effort": ["low", "high"]},
              {"id": "glm-flash", "label": "GLM Flash"}]
NATIVE_AGENT = [entry("agent.type", "claudecode", "config"), entry("agent.name", "Claude Code", "config")]


def _conv(**attrs) -> ClaudeCodeConversation:
    conv = ClaudeCodeConversation()
    for name, value in attrs.items():
        setattr(conv, name, value)
    return conv


def _native_config(**kw) -> ClaudeCodeTaskConfig:
    return ClaudeCodeTaskConfig(**{"consumer_instructions": "x", "mode": "conversation",
                                   "permission_mode": "bypassPermissions", "fs_isolation": False, **kw})


def test_a_gateway_conversation_reports_exactly_these_entries():                  # RF1, RF5
    cfg = ClaudeCodeRequestyTaskConfig(consumer_instructions="x", fs_isolation=False, permission_mode="dontAsk")
    conv = _conv(models_offered=GW_CATALOG, model="lyceum/kimi-k3.1-pro", effort="high",
                 cli_session_id="s-1", cli_version="2.1.326", cli_model="lyceum/kimi-k3.1-pro",
                 turn_model="lyceum/kimi-k3.1-pro")
    assert deliverable_metadata(cfg, conv, ("requesty", "eu")) == [
        entry("agent.type", "claudecode_requesty", "config"), entry("agent.name", "Claude Code + Requesty", "config"),
        entry("agent.version", "2.1.326", "agent"), entry("agent.session", "s-1", "agent"),
        entry("model", "lyceum/kimi-k3.1-pro", "optio"), entry("model", "lyceum/kimi-k3.1-pro", "agent"),
        entry("model.label", "Kimi K3.1 Pro", "seed"), entry("effort", "high", "optio"),
        entry("provider", "requesty", "seed"), entry("gateway.region", "eu", "seed")]


def test_a_native_conversation_labels_the_model_the_cli_reports():               # RF5
    cfg = _native_config(env={"OPERATOR": "Jane Doe <jane@x.com>"})
    conv = _conv(models_offered=CATALOG, model=None, effort=None, cli_session_id="s-2", cli_version="2.1.326",
                 cli_model="claude-opus-5[1m]", turn_model="claude-opus-5[1m]")
    md = deliverable_metadata(cfg, conv, None)
    assert md == [*NATIVE_AGENT,
                  entry("agent.version", "2.1.326", "agent"), entry("agent.session", "s-2", "agent"),
                  entry("model", "claude-opus-5[1m]", "agent"), entry("model.label", "Opus (1M context)", "agent"),
                  entry("effort", None, "optio")]
    assert "jane@x.com" not in json.dumps(md)


def test_the_label_follows_the_model_the_cli_resolved():
    # system/init names "claude-opus-5[1m]", the form of the CLI's model list;
    # the API's message.model, now the turn's model, lacks the context suffix.
    cfg = _native_config()
    conv = _conv(models_offered=CATALOG, model=None, effort=None,
                 cli_model="claude-opus-5[1m]", turn_model="claude-opus-5")
    assert catalog_label(CATALOG, "claude-opus-5") is None
    assert deliverable_metadata(cfg, conv, None) == [
        *NATIVE_AGENT, entry("model", "claude-opus-5", "agent"), entry("model.label", "Opus (1M context)", "agent"),
        entry("effort", None, "optio")]


def test_before_the_cli_names_a_model_optios_values_stand():
    cfg = _native_config()
    conv = _conv(models_offered=CATALOG, model="sonnet", effort=None,
                 cli_session_id=None, cli_version=None, cli_model=None, turn_model=None)
    assert deliverable_metadata(cfg, conv, None) == [
        *NATIVE_AGENT, entry("model", "sonnet", "optio"), entry("model.label", "Sonnet", "agent"),
        entry("effort", None, "optio")]
    # No model set: the label is the default entry's, and no model entry.
    conv.model = None
    assert deliverable_metadata(cfg, conv, None) == [
        *NATIVE_AGENT, entry("model.label", "Default (recommended)", "agent"), entry("effort", None, "optio")]


def test_no_label_without_a_catalog():
    cfg = _native_config()
    conv = _conv(models_offered=None, model="sonnet", cli_model="claude-sonnet-5", turn_model="claude-sonnet-5")
    assert deliverable_metadata(cfg, conv, None) == [
        *NATIVE_AGENT, entry("model", "sonnet", "optio"), entry("model", "claude-sonnet-5", "agent"),
        entry("effort", None, "optio")]


def test_no_label_when_the_catalog_lacks_the_turns_model():
    # The label follows the model the CLI reports; optio's model, which the
    # catalog does list, is no fallback.
    cfg = _native_config()
    conv = _conv(models_offered=CATALOG, model="sonnet", cli_model="claude-unlisted-1", turn_model="claude-unlisted-1")
    assert catalog_label(CATALOG, "sonnet") == "Sonnet"
    assert deliverable_metadata(cfg, conv, None) == [
        *NATIVE_AGENT, entry("model", "sonnet", "optio"), entry("model", "claude-unlisted-1", "agent"),
        entry("effort", None, "optio")]


def test_iframe_mode_reports_the_configured_model():
    md = deliverable_metadata(ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False, model="opus"),
                              None, None)
    assert md == [*NATIVE_AGENT, entry("model", "opus", "config")]
    md = deliverable_metadata(ClaudeCodeTaskConfig(consumer_instructions="x", fs_isolation=False, model=None),
                              None, None)
    assert md == NATIVE_AGENT


def test_catalog_label():
    assert catalog_label(CATALOG, "claude-sonnet-5") == "Sonnet"
    assert catalog_label(CATALOG, "sonnet") == "Sonnet"
    assert catalog_label(CATALOG, "x") is None
    assert catalog_label(None, "sonnet") is None
    assert catalog_label(CATALOG, None) is None
