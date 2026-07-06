"""Session factory for optio-antigravity.

Stage-0 stub: the real ``create_antigravity_task`` / ``run_antigravity_session``
(mirroring ``optio_grok.session``) land in Task 0.3. These placeholders exist
only so ``optio_antigravity`` imports resolve during scaffolding.
"""


def create_antigravity_task(*args, **kwargs):
    """Placeholder factory; superseded by the real one in Task 0.3."""
    raise NotImplementedError("create_antigravity_task lands in Task 0.3")


def run_antigravity_session(*args, **kwargs):
    """Placeholder session driver; superseded by the real one in Task 0.3."""
    raise NotImplementedError("run_antigravity_session lands in Task 0.3")
