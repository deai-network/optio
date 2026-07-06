"""Config types for optio-antigravity.

Stage-0 stub: the full ``AntigravityTaskConfig`` dataclass (mirroring
``optio_grok.types.GrokTaskConfig``) lands in Task 0.2. This placeholder
exists only so ``optio_antigravity`` imports resolve during scaffolding.
"""

from dataclasses import dataclass


@dataclass
class AntigravityTaskConfig:
    """Placeholder config; superseded by the real dataclass in Task 0.2."""

    consumer_instructions: str = ""
