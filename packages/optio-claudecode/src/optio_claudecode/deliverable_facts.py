"""The Claude Code deliverable metadata provider.

``deliverable_metadata`` builds one deliverable's entries from what optio holds
in memory: the task's config, the conversation's state (what optio launched or
switched to, and what the running claude reported in its stream) and, under a
gateway, the seed's provider and region. The session hands it those two strings
only, never the seed, so the token, the base URL and the key hint are out of its
reach. It reads nothing else (no env, no settings, no account): the entries it
builds below are all it can emit.

Synchronous and in-memory only: the driver calls it inline when it accepts a
``DELIVERABLE:`` line (``optio_agents.deliverable_meta.MetadataProvider``).
Design: ``docs/2026-11-18-deliverable-metadata-design.md``.
"""

from __future__ import annotations

from optio_agents.deliverable_meta import (
    KEY_AGENT_SESSION,
    KEY_AGENT_VERSION,
    KEY_EFFORT,
    KEY_GATEWAY_REGION,
    KEY_MODEL,
    KEY_MODEL_LABEL,
    KEY_PROVIDER,
    SOURCE_AGENT,
    SOURCE_CONFIG,
    SOURCE_OPTIO,
    SOURCE_SEED,
    DeliverableMetadata,
    agent_entries,
    entry,
)

from optio_claudecode import models as cc_models
from optio_claudecode.conversation import ClaudeCodeConversation
from optio_claudecode.info import AGENT_INFO, REQUESTY_AGENT_INFO
from optio_claudecode.types import ClaudeCodeTaskConfig


def deliverable_metadata(
    config: ClaudeCodeTaskConfig,
    conversation: ClaudeCodeConversation | None,
    gateway: tuple[str, str] | None,
) -> DeliverableMetadata:
    """The entries of one Claude Code deliverable, in this order:

    1. the agent's type and name (source ``config``);
    2. iframe mode (``conversation`` None): only ``model`` = ``config.model``
       (source ``config``) when set, and nothing more;
    3. ``agent.version`` and ``agent.session`` as the CLI's system/init named
       them (source ``agent``), each when known;
    4. ``model``: the one optio launched or switched to (source ``optio``),
       then the one the CLI reports for the turn (source ``agent``), each when
       known;
    5. ``model.label``: the catalog label of the model the CLI resolved
       (``cli_model``), else of the turn's model (``turn_model``), or, before
       the CLI names one, of optio's model (``default`` when none); source
       ``seed`` under a gateway, else ``agent``; only when the catalog has one
       (see ``_label``);
    6. ``effort`` (source ``optio``), always, None meaning no explicit level;
    7. under a gateway (``(provider, region)``, from the seed): ``provider``
       and ``gateway.region`` (source ``seed``).
    """
    info = REQUESTY_AGENT_INFO if config.agent_type == REQUESTY_AGENT_INFO.slug else AGENT_INFO
    md = agent_entries(config, info)
    if conversation is None:
        if config.model:
            md.append(entry(KEY_MODEL, config.model, SOURCE_CONFIG))
        return md
    if conversation.cli_version:
        md.append(entry(KEY_AGENT_VERSION, conversation.cli_version, SOURCE_AGENT))
    if conversation.cli_session_id:
        md.append(entry(KEY_AGENT_SESSION, conversation.cli_session_id, SOURCE_AGENT))
    turn_model = conversation.turn_model
    if conversation.model:
        md.append(entry(KEY_MODEL, conversation.model, SOURCE_OPTIO))
    if turn_model:
        md.append(entry(KEY_MODEL, turn_model, SOURCE_AGENT))
    label = _label(conversation)
    if label is not None:
        md.append(entry(KEY_MODEL_LABEL, label, SOURCE_SEED if gateway is not None else SOURCE_AGENT))
    md.append(entry(KEY_EFFORT, conversation.effort, SOURCE_OPTIO))
    if gateway is not None:
        provider, region = gateway
        md.append(entry(KEY_PROVIDER, provider, SOURCE_SEED))
        md.append(entry(KEY_GATEWAY_REGION, region, SOURCE_SEED))
    return md


def _label(conversation: ClaudeCodeConversation) -> str | None:
    """The catalog label (``conversation.models_offered``) of the model the CLI runs.

    The label of ``cli_model``, the latest system/init's model, when the catalog
    has one: it is the model as the CLI resolved it, the form the catalog's ids
    and ``resolved`` values take (e.g. ``claude-opus-5[1m]``). Else the label of
    ``turn_model``: an assistant ``message.model`` names the model as the API
    does, and may lack the context suffix (``claude-opus-5``). Once the CLI has
    named a model, optio's model is no fallback. Before that, the label of
    ``conversation.model``, or of ``default`` when that is None.
    """
    catalog = conversation.models_offered
    cli_model, turn_model = conversation.cli_model, conversation.turn_model
    if not cli_model and not turn_model:
        return cc_models.catalog_label(catalog, conversation.model or cc_models.DEFAULT_MODEL)
    label = cc_models.catalog_label(catalog, cli_model)
    return label if label is not None else cc_models.catalog_label(catalog, turn_model)
